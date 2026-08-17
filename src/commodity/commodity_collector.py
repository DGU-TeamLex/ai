from __future__ import annotations

import json
import hashlib
import logging
import os
from pathlib import Path
import time
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from ..config import (
    COMMODITY_COLLECTION_REPORT_PATH,
    COMMODITY_PRICE_CACHE_PATH,
    MARKET_SERIES_REGISTRY_PATH,
)


LOGGER = logging.getLogger(__name__)


def _write_collection_report(
    prices: pd.DataFrame,
    cache_path: Path,
    requested_provider: str,
) -> None:
    dates = pd.to_datetime(prices["date"], errors="coerce")
    series = {}
    for factor, group in prices.assign(_date=dates).groupby(
        "market_factor_id", observed=True
    ):
        ordered = group["_date"].dropna().sort_values()
        gaps = ordered.diff().dt.total_seconds().div(86400).dropna()
        series[str(factor)] = {
            "rows": int(len(group)),
            "date_min": str(ordered.min().date()) if not ordered.empty else "",
            "date_max": str(ordered.max().date()) if not ordered.empty else "",
            "median_observation_gap_days": (
                float(gaps.median()) if not gaps.empty else None
            ),
        }
    COMMODITY_COLLECTION_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    COMMODITY_COLLECTION_REPORT_PATH.write_text(
        json.dumps(
            {
                "version": "commodity-collection-provenance-v1",
                "requested_provider": requested_provider,
                "providers": {
                    str(key): int(value)
                    for key, value in prices["provider"].value_counts().items()
                },
                "rows": int(len(prices)),
                "cache_path": str(cache_path),
                "cache_sha256": (
                    hashlib.sha256(cache_path.read_bytes()).hexdigest()
                    if cache_path.exists()
                    else ""
                ),
                "series": series,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
PRICE_COLUMNS = [
    "date",
    "market_factor_id",
    "material",
    "price",
    "volume",
    "inventory",
    "open_interest",
    "provider",
    "series_id",
    "price_type",
    "currency",
    "unit",
    "is_proxy",
]
REGISTRY_COLUMNS = [
    "market_factor_id",
    "provider",
    "series_id",
    "interval",
    "price_type",
    "currency",
    "unit",
    "is_direct_factor",
    "source_url",
    "review_status",
]


SAMPLE_MARKETS = {
    "PETROCHEMICAL_NAPHTHA": (600.0, "USD_PER_TONNE"),
    "BRENT_CRUDE": (80.0, "USD_PER_BARREL"),
    "ALUMINUM": (2200.0, "USD_PER_TONNE"),
    "COPPER": (8500.0, "USD_PER_TONNE"),
    "COTTON": (100.0, "INDEX_OR_PROVIDER_UNIT"),
    "CORN": (200.0, "INDEX_OR_PROVIDER_UNIT"),
    "SUGAR": (120.0, "INDEX_OR_PROVIDER_UNIT"),
}


JsonRequester = Callable[[str, dict[str, str], int], dict]


def _request_json(url: str, params: dict[str, str], timeout: int = 60) -> dict:
    request = Request(
        f"{url}?{urlencode(params)}",
        headers={"User-Agent": "WeP-Stock-AI/1.0"},
    )
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _as_bool(series: pd.Series, default: bool = False) -> pd.Series:
    normalized = series.astype("string").fillna("").str.strip().str.lower()
    result = normalized.isin({"true", "t", "1", "yes", "y"})
    if default:
        result |= normalized.eq("")
    return result


def normalize_commodity_prices(prices: pd.DataFrame) -> pd.DataFrame:
    if prices.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    result = prices.copy()
    if "market_factor_id" not in result.columns:
        if "material" not in result.columns:
            raise ValueError("Commodity prices require market_factor_id or material")
        result["market_factor_id"] = result["material"]
    if "material" not in result.columns:
        result["material"] = result["market_factor_id"]
    if "date" not in result.columns or "price" not in result.columns:
        raise ValueError("Commodity prices require date and price columns")

    defaults = {
        "volume": pd.NA,
        "inventory": pd.NA,
        "open_interest": pd.NA,
        "provider": "csv",
        "series_id": "",
        "price_type": "unknown",
        "currency": "",
        "unit": "",
        "is_proxy": False,
    }
    for column, default in defaults.items():
        if column not in result.columns:
            result[column] = default

    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    result["price"] = pd.to_numeric(result["price"], errors="coerce")
    for column in ["volume", "inventory", "open_interest"]:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result["is_proxy"] = _as_bool(result["is_proxy"])
    result["market_factor_id"] = (
        result["market_factor_id"].astype("string").fillna("").str.strip()
    )
    result = result[
        result["date"].notna()
        & result["price"].notna()
        & result["price"].gt(0)
        & result["market_factor_id"].ne("")
    ].copy()
    result["material"] = result["market_factor_id"]
    result["date"] = result["date"].dt.strftime("%Y-%m-%d")
    return (
        result[PRICE_COLUMNS]
        .drop_duplicates(["date", "market_factor_id"], keep="last")
        .sort_values(["market_factor_id", "date"])
        .reset_index(drop=True)
    )


def load_market_series_registry(
    path: Path = MARKET_SERIES_REGISTRY_PATH,
) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=REGISTRY_COLUMNS)
    registry = pd.read_csv(path, keep_default_na=False)
    missing = [column for column in REGISTRY_COLUMNS if column not in registry.columns]
    if missing:
        raise ValueError(f"Market series registry is missing columns: {missing}")
    registry = registry[
        registry["review_status"].astype(str).str.strip().str.lower().eq("approved")
    ].copy()
    registry["is_direct_factor"] = _as_bool(registry["is_direct_factor"])
    return registry.reset_index(drop=True)


def collect_sample_prices(
    start_date: str = "2024-01-01",
    end_date: str = "2025-12-31",
) -> pd.DataFrame:
    months = pd.date_range(start_date, end_date, freq="MS")
    rows = []
    for factor, (base_price, unit) in SAMPLE_MARKETS.items():
        for index, month in enumerate(months):
            trend = 1 + 0.004 * index
            shock = 1.0
            if factor in {"PETROCHEMICAL_NAPHTHA", "BRENT_CRUDE"} and month >= pd.Timestamp("2025-03-01"):
                shock = 1.18
            if factor == "ALUMINUM" and month >= pd.Timestamp("2025-05-01"):
                shock = 1.08
            rows.append(
                {
                    "date": month,
                    "market_factor_id": factor,
                    "price": base_price * trend * shock,
                    "provider": "sample",
                    "series_id": factor,
                    "price_type": "synthetic_test_fixture",
                    "currency": "USD",
                    "unit": unit,
                    "is_proxy": False,
                }
            )
    return normalize_commodity_prices(pd.DataFrame(rows))


def collect_csv_prices(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Commodity CSV not found: {path}")
    return normalize_commodity_prices(pd.read_csv(path))


def _filter_price_window(
    prices: pd.DataFrame,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """가격 시계열을 요청 기간으로 자른다.

    기간 밖 가격은 위험 점수에 쓰이지 않는데도 그대로 두면 월별 집계가 그만큼
    늘어나 산출물이 조용히 커진다. 캐시는 계속 쌓이므로 시간이 갈수록 심해진다.
    """
    if prices.empty or "date" not in prices.columns:
        return prices
    dates = pd.to_datetime(prices["date"], errors="coerce")
    window = dates.between(pd.Timestamp(start_date), pd.Timestamp(end_date))
    filtered = prices.loc[window]
    if len(filtered) != len(prices):
        LOGGER.info(
            "Filtered commodity prices to %s~%s: %s → %s rows",
            start_date, end_date, f"{len(prices):,}", f"{len(filtered):,}",
        )
    return filtered.reset_index(drop=True)


def _registry_rows(registry: pd.DataFrame, provider: str) -> pd.DataFrame:
    return registry[
        registry["provider"].astype(str).str.strip().str.lower().eq(provider)
    ].copy()


def collect_alpha_vantage_prices(
    registry: pd.DataFrame,
    api_key: str,
    start_date: str | None = None,
    end_date: str | None = None,
    request_json: JsonRequester = _request_json,
    request_delay_seconds: float = 0.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    if not api_key:
        raise ValueError("ALPHA_VANTAGE_API_KEY is required")
    if request_delay_seconds < 0:
        raise ValueError("Alpha Vantage request delay must be non-negative")
    rows = []
    provider_rows = _registry_rows(registry, "alpha_vantage")
    for request_index, (_, series) in enumerate(provider_rows.iterrows()):
        if request_index and request_delay_seconds:
            sleeper(request_delay_seconds)
        payload = request_json(
            "https://www.alphavantage.co/query",
            {
                "function": str(series["series_id"]),
                "interval": str(series["interval"]),
                "apikey": api_key,
            },
            60,
        )
        if "data" not in payload:
            message = payload.get("Error Message") or payload.get("Information") or payload.get("Note")
            raise ValueError(f"Alpha Vantage response has no data: {message or 'unknown response'}")
        for observation in payload["data"]:
            rows.append(
                {
                    "date": observation.get("date"),
                    "market_factor_id": series["market_factor_id"],
                    "price": observation.get("value"),
                    "provider": "alpha_vantage",
                    "series_id": series["series_id"],
                    "price_type": series["price_type"],
                    "currency": series["currency"],
                    "unit": series["unit"],
                    "is_proxy": not bool(series["is_direct_factor"]),
                }
            )
    return _filter_dates(normalize_commodity_prices(pd.DataFrame(rows)), start_date, end_date)


def collect_fred_prices(
    registry: pd.DataFrame,
    api_key: str,
    start_date: str | None = None,
    end_date: str | None = None,
    request_json: JsonRequester = _request_json,
) -> pd.DataFrame:
    if not api_key:
        raise ValueError("FRED_API_KEY is required")
    rows = []
    for _, series in _registry_rows(registry, "fred").iterrows():
        params = {
            "series_id": str(series["series_id"]),
            "api_key": api_key,
            "file_type": "json",
        }
        if start_date:
            params["observation_start"] = start_date
        if end_date:
            params["observation_end"] = end_date
        payload = request_json(
            "https://api.stlouisfed.org/fred/series/observations",
            params,
            60,
        )
        for observation in payload.get("observations", []):
            rows.append(
                {
                    "date": observation.get("date"),
                    "market_factor_id": series["market_factor_id"],
                    "price": observation.get("value"),
                    "provider": "fred",
                    "series_id": series["series_id"],
                    "price_type": series["price_type"],
                    "currency": series["currency"],
                    "unit": series["unit"],
                    "is_proxy": not bool(series["is_direct_factor"]),
                }
            )
    return normalize_commodity_prices(pd.DataFrame(rows))


def collect_nasdaq_data_link_prices(
    registry: pd.DataFrame,
    api_key: str,
    start_date: str | None = None,
    end_date: str | None = None,
    request_json: JsonRequester = _request_json,
) -> pd.DataFrame:
    if not api_key:
        raise ValueError("NASDAQ_DATA_LINK_API_KEY is required")
    rows = []
    for _, series in _registry_rows(registry, "nasdaq_data_link").iterrows():
        params = {"api_key": api_key, "order": "asc"}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        payload = request_json(
            f"https://data.nasdaq.com/api/v3/datasets/{series['series_id']}/data.json",
            params,
            60,
        )
        dataset = payload.get("dataset_data", {})
        names = [str(value).strip().lower() for value in dataset.get("column_names", [])]
        if "date" not in names:
            raise ValueError(f"Nasdaq series has no Date column: {series['series_id']}")
        price_name = next(
            (name for name in ["settle", "value", "close", "last"] if name in names),
            None,
        )
        if not price_name:
            raise ValueError(f"Nasdaq series has no supported price column: {series['series_id']}")
        date_index = names.index("date")
        price_index = names.index(price_name)
        volume_index = names.index("volume") if "volume" in names else None
        oi_name = next((name for name in ["open interest", "prev. day open interest"] if name in names), None)
        oi_index = names.index(oi_name) if oi_name else None
        for values in dataset.get("data", []):
            rows.append(
                {
                    "date": values[date_index],
                    "market_factor_id": series["market_factor_id"],
                    "price": values[price_index],
                    "volume": values[volume_index] if volume_index is not None else None,
                    "open_interest": values[oi_index] if oi_index is not None else None,
                    "provider": "nasdaq_data_link",
                    "series_id": series["series_id"],
                    "price_type": series["price_type"],
                    "currency": series["currency"],
                    "unit": series["unit"],
                    "is_proxy": not bool(series["is_direct_factor"]),
                }
            )
    return normalize_commodity_prices(pd.DataFrame(rows))


def _filter_dates(
    prices: pd.DataFrame,
    start_date: str | None,
    end_date: str | None,
) -> pd.DataFrame:
    if prices.empty:
        return prices
    dates = pd.to_datetime(prices["date"])
    mask = pd.Series(True, index=prices.index)
    if start_date:
        mask &= dates.ge(pd.Timestamp(start_date))
    if end_date:
        mask &= dates.le(pd.Timestamp(end_date))
    return prices[mask].reset_index(drop=True)


def collect_commodity_prices(
    provider: str | None = None,
    data_path: str | Path | None = None,
    cache_path: Path = COMMODITY_PRICE_CACHE_PATH,
    refresh: bool | None = None,
    registry: pd.DataFrame | None = None,
    request_json: JsonRequester = _request_json,
) -> pd.DataFrame:
    selected = (provider or os.getenv("COMMODITY_PROVIDER", "disabled")).strip().lower()
    start_date = os.getenv("COMMODITY_START_DATE", "2024-01-01")
    end_date = os.getenv("COMMODITY_END_DATE", "2025-12-31")
    refresh = refresh if refresh is not None else os.getenv("COMMODITY_REFRESH", "false").lower() == "true"
    allow_fallback = os.getenv("COMMODITY_ALLOW_SAMPLE_FALLBACK", "false").lower() == "true"
    registry = load_market_series_registry() if registry is None else registry

    if selected in {"disabled", "none"}:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    if selected == "sample":
        return collect_sample_prices(start_date, end_date)
    if selected != "csv" and cache_path.exists() and not refresh:
        LOGGER.info("Loading cached commodity prices: %s", cache_path)
        # 캐시에도 요청 기간을 적용한다. 종전에는 캐시를 통째로 돌려줘서
        # COMMODITY_START_DATE/END_DATE 가 원격 수집에만 걸리고 캐시 경로에서는
        # 무시됐다. 캐시가 쌓일수록 산출물이 조용히 커진다.
        #
        # 실측(2026-08-13): 2023-10~2026-01 을 요청했는데 캐시 전체(2015-01~)가
        # 쓰여 점수가 139개월 19,631,970행(3.4GB), 감사가 26,557,686행(12.9GB)이
        # 됐다. 의도한 범위는 25개월 344만행이다.
        cached = _filter_price_window(
            collect_csv_prices(cache_path), start_date, end_date
        )
        _write_collection_report(cached, cache_path, selected)
        return cached

    try:
        if selected == "csv":
            selected_path = data_path or os.getenv("COMMODITY_DATA_PATH")
            if not selected_path:
                raise ValueError("COMMODITY_DATA_PATH is required when COMMODITY_PROVIDER=csv")
            result = collect_csv_prices(Path(selected_path))
        elif selected == "alpha_vantage":
            request_delay_seconds = float(
                os.getenv("ALPHA_VANTAGE_REQUEST_DELAY_SECONDS", "1.2")
            )
            result = collect_alpha_vantage_prices(
                registry=registry,
                api_key=os.getenv("ALPHA_VANTAGE_API_KEY", ""),
                start_date=start_date,
                end_date=end_date,
                request_json=request_json,
                request_delay_seconds=request_delay_seconds,
            )
        elif selected == "fred":
            result = collect_fred_prices(
                registry,
                os.getenv("FRED_API_KEY", ""),
                start_date,
                end_date,
                request_json,
            )
        elif selected == "nasdaq_data_link":
            result = collect_nasdaq_data_link_prices(
                registry,
                os.getenv("NASDAQ_DATA_LINK_API_KEY", ""),
                start_date,
                end_date,
                request_json,
            )
        else:
            raise ValueError(f"Unsupported COMMODITY_PROVIDER: {selected}")
        if result.empty:
            raise ValueError(f"Commodity provider returned no valid rows: {selected}")
    except Exception:
        if not allow_fallback:
            raise
        LOGGER.exception("Commodity collection failed; using sample fallback")
        return collect_sample_prices(start_date, end_date)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(cache_path, index=False)
    _write_collection_report(result, cache_path, selected)
    LOGGER.info("Saved commodity price cache: %s (%s rows)", cache_path, len(result))
    return result


if __name__ == "__main__":
    print(collect_commodity_prices().head().to_string(index=False))
