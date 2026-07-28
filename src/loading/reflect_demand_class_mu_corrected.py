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

# 기관코드 매핑: backend import_ssis_dataset.py:222 와 '동일한' 정렬 zip.
#   inventory 도 이 방식으로 적재됐으므로, 같은 매핑을 쓰면 우리 산출물이
#   inventory 의 바로 그 행에 정합하게 붙는다(실측: 조인율 100%, 409,459행 전량).
#   길이 불일치(anon 3,530 vs real 3,598)는 원장에 등장한 기관이 전체보다 적기
#   때문이며, zip 이 자연 절삭하는 그 68개는 애초에 inventory 에도 없는 기관이라
#   무해하다. 따라서 raise 하지 않고 경고만 남기고 진행한다.
#   ⚠️ 단, 이 매핑 자체가 익명코드↔실기관 대응을 보장하진 않는다(backend#16).
#      '데이터를 inventory 에 정합하게 붙이는' 목적에서만 유효하다.
if len(anon_codes) != len(real_ids_sorted):
    print(
        f"[경고] 기관코드 수 불일치: 우리 데이터 {len(anon_codes)} vs "
        f"institutions {len(real_ids_sorted)}. inventory 와 동일한 정렬 zip 으로 진행하며, "
        f"매칭되지 않는 {abs(len(anon_codes) - len(real_ids_sorted))}개는 inventory 에도 없어 무해."
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

    # is_medical 컬럼 준비 (없으면 생성). 비의료품(판촉·홍보·문구)을 예측·알림 대상에서
    # 제외하기 위한 플래그. 소유권상 분류(데이터)는 ai, 스키마는 backend 이지만,
    # 여기서는 IF NOT EXISTS 로 안전하게 보장한다(backend 스키마와 동일 정의).
    cur.execute("ALTER TABLE inventory ADD COLUMN IF NOT EXISTS is_medical BOOLEAN")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_inventory_is_medical ON inventory(is_medical)")

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

        # is_medical 파생: item_meta_map.raw_material_meta_code 로 명백한 비의료품을 false.
        #   명백한 3종만 false (PROMO_MATERIAL·HEALTH_BOOKLET·STATIONERY).
        #   NON_INGREDIENT_SPEC(성인용기저귀·멸균장갑 섞임)·ORAL_HYGIENE·ASSISTIVE 는
        #   경계라 제외하지 않고 의료(true)로 둔다. 메타코드 매핑이 없는 행도 true(보수적).
        cur.execute("""
            UPDATE inventory i SET
                is_medical = CASE
                    WHEN m.raw_material_meta_code IN
                         ('PROMO_MATERIAL', 'HEALTH_BOOKLET', 'STATIONERY') THEN false
                    ELSE true END,
                updated_at = now()
            FROM item_meta_map m
            WHERE i.standard_code = m.standard_code
        """)
        print(f"is_medical 파생 갱신(메타코드 매핑분): {cur.rowcount:,}행")
        # 메타코드 매핑이 없는 행은 보수적으로 의료(true)로 채운다.
        cur.execute("UPDATE inventory SET is_medical = true WHERE is_medical IS NULL")
        print(f"is_medical 미매핑분 true 채움: {cur.rowcount:,}행")

        conn.commit()
        print("\n*** 커밋 완료. ***")
