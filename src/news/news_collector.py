from datetime import date
import json
import logging
import os
from pathlib import Path
import time
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd


LOGGER = logging.getLogger(__name__)
NEWS_COLUMNS = ["date", "title", "summary", "source", "country", "url"]
REQUIRED_NEWS_COLUMNS = {"date", "title"}
GDELT_DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
DEFAULT_GDELT_QUERIES = {
    "infectious_disease": '(pandemic OR epidemic OR influenza OR covid OR "disease outbreak")',
    "medical_supply": '("medical supplies" OR "medical device" OR syringe OR catheter OR respirator) '
    '(shortage OR disruption OR shutdown OR "export ban")',
    "raw_material": '(latex OR polypropylene OR nitrile OR "medical grade plastic") '
    '(shortage OR price OR disruption OR shutdown)',
}


SAMPLE_NEWS = [
    {
        "date": date(2025, 1, 15).isoformat(),
        "title": "인플루엔자 환자 급증으로 호흡기 진료 수요 증가",
        "summary": "한국과 인접한 국가에서 독감 확산세가 이어지며 보건소 대응 물품 수요가 늘고 있다.",
        "source": "sample",
        "country": "Korea",
        "url": "sample://infectious-disease-1",
    },
    {
        "date": date(2025, 2, 10).isoformat(),
        "title": "라텍스 공장 가동 중단으로 의료용 장갑 공급 차질 우려",
        "summary": "말레이시아 주요 라텍스 공장이 폭우로 중단되며 의료용 장갑 조달 지연이 예상된다.",
        "source": "sample",
        "country": "Malaysia",
        "url": "sample://supply-risk-1",
    },
    {
        "date": date(2025, 3, 5).isoformat(),
        "title": "원유 가격 상승으로 플라스틱 원재료 비용 부담 확대",
        "summary": "중동 지정학 리스크로 원유와 플라스틱 계열 원자재 가격 변동성이 확대됐다.",
        "source": "sample",
        "country": "Global",
        "url": "sample://material-price-1",
    },
]


def _normalize_news(news: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED_NEWS_COLUMNS - set(news.columns)
    if missing:
        raise ValueError(f"News data is missing required columns: {sorted(missing)}")

    result = news.copy()
    for column, default in {
        "summary": "",
        "source": "unknown",
        "country": "Unknown",
        "url": "",
    }.items():
        if column not in result.columns:
            result[column] = default
        result[column] = result[column].fillna(default).astype(str)

    result["title"] = result["title"].fillna("").astype(str).str.strip()
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    invalid_count = int((result["date"].isna() | result["title"].eq("")).sum())
    if invalid_count:
        LOGGER.warning("Dropped %s news rows with an invalid date or empty title", invalid_count)
    result = result[result["date"].notna() & result["title"].ne("")].copy()
    result["date"] = result["date"].dt.strftime("%Y-%m-%d")

    identity = result["url"].where(
        result["url"].ne(""),
        result["date"] + "|" + result["source"] + "|" + result["title"],
    )
    result = result.loc[~identity.duplicated()].sort_values(["date", "source", "title"])
    return result[NEWS_COLUMNS].reset_index(drop=True)


def load_news_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"News CSV not found: {path}")
    return _normalize_news(pd.read_csv(path))


def _request_gdelt_articles(
    query: str,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    max_records: int,
) -> list[dict]:
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "sort": "datedesc",
        "maxrecords": max_records,
        "startdatetime": start_date.strftime("%Y%m%d000000"),
        "enddatetime": end_date.strftime("%Y%m%d235959"),
    }
    request = Request(
        f"{GDELT_DOC_API_URL}?{urlencode(params)}",
        headers={"User-Agent": "WeP-Stock-AI/1.0"},
    )
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            with urlopen(request, timeout=60) as response:
                payload = json.load(response)
            return payload.get("articles", [])
        except HTTPError as error:
            last_error = error
            if error.code != 429 and error.code < 500:
                raise
            retry_after = error.headers.get("Retry-After") if error.headers else None
            wait_seconds = float(retry_after) if retry_after and retry_after.isdigit() else min(2 ** (attempt + 1), 30)
            LOGGER.warning("GDELT request failed with HTTP %s; retrying in %.0f seconds", error.code, wait_seconds)
        except URLError as error:
            last_error = error
            wait_seconds = min(2 ** (attempt + 1), 30)
            LOGGER.warning("GDELT request failed: %s; retrying in %.0f seconds", error.reason, wait_seconds)
        if attempt == 4:
            break
        time.sleep(wait_seconds)
    if last_error:
        raise last_error
    return []


def _month_windows(start_date: str, end_date: str) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if pd.isna(start) or pd.isna(end) or start > end:
        raise ValueError(f"Invalid GDELT date range: {start_date} - {end_date}")

    windows = []
    cursor = start
    while cursor <= end:
        month_end = min(cursor + pd.offsets.MonthEnd(0), end)
        windows.append((cursor, month_end))
        cursor = month_end + pd.Timedelta(days=1)
    return windows


def collect_gdelt_news(
    start_date: str,
    end_date: str,
    cache_path: Path | None = None,
    refresh: bool = False,
    max_records: int = 250,
    request_delay_seconds: float = 2.0,
) -> pd.DataFrame:
    if cache_path and cache_path.exists() and not refresh:
        LOGGER.info("Loading cached GDELT news: %s", cache_path)
        return load_news_csv(cache_path)

    max_records = max(1, min(int(max_records), 250))
    rows = []
    part_dir = cache_path.parent / f".{cache_path.stem}_parts" if cache_path else None
    if part_dir:
        part_dir.mkdir(parents=True, exist_ok=True)
    for window_start, window_end in _month_windows(start_date, end_date):
        for category, query in DEFAULT_GDELT_QUERIES.items():
            part_path = None
            if part_dir:
                part_path = part_dir / f"{window_start.strftime('%Y%m')}_{category}_{max_records}.csv"
                if part_path.exists():
                    LOGGER.info("Loading GDELT checkpoint: %s", part_path)
                    rows.extend(load_news_csv(part_path).to_dict(orient="records"))
                    continue
            LOGGER.info("Collecting GDELT category=%s range=%s..%s", category, window_start.date(), window_end.date())
            articles = _request_gdelt_articles(query, window_start, window_end, max_records)
            part_rows = []
            for article in articles:
                part_rows.append(
                    {
                        "date": article.get("seendate"),
                        "title": article.get("title"),
                        "summary": "",
                        "source": article.get("domain") or "unknown",
                        "country": article.get("sourcecountry") or "Unknown",
                        "url": article.get("url") or "",
                    }
                )
            rows.extend(part_rows)
            if part_path:
                _normalize_news(pd.DataFrame(part_rows, columns=NEWS_COLUMNS)).to_csv(part_path, index=False)
            if request_delay_seconds > 0:
                time.sleep(request_delay_seconds)

    result = _normalize_news(pd.DataFrame(rows, columns=NEWS_COLUMNS))
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(cache_path, index=False)
        LOGGER.info("Saved GDELT news cache: %s (%s rows)", cache_path, len(result))
    return result


def collect_news(provider: str | None = None, data_path: str | Path | None = None) -> pd.DataFrame:
    selected_provider = (provider or os.getenv("NEWS_PROVIDER", "sample")).strip().lower()
    if selected_provider == "sample":
        return _normalize_news(pd.DataFrame(SAMPLE_NEWS))
    if selected_provider == "csv":
        selected_path = data_path or os.getenv("NEWS_DATA_PATH")
        if not selected_path:
            raise ValueError("NEWS_DATA_PATH is required when NEWS_PROVIDER=csv")
        return load_news_csv(Path(selected_path))
    if selected_provider == "gdelt":
        start_date = os.getenv("NEWS_START_DATE")
        end_date = os.getenv("NEWS_END_DATE")
        if not start_date or not end_date:
            raise ValueError("NEWS_START_DATE and NEWS_END_DATE are required when NEWS_PROVIDER=gdelt")
        cache_value = data_path or os.getenv("NEWS_DATA_PATH")
        cache_path = Path(cache_value) if cache_value else None
        refresh = os.getenv("NEWS_REFRESH", "false").strip().lower() == "true"
        max_records = int(os.getenv("GDELT_MAX_RECORDS", "250"))
        return collect_gdelt_news(
            start_date=start_date,
            end_date=end_date,
            cache_path=cache_path,
            refresh=refresh,
            max_records=max_records,
        )
    raise ValueError(f"Unsupported NEWS_PROVIDER: {selected_provider}")


if __name__ == "__main__":
    print(collect_news().to_string(index=False))
