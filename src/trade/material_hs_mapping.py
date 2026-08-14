from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import unicodedata

import pandas as pd

from ..config import (
    DRUG_INGREDIENT_ENRICHMENT_PATH,
    HSK_REFERENCE_NORMALIZED_PATH,
    MATERIAL_HS_MAPPING_PATH,
    MATERIAL_HS_MAPPING_REPORT_PATH,
    MATERIAL_HS_MAPPING_SAMPLE_PATH,
)
from ..utils import ensure_dirs, write_json


MAPPING_VERSION = "material-hs-hsk2026-drug-v2.0"
OUTPUT_COLUMNS = [
    "raw_material_meta_code",
    "hs_code",
    "hs_item_name_ko",
    "relation_type",
    "mapping_weight",
    "proxy_quality",
    "review_status",
    "approval_basis",
    "evidence_reference",
    "valid_from",
    "valid_to",
    "mapping_version",
]

# Generic materials can span several HSK leaf codes. Multiple rows preserve that
# uncertainty instead of pretending one product grade represents the whole family.
CURATED_PATHS = [
    ("POLYPROPYLENE_PP", "3902100000", "direct_raw_material", 1.00, 1.00),
    ("POLYVINYL_CHLORIDE_PVC", "3904100000", "material_family_proxy", 1.00, 0.75),
    ("POLYURETHANE_PU", "3909500000", "direct_raw_material", 1.00, 1.00),
    ("ALUMINUM", "7601100000", "upstream_material_proxy", 1.00, 0.80),
    ("POLYETHYLENE_PE", "3901101000", "material_grade_proxy", 0.50, 0.70),
    ("POLYETHYLENE_PE", "3901200000", "material_grade_proxy", 0.50, 0.70),
    ("PARAFFIN_PETROLEUM", "2710197320", "material_grade_proxy", 0.50, 0.65),
    ("PARAFFIN_PETROLEUM", "2712200000", "material_grade_proxy", 0.50, 0.65),
    ("COTTON_FIBER", "5201001000", "material_grade_proxy", 0.50, 0.70),
    ("COTTON_FIBER", "5203000000", "material_grade_proxy", 0.50, 0.70),
    ("POLYESTER_FIBER", "5501200000", "upstream_material_proxy", 1.00, 0.80),
    ("REFINED_SUGAR", "1701990000", "material_family_proxy", 1.00, 0.75),
    ("GLUCOSE_CORNSTARCH", "1702301000", "material_grade_proxy", 0.50, 0.70),
    ("GLUCOSE_CORNSTARCH", "1702401000", "material_grade_proxy", 0.50, 0.70),
    ("SODIUM_CHLORIDE", "2501009020", "direct_raw_material", 1.00, 1.00),
]


def _normalize_exact(value: object) -> str:
    text = (
        unicodedata.normalize("NFKD", str(value or ""))
        .encode("ascii", "ignore")
        .decode("ascii")
        .casefold()
    )
    return re.sub(r"[^a-z0-9]+", "", text)


def _reference_by_code(reference: pd.DataFrame) -> pd.DataFrame:
    required = {
        "hs_code",
        "item_name_ko",
        "item_name_en",
        "valid_from",
        "valid_to",
        "is_trade_leaf",
    }
    missing = required - set(reference.columns)
    if missing:
        raise ValueError(f"HSK reference is missing columns: {sorted(missing)}")
    leaf = reference[reference["is_trade_leaf"].astype(bool)].copy()
    leaf["hs_code"] = leaf["hs_code"].astype("string").str.strip()
    if leaf["hs_code"].duplicated().any():
        raise ValueError("HSK reference contains duplicate active leaf codes")
    return leaf.set_index("hs_code")


def build_material_hs_mapping(
    reference: pd.DataFrame,
    drug_ingredients: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    by_code = _reference_by_code(reference)
    rows: list[dict[str, object]] = []

    def append_row(
        material_code: str,
        hs_code: str,
        relation_type: str,
        mapping_weight: float,
        proxy_quality: float,
        approval_basis: str,
        extra_evidence: str = "",
    ) -> None:
        if hs_code not in by_code.index:
            raise ValueError(f"Approved HSK code is absent from reference: {hs_code}")
        item = by_code.loc[hs_code]
        evidence = (
            f"{Path(str(item.get('source_file', '관세청_HS부호_20260101.xlsx'))).name}"
            f"#{hs_code}"
        )
        if extra_evidence:
            evidence = f"{evidence};{extra_evidence}"
        rows.append(
            {
                "raw_material_meta_code": material_code,
                "hs_code": hs_code,
                "hs_item_name_ko": item["item_name_ko"],
                "relation_type": relation_type,
                "mapping_weight": mapping_weight,
                "proxy_quality": proxy_quality,
                "review_status": "approved",
                "approval_basis": approval_basis,
                "evidence_reference": evidence,
                "valid_from": pd.Timestamp(item["valid_from"]).date().isoformat(),
                "valid_to": pd.Timestamp(item["valid_to"]).date().isoformat(),
                "mapping_version": MAPPING_VERSION,
            }
        )

    for material_code, hs_code, relation, weight, quality in CURATED_PATHS:
        append_row(
            material_code,
            hs_code,
            relation,
            weight,
            quality,
            "user_requested_curated_hsk_reference_approval",
        )

    approved = drug_ingredients[
        drug_ingredients["drug_ingredient_review_status"].eq("approved")
    ].copy()
    approved = approved[
        ["drug_raw_material_meta_code", "drug_ingredient_name"]
    ].drop_duplicates()
    approved["normalized_name"] = approved["drug_ingredient_name"].map(
        _normalize_exact
    )

    hsk_names = by_code.reset_index()[
        ["hs_code", "item_name_en"]
    ].drop_duplicates()
    hsk_names["normalized_name"] = hsk_names["item_name_en"].map(_normalize_exact)
    name_counts = hsk_names.groupby("normalized_name")["hs_code"].nunique()
    unique_names = set(name_counts[name_counts.eq(1)].index) - {""}
    hsk_names = hsk_names[hsk_names["normalized_name"].isin(unique_names)]
    exact = approved.merge(
        hsk_names,
        on="normalized_name",
        how="inner",
        validate="many_to_one",
    )
    exact = exact.drop_duplicates(
        ["drug_raw_material_meta_code", "hs_code"]
    )
    for row in exact.itertuples(index=False):
        append_row(
            row.drug_raw_material_meta_code,
            row.hs_code,
            "direct_active_ingredient",
            1.0,
            1.0,
            "user_requested_exact_government_ingredient_hsk_approval",
            f"drug_ingredient_name={row.drug_ingredient_name}",
        )

    mapping = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    mapping = (
        mapping.drop_duplicates(["raw_material_meta_code", "hs_code"])
        .sort_values(["raw_material_meta_code", "hs_code"], kind="stable")
        .reset_index(drop=True)
    )
    if mapping[["raw_material_meta_code", "hs_code"]].eq("").any(axis=None):
        raise ValueError("Material-HS mapping contains blank approved keys")
    if not mapping["hs_code"].str.fullmatch(r"\d{10}").all():
        raise ValueError("Material-HS mapping requires 10-digit HSK codes")
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mapping_version": MAPPING_VERSION,
        "approved_mapping_rows": int(len(mapping)),
        "approved_material_codes": int(mapping["raw_material_meta_code"].nunique()),
        "approved_hs_codes": int(mapping["hs_code"].nunique()),
        "curated_mapping_rows": len(CURATED_PATHS),
        "exact_drug_ingredient_mapping_rows": int(
            mapping["relation_type"].eq("direct_active_ingredient").sum()
        ),
        "unmatched_approved_drug_material_codes": int(
            approved["drug_raw_material_meta_code"].nunique()
            - exact["drug_raw_material_meta_code"].nunique()
        ),
        "approval_scope": (
            "all generated mappings are approved; unmatched ingredients remain "
            "identity-approved without an invented HS code"
        ),
    }
    return mapping, report


def run_material_hs_mapping_update(
    reference_path: Path = HSK_REFERENCE_NORMALIZED_PATH,
    drug_ingredient_path: Path = DRUG_INGREDIENT_ENRICHMENT_PATH,
    output_path: Path = MATERIAL_HS_MAPPING_PATH,
    report_path: Path = MATERIAL_HS_MAPPING_REPORT_PATH,
    sample_path: Path = MATERIAL_HS_MAPPING_SAMPLE_PATH,
) -> dict[str, object]:
    reference = pd.read_parquet(reference_path)
    drug_ingredients = pd.read_parquet(drug_ingredient_path)
    mapping, report = build_material_hs_mapping(reference, drug_ingredients)
    ensure_dirs(output_path.parent, report_path.parent, sample_path.parent)
    mapping.to_csv(output_path, index=False)
    mapping.head(1000).to_csv(sample_path, index=False, encoding="utf-8-sig")
    report["outputs"] = {
        "mapping": str(output_path),
        "sample": str(sample_path),
        "report": str(report_path),
    }
    write_json(report, report_path)
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build approved material-HSK paths from official references"
    )
    parser.add_argument("--reference", type=Path, default=HSK_REFERENCE_NORMALIZED_PATH)
    parser.add_argument(
        "--drug-ingredients",
        type=Path,
        default=DRUG_INGREDIENT_ENRICHMENT_PATH,
    )
    parser.add_argument("--output", type=Path, default=MATERIAL_HS_MAPPING_PATH)
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    print(
        run_material_hs_mapping_update(
            reference_path=args.reference,
            drug_ingredient_path=args.drug_ingredients,
            output_path=args.output,
        )
    )
