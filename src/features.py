import logging
from pathlib import Path

import pandas as pd

try:
    from .config import EXTERNAL_FEATURE_PATH, GROUP_KEYS
    from .utils import safe_divide
except ImportError:
    from config import EXTERNAL_FEATURE_PATH, GROUP_KEYS
    from utils import safe_divide


LOGGER = logging.getLogger(__name__)

SERIES_KEYS = ["SIDO", "MED_DEVICE_5"]
LAG_COLUMNS = {
    "total_use": [1, 2, 3, 6, 12],
    "total_count": [1, 3],
    "patient_count": [1, 3],
    "total_amount": [1, 3],
    "use_per_patient": [1],
    "amount_per_use": [1],
    "count_per_patient": [1],
    "elderly_use_ratio": [1],
    "sex_1_use_ratio": [1],
    "sex_2_use_ratio": [1],
    "in_use_ratio": [1],
    "out_use_ratio": [1],
}

ROLLING_WINDOWS = [3, 6, 12]
CURRENT_MONTH_COLUMNS = {
    "total_use",
    "total_count",
    "total_amount",
    "patient_count",
    "use_per_patient",
    "amount_per_use",
    "count_per_patient",
    "elderly_use_ratio",
    "sex_1_use_ratio",
    "sex_2_use_ratio",
    "in_use_ratio",
    "out_use_ratio",
}


def add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["year"] = df["year_month"].dt.year
    df["month"] = df["year_month"].dt.month
    df["quarter"] = df["year_month"].dt.quarter
    return df


def add_external_features(df: pd.DataFrame, external_path: Path = EXTERNAL_FEATURE_PATH) -> pd.DataFrame:
    if not external_path.exists():
        LOGGER.info("External feature file not found: %s", external_path)
        return df

    external = pd.read_csv(external_path)
    if "year_month" not in external.columns or "SIDO" not in external.columns:
        raise ValueError("external_features.csv must include year_month and SIDO columns")

    external = external.copy()
    external["year_month"] = pd.to_datetime(external["year_month"]).dt.to_period("M").dt.to_timestamp()
    external["SIDO"] = external["SIDO"].astype(str)
    return df.merge(external, on=["year_month", "SIDO"], how="left")


def create_features(aggregated: pd.DataFrame) -> pd.DataFrame:
    df = aggregated.copy().sort_values(GROUP_KEYS).reset_index(drop=True)
    df["SIDO"] = df["SIDO"].astype(str)
    df["MED_DEVICE_5"] = df["MED_DEVICE_5"].astype(str)
    df = add_time_features(df)

    ratio_sources = {
        "elderly_use_ratio": "elderly_use",
        "sex_1_use_ratio": "sex_1_use",
        "sex_2_use_ratio": "sex_2_use",
        "in_use_ratio": "in_use",
        "out_use_ratio": "out_use",
    }
    for ratio_col, numerator_col in ratio_sources.items():
        if ratio_col not in df.columns and numerator_col in df.columns:
            df[ratio_col] = safe_divide(df[numerator_col], df["total_use"])
    df = df.drop(columns=[col for col in ratio_sources.values() if col in df.columns])

    df["use_per_patient"] = safe_divide(df["total_use"], df["patient_count"])
    df["amount_per_use"] = safe_divide(df["total_amount"], df["total_use"])
    df["count_per_patient"] = safe_divide(df["total_count"], df["patient_count"])

    grouped = df.groupby(SERIES_KEYS, sort=False)
    for source_col, lags in LAG_COLUMNS.items():
        for lag in lags:
            out_col = f"{source_col}_lag_{lag}"
            if source_col == "total_use":
                out_col = f"use_lag_{lag}"
            df[out_col] = grouped[source_col].shift(lag)

    shifted_use = grouped["total_use"].shift(1)
    for window in ROLLING_WINDOWS:
        rolling = shifted_use.groupby([df["SIDO"], df["MED_DEVICE_5"]])
        df[f"use_rolling_mean_{window}"] = rolling.transform(
            lambda s: s.rolling(window, min_periods=1).mean()
        )
        df[f"use_rolling_std_{window}"] = rolling.transform(
            lambda s: s.rolling(window, min_periods=2).std()
        )

    df["target_next_month"] = grouped["total_use"].shift(-1)
    df["next_year_month"] = grouped["year_month"].shift(-1)
    expected_next = df["year_month"] + pd.offsets.MonthBegin(1)
    df.loc[df["next_year_month"] != expected_next, "target_next_month"] = pd.NA
    df = df.drop(columns=["next_year_month"])

    df = add_external_features(df)
    return df


def get_model_feature_columns(df: pd.DataFrame) -> list[str]:
    excluded = CURRENT_MONTH_COLUMNS | {"target_next_month", "year_month"}
    return [col for col in df.columns if col not in excluded]
