import gc
import json
import logging
import pickle
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import (
    BACKTEST_PREDICTION_PATH,
    EVALUATION_REPORT_PATH,
    EVALUATION_SEGMENT_REPORT_PATH,
    FEATURE_TABLE_PATH,
    INVENTORY_STATUS_PATH,
    MODEL_DIR,
    MODEL_MANIFEST_PATH,
    OUTPUT_DIR,
    PREDICTION_PATH,
    SERIES_KEYS,
    TARGET_COLUMN,
    TEST_START,
)
from ..feature_engineering import run_feature_engineering
from ..material_mapping import attach_approved_material_mapping_metadata
from ..utils import ensure_dirs, setup_logging
from .baseline import BASELINE_PREDICTION_COLUMNS, add_baseline_predictions
from .classified_prediction import write_classified_prediction_outputs
from .data_quality import attach_standardization_metadata
from .evaluation import (
    build_evaluation_report,
    build_segment_evaluation_report,
    classify_demand_patterns,
)
from .inventory_policy import add_inventory_recommendations
from .training import run_training, transform_features


LOGGER = logging.getLogger(__name__)
INVENTORY_STATUS_PREDICTION_COLUMNS = [
    "stock_item_key",
    "mean_daily_usage",
    "daily_demand_stddev",
    "raw_mean_daily_usage",
    "raw_daily_demand_stddev",
    "mu_forecast_3m_92d",
    "observation_period_days",
    "zero_stock_reason",
    "inventory_action",
    "urgent_shortage",
    "exact_group_total_stock",
]
RISK_COLUMNS = [
    "disease_news_risk",
    "supply_news_risk",
    "material_news_risk",
    "total_news_risk",
    "commodity_risk",
    "module_c_demand_risk",
    "module_c_supply_news_risk",
    "module_c_material_news_risk",
    "module_c_market_price_risk",
    "module_c_trade_risk",
    "module_c_supply_risk",
    "module_c_total_risk",
    "module_c_signal_confidence",
]


def attach_current_inventory_status_parameters(
    frame: pd.DataFrame,
    path: Path = INVENTORY_STATUS_PATH,
) -> pd.DataFrame:
    if not path.exists():
        LOGGER.warning(
            "Inventory status output is unavailable; daily SS/ROP inputs are omitted: %s",
            path,
        )
        return frame
    header = pd.read_csv(path, nrows=0).columns
    missing = sorted(set(INVENTORY_STATUS_PREDICTION_COLUMNS) - set(header))
    if missing:
        raise ValueError(f"Inventory status output is missing columns: {missing}")
    status = pd.read_csv(
        path,
        usecols=INVENTORY_STATUS_PREDICTION_COLUMNS,
        dtype={"stock_item_key": str},
    )
    if status["stock_item_key"].duplicated().any():
        raise ValueError("Inventory status output is not unique by stock_item_key")
    result = frame.merge(
        status,
        on="stock_item_key",
        how="left",
        validate="many_to_one",
    )
    result["inventory_status_parameters_available"] = result[
        ["mean_daily_usage", "daily_demand_stddev"]
    ].notna().all(axis=1)
    return result


def _load_feature_table(manifest: list[dict]) -> pd.DataFrame:
    if not FEATURE_TABLE_PATH.exists():
        run_feature_engineering()
    columns = {
        "year_month",
        "forecast_month",
        *SERIES_KEYS,
        "item_name",
        "stock_item_key",
        "month_end_stock",
        "history_months",
        "demand_qty",
        TARGET_COLUMN,
        "lag_1",
        "rolling_mean_3",
        "rolling_median_3",
        "rolling_mean_6",
        "same_month_last_year",
        "expanding_mean",
        *RISK_COLUMNS,
    }
    for row in manifest:
        if row.get("method_type") == "machine_learning" and row.get("status") == "ready":
            columns.update(_load_bundle(row["model"])["feature_cols"])
    return pd.read_parquet(FEATURE_TABLE_PATH, columns=sorted(columns))


def _load_manifest() -> list[dict]:
    if not MODEL_MANIFEST_PATH.exists():
        run_training()
    with MODEL_MANIFEST_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def _load_bundle(model_name: str) -> dict:
    path = MODEL_DIR / f"{model_name}.pkl"
    if not path.exists():
        raise FileNotFoundError(f"Trained model bundle not found: {path}")
    with path.open("rb") as file:
        return pickle.load(file)


def _selected_prediction_column(manifest: list[dict], available_columns: set[str]) -> str:
    selected = [row for row in manifest if row.get("selected_on_validation")]
    ranked = selected or sorted(
        [row for row in manifest if row.get("WAPE") is not None],
        key=lambda row: row["WAPE"],
    )
    for row in ranked:
        prediction_column = f"{row['model']}_pred"
        if prediction_column in available_columns:
            return prediction_column
    raise ValueError("No validation-selected prediction column is available")


def _add_trained_model_predictions(
    frame: pd.DataFrame,
    manifest: list[dict],
) -> pd.DataFrame:
    result = frame
    trained_models = [
        row["model"]
        for row in manifest
        if row.get("method_type") == "machine_learning" and row.get("status") == "ready"
    ]
    for model_name in trained_models:
        bundle = _load_bundle(model_name)
        features = transform_features(result, bundle["preprocess"])
        if str(bundle["algorithm"]).startswith("lightgbm"):
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="X does not have valid feature names")
                raw_prediction = bundle["model"].predict(features, validate_features=False)
        else:
            raw_prediction = bundle["model"].predict(features)
        result[f"{model_name}_pred"] = np.clip(raw_prediction, 0, None)
    return result


def _build_prediction_frame(
    source: pd.DataFrame,
    manifest: list[dict],
    demand_patterns: pd.DataFrame,
    prediction_type: str,
) -> pd.DataFrame:
    enriched = add_baseline_predictions(source)
    enriched = _add_trained_model_predictions(enriched, manifest)
    model_prediction_columns = [
        column
        for column in enriched.columns
        if column.endswith("_pred") and column not in BASELINE_PREDICTION_COLUMNS
    ]
    prediction_columns = [*BASELINE_PREDICTION_COLUMNS, *model_prediction_columns]

    output_columns = [
        "year_month",
        "forecast_month",
        "institution_code",
        "department",
        "item_code",
        "item_name",
        "stock_item_key",
        "month_end_stock",
        "history_months",
        TARGET_COLUMN,
        *RISK_COLUMNS,
        *prediction_columns,
    ]
    output = enriched[[column for column in output_columns if column in enriched.columns]].copy()
    output = output.rename(
        columns={
            "year_month": "forecast_origin_month",
            "forecast_month": "year_month",
            TARGET_COLUMN: "actual_usage",
        }
    )
    if prediction_type == "future":
        output["actual_usage"] = pd.NA

    output = output.merge(demand_patterns, on=SERIES_KEYS, how="left", validate="many_to_one")
    output["demand_pattern"] = output["demand_pattern"].fillna("new_series")
    output = attach_standardization_metadata(output)

    primary_column = _selected_prediction_column(manifest, set(prediction_columns))
    output["primary_model"] = primary_column
    output["predicted_usage"] = output[primary_column]
    primary_name = primary_column.removesuffix("_pred")
    primary_spec = next(
        (row for row in manifest if row.get("model") == primary_name),
        {},
    )
    output["external_demand_signal_in_forecast"] = bool(
        primary_spec.get("uses_news", False)
        or primary_spec.get("uses_module_c", False)
    )
    output["current_stock"] = output["month_end_stock"].fillna(0.0)
    output["prediction_type"] = prediction_type
    output = attach_approved_material_mapping_metadata(output)
    if prediction_type == "future":
        output = attach_current_inventory_status_parameters(output)

    current_month = pd.Timestamp.now().to_period("M").to_timestamp()
    origin_month = pd.to_datetime(output["forecast_origin_month"])
    output["data_age_months"] = (
        (current_month.year - origin_month.dt.year) * 12
        + current_month.month
        - origin_month.dt.month
    )
    output["is_stale_data"] = output["data_age_months"].gt(1)
    return add_inventory_recommendations(
        output,
        prediction_col="predicted_usage",
        current_stock_col="current_stock",
    )


def build_prediction_outputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    manifest = _load_manifest()
    feature_table = _load_feature_table(manifest)
    eligible_mask = (
        feature_table["lag_1"].notna()
        & feature_table["lag_1"].ge(0)
        & feature_table["rolling_mean_3"].notna()
    )
    demand_patterns = classify_demand_patterns(feature_table)

    test = feature_table[
        eligible_mask
        & feature_table[TARGET_COLUMN].notna()
        & feature_table[TARGET_COLUMN].ge(0)
        & feature_table["year_month"].ge(pd.Timestamp(TEST_START))
    ].copy()
    latest_origin = feature_table.loc[eligible_mask, "year_month"].max()
    future = feature_table[eligible_mask & feature_table["year_month"].eq(latest_origin)].copy()
    del feature_table, eligible_mask
    gc.collect()
    if test.empty or future.empty:
        raise ValueError(f"Prediction inputs are empty: test={len(test)}, future={len(future)}")

    backtest = _build_prediction_frame(
        test,
        manifest,
        demand_patterns,
        prediction_type="backtest",
    )
    current_forecast = _build_prediction_frame(
        future,
        manifest,
        demand_patterns,
        prediction_type="future",
    )
    return backtest, current_forecast


def build_predictions() -> pd.DataFrame:
    _, current_forecast = build_prediction_outputs()
    return current_forecast


def run_prediction() -> None:
    setup_logging()
    ensure_dirs(OUTPUT_DIR)
    backtest, current_forecast = build_prediction_outputs()
    backtest.to_csv(BACKTEST_PREDICTION_PATH, index=False)
    current_forecast.to_csv(PREDICTION_PATH, index=False)
    write_classified_prediction_outputs(current_forecast)

    report = build_evaluation_report(backtest)
    report.to_csv(EVALUATION_REPORT_PATH, index=False)
    segment_report = build_segment_evaluation_report(backtest)
    segment_report.to_csv(EVALUATION_SEGMENT_REPORT_PATH, index=False)
    LOGGER.info("Saved current forecast: %s (%s rows)", PREDICTION_PATH, len(current_forecast))
    LOGGER.info("Saved backtest predictions: %s (%s rows)", BACKTEST_PREDICTION_PATH, len(backtest))
    LOGGER.info("Saved evaluation report: %s", EVALUATION_REPORT_PATH)


if __name__ == "__main__":
    run_prediction()
