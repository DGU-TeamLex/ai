import numpy as np
import pandas as pd

from ..config import (
    BACKTEST_PREDICTION_PATH,
    EVALUATION_REPORT_PATH,
    EVALUATION_SEGMENT_REPORT_PATH,
    OUTPUT_DIR,
    SERIES_KEYS,
    TRAIN_START,
    VALID_END,
)
from ..utils import ensure_dirs, setup_logging
from .metrics import regression_metrics


def build_evaluation_report(predictions: pd.DataFrame) -> pd.DataFrame:
    pred_cols = [col for col in predictions.columns if col.endswith("_pred") or col == "predicted_usage"]
    rows = []
    for pred_col in pred_cols:
        row = {"model": pred_col}
        row.update(regression_metrics(predictions["actual_usage"], predictions[pred_col]))
        rows.append(row)
    return pd.DataFrame(rows).sort_values("WAPE", na_position="last").reset_index(drop=True)


def classify_demand_patterns(feature_table: pd.DataFrame) -> pd.DataFrame:
    history_mask = (
        feature_table["year_month"].between(
            pd.Timestamp(TRAIN_START),
            pd.Timestamp(VALID_END),
        )
        & feature_table["demand_qty"].notna()
    )
    history = feature_table.loc[history_mask, [*SERIES_KEYS, "demand_qty"]].copy()
    history = history.assign(is_nonzero=history["demand_qty"].gt(0).astype("int8"))
    summary = history.groupby(SERIES_KEYS, observed=True).agg(
        observed_months=("demand_qty", "size"),
        nonzero_months=("is_nonzero", "sum"),
    )
    nonzero = history[history["demand_qty"].gt(0)].groupby(SERIES_KEYS, observed=True)["demand_qty"]
    nonzero_stats = nonzero.agg(nonzero_mean="mean", nonzero_std="std")
    summary = summary.join(nonzero_stats, how="left").reset_index()
    summary["adi"] = summary["observed_months"] / summary["nonzero_months"].replace(0, np.nan)
    summary["cv2"] = (summary["nonzero_std"] / summary["nonzero_mean"]).pow(2).fillna(0.0)

    conditions = [
        summary["observed_months"].lt(3),
        summary["nonzero_months"].eq(0),
        summary["adi"].lt(1.32) & summary["cv2"].lt(0.49),
        summary["adi"].ge(1.32) & summary["cv2"].lt(0.49),
        summary["adi"].lt(1.32) & summary["cv2"].ge(0.49),
    ]
    choices = ["insufficient_history", "all_zero", "smooth", "intermittent", "erratic"]
    summary["demand_pattern"] = np.select(conditions, choices, default="lumpy")
    return summary[[*SERIES_KEYS, "observed_months", "adi", "cv2", "demand_pattern"]]


def build_segment_evaluation_report(predictions: pd.DataFrame) -> pd.DataFrame:
    segment_columns = [
        column
        for column in ["demand_pattern", "normalization_status", "item_group_id_candidate"]
        if column in predictions.columns
    ]
    pred_columns = [
        column
        for column in predictions.columns
        if column.endswith("_pred") or column == "predicted_usage"
    ]
    rows = []
    for segment_column in segment_columns:
        for segment_value, segment in predictions.groupby(segment_column, dropna=False):
            for pred_column in pred_columns:
                row = {
                    "segment_type": segment_column,
                    "segment_value": segment_value,
                    "model": pred_column,
                }
                row.update(regression_metrics(segment["actual_usage"], segment[pred_column]))
                rows.append(row)
    return pd.DataFrame(rows).sort_values(
        ["segment_type", "segment_value", "WAPE"],
        na_position="last",
    ).reset_index(drop=True)


def run_evaluation() -> None:
    setup_logging()
    ensure_dirs(OUTPUT_DIR)
    predictions = pd.read_csv(BACKTEST_PREDICTION_PATH)
    report = build_evaluation_report(predictions)
    report.to_csv(EVALUATION_REPORT_PATH, index=False)
    segment_report = build_segment_evaluation_report(predictions)
    segment_report.to_csv(EVALUATION_SEGMENT_REPORT_PATH, index=False)


if __name__ == "__main__":
    run_evaluation()
