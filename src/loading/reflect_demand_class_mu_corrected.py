"""
이슈 #25 첫 산출물 적재: demand_class, mu_corrected

[PR #26 리뷰 반영 - 3차]
- status='DORMANT' 덮어쓰는 UPDATE 블록 제거 (리뷰 지시)
  -> demand_class/mu_corrected만 적재하고, status는 건드리지 않음
     (status는 기존 on_hand vs rop 로직이 별도로 관리)
- backend 스키마는 PR #54로 이미 프로덕션 적용 완료 (컬럼 존재 확인은 유지)
- 기관코드 매핑 길이 불일치 시 raise 유지
- institution_ids_sorted.csv 경로(data/mapping/) 유지
"""

import io
import os

import numpy as np
import pandas as pd
import psycopg

DRY_RUN = os.environ.get("DRY_RUN", "1") == "1"

handoff = pd.read_csv("output_full/backtest/demand_class_mu_corrected_handoff.csv")
real_ids = pd.read_csv("data/mapping/institution_ids_sorted.csv")["institution_id"].tolist()

anon_codes = sorted(handoff["anon_institution_code"].dropna().unique())
real_ids_sorted = sorted(real_ids)

print(f"우리 데이터 고유 기관코드 수: {len(anon_codes)}")
print(f"실제 institution_id 수: {len(real_ids_sorted)}")

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
        print("*** backend PR #54가 실제로 적용됐는지 확인하세요. ***")
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
        # [수정] demand_class, mu_corrected만 갱신. status는 건드리지 않음 (리뷰 지시로 블록 제거)
        cur.execute("""
            UPDATE inventory i SET
                demand_class = u.demand_class,
                mu_corrected = u.mu_corrected,
                updated_at = now()
            FROM _demand_class_update u
            WHERE i.institution_id = u.institution_id AND i.standard_code = u.standard_code
        """)
        print(f"demand_class/mu_corrected 갱신: {cur.rowcount:,}행")

        conn.commit()
        print("\n*** 커밋 완료. ***")
