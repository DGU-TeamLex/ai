import json
import logging
import pickle

import numpy as np
import pandas as pd

from ..config import (
    EVALUATION_REPORT_PATH,
    FEATURE_TABLE_PATH,
    MODEL_DIR,
    MODEL_MANIFEST_PATH,
    MODEL_VARIANTS,
    OUTPUT_DIR,
    PREDICTION_PATH,
    TARGET_COLUMN,
    TEST_START,
)
from ..feature_engineering import run_feature_engineering
from ..utils import ensure_dirs, setup_logging
from .baseline import BASELINE_PREDICTION_COLUMNS, add_baseline_predictions
from .evaluation import build_evaluation_report
from .inventory_policy import add_inventory_recommendations
from .training import run_training, transform_features


LOGGER = logging.getLogger(__name__)


def _load_feature_table() -> pd.DataFrame:
    if not FEATURE_TABLE_PATH.exists():
        run_feature_engineering()
    return pd.read_csv(FEATURE_TABLE_PATH, parse_dates=["year_month"])


def _load_bundle(model_name: str) -> dict:
    path = MODEL_DIR / f"{model_name}.pkl"
    if not path.exists():
        run_training()
    with path.open("rb") as f:
        return pickle.load(f)


def _select_primary_prediction_column(predictions: pd.DataFrame) -> str:
    manifest_path = MODEL_MANIFEST_PATH
    if manifest_path.exists():
        with manifest_path.open("r", encoding="utf-8") as f:
            manifest = json.load(f)
        valid_rows = [row for row in manifest if row.get("WAPE") is not None]
        if valid_rows:
            best = sorted(valid_rows, key=lambda row: row["WAPE"])[0]["model"]
            return f"{best}_pred"

    report = build_evaluation_report(predictions)
    return report.iloc[0]["model"]


def build_predictions() -> pd.DataFrame:
    feature_table = _load_feature_table().dropna(subset=[TARGET_COLUMN, "lag_1", "rolling_mean_3"])
    test = feature_table[feature_table["year_month"] >= pd.Timestamp(TEST_START)].copy()

    predictions = test[
        [
            "year_month",
            "institution_code",
            "department",
            "item_code",
            "item_name",
            "stock_item_key",
            "month_end_stock",
            TARGET_COLUMN,
            "disease_news_risk",
            "supply_news_risk",
            "material_news_risk",
            "total_news_risk",
            "commodity_risk",
        ]
    ].rename(columns={TARGET_COLUMN: "actual_usage"})
    predictions["year_month"] = predictions["year_month"] + pd.offsets.MonthBegin(1)
    baseline_input = pd.concat([predictions, test.drop(columns=predictions.columns, errors="ignore")], axis=1)
    predictions = add_baseline_predictions(baseline_input)
    predictions = predictions.loc[:, ~predictions.columns.duplicated()]

    for model_name in MODEL_VARIANTS:
        bundle = _load_bundle(model_name)
        x_test = transform_features(test, bundle["preprocess"])
        predictions[f"{model_name}_pred"] = np.clip(bundle["model"].predict(x_test), 0, None)

    primary_col = _select_primary_prediction_column(predictions)
    predictions["primary_model"] = primary_col
    predictions["predicted_usage"] = predictions[primary_col]
    predictions["current_stock"] = predictions["month_end_stock"].fillna(0.0)
    predictions = add_inventory_recommendations(
        predictions,
        prediction_col="predicted_usage",
        current_stock_col="current_stock",
    )

    keep_cols = [
        "year_month",
        "institution_code",
        "department",
        "item_code",
        "item_name",
        "stock_item_key",
        "actual_usage",
        *BASELINE_PREDICTION_COLUMNS,
        "stock_model_a_usage_only_pred",
        "stock_model_b_news_pred",
        "stock_model_c_news_commodity_pred",
        "primary_model",
        "predicted_usage",
        "disease_news_risk",
        "supply_news_risk",
        "material_news_risk",
        "total_news_risk",
        "commodity_risk",
        "external_risk_score",
        "safety_stock",
        "risk_buffer",
        "recommended_stock",
        "current_stock",
        "recommended_order",
    ]
    return predictions[[col for col in keep_cols if col in predictions.columns]]


def run_prediction() -> None:
    setup_logging()
    ensure_dirs(OUTPUT_DIR)
    predictions = build_predictions()
    predictions.to_csv(PREDICTION_PATH, index=False)
    report = build_evaluation_report(predictions)
    report.to_csv(EVALUATION_REPORT_PATH, index=False)
    LOGGER.info("Saved predictions: %s (%s rows)", PREDICTION_PATH, len(predictions))
    LOGGER.info("Saved evaluation report: %s", EVALUATION_REPORT_PATH)


if __name__ == "__main__":
    run_prediction()
