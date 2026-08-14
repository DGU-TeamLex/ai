"""기관 매핑이 실제로 맞는지 DB 와 대조해 검증한다 (ai#54, backend#16).

## 왜 필요한가

`data/mapping/institution_id_mapping.csv` 는 **0행이다.** 대신 AI 와 backend 가
각각 정렬 후 위치로 맞춘다.

    backend  scripts/import_ssis_dataset.py:370
             code_to_real = dict(zip(anon_codes, sorted(real_ids)))
    ai       src/loading/compute_mu_forecast.py:29
             mapping = dict(zip(anon_full, sorted(real)))

두 집합의 정렬 순서가 정확히 같을 때만 맞는다. 그런데

    원장 고유 기관   3,530
    institutions     3,598      ← 68개 차이

backend 는 자기 파싱 결과(불량행·극단값 제외 후) 집합을 쓰고 AI 는 정규화
parquet 집합을 쓴다. 중간에 하나라도 어긋나면 **그 뒤가 전부 밀린다.**
`zip()` 은 조용히 잘라내므로 오매핑이 예외 없이 통과한다.

이 상태로 mu_forecast 를 적재하면 **다른 기관의 수요가 들어간다.** 적재 전에
반드시 확인해야 한다.

## 어떻게 검증하나

매핑이 맞다면 같은 (기관, 품목) 에 대해 원장의 최근 마감재고와 DB 의 on_hand
가 일치해야 한다. backend 가 on_hand 를 "최근 마감재고량" 으로 넣기 때문이다
(import_ssis_dataset.py:344 `available = max(0, int(round(on_hand)))`).

    일치율이 높다        → 매핑이 맞다
    일치율이 낮다        → 위치가 밀렸다. 적재하면 안 된다

대조군으로 **의도적으로 한 칸 민 매핑** 의 일치율도 같이 낸다. 올바른 매핑과
차이가 없다면 이 검사 자체에 검정력이 없다는 뜻이므로 그것도 보고한다.

실행:
    DATABASE_URL='<DSN>' python scripts/verify_institution_mapping.py
    DATABASE_URL='<DSN>' python scripts/verify_institution_mapping.py --sample 50000
"""
import argparse
import os
import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import CURRENT_RAW_STOCK_FILE_PATTERN, RAW_STOCK_DIR  # noqa: E402
from src.data_loader import _read_stock_chunks, discover_raw_stock_files  # noqa: E402

INST_IDS_PATH = ROOT / "data" / "mapping" / "institution_ids_sorted.csv"
CHUNK_ROWS = 400_000
# 일치 판정 허용 오차. 원장 마감재고와 DB on_hand 는 같은 값이어야 하지만
# backend 가 round 하므로 1 이내는 같은 것으로 본다.
TOLERANCE = 1.0
# 이 밑이면 매핑이 틀렸다고 본다. 올바른 매핑이라면 대부분 일치해야 한다.
MIN_MATCH_RATIO = 0.50


def _latest_ledger_stock() -> pd.DataFrame:
    """기관 × 물품별 최근 마감재고를 원장에서 뽑는다."""
    frames = []
    for path in discover_raw_stock_files(RAW_STOCK_DIR, CURRENT_RAW_STOCK_FILE_PATTERN):
        for chunk in _read_stock_chunks(path, CHUNK_ROWS):
            need = ["보건기관코드_en", "물품코드", "재고마감일", "마감재고량"]
            if any(column not in chunk.columns for column in need):
                continue
            frame = chunk[need].copy()
            frame["재고마감일"] = pd.to_datetime(frame["재고마감일"], errors="coerce")
            frame["마감재고량"] = pd.to_numeric(frame["마감재고량"], errors="coerce")
            frames.append(frame.dropna(subset=["재고마감일", "마감재고량"]))
    if not frames:
        raise RuntimeError("원장을 읽지 못했다.")
    ledger = pd.concat(frames, ignore_index=True)
    ledger = ledger.sort_values("재고마감일").groupby(
        ["보건기관코드_en", "물품코드"], as_index=False, observed=True
    ).tail(1)
    return ledger.rename(
        columns={"보건기관코드_en": "anon_code", "물품코드": "standard_code",
                 "마감재고량": "ledger_stock"}
    )[["anon_code", "standard_code", "ledger_stock"]]


def _positional_mapping(anon_codes: list[str], offset: int = 0) -> dict:
    """정렬-zip 매핑. offset 은 대조군용으로 일부러 밀 때 쓴다."""
    real = sorted(pd.read_csv(INST_IDS_PATH)["institution_id"].astype(str))
    if offset:
        real = real[offset:] + real[:offset]
    return dict(zip(sorted(anon_codes), real))


def _match_ratio(ledger: pd.DataFrame, inventory: pd.DataFrame, mapping: dict) -> tuple:
    mapped = ledger.assign(institution_id=ledger["anon_code"].map(mapping)).dropna(
        subset=["institution_id"]
    )
    joined = mapped.merge(inventory, on=["institution_id", "standard_code"], how="inner")
    if joined.empty:
        return 0.0, 0, 0
    agree = (joined["ledger_stock"] - joined["on_hand"]).abs() <= TOLERANCE
    return float(agree.mean()), int(agree.sum()), len(joined)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=0, help="DB 에서 읽을 최대 행수")
    arguments = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("DATABASE_URL 환경변수가 필요하다.", file=sys.stderr)
        return 2

    import psycopg

    query = "SELECT institution_id, standard_code, on_hand FROM inventory"
    if arguments.sample:
        query += f" LIMIT {int(arguments.sample)}"
    with psycopg.connect(dsn) as connection:
        inventory = pd.read_sql(query, connection)
    inventory["on_hand"] = pd.to_numeric(inventory["on_hand"], errors="coerce")
    print(f"DB inventory {len(inventory):,}행  기관 {inventory['institution_id'].nunique():,}")

    ledger = _latest_ledger_stock()
    anon_codes = sorted(ledger["anon_code"].astype(str).unique())
    real_count = len(pd.read_csv(INST_IDS_PATH))
    print(f"원장 기관 {len(anon_codes):,}  institutions {real_count:,}  "
          f"차이 {real_count - len(anon_codes):,}")
    print()

    ratio, agree, total = _match_ratio(ledger, inventory, _positional_mapping(anon_codes))
    print(f"== 현행 정렬-zip 매핑 ==")
    print(f"  조인 {total:,}행 중 재고 일치 {agree:,}행 ({ratio:.2%})")

    print(f"\n== 대조군 (한 칸 민 매핑) ==")
    shifted, shifted_agree, shifted_total = _match_ratio(
        ledger, inventory, _positional_mapping(anon_codes, offset=1)
    )
    print(f"  조인 {shifted_total:,}행 중 재고 일치 {shifted_agree:,}행 ({shifted:.2%})")

    print()
    if total == 0:
        print("판정 불가 — 조인되는 행이 없다. 품목코드 체계부터 확인하라.")
        return 1
    if ratio - shifted < 0.10:
        print("⚠ 검사에 검정력이 없다 — 올바른 매핑과 어긋난 매핑의 일치율 차이가 "
              f"{ratio - shifted:.2%} 뿐이다. 이 방법으로는 매핑을 검증할 수 없다.")
        return 1
    if ratio < MIN_MATCH_RATIO:
        print(f"✗ 매핑이 틀렸다 — 일치율 {ratio:.2%} < 기준 {MIN_MATCH_RATIO:.0%}. "
              "이 상태로 적재하면 다른 기관의 값이 들어간다.")
        return 1
    print(f"✓ 매핑이 맞다고 볼 근거가 있다 — 일치율 {ratio:.2%}, "
          f"어긋난 매핑 대비 {ratio - shifted:.2%}p 우위.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
