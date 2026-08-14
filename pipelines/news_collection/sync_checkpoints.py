"""GDELT 수집 체크포인트를 handoff parquet 으로 내보내고 되돌린다.

GDELT DOC API 는 IP 단위로 요청을 강하게 제한한다. 실측하면 4~5요청이 통과한
뒤 긴 429 벽이 오고, 24개월 x 3분할 = 72요청을 다 받는 데 8시간 이상 걸린다.
그래서 수집 결과는 `.gitignore` 된 `data/external/` 에만 두면 기기를 옮길
때마다 처음부터 다시 받아야 한다.

`data/handoff/` 는 "재생성 비용이 크고 정의 고정이 필요한" 산출물을 위한
자리이므로 여기에 모아 둔다. 같은 입력으로 같은 WAPE 가 나오는지 확인하려면
기사 원본이 고정돼 있어야 한다는 점에서도 여기가 맞다.

    export   체크포인트 -> data/handoff/news_gdelt_articles.parquet
    restore  parquet -> 체크포인트 (다른 기기에서 이어받기)
    status   진행률만 출력

사용:
    python -m pipelines.news_collection.sync_checkpoints export
    python -m pipelines.news_collection.sync_checkpoints restore
    python -m pipelines.news_collection.sync_checkpoints status
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import PROJECT_ROOT  # noqa: E402

CATEGORIES = ("infectious_disease", "medical_supply", "raw_material")
MAX_RECORDS = 250
EXPECTED_PARTS = 24 * len(CATEGORIES)
NEWS_COLUMNS = ["date", "title", "summary", "source", "country", "url"]
PART_COLUMNS = ["source_month", "source_category"]
HANDOFF_DIR = PROJECT_ROOT / "data" / "handoff"
HANDOFF_PATH = HANDOFF_DIR / "news_gdelt_articles.parquet"
DEFAULT_CACHE_PATH = PROJECT_ROOT / "data" / "external" / "news_gdelt_cache.csv"


def cache_path() -> Path:
    """수집기와 같은 규칙으로 캐시 경로를 정한다 (NEWS_DATA_PATH 환경변수)."""
    value = os.getenv("NEWS_DATA_PATH")
    return Path(value) if value else DEFAULT_CACHE_PATH


def part_dir() -> Path:
    cache = cache_path()
    return cache.parent / f".{cache.stem}_parts"


def part_files() -> list[Path]:
    directory = part_dir()
    if not directory.exists():
        return []
    found: list[Path] = []
    for category in CATEGORIES:
        found.extend(sorted(directory.glob(f"*_{category}_{MAX_RECORDS}.csv")))
    return sorted(found)


def _split_name(path: Path) -> tuple[str, str]:
    """`202401_medical_supply_250.csv` -> ("202401", "medical_supply")."""
    stem = path.stem
    month, rest = stem.split("_", 1)
    category = rest.rsplit("_", 1)[0]
    return month, category


def export_parts() -> int:
    files = part_files()
    if not files:
        print("체크포인트가 없다. 먼저 수집을 돌려라.")
        return 1

    frames = []
    for path in files:
        month, category = _split_name(path)
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        frame["source_month"] = month
        frame["source_category"] = category
        frames.append(frame)

    combined = pd.concat(frames, ignore_index=True)
    missing = [column for column in NEWS_COLUMNS if column not in combined.columns]
    if missing:
        raise ValueError(f"체크포인트에 없는 컬럼: {missing}")
    combined = combined[[*NEWS_COLUMNS, *PART_COLUMNS]]

    HANDOFF_DIR.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(HANDOFF_PATH, index=False, compression="zstd")
    size_mb = HANDOFF_PATH.stat().st_size / 1048576
    print(f"내보냄 {HANDOFF_PATH}")
    print(f"  체크포인트 {len(files)}/{EXPECTED_PARTS}   기사 {len(combined):,}건   {size_mb:,.2f} MB")
    print(f"  기간 {combined.source_month.min()} ~ {combined.source_month.max()}")
    return 0


def restore_parts() -> int:
    if not HANDOFF_PATH.exists():
        print(f"핸드오프 파일이 없다: {HANDOFF_PATH}")
        return 1

    combined = pd.read_parquet(HANDOFF_PATH)
    directory = part_dir()
    directory.mkdir(parents=True, exist_ok=True)

    written = skipped = 0
    for (month, category), group in combined.groupby(["source_month", "source_category"]):
        target = directory / f"{month}_{category}_{MAX_RECORDS}.csv"
        if target.exists():
            skipped += 1
            continue
        group[NEWS_COLUMNS].to_csv(target, index=False)
        written += 1

    print(f"복원 완료 -> {directory}")
    print(f"  새로 쓴 체크포인트 {written}   이미 있어 건너뜀 {skipped}")
    print(f"  현재 {len(part_files())}/{EXPECTED_PARTS}")
    return 0


def status() -> int:
    files = part_files()
    have = {_split_name(path) for path in files}
    print(f"체크포인트 {len(files)}/{EXPECTED_PARTS}")
    months = sorted({month for month, _ in have})
    if months:
        print(f"  받은 달 {len(months)}개: {months[0]} ~ {months[-1]}")
    incomplete = [
        month
        for month in months
        if any((month, category) not in have for category in CATEGORIES)
    ]
    if incomplete:
        print(f"  분할이 덜 찬 달: {incomplete}")
    if HANDOFF_PATH.exists():
        size_mb = HANDOFF_PATH.stat().st_size / 1048576
        print(f"  핸드오프 {HANDOFF_PATH.name} {size_mb:,.2f} MB")
    else:
        print("  핸드오프 파일 없음")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["export", "restore", "status"])
    args = parser.parse_args()
    if args.action == "export":
        return export_parts()
    if args.action == "restore":
        return restore_parts()
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
