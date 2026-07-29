import argparse
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import (
    DRUG_INGREDIENT_ENRICHMENT_PATH,
    ITEM_MATERIAL_EVENT_CANDIDATE_PATH,
    ITEM_MATERIAL_GLOSSARY_PATH,
    ITEM_MATERIAL_OUTPUT_DIR,
    ITEM_MATERIAL_PIPELINE_DIR,
    ITEM_MATERIAL_PIPELINE_REPORT_PATH,
    ITEM_MATERIAL_REVIEW_QUEUE_PATH,
    ITEM_PARENT_CONCEPT_PATH,
    ITEM_PRODUCT_WORKLIST_PATH,
    ITEM_REPRESENTATIVE_ATTRIBUTES_PATH,
)
from .drug_ingredient import attach_approved_drug_ingredients
from .utils import ensure_dirs, setup_logging, write_json


LOGGER = logging.getLogger(__name__)
RETIRED_UPSTREAM_COMMIT = "938fa3e6a0af82bdeb1fd9c3e4ecb96a9db41499"
CANONICAL_UPSTREAM_COMMIT = "74d398204000d855e86da084afa5b671a63d50fe"
PIPELINE_VERSION = "combined-material-v2.2"
SENTINEL_MATERIAL_CODES = {
    "NON_INGREDIENT_SPEC",
    "MANUFACTURER_NAME_NOISE",
    "UNKNOWN_INGREDIENT",
}
REQUIRED_OUTPUT_COLUMNS = {
    "representative_item_id",
    "representative_name",
    "item_group_id_candidate",
    "item_family_id_suggested",
    "standard_family_name_suggested",
    "family_basis",
    "family_source",
    "family_resolution_status",
    "family_conflict_flag",
    "name_rule_item_family_id",
    "name_rule_family_basis",
    "family_review_status",
    "supply_cluster_id",
    "raw_material_suggested",
    "raw_material_evidence",
    "raw_material_meta_code",
    "material_confidence",
    "material_evidence_tier",
    "material_source_family_id",
    "material_source_subtype_id",
    "activity_scope",
    "raw_material_risk_meta_code",
    "demand_risk_meta_code",
    "material_review_status",
    "material_pipeline_version",
}
PARENT_REQUIRED_COLUMNS = {
    "representative_item_id",
    "parent_concept_id",
    "parent_concept_name",
    "parent_concept_source",
    "forecast_grouping_key_candidate",
}
META_CODE_COLUMNS = [
    "raw_material_meta_code",
    "raw_material_risk_meta_code",
    "demand_risk_meta_code",
]


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    return pd.read_csv(path, low_memory=False)


def _blank_mask(series: pd.Series) -> pd.Series:
    return series.isna() | series.astype("string").fillna("").str.strip().eq("")


def prepare_material_input(
    input_path: Path,
    output_dir: Path,
    attributes_path: Path | None = ITEM_REPRESENTATIVE_ATTRIBUTES_PATH,
    drug_ingredient_path: Path | None = DRUG_INGREDIENT_ENRICHMENT_PATH,
) -> tuple[Path, dict[str, object]]:
    """Attach parsed attributes without allowing them to replace populated worklist values."""
    base = _read_table(input_path)
    required = {"representative_item_id", "representative_name", "item_group_id_candidate"}
    missing = sorted(required - set(base.columns))
    if missing:
        raise ValueError(f"Material pipeline input is missing columns: {missing}")
    if base["representative_item_id"].duplicated().any():
        raise ValueError("Material input must have one row per representative_item_id")

    matched_rows = 0
    attribute_columns_added = 0
    drug_ingredient_rows = 0
    approved_drug_ingredient_rows = 0
    prepared = base.copy()
    if attributes_path is not None and attributes_path.exists():
        attributes = _read_table(attributes_path)
        if "representative_item_id" not in attributes.columns:
            raise ValueError("Representative attributes are missing representative_item_id")
        if attributes["representative_item_id"].duplicated().any():
            raise ValueError("Representative attributes have duplicate IDs")
        attribute_names = [
            column for column in attributes.columns if column != "representative_item_id"
        ]
        renamed = {column: f"{column}__attribute" for column in attribute_names}
        prepared = prepared.merge(
            attributes.rename(columns=renamed),
            on="representative_item_id",
            how="left",
            validate="one_to_one",
            indicator="_attribute_merge",
        )
        matched_rows = int(prepared["_attribute_merge"].eq("both").sum())
        prepared = prepared.drop(columns="_attribute_merge")
        for column in attribute_names:
            attribute_column = renamed[column]
            if column in base.columns:
                prepared[column] = prepared[column].where(
                    ~_blank_mask(prepared[column]), prepared[attribute_column]
                )
                prepared = prepared.drop(columns=attribute_column)
            else:
                prepared = prepared.rename(columns={attribute_column: column})
                attribute_columns_added += 1

    if drug_ingredient_path is not None and drug_ingredient_path.exists():
        drug_ingredients = _read_table(drug_ingredient_path)
        required_drug_columns = {
            "representative_item_id",
            "drug_ingredient_review_status",
            "drug_raw_material_meta_code",
        }
        missing_drug_columns = required_drug_columns - set(drug_ingredients.columns)
        if missing_drug_columns:
            raise ValueError(
                "Drug ingredient enrichment is missing columns: "
                f"{sorted(missing_drug_columns)}"
            )
        if drug_ingredients["representative_item_id"].duplicated().any():
            raise ValueError("Drug ingredient enrichment contains duplicate IDs")
        drug_ingredient_rows = int(len(drug_ingredients))
        approved_drug_ingredient_rows = int(
            drug_ingredients["drug_ingredient_review_status"].eq("approved").sum()
        )
        prepared = attach_approved_drug_ingredients(prepared, drug_ingredients)

    if len(prepared) != len(base):
        raise ValueError(
            f"Attribute merge changed row count: before={len(base)}, after={len(prepared)}"
        )
    if prepared["representative_item_id"].duplicated().any():
        raise ValueError("Prepared material input has duplicate representative IDs")

    ensure_dirs(output_dir)
    prepared_path = output_dir / "_integrated_input.parquet"
    prepared.to_parquet(prepared_path, index=False, compression="zstd")
    return prepared_path, {
        "source_rows": int(len(base)),
        "attribute_rows_matched": matched_rows,
        "attribute_columns_added": attribute_columns_added,
        "attributes_path": str(attributes_path) if attributes_path else "",
        "drug_ingredient_rows": drug_ingredient_rows,
        "approved_drug_ingredient_rows": approved_drug_ingredient_rows,
        "drug_ingredient_path": (
            str(drug_ingredient_path) if drug_ingredient_path else ""
        ),
        "prepared_path": str(prepared_path),
    }


def _value_counts(series: pd.Series) -> dict[str, int]:
    values = series.astype("string").fillna("missing")
    return {str(key): int(value) for key, value in values.value_counts().items()}


def _unique_meta_code_count(series: pd.Series) -> int:
    codes: set[str] = set()
    for value in series.dropna().astype(str):
        codes.update(code.strip() for code in value.split(";") if code.strip())
    return len(codes)


def _sentinel_material_rows(series: pd.Series) -> int:
    return int(
        series.astype("string")
        .fillna("")
        .map(
            lambda value: bool(
                SENTINEL_MATERIAL_CODES
                & {code.strip() for code in value.split(";") if code.strip()}
            )
        )
        .sum()
    )


def validate_material_pipeline_output(
    candidate_path: Path,
    input_path: Path,
    glossary_path: Path,
    queue_path: Path,
    parent_path: Path,
) -> dict[str, object]:
    if not candidate_path.exists():
        raise FileNotFoundError(f"Material candidate output not found: {candidate_path}")
    for label, path in [
        ("glossary", glossary_path),
        ("review queue", queue_path),
        ("parent concepts", parent_path),
    ]:
        if not path.exists():
            raise FileNotFoundError(f"Material pipeline {label} not found: {path}")

    candidates = pd.read_csv(candidate_path, low_memory=False)
    missing = sorted(REQUIRED_OUTPUT_COLUMNS - set(candidates.columns))
    if missing:
        raise ValueError(f"Material candidate output is missing columns: {missing}")
    if "raw_material_verified" in candidates.columns:
        raise ValueError(
            "Candidate output must not expose raw_material_verified; use raw_material_suggested"
        )
    if candidates["representative_item_id"].duplicated().any():
        raise ValueError("Material candidate output must have one row per representative_item_id")
    input_rows = len(_read_table(input_path))
    if len(candidates) != input_rows:
        raise ValueError(
            f"Material pipeline row count changed: input={input_rows}, output={len(candidates)}"
        )

    parents = pd.read_csv(parent_path, dtype=str, keep_default_na=False)
    missing_parent = sorted(PARENT_REQUIRED_COLUMNS - set(parents.columns))
    if missing_parent:
        raise ValueError(f"Parent concept output is missing columns: {missing_parent}")
    if len(parents) != input_rows or parents["representative_item_id"].duplicated().any():
        raise ValueError("Parent concept output must preserve one row per representative item")
    candidate_ids = set(candidates["representative_item_id"].astype(str))
    parent_ids = set(parents["representative_item_id"].astype(str))
    if candidate_ids != parent_ids:
        raise ValueError("Parent concept IDs do not match material candidate IDs")

    glossary = pd.read_csv(glossary_path, dtype=str, keep_default_na=False)
    glossary_required = {"meta_code", "category", "description", "stage_confidence"}
    missing_glossary_columns = sorted(glossary_required - set(glossary.columns))
    if missing_glossary_columns:
        raise ValueError(f"Material glossary is missing columns: {missing_glossary_columns}")
    if glossary.duplicated(["category", "meta_code"], keep=False).any():
        raise ValueError("Material glossary must be unique per category and meta_code")
    category_by_column = {
        "raw_material_meta_code": "raw_material",
        "raw_material_risk_meta_code": "raw_material_risk",
        "demand_risk_meta_code": "demand_risk",
    }
    for column, category in category_by_column.items():
        used_codes: set[str] = set()
        for value in candidates[column].dropna().astype(str):
            used_codes.update(code.strip() for code in value.split(";") if code.strip())
        glossary_codes = set(glossary.loc[glossary["category"].eq(category), "meta_code"])
        missing_codes = sorted(used_codes - glossary_codes)
        if missing_codes:
            raise ValueError(
                f"Material glossary is missing {category} codes used by candidates: "
                f"{missing_codes[:10]}"
            )

    required_nonblank = [
        "representative_item_id",
        "raw_material_meta_code",
        "raw_material_risk_meta_code",
        "demand_risk_meta_code",
        "material_review_status",
    ]
    blank = {
        column: int(_blank_mask(candidates[column]).sum()) for column in required_nonblank
    }
    blank = {column: count for column, count in blank.items() if count}
    if blank:
        raise ValueError(f"Material candidate output has blank required values: {blank}")
    if (~candidates["material_review_status"].eq("needs_review")).any():
        raise ValueError("Material candidates must remain needs_review until separately approved")
    if (~candidates["material_pipeline_version"].eq(PIPELINE_VERSION)).any():
        raise ValueError("Material candidate output has an unexpected pipeline version")
    sentinel_leaks = _sentinel_material_rows(candidates["raw_material_meta_code"])
    if sentinel_leaks:
        raise ValueError(f"Non-ingredient sentinel leaked into material codes: {sentinel_leaks}")

    hangul = re.compile(r"[가-힣]")
    hangul_meta_codes = sum(
        int(
            candidates[column]
            .astype("string")
            .fillna("")
            .map(lambda value: bool(hangul.search(value)))
            .sum()
        )
        for column in META_CODE_COLUMNS
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "candidate_output_ready_for_review",
        "source_repositories": {
            "retired_source": {
                "repository": "DGU-TeamLex/wep-stock-data-normalization",
                "commit": RETIRED_UPSTREAM_COMMIT,
            },
            "canonical_source": {
                "repository": "DGU-TeamLex/wep-stock-item-material-pipeline",
                "commit": CANONICAL_UPSTREAM_COMMIT,
            },
            "integration_policy": (
                "local structured family/subtype/specification wins; name rules and "
                "generic suffixes remain review candidates"
            ),
        },
        "pipeline_version": PIPELINE_VERSION,
        "input_path": str(input_path),
        "candidate_path": str(candidate_path),
        "glossary_path": str(glossary_path),
        "review_queue_path": str(queue_path),
        "parent_concept_path": str(parent_path),
        "rows": int(len(candidates)),
        "input_rows": int(input_rows),
        "family_basis": _value_counts(candidates["family_basis"]),
        "family_source": _value_counts(candidates["family_source"]),
        "family_resolution_status": _value_counts(candidates["family_resolution_status"]),
        "family_conflicts": int(
            candidates["family_conflict_flag"]
            .astype("string")
            .str.lower()
            .eq("true")
            .sum()
        ),
        "family_review_status": _value_counts(candidates["family_review_status"]),
        "material_confidence": _value_counts(candidates["material_confidence"]),
        "material_evidence_tier": _value_counts(candidates["material_evidence_tier"]),
        "material_review_status": _value_counts(candidates["material_review_status"]),
        "activity_scope": _value_counts(candidates["activity_scope"]),
        "parent_concept_source": _value_counts(parents["parent_concept_source"]),
        "unique_parent_concepts": int(parents["parent_concept_id"].nunique()),
        "unique_meta_codes": {
            column: _unique_meta_code_count(candidates[column]) for column in META_CODE_COLUMNS
        },
        "glossary_rows": int(len(glossary)),
        "hangul_meta_code_rows": int(hangul_meta_codes),
        "sentinel_material_code_rows": sentinel_leaks,
        "operational_mapping_rows": 0,
        "quality_gates": {
            "representative_rows_preserved": bool(len(candidates) == input_rows),
            "representative_ids_unique": not bool(
                candidates["representative_item_id"].duplicated().any()
            ),
            "parent_concept_rows_preserved": bool(len(parents) == input_rows),
            "parent_concept_ids_match": bool(candidate_ids == parent_ids),
            "sentinel_material_codes_blocked": bool(sentinel_leaks == 0),
            "all_materials_review_only": bool(
                candidates["material_review_status"].eq("needs_review").all()
            ),
        },
        "operational_gate": (
            "Only separately approved data/mapping/stock_item_material_mapping.csv rows "
            "may enter news or commodity risk scoring."
        ),
        "limitations": [
            "identified means a family or ingredient was identified; it does not mean product-level material verification.",
            "family_rule_candidate and subtype_rule_candidate are hypotheses requiring product evidence.",
            "group_coarse and naming_pattern_unverified values must not be promoted as precise materials.",
        ],
    }


def run_material_pipeline(
    input_path: Path = ITEM_PRODUCT_WORKLIST_PATH,
    output_dir: Path = ITEM_MATERIAL_OUTPUT_DIR,
    with_excel: bool = False,
    attributes_path: Path | None = ITEM_REPRESENTATIVE_ATTRIBUTES_PATH,
    drug_ingredient_path: Path | None = DRUG_INGREDIENT_ENRICHMENT_PATH,
) -> dict[str, object]:
    setup_logging()
    if not input_path.exists():
        raise FileNotFoundError(
            f"Representative item input not found: {input_path}. Run item enrichment first."
        )
    run_script = ITEM_MATERIAL_PIPELINE_DIR / "run_all.sh"
    if not run_script.exists():
        raise FileNotFoundError(f"Material pipeline runner not found: {run_script}")
    ensure_dirs(output_dir)
    prepared_path, preparation = prepare_material_input(
        input_path=input_path,
        output_dir=output_dir,
        attributes_path=attributes_path,
        drug_ingredient_path=drug_ingredient_path,
    )
    env = os.environ.copy()
    env["PYTHON"] = sys.executable
    env["PIPE_SKIP_EXCEL"] = "0" if with_excel else "1"
    LOGGER.info("Running combined item material candidate pipeline: %s", prepared_path)
    subprocess.run(
        ["bash", str(run_script), str(prepared_path), str(output_dir)],
        check=True,
        env=env,
    )

    candidate_path = output_dir / ITEM_MATERIAL_EVENT_CANDIDATE_PATH.name
    glossary_path = output_dir / ITEM_MATERIAL_GLOSSARY_PATH.name
    queue_path = output_dir / ITEM_MATERIAL_REVIEW_QUEUE_PATH.name
    parent_path = output_dir / ITEM_PARENT_CONCEPT_PATH.name
    report = validate_material_pipeline_output(
        candidate_path=candidate_path,
        input_path=prepared_path,
        glossary_path=glossary_path,
        queue_path=queue_path,
        parent_path=parent_path,
    )
    report["source_input_path"] = str(input_path)
    report["input_preparation"] = preparation
    report["xlsx_generated"] = (output_dir / "meta_code_glossary_full.xlsx").exists()
    report_path = output_dir / ITEM_MATERIAL_PIPELINE_REPORT_PATH.name
    write_json(report, report_path)
    LOGGER.info("Saved material pipeline report: %s", report_path)
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build review-only item material and risk meta-code candidates."
    )
    parser.add_argument("--input", type=Path, default=ITEM_PRODUCT_WORKLIST_PATH)
    parser.add_argument("--attributes", type=Path, default=ITEM_REPRESENTATIVE_ATTRIBUTES_PATH)
    parser.add_argument("--output-dir", type=Path, default=ITEM_MATERIAL_OUTPUT_DIR)
    parser.add_argument(
        "--with-excel",
        action="store_true",
        help="Generate the optional xlsx glossary when openpyxl is installed.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_material_pipeline(
        input_path=args.input,
        output_dir=args.output_dir,
        with_excel=args.with_excel,
        attributes_path=args.attributes,
    )
