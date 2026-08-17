"""원자재 매핑을 승인 대표품목 축으로 접고 재고키로 fanout한다 (ai#62).

원자재는 물품의 속성이지만 기존 매핑은 기관·부서·물품별로 중복되어 있다.
`USE`는 기관이 자체 부여한 로컬 코드이므로 코드 문자열만으로 전역 통합하지 않는다.

현재기간의 승인된 `representative_item_id → institution::item → stock_item_key` 연결을
사용한다. 대표품목에서 원자재가 하나로 합의된 경우만 fanout하고 충돌·미매핑은 별도
파일로 격리한다. 이 연결은 exact 제품 BOM이 아니라 대표품목 수준 원자재 proxy다.

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
        STANDARD_MAP_PATH,
        columns=["data_period", "local_item_key", "representative_item_id"],
    )
    standard = standard[
        standard["data_period"].astype(str).eq("current")
    ].drop(columns="data_period")
    print(f"원자재 매핑 {len(mapping):,}행  표준품목 매핑 {len(standard):,}행")

    key_parts = mapping["stock_item_key"].astype(str).str.split("::")
    mapping["local_item_key"] = (
        key_parts.str[0] + "::" + key_parts.str[-1]
    )
    mapping["item_code"] = mapping["stock_item_key"].astype(str).str.split("::").str[-1]
    if standard["local_item_key"].duplicated().any():
        raise ValueError("Standard mapping is not unique by local_item_key")
    folded = mapping.merge(
        standard[["local_item_key", "representative_item_id"]],
        on="local_item_key",
        how="left",
        validate="many_to_one",
    )
    unmatched = folded["representative_item_id"].isna()
    print(f"\n표준키 매칭 {(~unmatched).mean():.1%}  미매칭 {int(unmatched.sum()):,}행")
    unmatched_rows = folded[unmatched].copy()
    folded = folded[~unmatched].copy()

    # 대표품목마다 원자재가 하나로 모이는지 확인한다. 갈리면 접지 않는다.
    grouped = folded.groupby("representative_item_id")["raw_material_meta_code"]
    unique_counts = grouped.nunique()
    clean_keys = unique_counts[unique_counts.eq(1)].index
    conflict_keys = unique_counts[unique_counts.gt(1)].index
    print(f"\n대표품목 {len(unique_counts):,}개")
    print(f"  원자재가 하나로 모임  {len(clean_keys):,} ({len(clean_keys)/len(unique_counts):.1%})")
    print(f"  갈림(접지 않음)      {len(conflict_keys):,} ({len(conflict_keys)/len(unique_counts):.1%})")

    clean = folded[folded["representative_item_id"].isin(clean_keys)]
    result = (
        clean.groupby("representative_item_id", as_index=False)
        .agg(
            item_name=("item_name", "first"),
            raw_material_meta_code=("raw_material_meta_code", "first"),
            raw_material_risk_meta_code=("raw_material_risk_meta_code", "first"),
            demand_risk_meta_code=("demand_risk_meta_code", "first"),
            mapping_weight=("mapping_weight", "median"),
            exposure_score=("exposure_score", "median"),
            source_rows=("stock_item_key", "size"),
            source_stock_items=("stock_item_key", "nunique"),
            source_local_items=("local_item_key", "nunique"),
        )
    )
    result["relation_type"] = "representative_item_material_proxy"
    result["evidence_scope"] = (
        "inherited_stock_mapping_not_exact_product_bom"
    )
    result["review_status"] = "pending"
    result["mapping_version"] = (
        "material-representative-axis-v2-pending-review"
    )
    result.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    conflicts = (
        folded[folded["representative_item_id"].isin(conflict_keys)]
        .groupby("representative_item_id")
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
    conflicts["conflict_reason"] = "multiple_materials_for_representative_item"
    if not unmatched_rows.empty:
        unmatched_output = (
            unmatched_rows.groupby("local_item_key", as_index=False)
            .agg(
                item_names=(
                    "item_name",
                    lambda values: " | ".join(
                        sorted(set(values.astype(str)))[:3]
                    ),
                ),
                materials=(
                    "raw_material_meta_code",
                    lambda values: " | ".join(
                        sorted(set(values.astype(str)))[:5]
                    ),
                ),
                material_count=("raw_material_meta_code", "nunique"),
                rows=("stock_item_key", "size"),
            )
            .rename(columns={"local_item_key": "representative_item_id"})
        )
        unmatched_output["conflict_reason"] = (
            "missing_representative_item_mapping"
        )
        conflicts = pd.concat(
            [conflicts, unmatched_output],
            ignore_index=True,
        )
    conflicts.to_csv(CONFLICT_PATH, index=False, encoding="utf-8-sig")

    print(f"\n축소 효과")
    print(f"  접기 전 {len(mapping):,}행  →  접은 뒤 {len(result):,}종"
          f"  ({len(mapping)/max(len(result),1):.1f}배)")

    print(f"\n갈리는 표준키 상위 5 (사람이 봐야 한다)")
    for row in conflicts.head(5).itertuples():
        print(f"  {str(row.item_names)[:34]:<36}원자재 {row.material_count}종  {row.rows:,}행")

    print(f"\n저장: {OUT_PATH}  ({len(result):,}종, pending)")
    print(f"      {CONFLICT_PATH}  ({len(conflicts):,}종, 검토 필요)")


if __name__ == "__main__":
    main()
