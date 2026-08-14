from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import (
    BACKTEST_PREDICTION_PATH,
    FORECAST_ENSEMBLE_POLICY_PATH,
    FORECAST_ENSEMBLE_REPORT_PATH,
    FORECAST_ENSEMBLE_SAMPLE_PATH,
    FORECAST_ENSEMBLE_SEGMENT_PATH,
    FORECAST_ENSEMBLE_VALIDATION_PATH,
    MODEL_MANIFEST_PATH,
    PROJECT_ROOT,
    VALID_END,
)
from ..utils import ensure_dirs, setup_logging, write_json
from .metrics import regression_metrics


LOGGER = logging.getLogger(__name__)
POLICY_VERSION = "forecast-ensemble-temporal-v1.0"
DEFAULT_TEST_MONTH_COUNT = 3
DEFAULT_WEIGHT_STEP = 0.05
DEFAULT_MODEL_COLUMNS = [
    "stock_model_a_usage_only_pred",
    "stock_model_a_usage_tweedie_pred",
    "stock_model_d_module_c_pred",
]


def _portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


def split_validation_test_months(
    months: list[str],
    *,
    test_month_count: int = DEFAULT_TEST_MONTH_COUNT,
) -> tuple[list[str], list[str]]:
    ordered = sorted(set(months))
    if test_month_count <= 0 or len(ordered) <= test_month_count:
        raise ValueError(
            "At least one validation month must precede the test months: "
            f"months={len(ordered)}, test={test_month_count}"
        )
    return ordered[:-test_month_count], ordered[-test_month_count:]


def generate_weight_grid(
    columns: list[str],
    *,
    step: float = DEFAULT_WEIGHT_STEP,
) -> list[dict[str, float]]:
    if len(columns) < 2:
        raise ValueError("At least two forecast columns are required")
    units = int(round(1.0 / step))
    if step <= 0 or not np.isclose(units * step, 1.0):
        raise ValueError("Weight step must divide 1.0 exactly")

    allocations: list[tuple[int, ...]] = []

    def visit(prefix: tuple[int, ...], remaining: int) -> None:
        if len(prefix) == len(columns) - 1:
            allocations.append((*prefix, remaining))
            return
        for value in range(remaining + 1):
            visit((*prefix, value), remaining - value)

    visit((), units)
    return [
        {
            column: round(value / units, 10)
            for column, value in zip(columns, allocation)
        }
        for allocation in allocations
    ]


def blend_prediction(
    frame: pd.DataFrame,
    weights: dict[str, float],
) -> np.ndarray:
    values = np.zeros(len(frame), dtype="float64")
    for column, weight in weights.items():
        values += (
            pd.to_numeric(frame[column], errors="coerce")
            .fillna(0.0)
            .to_numpy(dtype="float64")
            * float(weight)
        )
    return np.clip(values, 0, None)


def select_validation_weights(
    validation: pd.DataFrame,
    model_columns: list[str],
    *,
    weight_step: float = DEFAULT_WEIGHT_STEP,
) -> tuple[dict[str, float], pd.DataFrame]:
    rows = []
    actual = validation["actual_usage"].to_numpy(dtype="float64")
    for weights in generate_weight_grid(model_columns, step=weight_step):
        metrics = regression_metrics(
            actual,
            blend_prediction(validation, weights),
        )
        rows.append(
            {
                **{
                    f"weight__{column}": weight
                    for column, weight in weights.items()
                },
                **metrics,
            }
        )
    evaluation = pd.DataFrame(rows).sort_values(
        ["WAPE", "BIAS_PCT", "RMSE"],
        key=lambda values: values.abs()
        if values.name == "BIAS_PCT"
        else values,
        kind="mergesort",
    ).reset_index(drop=True)
    selected_row = evaluation.iloc[0]
    selected = {
        column: float(selected_row[f"weight__{column}"])
        for column in model_columns
    }
    return selected, evaluation


def select_pattern_weights(
    validation: pd.DataFrame,
    model_columns: list[str],
    *,
    weight_step: float = DEFAULT_WEIGHT_STEP,
) -> dict[str, dict[str, float]]:
    return {
        str(pattern): select_validation_weights(
            group,
            model_columns,
            weight_step=weight_step,
        )[0]
        for pattern, group in validation.groupby(
            "demand_pattern",
            dropna=False,
            observed=True,
        )
    }


def apply_pattern_weights(
    frame: pd.DataFrame,
    pattern_weights: dict[str, dict[str, float]],
    fallback_weights: dict[str, float],
) -> np.ndarray:
    prediction = blend_prediction(frame, fallback_weights)
    pattern = frame["demand_pattern"].fillna("new_series").astype(str)
    for pattern_name, weights in pattern_weights.items():
        mask = pattern.eq(pattern_name)
        if mask.any():
            prediction[mask.to_numpy()] = blend_prediction(
                frame.loc[mask],
                weights,
            )
    return prediction


def _selected_manifest_column(
    manifest_path: Path,
    available_columns: set[str],
) -> str:
    with manifest_path.open("r", encoding="utf-8") as file:
        manifest = json.load(file)
    selected = [
        row
        for row in manifest
        if row.get("selected_on_validation")
    ]
    ranked = selected or sorted(
        [
            row
            for row in manifest
            if row.get("WAPE") is not None
        ],
        key=lambda row: row["WAPE"],
    )
    for row in ranked:
        column = f"{row['model']}_pred"
        if column in available_columns:
            return column
    raise ValueError("No manifest-selected forecast is in the backtest")


def _metric_delta(
    current: dict[str, float | int | None],
    selected: dict[str, float | int | None],
    key: str,
) -> dict[str, float | None]:
    before = current[key]
    after = selected[key]
    if before is None or after is None:
        return {
            "before": before,
            "after": after,
            "absolute_change": None,
            "relative_change_percent": None,
        }
    before_value = float(before)
    after_value = float(after)
    return {
        "before": before_value,
        "after": after_value,
        "absolute_change": after_value - before_value,
        "relative_change_percent": (
            100.0 * (after_value - before_value) / before_value
            if before_value != 0
            else None
        ),
    }


def _segment_evaluation(
    test: pd.DataFrame,
    current_column: str,
    selected_prediction: np.ndarray,
) -> pd.DataFrame:
    detail = test[["demand_pattern", "actual_usage", current_column]].copy()
    detail["temporal_ensemble_pred"] = selected_prediction
    rows = []
    for segment, group in detail.groupby(
        "demand_pattern",
        dropna=False,
        observed=True,
    ):
        for strategy, column in [
            ("current", current_column),
            ("temporal_ensemble", "temporal_ensemble_pred"),
        ]:
            rows.append(
                {
                    "demand_pattern": segment,
                    "strategy": strategy,
                    **regression_metrics(
                        group["actual_usage"],
                        group[column],
                    ),
                }
            )
    return pd.DataFrame(rows)


def run_temporal_ensemble_tuning(
    *,
    backtest_path: Path = BACKTEST_PREDICTION_PATH,
    apply: bool = False,
    test_month_count: int = DEFAULT_TEST_MONTH_COUNT,
    weight_step: float = DEFAULT_WEIGHT_STEP,
) -> dict[str, object]:
    setup_logging()
    header = set(pd.read_csv(backtest_path, nrows=0).columns)
    model_columns = [
        column for column in DEFAULT_MODEL_COLUMNS if column in header
    ]
    required = {
        "year_month",
        "actual_usage",
        "demand_pattern",
        *model_columns,
    }
    missing = sorted(required - header)
    if missing:
        raise ValueError(
            f"Temporal ensemble input is missing columns: {missing}"
        )
    if len(model_columns) < 2:
        raise ValueError(
            "Temporal ensemble requires at least two available model columns"
        )
    current_column = _selected_manifest_column(
        MODEL_MANIFEST_PATH,
        header,
    )
    usecols = sorted(
        required
        | {
            current_column,
            "forecast_origin_month",
            "stock_item_key",
            "institution_code",
            "department",
            "item_code",
            "item_name",
        }
        & header
    )
    frame = pd.read_csv(
        backtest_path,
        usecols=usecols,
        parse_dates=["year_month"],
    )
    frame = frame[
        pd.to_numeric(frame["actual_usage"], errors="coerce")
        .notna()
    ].copy()
    frame["actual_usage"] = pd.to_numeric(
        frame["actual_usage"],
        errors="coerce",
    ).clip(lower=0)
    month_values = (
        frame["year_month"].dt.to_period("M").astype(str)
    )
    validation_months, test_months = split_validation_test_months(
        month_values.unique().tolist(),
        test_month_count=test_month_count,
    )
    validation = frame[month_values.isin(validation_months)].copy()
    test = frame[month_values.isin(test_months)].copy()
    if validation.empty or test.empty:
        raise ValueError(
            f"Empty temporal split: validation={len(validation)}, "
            f"test={len(test)}"
        )

    selected_weights, validation_candidates = (
        select_validation_weights(
            validation,
            model_columns,
            weight_step=weight_step,
        )
    )
    selected_validation_metrics = regression_metrics(
        validation["actual_usage"],
        blend_prediction(validation, selected_weights),
    )
    current_validation_metrics = regression_metrics(
        validation["actual_usage"],
        validation[current_column],
    )
    pattern_weights = select_pattern_weights(
        validation,
        model_columns,
        weight_step=weight_step,
    )
    pattern_validation_prediction = apply_pattern_weights(
        validation,
        pattern_weights,
        selected_weights,
    )
    pattern_validation_metrics = regression_metrics(
        validation["actual_usage"],
        pattern_validation_prediction,
    )
    selected_strategy = min(
        [
            ("global_weight_blend", selected_validation_metrics),
            ("pattern_weight_router", pattern_validation_metrics),
        ],
        key=lambda item: (
            float(item[1]["WAPE"]),
            abs(float(item[1]["BIAS_PCT"])),
            float(item[1]["RMSE"]),
        ),
    )[0]
    if selected_strategy == "pattern_weight_router":
        selected_validation_metrics = pattern_validation_metrics
        selected_test_prediction = apply_pattern_weights(
            test,
            pattern_weights,
            selected_weights,
        )
    else:
        selected_test_prediction = blend_prediction(
            test,
            selected_weights,
        )
    selected_test_metrics = regression_metrics(
        test["actual_usage"],
        selected_test_prediction,
    )
    current_test_metrics = regression_metrics(
        test["actual_usage"],
        test[current_column],
    )
    current_weights = {
        column: float(column == current_column)
        for column in model_columns
    }

    segment = _segment_evaluation(
        test,
        current_column,
        selected_test_prediction,
    )
    sample_columns = list(
        dict.fromkeys(
            column
            for column in [
                "year_month",
                "forecast_origin_month",
                "institution_code",
                "department",
                "item_code",
                "item_name",
                "stock_item_key",
                "demand_pattern",
                "actual_usage",
                current_column,
                *model_columns,
            ]
            if column in test.columns
        )
    )
    sample = test[sample_columns].copy()
    sample["temporal_ensemble_pred"] = selected_test_prediction
    sample["current_absolute_error"] = (
        sample["actual_usage"] - sample[current_column]
    ).abs()
    sample["ensemble_absolute_error"] = (
        sample["actual_usage"]
        - sample["temporal_ensemble_pred"]
    ).abs()
    sample["absolute_error_improvement"] = (
        sample["current_absolute_error"]
        - sample["ensemble_absolute_error"]
    )
    sample = sample.reindex(
        sample["absolute_error_improvement"]
        .abs()
        .sort_values(ascending=False)
        .index
    ).head(1000)

    report = {
        "version": POLICY_VERSION,
        "status": (
            "validation_selected_test_evaluated_applied"
            if apply
            else "validation_selected_test_evaluated_not_applied"
        ),
        "source": _portable_path(backtest_path),
        "model_train_end": VALID_END,
        "validation_months": validation_months,
        "test_months": test_months,
        "selection_metric": "minimum validation WAPE",
        "selection_did_not_use_test": True,
        "weight_step": weight_step,
        "candidate_count": int(len(validation_candidates)),
        "model_columns": model_columns,
        "current_prediction_column": current_column,
        "current_weights": current_weights,
        "selected_strategy": selected_strategy,
        "selected_weights": selected_weights,
        "pattern_weights": pattern_weights,
        "current_validation_metrics": current_validation_metrics,
        "global_validation_metrics": regression_metrics(
            validation["actual_usage"],
            blend_prediction(validation, selected_weights),
        ),
        "pattern_router_validation_metrics": (
            pattern_validation_metrics
        ),
        "selected_validation_metrics": selected_validation_metrics,
        "current_test_metrics": current_test_metrics,
        "selected_test_metrics": selected_test_metrics,
        "test_metric_changes": {
            key: _metric_delta(
                current_test_metrics,
                selected_test_metrics,
                key,
            )
            for key in ["WAPE", "MAE", "RMSE", "BIAS_PCT"]
        },
        "apply_to_prediction": apply,
        "limitations": [
            "Only five out-of-training forecast months are available.",
            "The three-month test estimates may change as new months arrive.",
            "Weights optimize usage WAPE and do not optimize holding or shortage cost.",
        ],
        "validation_candidates_path": _portable_path(
            FORECAST_ENSEMBLE_VALIDATION_PATH
        ),
        "segment_test_path": _portable_path(
            FORECAST_ENSEMBLE_SEGMENT_PATH
        ),
        "sample_path": _portable_path(FORECAST_ENSEMBLE_SAMPLE_PATH),
    }
    ensure_dirs(
        FORECAST_ENSEMBLE_POLICY_PATH.parent,
        FORECAST_ENSEMBLE_REPORT_PATH.parent,
        FORECAST_ENSEMBLE_VALIDATION_PATH.parent,
        FORECAST_ENSEMBLE_SEGMENT_PATH.parent,
        FORECAST_ENSEMBLE_SAMPLE_PATH.parent,
    )
    validation_candidates.to_csv(
        FORECAST_ENSEMBLE_VALIDATION_PATH,
        index=False,
    )
    segment.to_csv(FORECAST_ENSEMBLE_SEGMENT_PATH, index=False)
    sample.to_csv(
        FORECAST_ENSEMBLE_SAMPLE_PATH,
        index=False,
        encoding="utf-8-sig",
    )
    write_json(report, FORECAST_ENSEMBLE_REPORT_PATH)
    if apply:
        write_json(report, FORECAST_ENSEMBLE_POLICY_PATH)
    LOGGER.info(
        "Saved temporal ensemble report: %s",
        FORECAST_ENSEMBLE_REPORT_PATH,
    )
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Select forecast ensemble weights on validation months and "
            "evaluate once on the latest test months"
        )
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--test-month-count",
        type=int,
        default=DEFAULT_TEST_MONTH_COUNT,
    )
    parser.add_argument(
        "--weight-step",
        type=float,
        default=DEFAULT_WEIGHT_STEP,
    )
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    print(
        run_temporal_ensemble_tuning(
            apply=args.apply,
            test_month_count=args.test_month_count,
            weight_step=args.weight_step,
        )
    )
