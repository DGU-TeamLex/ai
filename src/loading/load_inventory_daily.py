"""SSIS 원장 DAT → `inventory_daily` 적재 (backend#63 / ai#34).

원장은 (기관 × 부서 × 물품 × 재고마감일) 일별 행이다. 기존 `import_ssis_dataset.py` 는
이를 기관×물품으로 **집계해서** `inventory` 에 넣는 스크립트라 시간축이 사라진다.
이 스크립트는 원장을 그대로 `inventory_daily` 에 넣어 백테스트·결측일 복원·폐기 지표·
2018~19 적재를 가능하게 한다.

## 안전 설계

⚠️ 이 스크립트는 **TRUNCATE 를 하지 않는다.** `scripts/seed_db.py` 와
`scripts/import_ssis_dataset.py` 는 시작 시 `inventory` 를 TRUNCATE 하므로 운영 DB 에
그대로 돌리면 mu/rop/발주권고/status 가 전부 날아간다. 이 스크립트는 `inventory` 를
건드리지 않고 `inventory_daily` 에만 INSERT 한다.

- `inventory_daily` 에는 PK 가 없다. 자연키로 보이는
  `(institution_code, dept_code, standard_code, stock_date)` 가 원본에서 유니크하지 않기
  때문이다 — 2018~19 실측 중복키 2,955개(초과 3,201행, 0.052%), 표본 140건 중 107건은
  같은 키에 값이 다르다. 임의로 하나만 남기면 원장이 왜곡되므로 전부 보존한다.
  재적재는 `load_batch_id` 범위 삭제로 처리한다(`RELOAD=1`).
- 기관코드는 **익명 원본 코드(`보건기관코드_en`)를 그대로** 저장한다.
  `institutions.id` 로 매핑하지 않으므로 "부분집합을 따로 정렬하면 전량 오매핑"
  (ai#42) 함정에 걸리지 않는다. 매핑이 필요한 조회 시점에 조인한다.
- 파싱은 `csv.reader(delimiter='|', quotechar='"')`. 물품명에 줄바꿈이 인용된 행이 있어
  naive split 은 2018~19 에서 2,744행을 잃는다(backend#66).
- 컬럼은 헤더명으로 매핑한다. 2018~19 는 16컬럼, 2024~25 는 18컬럼(`구입처코드`·`구입단가`)이다.

## 검증

적재 후 행수를 정의서 기재값(`EXPECTED_ROWS`)과 대조하고, 재고 항등식
(자동폐기출고량 제외, ai#42)과 음수 재고를 집계해 보고한다.

## 실행

    DATABASE_URL=... \
    SSIS_DATA_DIR=~/Downloads/SSIS_20260728 SSIS_FILE_GLOB='stock_*.DAT' \
    EXPECTED_ROWS=6106936 \
    python3 src/loading/load_inventory_daily.py

    DRY_RUN=1 이면 파싱·검증만 하고 DB 에 쓰지 않는다.
"""
import csv
import glob
import io
import os
import time

import psycopg

csv.field_size_limit(10 ** 9)

DATA_DIR = os.path.expanduser(os.environ.get("SSIS_DATA_DIR", "."))
FILE_GLOB = os.environ.get("SSIS_FILE_GLOB", "*.DAT")
EXPECTED_ROWS = int(os.environ.get("EXPECTED_ROWS", "0"))
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
# 적재 배치 식별자. 같은 배치로 다시 넣으려면 RELOAD=1 (해당 배치 행을 지우고 재적재).
BATCH_ID = os.environ.get("LOAD_BATCH_ID", "ssis_2018_2019")
RELOAD = os.environ.get("RELOAD", "0") == "1"

# 원본 컬럼 → inventory_daily 컬럼
COLMAP = [
    ("보건기관코드_en", "institution_code"),
    ("부서코드", "dept_code"),
    ("물품코드", "standard_code"),
    ("재고마감일", "stock_date"),
    ("이전최종재고량", "prev_closing_qty"),
    ("마감재고량", "closing_qty"),
    ("입고량", "qty_in"),
    ("불출입고량", "qty_in_transfer"),
    ("반납입고량", "qty_in_return"),
    ("불출출고량", "qty_out_transfer"),
    ("정상출고량", "qty_out_normal"),
    ("반품출고량", "qty_out_return"),
    ("폐기출고량", "qty_out_disposal"),
    ("자동폐기출고량", "qty_out_auto_disposal"),
    ("보정출고량", "qty_out_adjust"),
    ("구입처코드", "purchase_place_code"),   # 2018~19 없음
    ("구입단가", "purchase_unit_price"),     # 2018~19 없음
]
TARGET_COLS = [dst for _, dst in COLMAP]
NUMERIC = {c for c in TARGET_COLS if c.startswith(("prev_", "closing_", "qty_", "purchase_unit"))}


def num(s):
    if s is None or s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def rows_from(path, stats):
    """DAT 한 파일을 (컬럼 순서에 맞춘) 튜플로 스트리밍한다."""
    with io.open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter="|", quotechar='"')
        header = [h.strip() for h in next(reader)]
        idx = {name: i for i, name in enumerate(header)}
        missing = [src for src, _ in COLMAP[:15] if src not in idx]  # 앞 15개는 필수
        if missing:
            raise SystemExit(f"[FATAL] {path}: 필수 컬럼 누락 {missing}\n  헤더: {header}")
        ncol = len(header)
        # 항등식 검증용 인덱스
        inflow = [idx[c] for c in ("입고량", "불출입고량", "반납입고량") if c in idx]
        outflow = [idx[c] for c in ("불출출고량", "정상출고량", "반품출고량",
                                    "폐기출고량", "보정출고량") if c in idx]
        i_prev, i_close = idx["이전최종재고량"], idx["마감재고량"]

        for parts in reader:
            if len(parts) != ncol:
                stats["bad_cols"] += 1
                continue
            stats["read"] += 1

            prev_v, close_v = num(parts[i_prev]) or 0.0, num(parts[i_close]) or 0.0
            if close_v < 0:
                stats["neg_closing"] += 1
            if prev_v < 0:
                stats["neg_prev"] += 1
            if inflow and outflow:
                expected = (prev_v
                            + sum(num(parts[j]) or 0.0 for j in inflow)
                            - sum(num(parts[j]) or 0.0 for j in outflow))
                stats["identity_checked"] += 1
                if abs(expected - close_v) > 1e-6:
                    stats["identity_bad"] += 1

            out = []
            for src, dst in COLMAP:
                if src not in idx:
                    out.append(None)                       # 2018~19 미존재 컬럼
                    continue
                raw = parts[idx[src]]
                if dst == "stock_date":
                    out.append(f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) == 8 else None)
                elif dst == "dept_code":
                    out.append(raw or "")
                elif dst in NUMERIC:
                    out.append(num(raw))
                else:
                    out.append(raw or None)
            if out[0] is None or out[2] is None or out[3] is None:
                stats["bad_key"] += 1
                continue
            yield tuple(out)


def main():
    files = sorted(glob.glob(os.path.join(DATA_DIR, FILE_GLOB)))
    if not files:
        raise SystemExit(f"[FATAL] 대상 파일 없음: {DATA_DIR}/{FILE_GLOB}")
    print(f"대상 파일 {len(files)}개")

    stats = dict(read=0, bad_cols=0, bad_key=0, neg_closing=0, neg_prev=0,
                 identity_checked=0, identity_bad=0)
    t0 = time.time()

    if DRY_RUN:
        for p in files:
            for _ in rows_from(p, stats):
                pass
        inserted = 0
    else:
        cols = ", ".join(TARGET_COLS)
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT count(*) FROM inventory_daily WHERE load_batch_id = %s", (BATCH_ID,)
            )
            already = cur.fetchone()[0]
            if already:
                if not RELOAD:
                    raise SystemExit(
                        f"[중단] 배치 '{BATCH_ID}' 로 적재된 행이 이미 {already:,}개 있다.\n"
                        f"  다시 넣으려면 RELOAD=1 (해당 배치 행을 지우고 재적재), "
                        f"이어붙이려면 LOAD_BATCH_ID 를 다르게 지정할 것."
                    )
                cur.execute("DELETE FROM inventory_daily WHERE load_batch_id = %s", (BATCH_ID,))
                print(f"RELOAD=1 — 기존 배치 {cur.rowcount:,}행 삭제", flush=True)

            # PK 가 없으므로(원본에 중복키 존재, schema.sql 주석 참조) 파티션으로
            # 바로 COPY 한다. 스테이징 후 INSERT..ON CONFLICT 경로는 같은 데이터를
            # 두 번 쓰고 행마다 인덱스를 조회해 실측상 30분이 넘어도 끝나지 않았다.
            # 재적재는 load_batch_id 범위 삭제로 처리한다.
            print(f"직접 COPY (배치 '{BATCH_ID}')", flush=True)
            n = 0
            with cur.copy(f"COPY inventory_daily ({cols}, load_batch_id) FROM STDIN") as cp:
                for p in files:
                    before = stats["read"]
                    for row in rows_from(p, stats):
                        cp.write_row(row + (BATCH_ID,))
                        n += 1
                    print(f"  {os.path.basename(p)}: {stats['read'] - before:,}행", flush=True)
            inserted = n
            conn.commit()

    el = time.time() - t0
    print(f"\n읽음 {stats['read']:,}행 / 적재 {inserted:,}행 ({el:.1f}s)")
    print(f"  컬럼수 불일치 {stats['bad_cols']:,} · 키 결측 {stats['bad_key']:,}")
    print(f"  음수 마감재고 {stats['neg_closing']:,} · 음수 이전최종 {stats['neg_prev']:,}")
    if stats["identity_checked"]:
        ok = stats["identity_checked"] - stats["identity_bad"]
        print(f"  항등식(자동폐기 제외) 일치율 {100 * ok / stats['identity_checked']:.3f}% "
              f"({stats['identity_bad']:,}건 불일치)")

    total_seen = stats["read"] + stats["bad_cols"]
    if EXPECTED_ROWS:
        if total_seen == EXPECTED_ROWS:
            print(f"  [GATE OK] 정의서 기재값과 일치 ({EXPECTED_ROWS:,})")
        else:
            print(f"  [GATE WARN] 불일치 — 읽음 {total_seen:,} vs 정의서 {EXPECTED_ROWS:,} "
                  f"({total_seen - EXPECTED_ROWS:+,})")
    else:
        print("  [GATE SKIP] EXPECTED_ROWS 미지정")

    if not DRY_RUN and inserted < stats["read"]:
        print(f"  ※ 중복 스킵 {stats['read'] - inserted:,}행 (재실행이면 정상)")


if __name__ == "__main__":
    main()
