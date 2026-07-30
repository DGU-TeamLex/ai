"""Recompute ``inventory.is_medical`` with ingredient evidence precedence.

An exact ``standard_code = drug_ingredient_master.drug_code`` match is stronger
than name heuristics and always protects the item as medical. Items absent from
the drug file keep the existing material-tier/name rules because consumables do
not have pharmaceutical ingredient records.
"""
from __future__ import annotations

import os
import re

DRY_RUN = os.environ.get("DRY_RUN", "1") == "1"

HARD = (
    "PROMO_MATERIAL",
    "HEALTH_BOOKLET",
    "STATIONERY",
    "ORAL_CARE_CONFECTION",
    "MEDICATION_ENVELOPE",
)
GUARD = (
    "CARRIER_BAG",
    "MATERNITY_CAMPAIGN_ITEM",
    "HANDKERCHIEF_TOWEL",
)
MED = (
    r"시약|검사|선별|스트립|시험지|검사지|채혈|검체|장갑|마스크|기저귀|팬티|"
    r"요실금|거즈|멸균|소독|주사|앰플|바이알|시럽|연고|점안|좌제|백신|수액|"
    r"드레싱|카테터|란셋|밴드|붕대|파스|폐기물|의료|엑스|탕|단미|연조|캡슐|"
    r"정\(|알콜|알코올"
)
PROMO = (
    r"가방|에코백|크린백|종이가방|쇼핑백|손잡이|텀블러|배지|뱃지|엠블럼|앰블럼|"
    r"자석|스티커|리플렛|리플릿|팜플렛|소책자|수첩|손수건|수건|타올|색연필|"
    r"색칠|크레파스|연필|볼펜|풍선|핫팩|부채|우산|캔디|사탕|은단|껌|매트|"
    r"요가|기념품|홍보|판촉|사은품|자동차표지|표지|쿠폰|교통카드|물티슈|"
    r"방향제|다이어리|메모지|캐릭터|인형|엽서|약봉투"
)

CASE_SQL = """CASE
    WHEN d.drug_code IS NOT NULL THEN true
    WHEN m.raw_material_meta_code = ANY(%(hard)s) THEN false
    WHEN m.raw_material_meta_code = ANY(%(guard)s)
         AND s.standard_name !~ %(medical_pattern)s THEN false
    WHEN (
            m.raw_material_meta_code = 'NON_INGREDIENT_SPEC'
            OR m.raw_material_meta_code IS NULL
            OR m.raw_material_meta_code = 'MATERIAL_UNSPECIFIED'
         )
         AND s.standard_name ~ %(promo_pattern)s
         AND s.standard_name !~ %(medical_pattern)s THEN false
    ELSE true
END"""


def classify_is_medical(
    *,
    raw_material_meta_code: str | None,
    standard_name: str,
    has_drug_ingredient_evidence: bool,
) -> bool:
    """Python mirror of the SQL precedence, used for review and regression tests."""
    if has_drug_ingredient_evidence:
        return True
    name = standard_name or ""
    if raw_material_meta_code in HARD:
        return False
    if (
        raw_material_meta_code in GUARD
        and not re.search(MED, name, flags=re.IGNORECASE)
    ):
        return False
    if (
        raw_material_meta_code
        in {None, "NON_INGREDIENT_SPEC", "MATERIAL_UNSPECIFIED"}
        and re.search(PROMO, name, flags=re.IGNORECASE)
        and not re.search(MED, name, flags=re.IGNORECASE)
    ):
        return False
    return True


def main() -> None:
    import psycopg

    params = {
        "hard": list(HARD),
        "guard": list(GUARD),
        "medical_pattern": MED,
        "promo_pattern": PROMO,
    }
    joins = """
        FROM inventory i
        JOIN standard_items s USING (standard_code)
        LEFT JOIN item_meta_map m USING (standard_code)
        LEFT JOIN drug_ingredient_master d
          ON d.drug_code = s.standard_code
    """
    with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
        cursor = connection.cursor()
        cursor.execute(
            f"""
            SELECT
              count(*) FILTER (WHERE d.drug_code IS NOT NULL) AS ingredient,
              count(*) FILTER (
                WHERE d.drug_code IS NULL
                  AND m.raw_material_meta_code = ANY(%(hard)s)
              ) AS tier1,
              count(*) FILTER (
                WHERE d.drug_code IS NULL
                  AND m.raw_material_meta_code = ANY(%(guard)s)
                  AND s.standard_name !~ %(medical_pattern)s
              ) AS tier2,
              count(*) FILTER (
                WHERE d.drug_code IS NULL
                  AND (
                    m.raw_material_meta_code = 'NON_INGREDIENT_SPEC'
                    OR m.raw_material_meta_code IS NULL
                    OR m.raw_material_meta_code = 'MATERIAL_UNSPECIFIED'
                  )
                  AND s.standard_name ~ %(promo_pattern)s
                  AND s.standard_name !~ %(medical_pattern)s
              ) AS tier3
            {joins}
            """,
            params,
        )
        ingredient, tier1, tier2, tier3 = cursor.fetchone()
        print(
            f"약성분 직접근거 {ingredient:,} / 비의료 TIER1 {tier1:,} · "
            f"TIER2 {tier2:,} · TIER3 {tier3:,}"
        )

        if DRY_RUN:
            cursor.execute(
                f"""
                SELECT count(*)
                {joins}
                WHERE i.status = 'CRITICAL'
                  AND {CASE_SQL} = false
                """,
                params,
            )
            print(f"[DRY] CRITICAL 중 비의료 제외 대상: {cursor.fetchone()[0]:,}")
            connection.rollback()
            print("*** DRY_RUN=1 - 미반영 ***")
            return

        cursor.execute(
            f"""
            UPDATE inventory i
            SET is_medical = {CASE_SQL},
                updated_at = now()
            FROM standard_items s
            LEFT JOIN item_meta_map m
              ON m.standard_code = s.standard_code
            LEFT JOIN drug_ingredient_master d
              ON d.drug_code = s.standard_code
            WHERE i.standard_code = s.standard_code
            """,
            params,
        )
        print(f"is_medical 재산정: {cursor.rowcount:,}행")
        connection.commit()
        print("반영 완료")


if __name__ == "__main__":
    main()
