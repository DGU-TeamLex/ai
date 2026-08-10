"""GDELT 전 기간(2024-01~2025-12) 수집 드라이버.

collect_gdelt_news 는 (월 x 카테고리) 단위로 체크포인트를 즉시 저장하고,
다음 호출에서 그 체크포인트를 건너뛴다. 따라서 429 로 중간에 죽어도
재호출하면 진행이 누적된다. 24개월 x 3카테고리 = 72개가 찰 때까지 재호출한다.

combined 모드는 쓰지 않는다. 질의가 261자라 GDELT 가
"Your query was too short or too long." 로 거부한다(split 은 최대 172자).
"""

import logging
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
LOG = logging.getLogger("collect_driver")

import pandas as pd  # noqa: E402

from src.news.news_collector import collect_gdelt_news  # noqa: E402

START = os.getenv("NEWS_START_DATE", "2024-01-01")
END = os.getenv("NEWS_END_DATE", "2025-12-31")
MAX_RECORDS = int(os.getenv("GDELT_MAX_RECORDS", "250"))
CACHE = Path(os.getenv("NEWS_DATA_PATH", "data/external/news_gdelt_cache.csv"))
PART_DIR = CACHE.parent / f".{CACHE.stem}_parts"
QUERY_MODE = "split"
CATEGORIES = ("infectious_disease", "medical_supply", "raw_material")
MAX_ATTEMPTS = int(os.getenv("GDELT_DRIVER_ATTEMPTS", "40"))


def _expected_parts() -> int:
    months = pd.period_range(START, END, freq="M")
    return len(months) * len(CATEGORIES)


def done_parts() -> int:
    if not PART_DIR.exists():
        return 0
    return sum(
        len(list(PART_DIR.glob(f"*_{category}_{MAX_RECORDS}.csv")))
        for category in CATEGORIES
    )


def main() -> int:
    LOG.info("시작 — 체크포인트 %d/%d", done_parts(), _expected_parts())
    for attempt in range(1, MAX_ATTEMPTS + 1):
        before = done_parts()
        if before >= _expected_parts():
            LOG.info("이미 체크포인트 %d개 완료", before)
            break
        try:
            frame = collect_gdelt_news(
                START,
                END,
                cache_path=CACHE,
                refresh=True,
                max_records=MAX_RECORDS,
                request_delay_seconds=float(
                    os.getenv("GDELT_REQUEST_DELAY_SECONDS", "30")
                ),
                query_mode=QUERY_MODE,
            )
            LOG.info(
                "수집 완료 — 기사 %d건, 체크포인트 %d/%d",
                len(frame),
                done_parts(),
                _expected_parts(),
            )
            return 0
        except Exception as error:  # noqa: BLE001 - 어떤 실패든 재시도 대상
            after = done_parts()
            gained = after - before
            LOG.warning(
                "[시도 %d/%d] 실패: %s: %s | 이번에 받은 체크포인트 %d, 누적 %d/%d",
                attempt,
                MAX_ATTEMPTS,
                type(error).__name__,
                str(error)[:120],
                gained,
                after,
                _expected_parts(),
            )
            if after >= _expected_parts():
                break
            # 진전이 없으면 더 오래 쉰다 (IP 단위 제한이 풀릴 시간을 준다)
            wait = 120 if gained else min(300 * (1 + attempt // 3), 1800)
            LOG.info("  %d초 대기 후 재시도", wait)
            time.sleep(wait)

    total = done_parts()
    LOG.info("루프 종료 — 체크포인트 %d/%d", total, _expected_parts())
    if total < _expected_parts():
        LOG.warning("전 기간 미완. 다시 실행하면 이어서 받는다.")
        return 1

    # 체크포인트가 다 찼으면 요청 없이 캐시만 조립한다.
    frame = collect_gdelt_news(
        START,
        END,
        cache_path=CACHE,
        refresh=True,
        max_records=MAX_RECORDS,
        request_delay_seconds=0.0,
        query_mode=QUERY_MODE,
    )
    LOG.info("캐시 조립 완료 — %d건 → %s", len(frame), CACHE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
