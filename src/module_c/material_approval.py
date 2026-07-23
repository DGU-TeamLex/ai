from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import pandas as pd

from ..config import (
    MATERIAL_APPROVAL_AUDIT_PATH,
    MATERIAL_APPROVAL_POLICY_PATH,
    MATERIAL_APPROVAL_REPORT_PATH,
    MATERIAL_APPROVAL_SAMPLE_PATH,
    MODULE_C_EXPOSURE_CANDIDATE_PATH,
    MONTHLY_STOCK_PATH,
    STOCK_MATERIAL_MAPPING_PATH,
)
from ..material_mapping import load_approved_stock_material_mapping
from ..utils import ensure_dirs, write_json


POLICY_REQUIRED_COLUMNS = {
    "local_item_key",
    "institution_code",
    "item_code",
    "representative_name",
    "item_family_id",
    "item_subtype_id",
    "raw_material_meta_code",
    "raw_material_risk_meta_code",
    "material_confidence",
    "material_evidence_tier",
    "material_evidence_reference",
    "classification_material_family_conflict",
    "market_factor_count",
    "supply_risk_policy_needs_review",
    "usage_sum",
}
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


def _as_bool(series: pd.Series) -> pd.Series:
    return (
        series.astype("string")
        .fillna("")
        .str.strip()
        .str.lower()
        .isin({"true", "t", "1", "yes", "y"})
    )


def load_approval_policy(path: Path = MATERIAL_APPROVAL_POLICY_PATH) -> dict:
    policy = json.loads(path.read_text(encoding="utf-8"))
    required = {"version", "status", "reviewer", "source", "rules"}
    missing = required - set(policy)
    if missing:
        raise ValueError(f"Material approval policy is missing keys: {sorted(missing)}")
    if policy["status"] != "experimental_branch_only":
        raise ValueError("Material approval policy must be marked experimental_branch_only")
    if not policy["rules"]:
        raise ValueError("Material approval policy has no rules")
    for rule in policy["rules"]:
        rule_required = {
            "rule_id",
            "item_family_id",
            "item_subtype_id",
            "raw_material_meta_code",
            "allowed_evidence_tiers",
            "relation_type",
            "usage_part",
            "related_material",
            "mapping_weight",
            "mapping_confidence",
            "exposure_score",
            "evidence_references",
            "weight_basis",
        }
        missing_rule = rule_required - set(rule)
        if missing_rule:
            raise ValueError(
                f"Material approval rule {rule.get('rule_id')} is missing: "
                f"{sorted(missing_rule)}"
            )
        for column in ["mapping_weight", "exposure_score"]:
            value = float(rule[column])
            if not 0 < value <= 1:
                raise ValueError(f"{rule['rule_id']} {column} must be within (0, 1]")
        if rule["mapping_confidence"] not in {"high", "medium", "low"}:
            raise ValueError(f"Unsupported mapping confidence: {rule['mapping_confidence']}")
        if not rule["evidence_references"]:
            raise ValueError(f"{rule['rule_id']} requires evidence references")
    return policy


def _candidate_rule_matches(candidates: pd.DataFrame, rule: dict) -> pd.Series:
    return (
        candidates["item_family_id"].astype(str).eq(rule["item_family_id"])
        & candidates["item_subtype_id"].astype(str).eq(rule["item_subtype_id"])
        & candidates["raw_material_meta_code"]
        .astype(str)
        .eq(rule["raw_material_meta_code"])
    )


def _prepare_stock_keys(monthly_stock: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "year_month",
        "institution_code",
        "department",
        "item_code",
        "item_name",
        "stock_item_key",
    ]
    missing = set(columns) - set(monthly_stock.columns)
    if missing:
        raise ValueError(f"Monthly stock is missing columns: {sorted(missing)}")
    stock = monthly_stock[columns].copy()
    for column in ["institution_code", "department", "item_code", "stock_item_key"]:
        stock[column] = stock[column].astype(str)
    stock["year_month"] = pd.to_datetime(stock["year_month"], errors="coerce")
    stock = stock.sort_values("year_month").drop_duplicates(
        ["institution_code", "department", "item_code"],
        keep="last",
    )
    return stock.drop(columns="year_month")


def build_material_approval(
    candidates: pd.DataFrame,
    monthly_stock: pd.DataFrame,
    policy: dict,
    reviewed_at: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    missing = POLICY_REQUIRED_COLUMNS - set(candidates.columns)
    if missing:
        raise ValueError(f"Material candidates are missing columns: {sorted(missing)}")
    reviewed_at = reviewed_at or datetime.now(timezone.utc).isoformat()
    if pd.isna(pd.to_datetime(reviewed_at, errors="coerce", utc=True)):
        raise ValueError("reviewed_at must be an ISO-8601 timestamp")

    audit = candidates.copy()
    audit["approval_status"] = "rejected"
    audit["approval_reason"] = "not_in_explicit_approval_policy"
    audit["approval_rule_id"] = ""

    family_conflict = _as_bool(audit["classification_material_family_conflict"])
    supply_review = _as_bool(audit["supply_risk_policy_needs_review"])
    market_covered = pd.to_numeric(
        audit["market_factor_count"], errors="coerce"
    ).fillna(0).gt(0)
    material_identified = audit["material_confidence"].astype(str).eq("identified")
    evidence_present = audit["material_evidence_reference"].fillna("").astype(str).str.strip().ne("")

    selected_frames = []
    for rule in policy["rules"]:
        matches = _candidate_rule_matches(audit, rule)
        tier_allowed = audit["material_evidence_tier"].astype(str).isin(
            rule["allowed_evidence_tiers"]
        )
        eligible = (
            matches
            & tier_allowed
            & ~family_conflict
            & ~supply_review
            & market_covered
            & material_identified
            & evidence_present
        )
        audit.loc[matches, "approval_rule_id"] = rule["rule_id"]
        audit.loc[matches & ~tier_allowed, "approval_reason"] = "evidence_tier_not_allowed"
        audit.loc[matches & family_conflict, "approval_reason"] = "family_conflict"
        audit.loc[matches & supply_review, "approval_reason"] = "supply_policy_review_required"
        audit.loc[matches & ~market_covered, "approval_reason"] = "market_factor_missing"
        audit.loc[matches & ~material_identified, "approval_reason"] = "material_not_identified"
        audit.loc[matches & ~evidence_present, "approval_reason"] = "candidate_evidence_missing"
        audit.loc[eligible, "approval_status"] = "approved"
        audit.loc[eligible, "approval_reason"] = "strict_policy_pass"

        selected = audit.loc[eligible].copy()
        if selected.empty:
            continue
        for key, value in {
            "relation_type": rule["relation_type"],
            "usage_part": rule["usage_part"],
            "related_material": rule["related_material"],
            "mapping_weight": float(rule["mapping_weight"]),
            "mapping_confidence": rule["mapping_confidence"],
            "exposure_score": float(rule["exposure_score"]),
            "evidence_reference": ";".join(rule["evidence_references"]),
        }.items():
            selected[key] = value
        selected_frames.append(selected)

    selected = (
        pd.concat(selected_frames, ignore_index=True)
        if selected_frames
        else audit.iloc[0:0].copy()
    )
    stock_keys = _prepare_stock_keys(monthly_stock)
    if selected.empty:
        mapping = pd.DataFrame(columns=MAPPING_OUTPUT_COLUMNS)
    else:
        for column in ["institution_code", "item_code"]:
            selected[column] = selected[column].astype(str)
        expanded = selected.merge(
            stock_keys,
            on=["institution_code", "item_code"],
            how="left",
            validate="many_to_many",
            indicator=True,
            suffixes=("", "_stock"),
        )
        unmatched = expanded["_merge"].ne("both")
        if unmatched.any():
            examples = expanded.loc[
                unmatched, ["institution_code", "item_code"]
            ].head(5)
            raise ValueError(
                "Approved candidates could not be expanded to stock_item_key: "
                f"{examples.to_dict(orient='records')}"
            )
        mapping = pd.DataFrame(
            {
                "stock_item_key": expanded["stock_item_key"],
                "item_name": expanded["representative_name"],
                "item_type": expanded["item_family_id"],
                "relation_type": expanded["relation_type"],
                "usage_part": expanded["usage_part"],
                "related_material": expanded["related_material"],
                "raw_material_meta_code": expanded["raw_material_meta_code"],
                "raw_material_risk_meta_code": expanded["raw_material_risk_meta_code"],
                "demand_risk_meta_code": "",
                "mapping_weight": expanded["mapping_weight"],
                "mapping_confidence": expanded["mapping_confidence"],
                "exposure_score": expanded["exposure_score"],
                "evidence_reference": expanded["evidence_reference"],
                "review_status": "approved",
                "reviewer": policy["reviewer"],
                "reviewed_at": reviewed_at,
                "mapping_version": policy["version"],
                "source": policy["source"],
            }
        )
        mapping = mapping.drop_duplicates(
            ["stock_item_key", "related_material"]
        ).sort_values(["stock_item_key", "related_material"])
        mapping = mapping[MAPPING_OUTPUT_COLUMNS].reset_index(drop=True)

    approved_candidate_keys = set(selected.get("local_item_key", pd.Series(dtype=str)))
    report = {
        "policy_version": policy["version"],
        "policy_status": policy["status"],
        "reviewer": policy["reviewer"],
        "reviewed_at": reviewed_at,
        "input_candidate_rows": int(len(candidates)),
        "input_local_item_count": int(candidates["local_item_key"].nunique()),
        "approved_candidate_rows": int(len(selected)),
        "approved_local_item_count": int(len(approved_candidate_keys)),
        "approved_stock_item_mapping_rows": int(len(mapping)),
        "approved_stock_item_count": int(mapping["stock_item_key"].nunique()),
        "approved_candidate_usage_sum": float(
            pd.to_numeric(selected.get("usage_sum"), errors="coerce").fillna(0).sum()
        ),
        "approval_reason_counts": {
            str(key): int(value)
            for key, value in audit["approval_reason"].value_counts().items()
        },
        "approved_rule_counts": {
            str(key): int(value)
            for key, value in audit.loc[
                audit["approval_status"].eq("approved"), "approval_rule_id"
            ].value_counts().items()
        },
        "demand_mapping_approved": False,
        "release_scope": "experimental_branch_only",
    }
    return mapping, audit, report


def select_approval_sample(audit: pd.DataFrame, sample_size: int = 1000) -> pd.DataFrame:
    if audit.empty or len(audit) <= sample_size:
        return audit.copy()
    approved = audit[audit["approval_status"].eq("approved")]
    rejected = audit[audit["approval_status"].ne("approved")]
    approved_quota = min(len(approved), sample_size // 2)
    rejected_quota = sample_size - approved_quota
    frames = [approved.sort_values("usage_sum", ascending=False).head(approved_quota)]
    if rejected_quota:
        rejected_sample = (
            rejected.sort_values("usage_sum", ascending=False)
            .groupby("approval_reason", group_keys=False)
            .head(max(1, rejected_quota // max(rejected["approval_reason"].nunique(), 1)))
            .head(rejected_quota)
        )
        if len(rejected_sample) < rejected_quota:
            remainder = rejected.loc[~rejected.index.isin(rejected_sample.index)].head(
                rejected_quota - len(rejected_sample)
            )
            rejected_sample = pd.concat([rejected_sample, remainder])
        frames.append(rejected_sample)
    return pd.concat(frames, ignore_index=True).head(sample_size)


def run_material_approval(
    apply: bool = False,
    policy_path: Path = MATERIAL_APPROVAL_POLICY_PATH,
    candidate_path: Path = MODULE_C_EXPOSURE_CANDIDATE_PATH,
    monthly_stock_path: Path = MONTHLY_STOCK_PATH,
    output_path: Path = STOCK_MATERIAL_MAPPING_PATH,
    reviewed_at: str | None = None,
) -> dict:
    policy = load_approval_policy(policy_path)
    candidates = pd.read_csv(candidate_path, low_memory=False, keep_default_na=False)
    monthly_columns = [
        "year_month",
        "institution_code",
        "department",
        "item_code",
        "item_name",
        "stock_item_key",
    ]
    monthly_stock = pd.read_parquet(monthly_stock_path, columns=monthly_columns)
    mapping, audit, report = build_material_approval(
        candidates,
        monthly_stock,
        policy,
        reviewed_at=reviewed_at,
    )
    sample = select_approval_sample(audit)
    ensure_dirs(
        MATERIAL_APPROVAL_AUDIT_PATH.parent,
        MATERIAL_APPROVAL_SAMPLE_PATH.parent,
        output_path.parent,
    )
    audit.to_csv(MATERIAL_APPROVAL_AUDIT_PATH, index=False)
    sample.to_csv(MATERIAL_APPROVAL_SAMPLE_PATH, index=False, encoding="utf-8-sig")
    report["applied"] = bool(apply)
    report["mapping_output_path"] = str(output_path)
    report["audit_output_path"] = str(MATERIAL_APPROVAL_AUDIT_PATH)
    report["sample_output_path"] = str(MATERIAL_APPROVAL_SAMPLE_PATH)
    if apply:
        temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
        try:
            mapping.to_csv(temporary_path, index=False)
            validated = load_approved_stock_material_mapping(temporary_path)
            if len(validated) != len(mapping):
                raise ValueError("Approved mapping validation changed the output row count")
            temporary_path.replace(output_path)
        finally:
            temporary_path.unlink(missing_ok=True)
    write_json(report, MATERIAL_APPROVAL_REPORT_PATH)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Approve stock-material mappings through an explicit pilot policy"
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--policy", type=Path, default=MATERIAL_APPROVAL_POLICY_PATH)
    parser.add_argument("--candidates", type=Path, default=MODULE_C_EXPOSURE_CANDIDATE_PATH)
    parser.add_argument("--monthly-stock", type=Path, default=MONTHLY_STOCK_PATH)
    parser.add_argument("--output", type=Path, default=STOCK_MATERIAL_MAPPING_PATH)
    parser.add_argument("--reviewed-at")
    args = parser.parse_args()
    report = run_material_approval(
        apply=args.apply,
        policy_path=args.policy,
        candidate_path=args.candidates,
        monthly_stock_path=args.monthly_stock,
        output_path=args.output,
        reviewed_at=args.reviewed_at,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
