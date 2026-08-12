"""프로덕션 DB 에 밀려 있는 멱등 마이그레이션 적용 (backend db/schema.sql 발췌).

배경: backend 의 PR #62 / #68 / #72 가 머지됐으나 DDL 이 프로덕션 DB 에 적용되지
않았다. Vercel 배포는 코드만 올리고 DDL 을 돌리지 않는다. 그 결과 코드가
SELECT 하는 컬럼이 DB 에 없어 500 이 난다.

    psycopg.errors.UndefinedColumn: column inv.order_suppress_reason does not exist
    (db/queries.py:321  inventory_policy_rows)

users.is_active 부재는 로그인 경로도 깨뜨린다.

여기 있는 문장은 전부 ADD COLUMN IF NOT EXISTS / DROP NOT NULL 이라 멱등이고
추가·완화만 한다. 기존 행은 변경되지 않는다. 여러 번 돌려도 안전하다.

실행:
    DATABASE_URL='<Neon DSN>' python scripts/apply_pending_migrations.py
    DATABASE_URL='<Neon DSN>' python scripts/apply_pending_migrations.py --dry-run
"""
import argparse
import os
import sys

import psycopg

STATEMENTS = [
    # PR #62 — 계정 비활성화. 없으면 로그인 쿼리가 깨진다.
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true",
    # PR #68 — 과거 전용 품목 화면 제외용 플래그
    "ALTER TABLE standard_items ADD COLUMN IF NOT EXISTS historical_only BOOLEAN NOT NULL DEFAULT FALSE",
    # PR #72 — 발주권고 NULL 허용 + 감사 컬럼.
    # "발주 안 함(0)" 과 "판단 불가(NULL)" 를 구분하기 위한 변경이다.
    "ALTER TABLE inventory ALTER COLUMN order_recommendation DROP NOT NULL",
    "ALTER TABLE inventory ADD COLUMN IF NOT EXISTS raw_order_recommendation INTEGER",
    "ALTER TABLE inventory ADD COLUMN IF NOT EXISTS order_suppress_reason TEXT",
    "ALTER TABLE inventory ADD COLUMN IF NOT EXISTS mu_is_floored BOOLEAN",
    "ALTER TABLE inventory ADD COLUMN IF NOT EXISTS sigma_is_floored BOOLEAN",
    # db/queries.py 가 SELECT 하지만 db/schema.sql 의 CREATE TABLE 에 없는 8개.
    # 실측: `inv.<컬럼>` 참조 28개 중 8개가 스키마 미선언이다. 프로덕션 DB 에
    # 이미 있으면 IF NOT EXISTS 로 무동작이고, 없으면 해당 쿼리가 500 이다.
    #   mu_forecast        직전 3개월 일수요율 (queries.py:380 은 WHERE 절에도 쓴다)
    #   zero_stock_reason  재고 0 의 사유 (TRUE_STOCKOUT|NOT_OPERATED|DATA_MISSING)
    #   demand_pattern     수요 패턴 분류
    #   is_medical         의약품 여부
    #   item_family_id     동일군 식별자
    #   family_available   동일군 합산 가용재고
    #   family_codes       동일군 구성 품목코드
    #   family_status      동일군 상태
    "ALTER TABLE inventory ADD COLUMN IF NOT EXISTS mu_forecast DOUBLE PRECISION",
    "ALTER TABLE inventory ADD COLUMN IF NOT EXISTS zero_stock_reason TEXT",
    "ALTER TABLE inventory ADD COLUMN IF NOT EXISTS demand_pattern TEXT",
    "ALTER TABLE inventory ADD COLUMN IF NOT EXISTS is_medical BOOLEAN",
    "ALTER TABLE inventory ADD COLUMN IF NOT EXISTS item_family_id TEXT",
    "ALTER TABLE inventory ADD COLUMN IF NOT EXISTS family_available INTEGER",
    "ALTER TABLE inventory ADD COLUMN IF NOT EXISTS family_codes TEXT",
    "ALTER TABLE inventory ADD COLUMN IF NOT EXISTS family_status TEXT",
    # queries.py:380 은 mu_forecast IS NOT NULL 로 거른다. 부분 인덱스가 맞다.
    "CREATE INDEX IF NOT EXISTS idx_inv_mu_forecast ON inventory(mu_forecast) "
    "WHERE mu_forecast IS NOT NULL",
]

VERIFY = [
    ("users", "is_active"),
    ("standard_items", "historical_only"),
    ("inventory", "raw_order_recommendation"),
    ("inventory", "order_suppress_reason"),
    ("inventory", "mu_is_floored"),
    ("inventory", "sigma_is_floored"),
    ("inventory", "mu_forecast"),
    ("inventory", "zero_stock_reason"),
    ("inventory", "demand_pattern"),
    ("inventory", "is_medical"),
    ("inventory", "item_family_id"),
    ("inventory", "family_available"),
    ("inventory", "family_codes"),
    ("inventory", "family_status"),
]


def _report(cur) -> None:
    print("\n=== 적용 후 상태 ===")
    for table, column in VERIFY:
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s AND column_name=%s",
            (table, column),
        )
        print(f"  {'있음' if cur.fetchone() else '없음 ← 실패'}  {table}.{column}")
    cur.execute(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='inventory' "
        "AND column_name='order_recommendation'"
    )
    row = cur.fetchone()
    print(f"  inventory.order_recommendation nullable = {row[0] if row else '(없음)'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="실행하지 않고 적용될 문장만 출력한다",
    )
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("DATABASE_URL 환경변수가 필요하다.", file=sys.stderr)
        return 2

    if args.dry_run:
        print("=== 적용될 문장 (실행하지 않음) ===")
        for statement in STATEMENTS:
            print(f"  {statement};")
        return 0

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for statement in STATEMENTS:
                cur.execute(statement)
                print(f"OK  {statement}")
            _report(cur)
        conn.commit()
    print("\n완료. backend 재배포는 필요 없다(스키마만 바뀌었다).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
