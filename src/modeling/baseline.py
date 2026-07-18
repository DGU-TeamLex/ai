import pandas as pd


BASELINE_PREDICTION_COLUMNS = [
    "baseline_last_month_pred",
    "baseline_rolling_mean_3_pred",
    "baseline_rolling_median_3_pred",
    "baseline_rolling_mean_6_pred",
    "baseline_same_month_last_year_pred",
    "baseline_expanding_mean_pred",
]


def add_baseline_predictions(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    fallback = result["lag_1"].fillna(0.0).clip(lower=0)
    result["baseline_last_month_pred"] = result["lag_1"]
    result["baseline_rolling_mean_3_pred"] = result["rolling_mean_3"]
    result["baseline_rolling_median_3_pred"] = result["rolling_median_3"]
    result["baseline_rolling_mean_6_pred"] = result["rolling_mean_6"]
    result["baseline_same_month_last_year_pred"] = result["same_month_last_year"]
    result["baseline_expanding_mean_pred"] = result["expanding_mean"]

    for col in BASELINE_PREDICTION_COLUMNS:
        result[col] = pd.to_numeric(result[col], errors="coerce").fillna(fallback).clip(lower=0)
    return result
