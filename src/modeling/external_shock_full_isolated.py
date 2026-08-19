"""Run the full external-shock experiment one task per Python process.

This runner preserves the production model capacity and the complete sample.  Its
only model-side resource change is ``n_jobs=1``.  Each fold/model task writes an
atomic checkpoint and then exits, so LightGBM and pandas return all process memory
before the next task starts.  The v1/v2 checkpoints are deliberately not reused:
v2 was created by the reduced-capacity exploratory runner.
"""

from __future__ import annotations

import argparse
import ctypes
import gc
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

import numpy as np
import pandas as pd

from ..config import (
    MODEL_VARIANTS,
    OUTPUT_DIR,
    PROJECT_ROOT,
    TARGET_COLUMN,
    VALIDATION_FOLDS,
)
from ..utils import ensure_dirs, setup_logging
from . import external_shock_experiment as experiment
from . import external_shock_resumable as resumable
from .artifact_paths import portable_artifact_path
from . import training


LOGGER = logging.getLogger(__name__)
CHECKPOINT_DIR = OUTPUT_DIR / "external_shock_experiment_checkpoints_v3_full_isolated"
STATUS_PATH = OUTPUT_DIR / "external_shock_full_isolated_status.json"
PROFILE_PATH = CHECKPOINT_DIR / "resource_profile.json"
DEFAULT_BOOTSTRAP_DRAWS = 500
DEFAULT_COOLDOWN_SECONDS = 60
N_JOBS = 1


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_status(status: str, stage: str, **extra: Any) -> None:
    _atomic_json(
        STATUS_PATH,
        {
            "status": status,
            "stage": stage,
            "execution_mode": "full_capacity_isolated_process",
            "updated_at": pd.Timestamp.now(tz="Asia/Seoul").isoformat(),
            **extra,
        },
    )


def _apply_process_limits() -> None:
    """Use one logical CPU and below-normal priority without changing the model."""

    if os.name != "nt":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetCurrentProcess()
        kernel32.SetPriorityClass(handle, 0x00004000)  # BELOW_NORMAL_PRIORITY_CLASS
        kernel32.SetProcessAffinityMask(handle, 1)
    except Exception:
        LOGGER.warning("Could not apply Windows priority/affinity limits", exc_info=True)


def _limit_lightgbm_threads() -> None:
    """Keep production hyperparameters intact, changing CPU parallelism only."""

    original_builder = training._build_estimator

    def limited_builder(objective: str):
        estimator, algorithm = original_builder(objective)
        if hasattr(estimator, "get_params") and hasattr(estimator, "set_params"):
            parameters = estimator.get_params(deep=False)
            if "n_jobs" in parameters:
                estimator.set_params(n_jobs=N_JOBS)
        return estimator, algorithm

    training._build_estimator = limited_builder
    experiment._build_estimator = limited_builder


def _feature_sets(table: pd.DataFrame) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for model in experiment.DIRECT_VARIANTS:
        options = MODEL_VARIANTS[model]
        result[model] = experiment.select_feature_columns(
            table,
            use_news=options["use_news"],
            use_commodity=options["use_commodity"],
            use_module_c=options.get("use_module_c", False),
            external_feature_mode=options.get("external_feature_mode", "all"),
        )
    return result


def _load_context() -> tuple[pd.DataFrame, dict[str, list[str]], dict[str, Any], float]:
    table = experiment._load_feature_table()
    table = table[table["rolling_mean_3"].notna()].copy()
    feature_sets = _feature_sets(table)
    policy = experiment.load_historical_training_policy()
    historical_weight = float(policy["selected_historical_weight"])
    expected_manifest = resumable._manifest(
        table,
        feature_sets,
        include_residual=True,
    )
    return table, feature_sets, expected_manifest, historical_weight


def _fold_definition(fold_name: str) -> dict[str, str]:
    for fold in VALIDATION_FOLDS:
        if str(fold["fold"]) == fold_name:
            return fold
    raise ValueError(f"Unknown fold: {fold_name}")


def _common_frame(fold_name: str, fold_valid: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "fold": fold_name,
            "row_id": np.arange(len(fold_valid), dtype="int64"),
            "year_month": fold_valid["year_month"].to_numpy(),
            "actual": fold_valid[TARGET_COLUMN].to_numpy(dtype="float64"),
            "external_signal_connected": fold_valid[experiment.RESIDUAL_FEATURES]
            .abs()
            .max(axis=1)
            .gt(0)
            .to_numpy(),
            "strong_shock": fold_valid["external_risk_shock_score"]
            .ge(0.8)
            .to_numpy(),
            "shock_score": fold_valid["external_risk_shock_score"].to_numpy(
                dtype="float32"
            ),
        }
    )


def _run_worker(fold_name: str, model: str, checkpoint_dir: Path) -> None:
    setup_logging()
    ensure_dirs(OUTPUT_DIR)
    _apply_process_limits()
    _limit_lightgbm_threads()
    table, feature_sets, manifest, historical_weight = _load_context()
    resumable._prepare_checkpoint_dir(checkpoint_dir, manifest)

    fold = _fold_definition(fold_name)
    fold_train = experiment.select_training_window(
        table, fold["train_end"], historical_weight
    )
    fold_valid = table[
        table["year_month"].between(
            pd.Timestamp(fold["valid_start"]),
            pd.Timestamp(fold["valid_end"]),
        )
    ].copy()
    if fold_train.empty or fold_valid.empty:
        raise ValueError(f"Empty experiment fold: {fold_name}")
    common = _common_frame(fold_name, fold_valid)
    checkpoint_path = resumable._checkpoint_path(checkpoint_dir, fold_name, model)

    if model in experiment.DIRECT_VARIANTS:
        options = MODEL_VARIANTS[model]
        bundle = training.train_model_variant(
            model,
            fold_train,
            feature_sets[model],
            objective=options["objective"],
            valid=fold_valid,
            sample_weight=training.training_sample_weights(
                fold_train, historical_weight
            ),
        )
        output = common.copy()
        output["model"] = model
        output["prediction"] = bundle["validation_prediction"]
        resumable._atomic_parquet(checkpoint_path, output)
        del bundle, output
    elif model == experiment.RESIDUAL_VARIANT:
        baseline_path = resumable._checkpoint_path(
            checkpoint_dir,
            fold_name,
            experiment.DIRECT_VARIANTS[0],
        )
        baseline = resumable._load_prediction_checkpoint(
            baseline_path,
            fold=fold_name,
            model=experiment.DIRECT_VARIANTS[0],
            expected_rows=len(common),
        )
        correction, audit = experiment._fit_residual_correction(
            fold_train,
            fold_valid,
            feature_sets[experiment.DIRECT_VARIANTS[0]],
            historical_weight,
        )
        output = common.copy()
        output["model"] = model
        output["prediction"] = np.clip(
            baseline["prediction"].to_numpy(dtype="float64") + correction,
            0.0,
            None,
        )
        resumable._atomic_parquet(checkpoint_path, output)
        _atomic_json(
            checkpoint_dir / f"{fold_name}__{model}.audit.json",
            audit,
        )
        del baseline, correction, output
    else:
        raise ValueError(f"Unknown model: {model}")

    del table, feature_sets, fold_train, fold_valid, common
    gc.collect()
    LOGGER.info("Full isolated checkpoint complete: %s/%s", fold_name, model)


def _tasks() -> list[tuple[str, str]]:
    models = [*experiment.DIRECT_VARIANTS, experiment.RESIDUAL_VARIANT]
    return [(str(fold["fold"]), model) for fold in VALIDATION_FOLDS for model in models]


def _task_complete(checkpoint_dir: Path, fold: str, model: str) -> bool:
    prediction = resumable._checkpoint_path(checkpoint_dir, fold, model)
    if not prediction.is_file():
        return False
    if model == experiment.RESIDUAL_VARIANT:
        return (checkpoint_dir / f"{fold}__{model}.audit.json").is_file()
    return True


def _completed_tasks(checkpoint_dir: Path) -> list[str]:
    return [
        f"{fold}/{model}"
        for fold, model in _tasks()
        if _task_complete(checkpoint_dir, fold, model)
    ]


def _worker_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
        }
    )
    return environment


def _aggregate(checkpoint_dir: Path, bootstrap_draws: int) -> None:
    setup_logging()
    _apply_process_limits()
    frames: list[pd.DataFrame] = []
    residual_audit: list[dict[str, Any]] = []
    for fold, model in _tasks():
        path = resumable._checkpoint_path(checkpoint_dir, fold, model)
        frame = pd.read_parquet(path)
        if set(frame["fold"].astype(str)) != {fold}:
            raise ValueError(f"Checkpoint fold mismatch: {path}")
        if set(frame["model"].astype(str)) != {model}:
            raise ValueError(f"Checkpoint model mismatch: {path}")
        frames.append(frame)
        if model == experiment.RESIDUAL_VARIANT:
            audit = json.loads(
                (checkpoint_dir / f"{fold}__{model}.audit.json").read_text(
                    encoding="utf-8"
                )
            )
            residual_audit.append({"fold": fold, **audit})

    predictions = pd.concat(frames, ignore_index=True)
    report = pd.DataFrame(experiment._cohort_metrics(predictions))
    bootstrap = experiment._monthly_block_bootstrap(
        predictions,
        draws=bootstrap_draws,
    )
    report_temporary = experiment.REPORT_PATH.with_suffix(".csv.tmp")
    report.to_csv(report_temporary, index=False)
    report_temporary.replace(experiment.REPORT_PATH)
    resumable._atomic_parquet(experiment.PREDICTION_PATH, predictions)

    policy = experiment.load_historical_training_policy()
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    summary: dict[str, Any] = {
        "status": "complete",
        "execution_mode": "full_capacity_isolated_process",
        "resource_profile": profile,
        "checkpoint_dir": portable_artifact_path(checkpoint_dir, PROJECT_ROOT),
        "objective_contract": "all direct variants use regression_l1",
        "target": "next-month demand",
        "shock_threshold": 0.8,
        "variants": [*experiment.DIRECT_VARIANTS, experiment.RESIDUAL_VARIANT],
        "historical_training_policy_version": policy.get("version"),
        "bootstrap": bootstrap,
        "residual_audit": residual_audit,
        "report_path": portable_artifact_path(experiment.REPORT_PATH, PROJECT_ROOT),
        "prediction_path": portable_artifact_path(experiment.PREDICTION_PATH, PROJECT_ROOT),
    }
    _atomic_json(experiment.SUMMARY_PATH, summary)
    _write_status(
        "complete",
        "complete",
        completed_tasks=_completed_tasks(checkpoint_dir),
        report_path=portable_artifact_path(experiment.REPORT_PATH, PROJECT_ROOT),
        summary_path=portable_artifact_path(experiment.SUMMARY_PATH, PROJECT_ROOT),
        prediction_path=portable_artifact_path(experiment.PREDICTION_PATH, PROJECT_ROOT),
    )


def _run_orchestrator(checkpoint_dir: Path, bootstrap_draws: int, cooldown: int) -> None:
    setup_logging()
    ensure_dirs(OUTPUT_DIR)
    _apply_process_limits()
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    production_parameters = training.production_lgbm_params("regression_l1")
    profile = {
        "experiment_role": "final_full_capacity_ablation",
        "sample_reduction": False,
        "feature_reduction": False,
        "production_hyperparameters_preserved": True,
        "only_model_change": "n_jobs=1",
        "n_estimators": int(production_parameters["n_estimators"]),
        "num_leaves": int(production_parameters["num_leaves"]),
        "histogram_pool_size_mb": production_parameters.get("histogram_pool_size"),
        "worker_processes_concurrent": 1,
        "cooldown_seconds": cooldown,
        "bootstrap_draws": bootstrap_draws,
    }
    _atomic_json(PROFILE_PATH, profile)

    environment = _worker_environment()
    for fold, model in _tasks():
        if _task_complete(checkpoint_dir, fold, model):
            LOGGER.info("Resume full isolated checkpoint: %s/%s", fold, model)
            continue
        task = f"{fold}/{model}"
        completed = _completed_tasks(checkpoint_dir)
        _write_status(
            "running",
            "training_worker",
            current_task=task,
            completed_tasks=completed,
            total_tasks=len(_tasks()),
        )
        command = [
            sys.executable,
            "-u",
            "-m",
            "src.modeling.external_shock_full_isolated",
            "--worker",
            "--fold",
            fold,
            "--model",
            model,
            "--checkpoint-dir",
            str(checkpoint_dir),
        ]
        result = subprocess.run(command, env=environment, check=False)
        if result.returncode != 0:
            _write_status(
                "failed",
                "worker_failed",
                current_task=task,
                completed_tasks=_completed_tasks(checkpoint_dir),
                return_code=result.returncode,
            )
            raise RuntimeError(f"Full isolated worker failed: {task}")
        _write_status(
            "running",
            "checkpointed",
            last_completed_task=task,
            completed_tasks=_completed_tasks(checkpoint_dir),
            total_tasks=len(_tasks()),
        )
        if cooldown > 0:
            LOGGER.info("Cooling down for %s seconds", cooldown)
            time.sleep(cooldown)

    _write_status(
        "running",
        "aggregating",
        completed_tasks=_completed_tasks(checkpoint_dir),
        total_tasks=len(_tasks()),
    )
    result = subprocess.run(
        [
            sys.executable,
            "-u",
            "-m",
            "src.modeling.external_shock_full_isolated",
            "--aggregate",
            "--bootstrap-draws",
            str(bootstrap_draws),
            "--checkpoint-dir",
            str(checkpoint_dir),
        ],
        env=environment,
        check=False,
    )
    if result.returncode != 0:
        _write_status(
            "failed",
            "aggregation_failed",
            completed_tasks=_completed_tasks(checkpoint_dir),
            return_code=result.returncode,
        )
        raise RuntimeError("Full isolated aggregation failed")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run full external-shock ablation in isolated worker processes"
    )
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--aggregate", action="store_true")
    parser.add_argument("--fold")
    parser.add_argument("--model")
    parser.add_argument("--bootstrap-draws", type=int, default=DEFAULT_BOOTSTRAP_DRAWS)
    parser.add_argument("--cooldown-seconds", type=int, default=DEFAULT_COOLDOWN_SECONDS)
    parser.add_argument("--checkpoint-dir", type=Path, default=CHECKPOINT_DIR)
    args = parser.parse_args()

    if args.worker and args.aggregate:
        parser.error("--worker and --aggregate are mutually exclusive")
    if args.worker:
        if not args.fold or not args.model:
            parser.error("--worker requires --fold and --model")
        _run_worker(args.fold, args.model, args.checkpoint_dir)
        return
    if args.aggregate:
        _aggregate(args.checkpoint_dir, args.bootstrap_draws)
        return
    _run_orchestrator(
        args.checkpoint_dir,
        args.bootstrap_draws,
        args.cooldown_seconds,
    )


if __name__ == "__main__":
    main()
