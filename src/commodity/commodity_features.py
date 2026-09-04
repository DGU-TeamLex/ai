import numpy as np
import pandas as pd

from .commodity_collector import normalize_commodity_prices


def _asof_return(group: pd.DataFrame, days: int) -> pd.Series:
    current = group[["date", "price"]].copy()
    current["cutoff"] = current["date"] - pd.Timedelta(days=days)
    history = group[["date", "price"]].rename(
        columns={"date": "lag_date", "price": "lag_price"}
    )
    matched = pd.merge_asof(
        current.sort_values("cutoff"),
        history.sort_values("lag_date"),
        left_on="cutoff",
        right_on="lag_date",
        direction="backward",
    ).sort_index()
    age_days = (matched["date"] - matched["lag_date"]).dt.days
    valid = age_days.between(days, days * 2, inclusive="both")
    values = matched["price"].div(matched["lag_price"]).sub(1.0)
    return values.where(valid).fillna(0.0)


def _add_group_features(group: pd.DataFrame) -> pd.DataFrame:
    result = group.sort_values("date").copy().reset_index(drop=True)
    gaps = result["date"].diff().dt.total_seconds().div(86400).dropna()
    median_gap_days = float(gaps.median()) if not gaps.empty else 30.0
    if median_gap_days <= 7.0:
        frequency = "daily"
        volatility_scale = np.sqrt(30.0)
        minimum_periods = 2
    elif median_gap_days <= 15.0:
        frequency = "weekly"
        volatility_scale = np.sqrt(30.0 / 7.0)
        minimum_periods = 2
    else:
        frequency = "monthly"
        volatility_scale = 1.0
        minimum_periods = 2
    result["observation_frequency"] = frequency
    result["median_observation_gap_days"] = median_gap_days
    result["return_7d"] = _asof_return(result, 7)
    result["return_30d"] = _asof_return(result, 30)

    observation_return = result["price"].pct_change()
    indexed_return = pd.Series(observation_return.to_numpy(), index=result["date"])
    time_volatility = indexed_return.rolling("30D", min_periods=minimum_periods).std().to_numpy()
    observation_volatility = observation_return.rolling(3, min_periods=2).std()
    result["volatility_30d"] = (
        pd.Series(time_volatility)
        .fillna(observation_volatility)
        .fillna(0.0)
        .mul(volatility_scale)
    )

    indexed_price = pd.Series(result["price"].to_numpy(), index=result["date"])
    mean_90d = indexed_price.rolling("90D", min_periods=1).mean().to_numpy()
    result["price_vs_90d_mean"] = result["price"].div(mean_90d).sub(1.0).fillna(0.0)
    return result


def add_commodity_features(prices: pd.DataFrame) -> pd.DataFrame:
    normalized = normalize_commodity_prices(prices)
    if normalized.empty:
        return normalized.assign(
            return_7d=pd.Series(dtype="float64"),
            return_30d=pd.Series(dtype="float64"),
            volatility_30d=pd.Series(dtype="float64"),
            price_vs_90d_mean=pd.Series(dtype="float64"),
        )
    normalized["date"] = pd.to_datetime(normalized["date"])
    groups = [
        _add_group_features(group)
        for _, group in normalized.groupby("market_factor_id", sort=False)
    ]
    return (
        pd.concat(groups, ignore_index=True)
        .sort_values(["market_factor_id", "date"])
        .reset_index(drop=True)
    )
