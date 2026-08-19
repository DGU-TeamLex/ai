"""Leakage-safe shock features for monthly external-risk signals.

Each row is a forecast origin for the following month.  Features may therefore
use the signal observed in the current row and its past, but never a future
row.  Cross-sectional ranks are calculated only among positive observations
in the same month so the many unmapped zero rows do not become a false
"middle-ranked" signal.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


NEWS_RAW_COLUMNS = [
    "disease_news_risk",
    "supply_news_risk",
    "material_news_risk",
    "total_news_risk",
]
COMMODITY_RAW_COLUMNS = [
    "commodity_risk",
    "material_return_30d",
    "material_volatility_30d",
]

NEWS_SHOCK_COLUMNS = [
    "total_news_risk_lag_1",
    "total_news_risk_lag_2",
    "news_risk_jump",
    "news_risk_relative_rank",
    "news_risk_shock",
    "news_risk_ewm_3",
    "news_risk_duration_months",
]
COMMODITY_SHOCK_COLUMNS = [
    "commodity_risk_lag_1",
    "commodity_risk_lag_2",
    "material_return_30d_lag_1",
    "material_return_30d_lag_2",
    "material_price_up_shock",
    "material_price_down_shock",
    "material_volatility_30d_lag_1",
    "material_volatility_30d_lag_2",
    "material_volatility_jump",
    "material_volatility_relative_rank",
    "commodity_volatility_shock",
    "commodity_risk_ewm_3",
]
COMBINED_SHOCK_COLUMNS = ["external_risk_shock_score"]


def _positive_monthly_rank(values: pd.Series, months: pd.Series) -> pd.Series:
    """Percentile rank positive values within each month; zero remains zero."""

    numeric = pd.to_numeric(values, errors="coerce").fillna(0.0)
    positive = numeric.gt(0)
    result = pd.Series(0.0, index=values.index, dtype="float32")
    if positive.any():
        result.loc[positive] = (
            numeric.loc[positive]
            .groupby(months.loc[positive], observed=True)
            .rank(method="average", pct=True)
            .astype("float32")
        )
    return result


def _consecutive_positive_duration(
    values: pd.Series,
    groups: pd.Series,
) -> pd.Series:
    positive = pd.to_numeric(values, errors="coerce").fillna(0.0).gt(0)
    run = (~positive).groupby(groups, observed=True, sort=False).cumsum()
    duration = (
        positive.astype("int16")
        .groupby([groups, run], observed=True, sort=False)
        .cumsum()
    )
    return duration.astype("float32")


def add_external_risk_shock_features(feature_table: pd.DataFrame) -> pd.DataFrame:
    """Add news/commodity shock, lag, persistence, and Noisy-OR features.

    ``series_segment_id`` is preferred because gaps already split a stock
    series during base feature engineering.  Small fixtures may omit it, in
    which case ``stock_item_key`` is used.
    """

    result = feature_table.copy()
    for column in [*NEWS_RAW_COLUMNS, *COMMODITY_RAW_COLUMNS]:
        if column not in result:
            result[column] = 0.0
        result[column] = pd.to_numeric(result[column], errors="coerce").fillna(0.0)

    group_column = (
        "series_segment_id" if "series_segment_id" in result.columns else "stock_item_key"
    )
    if group_column not in result.columns:
        raise ValueError("External shock features require series_segment_id or stock_item_key")
    result = result.sort_values([group_column, "year_month"], kind="stable")
    grouped = result.groupby(group_column, observed=True, sort=False)

    for source in (
        "total_news_risk",
        "commodity_risk",
        "material_return_30d",
        "material_volatility_30d",
    ):
        result[f"{source}_lag_1"] = grouped[source].shift(1).fillna(0.0).astype("float32")
        result[f"{source}_lag_2"] = grouped[source].shift(2).fillna(0.0).astype("float32")

    result["news_risk_jump"] = (
        result["total_news_risk"] - result["total_news_risk_lag_1"]
    ).clip(lower=0.0)
    result["news_risk_relative_rank"] = _positive_monthly_rank(
        result["total_news_risk"], result["year_month"]
    )
    result["news_risk_shock"] = _positive_monthly_rank(
        result["news_risk_jump"], result["year_month"]
    )
    result["news_risk_ewm_3"] = grouped["total_news_risk"].transform(
        lambda values: values.ewm(span=3, adjust=False).mean()
    )
    result["news_risk_duration_months"] = _consecutive_positive_duration(
        result["total_news_risk"], result[group_column]
    )

    positive_return = result["material_return_30d"].clip(lower=0.0)
    negative_return = (-result["material_return_30d"]).clip(lower=0.0)
    result["material_price_up_shock"] = _positive_monthly_rank(
        positive_return, result["year_month"]
    )
    result["material_price_down_shock"] = _positive_monthly_rank(
        negative_return, result["year_month"]
    )
    result["material_volatility_jump"] = (
        result["material_volatility_30d"]
        - result["material_volatility_30d_lag_1"]
    ).clip(lower=0.0)
    result["material_volatility_relative_rank"] = _positive_monthly_rank(
        result["material_volatility_30d"], result["year_month"]
    )
    result["commodity_volatility_shock"] = _positive_monthly_rank(
        result["material_volatility_jump"], result["year_month"]
    )
    result["commodity_risk_ewm_3"] = grouped["commodity_risk"].transform(
        lambda values: values.ewm(span=3, adjust=False).mean()
    )

    # Probability-like union: a strong signal is not diluted by averaging,
    # while simultaneous moderate signals reinforce one another.
    components = result[
        [
            "news_risk_shock",
            "material_price_up_shock",
            "material_price_down_shock",
            "commodity_volatility_shock",
        ]
    ].clip(0.0, 1.0)
    result["external_risk_shock_score"] = (
        1.0 - np.prod(1.0 - components.to_numpy(dtype="float32"), axis=1)
    ).astype("float32")

    derived = [*NEWS_SHOCK_COLUMNS, *COMMODITY_SHOCK_COLUMNS, *COMBINED_SHOCK_COLUMNS]
    result[derived] = result[derived].fillna(0.0).astype("float32")
    return result.sort_index()
