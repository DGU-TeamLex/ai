from collections import defaultdict
from datetime import date
from datetime import datetime, timedelta, timezone
import gzip
import json
import logging
import os
from pathlib import Path
import re
import shutil
import time
from urllib.parse import urlencode
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd


LOGGER = logging.getLogger(__name__)
NEWS_COLUMNS = ["date", "title", "summary", "source", "country", "url"]
REQUIRED_NEWS_COLUMNS = {"date", "title"}
GDELT_DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_NGRAM_BASE_URL = (
    "https://storage.googleapis.com/data.gdeltproject.org/"
    "gdeltv5/weblegacy/ngrams"
)
DEFAULT_GDELT_QUERIES = {
    "infectious_disease": '(pandemic OR epidemic OR influenza OR covid OR "disease outbreak")',
    "medical_supply": '("medical supplies" OR "medical device" OR syringe OR catheter OR respirator) '
    '(shortage OR disruption OR shutdown OR "export ban")',
    "raw_material": '(naphtha OR "crude oil" OR polypropylene OR polyethylene OR PVC '
    'OR latex OR nitrile OR aluminum OR cotton OR pulp) '
    '(shortage OR price OR disruption OR shutdown OR sanction)',
}
GDELT_NGRAM_TOPIC_KEYWORDS = {
    "infectious_disease": (
        "influenza",
        "pandemic",
        "epidemic",
        "covid",
        "coronavirus",
        "avian flu",
        "bird flu",
        "hiv",
        "measles",
        "mpox",
        "disease outbreak",
        "respiratory disease",
        "respiratory illness",
        "infectious disease",
    ),
    "medical_supply": (
        "medical supplies",
        "medical supply",
        "medical device",
        "syringe",
        "catheter",
        "respirator",
    ),
    "raw_material": (
        "naphtha",
        "crude oil",
        "brent",
        "wti",
        "polypropylene",
        "polyethylene",
        "pvc",
        "latex",
        "nitrile",
        "aluminum",
        "cotton",
        "pulp",
    ),
}
GDELT_NGRAM_RISK_KEYWORDS = (
    "outbreak",
    "surge",
    "spread",
    "spike",
    "case",
    "cases",
    "infection",
    "infections",
    "infected",
    "hospitalization",
    "hospitalizations",
    "quarantine",
    "quarantined",
    "suspected",
    "confirmed",
    "investigation",
    "survey",
    "warning",
    "warns",
    "urge",
    "urges",
    "rise",
    "rises",
    "rising",
    "fall",
    "falls",
    "falling",
    "plunge",
    "plunges",
    "lost",
    "rising cases",
    "case increase",
    "hospitalizations",
    "shortage",
    "supply",
    "disruption",
    "supply disruption",
    "supply chain",
    "price increase",
    "price spike",
    "price surge",
    "price",
    "prices",
    "surplus",
    "shutdown",
    "production halt",
    "export ban",
    "export restriction",
    "sanction",
    "customs delay",
    "shipping delay",
    "port strike",
    "war",
    "conflict",
)
# `combined` 모드는 제거했다(ai#22 결정).
#
# 요청 수를 72 → 24 로 줄이려고 세 카테고리를 하나의 OR 질의로 합쳤으나,
# GDELT 가 첫 요청부터 "Your query was too short or too long" 으로 거부한다.
# rate limit 이 아니라 질의 길이 문제다.
#
#     combined  risk_candidate   261자  → 거부
#     split     raw_material     172자  → 정상
#
# 즉 도달은 하지만 **항상 실패하는 경로**였다. 동작하지 않는 선택지를 남겨두면
# 다음 사람이 같은 곳에서 막힌다.
#
# 키워드를 임의로 쳐내는 방식(1안)은 택하지 않았다. recall 평가 없이 줄이면
# 수집 대상이 조용히 줄어든다. 고정 2분할(2안)도 최종 규칙으로 두지 않는다.
# 감염병·의료용품·원자재는 감사 가능한 의미 축이므로 유지하고, 카테고리 내부를
# 검증 가능한 크기로 동적 분할하는 방향이 후속 과제다.
#
# 주의: GDELT 공식 문서에 문자 수 상한이 명시되어 있지 않다. 위 261/172 는
# 재현된 관측이지 계약이 아니다. URL 인코딩 길이와 연산자 구성도 영향을 준다.


class GDELTTransientResponseError(RuntimeError):
    """일시적 오류. 재시도로 풀릴 수 있다 (429, 5xx, 빈 응답, 비JSON)."""


class GDELTPermanentQueryError(RuntimeError):
    """설정 오류. 재시도해도 달라지지 않으므로 즉시 실패시킨다.

    질의 길이 초과가 여기 해당한다. 종전에는 이것이 GDELTTransientResponseError
    로 잡혀 재시도 10회를 그대로 소진했다. 질의 길이는 기다린다고 바뀌지 않는다.
    """


# GDELT 는 질의를 거부할 때 JSON 이 아니라 평문 문장을 돌려준다.
# 실측 문구: "Your query was too short or too long."
_QUERY_LENGTH_REJECTION_PATTERN = re.compile(
    r"query was too (?:short|long)", re.IGNORECASE
)


def _keyword_pattern(keywords: tuple[str, ...]) -> re.Pattern:
    alternatives = "|".join(
        re.escape(keyword) for keyword in sorted(keywords, key=len, reverse=True)
    )
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)


GDELT_NGRAM_TOPIC_PATTERNS = {
    category: _keyword_pattern(keywords)
    for category, keywords in GDELT_NGRAM_TOPIC_KEYWORDS.items()
}
GDELT_NGRAM_TITLE_TOPIC_PATTERNS = {
    **GDELT_NGRAM_TOPIC_PATTERNS,
    "raw_material": _keyword_pattern(
        GDELT_NGRAM_TOPIC_KEYWORDS["raw_material"] + ("oil",)
    ),
}
GDELT_NGRAM_RISK_PATTERN = _keyword_pattern(GDELT_NGRAM_RISK_KEYWORDS)


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
    max_retries = max(
        0,
        min(int(os.getenv("GDELT_MAX_RETRIES", "8")), 20),
    )
    rate_limit_backoff = max(
        float(os.getenv("GDELT_RATE_LIMIT_BACKOFF_SECONDS", "30.0")),
        1.0,
    )
    max_backoff = max(
        float(os.getenv("GDELT_MAX_BACKOFF_SECONDS", "300.0")),
        rate_limit_backoff,
    )
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            with urlopen(request, timeout=60) as response:
                response_body = response.read()
            if not response_body.strip():
                raise GDELTTransientResponseError(
                    "GDELT returned an empty response body"
                )
            try:
                payload = json.loads(response_body)
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                preview = response_body[:160].decode("utf-8", errors="replace")
                # 질의 길이 초과는 평문으로 온다. 재시도해도 질의는 안 바뀌므로
                # 일시 오류로 취급하면 10회를 그대로 날린다(ai#22).
                if _QUERY_LENGTH_REJECTION_PATTERN.search(preview):
                    raise GDELTPermanentQueryError(
                        f"GDELT 가 질의를 거부했다(길이/구성 문제, 재시도 무의미): "
                        f"{preview!r}"
                    ) from error
                raise GDELTTransientResponseError(
                    f"GDELT returned a non-JSON response: {preview!r}"
                ) from error
            if not isinstance(payload, dict):
                raise GDELTTransientResponseError(
                    f"GDELT returned an unexpected JSON value: {type(payload).__name__}"
                )
            return payload.get("articles", [])
        except HTTPError as error:
            last_error = error
            if error.code != 429 and error.code < 500:
                raise
            retry_after = error.headers.get("Retry-After") if error.headers else None
            if retry_after and retry_after.isdigit():
                wait_seconds = float(retry_after)
            elif error.code == 429:
                wait_seconds = min(rate_limit_backoff * (2 ** attempt), max_backoff)
            else:
                wait_seconds = min(2 ** (attempt + 1), max_backoff)
        except URLError as error:
            last_error = error
            wait_seconds = min(2 ** (attempt + 1), max_backoff)
        except GDELTTransientResponseError as error:
            last_error = error
            wait_seconds = min(rate_limit_backoff * (2 ** attempt), max_backoff)
        if attempt == max_retries:
            break
        if isinstance(last_error, HTTPError):
            LOGGER.warning(
                "GDELT request failed with HTTP %s; retrying in %.0f seconds "
                "(attempt %s/%s)",
                last_error.code,
                wait_seconds,
                attempt + 1,
                max_retries,
            )
        else:
            LOGGER.warning(
                "GDELT request failed: %s; retrying in %.0f seconds "
                "(attempt %s/%s)",
                getattr(last_error, "reason", last_error),
                wait_seconds,
                attempt + 1,
                max_retries,
            )
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
    query_mode: str = "split",
) -> pd.DataFrame:
    if cache_path and cache_path.exists() and not refresh:
        LOGGER.info("Loading cached GDELT news: %s", cache_path)
        return load_news_csv(cache_path)

    max_records = max(1, min(int(max_records), 250))
    selected_query_mode = query_mode.strip().lower()
    if selected_query_mode == "combined":
        # 조용히 split 으로 넘기지 않는다. 설정한 사람이 combined 로 돌아간다고
        # 믿은 채 다른 결과를 받으면 더 나쁘다. 명시적으로 실패시킨다.
        raise ValueError(
            "GDELT_QUERY_MODE=combined 는 지원하지 않는다(ai#22). GDELT 가 질의 "
            "길이 초과로 항상 거부하므로 제거했다. 'split' 을 쓸 것."
        )
    if selected_query_mode != "split":
        raise ValueError(
            f"Unsupported GDELT query mode: {query_mode}. Expected 'split'."
        )
    queries = DEFAULT_GDELT_QUERIES
    rows = []
    part_dir = cache_path.parent / f".{cache_path.stem}_parts" if cache_path else None
    if part_dir:
        part_dir.mkdir(parents=True, exist_ok=True)
    for window_start, window_end in _month_windows(start_date, end_date):
        for category, query in queries.items():
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


def _gdelt_ngram_urls(batch_id: str) -> tuple[str, str]:
    return (
        f"{GDELT_NGRAM_BASE_URL}/{batch_id}.ngrams.txt.gz",
        f"{GDELT_NGRAM_BASE_URL}/{batch_id}.toc.json.gz",
    )


def _remote_file_exists(url: str) -> bool:
    try:
        with urlopen(Request(url, method="HEAD"), timeout=30):
            return True
    except HTTPError as error:
        if error.code == 404:
            return False
        raise


def _find_recent_gdelt_ngram_batches(
    batch_count: int,
    lookback_minutes: int,
    now: datetime | None = None,
) -> list[str]:
    if batch_count < 1:
        raise ValueError("GDELT NGram batch count must be at least 1")
    if lookback_minutes < batch_count:
        raise ValueError(
            "GDELT NGram lookback minutes must be at least the batch count"
        )

    cursor = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cursor = cursor.replace(second=0, microsecond=0) - timedelta(minutes=5)
    batches = []
    for offset in range(lookback_minutes):
        batch_id = (cursor - timedelta(minutes=offset)).strftime("%Y%m%d%H%M00")
        ngram_url, _ = _gdelt_ngram_urls(batch_id)
        if _remote_file_exists(ngram_url):
            batches.append(batch_id)
            if len(batches) == batch_count:
                break
    return sorted(batches)


def _download_gdelt_ngram_file(url: str, destination: Path) -> None:
    if destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = destination.with_suffix(destination.suffix + ".part")
    try:
        with urlopen(Request(url, headers={"User-Agent": "WeP-Stock-AI/1.0"}), timeout=120) as response:
            with temporary_path.open("wb") as output:
                shutil.copyfileobj(response, output)
        temporary_path.replace(destination)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _match_gdelt_ngram_documents(ngram_path: Path) -> dict[int, dict]:
    matched_documents: dict[int, dict] = defaultdict(
        lambda: {
            "topics": set(),
            "coupled_topics": set(),
            "has_risk_context": False,
            "snippets": [],
        }
    )
    with gzip.open(ngram_path, "rt", encoding="utf-8", errors="replace") as rows:
        for row in rows:
            fields = row.rstrip("\n").split("\t", 2)
            if len(fields) != 3:
                continue
            document_id_value, quadgram, _ = fields
            try:
                document_id = int(document_id_value)
            except ValueError:
                continue

            topics = {
                category
                for category, pattern in GDELT_NGRAM_TOPIC_PATTERNS.items()
                if pattern.search(quadgram)
            }
            has_risk_context = bool(GDELT_NGRAM_RISK_PATTERN.search(quadgram))
            if not topics and not has_risk_context:
                continue

            evidence = matched_documents[document_id]
            evidence["topics"].update(topics)
            evidence["has_risk_context"] |= has_risk_context
            if has_risk_context:
                evidence["coupled_topics"].update(topics)
            if quadgram not in evidence["snippets"] and len(evidence["snippets"]) < 12:
                evidence["snippets"].append(quadgram)
    return {
        document_id: evidence
        for document_id, evidence in matched_documents.items()
        if evidence["topics"] and evidence["has_risk_context"]
    }


def _url_has_stale_publication_year(url: str, observed_date: str) -> bool:
    try:
        observed_year = pd.Timestamp(observed_date).year
    except (TypeError, ValueError):
        return False
    path_years = {
        int(value)
        for value in re.findall(r"(?<!\d)(20\d{2})(?!\d)", urlparse(url).path)
    }
    return bool(path_years) and max(path_years) < observed_year - 1


def _news_from_gdelt_ngram_batch(
    ngram_path: Path,
    toc_path: Path,
    languages: set[str],
) -> list[dict]:
    matches = _match_gdelt_ngram_documents(ngram_path)
    if not matches:
        return []

    rows = []
    with gzip.open(toc_path, "rt", encoding="utf-8", errors="replace") as toc:
        for line in toc:
            try:
                article = json.loads(line)
                document_id = int(article.get("ID"))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            evidence = matches.get(document_id)
            if not evidence:
                continue
            language = str(article.get("lang") or "").strip().lower()
            if languages and language not in languages:
                continue
            url = str(article.get("url") or "").strip()
            observed_date = str(article.get("date") or "")
            if _url_has_stale_publication_year(url, observed_date):
                continue

            title = str(article.get("title") or "")
            title_topics = {
                category
                for category, pattern in GDELT_NGRAM_TITLE_TOPIC_PATTERNS.items()
                if pattern.search(title)
            }
            accepted_topics: set[str] = set()
            if GDELT_NGRAM_RISK_PATTERN.search(title):
                accepted_topics.update(title_topics & evidence["topics"])
            if not accepted_topics:
                continue

            topics = ",".join(sorted(accepted_topics))
            snippets = " | ".join(evidence["snippets"])
            rows.append(
                {
                    "date": observed_date,
                    "title": title,
                    "summary": f"ngram_topics={topics}; evidence={snippets}",
                    "source": urlparse(url).netloc.lower() or "unknown",
                    "country": "Unknown",
                    "url": url,
                }
            )
    return rows


def collect_gdelt_ngram_news(
    cache_path: Path,
    refresh: bool = False,
    batch_count: int = 4,
    lookback_minutes: int = 45,
    languages: set[str] | None = None,
    append_cache: bool = True,
) -> pd.DataFrame:
    if cache_path.exists() and not refresh:
        LOGGER.info("Loading cached GDELT Web NGrams news: %s", cache_path)
        return load_news_csv(cache_path)

    selected_languages = languages if languages is not None else {"en", "ko"}
    batch_ids = _find_recent_gdelt_ngram_batches(
        batch_count=batch_count,
        lookback_minutes=lookback_minutes,
    )
    if not batch_ids:
        raise RuntimeError(
            "No GDELT Web NGrams batches were found in the configured lookback window"
        )

    part_dir = cache_path.parent / ".gdelt_ngram_parts"
    rows = []
    for batch_id in batch_ids:
        ngram_url, toc_url = _gdelt_ngram_urls(batch_id)
        ngram_path = part_dir / f"{batch_id}.ngrams.txt.gz"
        toc_path = part_dir / f"{batch_id}.toc.json.gz"
        _download_gdelt_ngram_file(ngram_url, ngram_path)
        _download_gdelt_ngram_file(toc_url, toc_path)
        rows.extend(
            _news_from_gdelt_ngram_batch(
                ngram_path=ngram_path,
                toc_path=toc_path,
                languages=selected_languages,
            )
        )

    if cache_path.exists() and append_cache:
        rows.extend(load_news_csv(cache_path).to_dict(orient="records"))
    result = _normalize_news(pd.DataFrame(rows, columns=NEWS_COLUMNS))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(cache_path, index=False)
    LOGGER.info(
        "Saved GDELT Web NGrams news cache: %s (%s rows, batches=%s)",
        cache_path,
        len(result),
        ",".join(batch_ids),
    )
    return result


def collect_news(provider: str | None = None, data_path: str | Path | None = None) -> pd.DataFrame:
    selected_provider = (provider or os.getenv("NEWS_PROVIDER", "disabled")).strip().lower()
    if selected_provider in {"disabled", "none"}:
        return pd.DataFrame(columns=NEWS_COLUMNS)
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
        request_delay_seconds = float(
            os.getenv("GDELT_REQUEST_DELAY_SECONDS", "5.0")
        )
        query_mode = os.getenv("GDELT_QUERY_MODE", "split")
        if request_delay_seconds < 0:
            raise ValueError("GDELT request delay must be non-negative")
        return collect_gdelt_news(
            start_date=start_date,
            end_date=end_date,
            cache_path=cache_path,
            refresh=refresh,
            max_records=max_records,
            request_delay_seconds=request_delay_seconds,
            query_mode=query_mode,
        )
    if selected_provider == "gdelt_ngram":
        cache_value = data_path or os.getenv("NEWS_DATA_PATH")
        if not cache_value:
            raise ValueError(
                "NEWS_DATA_PATH is required when NEWS_PROVIDER=gdelt_ngram"
            )
        refresh = os.getenv("NEWS_REFRESH", "false").strip().lower() == "true"
        batch_count = int(os.getenv("GDELT_NGRAM_BATCH_COUNT", "4"))
        lookback_minutes = int(os.getenv("GDELT_NGRAM_LOOKBACK_MINUTES", "45"))
        languages = {
            value.strip().lower()
            for value in os.getenv("GDELT_NGRAM_LANGUAGES", "en,ko").split(",")
            if value.strip()
        }
        append_cache = (
            os.getenv("GDELT_NGRAM_APPEND_CACHE", "true").strip().lower()
            == "true"
        )
        return collect_gdelt_ngram_news(
            cache_path=Path(cache_value),
            refresh=refresh,
            batch_count=batch_count,
            lookback_minutes=lookback_minutes,
            languages=languages,
            append_cache=append_cache,
        )
    raise ValueError(f"Unsupported NEWS_PROVIDER: {selected_provider}")


if __name__ == "__main__":
    collected_news = collect_news()
    if collected_news.empty:
        print("Collected 0 news rows")
    else:
        print(
            "Collected "
            f"{len(collected_news)} news rows "
            f"({collected_news['date'].min()}..{collected_news['date'].max()})"
        )
