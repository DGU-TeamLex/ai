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
OUT_PATH = OUT_DIR / "gdelt_supply_disruption_news.csv"
PROGRESS_PATH = OUT_DIR / ".gdelt_supply_disruption_progress.json"

# 하루 96개 슬라이스 중 매시 정각만. 표본률 1/4.
MINUTES_SAMPLED = ("0000",)
SAMPLE_RATE = len(MINUTES_SAMPLED) * 24 / 96
DOWNLOAD_WORKERS = 8

# 목표는 **공급 차질 사건 탐지** 다. 가격 등락이 아니다.
#
# 리드타임을 늘려야 하는 것은 공장 가동 중단·파업·항만 적체 같은 **사건** 이고,
# 가격은 그 결과로 따라오는 후행 지표다. 게다가 가격은 Alpha Vantage 로 이미
# 숫자로 갖고 있으므로 뉴스에서 다시 볼 이유가 없다.
#
# 첫 수집(29,453건)은 가격 기사 중심이라 이 목적에 맞지 않았다.
#   "Closing prices for crude oil, gold and other commodities"
#   "ICE cotton drops following US export sales report"

# ── 무엇에 대한 차질인가 ────────────────────────────────────────────────
# material  : 우리 품목에 매핑된 원자재. 그 원자재 품목의 리드타임에 반영한다.
# logistics : 물류·통관 전반. 원자재를 가리지 않으므로 **전 품목** 에 반영한다.
# medical_supply : 완제 의료용품 자체의 공급 차질.
SUBJECT_KEYWORDS = {
    "material": (
        "naphtha", "crude oil", "polypropylene", "polyethylene", "pvc",
        "latex", "nitrile", "aluminum", "aluminium", "cotton", "pulp",
        "petrochemical", "resin", "ethylene", "propylene", "bauxite", "alumina",
    ),
    "logistics": (
        "shipping", "freight", "container", "port", "customs", "tariff",
        "supply chain", "logistics", "vessel", "canal", "shipment",
    ),
    "medical_supply": (
        "medical supplies", "medical device", "syringe", "catheter",
        "respirator", "surgical mask", "surgical glove", "vaccine supply",
    ),
}

# ── 어떤 사건인가 ──────────────────────────────────────────────────────
# 공급이 실제로 끊기거나 늦어지는 사건만 남긴다.
# 가격 등락어(price, surge, soar, plunge, drop)는 **의도적으로 제외** 한다.
#
# 사건어를 두 등급으로 나눈다. 단어 하나로는 걸러지지 않기 때문이다.
# 2차 수집에서 fire/strike/explosion/blast 가 5,176건 중 2,919건(56%)이었는데
# 대부분 무관했다.
#
#   "Crude oil steady on Gaza cease-fire hopes"     ← cease-fire 의 fire
#   "Iran's Strike Against Israel"                  ← 군사 공격
#   "Humpback calf injured in strike with BC Ferries"
#   "Former Port Clinton fire chief indicted"
#
# 명확한 사건어는 단독으로 채택하고, 모호한 사건어는 **산업 문맥 동반**을 요구한다.

# 그 자체로 공급 차질을 뜻하는 표현.
DISRUPTION_UNAMBIGUOUS = (
    "force majeure", "production halt", "halts production", "halted production",
    "suspend production", "suspends production", "suspended production",
    "plant closure", "plant shutdown", "shutdown", "shut down",
    "supply disruption", "supply chain disruption", "shortage",
    "export ban", "export curb", "export control", "export restriction",
    "import ban", "embargo", "sanctions", "quota",
    "port congestion", "port strike", "dock strike", "port closure",
    "shipping delay", "delivery delay", "delayed shipment", "lead time",
    "capacity cut", "output cut", "production cut", "curtailment",
    "backlog", "bottleneck", "walkout", "labor dispute", "labour dispute",
    "outage", "disrupted supply", "supply crunch",
)

# 문맥이 있어야 공급 차질인 표현.
DISRUPTION_AMBIGUOUS = (
    "fire", "explosion", "blast", "strike", "closure", "suspended",
    "disrupted", "blocked", "halted", "disruption", "curtail",
)

# 모호한 사건어를 채택하기 위한 산업·조달 문맥.
INDUSTRIAL_CONTEXT = (
    "plant", "factory", "refinery", "mill", "smelter", "terminal",
    "warehouse", "pipeline", "port", "dock", "harbour", "harbor",
    "cargo", "freight", "shipment", "supply", "production", "output",
    "manufacturer", "manufacturing", "processing", "facility", "operations",
    "exports", "imports", "workers", "union",
)

# 명백한 오탐. 이 표현이 있으면 사건어가 맞아도 버린다.
NEGATIVE_CONTEXT = (
    "cease-fire", "ceasefire", "cease fire",
    "missile strike", "air strike", "airstrike", "drone strike",
    "fire chief", "fire department", "firefighter", "fire station",
    "hunger strike", "strike vote", "on strike over pay",
)

TITLE_PATTERN = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>")

# GKG 는 2019-10 경부터 Extras 에 PAGE_TITLE 을 넣기 시작했다. 실측:
#   2018-01-15 / 2019-01-15 / 2019-07-01 / 2019-09-01  → 보유율 0%
#   2019-10-01 / 2024-01-15                            → 보유율 100%
# 제목이 없으면 사건 분류가 불가능하므로 2018-01~2019-09 가 통째로 비었다.
# 그 구간은 URL 슬러그에서 제목을 복원한다(실측 복원율 80%).
SLUG_STRIP_EXTENSION = re.compile(r"\.(html?|php|aspx?|shtml)$", re.IGNORECASE)
SLUG_SEPARATORS = re.compile(r"[-_]+")
# 슬러그 끝에 붙는 기사 ID(숫자·해시)는 단어가 아니므로 떼어낸다.
SLUG_TRAILING_ID = re.compile(r"(?:\s+[0-9a-f]{5,})+$", re.IGNORECASE)
SLUG_MIN_WORDS = 4


def _title_from_url(url: str) -> str:
    """URL 슬러그에서 제목을 복원한다. 복원 불가면 빈 문자열.

    슬러그는 대개 헤드라인을 하이픈으로 이은 것이라 키워드 매칭에 쓸 수 있다.
    단어가 너무 적으면(카테고리 페이지, 숫자 ID 만 있는 경로) 버린다 —
    잘못 복원한 제목으로 사건을 분류하면 오탐이 늘어난다.
    """
    tail = url.rstrip("/").split("/")[-1]
    tail = SLUG_STRIP_EXTENSION.sub("", tail)
    text = SLUG_SEPARATORS.sub(" ", tail).strip()
    text = SLUG_TRAILING_ID.sub("", text).strip()
    return text if len(text.split()) >= SLUG_MIN_WORDS else ""


def _boundary_pattern(keywords: tuple[str, ...]) -> re.Pattern:
    """단어 경계 매칭.

    첫 수집은 부분 문자열 매칭이라 오탐이 심했다. `ban` 이 Ban-non 에,
    `cut` 이 exe-cut-ion 에, `ppe` 가 a-ppe-al 에 걸려 medical_supply 22,523건
    대부분이 무관한 기사였다("Wrapped BNB Price Reaches $484.95" 등).
    """
    alternatives = "|".join(
        re.escape(keyword) for keyword in sorted(keywords, key=len, reverse=True)
    )
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)


SUBJECT_PATTERNS = {
    subject: _boundary_pattern(keywords)
    for subject, keywords in SUBJECT_KEYWORDS.items()
}
UNAMBIGUOUS_PATTERN = _boundary_pattern(DISRUPTION_UNAMBIGUOUS)
AMBIGUOUS_PATTERN = _boundary_pattern(DISRUPTION_AMBIGUOUS)
INDUSTRIAL_PATTERN = _boundary_pattern(INDUSTRIAL_CONTEXT)
NEGATIVE_PATTERN = re.compile(
    "|".join(re.escape(phrase) for phrase in NEGATIVE_CONTEXT), re.IGNORECASE
)
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


def _classify(title: str) -> tuple[str, str] | None:
    """(대상, 사건어)를 돌려준다. 공급 차질 사건이 아니면 None.

    사건어를 함께 남기는 이유: 나중에 "어떤 사건이 리드타임을 늘렸는가" 를
    되짚을 수 있어야 한다. 점수만 남으면 근거를 잃는다.
    """
    if NEGATIVE_PATTERN.search(title):
        return None

    disruption = UNAMBIGUOUS_PATTERN.search(title)
    if not disruption:
        # 모호한 사건어는 산업·조달 문맥이 함께 있을 때만 채택한다.
        candidate = AMBIGUOUS_PATTERN.search(title)
        if not candidate or not INDUSTRIAL_PATTERN.search(title):
            return None
        disruption = candidate

    for subject, pattern in SUBJECT_PATTERNS.items():
        if pattern.search(title):
            return subject, disruption.group(0).lower()
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
        if match:
            title = match.group(1).strip()
            title_source = "page_title"
        else:
            title = _title_from_url(record[COL_URL] or "")
            title_source = "url_slug"
        if not title:
            continue
        classified = _classify(title)
        if not classified:
            continue
        subject, event = classified
        rows.append(
            {
                "date": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}",
                "title": title,
                "summary": "",
                "source": record[COL_SOURCE],
                "country": "Unknown",
                "url": record[COL_URL],
                "category": subject,
                "disruption_event": event,
                # 2019-10 전후로 제목 출처가 다르다. 시계열 비교 시 이 차이를
                # 반드시 함께 봐야 한다(복원 제목은 정밀도가 낮다).
                "title_source": title_source,
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
            fieldnames=[
                "date", "title", "summary", "source", "country", "url",
                "category", "disruption_event", "title_source",
            ],
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
