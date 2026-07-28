import pandas as pd


def add_commodity_features(prices: pd.DataFrame) -> pd.DataFrame:
    df = prices.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["material", "date"])
    grouped = df.groupby("material", sort=False)
    df["return_7d"] = grouped["price"].pct_change(1).fillna(0.0)
    df["return_30d"] = grouped["price"].pct_change(1).fillna(0.0)
    df["volatility_30d"] = grouped["return_30d"].transform(lambda s: s.rolling(3, min_periods=2).std()).fillna(0.0)
    df["price_vs_90d_mean"] = (
        df["price"] / grouped["price"].transform(lambda s: s.rolling(3, min_periods=1).mean())
    ).fillna(1.0) - 1.0
    return df

