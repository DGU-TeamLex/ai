"""HS 매핑이 없는 원자재에 대한 후보안을 만든다 (ai#20).

## 왜 필요한가

관세청 신호는 원자재 메타코드 → HS 세번 매핑을 타고 품목에 붙는다. 그런데

    HS 매핑 보유    28종
    원장 사용      692종
    HS 있음   16종   194,727건 (30.7%)
    HS 없음  676종   440,079건 (69.3%)

**69.3% 의 품목에 관세청 신호가 아예 닿지 않는다.** `trade_risk` 가 19.2%
행에서만 0 보다 큰 이유다. 24개월 검정에서 유일하게 재현된 축이 관세청인데
(lead_p75, lag1 r=+0.499 p=0.0153) 커버리지가 3분의 1이다.

## 무엇을 하고 무엇을 하지 않나

**한다**: 공식 HSK 참조(관세청 HS부호 2026-01-01, 11,327 세번)에서 품명을
대조해 후보를 뽑고, 근거와 함께 `review_status=pending` 으로 낸다.

**하지 않는다**: 승인하지 않는다. 기존 매핑은 전부 `approved` 이고
`approval_basis` 에 사람이 확인한 근거가 붙어 있다. 자동 생성물을 approved 로
넣으면 그 계약이 깨진다. 관세청 신호가 재고정책에 직접 들어가므로 잘못된
매핑은 조용히 틀린 발주로 이어진다.

## 후보 선정 기준

품명에 재료명이 직접 나오는 세번만 넣는다. 검색어가 다의어라 오탐이 섞이는
재료(예: `면` 은 면양·칠면조에도 걸린다)는 세번 범위를 직접 지정한다.

`relation_type` 은 기존 규약을 따른다.
    direct_active_ingredient   해당 물질 그 자체
    upstream_material_input    그 물질로 만드는 상위 원료

실행:
    python scripts/analysis/propose_material_hs_mapping.py
"""
import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

HSK_PATH = ROOT / "data" / "processed" / "trade" / "hsk_reference_2026.parquet"
CURRENT_PATH = ROOT / "data" / "mapping" / "material_hs_mapping.csv"
OUT_PATH = ROOT / "outputs" / "material_hs_mapping_candidates.csv"
MAPPING_VERSION = "material-hs-candidate-v1-pending-review"

# 세번 접두사로 범위를 지정한다. 품명 검색만으로는 오탐이 섞이는 재료가 있어
# HS 부(chapter)·호(heading) 수준을 명시하는 편이 안전하다.
#
# proxy_quality 는 "이 세번의 가격·물량이 그 재료를 얼마나 대표하는가" 다.
# 재료 그 자체면 1.0, 상위 원료라 간접적이면 낮춘다. 기존 매핑 규약과 같다.
PROPOSALS = {
    "NATURAL_RUBBER_LATEX": [
        ("4001100000", "direct_active_ingredient", 1.0, "천연고무 라텍스 그 자체"),
        ("4001220000", "upstream_material_input", 0.7, "공업규격 천연고무(TSNR) — 라텍스 이외 형태"),
    ],
    "SYNTHETIC_NITRILE_RUBBER": [
        ("4002110000", "direct_active_ingredient", 1.0, "합성고무 라텍스(4002호 = 합성고무)"),
        ("2926100000", "upstream_material_input", 0.6, "아크릴로니트릴 — 니트릴고무 단량체"),
    ],
    "NONWOVEN_FABRIC_SPUNBOND": [
        ("5603119000", "direct_active_ingredient", 1.0, "부직포(5603호), 인조필라멘트 25g/m2 이하"),
        ("5603129000", "direct_active_ingredient", 1.0, "부직포(5603호), 25~70g/m2"),
        ("5603139000", "direct_active_ingredient", 1.0, "부직포(5603호), 70~150g/m2"),
    ],
    # 소독용 에탄올은 변성 주정을 쓴다. 2207.10 하위는 조주정·주류제조용이라
    # 의료 소독제 원료로 보기 어려워 변성 에틸알코올만 넣는다.
    "ETHANOL": [
        ("2207200000", "direct_active_ingredient", 1.0, "변성 에틸알코올 — 소독용 원료"),
    ],
    "STAINLESS_STEEL": [
        ("7222200000", "direct_active_ingredient", 1.0, "스테인리스강 봉(냉간성형)"),
        ("7223000000", "direct_active_ingredient", 0.9, "스테인리스강 선"),
    ],
    # 5503.20 은 세분되어 있다. 이형단면(5503201000)은 특수 용도라 제외하고
    # 일반품(5503209000)만 쓴다.
    "POLYESTER_FIBER": [
        ("5503209000", "direct_active_ingredient", 1.0, "폴리에스테르 스테이플 섬유(일반)"),
    ],
    # 면은 섬유장별로 세분된다. 의료용 탈지면 원료 규격을 특정할 근거가 없어
    # 가장 흔한 중간 섬유장 두 개를 후보로 낸다. 검토자가 좁혀야 한다.
    "COTTON_FIBER": [
        ("5201009030", "direct_active_ingredient", 0.8, "면 원면 25.4~28.5mm — 규격 확인 필요"),
        ("5201009050", "direct_active_ingredient", 0.8, "면 원면 28.5~34.9mm — 규격 확인 필요"),
    ],
    "PARAFFIN_PETROLEUM": [
        ("2712200000", "direct_active_ingredient", 1.0, "파라핀왁스(기름 0.75% 미만)"),
        ("2712101000", "upstream_material_input", 0.7, "바셀린 — 연고 기제"),
    ],
}


def main() -> None:
    reference = pd.read_parquet(HSK_PATH)
    reference["hs_code"] = reference["hs_code"].astype(str)
    names = dict(zip(reference["hs_code"], reference["item_name_ko"].astype(str)))

    current = pd.read_csv(CURRENT_PATH, encoding="utf-8-sig")
    already = set(
        current["raw_material_meta_code"].astype(str).str.strip()
        + "|"
        + current["hs_code"].astype(str).str.strip()
    )

    rows = []
    unverified = []
    for meta_code, entries in PROPOSALS.items():
        for hs_code, relation, quality, reason in entries:
            official = names.get(hs_code)
            if official is None:
                unverified.append((meta_code, hs_code, reason))
                continue
            if f"{meta_code}|{hs_code}" in already:
                continue
            rows.append(
                {
                    "raw_material_meta_code": meta_code,
                    "hs_code": hs_code,
                    "hs_item_name_ko": official,
                    "relation_type": relation,
                    "mapping_weight": 1.0,
                    "proxy_quality": quality,
                    # 자동 생성물이다. 승인은 사람이 한다.
                    "review_status": "pending",
                    "approval_basis": "hsk_official_name_match_pending_review",
                    "proposal_reason": reason,
                    "evidence_reference": "관세청_HS부호_20260101 (hsk_reference_2026)",
                    "valid_from": "2024-01-01",
                    "valid_to": "2026-12-31",
                    "mapping_version": MAPPING_VERSION,
                }
            )

    result = pd.DataFrame(rows)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    print(f"후보 {len(result)}건 → {OUT_PATH}")
    print(f"대상 원자재 {result['raw_material_meta_code'].nunique()}종\n")
    for meta_code, block in result.groupby("raw_material_meta_code", sort=False):
        print(f"  {meta_code}")
        for row in block.itertuples():
            print(f"    {row.hs_code}  {str(row.hs_item_name_ko)[:40]:<42}"
                  f"proxy {row.proxy_quality}")

    if unverified:
        print(f"\n⚠ 공식 HSK 에서 확인되지 않은 세번 {len(unverified)}건 — 제외했다")
        for meta_code, hs_code, reason in unverified:
            print(f"    {meta_code}  {hs_code}  ({reason})")

    print("\n전부 review_status=pending 이다. 승인 전에는 관세청 신호에 반영되지 않는다.")


if __name__ == "__main__":
    main()
