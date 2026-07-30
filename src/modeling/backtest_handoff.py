from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from ..config import BACKTEST_PREDICTION_PATH, OUTPUT_DIR, PROJECT_ROOT


DEFAULT_ROW_OUTPUT = (
    PROJECT_ROOT / "data" / "handoff" / "backtest_predictions.parquet"
)
DEFAULT_SEGMENT_INPUT = OUTPUT_DIR / "stock_evaluation_by_segment.csv"
DEFAULT_SEGMENT_OUTPUT = (
    PROJECT_ROOT / "data" / "handoff" / "backtest_segment_evaluation.parquet"
)

ROW_COLUMNS = [
    "forecast_origin_month",
    "year_month",
    "institution_code",
    "department",
    "item_code",
    "item_name",
    "stock_item_key",
    "standard_item_key",
    "standard_item_definition_key",
    "standard_item_group_id",
    "standard_item_family_id",
    "standard_item_subtype_id",
    "standard_item_specification",
    "standard_item_unit_code",
    "standardization_match_method",
    "data_period",
    "history_months",
    "actual_usage",
    "demand_pattern",
    "primary_model",
    "predicted_usage",
    "external_demand_signal_in_forecast",
    "prediction_type",
]
ROW_KEY = [
    "forecast_origin_month",
    "year_month",
    "institution_code",
    "department",
    "item_code",
]
SEGMENT_COLUMNS = [
    "segment_type",
    "segment_value",
    "model",
    "N",
    "ACTUAL_SUM",
    "PREDICTION_SUM",
    "MAE",
    "RMSE",
    "RMSLE",
    "MAPE",
    "SMAPE",
    "WAPE",
    "BIAS",
    "BIAS_PCT",
    "ZERO_ACTUAL_RATE",
]


def _require_columns(
    available: list[str],
    required: list[str],
    label: str,
) -> None:
    missing = sorted(set(required) - set(available))
    if missing:
        raise ValueError(f"{label} is missing required columns: {missing}")


def build_backtest_handoff(frame: pd.DataFrame) -> pd.DataFrame:
    _require_columns(frame.columns.tolist(), ROW_COLUMNS, "backtest")
    result = frame[ROW_COLUMNS].copy()
    if result[ROW_KEY].isna().any().any():
        raise ValueError("backtest handoff contains null row keys")
    if result.duplicated(ROW_KEY).any():
        raise ValueError("backtest handoff contains duplicate forecast rows")
    for column in ["actual_usage", "predicted_usage"]:
        result[column] = pd.to_numeric(result[column], errors="coerce")
        if result[column].isna().any():
            raise ValueError(f"backtest handoff contains invalid {column}")
        if result[column].lt(0).any():
            raise ValueError(f"backtest handoff contains negative {column}")
    return result.sort_values(ROW_KEY, kind="mergesort").reset_index(drop=True)


def build_segment_handoff(frame: pd.DataFrame) -> pd.DataFrame:
    _require_columns(
        frame.columns.tolist(),
        SEGMENT_COLUMNS,
        "segment evaluation",
    )
    result = frame[SEGMENT_COLUMNS].copy()
    key = ["segment_type", "segment_value", "model"]
    if result[key].isna().any().any() or result.duplicated(key).any():
        raise ValueError("segment evaluation has invalid or duplicate keys")
    return result.sort_values(key, kind="mergesort").reset_index(drop=True)


def export_backtest_handoff(
    *,
    prediction_path: Path = BACKTEST_PREDICTION_PATH,
    segment_path: Path = DEFAULT_SEGMENT_INPUT,
    row_output_path: Path = DEFAULT_ROW_OUTPUT,
    segment_output_path: Path = DEFAULT_SEGMENT_OUTPUT,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    header = pd.read_csv(prediction_path, nrows=0).columns.tolist()
    _require_columns(header, ROW_COLUMNS, "backtest")
    predictions = build_backtest_handoff(
        pd.read_csv(prediction_path, usecols=ROW_COLUMNS)
    )
    segments = build_segment_handoff(pd.read_csv(segment_path))

    row_output_path.parent.mkdir(parents=True, exist_ok=True)
    segment_output_path.parent.mkdir(parents=True, exist_ok=True)
    predictions.to_parquet(
        row_output_path,
        index=False,
        compression="zstd",
    )
    segments.to_parquet(
        segment_output_path,
        index=False,
        compression="zstd",
    )
    return predictions, segments


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--prediction-path",
        type=Path,
        default=BACKTEST_PREDICTION_PATH,
    )
    parser.add_argument(
        "--segment-path",
        type=Path,
        default=DEFAULT_SEGMENT_INPUT,
    )
    parser.add_argument(
        "--row-output-path",
        type=Path,
        default=DEFAULT_ROW_OUTPUT,
    )
    parser.add_argument(
        "--segment-output-path",
        type=Path,
        default=DEFAULT_SEGMENT_OUTPUT,
    )
    args = parser.parse_args()
    predictions, segments = export_backtest_handoff(
        prediction_path=args.prediction_path,
        segment_path=args.segment_path,
        row_output_path=args.row_output_path,
        segment_output_path=args.segment_output_path,
    )
    print(
        f"행별 handoff: {args.row_output_path} ({len(predictions):,}행)"
    )
    print(
        f"세그먼트 handoff: {args.segment_output_path} ({len(segments):,}행)"
    )


if __name__ == "__main__":
    main()
