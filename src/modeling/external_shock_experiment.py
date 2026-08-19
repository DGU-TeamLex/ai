"""Fair ablation experiment for news and commodity shock features.

The production validation report is intentionally not overwritten.  All four
direct models use the same L1 objective and validation folds.  A fifth model
adds a temporally separated residual correction to the usage-only baseline.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import (
    MODEL_VARIANTS,
    OUTPUT_DIR,
    PROJECT_ROOT,
    RANDOM_STATE,
    TARGET_COLUMN,
    TRAIN_START,
    VALIDATION_FOLDS,
)
from ..utils import ensure_dirs, setup_logging
from .artifact_paths import portable_artifact_path
from .external_risk_features import (
    COMBINED_SHOCK_COLUMNS,
    COMMODITY_SHOCK_COLUMNS,
    NEWS_SHOCK_COLUMNS,
)
from .metrics import regression_metrics
from .training import (
    _build_estimator,
    _fit_preprocessor,
    _load_feature_table,
    load_historical_training_policy,
    select_feature_columns,
    select_training_window,
    train_model_variant,
    training_sample_weights,
    transform_features,
)


LOGGER = logging.getLogger(__name__)
REPORT_PATH = OUTPUT_DIR / "external_shock_experiment_report.csv"
SUMMARY_PATH = OUTPUT_DIR / "external_shock_experiment_summary.json"
PREDICTION_PATH = OUTPUT_DIR / "external_shock_experiment_predictions.parquet"

DIRECT_VARIANTS = [
    "stock_model_a_usage_only",
    "stock_model_f_news_shock_l1",
    "stock_model_g_commodity_shock_l1",
    "stock_model_h_news_commodity_shock_l1",
]
RESIDUAL_VARIANT = "stock_model_i_external_shock_residual_l1"
RESIDUAL_FEATURES = [
    *NEWS_SHOCK_COLUMNS,
    *COMMODITY_SHOCK_COLUMNS,
    *COMBINED_SHOCK_COLUMNS,
]


def _predict_bundle(bundle: dict, frame: pd.DataFrame) -> np.ndarray:
    matrix = transform_features(frame, bundle["preprocess"])
    estimator = bundle["model"]
    try:
        prediction = estimator.predict(matrix, validate_features=False)
    except TypeError:
        prediction = estimator.predict(matrix)
    return np.clip(prediction, 0.0, None)


def _cohort_metrics(predictions: pd.DataFrame) -> list[dict]:
    masks = {
        "all": pd.Series(True, index=predictions.index),
        "external_signal_connected": predictions["external_signal_connected"],
        "strong_shock_ge_0_8": predictions["strong_shock"],
        "non_shock_lt_0_8": ~predictions["strong_shock"],
    }
    rows = []
    for (fold, model), block in predictions.groupby(["fold", "model"], sort=False):
        for cohort, full_mask in masks.items():
            selected = block.loc[full_mask.reindex(block.index, fill_value=False)]
            if selected.empty:
                continue
            rows.append(
                {
                    "fold": fold,
                    "model": model,
                    "cohort": cohort,
                    **regression_metrics(selected["actual"], selected["prediction"]),
                }
            )
    for model, block in predictions.groupby("model", sort=False):
        for cohort, full_mask in masks.items():
            selected = block.loc[full_mask.reindex(block.index, fill_value=False)]
            if selected.empty:
                continue
            rows.append(
                {
                    "fold": "pooled",
                    "model": model,
                    "cohort": cohort,
                    **regression_metrics(selected["actual"], selected["prediction"]),
                }
            )
    return rows


def _wape(actual: np.ndarray, prediction: np.ndarray) -> float:
    denominator = float(np.abs(actual).sum())
    if denominator == 0:
        return float("nan")
    return float(np.abs(actual - prediction).sum() / denominator * 100.0)


def _monthly_block_bootstrap(
    predictions: pd.DataFrame,
    *,
    draws: int,
) -> list[dict]:
    baseline = predictions[predictions["model"].eq(DIRECT_VARIANTS[0])][
        ["fold", "row_id", "year_month", "actual", "prediction"]
    ].rename(columns={"prediction": "baseline_prediction"})
    rows = []
    rng = np.random.default_rng(RANDOM_STATE)
    for model in predictions["model"].drop_duplicates():
        if model == DIRECT_VARIANTS[0]:
            continue
        paired = predictions[predictions["model"].eq(model)].merge(
            baseline,
            on=["fold", "row_id", "year_month", "actual"],
            validate="one_to_one",
        )
        paired["actual_abs"] = paired["actual"].abs()
        paired["model_abs_error"] = (paired["actual"] - paired["prediction"]).abs()
        paired["baseline_abs_error"] = (
            paired["actual"] - paired["baseline_prediction"]
        ).abs()
        monthly = paired.groupby("year_month", sort=True).agg(
            actual_abs=("actual_abs", "sum"),
            model_abs_error=("model_abs_error", "sum"),
            baseline_abs_error=("baseline_abs_error", "sum"),
        )
        month_count = len(monthly)
        actual_sums = monthly["actual_abs"].to_numpy(dtype="float64")
        model_errors = monthly["model_abs_error"].to_numpy(dtype="float64")
        baseline_errors = monthly["baseline_abs_error"].to_numpy(dtype="float64")
        deltas = []
        for _ in range(draws):
            sampled = rng.integers(0, month_count, size=month_count)
            denominator = actual_sums[sampled].sum()
            deltas.append(
                float(
                    (model_errors[sampled].sum() - baseline_errors[sampled].sum())
                    / denominator
                    * 100.0
                )
            )
        rows.append(
            {
                "model": model,
                "delta_wape_vs_usage_only": _wape(
                    paired["actual"].to_numpy(dtype="float64"),
                    paired["prediction"].to_numpy(dtype="float64"),
                )
                - _wape(
                    paired["actual"].to_numpy(dtype="float64"),
                    paired["baseline_prediction"].to_numpy(dtype="float64"),
                ),
                "bootstrap_draws": draws,
                "delta_wape_ci95_low": float(np.quantile(deltas, 0.025)),
                "delta_wape_ci95_high": float(np.quantile(deltas, 0.975)),
                "improvement_probability": float(np.mean(np.asarray(deltas) < 0.0)),
            }
        )
    return rows


def _fit_residual_correction(
    fold_train: pd.DataFrame,
    fold_valid: pd.DataFrame,
    base_features: list[str],
    historical_weight: float,
) -> tuple[np.ndarray, dict]:
    valid_start = fold_valid["year_month"].min()
    calibration_start = valid_start - pd.DateOffset(months=3)
    base_history = fold_train[fold_train["year_month"].lt(calibration_start)]
    calibration = fold_train[fold_train["year_month"].ge(calibration_start)].copy()
    if base_history.empty or calibration.empty:
        raise ValueError("Residual correction requires history plus three calibration months")

    base_for_calibration = train_model_variant(
        "residual_base_calibration",
        base_history,
        base_features,
        objective="regression_l1",
        sample_weight=training_sample_weights(base_history, historical_weight),
    )
    calibration["residual_target"] = (
        calibration[TARGET_COLUMN].to_numpy(dtype="float64")
        - _predict_bundle(base_for_calibration, calibration)
    )
    shock_train = calibration[calibration["external_risk_shock_score"].gt(0)].copy()
    if len(shock_train) < 100:
        raise ValueError(f"Residual correction has too few shock rows: {len(shock_train)}")

    preprocessor = _fit_preprocessor(shock_train, RESIDUAL_FEATURES)
    x_train = transform_features(shock_train, preprocessor)
    estimator, algorithm = _build_estimator("regression_l1")
    # The correction is deliberately lower-capacity than the demand model.
    if hasattr(estimator, "set_params"):
        estimator.set_params(n_estimators=400, num_leaves=15, min_child_samples=50)
    estimator.fit(x_train, shock_train["residual_target"].to_numpy(dtype="float64"))
    correction = estimator.predict(transform_features(fold_valid, preprocessor))
    lower, upper = np.quantile(shock_train["residual_target"], [0.05, 0.95])
    correction = np.clip(correction, lower, upper)
    gate = fold_valid["external_risk_shock_score"].to_numpy(dtype="float64")
    return correction * gate, {
        "algorithm": algorithm,
        "calibration_rows": int(len(calibration)),
        "shock_calibration_rows": int(len(shock_train)),
        "correction_clip_low": float(lower),
        "correction_clip_high": float(upper),
    }


def run_experiment(*, include_residual: bool = True, bootstrap_draws: int = 500) -> dict:
    setup_logging()
    ensure_dirs(OUTPUT_DIR)
    table = _load_feature_table()
    table = table[table["rolling_mean_3"].notna()].copy()
    policy = load_historical_training_policy()
    historical_weight = float(policy["selected_historical_weight"])
    prediction_frames = []
    residual_audit = []

    feature_sets = {}
    for model in DIRECT_VARIANTS:
        options = MODEL_VARIANTS[model]
        feature_sets[model] = select_feature_columns(
            table,
            use_news=options["use_news"],
            use_commodity=options["use_commodity"],
            use_module_c=options.get("use_module_c", False),
            external_feature_mode=options.get("external_feature_mode", "all"),
        )

    for fold in VALIDATION_FOLDS:
        fold_train = select_training_window(table, fold["train_end"], historical_weight)
        fold_valid = table[
            table["year_month"].between(
                pd.Timestamp(fold["valid_start"]), pd.Timestamp(fold["valid_end"])
            )
        ].copy()
        if fold_train.empty or fold_valid.empty:
            raise ValueError(f"Empty experiment fold: {fold['fold']}")
        common = pd.DataFrame(
            {
                "fold": fold["fold"],
                "row_id": np.arange(len(fold_valid), dtype="int64"),
                "year_month": fold_valid["year_month"].to_numpy(),
                "actual": fold_valid[TARGET_COLUMN].to_numpy(dtype="float64"),
                "external_signal_connected": fold_valid[RESIDUAL_FEATURES].abs().max(axis=1).gt(0).to_numpy(),
                "strong_shock": fold_valid["external_risk_shock_score"].ge(0.8).to_numpy(),
                "shock_score": fold_valid["external_risk_shock_score"].to_numpy(dtype="float32"),
            }
        )
        fold_bundles = {}
        for model in DIRECT_VARIANTS:
            options = MODEL_VARIANTS[model]
            bundle = train_model_variant(
                model,
                fold_train,
                feature_sets[model],
                objective=options["objective"],
                valid=fold_valid,
                sample_weight=training_sample_weights(fold_train, historical_weight),
            )
            fold_bundles[model] = bundle
            output = common.copy()
            output["model"] = model
            output["prediction"] = bundle["validation_prediction"]
            prediction_frames.append(output)
            LOGGER.info("External shock experiment %s/%s complete", fold["fold"], model)

        if include_residual:
            correction, audit = _fit_residual_correction(
                fold_train,
                fold_valid,
                feature_sets[DIRECT_VARIANTS[0]],
                historical_weight,
            )
            residual = common.copy()
            residual["model"] = RESIDUAL_VARIANT
            residual["prediction"] = np.clip(
                fold_bundles[DIRECT_VARIANTS[0]]["validation_prediction"] + correction,
                0.0,
                None,
            )
            prediction_frames.append(residual)
            residual_audit.append({"fold": fold["fold"], **audit})
            LOGGER.info("External shock experiment %s/residual complete", fold["fold"])

    predictions = pd.concat(prediction_frames, ignore_index=True)
    report = pd.DataFrame(_cohort_metrics(predictions))
    bootstrap = _monthly_block_bootstrap(predictions, draws=bootstrap_draws)
    report.to_csv(REPORT_PATH, index=False)
    predictions.to_parquet(PREDICTION_PATH, index=False, compression="zstd")
    summary = {
        "status": "complete",
        "objective_contract": "all direct variants use regression_l1",
        "target": "next-month demand",
        "shock_threshold": 0.8,
        "variants": DIRECT_VARIANTS + ([RESIDUAL_VARIANT] if include_residual else []),
        "feature_counts": {model: len(columns) for model, columns in feature_sets.items()},
        "historical_training_policy_version": policy.get("version"),
        "bootstrap": bootstrap,
        "residual_audit": residual_audit,
        "report_path": portable_artifact_path(REPORT_PATH, PROJECT_ROOT),
        "prediction_path": portable_artifact_path(PREDICTION_PATH, PROJECT_ROOT),
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    LOGGER.info("Saved external shock experiment: %s", REPORT_PATH)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run fair external shock ablation")
    parser.add_argument("--no-residual", action="store_true")
    parser.add_argument("--bootstrap-draws", type=int, default=500)
    args = parser.parse_args()
    run_experiment(
        include_residual=not args.no_residual,
        bootstrap_draws=args.bootstrap_draws,
    )
