from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path
import re
import unicodedata

import pandas as pd

from .config import (
    DRUG_INGREDIENT_DICTIONARY_PATH,
    DRUG_INGREDIENT_ENRICHMENT_PATH,
    DRUG_INGREDIENT_REPORT_PATH,
    DRUG_INGREDIENT_SAMPLE_PATH,
    DRUG_INGREDIENT_SOURCE_PATH,
    ITEM_ALIAS_TO_PRODUCT_PATH,
)
from .utils import ensure_dirs, write_json


SOURCE_COLUMNS = [
    "약품코드",
    "약품명1",
    "용도구분",
    "약종류구분",
    "약품단위1",
    "성분코드",
    "성분명",
    "보건기관코드_en",
]
PIPELINE_VERSION = "drug-ingredient-enrichment-v1.1"
STRENGTH_UNIT = (
    r"(?:mcg|μg|µg|ug|mg|kg|g|ml|mL|l|%|iu|i\.u\.?|u|unit|units)"
)
PARENTHETICAL_STRENGTH_AT_END = re.compile(
    rf"(?i)\((?P<strength>[^()]*(?:\d+(?:\.\d+)?\s*{STRENGTH_UNIT})[^()]*)\)"
    r"\s*(?:이상|이하|미만|초과)?\s*$"
)
STRENGTH_AT_END = re.compile(
    r"(?i)(?P<strength>"
    r"\d+(?:\.\d+)?\s*"
    + STRENGTH_UNIT
    + r"(?:\s*/\s*[^,;()]*)?"
    r")\s*(?:이상|이하|미만|초과)?\s*$"
)
STRENGTH_TOKEN_ANYWHERE = re.compile(
    rf"(?i)\d+(?:\.\d+)?\s*{STRENGTH_UNIT}"
    rf"(?:\s*/\s*\d*(?:\.\d+)?\s*{STRENGTH_UNIT})?"
    r"\s*(?:이상|이하|미만|초과)?"
)


def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(text.replace("\r", " ").replace("\n", " ").split())


def normalize_match_name(value: object) -> str:
    return normalize_text(value).casefold()


def parse_ingredient_name(value: object) -> tuple[str, str]:
    raw = normalize_text(value)
    if not raw:
        return "", ""
    strengths: list[str] = []
    name = raw
    parenthetical = PARENTHETICAL_STRENGTH_AT_END.search(name)
    if parenthetical:
        strengths.append(normalize_text(parenthetical.group("strength")))
        name = name[: parenthetical.start()].rstrip()
    match = STRENGTH_AT_END.search(name)
    if match:
        strengths.insert(0, normalize_text(match.group("strength")))
        name = name[: match.start()].strip(" ,;/")
    return name or raw, ";".join(strengths)


def ingredient_meta_code(ingredient_name: object) -> str:
    name, _ = parse_ingredient_name(ingredient_name)
    name = STRENGTH_TOKEN_ANYWHERE.sub(" ", name)
    ascii_name = (
        unicodedata.normalize("NFKD", name)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    code = re.sub(r"[^A-Za-z0-9]+", "_", ascii_name).strip("_").upper()
    if not code or not re.search(r"[A-Z]", code):
        return ""
    if code[0].isdigit():
        code = f"API_{code}"
    return code


def _signature(row: dict[str, str]) -> tuple[str, str, str, str]:
    ingredient_name, strength = parse_ingredient_name(row.get("성분명", ""))
    return (
        normalize_text(row.get("성분코드", "")),
        ingredient_meta_code(ingredient_name),
        ingredient_name,
        strength,
    )


def _signature_is_usable(signature: tuple[str, str, str, str]) -> bool:
    ingredient_code, material_code, ingredient_name, _ = signature
    return bool((ingredient_code or ingredient_name) and material_code)


def _select_unique(
    candidates: set[tuple[str, str, str, str]] | None,
) -> tuple[str, str, str, str] | None:
    usable = {candidate for candidate in (candidates or set()) if _signature_is_usable(candidate)}
    identities = {(candidate[0], candidate[1], candidate[2]) for candidate in usable}
    if len(identities) != 1:
        return None
    strengths = sorted({candidate[3] for candidate in usable if candidate[3]})
    ingredient_code, material_code, ingredient_name = next(iter(identities))
    return ingredient_code, material_code, ingredient_name, ";".join(strengths)


def _read_aliases(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Item alias mapping not found: {path}")
    aliases = pd.read_parquet(path)
    required = {
        "institution_id",
        "local_item_code",
        "raw_item_name",
        "representative_item_id",
    }
    missing = required - set(aliases.columns)
    if missing:
        raise ValueError(f"Item alias mapping is missing columns: {sorted(missing)}")
    aliases = aliases[list(required)].copy()
    for column in required:
        aliases[column] = aliases[column].astype("string").fillna("").str.strip()
    if aliases[["institution_id", "local_item_code"]].eq("").any(axis=None):
        raise ValueError("Item alias mapping contains blank institution or item codes")
    return aliases


def build_drug_ingredient_outputs(
    source_path: Path = DRUG_INGREDIENT_SOURCE_PATH,
    alias_path: Path = ITEM_ALIAS_TO_PRODUCT_PATH,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    if not source_path.exists():
        raise FileNotFoundError(f"Drug ingredient source not found: {source_path}")
    aliases = _read_aliases(alias_path)
    aliases["match_name"] = aliases["raw_item_name"].map(normalize_match_name)

    target_keys = set(
        zip(aliases["institution_id"], aliases["local_item_code"], strict=False)
    )
    target_codes = set(aliases["local_item_code"])
    target_names = {value for value in aliases["match_name"] if value}

    exact_candidates: dict[tuple[str, str], set[tuple[str, str, str, str]]] = {}
    code_candidates: dict[str, set[tuple[str, str, str, str]]] = {}
    name_candidates: dict[str, set[tuple[str, str, str, str]]] = {}
    global_code_candidates: dict[str, set[tuple[str, str, str, str]]] = {}
    global_name_candidates: dict[str, set[tuple[str, str, str, str]]] = {}
    source_rows = 0
    multiline_names = 0

    with source_path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file, delimiter="|", quotechar='"')
        if reader.fieldnames != SOURCE_COLUMNS:
            raise ValueError(
                f"Unexpected drug ingredient header in {source_path}: {reader.fieldnames}"
            )
        for row in reader:
            if None in row:
                raise ValueError(
                    "Malformed drug ingredient record near physical line "
                    f"{reader.line_num}"
                )
            source_rows += 1
            multiline_names += int(
                "\n" in row["약품명1"] or "\r" in row["약품명1"]
            )
            institution = normalize_text(row["보건기관코드_en"])
            drug_code = normalize_text(row["약품코드"])
            drug_name = normalize_match_name(row["약품명1"])
            signature = _signature(row)
            if not _signature_is_usable(signature):
                continue

            global_code_candidates.setdefault(drug_code, set()).add(signature)
            if drug_name:
                global_name_candidates.setdefault(drug_name, set()).add(signature)

            key = (institution, drug_code)
            if key in target_keys:
                exact_candidates.setdefault(key, set()).add(signature)
            if drug_code in target_codes:
                code_candidates.setdefault(drug_code, set()).add(signature)
            if drug_name and drug_name in target_names:
                name_candidates.setdefault(drug_name, set()).add(signature)

    local_rows: list[dict[str, object]] = []
    for row in aliases.itertuples(index=False):
        key = (str(row.institution_id), str(row.local_item_code))
        signature = _select_unique(exact_candidates.get(key))
        method = "institution_item_code_exact"
        confidence = 1.0
        if signature is None:
            signature = _select_unique(code_candidates.get(str(row.local_item_code)))
            method = "drug_code_unique"
            confidence = 0.95
        if signature is None and row.match_name:
            signature = _select_unique(name_candidates.get(str(row.match_name)))
            method = "normalized_drug_name_unique"
            confidence = 0.85
        if signature is None:
            continue
        ingredient_code, material_code, ingredient_name, strengths = signature
        local_rows.append(
            {
                "representative_item_id": row.representative_item_id,
                "institution_id": row.institution_id,
                "local_item_code": row.local_item_code,
                "ingredient_code": ingredient_code,
                "raw_material_meta_code": material_code,
                "ingredient_name": ingredient_name,
                "ingredient_strengths": strengths,
                "match_method": method,
                "match_confidence": confidence,
            }
        )
    local = pd.DataFrame(local_rows)

    enrichment_rows: list[dict[str, object]] = []
    conflict_count = 0
    if not local.empty:
        for representative_id, group in local.groupby(
            "representative_item_id", sort=False, observed=True
        ):
            identities = group[
                ["ingredient_code", "raw_material_meta_code", "ingredient_name"]
            ].drop_duplicates()
            approved = len(identities) == 1
            conflict_count += int(not approved)
            identity = identities.iloc[0] if approved else None
            methods = sorted(group["match_method"].unique().tolist())
            strengths = sorted(
                {
                    strength
                    for values in group["ingredient_strengths"]
                    for strength in str(values).split(";")
                    if strength
                }
            )
            enrichment_rows.append(
                {
                    "representative_item_id": representative_id,
                    "drug_ingredient_code": (
                        identity["ingredient_code"] if identity is not None else ""
                    ),
                    "drug_raw_material_meta_code": (
                        identity["raw_material_meta_code"]
                        if identity is not None
                        else ""
                    ),
                    "drug_ingredient_name": (
                        identity["ingredient_name"] if identity is not None else ""
                    ),
                    "drug_ingredient_strengths": ";".join(strengths),
                    "drug_ingredient_match_methods": ";".join(methods),
                    "drug_ingredient_match_confidence": (
                        float(group["match_confidence"].min()) if approved else 0.0
                    ),
                    "drug_ingredient_local_item_count": int(len(group)),
                    "drug_ingredient_review_status": (
                        "approved" if approved else "blocked_identity_conflict"
                    ),
                    "drug_ingredient_evidence_reference": (
                        f"{source_path.name}#ingredient_code="
                        f"{identity['ingredient_code']}"
                        if approved
                        else f"{source_path.name}#conflicting_ingredient_identities"
                    ),
                    "drug_ingredient_approval_basis": (
                        "user_requested_government_dataset_identity_approval"
                        if approved
                        else "identity_conflict_requires_review"
                    ),
                    "drug_ingredient_version": PIPELINE_VERSION,
                }
            )
    enrichment = pd.DataFrame(enrichment_rows)

    dictionary_rows: list[dict[str, object]] = []
    for key_type, candidates in [
        ("drug_code", global_code_candidates),
        ("normalized_drug_name", global_name_candidates),
    ]:
        for key, values in candidates.items():
            signature = _select_unique(values)
            if signature is None:
                continue
            ingredient_code, material_code, ingredient_name, strengths = signature
            dictionary_rows.append(
                {
                    "dictionary_key_type": key_type,
                    "dictionary_key": key,
                    "ingredient_code": ingredient_code,
                    "raw_material_meta_code": material_code,
                    "ingredient_name": ingredient_name,
                    "ingredient_strengths": strengths,
                    "review_status": "approved",
                    "approval_basis": "user_requested_government_dataset_identity_approval",
                    "evidence_reference": (
                        f"{source_path.name}#unique_{key_type}={key}"
                    ),
                    "dictionary_version": PIPELINE_VERSION,
                }
            )
    dictionary = pd.DataFrame(dictionary_rows)

    method_counts = (
        local["match_method"].value_counts().to_dict() if not local.empty else {}
    )
    approved_representatives = (
        int(enrichment["drug_ingredient_review_status"].eq("approved").sum())
        if not enrichment.empty
        else 0
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": PIPELINE_VERSION,
        "source_path": str(source_path),
        "source_rows": source_rows,
        "source_multiline_drug_names": multiline_names,
        "alias_rows": int(len(aliases)),
        "matched_local_item_rows": int(len(local)),
        "match_method_counts": {str(key): int(value) for key, value in method_counts.items()},
        "representative_rows": int(len(enrichment)),
        "approved_representative_rows": approved_representatives,
        "blocked_representative_conflicts": conflict_count,
        "dictionary_rows": int(len(dictionary)),
        "approval_scope": (
            "ingredient identity is approved; market and HS signal paths require "
            "separate evidence-backed mappings"
        ),
    }
    return enrichment, dictionary, report


def attach_approved_drug_ingredients(
    items: pd.DataFrame,
    enrichment: pd.DataFrame,
) -> pd.DataFrame:
    if enrichment.empty:
        return items.copy()
    approved = enrichment[
        enrichment["drug_ingredient_review_status"].eq("approved")
    ].copy()
    if approved["representative_item_id"].duplicated().any():
        raise ValueError("Approved drug ingredient enrichment contains duplicate IDs")
    result = items.merge(
        approved,
        on="representative_item_id",
        how="left",
        validate="one_to_one",
    )
    has_drug = result["drug_raw_material_meta_code"].fillna("").astype(str).ne("")
    replacements = {
        "ingredient_ids": "drug_raw_material_meta_code",
        "ingredient_names": "drug_ingredient_name",
        "ingredient_source": None,
        "material_match_readiness": None,
    }
    for target, source in replacements.items():
        if target not in result.columns:
            result[target] = ""
        if source is not None:
            result.loc[has_drug, target] = result.loc[has_drug, source]
    result.loc[has_drug, "ingredient_source"] = (
        "government_drug_ingredient_dataset_approved"
    )
    result.loc[has_drug, "material_match_readiness"] = "verified_ingredient_ready"
    return result


def run_drug_ingredient_pipeline(
    source_path: Path = DRUG_INGREDIENT_SOURCE_PATH,
    alias_path: Path = ITEM_ALIAS_TO_PRODUCT_PATH,
    enrichment_path: Path = DRUG_INGREDIENT_ENRICHMENT_PATH,
    dictionary_path: Path = DRUG_INGREDIENT_DICTIONARY_PATH,
    sample_path: Path = DRUG_INGREDIENT_SAMPLE_PATH,
    report_path: Path = DRUG_INGREDIENT_REPORT_PATH,
    sample_size: int = 1000,
) -> dict[str, object]:
    enrichment, dictionary, report = build_drug_ingredient_outputs(
        source_path=source_path,
        alias_path=alias_path,
    )
    ensure_dirs(
        enrichment_path.parent,
        dictionary_path.parent,
        sample_path.parent,
        report_path.parent,
    )
    enrichment.to_parquet(enrichment_path, index=False, compression="zstd")
    dictionary.to_parquet(dictionary_path, index=False, compression="zstd")
    sample = (
        enrichment.sort_values(
            [
                "drug_ingredient_review_status",
                "drug_ingredient_local_item_count",
            ],
            ascending=[True, False],
        )
        .head(sample_size)
        .copy()
    )
    sample.to_csv(sample_path, index=False, encoding="utf-8-sig")
    report["outputs"] = {
        "enrichment": str(enrichment_path),
        "dictionary": str(dictionary_path),
        "sample": str(sample_path),
        "report": str(report_path),
    }
    write_json(report, report_path)
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build approved drug ingredient identity enrichment"
    )
    parser.add_argument("--source", type=Path, default=DRUG_INGREDIENT_SOURCE_PATH)
    parser.add_argument("--aliases", type=Path, default=ITEM_ALIAS_TO_PRODUCT_PATH)
    parser.add_argument(
        "--enrichment-output",
        type=Path,
        default=DRUG_INGREDIENT_ENRICHMENT_PATH,
    )
    parser.add_argument(
        "--dictionary-output",
        type=Path,
        default=DRUG_INGREDIENT_DICTIONARY_PATH,
    )
    parser.add_argument("--sample-size", type=int, default=1000)
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    print(
        run_drug_ingredient_pipeline(
            source_path=args.source,
            alias_path=args.aliases,
            enrichment_path=args.enrichment_output,
            dictionary_path=args.dictionary_output,
            sample_size=args.sample_size,
        )
    )
