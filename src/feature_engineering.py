import logging

import pandas as pd

from .config import (
    COMMODITY_RISK_SCORE_PATH,
    FEATURE_TABLE_PATH,
    GROUP_KEYS,
    HISTORICAL_MONTHLY_STOCK_PATH,
    MODULE_C_RISK_SCORE_PATH,
    MONTHLY_STOCK_PATH,
    NEWS_RISK_SCORE_PATH,
    OUTPUT_DIR,
    PROCESSED_DATA_DIR,
)
from .data_loader import load_stock_data
from .features import create_features
from .modeling.data_quality import write_forecast_data_quality_report
from .modeling.standardized_history import attach_standard_item_features
from .utils import ensure_dirs


LOGGER = logging.getLogger(__name__)
RISK_JOIN_KEYS = ["year_month", "stock_item_key"]
MONTHLY_FEATURE_COLUMNS = [
    "year_month",
    "institution_code",
    "department",
    "item_code",
    "item_name",
    "stock_item_key",
    "consumption_qty",
    "inbound_qty",
    "month_end_stock",
    "stockout_rate",
    "disposal_qty",
    "auto_disposal_adjustment_qty",
]


def _load_monthly_stock() -> pd.DataFrame:
    if MONTHLY_STOCK_PATH.exists():
        current = pd.read_parquet(
            MONTHLY_STOCK_PATH,
            columns=MONTHLY_FEATURE_COLUMNS,
        )
    else:
        monthly_stock = load_stock_data()
        ensure_dirs(PROCESSED_DATA_DIR)
        monthly_stock.to_parquet(
            MONTHLY_STOCK_PATH,
            index=False,
            compression="zstd",
        )
        current = monthly_stock[MONTHLY_FEATURE_COLUMNS]
    current["data_period"] = "current"
    frames = [current]
    if HISTORICAL_MONTHLY_STOCK_PATH.exists():
        historical = pd.read_parquet(
            HISTORICAL_MONTHLY_STOCK_PATH,
            columns=MONTHLY_FEATURE_COLUMNS,
        )
        historical["data_period"] = "historical"
        frames.insert(0, historical)
    combined = pd.concat(frames, ignore_index=True)
    return attach_standard_item_features(combined).drop(
        columns="local_item_key",
    )


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

    header = pd.read_csv(path, nrows=0).columns
    available = [column for column in value_columns if column in header]
    read_columns = [
        column
        for column in ["STD_YYYYMM", "year_month", "stock_item_key", *available]
        if column in header
    ]
    risk = _normalize_yyyymm(
        pd.read_csv(path, low_memory=False, usecols=read_columns)
    )
    if risk.empty:
        feature_table[value_columns] = 0.0
        return feature_table
    if not set(RISK_JOIN_KEYS).issubset(risk.columns):
        LOGGER.warning("Ignoring incompatible risk output without raw_stock keys: %s", path)
        feature_table[value_columns] = 0.0
        return feature_table
    risk["stock_item_key"] = risk["stock_item_key"].astype(str)
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
            "use_rolling_median_3": "rolling_median_3",
            "use_expanding_mean": "expanding_mean",
            "use_zero_rate_6": "zero_rate_6",
            "use_zero_rate_12": "zero_rate_12",
        }
    )
    feature_table["is_winter"] = feature_table["month"].isin([12, 1, 2]).astype(int)
    feature_table["is_summer"] = feature_table["month"].isin([6, 7, 8]).astype(int)
    feature_table["same_month_last_year"] = feature_table["lag_12"]
    lag_1 = feature_table["lag_1"].astype("float64")
    lag_12 = feature_table["lag_12"].astype("float64")
    feature_table["yoy_growth_rate"] = (
        ((lag_1 - lag_12) / lag_12.where(lag_12.ne(0))).fillna(0.0).astype("float32")
    )

    news_columns = ["disease_news_risk", "supply_news_risk", "material_news_risk", "total_news_risk"]
    commodity_columns = ["commodity_risk", "material_return_30d", "material_volatility_30d"]
    module_c_columns = [
        "module_c_demand_risk",
        "module_c_supply_news_risk",
        "module_c_material_news_risk",
        "module_c_market_price_risk",
        "module_c_trade_risk",
        "module_c_supply_risk",
        "module_c_total_risk",
        "module_c_signal_confidence",
    ]
    feature_table = _merge_risk(feature_table, NEWS_RISK_SCORE_PATH, news_columns)
    feature_table = _merge_risk(feature_table, COMMODITY_RISK_SCORE_PATH, commodity_columns)
    feature_table = _merge_risk(feature_table, MODULE_C_RISK_SCORE_PATH, module_c_columns)
    feature_table[[*news_columns, *commodity_columns, *module_c_columns]] = feature_table[
        [*news_columns, *commodity_columns, *module_c_columns]
    ].fillna(0.0).astype("float32")
    return feature_table.sort_values(GROUP_KEYS).reset_index(drop=True)


def run_feature_engineering() -> None:
    ensure_dirs(OUTPUT_DIR, PROCESSED_DATA_DIR)
    feature_table = build_feature_table()
    write_forecast_data_quality_report(feature_table, feature_table)
    feature_table = feature_table.dropna(subset=["lag_1", "rolling_mean_3"])
    feature_table.to_parquet(FEATURE_TABLE_PATH, index=False, compression="zstd")
    feature_table.to_parquet(
        PROCESSED_DATA_DIR / "stock_model_dataset.parquet",
        index=False,
        compression="zstd",
    )
    LOGGER.info("Saved raw_stock feature table: %s (%s rows)", FEATURE_TABLE_PATH, len(feature_table))


if __name__ == "__main__":
    run_feature_engineering()
