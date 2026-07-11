import logging
from pathlib import Path

import pandas as pd

from .config import EXTERNAL_FEATURE_PATH, GROUP_KEYS, SERIES_KEYS


LOGGER = logging.getLogger(__name__)

LAG_COLUMNS = {
    "consumption_qty": [1, 2, 3, 6, 12],
    "inbound_qty": [1, 2, 3],
    "month_end_stock": [1, 2, 3],
    "stockout_rate": [1, 2, 3],
    "disposal_qty": [1, 2, 3],
    "auto_disposal_adjustment_qty": [1],
}
ROLLING_WINDOWS = [3, 6, 12]


def add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    result["year"] = result["year_month"].dt.year
    result["month"] = result["year_month"].dt.month
    result["quarter"] = result["year_month"].dt.quarter
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
    df = monthly_stock.copy().sort_values(GROUP_KEYS).reset_index(drop=True)
    for column in SERIES_KEYS:
        df[column] = df[column].astype(str)
    df = add_time_features(df)

    grouped = df.groupby(SERIES_KEYS, sort=False)
    for source_col, lags in LAG_COLUMNS.items():
        for lag in lags:
            # The current row is the forecast origin, so its value is lag 1 for next month.
            shift_periods = lag - 1
            output_col = f"{source_col}_lag_{lag}"
            if source_col == "consumption_qty":
                output_col = f"use_lag_{lag}"
            df[output_col] = grouped[source_col].shift(shift_periods)

    for window in ROLLING_WINDOWS:
        rolling = df["consumption_qty"].groupby([df[column] for column in SERIES_KEYS])
        df[f"use_rolling_mean_{window}"] = rolling.transform(
            lambda values: values.rolling(window, min_periods=1).mean()
        )
        df[f"use_rolling_std_{window}"] = rolling.transform(
            lambda values: values.rolling(window, min_periods=2).std()
        )

    df["target_next_month"] = grouped["consumption_qty"].shift(-1)
    next_year_month = grouped["year_month"].shift(-1)
    expected_next = df["year_month"] + pd.offsets.MonthBegin(1)
    df.loc[next_year_month.ne(expected_next), "target_next_month"] = pd.NA
    return add_external_features(df)


def get_model_feature_columns(df: pd.DataFrame) -> list[str]:
    excluded = {
        "target_next_month",
        "year_month",
        "stock_item_key",
        "item_name",
        "vendor_code",
        "first_date",
        "last_date",
    }
    return [column for column in df.columns if column not in excluded]
