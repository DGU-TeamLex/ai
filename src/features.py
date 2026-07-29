import logging
from pathlib import Path

import pandas as pd

from .config import CATEGORICAL_FEATURES, EXTERNAL_FEATURE_PATH, SERIES_KEYS


LOGGER = logging.getLogger(__name__)

LAG_COLUMNS = {
    "demand_qty": [1, 2, 3, 6, 12],
    "inbound_qty": [1, 2, 3],
    "month_end_stock": [1, 2, 3],
    "stockout_rate": [1, 2, 3],
    "disposal_qty": [1, 2, 3],
    "auto_disposal_adjustment_qty": [1],
}
ROLLING_WINDOWS = [3, 6, 12]


def add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    result["forecast_month"] = result["year_month"] + pd.offsets.MonthBegin(1)
    result["year"] = result["forecast_month"].dt.year
    result["month"] = result["forecast_month"].dt.month
    result["quarter"] = result["forecast_month"].dt.quarter
    return result


def add_external_features(df: pd.DataFrame, external_path: Path = EXTERNAL_FEATURE_PATH) -> pd.DataFrame:
    if not external_path.exists():
        return df

    external = pd.read_csv(external_path)
    required = {"year_month", "institution_code"}
    if not required.issubset(external.columns):
        raise ValueError(f"external_features.csv must include {sorted(required)}")
    external["year_month"] = pd.to_datetime(external["year_month"]).dt.to_period("M").dt.to_timestamp()
    external["institution_code"] = external["institution_code"].astype(str)
    return df.merge(external, on=["year_month", "institution_code"], how="left")


def create_features(monthly_stock: pd.DataFrame) -> pd.DataFrame:
    df = monthly_stock.copy().sort_values([*SERIES_KEYS, "year_month"]).reset_index(drop=True)
    for column in CATEGORICAL_FEATURES:
        if column in df.columns:
            df[column] = df[column].astype("category")
    for column in ["stock_item_key", "item_name"]:
        if column in df.columns:
            df[column] = df[column].astype("category")
    df["negative_consumption_flag"] = df["consumption_qty"].lt(0).astype("int8")
    df["demand_qty"] = df["consumption_qty"].where(df["consumption_qty"].ge(0))

    series_changed = df[SERIES_KEYS].ne(df[SERIES_KEYS].shift()).any(axis=1)
    previous_month = df["year_month"].shift() + pd.offsets.MonthBegin(1)
    segment_started = series_changed | df["year_month"].ne(previous_month)
    df["series_segment_id"] = segment_started.cumsum().astype("int32")

    df = add_time_features(df)
    segment_grouped = df.groupby("series_segment_id", sort=False)
    series_grouped = df.groupby(SERIES_KEYS, sort=False, observed=True)
    df["history_months"] = (segment_grouped.cumcount() + 1).astype("int16")
    df["series_observation_count"] = (series_grouped.cumcount() + 1).astype("int16")

    for source_col, lags in LAG_COLUMNS.items():
        for lag in lags:
            # The current row is the forecast origin, so its value is lag 1 for next month.
            shift_periods = lag - 1
            output_col = f"{source_col}_lag_{lag}"
            if source_col == "demand_qty":
                output_col = f"use_lag_{lag}"
            df[output_col] = segment_grouped[source_col].shift(shift_periods).astype("float32")

    for window in ROLLING_WINDOWS:
        rolling = segment_grouped["demand_qty"].rolling(window, min_periods=1)
        df[f"use_rolling_mean_{window}"] = (
            rolling.mean().reset_index(level=0, drop=True).reindex(df.index)
        ).astype("float32")
        df[f"use_rolling_std_{window}"] = (
            segment_grouped["demand_qty"]
            .rolling(window, min_periods=2)
            .std()
            .reset_index(level=0, drop=True)
            .reindex(df.index)
        ).astype("float32")

    df["use_rolling_median_3"] = (
        segment_grouped["demand_qty"]
        .rolling(3, min_periods=1)
        .median()
        .reset_index(level=0, drop=True)
        .reindex(df.index)
    ).astype("float32")
    valid_observations = df["demand_qty"].notna().astype("int16").groupby(df["series_segment_id"]).cumsum()
    cumulative_demand = df["demand_qty"].fillna(0).groupby(df["series_segment_id"]).cumsum()
    df["use_expanding_mean"] = (
        cumulative_demand / valid_observations.replace(0, pd.NA)
    ).astype("float32")

    zero_indicator = df["demand_qty"].eq(0).astype("float32").where(df["demand_qty"].notna())
    df["use_zero_rate_6"] = (
        zero_indicator.groupby(df["series_segment_id"])
        .rolling(6, min_periods=1)
        .mean()
        .reset_index(level=0, drop=True)
        .reindex(df.index)
    ).astype("float32")
    df["use_zero_rate_12"] = (
        zero_indicator.groupby(df["series_segment_id"])
        .rolling(12, min_periods=1)
        .mean()
        .reset_index(level=0, drop=True)
        .reindex(df.index)
    ).astype("float32")

    df["target_next_month"] = segment_grouped["demand_qty"].shift(-1)
    return add_external_features(df)


def get_model_feature_columns(df: pd.DataFrame) -> list[str]:
    excluded = {
        "target_next_month",
        "year_month",
        "forecast_month",
        "stock_item_key",
        "item_name",
        "vendor_code",
        "first_date",
        "last_date",
        "demand_qty",
        "series_segment_id",
    }
    return [column for column in df.columns if column not in excluded]
