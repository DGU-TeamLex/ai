import logging

import pandas as pd

from .config import (
    COMMODITY_RISK_SCORE_PATH,
    FEATURE_TABLE_PATH,
    GROUP_KEYS,
    MONTHLY_USAGE_PATH,
    NEWS_RISK_SCORE_PATH,
    OUTPUT_DIR,
    PROCESSED_DATA_DIR,
)
from .data_loader import load_usage_data
from .features import create_features
from .utils import ensure_dirs, setup_logging


LOGGER = logging.getLogger(__name__)


def _load_monthly_usage() -> pd.DataFrame:
    if MONTHLY_USAGE_PATH.exists():
        return pd.read_csv(MONTHLY_USAGE_PATH, parse_dates=["year_month"])
    LOGGER.info("Monthly usage table not found. Running raw data loader.")
    usage = load_usage_data()
    ensure_dirs(PROCESSED_DATA_DIR)
    usage.to_csv(MONTHLY_USAGE_PATH, index=False)
    return usage


def _normalize_yyyymm(df: pd.DataFrame, column: str = "STD_YYYYMM") -> pd.DataFrame:
    result = df.copy()
    result["year_month"] = pd.to_datetime(result[column].astype(str), errors="coerce").dt.to_period("M").dt.to_timestamp()
    return result.drop(columns=[column])


def _merge_news_risk(feature_table: pd.DataFrame) -> pd.DataFrame:
    if not NEWS_RISK_SCORE_PATH.exists():
        for col in ["disease_news_risk", "supply_news_risk", "material_news_risk", "total_news_risk"]:
            feature_table[col] = 0.0
        return feature_table

    risk = _normalize_yyyymm(pd.read_csv(NEWS_RISK_SCORE_PATH))
    risk["MED_DEVICE_5"] = risk["MED_DEVICE_5"].astype(str)
    return feature_table.merge(risk, on=["year_month", "MED_DEVICE_5"], how="left")


def _merge_commodity_risk(feature_table: pd.DataFrame) -> pd.DataFrame:
    if not COMMODITY_RISK_SCORE_PATH.exists():
        for col in ["commodity_risk", "material_return_30d", "material_volatility_30d"]:
            feature_table[col] = 0.0
        return feature_table

    risk = _normalize_yyyymm(pd.read_csv(COMMODITY_RISK_SCORE_PATH))
    risk["MED_DEVICE_5"] = risk["MED_DEVICE_5"].astype(str)
    return feature_table.merge(risk, on=["year_month", "MED_DEVICE_5"], how="left")


def build_feature_table() -> pd.DataFrame:
    monthly_usage = _load_monthly_usage()
    feature_table = create_features(monthly_usage)
    feature_table = feature_table.rename(
        columns={
            "target_next_month": "target_usage",
            "use_lag_1": "lag_1",
            "use_lag_2": "lag_2",
            "use_lag_3": "lag_3",
            "use_lag_6": "lag_6",
            "use_lag_12": "lag_12",
            "use_rolling_mean_3": "rolling_mean_3",
            "use_rolling_mean_6": "rolling_mean_6",
            "use_rolling_mean_12": "rolling_mean_12",
            "use_rolling_std_3": "rolling_std_3",
            "use_rolling_std_6": "rolling_std_6",
            "use_rolling_std_12": "rolling_std_12",
        }
    )
    feature_table["is_winter"] = feature_table["month"].isin([12, 1, 2]).astype(int)
    feature_table["is_summer"] = feature_table["month"].isin([6, 7, 8]).astype(int)
    feature_table["same_month_last_year"] = feature_table["lag_12"]
    feature_table["yoy_growth_rate"] = (feature_table["lag_1"] - feature_table["lag_12"]) / feature_table[
        "lag_12"
    ].replace(0, pd.NA)
    feature_table["yoy_growth_rate"] = feature_table["yoy_growth_rate"].astype("float64").fillna(0.0)

    feature_table = _merge_news_risk(feature_table)
    feature_table = _merge_commodity_risk(feature_table)
    risk_cols = [
        "disease_news_risk",
        "supply_news_risk",
        "material_news_risk",
        "total_news_risk",
        "commodity_risk",
        "material_return_30d",
        "material_volatility_30d",
    ]
    feature_table[risk_cols] = feature_table[risk_cols].fillna(0.0)
    return feature_table.sort_values(GROUP_KEYS).reset_index(drop=True)


def run_feature_engineering() -> None:
    setup_logging()
    ensure_dirs(OUTPUT_DIR, PROCESSED_DATA_DIR)
    feature_table = build_feature_table()
    feature_table = feature_table.dropna(subset=["target_usage", "lag_1", "lag_12", "rolling_mean_3"])
    feature_table.to_csv(FEATURE_TABLE_PATH, index=False)
    feature_table.to_csv(PROCESSED_DATA_DIR / "model_dataset.csv", index=False)
    LOGGER.info("Saved feature table: %s (%s rows)", FEATURE_TABLE_PATH, len(feature_table))


if __name__ == "__main__":
    run_feature_engineering()

