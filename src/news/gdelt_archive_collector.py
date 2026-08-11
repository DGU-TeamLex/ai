"""GDELT GKG 원본 아카이브에서 과거 뉴스 수집 — 계정·키·인증 불필요.

## 왜 이 경로인가

앞선 두 경로가 막혔다.

* **DOC 2.0 API** — `artlist` 모드가 최근 3개월만 서빙한다. 실측:
  2024-01 요청 시 응답 250건이 오지만 요청 창 안은 0건(전부 2024-02-01).
  PR #69 수집분의 "창 밖 68.8%" 도 같은 원인이다.
* **네이버/카카오 검색 API** — 날짜 범위 지정 파라미터가 없다. 최신순 1,000건이
  상한이라 "2024년 3월 기사만" 을 받을 수 없다. DOC API 와 같은 실패다.
* **BigQuery** — 데이터는 있으나 GCP 프로젝트와 결제 계정이 필요하다.

GKG 원본 파일은 `data.gdeltproject.org` 에 그냥 열려 있다. 인증이 없다.
15분마다 한 파일이고 zip 약 5MB, 기사 약 1,345건, `PAGE_TITLE` 보유율 100%.

## 표본 설계

전량은 96파일/일 x 548일 = 약 52,600파일 = 263GB 다운로드다. 우리 뉴스 위험은
**월 단위 집계**이므로 전수가 필요하지 않다. 매시 정각 슬라이스 하나씩
(하루 24파일) 체계적 표본을 쓴다.

    표본률 = 24/96 = 1/4,  다운로드 약 66GB,  예상 매칭 약 1.3만 건

시각 고정 표본이라 요일·시간대 편향이 월 간 일정하게 유지된다. 월별 **상대**
변화를 보는 우리 용도에는 이것으로 충분하다. 절대 건수를 쓰려면 표본률로
보정해야 하며, 그 사실을 산출물에 `sample_rate` 로 남긴다.

원본은 저장하지 않는다. 내려받아 걸러내고 버린다(디스크 263GB 를 쓰지 않는다).

실행:
    python -m src.news.gdelt_archive_collector --start 2024-01-01 --end 2025-06-30
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import re
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

import pandas as pd

from ..config import PROJECT_ROOT
from ..utils import ensure_dirs

LOGGER = logging.getLogger(__name__)
csv.field_size_limit(10**8)

BASE_URL = "http://data.gdeltproject.org/gdeltv2"
OUT_DIR = PROJECT_ROOT / "data" / "raw" / "news"
OUT_PATH = OUT_DIR / "gdelt_archive_history.csv"
PROGRESS_PATH = OUT_DIR / ".gdelt_archive_progress.json"

# 하루 96개 슬라이스 중 매시 정각만. 표본률 1/4.
MINUTES_SAMPLED = ("0000",)
SAMPLE_RATE = len(MINUTES_SAMPLED) * 24 / 96
DOWNLOAD_WORKERS = 8

# DOC API 질의(DEFAULT_GDELT_QUERIES)와 같은 세 축을 유지한다.
TOPIC_KEYWORDS = {
    "infectious_disease": (
        "pandemic", "epidemic", "influenza", "covid", "outbreak",
        "measles", "mpox", "cholera", "dengue",
    ),
    "medical_supply": (
        "medical supplies", "medical device", "syringe", "catheter",
        "respirator", "ppe", "vaccine", "surgical mask", "glove",
    ),
    "raw_material": (
        "naphtha", "crude oil", "polypropylene", "polyethylene", "pvc",
        "latex", "nitrile", "aluminum", "aluminium", "cotton", "pulp",
        "petrochemical", "resin",
    ),
}
# 단순 언급을 버리고 위험 문맥이 있는 것만 남긴다.
# DOC API 질의의 두 번째 괄호와 같은 역할이다.
RISK_KEYWORDS = (
    "shortage", "disruption", "shutdown", "export ban", "sanction",
    "price", "supply chain", "halt", "strike", "surge", "soar", "plunge",
    "crisis", "delay", "ban", "cut",
)

TITLE_PATTERN = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>")
# GKG 2.0 열 순서 (0-based): 1=DATE, 3=SourceCommonName, 4=DocumentIdentifier
COL_DATE, COL_SOURCE, COL_URL = 1, 3, 4


def _slice_urls(start: date, end: date) -> list[tuple[str, str]]:
    urls = []
    cursor = start
    while cursor <= end:
        for hour in range(24):
            for minute in MINUTES_SAMPLED:
                stamp = f"{cursor:%Y%m%d}{hour:02d}{minute}"
                urls.append((stamp, f"{BASE_URL}/{stamp}.gkg.csv.zip"))
        cursor += timedelta(days=1)
    return urls


def _classify(title: str) -> str | None:
    lowered = title.lower()
    if not any(word in lowered for word in RISK_KEYWORDS):
        return None
    for category, keywords in TOPIC_KEYWORDS.items():
        if any(word in lowered for word in keywords):
            return category
    return None


def _fetch_slice(stamp: str, url: str) -> list[dict]:
    """한 슬라이스를 내려받아 걸러진 행만 돌려준다. 원본은 버린다."""
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            payload = response.read()
    except urllib.error.HTTPError as error:
        # 일부 슬라이스는 실제로 존재하지 않는다(수집 중단 구간).
        if error.code == 404:
            return []
        raise
    except urllib.error.URLError:
        return []

    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
        text = archive.read(archive.namelist()[0]).decode("utf-8", "replace")
    except (zipfile.BadZipFile, IndexError):
        LOGGER.warning("깨진 zip: %s", stamp)
        return []

    rows = []
    for record in csv.reader(io.StringIO(text), delimiter="\t"):
        if len(record) < 27:
            continue
        match = TITLE_PATTERN.search(record[-1] or "")
        if not match:
            continue
        title = match.group(1).strip()
        category = _classify(title)
        if not category:
            continue
        rows.append(
            {
                "date": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}",
                "title": title,
                "summary": "",
                "source": record[COL_SOURCE],
                "country": "Unknown",
                "url": record[COL_URL],
                "category": category,
            }
        )
    return rows


def _load_progress() -> set[str]:
    if not PROGRESS_PATH.exists():
        return set()
    try:
        return set(json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))["done"])
    except (json.JSONDecodeError, KeyError):
        return set()


def _save_progress(done: set[str]) -> None:
    PROGRESS_PATH.write_text(
        json.dumps({"done": sorted(done), "sample_rate": SAMPLE_RATE}),
        encoding="utf-8",
    )


def collect(start: str, end: str) -> None:
    ensure_dirs(OUT_DIR)
    done = _load_progress()
    targets = [
        (stamp, url)
        for stamp, url in _slice_urls(date.fromisoformat(start), date.fromisoformat(end))
        if stamp not in done
    ]
    LOGGER.info(
        "대상 슬라이스 %s개 (완료 %s개 건너뜀), 표본률 %.2f",
        f"{len(targets):,}", f"{len(done):,}", SAMPLE_RATE,
    )

    write_header = not OUT_PATH.exists()
    kept = 0
    started = datetime.now()
    with OUT_PATH.open("a", encoding="utf-8-sig", newline="") as sink:
        writer = csv.DictWriter(
            sink,
            fieldnames=["date", "title", "summary", "source", "country", "url", "category"],
        )
        if write_header:
            writer.writeheader()
        with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
            futures = {pool.submit(_fetch_slice, s, u): s for s, u in targets}
            for index, future in enumerate(as_completed(futures), start=1):
                stamp = futures[future]
                try:
                    rows = future.result()
                except Exception as error:  # noqa: BLE001 - 한 슬라이스 실패로 전체를 멈추지 않는다
                    LOGGER.warning("슬라이스 실패 %s: %s", stamp, error)
                    continue
                writer.writerows(rows)
                kept += len(rows)
                done.add(stamp)
                if index % 200 == 0:
                    sink.flush()
                    _save_progress(done)
                    elapsed = (datetime.now() - started).total_seconds() / 60
                    remaining = (len(targets) - index) * elapsed / index
                    LOGGER.info(
                        "%s/%s 슬라이스 | 누적 %s건 | %.0f분 경과 | 남은 %.0f분",
                        f"{index:,}", f"{len(targets):,}", f"{kept:,}", elapsed, remaining,
                    )
    _save_progress(done)
    LOGGER.info("수집 완료: %s건 → %s", f"{kept:,}", OUT_PATH)


def finalize() -> None:
    """URL 중복 제거 후 월별 분포를 보고한다."""
    frame = pd.read_csv(OUT_PATH)
    before = len(frame)
    frame = frame.drop_duplicates(subset="url", keep="first").sort_values("date")
    frame.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    LOGGER.info("중복 제거: %s → %s건", f"{before:,}", f"{len(frame):,}")
    monthly = frame.groupby(pd.to_datetime(frame["date"]).dt.to_period("M")).size()
    LOGGER.info("월별 기사 수:\n%s", monthly.to_string())
    LOGGER.info("카테고리:\n%s", frame["category"].value_counts().to_string())


def main() -> None:
    parser = argparse.ArgumentParser(description="GDELT GKG 원본 아카이브 뉴스 수집")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2025-06-30")
    parser.add_argument("--finalize-only", action="store_true")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not arguments.finalize_only:
        collect(arguments.start, arguments.end)
    finalize()


if __name__ == "__main__":
    main()
