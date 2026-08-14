"""원자재 매핑을 표준품목 축으로 접는다 — 접을 수 있는 코드군만 (ai#62).

## 왜

원자재는 물품의 속성인데 매핑을 `기관::부서::물품` 축에 걸고 있다.

    승인 매핑 634,806행  /  고유 stock_item_key 416,128  /  고유 물품코드 17,155
    → 같은 물품을 24.3배 중복 매핑

주사기가 무엇으로 만들어졌는지는 어느 보건소에 있든 같다.

## 그런데 전부 접을 수는 없다

접두사별로 재면 코드군마다 성질이 다르다.

    접두사   물품수    행수     원자재 충돌   품명 충돌   표준키 충돌
    USE     6,728  360,728     90.2%      87.4%      87.4%
    W       5,653  127,710     31.9%      18.5%      16.7%
    WMD     1,529   23,072     39.0%      22.5%      22.0%
    WA      2,130  103,437     59.2%      29.2%      23.2%

`USE` 는 기관이 자체 부여한 지역코드다. 코드값 자체에 전역적 의미가 없다.

    USE0000037  "삼남로페라마이드"  →  ACETAMINOPHEN, ALUMINUM, AMLODIPINE ...

로페라마이드에 암로디핀이 붙을 리 없다. 표준품목 축으로 바꿔도 87.4% 가 여러
표준키로 갈리므로 **어떤 축으로도 접을 수 없다.**

## 그래서 부분 적용한다

충돌률이 낮은 `W`·`WMD` 만 표준품목 축으로 접고 나머지는 현행을 유지한다.
표준품목 축을 쓰는 이유는 물품 축보다 충돌이 더 낮기 때문이다(W 16.7% vs 18.5%).

접을 때 충돌이 남는 물품은 **접지 않고 남긴다.** 임의로 하나를 고르면 조용히
틀린 매핑이 된다. 남은 것은 사람이 봐야 한다.

실행:
    python scripts/analysis/material_mapping_standard_axis.py
"""
import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.config import STOCK_MATERIAL_MAPPING_PATH  # noqa: E402

STANDARD_MAP_PATH = ROOT / "data" / "processed" / "stock_standard_item_mapping.parquet"
OUT_PATH = ROOT / "outputs" / "material_mapping_standard_axis.csv"
CONFLICT_PATH = ROOT / "outputs" / "material_mapping_standard_axis_conflicts.csv"
# 접을 코드군. 충돌률이 낮아 표준품목 축이 성립하는 것만.
FOLDABLE_PREFIXES = ("W", "WMD")
MATERIAL_COLUMNS = [
    "raw_material_meta_code",
    "raw_material_risk_meta_code",
    "demand_risk_meta_code",
]


def main() -> None:
    mapping = pd.read_parquet(
        STOCK_MATERIAL_MAPPING_PATH,
        columns=["stock_item_key", "item_name", "mapping_weight",
                 "exposure_score", "mapping_confidence", *MATERIAL_COLUMNS],
    )
    standard = pd.read_parquet(
        STANDARD_MAP_PATH, columns=["local_item_key", "standard_item_key"]
    )
    print(f"원자재 매핑 {len(mapping):,}행  표준품목 매핑 {len(standard):,}행")

    mapping["item_code"] = mapping["stock_item_key"].astype(str).str.split("::").str[-1]
    # 접두사는 가장 긴 것부터 맞춘다. WMD 가 W 로 잡히면 안 된다.
    mapping["prefix"] = ""
    for prefix in sorted(FOLDABLE_PREFIXES, key=len, reverse=True):
        unset = mapping["prefix"].eq("")
        matches = mapping["item_code"].str.match(rf"^{prefix}\d")
        mapping.loc[unset & matches, "prefix"] = prefix

    foldable = mapping[mapping["prefix"].ne("")].copy()
    kept = mapping[mapping["prefix"].eq("")]
    print(f"\n접기 대상 {len(foldable):,}행 ({len(foldable)/len(mapping):.1%})"
          f"  현행 유지 {len(kept):,}행")
    for prefix in FOLDABLE_PREFIXES:
        block = foldable[foldable["prefix"].eq(prefix)]
        print(f"  {prefix:<5}{len(block):>9,}행  물품 {block['item_code'].nunique():,}종")

    standard["item_code"] = standard["local_item_key"].astype(str).str.split("::").str[-1]
    lookup = standard.drop_duplicates(["item_code", "standard_item_key"])
    folded = foldable.merge(
        lookup[["item_code", "standard_item_key"]], on="item_code", how="left"
    )
    unmatched = folded["standard_item_key"].isna()
    print(f"\n표준키 매칭 {(~unmatched).mean():.1%}  미매칭 {int(unmatched.sum()):,}행")
    folded = folded[~unmatched]

    # 표준키마다 원자재가 하나로 모이는지 확인한다. 갈리면 접지 않는다.
    grouped = folded.groupby("standard_item_key")["raw_material_meta_code"]
    unique_counts = grouped.nunique()
    clean_keys = unique_counts[unique_counts.eq(1)].index
    conflict_keys = unique_counts[unique_counts.gt(1)].index
    print(f"\n표준키 {len(unique_counts):,}개")
    print(f"  원자재가 하나로 모임  {len(clean_keys):,} ({len(clean_keys)/len(unique_counts):.1%})")
    print(f"  갈림(접지 않음)      {len(conflict_keys):,} ({len(conflict_keys)/len(unique_counts):.1%})")

    clean = folded[folded["standard_item_key"].isin(clean_keys)]
    result = (
        clean.groupby("standard_item_key", as_index=False)
        .agg(
            item_name=("item_name", "first"),
            raw_material_meta_code=("raw_material_meta_code", "first"),
            raw_material_risk_meta_code=("raw_material_risk_meta_code", "first"),
            demand_risk_meta_code=("demand_risk_meta_code", "first"),
            mapping_weight=("mapping_weight", "median"),
            exposure_score=("exposure_score", "median"),
            source_rows=("stock_item_key", "size"),
        )
    )
    result["review_status"] = "pending"
    result["mapping_version"] = "material-standard-axis-v1-pending-review"
    result.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    conflicts = (
        folded[folded["standard_item_key"].isin(conflict_keys)]
        .groupby("standard_item_key")
        .agg(
            item_names=("item_name", lambda s: " | ".join(sorted(set(s.astype(str)))[:3])),
            materials=("raw_material_meta_code",
                       lambda s: " | ".join(sorted(set(s.astype(str)))[:5])),
            material_count=("raw_material_meta_code", "nunique"),
            rows=("stock_item_key", "size"),
        )
        .sort_values("rows", ascending=False)
        .reset_index()
    )
    conflicts.to_csv(CONFLICT_PATH, index=False, encoding="utf-8-sig")

    print(f"\n축소 효과")
    print(f"  접기 전 {len(foldable):,}행  →  접은 뒤 {len(result):,}종"
          f"  ({len(foldable)/max(len(result),1):.1f}배)")
    print(f"  전체 매핑 {len(mapping):,}행  →  {len(kept):,} + {len(result):,}"
          f" = {len(kept)+len(result):,}행")

    print(f"\n갈리는 표준키 상위 5 (사람이 봐야 한다)")
    for row in conflicts.head(5).itertuples():
        print(f"  {str(row.item_names)[:34]:<36}원자재 {row.material_count}종  {row.rows:,}행")

    print(f"\n저장: {OUT_PATH}  ({len(result):,}종, pending)")
    print(f"      {CONFLICT_PATH}  ({len(conflicts):,}종, 검토 필요)")


if __name__ == "__main__":
    main()
