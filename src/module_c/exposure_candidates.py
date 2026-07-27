from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..commodity.commodity_risk_scorer import load_material_market_factor_mapping
from ..config import (
    APPROVED_ITEM_CLASSIFICATION_PATH,
    ITEM_ALIAS_TO_PRODUCT_PATH,
    ITEM_INTEGRATED_CLASSIFICATION_PARQUET_PATH,
    OFFICIAL_DEVICE_MATERIAL_CLAIMS_PATH,
)
from .supply_risk_policy import derive_supply_risk_frame


CANDIDATE_COLUMNS = [
    "local_item_key",
    "institution_code",
    "item_code",
    "representative_item_id",
    "representative_name",
    "item_family_id",
    "item_subtype_id",
    "normalized_specification",
    "unit_code",
    "raw_material_meta_code",
    "raw_material_risk_meta_code",
    "demand_risk_meta_code",
    "raw_material_suggested",
    "material_confidence",
    "material_evidence_tier",
    "material_evidence_reference",
    "material_review_status",
    "official_material_claim_approved",
    "official_material_evidence_reference",
    "classification_material_family_conflict",
    "market_factor_ids",
    "market_factor_count",
    "baseline_supply_risk_level",
    "baseline_supply_risk_z",
    "baseline_lead_time_multiplier",
    "supply_risk_level_source",
    "canonical_supply_risk_meta_codes",
    "ignored_event_or_demand_codes",
    "unmapped_supply_risk_codes",
    "supply_risk_policy_needs_review",
    "supply_risk_policy_version",
    "supply_risk_policy_status",
    "operational_risk_eligible",
    "review_priority",
    "usage_sum",
    "occurrence_count",
    "candidate_version",
]


def _empty_candidates() -> pd.DataFrame:
    return pd.DataFrame(columns=CANDIDATE_COLUMNS)


def _read_approved_classification(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    classification = pd.read_csv(path, dtype=str, keep_default_na=False)
    required = {
        "local_item_key",
        "institution_code",
        "item_code",
        "item_family_id",
        "item_subtype_id",
        "normalized_specification",
        "unit_code",
        "review_status",
    }
    missing = required - set(classification.columns)
    if missing:
        raise ValueError(f"Approved classification is missing columns: {sorted(missing)}")
    return classification[
        classification["review_status"].str.strip().str.lower().eq("approved")
    ].copy()


def _read_alias_map(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    columns = ["local_item_key", "representative_item_id"]
    alias = pd.read_parquet(path, columns=columns).dropna(subset=columns)
    alias[columns] = alias[columns].astype(str)
    counts = alias.groupby("local_item_key")["representative_item_id"].nunique()
    unambiguous = counts[counts.eq(1)].index
    return (
        alias[alias["local_item_key"].isin(unambiguous)]
        .drop_duplicates("local_item_key")
        .reset_index(drop=True)
    )


def _read_integrated_items(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    columns = [
        "representative_item_id",
        "representative_name",
        "classification_selected_item_family_id",
        "classification_selected_item_subtype_id",
        "raw_material_meta_code",
        "raw_material_risk_meta_code",
        "demand_risk_meta_code",
        "raw_material_suggested",
        "raw_material_evidence",
        "material_confidence",
        "material_evidence_tier",
        "material_review_status",
        "usage_sum",
        "occurrence_count",
    ]
    return pd.read_parquet(path, columns=columns)


def _read_official_material_claims(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    claims = pd.read_csv(path, dtype=str, keep_default_na=False)
    required = {
        "representative_item_id",
        "raw_material_meta_code",
        "evidence_source",
        "evidence_field",
        "evidence_url",
        "identity_review_status",
        "material_review_status",
    }
    missing = required - set(claims.columns)
    if missing:
        raise ValueError(f"Official material claims are missing columns: {sorted(missing)}")
    approved = claims[
        claims["identity_review_status"].str.strip().str.lower().eq("approved")
        & claims["material_review_status"].str.strip().str.lower().eq("approved")
    ].copy()
    duplicate = approved.duplicated(
        ["representative_item_id", "raw_material_meta_code"], keep=False
    )
    if duplicate.any():
        raise ValueError("Official material claims contain duplicate product-material rows")
    return approved


def _join_unique(values: pd.Series) -> str:
    return ";".join(sorted({str(value).strip() for value in values if str(value).strip()}))


def build_module_c_exposure_candidates(
    classification: pd.DataFrame | None = None,
    alias_map: pd.DataFrame | None = None,
    integrated_items: pd.DataFrame | None = None,
    market_mapping: pd.DataFrame | None = None,
    official_material_claims: pd.DataFrame | None = None,
    classification_path: Path = APPROVED_ITEM_CLASSIFICATION_PATH,
    alias_path: Path = ITEM_ALIAS_TO_PRODUCT_PATH,
    integrated_path: Path = ITEM_INTEGRATED_CLASSIFICATION_PARQUET_PATH,
    official_material_claims_path: Path = OFFICIAL_DEVICE_MATERIAL_CLAIMS_PATH,
) -> tuple[pd.DataFrame, dict]:
    classification = (
        _read_approved_classification(classification_path)
        if classification is None
        else classification.copy()
    )
    alias_map = _read_alias_map(alias_path) if alias_map is None else alias_map.copy()
    integrated_items = (
        _read_integrated_items(integrated_path)
        if integrated_items is None
        else integrated_items.copy()
    )
    market_mapping = (
        load_material_market_factor_mapping()
        if market_mapping is None
        else market_mapping.copy()
    )
    official_material_claims = (
        _read_official_material_claims(official_material_claims_path)
        if official_material_claims is None
        else official_material_claims.copy()
    )
    for column in ["raw_material_risk_meta_code", "demand_risk_meta_code"]:
        if column not in integrated_items.columns:
            integrated_items[column] = ""
    if classification.empty or alias_map.empty or integrated_items.empty:
        return _empty_candidates(), {
            "approved_classification_count": int(len(classification)),
            "alias_mapping_count": int(len(alias_map)),
            "integrated_item_count": int(len(integrated_items)),
            "candidate_count": 0,
            "operational_risk_eligible_count": 0,
            "blocked_reason": "required_classification_artifact_missing_or_empty",
        }

    merged = classification.merge(
        alias_map[["local_item_key", "representative_item_id"]],
        on="local_item_key",
        how="left",
        validate="one_to_one",
    )
    merged = merged.merge(
        integrated_items,
        on="representative_item_id",
        how="left",
        validate="many_to_one",
    )
    policy_code_column = (
        "supply_risk_meta_code"
        if "supply_risk_meta_code" in merged.columns
        else "raw_material_risk_meta_code"
    )
    merged = derive_supply_risk_frame(
        merged,
        code_column=policy_code_column,
        existing_level_column=(
            "supply_risk_level" if "supply_risk_level" in merged.columns else None
        ),
    )
    merged["classification_material_family_conflict"] = (
        merged["classification_selected_item_family_id"].fillna("").astype(str).str.strip().ne("")
        & merged["classification_selected_item_family_id"].astype(str).ne(
            merged["item_family_id"].astype(str)
        )
    )
    merged["raw_material_meta_code"] = (
        merged["raw_material_meta_code"].fillna("").astype(str).str.split(";")
    )
    merged = merged.explode("raw_material_meta_code")
    merged["raw_material_meta_code"] = merged["raw_material_meta_code"].str.strip()
    merged = merged[merged["raw_material_meta_code"].ne("")].copy()
    if merged.empty:
        return _empty_candidates(), {
            "approved_classification_count": int(len(classification)),
            "alias_mapping_count": int(len(alias_map)),
            "integrated_item_count": int(len(integrated_items)),
            "candidate_count": 0,
            "operational_risk_eligible_count": 0,
            "blocked_reason": "no_material_candidates_for_approved_classifications",
        }

    if official_material_claims.empty:
        merged["official_material_claim_approved"] = False
        merged["official_material_evidence_reference"] = ""
    else:
        required_claim_columns = {
            "representative_item_id",
            "raw_material_meta_code",
            "evidence_source",
            "evidence_field",
            "evidence_url",
            "identity_review_status",
            "material_review_status",
        }
        missing_claim_columns = required_claim_columns - set(official_material_claims.columns)
        if missing_claim_columns:
            raise ValueError(
                "Official material claims are missing columns: "
                f"{sorted(missing_claim_columns)}"
            )
        claims = official_material_claims.copy()
        approved_claim = (
            claims["identity_review_status"].astype(str).str.strip().str.lower().eq("approved")
            & claims["material_review_status"]
            .astype(str)
            .str.strip()
            .str.lower()
            .eq("approved")
        )
        claims = claims[approved_claim].copy()
        claims["official_material_evidence_reference"] = claims.apply(
            lambda row: " | ".join(
                value
                for value in [
                    str(row.get("evidence_source", "")).strip(),
                    str(row.get("evidence_field", "")).strip(),
                    str(row.get("evidence_url", "")).strip(),
                ]
                if value
            ),
            axis=1,
        )
        claims["official_material_claim_approved"] = True
        claims = claims[
            [
                "representative_item_id",
                "raw_material_meta_code",
                "official_material_claim_approved",
                "official_material_evidence_reference",
            ]
        ]
        if claims.duplicated(
            ["representative_item_id", "raw_material_meta_code"], keep=False
        ).any():
            raise ValueError("Official material claims contain duplicate product-material rows")
        merged = merged.merge(
            claims,
            on=["representative_item_id", "raw_material_meta_code"],
            how="left",
            validate="many_to_one",
        )
        merged["official_material_claim_approved"] = merged[
            "official_material_claim_approved"
        ].fillna(False)
        merged["official_material_evidence_reference"] = merged[
            "official_material_evidence_reference"
        ].fillna("")
        official_approved = merged["official_material_claim_approved"]
        merged.loc[official_approved, "material_review_status"] = "approved"
        merged.loc[official_approved, "material_confidence"] = "verified"
        merged.loc[official_approved, "material_evidence_tier"] = (
            "official_product_material"
        )
        merged.loc[official_approved, "raw_material_evidence"] = merged.loc[
            official_approved, "official_material_evidence_reference"
        ]

    factor_summary = (
        market_mapping.groupby("raw_material_meta_code", as_index=False, observed=True)
        .agg(
            market_factor_ids=("market_factor_id", _join_unique),
            market_factor_count=("market_factor_id", "nunique"),
        )
    )
    merged = merged.merge(
        factor_summary,
        on="raw_material_meta_code",
        how="left",
        validate="many_to_one",
    )
    merged["market_factor_ids"] = merged["market_factor_ids"].fillna("")
    merged["market_factor_count"] = merged["market_factor_count"].fillna(0).astype(int)
    material_approved = (
        merged["material_review_status"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
        .eq("approved")
    )
    merged["operational_risk_eligible"] = (
        material_approved
        & ~merged["classification_material_family_conflict"]
        & merged["market_factor_count"].gt(0)
    )
    merged["review_priority"] = "normal"
    merged.loc[merged["market_factor_count"].gt(0), "review_priority"] = "high"
    merged.loc[
        merged["classification_material_family_conflict"], "review_priority"
    ] = "blocked_family_conflict"
    merged.loc[
        merged["supply_risk_policy_needs_review"].fillna(True),
        "review_priority",
    ] = "blocked_supply_policy_review"
    merged["material_evidence_reference"] = merged["raw_material_evidence"].fillna("")
    merged["candidate_version"] = "module-c-exposure-candidate-v1"
    merged = merged.rename(
        columns={
            "item_family_id": "item_family_id",
            "item_subtype_id": "item_subtype_id",
        }
    )
    for column in CANDIDATE_COLUMNS:
        if column not in merged.columns:
            merged[column] = ""
    candidates = (
        merged[CANDIDATE_COLUMNS]
        .drop_duplicates(["local_item_key", "raw_material_meta_code"])
        .sort_values(
            ["operational_risk_eligible", "review_priority", "usage_sum"],
            ascending=[False, True, False],
        )
        .reset_index(drop=True)
    )
    report = {
        "approved_classification_count": int(len(classification)),
        "classification_with_representative_count": int(
            merged.loc[
                merged["representative_item_id"].notna(), "local_item_key"
            ].nunique()
        ),
        "candidate_count": int(len(candidates)),
        "official_material_claim_approved_count": int(
            candidates["official_material_claim_approved"].fillna(False).sum()
        ),
        "candidate_local_item_count": int(candidates["local_item_key"].nunique()),
        "market_factor_covered_count": int(candidates["market_factor_count"].gt(0).sum()),
        "material_review_approved_count": int(material_approved.sum()),
        "family_conflict_count": int(
            candidates["classification_material_family_conflict"].sum()
        ),
        "supply_policy_review_count": int(
            candidates["supply_risk_policy_needs_review"].sum()
        ),
        "supply_policy_review_local_item_count": int(
            candidates.loc[
                candidates["supply_risk_policy_needs_review"],
                "local_item_key",
            ].nunique()
        ),
        "supply_policy_unmapped_count": int(
            candidates["unmapped_supply_risk_codes"].astype(str).str.strip().ne("").sum()
        ),
        "supply_policy_unmapped_local_item_count": int(
            candidates.loc[
                candidates["unmapped_supply_risk_codes"]
                .astype(str)
                .str.strip()
                .ne(""),
                "local_item_key",
            ].nunique()
        ),
        "operational_risk_eligible_count": int(
            candidates["operational_risk_eligible"].sum()
        ),
        "blocked_reason": (
            None
            if candidates["operational_risk_eligible"].any()
            else "material_candidates_require_explicit_review_before_inventory_adjustment"
        ),
    }
    return candidates, report
