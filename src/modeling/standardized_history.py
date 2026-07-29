import hashlib
import json
import logging
from pathlib import Path

import pandas as pd

from ..config import (
    HISTORICAL_MONTHLY_STOCK_PATH,
    ITEM_ALIAS_CANDIDATE_PATH,
    ITEM_ALIAS_TO_PRODUCT_PATH,
    ITEM_INTEGRATED_CLASSIFICATION_PARQUET_PATH,
    MONTHLY_STOCK_PATH,
    STANDARD_ITEM_MAPPING_PATH,
    STANDARD_ITEM_MAPPING_REPORT_PATH,
    STANDARD_ITEM_MAPPING_SAMPLE_PATH,
)
from ..item_enrichment import normalize_match_name
from ..item_normalization import AliasStats, normalize_alias
from ..utils import ensure_dirs


LOGGER = logging.getLogger(__name__)
MAPPING_VERSION = "standard-item-history-v1.0"
STANDARD_ITEM_FEATURE_COLUMNS = [
    "standard_item_key",
    "standard_item_definition_key",
    "standard_item_group_id",
    "standard_item_family_id",
    "standard_item_subtype_id",
    "standard_item_specification",
    "standard_item_unit_code",
    "standardization_match_method",
    "standardization_confidence",
    "historical_training_eligible",
]


def _text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _representative_item_id(match_name: str) -> str:
    digest = hashlib.sha256(match_name.encode("utf-8")).hexdigest()[:20]
    return f"ITEM_{digest}"


def _read_catalog(path: Path, data_period: str) -> pd.DataFrame:
    catalog = pd.read_parquet(
        path,
        columns=["institution_code", "item_code", "item_name"],
    ).drop_duplicates(["institution_code", "item_code"])
    catalog["data_period"] = data_period
    catalog["local_item_key"] = (
        catalog["institution_code"].astype(str)
        + "::"
        + catalog["item_code"].astype(str)
    )
    return catalog.rename(columns={"item_name": "raw_item_name"})


def _historical_candidates(catalog: pd.DataFrame) -> pd.DataFrame:
    records = []
    for row in catalog.itertuples(index=False):
        normalized = normalize_alias(
            AliasStats(
                institution_id=str(row.institution_code),
                local_item_code=str(row.item_code),
                raw_item_name=str(row.raw_item_name),
                example_department="",
                occurrence_count=1,
                usage_sum=0.0,
                first_seen_date="",
                last_seen_date="",
            )
        )
        product_name = _text(normalized["product_name_candidate"])
        strict_name = normalize_match_name(product_name)
        records.append(
            {
                "data_period": "historical",
                "local_item_key": str(row.local_item_key),
                "raw_item_name": str(row.raw_item_name),
                "product_name_candidate": product_name,
                "representative_item_id": _representative_item_id(strict_name),
                "match_name_core": normalize_match_name(
                    product_name,
                    remove_parenthetical=True,
                    remove_trailing_pack=True,
                ),
                "fallback_group_id": _text(
                    normalized["item_group_id_candidate"]
                ),
                "fallback_family_id": _text(
                    normalized["item_family_id_candidate"]
                ),
                "fallback_subtype_id": _text(
                    normalized["item_subtype_id_candidate"]
                ),
                "fallback_specification": _text(
                    normalized["normalized_specification_candidate"]
                ),
                "fallback_unit_code": _text(
                    normalized["standard_unit_candidate"]
                ),
            }
        )
    return pd.DataFrame.from_records(records)


def _current_candidates(catalog: pd.DataFrame) -> pd.DataFrame:
    links = pd.read_parquet(
        ITEM_ALIAS_TO_PRODUCT_PATH,
        columns=[
            "local_item_key",
            "product_name_candidate",
            "representative_item_id",
        ],
    )
    aliases = pd.read_parquet(
        ITEM_ALIAS_CANDIDATE_PATH,
        columns=[
            "local_item_key",
            "item_group_id_candidate",
            "item_family_id_candidate",
            "item_subtype_id_candidate",
            "normalized_specification_candidate",
            "standard_unit_candidate",
        ],
    ).rename(
        columns={
            "item_group_id_candidate": "fallback_group_id",
            "item_family_id_candidate": "fallback_family_id",
            "item_subtype_id_candidate": "fallback_subtype_id",
            "normalized_specification_candidate": "fallback_specification",
            "standard_unit_candidate": "fallback_unit_code",
        }
    )
    if links["local_item_key"].duplicated().any():
        raise ValueError("Current alias-to-product mapping is not unique")
    if aliases["local_item_key"].duplicated().any():
        raise ValueError("Current alias candidate mapping is not unique")

    result = catalog.merge(
        links,
        on="local_item_key",
        how="left",
        validate="one_to_one",
    ).merge(
        aliases,
        on="local_item_key",
        how="left",
        validate="one_to_one",
    )
    if result["representative_item_id"].isna().any():
        raise ValueError("Current catalog is missing representative item links")
    result["match_name_core"] = result["product_name_candidate"].map(
        lambda value: normalize_match_name(
            value,
            remove_parenthetical=True,
            remove_trailing_pack=True,
        )
    )
    return result[
        [
            "data_period",
            "local_item_key",
            "raw_item_name",
            "product_name_candidate",
            "representative_item_id",
            "match_name_core",
            "fallback_group_id",
            "fallback_family_id",
            "fallback_subtype_id",
            "fallback_specification",
            "fallback_unit_code",
        ]
    ]


def _integrated_standard_items() -> pd.DataFrame:
    integrated = pd.read_parquet(
        ITEM_INTEGRATED_CLASSIFICATION_PARQUET_PATH,
        columns=[
            "representative_item_id",
            "representative_name",
            "match_name_core",
            "item_group_id_candidate",
            "effective_item_family_id",
            "effective_item_subtype_id",
            "effective_specification",
            "effective_unit_code",
            "forecast_series_definition_key",
            "classification_selected_item_family_id",
            "classification_selected_item_subtype_id",
            "classification_selected_specification",
            "classification_selected_unit_code",
            "classification_classification_status",
        ],
    )
    if integrated["representative_item_id"].duplicated().any():
        raise ValueError("Integrated classification is not unique by representative item")

    status = integrated["classification_classification_status"].fillna("")
    definition = integrated["forecast_series_definition_key"].fillna("")
    semantic = (
        definition.ne("")
        & ~status.isin(["conflict", "unresolved", "group_only"])
    )
    integrated["resolved_standard_item_key"] = (
        "NAME::" + integrated["representative_item_id"].astype(str)
    )
    integrated["resolved_definition_key"] = "UNRESOLVED_DEFINITION"
    integrated.loc[definition.ne(""), "resolved_definition_key"] = (
        "DEF::" + definition.loc[definition.ne("")].astype(str)
    )

    selected_family = integrated[
        "classification_selected_item_family_id"
    ].fillna("")
    selected_subtype = integrated[
        "classification_selected_item_subtype_id"
    ].fillna("")
    selected_specification = integrated[
        "classification_selected_specification"
    ].fillna("")
    selected_unit = integrated[
        "classification_selected_unit_code"
    ].fillna("")
    integrated["resolved_group_id"] = integrated[
        "item_group_id_candidate"
    ].fillna("")
    integrated["resolved_family_id"] = selected_family.where(
        selected_family.ne(""),
        integrated["effective_item_family_id"].fillna(""),
    )
    integrated["resolved_subtype_id"] = selected_subtype.where(
        selected_subtype.ne(""),
        integrated["effective_item_subtype_id"].fillna(""),
    )
    integrated["resolved_specification"] = selected_specification.where(
        selected_specification.ne(""),
        integrated["effective_specification"].fillna(""),
    )
    integrated["resolved_unit_code"] = selected_unit.where(
        selected_unit.ne(""),
        integrated["effective_unit_code"].fillna(""),
    )
    integrated["semantic_definition_applied"] = semantic
    return integrated[
        [
            "representative_item_id",
            "representative_name",
            "match_name_core",
            "resolved_standard_item_key",
            "resolved_definition_key",
            "resolved_group_id",
            "resolved_family_id",
            "resolved_subtype_id",
            "resolved_specification",
            "resolved_unit_code",
            "semantic_definition_applied",
        ]
    ]


def _unique_core_mapping(integrated: pd.DataFrame) -> pd.DataFrame:
    nonempty = integrated[integrated["match_name_core"].fillna("").ne("")].copy()
    key_counts = nonempty.groupby("match_name_core")[
        "resolved_standard_item_key"
    ].nunique()
    unique_cores = key_counts[key_counts.eq(1)].index
    return (
        nonempty[nonempty["match_name_core"].isin(unique_cores)]
        .sort_values(["match_name_core", "representative_item_id"])
        .drop_duplicates("match_name_core")
        .rename(
            columns={
                "representative_item_id": "core_representative_item_id",
                "representative_name": "core_representative_name",
                "resolved_standard_item_key": "core_standard_item_key",
                "resolved_definition_key": "core_definition_key",
                "resolved_group_id": "core_group_id",
                "resolved_family_id": "core_family_id",
                "resolved_subtype_id": "core_subtype_id",
                "resolved_specification": "core_specification",
                "resolved_unit_code": "core_unit_code",
                "semantic_definition_applied": "core_semantic_definition_applied",
            }
        )
    )


def _resolve_candidates(
    candidates: pd.DataFrame,
    integrated: pd.DataFrame,
) -> pd.DataFrame:
    resolved = candidates.merge(
        integrated.drop(columns=["match_name_core"]),
        on="representative_item_id",
        how="left",
        validate="many_to_one",
    )
    core = _unique_core_mapping(integrated)
    resolved = resolved.merge(
        core,
        on="match_name_core",
        how="left",
        validate="many_to_one",
    )

    strict_match = resolved["resolved_standard_item_key"].notna()
    core_match = ~strict_match & resolved["core_standard_item_key"].notna()
    fallback_key = "NAME::" + resolved["representative_item_id"].astype(str)
    resolved["standard_item_key"] = resolved[
        "resolved_standard_item_key"
    ].where(
        strict_match,
        resolved["core_standard_item_key"].where(core_match, fallback_key),
    )
    fallback_definition = (
        "GROUP::"
        + resolved["fallback_group_id"].fillna("").replace("", "UNCLASSIFIED")
        + "::"
        + resolved["fallback_family_id"].fillna("").replace("", "UNSPECIFIED_FAMILY")
        + "::"
        + resolved["fallback_subtype_id"].fillna("").replace("", "UNSPECIFIED_SUBTYPE")
        + "::"
        + resolved["fallback_specification"].fillna("").replace("", "UNSPECIFIED_SPEC")
        + "::"
        + resolved["fallback_unit_code"].fillna("").replace("", "UNSPECIFIED_UNIT")
    )
    resolved["standard_item_definition_key"] = resolved[
        "resolved_definition_key"
    ].where(
        strict_match,
        resolved["core_definition_key"].where(
            core_match,
            fallback_definition,
        ),
    )

    field_specs = {
        "standard_item_group_id": (
            "resolved_group_id",
            "core_group_id",
            "fallback_group_id",
        ),
        "standard_item_family_id": (
            "resolved_family_id",
            "core_family_id",
            "fallback_family_id",
        ),
        "standard_item_subtype_id": (
            "resolved_subtype_id",
            "core_subtype_id",
            "fallback_subtype_id",
        ),
        "standard_item_specification": (
            "resolved_specification",
            "core_specification",
            "fallback_specification",
        ),
        "standard_item_unit_code": (
            "resolved_unit_code",
            "core_unit_code",
            "fallback_unit_code",
        ),
    }
    for output, (strict_column, core_column, fallback_column) in field_specs.items():
        resolved[output] = resolved[strict_column].where(
            strict_match,
            resolved[core_column].where(core_match, resolved[fallback_column]),
        ).fillna("")

    current = resolved["data_period"].eq("current")
    semantic = resolved["semantic_definition_applied"].eq(True)
    core_semantic = resolved["core_semantic_definition_applied"].eq(True)
    resolved["standardization_match_method"] = "historical_name_fallback"
    resolved.loc[current & strict_match & semantic, "standardization_match_method"] = (
        "current_semantic_definition"
    )
    resolved.loc[current & strict_match & ~semantic, "standardization_match_method"] = (
        "current_representative_name"
    )
    resolved.loc[~current & strict_match & semantic, "standardization_match_method"] = (
        "historical_strict_semantic"
    )
    resolved.loc[~current & strict_match & ~semantic, "standardization_match_method"] = (
        "historical_strict_name"
    )
    resolved.loc[~current & core_match & core_semantic, "standardization_match_method"] = (
        "historical_core_semantic"
    )
    resolved.loc[~current & core_match & ~core_semantic, "standardization_match_method"] = (
        "historical_core_name"
    )
    confidence = {
        "current_semantic_definition": 1.0,
        "current_representative_name": 0.9,
        "historical_strict_semantic": 0.95,
        "historical_strict_name": 0.9,
        "historical_core_semantic": 0.8,
        "historical_core_name": 0.75,
        "historical_name_fallback": 0.5,
    }
    resolved["standardization_confidence"] = (
        resolved["standardization_match_method"].map(confidence).astype("float32")
    )
    resolved["historical_training_eligible"] = (
        current | strict_match | core_match
    )
    resolved["standard_item_mapping_version"] = MAPPING_VERSION
    output_columns = [
        "data_period",
        "local_item_key",
        "raw_item_name",
        "product_name_candidate",
        "representative_item_id",
        *STANDARD_ITEM_FEATURE_COLUMNS,
        "standard_item_mapping_version",
    ]
    return resolved[output_columns].sort_values(
        ["data_period", "local_item_key"]
    ).reset_index(drop=True)


def _mapping_sources() -> list[Path]:
    return [
        MONTHLY_STOCK_PATH,
        HISTORICAL_MONTHLY_STOCK_PATH,
        ITEM_ALIAS_CANDIDATE_PATH,
        ITEM_ALIAS_TO_PRODUCT_PATH,
        ITEM_INTEGRATED_CLASSIFICATION_PARQUET_PATH,
    ]


def mapping_is_current(path: Path = STANDARD_ITEM_MAPPING_PATH) -> bool:
    if not path.exists() or not STANDARD_ITEM_MAPPING_REPORT_PATH.exists():
        return False
    output_mtime = path.stat().st_mtime
    return all(source.exists() and source.stat().st_mtime <= output_mtime for source in _mapping_sources())


def build_standard_item_mapping(
    force: bool = False,
) -> tuple[pd.DataFrame, dict[str, object]]:
    if not force and mapping_is_current():
        mapping = pd.read_parquet(STANDARD_ITEM_MAPPING_PATH)
        report = json.loads(
            STANDARD_ITEM_MAPPING_REPORT_PATH.read_text(encoding="utf-8")
        )
        return mapping, report

    ensure_dirs(
        STANDARD_ITEM_MAPPING_PATH.parent,
        STANDARD_ITEM_MAPPING_REPORT_PATH.parent,
        STANDARD_ITEM_MAPPING_SAMPLE_PATH.parent,
    )
    current_catalog = _read_catalog(MONTHLY_STOCK_PATH, "current")
    historical_catalog = _read_catalog(
        HISTORICAL_MONTHLY_STOCK_PATH,
        "historical",
    )
    current_candidates = _current_candidates(current_catalog)
    LOGGER.info("Normalizing %s historical local items", len(historical_catalog))
    historical_candidates = _historical_candidates(historical_catalog)
    integrated = _integrated_standard_items()
    mapping = _resolve_candidates(
        pd.concat(
            [current_candidates, historical_candidates],
            ignore_index=True,
        ),
        integrated,
    )
    if mapping.duplicated(["data_period", "local_item_key"]).any():
        raise ValueError("Standard item mapping is not unique by period and local key")
    if mapping[STANDARD_ITEM_FEATURE_COLUMNS].isna().any().any():
        raise ValueError("Standard item mapping contains missing feature values")

    historical = mapping[mapping["data_period"].eq("historical")]
    report = {
        "version": MAPPING_VERSION,
        "mapping_rows": int(len(mapping)),
        "current_local_items": int(mapping["data_period"].eq("current").sum()),
        "historical_local_items": int(len(historical)),
        "historical_training_eligible_items": int(
            historical["historical_training_eligible"].sum()
        ),
        "historical_training_eligible_pct": float(
            historical["historical_training_eligible"].mean() * 100
        ),
        "standard_item_count": int(mapping["standard_item_key"].nunique()),
        "semantic_definition_mapping_rows": int(
            mapping["standard_item_definition_key"].str.startswith("DEF::").sum()
        ),
        "match_method_counts": {
            str(key): int(value)
            for key, value in mapping[
                "standardization_match_method"
            ].value_counts().items()
        },
        "historical_unmatched_items_excluded_from_training": int(
            (~historical["historical_training_eligible"]).sum()
        ),
        "guardrails": [
            "Physical stock series remain institution-department-local-code based.",
            "A 2019 to 2024 gap starts a new lag segment.",
            "Historical-only names are retained for audit but excluded from model fitting.",
            "Core-name matching is allowed only when it resolves to one standard item key.",
        ],
    }
    mapping.to_parquet(
        STANDARD_ITEM_MAPPING_PATH,
        index=False,
        compression="zstd",
    )
    sample = (
        mapping.assign(
            _priority=mapping["data_period"].eq("historical").astype(int)
        )
        .sort_values(
            ["_priority", "standardization_match_method", "local_item_key"],
            ascending=[False, True, True],
        )
        .drop(columns="_priority")
        .head(1000)
    )
    sample.to_csv(STANDARD_ITEM_MAPPING_SAMPLE_PATH, index=False)
    STANDARD_ITEM_MAPPING_REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    LOGGER.info(
        "Saved standard item mapping: %s (%s rows)",
        STANDARD_ITEM_MAPPING_PATH,
        len(mapping),
    )
    return mapping, report


def attach_standard_item_features(
    monthly_stock: pd.DataFrame,
    mapping: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if "data_period" not in monthly_stock.columns:
        raise ValueError("Monthly stock requires data_period before standardization")
    mapping = mapping if mapping is not None else build_standard_item_mapping()[0]
    result = monthly_stock.copy()
    result["local_item_key"] = (
        result["institution_code"].astype(str)
        + "::"
        + result["item_code"].astype(str)
    )
    result = result.merge(
        mapping[
            [
                "data_period",
                "local_item_key",
                *STANDARD_ITEM_FEATURE_COLUMNS,
            ]
        ],
        on=["data_period", "local_item_key"],
        how="left",
        validate="many_to_one",
    )
    if result[STANDARD_ITEM_FEATURE_COLUMNS].isna().any().any():
        examples = result.loc[
            result["standard_item_key"].isna(),
            ["data_period", "local_item_key"],
        ].head(10)
        raise ValueError(
            "Monthly stock has missing standard item joins: "
            f"{examples.to_dict('records')}"
        )
    return result


if __name__ == "__main__":
    build_standard_item_mapping(force=True)
