"""
이슈 #25 첫 산출물 적재: demand_class, mu_corrected

전제조건
-------
1. backend가 먼저 스키마 변경을 해야 함:
     ALTER TABLE inventory ADD COLUMN demand_class TEXT;
     ALTER TABLE inventory ADD COLUMN mu_corrected DOUBLE PRECISION;
   (이슈#25 소유권 경계: 스키마=backend, 데이터=ai)
2. DATABASE_URL 환경변수 설정 필요.
3. compute_demand_class_mu_corrected.py 를 먼저 실행해서
   output_full/backtest/demand_class_mu_corrected_handoff.csv 를 만들어둬야 함.

이슈#25가 지적한 함정 반영
------------------------
- Neon pooled(PgBouncer) 세션 재사용 대응: 임시테이블 DROP TABLE IF EXISTS + ON COMMIT DROP
- COPY -> 임시테이블 -> UPDATE ... FROM 패턴 (409k행 단건 UPDATE 방지)
- 적재 후 저장된 on_hand 기준으로 order_recommendation/status 재계산 (backend#52 사고 재발 방지)
- 기관코드 매핑은 backend와 동일한 정렬 zip 방식 사용 (단, 이 매핑 자체가
  backend#16에서 부정확 이슈로 열려있다는 점 인지하고 있을 것)
"""

import io
import os

import numpy as np
import pandas as pd
import psycopg

DRY_RUN = os.environ.get("DRY_RUN", "1") == "1"

handoff = pd.read_csv("output_full/backtest/demand_class_mu_corrected_handoff.csv")
real_ids = pd.read_csv("institution_ids_sorted.csv")["institution_id"].tolist()

# --- 기관코드 매핑 (backend import_ssis_dataset.py:222 와 동일 방식) ---
anon_codes = sorted(handoff["anon_institution_code"].dropna().unique())
real_ids_sorted = sorted(real_ids)

print(f"우리 데이터 고유 기관코드 수: {len(anon_codes)}")
print(f"실제 institution_id 수: {len(real_ids_sorted)}")
if len(anon_codes) != len(real_ids_sorted):
    print("\n*** 경고: 길이 불일치. backend#16(기관 매핑 부정확 이슈)과 관련 가능성. "
          "진행 전 반드시 확인. ***")

mapping = dict(zip(anon_codes, real_ids_sorted))
handoff["institution_id"] = handoff["anon_institution_code"].map(mapping)
before = len(handoff)
handoff = handoff.dropna(subset=["institution_id"])
print(f"매핑 실패로 제외된 행: {before - len(handoff)}건")

update_df = handoff[["institution_id", "standard_code", "demand_class", "mu_corrected"]]

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    cur = conn.cursor()

    # 사전 확인: 신규 컬럼이 실제로 존재하는지
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name='inventory' AND column_name IN ('demand_class', 'mu_corrected')
    """)
    existing_cols = {r[0] for r in cur.fetchall()}
    missing = {"demand_class", "mu_corrected"} - existing_cols
    if missing:
        print(f"\n*** 중단: inventory 테이블에 컬럼이 없습니다: {missing} ***")
        print("*** backend에게 ALTER TABLE 먼저 요청하세요. ***")
        raise SystemExit(1)

    cur.execute("DROP TABLE IF EXISTS _demand_class_update")
    cur.execute("""
        CREATE TEMP TABLE _demand_class_update (
            institution_id TEXT, standard_code TEXT,
            demand_class TEXT, mu_corrected DOUBLE PRECISION
        ) ON COMMIT DROP
    """)
    buf = io.StringIO()
    update_df.to_csv(buf, index=False, header=False)
    buf.seek(0)
    with cur.copy("COPY _demand_class_update FROM STDIN WITH (FORMAT CSV)") as cp:
        cp.write(buf.read())

    cur.execute("""
        SELECT count(*) FROM inventory i
        JOIN _demand_class_update u ON i.institution_id = u.institution_id
                                     AND i.standard_code = u.standard_code
    """)
    matched = cur.fetchone()[0]
    print(f"\ninventory 테이블과 매칭되는 행: {matched:,}건")

    if DRY_RUN:
        cur.execute("""
            SELECT i.institution_id, i.standard_code, i.demand_class AS old_class,
                   u.demand_class AS new_class, i.mu AS old_mu, u.mu_corrected AS new_mu
            FROM inventory i
            JOIN _demand_class_update u ON i.institution_id = u.institution_id
                                         AND i.standard_code = u.standard_code
            WHERE u.demand_class = 'DORMANT'
            LIMIT 10
        """)
        print("\n=== DRY RUN: DORMANT 분류 샘플 (미리보기, 아직 반영 안 됨) ===")
        for row in cur.fetchall():
            print(row)
        print("\n*** DRY_RUN=1 이라 반영되지 않았습니다. 확인 후 DRY_RUN=0 으로 재실행하세요. ***")
        conn.rollback()
    else:
        cur.execute("""
            UPDATE inventory i SET
                demand_class = u.demand_class,
                mu_corrected = u.mu_corrected,
                updated_at = now()
            FROM _demand_class_update u
            WHERE i.institution_id = u.institution_id AND i.standard_code = u.standard_code
        """)
        print(f"demand_class/mu_corrected 갱신: {cur.rowcount:,}행")

        # DORMANT는 status를 'DORMANT'로 덮어씀. 나머지는 기존 on_hand vs rop 규칙 그대로 둠(건드리지 않음)
        cur.execute("""
            UPDATE inventory SET status = 'DORMANT', updated_at = now()
            WHERE demand_class = 'DORMANT'
        """)
        print(f"DORMANT 상태 반영: {cur.rowcount:,}행")

        conn.commit()
        print("\n*** 커밋 완료. ***")
