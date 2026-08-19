"""저부하·체크포인트 방식의 외부위험 충격 실험 실행기.

기존 공정 비교 공식과 모델 설정은 바꾸지 않는다. 각 폴드/모델의 예측을 즉시 저장해
비정상 종료 뒤에도 완료 작업을 건너뛸 수 있게 하고, 이 프로세스 안에서만 LightGBM
스레드 수를 제한한다.
"""

from __future__ import annotations

import argparse
import gc
import json
import logging
import time
from datetime import datetime
from pathlib import Path
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
from .artifact_paths import portable_artifact_path
from . import training


LOGGER = logging.getLogger(__name__)
DEFAULT_CHECKPOINT_DIR = OUTPUT_DIR / "external_shock_experiment_checkpoints_v1"
STATUS_PATH = OUTPUT_DIR / "external_shock_resumable_status.json"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _atomic_parquet(path: Path, frame: pd.DataFrame) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False, compression="zstd")
    temporary.replace(path)


def _write_status(status: str, stage: str, **extra: Any) -> None:
    _atomic_json(
        STATUS_PATH,
        {
            "status": status,
            "stage": stage,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **extra,
        },
    )


def _limit_lightgbm_threads(n_jobs: int) -> None:
    """현재 실행기에서 만들어지는 LightGBM 모델만 스레드 수를 제한한다."""

    original_builder = training._build_estimator

    def limited_builder(objective: str):
        estimator, algorithm = original_builder(objective)
        if hasattr(estimator, "get_params") and hasattr(estimator, "set_params"):
            parameters = estimator.get_params(deep=False)
            if "n_jobs" in parameters:
                estimator.set_params(n_jobs=n_jobs)
        return estimator, algorithm

    training._build_estimator = limited_builder
    # 잔차 모형은 원 모듈로 import된 builder를 직접 사용한다.
    experiment._build_estimator = limited_builder


def _manifest(
    table: pd.DataFrame,
    feature_sets: dict[str, list[str]],
    *,
    include_residual: bool,
) -> dict[str, Any]:
    months = table["year_month"]
    return {
        "schema_version": 1,
        "target": TARGET_COLUMN,
        "include_residual": include_residual,
        "folds": VALIDATION_FOLDS,
        "direct_variants": experiment.DIRECT_VARIANTS,
        "residual_variant": experiment.RESIDUAL_VARIANT,
        "feature_counts": {
            model: len(columns) for model, columns in feature_sets.items()
        },
        "table_rows": int(len(table)),
        "table_month_min": str(months.min()),
        "table_month_max": str(months.max()),
    }


def _prepare_checkpoint_dir(
    checkpoint_dir: Path,
    expected_manifest: dict[str, Any],
) -> None:
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = checkpoint_dir / "manifest.json"
    if manifest_path.is_file():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing != expected_manifest:
            raise ValueError(
                "체크포인트의 데이터·모델 계약이 현재 실행과 다릅니다. "
                "기존 체크포인트를 보존한 채 새 --checkpoint-dir을 사용하세요."
            )
    else:
        _atomic_json(manifest_path, expected_manifest)


def _checkpoint_path(checkpoint_dir: Path, fold: str, model: str) -> Path:
    return checkpoint_dir / f"{fold}__{model}.parquet"


def _load_prediction_checkpoint(
    path: Path,
    *,
    fold: str,
    model: str,
    expected_rows: int,
) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    if len(frame) != expected_rows:
        raise ValueError(f"체크포인트 행 수 불일치: {path}")
    if set(frame["fold"].astype(str)) != {fold}:
        raise ValueError(f"체크포인트 fold 불일치: {path}")
    if set(frame["model"].astype(str)) != {model}:
        raise ValueError(f"체크포인트 model 불일치: {path}")
    return frame


def _cooldown(seconds: int) -> None:
    gc.collect()
    if seconds > 0:
        LOGGER.info("Cooling down for %s seconds", seconds)
        time.sleep(seconds)


def run_resumable_experiment(
    *,
    include_residual: bool = True,
    bootstrap_draws: int = 500,
    n_jobs: int = 2,
    cooldown_seconds: int = 20,
    checkpoint_dir: Path = DEFAULT_CHECKPOINT_DIR,
) -> dict[str, Any]:
    if n_jobs < 1:
        raise ValueError("n_jobs must be at least 1")
    if cooldown_seconds < 0:
        raise ValueError("cooldown_seconds cannot be negative")

    setup_logging()
    ensure_dirs(OUTPUT_DIR)
    _limit_lightgbm_threads(n_jobs)
    _write_status("running", "loading_feature_table", n_jobs=n_jobs)

    table = experiment._load_feature_table()
    table = table[table["rolling_mean_3"].notna()].copy()
    policy = experiment.load_historical_training_policy()
    historical_weight = float(policy["selected_historical_weight"])

    feature_sets: dict[str, list[str]] = {}
    for model in experiment.DIRECT_VARIANTS:
        options = MODEL_VARIANTS[model]
        feature_sets[model] = experiment.select_feature_columns(
            table,
            use_news=options["use_news"],
            use_commodity=options["use_commodity"],
            use_module_c=options.get("use_module_c", False),
            external_feature_mode=options.get("external_feature_mode", "all"),
        )

    expected_manifest = _manifest(
        table,
        feature_sets,
        include_residual=include_residual,
    )
    _prepare_checkpoint_dir(checkpoint_dir, expected_manifest)

    prediction_frames: list[pd.DataFrame] = []
    residual_audit: list[dict[str, Any]] = []
    completed_tasks: list[str] = []

    for fold in VALIDATION_FOLDS:
        fold_name = str(fold["fold"])
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

        common = pd.DataFrame(
            {
                "fold": fold_name,
                "row_id": np.arange(len(fold_valid), dtype="int64"),
                "year_month": fold_valid["year_month"].to_numpy(),
                "actual": fold_valid[TARGET_COLUMN].to_numpy(dtype="float64"),
                "external_signal_connected": fold_valid[
                    experiment.RESIDUAL_FEATURES
                ]
                .abs()
                .max(axis=1)
                .gt(0)
                .to_numpy(),
                "strong_shock": fold_valid["external_risk_shock_score"]
                .ge(0.8)
                .to_numpy(),
                "shock_score": fold_valid[
                    "external_risk_shock_score"
                ].to_numpy(dtype="float32"),
            }
        )
        baseline_prediction: np.ndarray | None = None

        for model in experiment.DIRECT_VARIANTS:
            task = f"{fold_name}/{model}"
            checkpoint_path = _checkpoint_path(checkpoint_dir, fold_name, model)
            if checkpoint_path.is_file():
                output = _load_prediction_checkpoint(
                    checkpoint_path,
                    fold=fold_name,
                    model=model,
                    expected_rows=len(common),
                )
                LOGGER.info("Resumed external shock experiment %s", task)
            else:
                _write_status(
                    "running",
                    "training",
                    current_task=task,
                    completed_tasks=completed_tasks,
                    n_jobs=n_jobs,
                )
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
                _atomic_parquet(checkpoint_path, output)
                del bundle
                LOGGER.info("Checkpointed external shock experiment %s", task)

            prediction_frames.append(output)
            if model == experiment.DIRECT_VARIANTS[0]:
                baseline_prediction = output["prediction"].to_numpy(dtype="float64")
            completed_tasks.append(task)
            _write_status(
                "running",
                "checkpointed",
                last_completed_task=task,
                completed_tasks=completed_tasks,
                n_jobs=n_jobs,
            )
            _cooldown(cooldown_seconds)

        if include_residual:
            model = experiment.RESIDUAL_VARIANT
            task = f"{fold_name}/{model}"
            checkpoint_path = _checkpoint_path(checkpoint_dir, fold_name, model)
            audit_path = checkpoint_dir / f"{fold_name}__{model}.audit.json"
            if checkpoint_path.is_file() and audit_path.is_file():
                residual = _load_prediction_checkpoint(
                    checkpoint_path,
                    fold=fold_name,
                    model=model,
                    expected_rows=len(common),
                )
                audit = json.loads(audit_path.read_text(encoding="utf-8"))
                LOGGER.info("Resumed external shock experiment %s", task)
            else:
                if baseline_prediction is None:
                    raise RuntimeError(f"Missing baseline prediction for {fold_name}")
                _write_status(
                    "running",
                    "training",
                    current_task=task,
                    completed_tasks=completed_tasks,
                    n_jobs=n_jobs,
                )
                correction, audit = experiment._fit_residual_correction(
                    fold_train,
                    fold_valid,
                    feature_sets[experiment.DIRECT_VARIANTS[0]],
                    historical_weight,
                )
                residual = common.copy()
                residual["model"] = model
                residual["prediction"] = np.clip(
                    baseline_prediction + correction,
                    0.0,
                    None,
                )
                _atomic_parquet(checkpoint_path, residual)
                _atomic_json(audit_path, audit)
                LOGGER.info("Checkpointed external shock experiment %s", task)

            prediction_frames.append(residual)
            residual_audit.append({"fold": fold_name, **audit})
            completed_tasks.append(task)
            _write_status(
                "running",
                "checkpointed",
                last_completed_task=task,
                completed_tasks=completed_tasks,
                n_jobs=n_jobs,
            )
            _cooldown(cooldown_seconds)

        del fold_train, fold_valid, common
        gc.collect()

    _write_status(
        "running",
        "aggregating",
        completed_tasks=completed_tasks,
        n_jobs=n_jobs,
    )
    predictions = pd.concat(prediction_frames, ignore_index=True)
    report = pd.DataFrame(experiment._cohort_metrics(predictions))
    bootstrap = experiment._monthly_block_bootstrap(
        predictions,
        draws=bootstrap_draws,
    )
    report.to_csv(experiment.REPORT_PATH, index=False)
    predictions.to_parquet(
        experiment.PREDICTION_PATH,
        index=False,
        compression="zstd",
    )
    summary: dict[str, Any] = {
        "status": "complete",
        "execution_mode": "resumable_low_resource",
        "n_jobs": n_jobs,
        "cooldown_seconds": cooldown_seconds,
        "checkpoint_dir": portable_artifact_path(checkpoint_dir, PROJECT_ROOT),
        "objective_contract": "all direct variants use regression_l1",
        "target": "next-month demand",
        "shock_threshold": 0.8,
        "variants": experiment.DIRECT_VARIANTS
        + ([experiment.RESIDUAL_VARIANT] if include_residual else []),
        "feature_counts": {
            model: len(columns) for model, columns in feature_sets.items()
        },
        "historical_training_policy_version": policy.get("version"),
        "bootstrap": bootstrap,
        "residual_audit": residual_audit,
        "report_path": portable_artifact_path(experiment.REPORT_PATH, PROJECT_ROOT),
        "prediction_path": portable_artifact_path(experiment.PREDICTION_PATH, PROJECT_ROOT),
    }
    experiment.SUMMARY_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_status(
        "complete",
        "complete",
        completed_tasks=completed_tasks,
        report_path=portable_artifact_path(experiment.REPORT_PATH, PROJECT_ROOT),
        summary_path=portable_artifact_path(experiment.SUMMARY_PATH, PROJECT_ROOT),
        prediction_path=portable_artifact_path(experiment.PREDICTION_PATH, PROJECT_ROOT),
    )
    LOGGER.info("Saved resumable external shock experiment: %s", experiment.REPORT_PATH)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the external shock experiment with checkpoints and low CPU load"
    )
    parser.add_argument("--no-residual", action="store_true")
    parser.add_argument("--bootstrap-draws", type=int, default=500)
    parser.add_argument("--n-jobs", type=int, default=2)
    parser.add_argument("--cooldown-seconds", type=int, default=20)
    parser.add_argument("--checkpoint-dir", type=Path, default=DEFAULT_CHECKPOINT_DIR)
    args = parser.parse_args()
    try:
        run_resumable_experiment(
            include_residual=not args.no_residual,
            bootstrap_draws=args.bootstrap_draws,
            n_jobs=args.n_jobs,
            cooldown_seconds=args.cooldown_seconds,
            checkpoint_dir=args.checkpoint_dir,
        )
    except Exception as error:
        _write_status(
            "failed",
            "failed",
            error_type=type(error).__name__,
            error=str(error),
        )
        raise


if __name__ == "__main__":
    main()
