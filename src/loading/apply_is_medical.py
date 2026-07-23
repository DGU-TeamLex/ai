"""is_medical 재산정 — 비의료품(판촉·홍보·문구·약봉투 등)을 예측·알림 대상에서 제외.

과제는 '필수 의료물품' 재고예측. 판촉·홍보물은 대상이 아니다. raw_material_meta_code
(대분류)로 판정하되, 메타코드마다 오염도가 달라 3단계로 나눈다(2026-07-23 실측 검증).

[TIER-1 HARD] 오염 0 → 무조건 false:
  PROMO_MATERIAL(텀블러·배지·자석), HEALTH_BOOKLET(리플릿·수첩), STATIONERY(색연필),
  ORAL_CARE_CONFECTION(은단·캔디·껌), MEDICATION_ENVELOPE(약봉투·약포지)

[TIER-2 GUARD] 대체로 비의료지만 의료품 섞임 → 품명이 의료가드 안 걸릴 때만 false:
  CARRIER_BAG(의료용폐기물봉투 보호), MATERNITY_CAMPAIGN_ITEM(임신성당뇨선별검사시약 보호),
  HANDKERCHIEF_TOWEL

[TIER-3 NAME-ONLY] NON_INGREDIENT_SPEC + 코드미상(NULL/MATERIAL_UNSPECIFIED):
  혼합 심함(기저귀·일회용마스크·한약제제엑스·요실금팬티 = 진짜 의료 다수) → blanket 절대금지.
  품명이 판촉패턴(PROMO)에 걸리고 의료가드(MED)엔 안 걸릴 때만 false.

그 외 전부 true. 경계 5종(유산균·뉴케어·틀니세정·가그린·칫솔)은 의료 유지(SSIS 확인 후 별도).
"""
import os

import psycopg

DRY_RUN = os.environ.get("DRY_RUN", "1") == "1"

HARD = ("PROMO_MATERIAL", "HEALTH_BOOKLET", "STATIONERY",
        "ORAL_CARE_CONFECTION", "MEDICATION_ENVELOPE")
GUARD = ("CARRIER_BAG", "MATERNITY_CAMPAIGN_ITEM", "HANDKERCHIEF_TOWEL")

# 의료가드: 하나라도 걸리면 의료로 보호(TIER-2/3에서 false 로 안 내림)
MED = (r'시약|검사|선별|스트립|시험지|검사지|채혈|검체|장갑|마스크|기저귀|팬티|요실금|거즈|'
       r'멸균|소독|주사|앰플|바이알|시럽|연고|점안|좌제|백신|수액|드레싱|카테터|란셋|밴드|'
       r'붕대|파스|폐기물|의료|엑스|탕|단미|연조|캡슐|정\(|알콜|알코올')
# 판촉패턴: TIER-3(NON_INGREDIENT_SPEC/코드미상)에서 이게 걸려야 false 후보
PROMO = (r'가방|에코백|크린백|종이가방|쇼핑백|손잡이|텀블러|배지|뱃지|엠블럼|앰블럼|자석|스티커|'
         r'리플렛|리플릿|팜플렛|소책자|수첩|손수건|수건|타올|색연필|색칠|크레파스|연필|볼펜|풍선|'
         r'핫팩|부채|우산|캔디|사탕|은단|껌|매트|요가|기념품|홍보|판촉|사은품|자동차표지|표지|'
         r'쿠폰|교통카드|물티슈|방향제|다이어리|메모지|캐릭터|인형|엽서|약봉투')

# is_medical 을 전 행에 대해 3단계로 재도출(ELSE true = 클린슬레이트)
CASE = f"""CASE
    WHEN m.raw_material_meta_code = ANY(%(hard)s) THEN false
    WHEN m.raw_material_meta_code = ANY(%(guard)s) AND s.standard_name !~ '{MED}' THEN false
    WHEN (m.raw_material_meta_code = 'NON_INGREDIENT_SPEC'
          OR m.raw_material_meta_code IS NULL
          OR m.raw_material_meta_code = 'MATERIAL_UNSPECIFIED')
         AND s.standard_name ~ '{PROMO}' AND s.standard_name !~ '{MED}' THEN false
    ELSE true END"""

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    cur = conn.cursor()
    params = {"hard": list(HARD), "guard": list(GUARD)}

    # 티어별 내역(검증용)
    cur.execute(f"""
        SELECT
          count(*) FILTER (WHERE m.raw_material_meta_code = ANY(%(hard)s)) AS t1,
          count(*) FILTER (WHERE m.raw_material_meta_code = ANY(%(guard)s)
                                 AND s.standard_name !~ '{MED}') AS t2,
          count(*) FILTER (WHERE (m.raw_material_meta_code='NON_INGREDIENT_SPEC'
                                  OR m.raw_material_meta_code IS NULL
                                  OR m.raw_material_meta_code='MATERIAL_UNSPECIFIED')
                                 AND s.standard_name ~ '{PROMO}' AND s.standard_name !~ '{MED}') AS t3
        FROM inventory i JOIN standard_items s USING(standard_code)
        LEFT JOIN item_meta_map m USING(standard_code)
    """, params)
    t1, t2, t3 = cur.fetchone()
    print(f"[내역] TIER1(hard) {t1:,} / TIER2(guard) {t2:,} / TIER3(name) {t3:,} → 비의료 {t1+t2+t3:,}")

    # GUARD·NON_INGREDIENT_SPEC 에서 의료로 '보호'된 표본(오분류 방지 확인)
    cur.execute(f"""SELECT DISTINCT s.standard_name FROM standard_items s
        JOIN item_meta_map m USING(standard_code)
        WHERE (m.raw_material_meta_code = ANY(%(guard)s)
               OR m.raw_material_meta_code='NON_INGREDIENT_SPEC')
          AND s.standard_name ~ '{MED}' LIMIT 12""", params)
    print("[보호된 의료품 표본]", " · ".join(r[0][:16] for r in cur.fetchall()))

    if DRY_RUN:
        cur.execute(f"""SELECT count(*) FROM inventory i JOIN standard_items s USING(standard_code)
            LEFT JOIN item_meta_map m USING(standard_code)
            WHERE i.status='CRITICAL' AND {CASE} = false""", params)
        print(f"[DRY] CRITICAL 중 비의료로 빠질 행: {cur.fetchone()[0]:,}")
        conn.rollback()
        print("*** DRY_RUN=1 미반영 ***")
    else:
        cur.execute(f"""UPDATE inventory i SET is_medical = {CASE}, updated_at = now()
            FROM standard_items s LEFT JOIN item_meta_map m ON m.standard_code = s.standard_code
            WHERE i.standard_code = s.standard_code""", params)
        print(f"is_medical 재산정: {cur.rowcount:,}행")
        cur.execute("""SELECT count(*) FILTER(WHERE is_medical=false),
                              count(*) FILTER(WHERE status='CRITICAL' AND is_medical=false)
                       FROM inventory""")
        r = cur.fetchone()
        print(f"비의료 총 {r[0]:,} / 그중 CRITICAL {r[1]:,}")
        conn.commit()
        print("*** 커밋 완료 ***")
