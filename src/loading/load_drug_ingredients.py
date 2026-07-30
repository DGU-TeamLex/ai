"""약성분 DAT → `drug_ingredients` + `drug_ingredient_master` 적재 (backend#65).

2026-07-28 수신 약성분 데이터셋(8컬럼 3,576,320행, 기관 3,861 × 약품 80,934)을 적재한다.

## 두 테이블

1. `drug_ingredients` — 원본 그대로. PK `(institution_code, drug_code)`.
   실측상 이 쌍이 전수 유니크(유니크 쌍 = 파일 행수)임을 확인하고 PK 로 삼았다.
2. `drug_ingredient_master` — 약품코드 canonical 매핑. **기관 간 교차 보완** 결과다.
   행 단위 성분코드 결측은 12.75% 지만, 같은 약품이라도 기관에 따라 채워진 곳이 있어
   약품코드 기준으로 합치면 87.49%(70,812/80,934)까지 외부 데이터 없이 복구된다.
   대표 성분코드는 **최다 기관이 보고한 값**(동률이면 코드 오름차순 — 결정적)이다.

## 안전 설계

- `inventory` 를 포함해 기존 테이블을 건드리지 않는다. TRUNCATE 없음.
- PK 충돌은 `DO NOTHING` → 재실행 가능(멱등).
- 기관코드는 익명 원본 코드를 그대로 저장한다(`institutions.id` 매핑 안 함).
- 조인키 무변환: `drug_code` = 물품재고의 `물품코드` = `standard_items.standard_code`.
- `drug_ingredient_master` 에는 `standard_items` FK 가 없다 — 약품코드 80,934종 중
  카탈로그(17,148종)에 없는 코드가 대부분이라 FK 를 걸면 적재가 실패한다.

## 실행

    DATABASE_URL=... SSIS_DRUG_FILE=~/Downloads/SSIS_20260728/drug_ingredient.DAT \
    EXPECTED_ROWS=3576320 python3 src/loading/load_drug_ingredients.py

    기본값은 DRY_RUN=1이다. 실제 적재는 DRY_RUN=0을 명시해야 한다.
"""
import csv
import io
import os
import time

csv.field_size_limit(10 ** 9)

DRUG_FILE = os.path.expanduser(
    os.environ.get("SSIS_DRUG_FILE", "~/Downloads/SSIS_20260728/drug_ingredient.DAT")
)
EXPECTED_ROWS = int(os.environ.get("EXPECTED_ROWS", "0"))
DRY_RUN = os.environ.get("DRY_RUN", "1") == "1"

COLMAP = [
    ("보건기관코드_en", "institution_code"),
    ("약품코드", "drug_code"),
    ("약품명1", "drug_name"),
    ("용도구분", "usage_division"),
    ("약종류구분", "drug_kind"),
    ("약품단위1", "drug_unit"),
    ("성분코드", "ingredient_code"),
    ("성분명", "ingredient_name"),
]
COLS = [dst for _, dst in COLMAP]


def scan(stats, votes, names, attrs):
    """원본을 스트리밍하며 (행 튜플)을 내보내고, 동시에 master 집계를 누적한다."""
    with io.open(DRUG_FILE, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f, delimiter="|", quotechar='"')
        header = [h.strip() for h in next(reader)]
        idx = {n: i for i, n in enumerate(header)}
        missing = [src for src, _ in COLMAP if src not in idx]
        if missing:
            raise SystemExit(f"[FATAL] 필수 컬럼 누락 {missing}\n  헤더: {header}")
        ncol = len(header)

        for parts in reader:
            if len(parts) != ncol:
                stats["bad_cols"] += 1
                continue
            stats["read"] += 1

            drug = parts[idx["약품코드"]]
            inst = parts[idx["보건기관코드_en"]]
            if not drug or not inst:
                stats["bad_key"] += 1
                continue

            code = (parts[idx["성분코드"]] or "").strip()
            if code:
                votes.setdefault(drug, {}).setdefault(code, set()).add(inst)
                if code not in names:
                    names[code] = (parts[idx["성분명"]] or "").strip()
            else:
                stats["missing_ing"] += 1

            if drug not in attrs:
                attrs[drug] = (parts[idx["약품명1"]], parts[idx["용도구분"]],
                               parts[idx["약종류구분"]], parts[idx["약품단위1"]])

            yield tuple((parts[idx[src]] or None) for src, _ in COLMAP)


def build_master(votes, names, attrs):
    """약품코드별 canonical 매핑 — 기관 간 교차 보완."""
    for drug, (dname, usage, kind, unit) in attrs.items():
        v = votes.get(drug)
        if v:
            counts = {
                code: len(institutions)
                for code, institutions in v.items()
            }
            top, n = sorted(
                counts.items(),
                key=lambda item: (-item[1], item[0]),
            )[0]
        else:
            top, n = None, 0
        yield (drug, top, names.get(top) if top else None, usage or None,
               kind or None, bool(v and len(v) > 1), n)


def main():
    import psycopg

    print(f"원본: {DRUG_FILE}")
    stats = dict(read=0, bad_cols=0, bad_key=0, missing_ing=0)
    votes = {}
    names, attrs = {}, {}
    t0 = time.time()

    if DRY_RUN:
        for _ in scan(stats, votes, names, attrs):
            pass
        ins_rows = ins_master = 0
    else:
        cols = ", ".join(COLS)
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            cur = conn.cursor()
            cur.execute("CREATE TEMP TABLE _di_stage (LIKE drug_ingredients) ON COMMIT DROP")
            with cur.copy(f"COPY _di_stage ({cols}) FROM STDIN") as cp:
                for row in scan(stats, votes, names, attrs):
                    cp.write_row(row)
            cur.execute(
                f"INSERT INTO drug_ingredients ({cols}) SELECT {cols} FROM _di_stage "
                f"ON CONFLICT DO NOTHING"
            )
            ins_rows = cur.rowcount
            print(f"drug_ingredients 적재 {ins_rows:,}행", flush=True)

            mcols = ("drug_code, ingredient_code, ingredient_name, usage_division, "
                     "drug_kind, has_multiple_ingredients, source_institution_count")
            cur.execute(f"CREATE TEMP TABLE _dm_stage (LIKE drug_ingredient_master) ON COMMIT DROP")
            with cur.copy(f"COPY _dm_stage ({mcols}) FROM STDIN") as cp:
                for row in build_master(votes, names, attrs):
                    cp.write_row(row)
            cur.execute(
                f"INSERT INTO drug_ingredient_master ({mcols}) SELECT {mcols} FROM _dm_stage "
                f"ON CONFLICT (drug_code) DO UPDATE SET "
                f"ingredient_code=EXCLUDED.ingredient_code, "
                f"ingredient_name=EXCLUDED.ingredient_name, "
                f"usage_division=EXCLUDED.usage_division, drug_kind=EXCLUDED.drug_kind, "
                f"has_multiple_ingredients=EXCLUDED.has_multiple_ingredients, "
                f"source_institution_count=EXCLUDED.source_institution_count"
            )
            ins_master = cur.rowcount
            conn.commit()

    el = time.time() - t0
    drugs = len(attrs)
    with_code = sum(1 for d in attrs if votes.get(d))
    multi = sum(1 for d, v in votes.items() if len(v) > 1)

    print(f"\n읽음 {stats['read']:,}행 ({el:.1f}s)")
    print(f"  약품코드 {drugs:,}종 · 성분코드 {len(names):,}종")
    print(f"  행 단위 성분코드 결측 {stats['missing_ing']:,} "
          f"({100 * stats['missing_ing'] / max(1, stats['read']):.2f}%)")
    print(f"  교차 보완 후 확보 {with_code:,}/{drugs:,} = {100 * with_code / max(1, drugs):.2f}%")
    print(f"  복수 성분 약품 {multi:,}종")
    if not DRY_RUN:
        print(f"  적재: drug_ingredients {ins_rows:,} · master {ins_master:,}")

    total_seen = stats["read"] + stats["bad_cols"]
    if EXPECTED_ROWS:
        ok = total_seen == EXPECTED_ROWS
        print(f"  [GATE {'OK' if ok else 'WARN'}] 읽음 {total_seen:,} vs 정의서 {EXPECTED_ROWS:,}")
    else:
        print("  [GATE SKIP] EXPECTED_ROWS 미지정")


if __name__ == "__main__":
    main()
