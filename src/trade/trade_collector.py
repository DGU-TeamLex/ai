from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Callable
from urllib.parse import unquote, urlencode
from urllib.error import HTTPError, URLError
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
COLLECTION_STATE_VERSION = "kcs-trade-collection-state-v1"


def _request_xml(url: str, params: dict[str, str], timeout: int = 60) -> bytes:
    request = Request(
        f"{url}?{urlencode(params)}",
        headers={"User-Agent": "WeP-Stock-AI/1.0"},
    )
    max_retries = max(0, int(os.getenv("TRADE_MAX_RETRIES", "5")))
    backoff = max(
        0.1,
        float(os.getenv("TRADE_RETRY_BACKOFF_SECONDS", "2.0")),
    )
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except HTTPError as error:
            last_error = error
            if error.code != 429 and error.code < 500:
                raise
        except (URLError, TimeoutError) as error:
            last_error = error
        if attempt == max_retries:
            break
        wait_seconds = min(backoff * (2 ** attempt), 60.0)
        LOGGER.warning(
            "KCS request failed (%s); retrying in %.1f seconds (%s/%s)",
            last_error,
            wait_seconds,
            attempt + 1,
            max_retries,
        )
        time.sleep(wait_seconds)
    if last_error is not None:
        raise last_error
    raise RuntimeError("KCS request failed without an exception")


def _empty_trade() -> pd.DataFrame:
    return pd.DataFrame(columns=TRADE_COLUMNS)


def _collection_state_path(country_cache_path: Path) -> Path:
    return country_cache_path.with_name("kcs_trade_collection_state.json")


def _load_collection_state(
    path: Path,
    *,
    start_month: str,
    end_month: str,
) -> dict[str, object]:
    if not path.exists():
        return {
            "version": COLLECTION_STATE_VERSION,
            "start_month": start_month,
            "end_month": end_month,
            "completed_total_hs_codes": [],
            "completed_country_hs_pairs": [],
        }
    with path.open("r", encoding="utf-8") as file:
        state = json.load(file)
    if (
        state.get("version") != COLLECTION_STATE_VERSION
        or state.get("start_month") != start_month
        or state.get("end_month") != end_month
    ):
        return {
            "version": COLLECTION_STATE_VERSION,
            "start_month": start_month,
            "end_month": end_month,
            "completed_total_hs_codes": [],
            "completed_country_hs_pairs": [],
        }
    return state


def _write_collection_state(
    state: dict[str, object],
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)
        file.write("\n")
    temporary.replace(path)


def _merge_trade_cache(
    existing: pd.DataFrame,
    collected: pd.DataFrame,
) -> pd.DataFrame:
    if existing.empty:
        return normalize_trade_flows(collected)
    if collected.empty:
        return normalize_trade_flows(existing)
    return normalize_trade_flows(
        pd.concat([existing, collected], ignore_index=True)
    )


def _write_trade_cache(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(path)


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
    state_path: Path | None = None,
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
    # 캐시 조기 반환을 두지 않는다.
    #
    # 종전에는 `total_cache_path.exists()` 만 보고 돌려줬고, 그 뒤 요청 HS 코드
    # 포함 여부를 덧대 봤지만 그것도 불완전했다. 리뷰가 든 반례가 그대로 통과한다.
    #
    #   요청 기간   2024-01~2025-12
    #   total cache 3902100000 의 2025-01 한 행만 존재
    #   country     없음
    #   → totals=1행, countries=0행, 네트워크 수집 없이 "성공"
    #
    # HS 코드 집합이 맞아도 **기간과 국가 pair 가 비어 있을 수 있다.** 조기
    # 반환에 검사를 계속 덧대는 대신 아래 증분 completeness 경로로 항상 내려간다.
    # 그 경로는 이미 기간·state·country-HS pair 를 보고 빠진 것만 수집하며,
    # 마지막에 요청 HS 로 필터링한다. 캐시가 완전하면 요청이 0건이므로
    # 네트워크 비용도 종전과 같다.
    #
    # ai#71 Blocking 1.

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
    requested_hs_codes = sorted({str(code).strip() for code in hs_codes})
    requested_country_codes = sorted(
        {str(code).strip().upper() for code in country_codes if str(code).strip()}
    )
    existing_totals = (
        normalize_trade_flows(
            pd.read_csv(total_cache_path, dtype={"hs_code": "string"})
        )
        if total_cache_path.exists()
        else _empty_trade()
    )
    existing_countries = (
        normalize_trade_flows(
            pd.read_csv(country_cache_path, dtype={"hs_code": "string"})
        )
        if country_cache_path.exists()
        else _empty_trade()
    )
    state_path = state_path or _collection_state_path(country_cache_path)
    state = _load_collection_state(
        state_path,
        start_month=start_month,
        end_month=end_month,
    )
    completed_totals = {
        str(code) for code in state.get("completed_total_hs_codes", [])
    }
    completed_country_pairs = {
        str(value) for value in state.get("completed_country_hs_pairs", [])
    }

    expected_start = pd.Period(start_month, freq="M").strftime("%Y-%m")
    expected_end = pd.Period(end_month, freq="M").strftime("%Y-%m")
    if (
        not existing_totals.empty
        and existing_totals["STD_YYYYMM"].min() <= expected_start
        and existing_totals["STD_YYYYMM"].max() >= expected_end
    ):
        observed_total_hs = set(existing_totals["hs_code"].astype(str))
        if set(requested_hs_codes).issubset(observed_total_hs):
            completed_totals.update(requested_hs_codes)
    if not existing_countries.empty:
        # (국가, HS) **쌍마다** 기간을 확인한다. 종전에는 국가 단위로 기간만 보고
        # 요청 HS 전부를 완료 처리했다. 그러면 그 국가에 일부 HS 만 있어도
        # 나머지가 수집된 것으로 표시되어 조용히 빠진다(ai#71 Blocking 1).
        pair_span = existing_countries.groupby(
            ["country_code", "hs_code"],
            observed=True,
        )["STD_YYYYMM"].agg(["min", "max"])
        for (country_code, hs_code), row in pair_span.iterrows():
            if row["min"] <= expected_start and row["max"] >= expected_end:
                completed_country_pairs.add(f"{country_code}:{hs_code}")

    missing_total_hs = [
        hs_code
        for hs_code in requested_hs_codes
        if hs_code not in completed_totals
    ]
    missing_country_hs = {
        country_code: [
            hs_code
            for hs_code in requested_hs_codes
            if f"{country_code}:{hs_code}" not in completed_country_pairs
        ]
        for country_code in requested_country_codes
    }
    missing_country_hs = {
        country_code: codes
        for country_code, codes in missing_country_hs.items()
        if codes
    }
    chunk_count = len(_month_chunks(start_month, end_month))
    estimated_requests = chunk_count * (
        len(missing_total_hs)
        + sum(len(codes) for codes in missing_country_hs.values())
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
    LOGGER.info(
        "KCS incremental collection: missing_total_hs=%s "
        "missing_country_hs_pairs=%s estimated_requests=%s",
        len(missing_total_hs),
        sum(len(codes) for codes in missing_country_hs.values()),
        estimated_requests,
    )
    total_cache_path.parent.mkdir(parents=True, exist_ok=True)
    totals = existing_totals
    for index, hs_code in enumerate(missing_total_hs, start=1):
        LOGGER.info(
            "Collecting KCS total HSK %s (%s/%s)",
            hs_code,
            index,
            len(missing_total_hs),
        )
        collected = collect_kcs_trade_totals(
            [hs_code],
            start_month,
            end_month,
            service_key,
            request_xml=request_xml,
            request_delay_seconds=delay,
        )
        totals = _merge_trade_cache(totals, collected)
        _write_trade_cache(totals, total_cache_path)
        completed_totals.add(hs_code)
        state["completed_total_hs_codes"] = sorted(completed_totals)
        state["completed_country_hs_pairs"] = sorted(completed_country_pairs)
        _write_collection_state(state, state_path)

    countries = existing_countries
    pending_country_pairs = sum(
        len(codes) for codes in missing_country_hs.values()
    )
    completed_pending_pairs = 0
    for country_index, (country_code, country_hs_codes) in enumerate(
        sorted(missing_country_hs.items()),
        start=1,
    ):
        LOGGER.info(
            "Collecting KCS country %s for %s HSK codes (%s/%s)",
            country_code,
            len(country_hs_codes),
            country_index,
            len(missing_country_hs),
        )
        for hs_code in country_hs_codes:
            completed_pending_pairs += 1
            LOGGER.info(
                "Collecting KCS country-HSK %s:%s (%s/%s)",
                country_code,
                hs_code,
                completed_pending_pairs,
                pending_country_pairs,
            )
            collected = collect_kcs_country_trade(
                [hs_code],
                [country_code],
                start_month,
                end_month,
                service_key,
                request_xml=request_xml,
                request_delay_seconds=delay,
            )
            countries = _merge_trade_cache(countries, collected)
            _write_trade_cache(countries, country_cache_path)
            completed_country_pairs.add(f"{country_code}:{hs_code}")
            state["completed_total_hs_codes"] = sorted(completed_totals)
            state["completed_country_hs_pairs"] = sorted(
                completed_country_pairs
            )
            _write_collection_state(state, state_path)

    if not total_cache_path.exists():
        _write_trade_cache(totals, total_cache_path)
    if not country_cache_path.exists():
        _write_trade_cache(countries, country_cache_path)
    state["completed_total_hs_codes"] = sorted(completed_totals)
    state["completed_country_hs_pairs"] = sorted(completed_country_pairs)
    _write_collection_state(state, state_path)
    requested_hs = set(requested_hs_codes)
    totals = totals[totals["hs_code"].astype(str).isin(requested_hs)].copy()
    countries = countries[
        countries["hs_code"].astype(str).isin(requested_hs)
        & countries["country_code"].isin(requested_country_codes)
    ].copy()
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
