from pathlib import Path

import pandas as pd

from .config import STOCK_MATERIAL_MAPPING_PATH


REQUIRED_MAPPING_COLUMNS = [
    "stock_item_key",
    "item_name",
    "item_type",
    "relation_type",
    "usage_part",
    "related_material",
    "mapping_weight",
    "mapping_confidence",
    "exposure_score",
    "evidence_reference",
    "review_status",
    "reviewer",
    "reviewed_at",
    "mapping_version",
    "source",
]

MAPPING_METADATA_COLUMNS = [
    "approved_material_mapping_count",
    "has_approved_material_mapping",
    "approved_related_materials",
    "approved_raw_material_meta_codes",
    "approved_raw_material_risk_meta_codes",
    "approved_demand_risk_meta_codes",
    "approved_material_mapping_versions",
]


def _empty_mapping() -> pd.DataFrame:
    return pd.DataFrame(columns=REQUIRED_MAPPING_COLUMNS)


def load_approved_stock_material_mapping(
    path: Path = STOCK_MATERIAL_MAPPING_PATH,
    operational_only: bool = True,
    eligibility_column: str = "operational_eligible",
) -> pd.DataFrame:
    if not path.exists():
        return _empty_mapping()
    dtype = {
            "stock_item_key": str,
            "item_name": str,
            "item_type": str,
            "relation_type": str,
            "usage_part": str,
            "related_material": str,
            "mapping_confidence": str,
            "evidence_reference": str,
            "review_status": str,
            "reviewer": str,
            "reviewed_at": str,
            "mapping_version": str,
            "source": str,
        }
    if path.suffix.lower() in {".parquet", ".pq"}:
        mapping = pd.read_parquet(path)
    else:
        mapping = pd.read_csv(path, dtype=dtype, keep_default_na=False)
    missing = [column for column in REQUIRED_MAPPING_COLUMNS if column not in mapping.columns]
    if missing:
        raise ValueError(f"Stock item material mapping is missing columns: {missing}")
    approved = mapping[mapping["review_status"].str.strip().str.lower().eq("approved")].copy()
    if operational_only and eligibility_column in approved.columns:
        operational = (
            approved[eligibility_column]
            .astype("string")
            .fillna("")
            .str.strip()
            .str.lower()
            .isin({"true", "t", "1", "yes", "y"})
        )
        approved = approved.loc[operational].copy()
    if approved.empty:
        return mapping.iloc[0:0].copy()

    text_columns = [
        "stock_item_key",
        "item_name",
        "item_type",
        "relation_type",
        "usage_part",
        "related_material",
        "mapping_confidence",
        "evidence_reference",
        "reviewer",
        "reviewed_at",
        "mapping_version",
        "source",
    ]
    for column in text_columns:
        approved[column] = approved[column].astype("string").fillna("").str.strip()
    blank = [column for column in text_columns if approved[column].eq("").any()]
    if blank:
        raise ValueError(f"Approved stock item material mapping has blank values in: {blank}")
    allowed_relation_types = {
        "active_ingredient",
        "excipient",
        "direct_component",
        "primary_packaging",
        "secondary_packaging",
        "upstream_material",
        "compatible_consumable",
    }
    invalid_relation = ~approved["relation_type"].isin(allowed_relation_types)
    if invalid_relation.any():
        values = sorted(approved.loc[invalid_relation, "relation_type"].unique().tolist())
        raise ValueError(f"Approved material mapping has invalid relation_type values: {values}")
    invalid_reviewed_at = pd.to_datetime(approved["reviewed_at"], errors="coerce", utc=True).isna()
    if invalid_reviewed_at.any():
        raise ValueError("Approved stock item material mapping has invalid reviewed_at values")

    # Source institution codes can themselves contain the historical ``::`` delimiter.
    invalid_key = approved["stock_item_key"].str.count("::").lt(2)
    if invalid_key.any():
        examples = approved.loc[invalid_key, "stock_item_key"].head(5).tolist()
        raise ValueError(f"Approved material mapping has invalid stock_item_key values: {examples}")
    duplicate = approved.duplicated(["stock_item_key", "related_material"], keep=False)
    if duplicate.any():
        examples = approved.loc[
            duplicate,
            ["stock_item_key", "related_material"],
        ].head(5)
        raise ValueError(
            "Approved material mapping must be unique per stock item and material: "
            f"{examples.to_dict(orient='records')}"
        )

    approved["mapping_weight"] = pd.to_numeric(approved["mapping_weight"], errors="coerce")
    approved["exposure_score"] = pd.to_numeric(approved["exposure_score"], errors="coerce")
    invalid_weight = approved["mapping_weight"].isna() | ~approved["mapping_weight"].between(
        0, 1, inclusive="right"
    )
    if invalid_weight.any():
        raise ValueError("Approved mapping_weight must be greater than 0 and at most 1")
    invalid_exposure = approved["exposure_score"].isna() | ~approved[
        "exposure_score"
    ].between(0, 1, inclusive="both")
    if invalid_exposure.any():
        raise ValueError("Approved exposure_score must be between 0 and 1")
    return approved.reset_index(drop=True)


def _join_unique(values: pd.Series) -> str:
    cleaned = {
        str(value).strip()
        for value in values
        if pd.notna(value) and str(value).strip()
    }
    return ";".join(sorted(cleaned))


def attach_approved_material_mapping_metadata(
    df: pd.DataFrame,
    path: Path = STOCK_MATERIAL_MAPPING_PATH,
) -> pd.DataFrame:
    if "stock_item_key" not in df.columns:
        raise ValueError("stock_item_key is required to attach material mapping metadata")

    result = df.drop(
        columns=[column for column in MAPPING_METADATA_COLUMNS if column in df.columns]
    ).copy()
    approved = load_approved_stock_material_mapping(path)
    if approved.empty:
        result["approved_material_mapping_count"] = 0
        result["has_approved_material_mapping"] = False
        for column in MAPPING_METADATA_COLUMNS[2:]:
            result[column] = ""
        return result

    optional_columns = [
        "raw_material_meta_code",
        "raw_material_risk_meta_code",
        "demand_risk_meta_code",
    ]
    for column in optional_columns:
        if column not in approved.columns:
            approved[column] = ""

    metadata = (
        approved.groupby("stock_item_key", as_index=False, observed=True)
        .agg(
            approved_material_mapping_count=("related_material", "size"),
            approved_related_materials=("related_material", _join_unique),
            approved_raw_material_meta_codes=("raw_material_meta_code", _join_unique),
            approved_raw_material_risk_meta_codes=(
                "raw_material_risk_meta_code",
                _join_unique,
            ),
            approved_demand_risk_meta_codes=("demand_risk_meta_code", _join_unique),
            approved_material_mapping_versions=("mapping_version", _join_unique),
        )
    )
    metadata["has_approved_material_mapping"] = True
    result = result.merge(metadata, on="stock_item_key", how="left", validate="many_to_one")
    result["approved_material_mapping_count"] = (
        result["approved_material_mapping_count"].fillna(0).astype("int64")
    )
    result["has_approved_material_mapping"] = result[
        "has_approved_material_mapping"
    ].eq(True)
    for column in MAPPING_METADATA_COLUMNS[2:]:
        result[column] = result[column].fillna("")
    return result
