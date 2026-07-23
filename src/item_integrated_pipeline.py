import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path

import pandas as pd

from .config import (
    ITEM_INTEGRATED_CLASSIFICATION_CSV_PATH,
    ITEM_INTEGRATED_CLASSIFICATION_PARQUET_PATH,
    ITEM_INTEGRATED_CLASSIFICATION_REPORT_PATH,
    ITEM_INTEGRATED_SAMPLE_PATH,
    ITEM_MATERIAL_OUTPUT_DIR,
    ITEM_MATERIAL_PIPELINE_REPORT_PATH,
    ITEM_PARENT_CONCEPT_PATH,
    ITEM_PRODUCT_WORKLIST_PATH,
    ITEM_REPRESENTATIVE_ATTRIBUTES_PATH,
)
from .item_classification import REPRESENTATIVE_OUTPUT_PATH, run_item_classification
from .material_pipeline import PIPELINE_VERSION, run_material_pipeline
from .utils import setup_logging, write_json


LOGGER = logging.getLogger(__name__)
INTEGRATED_PIPELINE_VERSION = "item-integrated-v2.1"
UNRESOLVED_FAMILIES = {"", "UNSPECIFIED_ITEM", "MATERIAL_UNSPECIFIED"}
PHYSICAL_FAMILY_DEFAULTS = {
    "DISPOSABLE_SYRINGE": ("SYRINGE_USAGE_BASED", "주사기(사용량 기준)", "EA"),
    "BLOOD_LANCET": ("BLOOD_LANCET", "채혈침", "EA"),
    "INJECTION_NEEDLE": ("INJECTION_NEEDLE", "주사침", "EA"),
    "BLOOD_GLUCOSE_TEST_STRIP": (
        "BLOOD_GLUCOSE_TEST_STRIP",
        "혈당검사지",
        "EA",
    ),
    "BLOOD_GLUCOSE_TESTING_SET": (
        "BLOOD_GLUCOSE_TESTING_SET",
        "혈당측정 소모품 세트",
        "EA",
    ),
    "BLOOD_GLUCOSE_METER_KIT": (
        "BLOOD_GLUCOSE_METER_KIT",
        "혈당측정기·소모품 세트",
        "EA",
    ),
    "ALCOHOL_SWAB": ("ALCOHOL_SWAB", "알코올스왑", "EA"),
    "MEDICAL_MASK": ("MEDICAL_MASK", "의료용 마스크", "EA"),
    "MEDICAL_WASTE_CONTAINER": (
        "WASTE_CONTAINER_UNSPECIFIED",
        "의료폐기물 전용용기(형태 미상)",
        "EA",
    ),
    "INFUSION_SET": ("INFUSION_SET", "수액세트", "EA"),
    "ANGIO_CATHETER": ("ANGIO_CATHETER", "카테터(angio needle)", "EA"),
    "IV_FLUID_CONTAINER": ("IV_FLUID_CONTAINER", "수액제통", "EA"),
    "DIALYSATE_CONTAINER": ("DIALYSATE_CONTAINER", "혈액투석제통", "EA"),
    "URINE_BAG": ("URINE_BAG", "Urine bag", "EA"),
}


def _text_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series("", index=frame.index, dtype="string")
    return frame[column].astype("string").fillna("").str.strip()


def _fill_text(primary: pd.Series, fallback: pd.Series) -> pd.Series:
    primary = primary.astype("string").fillna("")
    return primary.where(primary.str.strip().ne(""), fallback.astype("string").fillna(""))


def _truthy(series: pd.Series) -> pd.Series:
    return series.astype("string").fillna("").str.lower().isin({"true", "t", "1", "yes"})


def _read_previous_report(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def compose_integrated_classification(
    material_path: Path,
    classification_path: Path,
    parent_path: Path,
) -> pd.DataFrame:
    material = pd.read_csv(material_path, low_memory=False)
    classifications = pd.read_parquet(classification_path)
    parents = pd.read_csv(parent_path, dtype=str, keep_default_na=False)

    for label, frame in [
        ("material", material),
        ("classification", classifications),
        ("parent", parents),
    ]:
        if frame["representative_item_id"].duplicated().any():
            raise ValueError(f"{label} output has duplicate representative_item_id values")
    ids = set(material["representative_item_id"].astype(str))
    if ids != set(classifications["representative_item_id"].astype(str)):
        raise ValueError("Classification IDs do not match material IDs")
    if ids != set(parents["representative_item_id"].astype(str)):
        raise ValueError("Parent concept IDs do not match material IDs")

    classification_columns = [
        "representative_item_id",
        "selected_item_family_id",
        "selected_standard_family_name",
        "selected_item_subtype_id",
        "selected_standard_subtype_name",
        "selected_specification",
        "selected_unit_code",
        "classification_status",
        "classification_basis",
        "classification_confidence",
        "review_status",
        "review_reason",
        "verification_status",
        "evidence_source",
        "evidence_record_id",
        "evidence_url",
        "evidence_field",
        "retrieved_at_effective",
        "is_forecastable",
        "classification_version",
    ]
    classification_columns = [
        column for column in classification_columns if column in classifications.columns
    ]
    rename = {
        column: f"classification_{column}"
        for column in classification_columns
        if column != "representative_item_id"
    }
    classification_frame = classifications[classification_columns].rename(columns=rename)
    parent_columns = [
        "representative_item_id",
        "parent_concept_id",
        "parent_concept_name",
        "parent_concept_source",
        "child_original_name",
        "concept_match_key",
        "forecast_grouping_key_candidate",
    ]
    integrated = material.merge(
        classification_frame,
        on="representative_item_id",
        how="left",
        validate="one_to_one",
    ).merge(
        parents[parent_columns],
        on="representative_item_id",
        how="left",
        validate="one_to_one",
    )

    classification_approved = _text_series(
        integrated, "classification_review_status"
    ).eq("approved")
    classification_family = _text_series(
        integrated, "classification_selected_item_family_id"
    )
    classification_family_name = _text_series(
        integrated, "classification_selected_standard_family_name"
    )
    integrated["effective_item_family_id"] = _fill_text(
        _text_series(integrated, "item_family_id_suggested"),
        classification_family,
    )
    integrated.loc[classification_approved, "effective_item_family_id"] = _fill_text(
        classification_family,
        integrated["effective_item_family_id"],
    ).loc[classification_approved]
    integrated["effective_standard_family_name"] = _fill_text(
        _text_series(integrated, "standard_family_name_suggested"),
        classification_family_name,
    )
    integrated.loc[
        classification_approved, "effective_standard_family_name"
    ] = _fill_text(
        classification_family_name,
        integrated["effective_standard_family_name"],
    ).loc[classification_approved]
    integrated["effective_item_subtype_id"] = _fill_text(
        _text_series(integrated, "classification_selected_item_subtype_id"),
        _text_series(integrated, "item_subtype_id_candidate"),
    )
    integrated["effective_standard_subtype_name"] = _fill_text(
        _text_series(integrated, "classification_selected_standard_subtype_name"),
        _text_series(integrated, "standard_subtype_name_candidate"),
    )
    specification = _fill_text(
        _text_series(integrated, "classification_selected_specification"),
        _text_series(integrated, "normalized_specification_candidate"),
    )
    specification = _fill_text(specification, _text_series(integrated, "capacity_normalized"))
    specification = _fill_text(specification, _text_series(integrated, "needle_gauge"))
    integrated["effective_specification"] = specification
    integrated["effective_unit_code"] = _fill_text(
        _text_series(integrated, "classification_selected_unit_code"),
        _text_series(integrated, "standard_unit_candidate"),
    )
    integrated["effective_unit_code"] = _fill_text(
        integrated["effective_unit_code"], _text_series(integrated, "inventory_unit")
    )
    family_changed = classification_family.ne("") & classification_family.ne(
        integrated["effective_item_family_id"]
    )
    for family_id, (subtype_id, subtype_name, unit_code) in PHYSICAL_FAMILY_DEFAULTS.items():
        family_rows = integrated["effective_item_family_id"].eq(family_id)
        detail_rows = family_rows & (
            family_changed | integrated["effective_item_subtype_id"].eq("")
        )
        integrated.loc[detail_rows, "effective_item_subtype_id"] = subtype_id
        integrated.loc[detail_rows, "effective_standard_subtype_name"] = subtype_name
        unit_rows = family_rows & (
            family_changed | integrated["effective_unit_code"].eq("")
        )
        integrated.loc[unit_rows, "effective_unit_code"] = unit_code

    composite_rows = integrated["effective_item_family_id"].isin(
        {"BLOOD_GLUCOSE_TESTING_SET", "BLOOD_GLUCOSE_METER_KIT"}
    )
    integrated.loc[composite_rows, "effective_specification"] = ""
    gauge_family_changed = family_changed & integrated["effective_item_family_id"].isin(
        {"BLOOD_LANCET", "INJECTION_NEEDLE", "ANGIO_CATHETER"}
    )
    integrated.loc[gauge_family_changed, "effective_specification"] = _text_series(
        integrated, "needle_gauge"
    ).loc[gauge_family_changed]
    integrated["forecast_series_definition_key"] = (
        _text_series(integrated, "parent_concept_id")
        + "::"
        + integrated["effective_item_subtype_id"].replace("", "UNSPECIFIED_SUBTYPE")
        + "::"
        + integrated["effective_specification"].replace("", "UNSPECIFIED_SPEC")
        + "::"
        + integrated["effective_unit_code"].replace("", "UNSPECIFIED_UNIT")
    )

    forecastable = _truthy(_text_series(integrated, "classification_is_forecastable"))
    activity = _text_series(integrated, "activity_scope")
    unresolved = integrated["effective_item_family_id"].isin(UNRESOLVED_FAMILIES)
    details_complete = (
        integrated["effective_item_subtype_id"].ne("")
        & integrated["effective_specification"].ne("")
        & integrated["effective_unit_code"].ne("")
    )
    integrated["forecast_eligibility_candidate"] = "ready_after_review"
    integrated.loc[~details_complete, "forecast_eligibility_candidate"] = (
        "family_only_needs_detail"
    )
    integrated.loc[unresolved, "forecast_eligibility_candidate"] = "unresolved_family"
    integrated.loc[activity.eq("one_off"), "forecast_eligibility_candidate"] = (
        "exclude_one_off_candidate"
    )
    integrated.loc[~forecastable, "forecast_eligibility_candidate"] = (
        "exclude_non_forecastable_group"
    )

    family_verified = _text_series(integrated, "family_source").isin(
        {"verified_structured_family", "verified_ingredient_dictionary"}
    )
    integrated["operational_readiness"] = "classification_and_material_review_required"
    integrated.loc[unresolved, "operational_readiness"] = "external_evidence_required"
    integrated.loc[family_verified & ~classification_approved, "operational_readiness"] = (
        "taxonomy_review_and_material_review_required"
    )
    integrated.loc[classification_approved, "operational_readiness"] = (
        "classification_approved_material_review_required"
    )
    integrated["integrated_pipeline_version"] = INTEGRATED_PIPELINE_VERSION

    key_columns = [
        "representative_item_id",
        "representative_name",
        "item_group_id_candidate",
        "effective_item_family_id",
        "effective_standard_family_name",
        "effective_item_subtype_id",
        "effective_standard_subtype_name",
        "effective_specification",
        "effective_unit_code",
        "parent_concept_id",
        "parent_concept_name",
        "forecast_series_definition_key",
        "forecast_eligibility_candidate",
        "operational_readiness",
    ]
    ordered = key_columns + [column for column in integrated.columns if column not in key_columns]
    return integrated[ordered]


def _attention_flags(frame: pd.DataFrame) -> pd.Series:
    names = _text_series(frame, "representative_name").str.lower()
    conflict = _truthy(_text_series(frame, "family_conflict_flag"))
    family_source = _text_series(frame, "family_source")
    material_tier = _text_series(frame, "material_evidence_tier")
    unresolved = frame["effective_item_family_id"].isin(UNRESOLVED_FAMILIES)
    context_pattern = names.str.contains(
        r"lanset|lancet|란셋|난셋|산소\s*마스크|주사(?:침|바늘).*통|알코올.*(?:솜|스왑)|알콜.*(?:솜|스왑)",
        regex=True,
    )
    masks = [
        (conflict, "family_conflict_structured_preferred"),
        (family_source.isin({"verified_structured_family", "verified_ingredient_dictionary"}), "verified_family_evidence"),
        (context_pattern, "context_collision_guard"),
        (material_tier.eq("subtype_rule_candidate"), "subtype_material_candidate"),
        (_text_series(frame, "needle_gauge").ne(""), "needle_gauge_parsed"),
        (_text_series(frame, "capacity_normalized").ne(""), "capacity_parsed"),
        (_text_series(frame, "pack_quantity").ne(""), "pack_quantity_parsed"),
        (material_tier.isin({"group_fallback", "unmapped", "sentinel_blocked", "composite_set_requires_bom", "naming_pattern_unverified"}), "material_low_evidence"),
        (unresolved & _text_series(frame, "activity_scope").ne("one_off"), "active_unresolved"),
        (family_source.eq("name_rule"), "name_rule_only_review"),
    ]
    flags = []
    for index in frame.index:
        labels = [label for mask, label in masks if bool(mask.loc[index])]
        flags.append(";".join(labels) if labels else "high_activity_reference")
    return pd.Series(flags, index=frame.index, dtype="string")


def select_integrated_sample(frame: pd.DataFrame, sample_size: int = 1000) -> pd.DataFrame:
    if sample_size <= 0:
        raise ValueError("sample_size must be positive")
    review = frame.copy()
    review["attention_flags"] = _attention_flags(review)
    review["primary_attention_reason"] = review["attention_flags"].str.split(";").str[0]
    review["_occurrence"] = pd.to_numeric(review.get("occurrence_count"), errors="coerce").fillna(0)
    review["_usage"] = pd.to_numeric(review.get("usage_sum"), errors="coerce").fillna(0)
    review["_hash"] = review["representative_item_id"].astype(str).map(
        lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
    )
    review = review.sort_values(
        ["_occurrence", "_usage", "_hash"],
        ascending=[False, False, True],
        kind="stable",
    )

    strata = review.groupby(
        ["primary_attention_reason", "item_group_id_candidate"],
        dropna=False,
        sort=False,
    ).head(2)
    critical = review[
        _truthy(_text_series(review, "family_conflict_flag"))
        | _text_series(review, "family_source").isin(
            {"verified_structured_family", "verified_ingredient_dictionary"}
        )
        | _text_series(review, "material_evidence_tier").isin(
            {"sentinel_blocked", "composite_set_requires_bom"}
        )
    ]
    reasons = review["primary_attention_reason"].drop_duplicates().tolist()
    per_reason = max(20, sample_size // max(len(reasons), 1) // 2)
    reason_rows = review.groupby("primary_attention_reason", sort=False).head(per_reason)
    selected = pd.concat([critical, strata, reason_rows], ignore_index=False).drop_duplicates(
        "representative_item_id"
    )
    if len(selected) < sample_size:
        remaining = review[
            ~review["representative_item_id"].isin(selected["representative_item_id"])
        ]
        selected = pd.concat([selected, remaining.head(sample_size - len(selected))])
    selected = selected.head(min(sample_size, len(review)))

    sample_columns = [
        "representative_item_id",
        "representative_name",
        "raw_name_examples",
        "item_group_id_candidate",
        "effective_item_family_id",
        "effective_standard_family_name",
        "effective_item_subtype_id",
        "effective_standard_subtype_name",
        "effective_specification",
        "effective_unit_code",
        "manufacturer_candidate",
        "ingredient_ids",
        "ingredient_names",
        "dosage_form",
        "capacity_normalized",
        "capacity_role",
        "needle_gauge",
        "needle_length",
        "dimensions",
        "pack_quantity",
        "pack_unit",
        "inventory_unit",
        "parent_concept_id",
        "parent_concept_name",
        "family_source",
        "family_resolution_status",
        "family_conflict_flag",
        "family_conflict_reason",
        "name_rule_item_family_id",
        "name_rule_standard_family_name",
        "name_rule_family_basis",
        "material_source_family_id",
        "material_source_subtype_id",
        "raw_material_suggested",
        "raw_material_meta_code",
        "material_evidence_tier",
        "material_review_status",
        "classification_classification_status",
        "classification_review_status",
        "forecast_eligibility_candidate",
        "operational_readiness",
        "occurrence_count",
        "institution_count",
        "usage_sum",
        "external_match_needed_reasons",
        "evidence_note",
        "name_rule_evidence_note",
        "attention_flags",
        "primary_attention_reason",
    ]
    sample_columns = [column for column in sample_columns if column in selected.columns]
    return selected[sample_columns].reset_index(drop=True)


def _counts(frame: pd.DataFrame, column: str) -> dict[str, int]:
    return {
        str(key): int(value)
        for key, value in frame[column]
        .astype("string")
        .fillna("missing")
        .value_counts()
        .items()
    }


def _comparison(
    previous: dict[str, object],
    current: dict[str, object],
    inherited: dict[str, object] | None = None,
) -> dict[str, object]:
    if (
        inherited
        and inherited.get("baseline_available")
        and previous.get("pipeline_version")
        != inherited.get("baseline_pipeline_version")
    ):
        comparison = dict(inherited)
        comparison.update(
            {
                "current_pipeline_version": current.get("pipeline_version", ""),
                "current_rows": current.get("rows"),
                "current_family_basis": current.get("family_basis", {}),
                "current_material_confidence": current.get("material_confidence", {}),
                "current_family_conflicts_preserved": current.get("family_conflicts", 0),
                "current_sentinel_material_code_rows": current.get(
                    "sentinel_material_code_rows", 0
                ),
            }
        )
        return comparison
    if not previous:
        return {"baseline_available": False}
    return {
        "baseline_available": True,
        "baseline_pipeline_version": previous.get("pipeline_version", ""),
        "current_pipeline_version": current.get("pipeline_version", ""),
        "baseline_rows": previous.get("rows"),
        "current_rows": current.get("rows"),
        "baseline_family_basis": previous.get("family_basis", {}),
        "current_family_basis": current.get("family_basis", {}),
        "baseline_material_confidence": previous.get("material_confidence", {}),
        "current_material_confidence": current.get("material_confidence", {}),
        "current_family_conflicts_preserved": current.get("family_conflicts", 0),
        "current_sentinel_material_code_rows": current.get("sentinel_material_code_rows", 0),
    }


def run_integrated_pipeline(
    input_path: Path = ITEM_PRODUCT_WORKLIST_PATH,
    attributes_path: Path = ITEM_REPRESENTATIVE_ATTRIBUTES_PATH,
    material_output_dir: Path = ITEM_MATERIAL_OUTPUT_DIR,
    full_csv_path: Path = ITEM_INTEGRATED_CLASSIFICATION_CSV_PATH,
    full_parquet_path: Path = ITEM_INTEGRATED_CLASSIFICATION_PARQUET_PATH,
    sample_path: Path = ITEM_INTEGRATED_SAMPLE_PATH,
    report_path: Path = ITEM_INTEGRATED_CLASSIFICATION_REPORT_PATH,
    sample_size: int = 1000,
    with_excel: bool = False,
) -> dict[str, object]:
    setup_logging()
    previous_report = _read_previous_report(
        material_output_dir / ITEM_MATERIAL_PIPELINE_REPORT_PATH.name
    )
    previous_integrated_report = _read_previous_report(report_path)
    material_report = run_material_pipeline(
        input_path=input_path,
        output_dir=material_output_dir,
        with_excel=with_excel,
        attributes_path=attributes_path,
    )
    suggestion_path = material_output_dir / "item_family_candidate_suggestions_full.csv"
    classification_report = run_item_classification(
        worklist_path=input_path,
        suggestion_path=suggestion_path,
        sample_size=sample_size,
    )
    integrated = compose_integrated_classification(
        material_path=material_output_dir / "item_material_event_mapping_full.csv",
        classification_path=REPRESENTATIVE_OUTPUT_PATH,
        parent_path=material_output_dir / ITEM_PARENT_CONCEPT_PATH.name,
    )
    sample = select_integrated_sample(integrated, sample_size=sample_size)

    approved = _text_series(integrated, "classification_review_status").eq("approved")
    approved_fields_preserved = (
        integrated.loc[approved, "effective_item_family_id"]
        .eq(
            _text_series(
                integrated.loc[approved], "classification_selected_item_family_id"
            )
        )
        .all()
        and integrated.loc[approved, "effective_item_subtype_id"]
        .eq(
            _text_series(
                integrated.loc[approved], "classification_selected_item_subtype_id"
            )
        )
        .all()
        and integrated.loc[approved, "effective_specification"]
        .eq(
            _text_series(
                integrated.loc[approved], "classification_selected_specification"
            )
        )
        .all()
        and integrated.loc[approved, "effective_unit_code"]
        .eq(
            _text_series(
                integrated.loc[approved], "classification_selected_unit_code"
            )
        )
        .all()
    )

    for path in [full_csv_path, full_parquet_path, sample_path, report_path]:
        path.parent.mkdir(parents=True, exist_ok=True)
    integrated.to_csv(full_csv_path, index=False, encoding="utf-8-sig")
    integrated.to_parquet(full_parquet_path, index=False, compression="zstd")
    sample.to_csv(sample_path, index=False, encoding="utf-8-sig")

    expected_sample_rows = min(sample_size, len(integrated))
    quality_gates = {
        "full_rows_equal_input": len(integrated) == material_report["input_rows"],
        "full_ids_unique": not integrated["representative_item_id"].duplicated().any(),
        "sample_rows_exact": len(sample) == expected_sample_rows,
        "sample_ids_unique": not sample["representative_item_id"].duplicated().any(),
        "approved_classification_fields_preserved": bool(
            approved_fields_preserved
        ),
        "no_verified_material_claims": "raw_material_verified" not in integrated.columns,
        "all_material_candidates_review_only": bool(
            _text_series(integrated, "material_review_status")
            .eq("needs_review")
            .all()
        ),
    }
    if not all(quality_gates.values()):
        raise ValueError(f"Integrated output quality gate failed: {quality_gates}")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "integrated_pipeline_version": INTEGRATED_PIPELINE_VERSION,
        "material_pipeline_version": PIPELINE_VERSION,
        "status": "full_and_sample_outputs_ready_for_review",
        "rows": int(len(integrated)),
        "sample_rows": int(len(sample)),
        "unique_representative_items": int(integrated["representative_item_id"].nunique()),
        "family_source": _counts(integrated, "family_source"),
        "family_resolution_status": _counts(integrated, "family_resolution_status"),
        "material_evidence_tier": _counts(integrated, "material_evidence_tier"),
        "forecast_eligibility_candidate": _counts(
            integrated, "forecast_eligibility_candidate"
        ),
        "operational_readiness": _counts(integrated, "operational_readiness"),
        "sample_attention_reason": _counts(sample, "primary_attention_reason"),
        "material_report": material_report,
        "classification_summary": classification_report,
        "comparison_with_previous_local_run": _comparison(
            previous_report,
            material_report,
            previous_integrated_report.get("comparison_with_previous_local_run", {}),
        ),
        "quality_gates": quality_gates,
        "outputs": {
            "full_csv": str(full_csv_path),
            "full_parquet": str(full_parquet_path),
            "sample_csv": str(sample_path),
            "material_candidates": str(
                material_output_dir / "item_material_event_mapping_full.csv"
            ),
            "parent_concepts": str(material_output_dir / ITEM_PARENT_CONCEPT_PATH.name),
            "report": str(report_path),
        },
        "interpretation": [
            "effective_* fields are the integrated candidate classification for review.",
            "24G and similar gauge tokens remain specifications, not inventory quantities.",
            "raw_material_suggested is never operational until a separate approved mapping exists.",
            "forecast_series_definition_key is a candidate grouping key, not an approved merge key.",
        ],
    }
    write_json(report, report_path)
    LOGGER.info("Saved integrated full output: %s", full_csv_path)
    LOGGER.info("Saved integrated review sample: %s", sample_path)
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the combined item classification, material, and parent-concept pipeline."
    )
    parser.add_argument("--input", type=Path, default=ITEM_PRODUCT_WORKLIST_PATH)
    parser.add_argument("--attributes", type=Path, default=ITEM_REPRESENTATIVE_ATTRIBUTES_PATH)
    parser.add_argument("--sample-size", type=int, default=1000)
    parser.add_argument("--with-excel", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    result = run_integrated_pipeline(
        input_path=args.input,
        attributes_path=args.attributes,
        sample_size=args.sample_size,
        with_excel=args.with_excel,
    )
    print(json.dumps({
        "status": result["status"],
        "rows": result["rows"],
        "sample_rows": result["sample_rows"],
        "outputs": result["outputs"],
    }, ensure_ascii=False, indent=2))
