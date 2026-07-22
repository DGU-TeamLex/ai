"""
이슈 #25 첫 산출물 적재: demand_class, mu_corrected

[PR #26 리뷰 반영 - 2차]
- 기관코드 매핑 길이 불일치 시 경고만 찍고 zip()으로 조용히 자르던 버그 수정
  -> raise로 중단 (68개 어긋난 매핑이 조용히 들어가던 문제)
- institution_ids_sorted.csv 경로를 실제 위치(data/mapping/)로 수정
- backend#16(기관 매핑 부정확 이슈)이 해결되기 전까지는, 매핑 정합성이
  100% 보장되지 않는다는 점을 감안해 DRY_RUN 기본값을 유지할 것을 권장
  (리뷰 코멘트 그대로 반영)
"""

import io
import os

import numpy as np
import pandas as pd
import psycopg

DRY_RUN = os.environ.get("DRY_RUN", "1") == "1"

handoff = pd.read_csv("output_full/backtest/demand_class_mu_corrected_handoff.csv")
# [수정] 실제 저장 위치인 data/mapping/ 으로 경로 수정 (리뷰 지적)
real_ids = pd.read_csv("data/mapping/institution_ids_sorted.csv")["institution_id"].tolist()

# --- 기관코드 매핑 (backend import_ssis_dataset.py:222 와 동일 방식) ---
anon_codes = sorted(handoff["anon_institution_code"].dropna().unique())
real_ids_sorted = sorted(real_ids)

print(f"우리 데이터 고유 기관코드 수: {len(anon_codes)}")
print(f"실제 institution_id 수: {len(real_ids_sorted)}")

# [수정] 경고만 찍고 넘어가던 것을 raise로 변경 (리뷰 지적 - 68개 조용히 잘려나가던 버그)
if len(anon_codes) != len(real_ids_sorted):
    raise ValueError(
        f"기관코드 매핑 길이 불일치: 우리 데이터 {len(anon_codes)}개 vs "
        f"institution_ids_sorted.csv {len(real_ids_sorted)}개. "
        f"zip()으로 그냥 진행하면 짧은 쪽 기준으로 조용히 잘려서 매핑이 어긋납니다. "
        f"backend#16(기관 매핑 부정확) 관련 여부를 먼저 확인하세요."
    )

mapping = dict(zip(anon_codes, real_ids_sorted))
handoff["institution_id"] = handoff["anon_institution_code"].map(mapping)
before = len(handoff)
handoff = handoff.dropna(subset=["institution_id"])
print(f"매핑 실패로 제외된 행: {before - len(handoff)}건")

update_df = handoff[["institution_id", "standard_code", "demand_class", "mu_corrected"]]

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    cur = conn.cursor()

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
        print("\n*** DRY_RUN=1 이라 반영되지 않았습니다. ***")
        print("*** zero_ratio가 아직 월 패널 근사치입니다. censored_demand.parquet")
        print("*** 로 정확한 값을 확보하기 전까지는 DRY_RUN=0 실행을 보류하는 것을 권장합니다. ***")
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

        cur.execute("""
            UPDATE inventory SET status = 'DORMANT', updated_at = now()
            WHERE demand_class = 'DORMANT'
        """)
        print(f"DORMANT 상태 반영: {cur.rowcount:,}행")

        conn.commit()
        print("\n*** 커밋 완료. ***")
