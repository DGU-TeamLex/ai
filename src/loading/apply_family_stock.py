"""family(동일 품목군) 단위 재고 집계 — 물품코드 분산으로 인한 긴급부족 오탐 제거 (ai#33).

[문제] 같은 보건소에 동일 품목이 업체·규격 표기 차이로 물품코드 여러 개로 분산 등록됨.
  한 코드는 재고 100개인데 옆 코드는 0개 → 개별 코드 기준으로 CRITICAL 오탐.
  실측(2026-07-23): 코드 분산 family 60,195 / 오탐 family 36,691 /
  **CRITICAL 오탐 81,250건 = 전체 CRITICAL(128,250)의 63%**.
  혈당스틱 최대 사례: inst_1100 코드 52개(회의 언급 '8개'보다 훨씬 분산), inst_0824는
  코드 44개 중 16개 CRITICAL인데 기관 합계재고 10,316개(발주 불필요).

[해결] 기관×item_family_id 로 가용재고를 합산해 family 기준 상태를 별도 산출한다.
  개별 코드 status 는 그대로 두고(세부품목 단위 표기 유지, 7/16 소유권 결정), 화면이
  family_status 로 오탐을 걸러낼 수 있게 값을 추가 제공하는 방식.

[핵심 규칙] family_status='CRITICAL' 은 **family 합계 가용재고 ≤ 0** 일 때만.
  → 같은 family 에 재고가 있으면 개별 코드가 0이어도 긴급부족이 아님(이슈 완료조건).

[범위] 재고 집계 기준 상태 판별까지. family 단위 μ·SS/ROP 재도출은 코드별 mu 하한(0.5)이
  합산되며 과대해지는 문제가 있어 ai#23(SS/ROP 이관)에서 함께 다룬다.
  여기서 쓰는 family_rop 은 코드별 rop 단순합(근사)이며, CRITICAL 판정에는 쓰지 않는다.

원장 불필요 — DB 내부(inventory × item_meta_map) 집계만으로 산출. DRY_RUN=1 기본.
"""
import os

import psycopg

DRY_RUN = os.environ.get("DRY_RUN", "1") == "1"

# 기관 × family 집계 (available 은 음수 방어를 위해 0 하한)
FAM_CTE = """
WITH fam AS (
  SELECT i.institution_id,
         m.item_family_id,
         SUM(GREATEST(COALESCE(i.available, 0), 0)) AS fam_avail,
         SUM(COALESCE(i.rop, 0))                    AS fam_rop,
         count(*)                                   AS fam_codes
  FROM inventory i
  JOIN item_meta_map m ON m.standard_code = i.standard_code
  WHERE m.item_family_id IS NOT NULL AND m.item_family_id <> ''
  GROUP BY 1, 2
)
"""
# family 집계 기준 상태. CRITICAL 은 family 재고가 실제로 바닥일 때만 부여.
FAM_STATUS = """CASE
    WHEN f.fam_avail <= 0                 THEN 'CRITICAL'
    WHEN f.fam_avail <  f.fam_rop         THEN 'BELOW_ROP'
    WHEN f.fam_avail <  f.fam_rop * 1.2   THEN 'WATCH'
    ELSE 'OK' END"""

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    cur = conn.cursor()
    for ddl in (
        "ALTER TABLE inventory ADD COLUMN IF NOT EXISTS item_family_id TEXT",
        "ALTER TABLE inventory ADD COLUMN IF NOT EXISTS family_available DOUBLE PRECISION",
        "ALTER TABLE inventory ADD COLUMN IF NOT EXISTS family_codes INT",
        "ALTER TABLE inventory ADD COLUMN IF NOT EXISTS family_status TEXT",
    ):
        cur.execute(ddl)

    # ── 효과 측정: 개별 CRITICAL 중 family 로 보면 재고가 있는 건(=오탐) ──
    cur.execute(FAM_CTE + f"""
      SELECT count(*) FILTER (WHERE i.status='CRITICAL')                              AS crit,
             count(*) FILTER (WHERE i.status='CRITICAL' AND f.fam_avail > 0)          AS false_pos,
             count(*) FILTER (WHERE i.status='CRITICAL' AND {FAM_STATUS} = 'CRITICAL') AS true_crit,
             count(*) FILTER (WHERE f.fam_codes > 1)                                  AS split_rows
      FROM inventory i
      JOIN item_meta_map m ON m.standard_code = i.standard_code
      JOIN fam f ON f.institution_id = i.institution_id
                AND f.item_family_id = m.item_family_id
    """)
    crit, fp, tc, split = cur.fetchone()
    print(f"[분석] family 매핑된 행 기준 CRITICAL {crit:,}")
    print(f"   ├ family 에 가용재고 있음(=오탐) {fp:,} ({fp / crit * 100:.1f}%)")
    print(f"   ├ family 도 바닥(=진짜 결품)    {tc:,} ({tc / crit * 100:.1f}%)")
    print(f"   └ 코드 분산(2개↑) family 소속 행 {split:,}")

    if DRY_RUN:
        conn.rollback()
        print("*** DRY_RUN=1 미반영 ***")
    else:
        cur.execute(FAM_CTE + f"""
          UPDATE inventory i SET
            item_family_id   = m.item_family_id,
            family_available = f.fam_avail,
            family_codes     = f.fam_codes,
            family_status    = {FAM_STATUS}
          FROM item_meta_map m, fam f
          WHERE i.standard_code = m.standard_code
            AND f.institution_id = i.institution_id
            AND f.item_family_id = m.item_family_id
        """)
        print(f"UPDATE {cur.rowcount:,}행")
        cur.execute("""SELECT count(family_status),
                              count(*) FILTER (WHERE status='CRITICAL' AND family_status<>'CRITICAL')
                       FROM inventory""")
        r = cur.fetchone()
        print(f"family_status 채워짐 {r[0]:,} / 개별CRITICAL이지만 family는 정상 {r[1]:,}")
        conn.commit()
        print("*** 커밋 완료 ***")
