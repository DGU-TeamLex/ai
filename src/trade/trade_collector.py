from __future__ import annotations

import argparse
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import re
import time
from typing import Callable
from urllib.parse import unquote, urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

import pandas as pd

from ..config import (
    TRADE_COUNTRY_CACHE_PATH,
    TRADE_COUNTRY_SCOPE_PATH,
    TRADE_TOTAL_CACHE_PATH,
)


LOGGER = logging.getLogger(__name__)
KCS_TOTAL_ENDPOINT = (
    "https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList"
)
KCS_COUNTRY_ENDPOINT = (
    "https://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList"
)
TRADE_COLUMNS = [
    "STD_YYYYMM",
    "hs_code",
    "country_code",
    "country_name",
    "export_weight_kg",
    "export_value_usd",
    "import_weight_kg",
    "import_value_usd",
    "trade_balance_usd",
    "provider",
    "source_endpoint",
    "retrieved_at",
]
COUNTRY_SCOPE_COLUMNS = [
    "country_code",
    "country_name",
    "scope_role",
    "review_status",
    "evidence_reference",
    "scope_version",
]
XmlRequester = Callable[[str, dict[str, str], int], bytes]


def _request_xml(url: str, params: dict[str, str], timeout: int = 60) -> bytes:
    request = Request(
        f"{url}?{urlencode(params)}",
        headers={"User-Agent": "WeP-Stock-AI/1.0"},
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def _empty_trade() -> pd.DataFrame:
    return pd.DataFrame(columns=TRADE_COLUMNS)


def load_trade_country_scope(
    path: Path = TRADE_COUNTRY_SCOPE_PATH,
) -> list[str]:
    if not path.exists():
        return []
    scope = pd.read_csv(path, keep_default_na=False)
    missing = [column for column in COUNTRY_SCOPE_COLUMNS if column not in scope.columns]
    if missing:
        raise ValueError(f"Trade country scope is missing columns: {missing}")
    scope = scope[
        scope["review_status"].astype(str).str.strip().str.lower().eq("approved")
    ].copy()
    scope["country_code"] = (
        scope["country_code"].astype("string").fillna("").str.strip().str.upper()
    )
    invalid = ~scope["country_code"].str.fullmatch(r"[A-Z]{2}")
    if invalid.any():
        raise ValueError(
            "Trade country scope contains invalid codes: "
            f"{scope.loc[invalid, 'country_code'].tolist()}"
        )
    if scope["country_code"].duplicated().any():
        raise ValueError("Trade country scope contains duplicate approved codes")
    return scope["country_code"].tolist()


def parse_kcs_trade_xml(
    payload: bytes | str,
    *,
    country_code: str = "ALL",
    endpoint: str = KCS_TOTAL_ENDPOINT,
    retrieved_at: str | None = None,
) -> pd.DataFrame:
    if not payload:
        return _empty_trade()
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as error:
        preview = (
            payload.decode("utf-8", errors="replace")
            if isinstance(payload, bytes)
            else payload
        )
        raise ValueError(f"KCS API returned non-XML content: {preview[:120]}") from error

    result_code = root.findtext(".//resultCode", default="00").strip()
    if result_code not in {"00", "0", "NORMAL_CODE"}:
        message = root.findtext(".//resultMsg", default="").strip()
        raise ValueError(f"KCS API returned resultCode={result_code}: {message}")

    retrieved_at = retrieved_at or datetime.now(timezone.utc).isoformat()
    rows = []
    for item in root.findall(".//item"):
        values = {child.tag: (child.text or "").strip() for child in item}
        rows.append(
            {
                "STD_YYYYMM": values.get("year", ""),
                "hs_code": values.get("hsCd") or values.get("hsCode", ""),
                "country_code": values.get("statCd", country_code) or country_code,
                "country_name": values.get("statCdCntnKor1", ""),
                "export_weight_kg": values.get("expWgt", "0"),
                "export_value_usd": values.get("expDlr", "0"),
                "import_weight_kg": values.get("impWgt", "0"),
                "import_value_usd": values.get("impDlr", "0"),
                "trade_balance_usd": values.get("balPayments", "0"),
                "provider": "korea_customs_service",
                "source_endpoint": endpoint,
                "retrieved_at": retrieved_at,
            }
        )
    return normalize_trade_flows(pd.DataFrame(rows))


def normalize_trade_flows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return _empty_trade()
    result = frame.copy()
    defaults = {
        "country_code": "ALL",
        "country_name": "",
        "provider": "csv",
        "source_endpoint": "",
        "retrieved_at": "",
        "trade_balance_usd": 0,
    }
    for column, default in defaults.items():
        if column not in result.columns:
            result[column] = default
    required = {
        "STD_YYYYMM",
        "hs_code",
        "export_weight_kg",
        "export_value_usd",
        "import_weight_kg",
        "import_value_usd",
    }
    missing = sorted(required - set(result.columns))
    if missing:
        raise ValueError(f"Trade data is missing columns: {missing}")

    month_text = (
        result["STD_YYYYMM"]
        .astype("string")
        .fillna("")
        .str.strip()
        .str.replace(".", "-", regex=False)
    )
    valid_month = month_text.where(month_text.str.fullmatch(r"\d{4}-\d{2}"), "")
    parsed_month = pd.to_datetime(
        valid_month + "-01",
        format="%Y-%m-%d",
        errors="coerce",
    )
    result["STD_YYYYMM"] = parsed_month.dt.strftime("%Y-%m")
    result["hs_code"] = (
        result["hs_code"]
        .astype("string")
        .fillna("")
        .str.replace(r"\.0$", "", regex=True)
        .str.strip()
    )
    result["country_code"] = (
        result["country_code"].astype("string").fillna("ALL").str.strip().str.upper()
    )
    for column in [
        "export_weight_kg",
        "export_value_usd",
        "import_weight_kg",
        "import_value_usd",
        "trade_balance_usd",
    ]:
        result[column] = pd.to_numeric(result[column], errors="coerce").fillna(0.0)
    invalid_numeric = result[
        [
            "export_weight_kg",
            "export_value_usd",
            "import_weight_kg",
            "import_value_usd",
        ]
    ].lt(0)
    if invalid_numeric.any(axis=None):
        raise ValueError("Trade weights and values must be non-negative")
    result = result[
        result["STD_YYYYMM"].notna()
        & result["hs_code"].str.fullmatch(r"\d{2,10}")
    ].copy()
    return (
        result[TRADE_COLUMNS]
        .drop_duplicates(["STD_YYYYMM", "hs_code", "country_code"], keep="last")
        .sort_values(["hs_code", "country_code", "STD_YYYYMM"])
        .reset_index(drop=True)
    )


def _month_chunks(start_month: str, end_month: str) -> list[tuple[str, str]]:
    start = pd.Period(start_month, freq="M")
    end = pd.Period(end_month, freq="M")
    if start > end:
        raise ValueError("TRADE_START_MONTH must not be after TRADE_END_MONTH")
    chunks = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + 11, end)
        chunks.append((cursor.strftime("%Y%m"), chunk_end.strftime("%Y%m")))
        cursor = chunk_end + 1
    return chunks


def collect_kcs_trade_totals(
    hs_codes: list[str],
    start_month: str,
    end_month: str,
    service_key: str,
    *,
    request_xml: XmlRequester = _request_xml,
    request_delay_seconds: float = 0.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    key = unquote(service_key).strip()
    if not key:
        raise ValueError("DATA_GO_KR_SERVICE_KEY is required for KCS collection")
    if request_delay_seconds < 0:
        raise ValueError("Trade request delay must be non-negative")

    frames = []
    request_index = 0
    for hs_code in sorted(set(hs_codes)):
        if not re.fullmatch(r"\d{10}", str(hs_code)):
            raise ValueError(f"KCS collection requires a 10-digit HSK code: {hs_code}")
        for start, end in _month_chunks(start_month, end_month):
            if request_index and request_delay_seconds:
                sleeper(request_delay_seconds)
            payload = request_xml(
                KCS_TOTAL_ENDPOINT,
                {
                    "serviceKey": key,
                    "strtYymm": start,
                    "endYymm": end,
                    "hsSgn": hs_code,
                },
                60,
            )
            frames.append(
                parse_kcs_trade_xml(
                    payload,
                    country_code="ALL",
                    endpoint=KCS_TOTAL_ENDPOINT,
                )
            )
            request_index += 1
    frames = [frame for frame in frames if not frame.empty]
    return pd.concat(frames, ignore_index=True) if frames else _empty_trade()


def collect_kcs_country_trade(
    hs_codes: list[str],
    country_codes: list[str],
    start_month: str,
    end_month: str,
    service_key: str,
    *,
    request_xml: XmlRequester = _request_xml,
    request_delay_seconds: float = 0.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    key = unquote(service_key).strip()
    if not key:
        raise ValueError("DATA_GO_KR_SERVICE_KEY is required for KCS collection")
    frames = []
    request_index = 0
    for hs_code in sorted(set(hs_codes)):
        if not re.fullmatch(r"\d{10}", str(hs_code)):
            raise ValueError(f"KCS collection requires a 10-digit HSK code: {hs_code}")
        for country_code in sorted({code.strip().upper() for code in country_codes if code.strip()}):
            if not re.fullmatch(r"[A-Z]{2}", country_code):
                raise ValueError(f"Invalid KCS country code: {country_code}")
            for start, end in _month_chunks(start_month, end_month):
                if request_index and request_delay_seconds:
                    sleeper(request_delay_seconds)
                payload = request_xml(
                    KCS_COUNTRY_ENDPOINT,
                    {
                        "serviceKey": key,
                        "strtYymm": start,
                        "endYymm": end,
                        "hsSgn": hs_code,
                        "cntyCd": country_code,
                    },
                    60,
                )
                frames.append(
                    parse_kcs_trade_xml(
                        payload,
                        country_code=country_code,
                        endpoint=KCS_COUNTRY_ENDPOINT,
                    )
                )
                request_index += 1
    frames = [frame for frame in frames if not frame.empty]
    return pd.concat(frames, ignore_index=True) if frames else _empty_trade()


def collect_trade_flows(
    hs_codes: list[str],
    *,
    provider: str | None = None,
    total_cache_path: Path = TRADE_TOTAL_CACHE_PATH,
    country_cache_path: Path = TRADE_COUNTRY_CACHE_PATH,
    refresh: bool | None = None,
    request_xml: XmlRequester = _request_xml,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    selected = (provider or os.getenv("TRADE_PROVIDER", "disabled")).strip().lower()
    refresh = (
        refresh
        if refresh is not None
        else os.getenv("TRADE_REFRESH", "false").strip().lower() == "true"
    )
    if selected in {"disabled", "none"}:
        return _empty_trade(), _empty_trade()
    if selected == "csv":
        totals = (
            normalize_trade_flows(pd.read_csv(total_cache_path, dtype={"hs_code": "string"}))
            if total_cache_path.exists()
            else _empty_trade()
        )
        countries = (
            normalize_trade_flows(
                pd.read_csv(country_cache_path, dtype={"hs_code": "string"})
            )
            if country_cache_path.exists()
            else _empty_trade()
        )
        return totals, countries
    if selected != "kcs":
        raise ValueError(f"Unsupported TRADE_PROVIDER: {selected}")
    if total_cache_path.exists() and not refresh:
        return collect_trade_flows(
            hs_codes,
            provider="csv",
            total_cache_path=total_cache_path,
            country_cache_path=country_cache_path,
        )

    start_month = os.getenv("TRADE_START_MONTH", "2023-01")
    end_month = os.getenv("TRADE_END_MONTH", "2026-06")
    delay = float(os.getenv("TRADE_REQUEST_DELAY_SECONDS", "0.2"))
    service_key = os.getenv("DATA_GO_KR_SERVICE_KEY", "")
    configured_country_codes = os.getenv("TRADE_COUNTRY_CODES", "").strip()
    country_codes = (
        [
            value.strip()
            for value in configured_country_codes.split(",")
            if value.strip()
        ]
        if configured_country_codes
        else load_trade_country_scope()
    )
    chunk_count = len(_month_chunks(start_month, end_month))
    estimated_requests = len(set(hs_codes)) * chunk_count * (
        1 + len(set(country_codes))
    )
    max_requests = int(os.getenv("TRADE_MAX_REQUESTS", "1000"))
    if max_requests <= 0:
        raise ValueError("TRADE_MAX_REQUESTS must be greater than zero")
    if estimated_requests > max_requests:
        raise RuntimeError(
            "KCS request budget exceeded: "
            f"estimated={estimated_requests}, allowed={max_requests}. "
            "Reduce HS codes, countries, or the date range."
        )
    totals = collect_kcs_trade_totals(
        hs_codes,
        start_month,
        end_month,
        service_key,
        request_xml=request_xml,
        request_delay_seconds=delay,
    )
    countries = (
        collect_kcs_country_trade(
            hs_codes,
            country_codes,
            start_month,
            end_month,
            service_key,
            request_xml=request_xml,
            request_delay_seconds=delay,
        )
        if country_codes
        else _empty_trade()
    )
    total_cache_path.parent.mkdir(parents=True, exist_ok=True)
    totals.to_csv(total_cache_path, index=False)
    countries.to_csv(country_cache_path, index=False)
    return totals, countries


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect KCS monthly trade data")
    parser.add_argument("--hs-code", action="append", required=True)
    parser.add_argument("--provider", choices=["disabled", "csv", "kcs"], default=None)
    parser.add_argument("--refresh", action="store_true")
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    total, country = collect_trade_flows(
        args.hs_code,
        provider=args.provider,
        refresh=args.refresh,
    )
    print({"total_rows": len(total), "country_rows": len(country)})
