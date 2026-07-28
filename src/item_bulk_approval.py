from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pandas as pd

from .config import (
    APPROVED_ITEM_CLASSIFICATION_SEED_PATH,
    ITEM_ALIAS_CANDIDATE_PATH,
    ITEM_BULK_APPROVAL_MARKER_PATH,
    ITEM_BULK_APPROVAL_POLICY_PATH,
    ITEM_BULK_APPROVAL_REPORT_PATH,
    ITEM_BULK_CLASSIFICATION_PATH,
    ITEM_BULK_CLASSIFICATION_SAMPLE_PATH,
    ITEM_BULK_MATERIAL_MAPPING_PATH,
    ITEM_BULK_MATERIAL_SAMPLE_PATH,
    ITEM_BULK_TAXONOMY_PATH,
    ITEM_FAMILY_TAXONOMY_SEED_PATH,
    MONTHLY_STOCK_PATH,
)
from .item_classification import (
    LOCAL_OUTPUT_PATH,
    TAXONOMY_COLUMNS,
    UNIT_NAMES,
)
from .item_normalization import FORECASTABLE_BY_GROUP
from .material_mapping import (
    load_approved_stock_material_mapping,
)
from .modeling.classified_prediction import (
    TAXONOMY_REFERENCE_COLUMNS,
    VALID_ITEM_GROUP_IDS,
    load_approved_classifications,
)
from .module_c.exposure_candidates import build_module_c_exposure_candidates
from .utils import ensure_dirs, write_json


BULK_CLASSIFICATION_VERSION = "classification-bulk-v1.0"
BULK_MATERIAL_VERSION = "item-material-bulk-v1.0"
CLASSIFICATION_OUTPUT_COLUMNS = [
    "institution_code",
    "item_code",
    "local_item_key",
    "item_family_id",
    "item_subtype_id",
    "normalized_specification",
    "unit_code",
    "taxonomy_version",
    "review_status",
    "reviewer",
    "reviewed_at",
    "evidence_reference",
    "classification_version",
    "item_group_id",
    "standard_family_name",
    "standard_subtype_name",
    "approval_basis",
    "evidence_status",
    "operational_eligible",
    "automatic_order_eligible",
    "source_representative_statuses",
]
MATERIAL_EXTRA_COLUMNS = [
    "approval_basis",
    "evidence_status",
    "identity_approved",
    "operational_eligible",
    "market_signal_eligible",
    "news_signal_eligible",
    "source_material_evidence_tier",
    "source_material_review_status",
    "source_supply_policy_needs_review",
]
MAPPING_OUTPUT_COLUMNS = [
    "stock_item_key",
    "item_name",
    "item_type",
    "relation_type",
    "usage_part",
    "related_material",
    "raw_material_meta_code",
    "raw_material_risk_meta_code",
    "demand_risk_meta_code",
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
BLOCKED_MATERIAL_CODES = {
    "",
    "MATERIAL_UNSPECIFIED",
    "MIXED_MATERIAL_NOT_SINGLE",
    "NOT_APPLICABLE",
}


def _text(series: pd.Series) -> pd.Series:
    return series.astype("string").fillna("").str.strip()


def _as_bool(series: pd.Series) -> pd.Series:
    return _text(series).str.lower().isin({"true", "t", "1", "yes", "y"})


def _stable_token(value: object) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16].upper()


def load_bulk_approval_policy(
    path: Path = ITEM_BULK_APPROVAL_POLICY_PATH,
) -> dict:
    policy = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "version",
        "status",
        "reviewer",
        "classification",
        "material",
        "safety",
    }
    missing = required - set(policy)
    if missing:
        raise ValueError(f"Bulk approval policy is missing keys: {sorted(missing)}")
    if policy["status"] != "user_requested_bulk_candidate_acceptance":
        raise ValueError("Bulk approval policy has an unsupported status")
    if not policy["classification"].get("approve_all_local_item_candidates"):
        raise ValueError("Bulk classification approval must be explicit")
    if not policy["material"].get("approve_all_generated_candidates"):
        raise ValueError("Bulk material approval must be explicit")
    if not policy["safety"].get(
        "approval_means_candidate_acceptance_not_external_fact_verification"
    ):
        raise ValueError("Bulk approval semantics must remain explicit")
    return policy


def _load_strict_classifications() -> pd.DataFrame:
    loaded = load_approved_classifications(
        APPROVED_ITEM_CLASSIFICATION_SEED_PATH,
        ITEM_FAMILY_TAXONOMY_SEED_PATH,
    )
    strict = loaded.mappings.copy()
    if strict.empty:
        return strict
    strict_columns = [
        "local_item_key",
        "item_family_id",
        "item_subtype_id",
        "normalized_specification",
        "unit_code",
        "taxonomy_version",
        "reviewer",
        "reviewed_at",
        "evidence_reference",
        "classification_version",
        "item_group_id",
        "standard_family_name",
        "standard_subtype_name",
        "unit_name",
        "is_forecastable",
    ]
    return strict[strict_columns].drop_duplicates("local_item_key")


def build_bulk_classifications(
    local_candidates: pd.DataFrame,
    aliases: pd.DataFrame,
    strict_classifications: pd.DataFrame,
    policy: dict,
    reviewed_at: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    required = {
        "local_item_key",
        "institution_code",
        "item_code",
        "item_group_id",
        "item_family_id",
        "standard_family_name",
        "item_subtype_id",
        "standard_subtype_name",
        "normalized_specification",
        "unit_code",
        "representative_statuses",
    }
    missing = required - set(local_candidates)
    if missing:
        raise ValueError(f"Local classification candidates are missing: {sorted(missing)}")
    alias_required = {"local_item_key", "raw_item_name"}
    missing_alias = alias_required - set(aliases)
    if missing_alias:
        raise ValueError(f"Alias candidates are missing: {sorted(missing_alias)}")

    local = local_candidates.copy()
    if local["local_item_key"].duplicated().any():
        raise ValueError("Local classification candidates contain duplicate keys")
    alias_names = (
        aliases[["local_item_key", "raw_item_name"]]
        .drop_duplicates("local_item_key")
        .copy()
    )
    local = local.merge(
        alias_names,
        on="local_item_key",
        how="left",
        validate="one_to_one",
    )

    strict = strict_classifications.copy()
    strict_columns = [column for column in strict.columns if column != "local_item_key"]
    strict = strict.rename(columns={column: f"strict_{column}" for column in strict_columns})
    local = local.merge(
        strict,
        on="local_item_key",
        how="left",
        validate="one_to_one",
    )
    strict_mask = _text(local.get("strict_item_family_id", pd.Series("", index=local.index))).ne("")

    group = _text(local["item_group_id"])
    valid_candidate_group = group.isin(VALID_ITEM_GROUP_IDS)
    group = group.where(valid_candidate_group, "UNCLASSIFIED")
    strict_group = _text(
        local.get("strict_item_group_id", pd.Series("", index=local.index))
    )
    group = group.where(~strict_mask, strict_group)

    candidate_fields = [
        "item_family_id",
        "standard_family_name",
        "item_subtype_id",
        "standard_subtype_name",
        "normalized_specification",
        "unit_code",
    ]
    candidate_complete = valid_candidate_group.copy()
    for column in candidate_fields:
        candidate_complete &= _text(local[column]).ne("")
    conflict = _text(local["representative_statuses"]).str.contains(
        "conflict", case=False, regex=False
    )
    candidate_complete &= ~conflict

    tokens = local["local_item_key"].map(_stable_token)
    raw_name = _text(local["raw_item_name"])
    raw_name = raw_name.where(raw_name.ne(""), "원본명 미확인")

    output = pd.DataFrame(
        {
            "institution_code": _text(local["institution_code"]),
            "item_code": _text(local["item_code"]),
            "local_item_key": _text(local["local_item_key"]),
            "item_group_id": group,
            "item_family_id": _text(local["item_family_id"]),
            "standard_family_name": _text(local["standard_family_name"]),
            "item_subtype_id": _text(local["item_subtype_id"]),
            "standard_subtype_name": _text(local["standard_subtype_name"]),
            "normalized_specification": _text(local["normalized_specification"]),
            "unit_code": _text(local["unit_code"]),
            "source_representative_statuses": _text(
                local["representative_statuses"]
            ),
        }
    )

    incomplete = ~strict_mask & ~candidate_complete
    output.loc[incomplete, "item_family_id"] = (
        "BULK_PENDING_ITEM_" + tokens.loc[incomplete]
    )
    output.loc[incomplete, "standard_family_name"] = (
        "검증대기: " + raw_name.loc[incomplete]
    )
    output.loc[incomplete, "item_subtype_id"] = (
        "BULK_PENDING_SUBTYPE_" + tokens.loc[incomplete]
    )
    output.loc[incomplete, "standard_subtype_name"] = "세부유형 검증대기"
    output.loc[
        incomplete & output["normalized_specification"].eq(""),
        "normalized_specification",
    ] = "UNSPECIFIED"
    output.loc[incomplete & output["unit_code"].eq(""), "unit_code"] = "UNSPECIFIED"

    strict_field_map = {
        "item_family_id": "strict_item_family_id",
        "standard_family_name": "strict_standard_family_name",
        "item_subtype_id": "strict_item_subtype_id",
        "standard_subtype_name": "strict_standard_subtype_name",
        "normalized_specification": "strict_normalized_specification",
        "unit_code": "strict_unit_code",
    }
    for output_column, strict_column in strict_field_map.items():
        output.loc[strict_mask, output_column] = _text(local[strict_column]).loc[
            strict_mask
        ]

    candidate_taxonomy_version = (
        "bulk-v1.0-" + output["item_group_id"].str.lower()
    )
    output["taxonomy_version"] = candidate_taxonomy_version
    output.loc[strict_mask, "taxonomy_version"] = _text(
        local["strict_taxonomy_version"]
    ).loc[strict_mask]
    output["review_status"] = "approved"
    output["reviewer"] = policy["reviewer"]
    output["reviewed_at"] = reviewed_at
    output["evidence_reference"] = (
        "USER_BULK_APPROVAL::"
        + policy["version"]
        + "::"
        + output["local_item_key"]
    )
    output.loc[strict_mask, "reviewer"] = _text(local["strict_reviewer"]).loc[
        strict_mask
    ]
    output.loc[strict_mask, "reviewed_at"] = _text(
        local["strict_reviewed_at"]
    ).loc[strict_mask]
    output.loc[strict_mask, "evidence_reference"] = _text(
        local["strict_evidence_reference"]
    ).loc[strict_mask]
    output["classification_version"] = BULK_CLASSIFICATION_VERSION
    output.loc[strict_mask, "classification_version"] = _text(
        local["strict_classification_version"]
    ).loc[strict_mask]
    output["approval_basis"] = "user_bulk_pending_fields_acceptance"
    output.loc[candidate_complete & ~strict_mask, "approval_basis"] = (
        "user_bulk_complete_candidate_acceptance"
    )
    output.loc[strict_mask, "approval_basis"] = "existing_evidence_approval"
    output["evidence_status"] = "pending_missing_taxonomy_fields"
    output.loc[candidate_complete & ~strict_mask, "evidence_status"] = (
        "candidate_complete_not_external_fact_verified"
    )
    output.loc[strict_mask, "evidence_status"] = "existing_verified_evidence"
    forecastable_group = output["item_group_id"].map(FORECASTABLE_BY_GROUP).eq("t")
    output["operational_eligible"] = (
        strict_mask
        | (
            candidate_complete
            & forecastable_group
            & policy["classification"]["activate_complete_candidates_for_forecast"]
        )
    )
    output["automatic_order_eligible"] = strict_mask

    blank_required = [
        column
        for column in [
            "institution_code",
            "item_code",
            "local_item_key",
            "item_family_id",
            "item_subtype_id",
            "normalized_specification",
            "unit_code",
            "taxonomy_version",
            "reviewer",
            "reviewed_at",
            "evidence_reference",
        ]
        if _text(output[column]).eq("").any()
    ]
    if blank_required:
        raise ValueError(f"Bulk approved classifications have blanks: {blank_required}")
    if output["local_item_key"].duplicated().any():
        raise ValueError("Bulk approved classifications contain duplicate keys")

    taxonomy_source = output.copy()
    taxonomy_source["is_forecastable"] = taxonomy_source[
        "operational_eligible"
    ].map({True: "t", False: "f"})
    taxonomy_source["unit_name"] = taxonomy_source["unit_code"].map(UNIT_NAMES)
    taxonomy_source["unit_name"] = taxonomy_source["unit_name"].fillna(
        "단위 검증대기"
    )
    taxonomy_source["source_item_name"] = taxonomy_source["standard_family_name"]
    taxonomy_source["source_subtype_name"] = taxonomy_source[
        "standard_subtype_name"
    ]
    taxonomy_source["source_specification"] = taxonomy_source[
        "normalized_specification"
    ]
    taxonomy_source["material_candidate"] = ""
    taxonomy_source["material_mapping_status"] = (
        "bulk_candidate_approval_separate_operational_gate"
    )
    taxonomy_source["review_status"] = "approved"

    duplicate_group_count = (
        taxonomy_source.groupby(TAXONOMY_REFERENCE_COLUMNS, observed=True)[
            "item_group_id"
        ]
        .nunique()
        .gt(1)
        .sum()
    )
    if duplicate_group_count:
        raise ValueError("Bulk taxonomy references resolve to multiple item groups")
    taxonomy = (
        taxonomy_source.sort_values(
            ["operational_eligible", "local_item_key"],
            ascending=[False, True],
            kind="stable",
        )
        .drop_duplicates(TAXONOMY_REFERENCE_COLUMNS)
        .copy()
    )
    taxonomy = taxonomy[TAXONOMY_COLUMNS].reset_index(drop=True)

    output = output[CLASSIFICATION_OUTPUT_COLUMNS].reset_index(drop=True)
    report = {
        "input_local_item_count": int(len(local)),
        "approved_local_item_count": int(len(output)),
        "existing_evidence_approved_count": int(strict_mask.sum()),
        "bulk_complete_candidate_approved_count": int(
            (candidate_complete & ~strict_mask).sum()
        ),
        "bulk_pending_fields_approved_count": int(incomplete.sum()),
        "forecast_operational_eligible_count": int(
            output["operational_eligible"].sum()
        ),
        "automatic_order_eligible_count": int(
            output["automatic_order_eligible"].sum()
        ),
        "taxonomy_rows": int(len(taxonomy)),
        "approval_coverage": float(len(output) / len(local)) if len(local) else 0.0,
    }
    return output, taxonomy, report


def _material_weight(tier: object) -> float:
    return {
        "government_drug_ingredient_dataset": 0.95,
        "official_product_material": 0.90,
        "verified_external_dictionary": 0.85,
        "family_as_ingredient_candidate": 0.65,
        "subtype_rule_candidate": 0.60,
        "family_rule_candidate": 0.50,
        "cluster_default": 0.35,
        "group_fallback": 0.15,
        "composite_set_requires_bom": 0.10,
        "unmapped": 0.05,
        "sentinel_blocked": 0.05,
    }.get(str(tier).strip(), 0.10)


def build_bulk_material_mapping(
    candidates: pd.DataFrame,
    monthly_stock: pd.DataFrame,
    policy: dict,
    reviewed_at: str,
) -> tuple[pd.DataFrame, dict]:
    required = {
        "institution_code",
        "item_code",
        "representative_item_id",
        "representative_name",
        "item_family_id",
        "raw_material_meta_code",
        "raw_material_risk_meta_code",
        "demand_risk_meta_code",
        "material_confidence",
        "material_evidence_tier",
        "material_evidence_reference",
        "material_review_status",
        "classification_material_family_conflict",
        "market_factor_count",
        "supply_risk_policy_needs_review",
    }
    missing = required - set(candidates)
    if missing:
        raise ValueError(f"Material candidates are missing: {sorted(missing)}")
    stock_required = {
        "year_month",
        "institution_code",
        "department",
        "item_code",
        "stock_item_key",
    }
    missing_stock = stock_required - set(monthly_stock)
    if missing_stock:
        raise ValueError(f"Monthly stock is missing: {sorted(missing_stock)}")

    stock = monthly_stock[list(stock_required)].copy()
    stock["year_month"] = pd.to_datetime(stock["year_month"], errors="coerce")
    stock = (
        stock.sort_values("year_month")
        .drop_duplicates(["institution_code", "department", "item_code"], keep="last")
        .drop(columns="year_month")
    )
    for column in ["institution_code", "item_code"]:
        stock[column] = _text(stock[column])

    material = candidates.copy()
    for column in ["institution_code", "item_code"]:
        material[column] = _text(material[column])
    expanded = material.merge(
        stock,
        on=["institution_code", "item_code"],
        how="left",
        validate="many_to_many",
        indicator=True,
    )
    unmatched = expanded["_merge"].ne("both")
    if unmatched.any():
        examples = expanded.loc[
            unmatched, ["institution_code", "item_code"]
        ].head(5)
        raise ValueError(
            "Bulk material candidates could not resolve stock keys: "
            f"{examples.to_dict(orient='records')}"
        )

    code = _text(expanded["raw_material_meta_code"])
    evidence = _text(expanded["material_evidence_reference"])
    confidence = _text(expanded["material_confidence"]).str.lower()
    market_covered = pd.to_numeric(
        expanded["market_factor_count"], errors="coerce"
    ).fillna(0).gt(0)
    family_conflict = _as_bool(expanded["classification_material_family_conflict"])
    supply_review = _as_bool(expanded["supply_risk_policy_needs_review"])
    identified = confidence.isin({"identified", "verified", "high"})
    material_code_usable = ~code.isin(BLOCKED_MATERIAL_CODES)
    material_policy = policy.get("material", {})
    identity_approved = identified & material_code_usable
    if material_policy.get("operational_use_requires_evidence", True):
        identity_approved &= evidence.ne("")
    operational = identity_approved.copy()
    if material_policy.get("operational_use_requires_market_factor", True):
        operational &= market_covered
    if material_policy.get("operational_use_blocks_family_conflict", True):
        operational &= ~family_conflict
    if material_policy.get("operational_use_blocks_supply_policy_review", True):
        operational &= ~supply_review
    news_signal_eligible = (
        identity_approved
        & (
            _text(expanded["raw_material_risk_meta_code"]).ne("")
            | _text(expanded["demand_risk_meta_code"]).ne("")
        )
    )

    tiers = _text(expanded["material_evidence_tier"])
    weights = tiers.map(_material_weight).astype(float)
    confidence_label = "low"
    government_ingredient = tiers.eq("government_drug_ingredient_dataset")
    relation_types = pd.Series(
        "upstream_material",
        index=expanded.index,
        dtype="string",
    )
    relation_types.loc[government_ingredient] = "active_ingredient"
    usage_parts = pd.Series(
        "candidate_supply_exposure",
        index=expanded.index,
        dtype="string",
    )
    usage_parts.loc[government_ingredient] = "active_ingredient"
    sources = pd.Series(
        "user_bulk_candidate_approval_not_fact_verification",
        index=expanded.index,
        dtype="string",
    )
    sources.loc[government_ingredient] = (
        "government_drug_ingredient_dataset_user_approved"
    )
    mapping = pd.DataFrame(
        {
            "stock_item_key": _text(expanded["stock_item_key"]),
            "item_name": _text(expanded["representative_name"]),
            "item_type": _text(expanded["item_family_id"]).where(
                _text(expanded["item_family_id"]).ne(""),
                "UNCLASSIFIED_ITEM",
            ),
            "relation_type": relation_types,
            "usage_part": usage_parts,
            "related_material": code,
            "raw_material_meta_code": code,
            "raw_material_risk_meta_code": _text(
                expanded["raw_material_risk_meta_code"]
            ),
            "demand_risk_meta_code": _text(expanded["demand_risk_meta_code"]),
            "mapping_weight": weights,
            "mapping_confidence": confidence_label,
            "exposure_score": weights,
            "evidence_reference": evidence,
            "review_status": "approved",
            "reviewer": policy["reviewer"],
            "reviewed_at": reviewed_at,
            "mapping_version": BULK_MATERIAL_VERSION,
            "source": sources,
            "approval_basis": "user_bulk_material_candidate_acceptance",
            "evidence_status": "candidate_not_external_fact_verified",
            "identity_approved": identity_approved,
            "operational_eligible": operational,
            "market_signal_eligible": identity_approved & market_covered,
            "news_signal_eligible": news_signal_eligible,
            "source_material_evidence_tier": tiers,
            "source_material_review_status": _text(
                expanded["material_review_status"]
            ),
            "source_supply_policy_needs_review": supply_review,
        }
    )
    mapping.loc[identified & evidence.ne(""), "mapping_confidence"] = "medium"
    mapping.loc[
        tiers.isin({"official_product_material", "verified_external_dictionary"}),
        "mapping_confidence",
    ] = "high"
    mapping.loc[government_ingredient, "mapping_confidence"] = "high"
    mapping.loc[government_ingredient, "approval_basis"] = (
        "user_requested_government_drug_ingredient_identity_approval"
    )
    mapping.loc[government_ingredient, "evidence_status"] = (
        "government_dataset_identity_approved"
    )
    mapping.loc[evidence.eq(""), "evidence_reference"] = (
        "USER_BULK_APPROVAL::"
        + policy["version"]
        + "::"
        + _text(expanded["representative_item_id"])
        + "::"
        + code
    )
    mapping.loc[evidence.eq(""), "evidence_status"] = "candidate_evidence_missing"
    mapping.loc[code.isin(BLOCKED_MATERIAL_CODES), "evidence_status"] = (
        "material_unresolved_or_mixed"
    )
    mapping = (
        mapping.drop_duplicates(["stock_item_key", "related_material"])
        .sort_values(["stock_item_key", "related_material"], kind="stable")
        .reset_index(drop=True)
    )
    mapping = mapping[[*MAPPING_OUTPUT_COLUMNS, *MATERIAL_EXTRA_COLUMNS]]
    report = {
        "input_candidate_rows": int(len(candidates)),
        "input_candidate_local_item_count": int(
            candidates["local_item_key"].nunique()
        ),
        "approved_candidate_rows": int(len(candidates)),
        "approved_stock_mapping_rows": int(len(mapping)),
        "approved_stock_item_count": int(mapping["stock_item_key"].nunique()),
        "operational_eligible_mapping_rows": int(
            mapping["operational_eligible"].sum()
        ),
        "identity_approved_mapping_rows": int(mapping["identity_approved"].sum()),
        "market_signal_eligible_mapping_rows": int(
            mapping["market_signal_eligible"].sum()
        ),
        "news_signal_eligible_mapping_rows": int(
            mapping["news_signal_eligible"].sum()
        ),
        "operational_eligible_stock_item_count": int(
            mapping.loc[mapping["operational_eligible"], "stock_item_key"].nunique()
        ),
        "evidence_status_counts": {
            str(key): int(value)
            for key, value in mapping["evidence_status"].value_counts().items()
        },
    }
    return mapping, report


def _select_sample(
    frame: pd.DataFrame,
    strata: list[str],
    key: str,
    sample_size: int,
) -> pd.DataFrame:
    if len(frame) <= sample_size:
        return frame.copy()
    sampled = frame.copy()
    sampled["_sample_hash"] = sampled[key].map(_stable_token)
    sampled = sampled.sort_values("_sample_hash", kind="stable")
    first = sampled.groupby(strata, dropna=False, sort=False).head(1)
    selected = first
    if len(selected) < sample_size:
        remainder = sampled.loc[~sampled.index.isin(selected.index)]
        selected = pd.concat([selected, remainder.head(sample_size - len(selected))])
    return selected.head(sample_size).drop(columns="_sample_hash")


def run_bulk_approval(
    apply: bool = False,
    sample_size: int = 1000,
    reviewed_at: str | None = None,
) -> dict:
    policy = load_bulk_approval_policy()
    reviewed_at = reviewed_at or datetime.now(timezone.utc).isoformat()
    if pd.isna(pd.to_datetime(reviewed_at, errors="coerce", utc=True)):
        raise ValueError("reviewed_at must be an ISO-8601 timestamp")

    local = pd.read_parquet(LOCAL_OUTPUT_PATH)
    aliases = pd.read_parquet(
        ITEM_ALIAS_CANDIDATE_PATH,
        columns=["local_item_key", "raw_item_name"],
    )
    strict = _load_strict_classifications()
    classifications, taxonomy, classification_report = (
        build_bulk_classifications(
            local,
            aliases,
            strict,
            policy,
            reviewed_at,
        )
    )

    candidates, exposure_report = build_module_c_exposure_candidates(
        classification=classifications,
    )
    monthly_columns = [
        "year_month",
        "institution_code",
        "department",
        "item_code",
        "stock_item_key",
    ]
    monthly = pd.read_parquet(MONTHLY_STOCK_PATH, columns=monthly_columns)
    material_mapping, material_report = build_bulk_material_mapping(
        candidates,
        monthly,
        policy,
        reviewed_at,
    )

    classification_sample = _select_sample(
        classifications,
        ["approval_basis", "evidence_status", "item_group_id"],
        "local_item_key",
        sample_size,
    )
    material_sample = _select_sample(
        material_mapping,
        ["evidence_status", "operational_eligible"],
        "stock_item_key",
        sample_size,
    )
    ensure_dirs(
        ITEM_BULK_CLASSIFICATION_PATH.parent,
        ITEM_BULK_APPROVAL_REPORT_PATH.parent,
        ITEM_BULK_CLASSIFICATION_SAMPLE_PATH.parent,
    )
    classifications.to_parquet(
        ITEM_BULK_CLASSIFICATION_PATH,
        index=False,
        compression="zstd",
    )
    taxonomy.to_parquet(
        ITEM_BULK_TAXONOMY_PATH,
        index=False,
        compression="zstd",
    )
    material_mapping.to_parquet(
        ITEM_BULK_MATERIAL_MAPPING_PATH,
        index=False,
        compression="zstd",
    )
    classification_sample.to_csv(
        ITEM_BULK_CLASSIFICATION_SAMPLE_PATH,
        index=False,
        encoding="utf-8-sig",
    )
    material_sample.to_csv(
        ITEM_BULK_MATERIAL_SAMPLE_PATH,
        index=False,
        encoding="utf-8-sig",
    )

    loaded_classification = load_approved_classifications(
        ITEM_BULK_CLASSIFICATION_PATH,
        ITEM_BULK_TAXONOMY_PATH,
    )
    loaded_material_all = load_approved_stock_material_mapping(
        ITEM_BULK_MATERIAL_MAPPING_PATH,
        operational_only=False,
    )
    loaded_material_operational = load_approved_stock_material_mapping(
        ITEM_BULK_MATERIAL_MAPPING_PATH,
        operational_only=True,
    )
    quality_gates = {
        "all_local_candidates_approved": (
            len(classifications) == len(local)
            and classifications["review_status"].eq("approved").all()
        ),
        "classification_keys_unique": not classifications[
            "local_item_key"
        ].duplicated().any(),
        "all_taxonomy_references_valid": (
            loaded_classification.approved_rows == len(classifications)
        ),
        "all_material_candidates_approved": (
            material_mapping["review_status"].eq("approved").all()
        ),
        "all_material_rows_validate": (
            len(loaded_material_all) == len(material_mapping)
        ),
        "operational_material_gate_preserved": (
            len(loaded_material_operational)
            == int(material_mapping["operational_eligible"].sum())
        ),
        "automatic_order_not_bulk_enabled": (
            classifications.loc[
                classifications["approval_basis"].ne("existing_evidence_approval"),
                "automatic_order_eligible",
            ].eq(False).all()
        ),
    }
    quality_gates = {
        key: bool(value)
        for key, value in quality_gates.items()
    }
    if not all(quality_gates.values()):
        raise ValueError(f"Bulk approval quality gate failed: {quality_gates}")

    report = {
        "generated_at": reviewed_at,
        "policy_version": policy["version"],
        "policy_status": policy["status"],
        "approval_semantics": (
            "All rows are accepted for workflow review status; external fact "
            "verification and operational eligibility remain separate."
        ),
        "applied": bool(apply),
        "classification": classification_report,
        "material": material_report,
        "exposure_candidate_generation": exposure_report,
        "quality_gates": quality_gates,
        "active_paths": {
            "classification": str(ITEM_BULK_CLASSIFICATION_PATH),
            "taxonomy": str(ITEM_BULK_TAXONOMY_PATH),
            "material_mapping": str(ITEM_BULK_MATERIAL_MAPPING_PATH),
        },
        "samples": {
            "classification": str(ITEM_BULK_CLASSIFICATION_SAMPLE_PATH),
            "material": str(ITEM_BULK_MATERIAL_SAMPLE_PATH),
        },
        "warnings": [
            "Bulk approval is candidate acceptance, not proof that inferred facts are correct.",
            "Incomplete taxonomy rows remain non-forecastable.",
            "Only operationally eligible material rows are consumed by risk scorers.",
            "Automatic ordering remains limited to prior evidence-approved classifications.",
        ],
    }
    write_json(report, ITEM_BULK_APPROVAL_REPORT_PATH)

    if apply:
        marker = {
            "status": "active",
            "policy_version": policy["version"],
            "activated_at": reviewed_at,
            "classification_path": str(ITEM_BULK_CLASSIFICATION_PATH),
            "taxonomy_path": str(ITEM_BULK_TAXONOMY_PATH),
            "material_mapping_path": str(ITEM_BULK_MATERIAL_MAPPING_PATH),
        }
        write_json(marker, ITEM_BULK_APPROVAL_MARKER_PATH)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Accept every classification and material candidate with audit gates."
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--sample-size", type=int, default=1000)
    parser.add_argument("--reviewed-at")
    args = parser.parse_args()
    report = run_bulk_approval(
        apply=args.apply,
        sample_size=args.sample_size,
        reviewed_at=args.reviewed_at,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
