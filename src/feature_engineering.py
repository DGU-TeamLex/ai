import logging

import pandas as pd

from .config import (
    COMMODITY_RISK_SCORE_PATH,
    FEATURE_TABLE_PATH,
    GROUP_KEYS,
    MONTHLY_STOCK_PATH,
    NEWS_RISK_SCORE_PATH,
    OUTPUT_DIR,
    PROCESSED_DATA_DIR,
)
from .data_loader import load_stock_data
from .features import create_features
from .utils import ensure_dirs


LOGGER = logging.getLogger(__name__)
RISK_JOIN_KEYS = ["year_month", "stock_item_key"]


def _load_monthly_stock() -> pd.DataFrame:
    if MONTHLY_STOCK_PATH.exists():
        return pd.read_csv(MONTHLY_STOCK_PATH, parse_dates=["year_month", "first_date", "last_date"])
    monthly_stock = load_stock_data()
    ensure_dirs(PROCESSED_DATA_DIR)
    monthly_stock.to_csv(MONTHLY_STOCK_PATH, index=False)
    return monthly_stock


def _normalize_yyyymm(df: pd.DataFrame, column: str = "STD_YYYYMM") -> pd.DataFrame:
    result = df.copy()
    result["year_month"] = pd.to_datetime(result[column].astype(str), errors="coerce").dt.to_period("M").dt.to_timestamp()
    return result.drop(columns=[column])


def _merge_risk(
    feature_table: pd.DataFrame,
    path,
    value_columns: list[str],
) -> pd.DataFrame:
    if not path.exists():
        feature_table[value_columns] = 0.0
        return feature_table

    risk = _normalize_yyyymm(pd.read_csv(path))
    if not set(RISK_JOIN_KEYS).issubset(risk.columns):
        LOGGER.warning("Ignoring incompatible risk output without raw_stock keys: %s", path)
        feature_table[value_columns] = 0.0
        return feature_table
    risk["stock_item_key"] = risk["stock_item_key"].astype(str)
    available = [column for column in value_columns if column in risk.columns]
    merged = feature_table.merge(risk[[*RISK_JOIN_KEYS, *available]], on=RISK_JOIN_KEYS, how="left")
    for column in value_columns:
        if column not in merged.columns:
            merged[column] = 0.0
    return merged


def build_feature_table() -> pd.DataFrame:
    feature_table = create_features(_load_monthly_stock())
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
    feature_table["yoy_growth_rate"] = (
        (feature_table["lag_1"] - feature_table["lag_12"])
        / feature_table["lag_12"].replace(0, pd.NA)
    ).astype("float64").fillna(0.0)

    news_columns = ["disease_news_risk", "supply_news_risk", "material_news_risk", "total_news_risk"]
    commodity_columns = ["commodity_risk", "material_return_30d", "material_volatility_30d"]
    feature_table = _merge_risk(feature_table, NEWS_RISK_SCORE_PATH, news_columns)
    feature_table = _merge_risk(feature_table, COMMODITY_RISK_SCORE_PATH, commodity_columns)
    feature_table[[*news_columns, *commodity_columns]] = feature_table[
        [*news_columns, *commodity_columns]
    ].fillna(0.0)
    return feature_table.sort_values(GROUP_KEYS).reset_index(drop=True)


def run_feature_engineering() -> None:
    ensure_dirs(OUTPUT_DIR, PROCESSED_DATA_DIR)
    feature_table = build_feature_table().dropna(subset=["target_usage", "lag_1", "rolling_mean_3"])
    feature_table.to_csv(FEATURE_TABLE_PATH, index=False)
    feature_table.to_csv(PROCESSED_DATA_DIR / "stock_model_dataset.csv", index=False)
    LOGGER.info("Saved raw_stock feature table: %s (%s rows)", FEATURE_TABLE_PATH, len(feature_table))


if __name__ == "__main__":
    run_feature_engineering()
