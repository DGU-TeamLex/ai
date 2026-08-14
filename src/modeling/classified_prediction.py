import argparse
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..config import (
    APPROVED_ITEM_CLASSIFICATION_PATH,
    CLASSIFIED_PREDICTION_PATH,
    CLASSIFIED_PREDICTION_QUALITY_PATH,
    ITEM_FAMILY_TAXONOMY_PATH,
    OUTPUT_DIR,
    PREDICTION_PATH,
)
from ..utils import ensure_dirs, guard_not_empty, setup_logging, write_json


LOGGER = logging.getLogger(__name__)

TAXONOMY_REFERENCE_COLUMNS = [
    "item_family_id",
    "item_subtype_id",
    "normalized_specification",
    "unit_code",
    "taxonomy_version",
]
CLASSIFICATION_AUDIT_COLUMNS = [
    "review_status",
    "reviewer",
    "reviewed_at",
    "evidence_reference",
    "classification_version",
]
TAXONOMY_OUTPUT_COLUMNS = [
    "item_group_id",
    "is_forecastable",
    "standard_family_name",
    "standard_subtype_name",
    "unit_name",
]
VALID_ITEM_GROUP_IDS = {
    "MED_ORAL",
    "MED_INJECT",
    "MED_TOPICAL",
    "LAB_REAGENT",
    "DISINFECT",
    "MED_SUPPLY",
    "KM_EXTRACT",
    "KM_HERB",
    "SUPPLEMENT",
    "PROMO",
    "FUEL",
    "WASTE",
    "RENTAL",
    "UNCLASSIFIED",
}
QUANTITY_COLUMNS = [
    "actual_usage",
    "predicted_usage",
    "protection_period_demand",
    "safety_stock",
    "base_stock",
    "demand_risk_buffer",
    "supply_risk_buffer",
    "material_risk_buffer",
    "risk_buffer",
    "target_stock",
    "recommended_stock",
    "current_stock",
    "on_order_qty",
    "backorder_qty",
    "inventory_position",
    "recommended_order",
]
POLICY_DAY_COLUMNS = [
    "review_period_days",
    "lead_time_days",
    "protection_period_days",
]
RISK_SCORE_COLUMNS = [
    "demand_risk_score",
    "supply_risk_score",
    "material_risk_score",
    "external_risk_score",
]


class ClassificationValidationError(ValueError):
    """Raised when an approved classification cannot be used without ambiguity."""


@dataclass
class ClassificationLoadResult:
    mappings: pd.DataFrame
    input_rows: int
    approved_rows: int
    ignored_unapproved_rows: int
    approved_taxonomy_rows: int


def _read_mapping_table(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Classification input not found: {path}")
    if path.suffix.lower() in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _clean_text_columns(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    result = frame.copy()
    for column in columns:
        if column in result.columns:
            result[column] = result[column].astype("string").fillna("").str.strip()
    return result


def _require_columns(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ClassificationValidationError(
            f"{label} is missing required columns: {', '.join(missing)}"
        )


def _require_nonblank(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    blank_columns = [column for column in columns if frame[column].eq("").any()]
    if blank_columns:
        raise ClassificationValidationError(
            f"{label} has blank approved values in: {', '.join(blank_columns)}"
        )


def _parse_forecastable(series: pd.Series) -> pd.Series:
    normalized = series.astype("string").fillna("").str.strip().str.lower()
    values = {
        "t": True,
        "true": True,
        "1": True,
        "y": True,
        "yes": True,
        "f": False,
        "false": False,
        "0": False,
        "n": False,
        "no": False,
    }
    parsed = normalized.map(values)
    if parsed.isna().any():
        invalid = sorted(normalized[parsed.isna()].unique().tolist())
        raise ClassificationValidationError(
            f"taxonomy has invalid is_forecastable values: {invalid[:5]}"
        )
    return parsed.astype(bool)


def _normalize_local_keys(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    aliases = {
        "institution_id": "institution_code",
        "local_item_code": "item_code",
    }
    for source, target in aliases.items():
        if target not in result.columns and source in result.columns:
            result = result.rename(columns={source: target})

    if "local_item_key" not in result.columns:
        result["local_item_key"] = ""
    text_columns = ["local_item_key", "institution_code", "item_code"]
    result = _clean_text_columns(result, text_columns)

    has_components = {"institution_code", "item_code"}.issubset(result.columns)
    if has_components:
        component_blank = result["institution_code"].eq("") | result["item_code"].eq("")
        derived = result["institution_code"] + "::" + result["item_code"]
        fill_mask = result["local_item_key"].eq("") & ~component_blank
        result.loc[fill_mask, "local_item_key"] = derived[fill_mask]
        mismatch = ~result["local_item_key"].eq("") & ~component_blank & result[
            "local_item_key"
        ].ne(derived)
        if mismatch.any():
            examples = result.loc[mismatch, "local_item_key"].head(5).tolist()
            raise ClassificationValidationError(
                f"local_item_key disagrees with institution/item codes: {examples}"
            )

    invalid_key = (
        result["local_item_key"].eq("")
        # Item codes are opaque source values and can themselves contain "::".
        # The exact component check above is authoritative when those columns exist.
        | result["local_item_key"].str.count("::").lt(1)
        | result["local_item_key"].str.startswith("::")
        | result["local_item_key"].str.endswith("::")
    )
    if invalid_key.any():
        examples = result.loc[invalid_key, "local_item_key"].head(5).tolist()
        raise ClassificationValidationError(f"invalid local_item_key values: {examples}")
    return result


def load_approved_classifications(
    classification_path: Path = APPROVED_ITEM_CLASSIFICATION_PATH,
    taxonomy_path: Path = ITEM_FAMILY_TAXONOMY_PATH,
) -> ClassificationLoadResult:
    classifications = _read_mapping_table(classification_path)
    input_rows = len(classifications)
    _require_columns(
        classifications,
        [*TAXONOMY_REFERENCE_COLUMNS, *CLASSIFICATION_AUDIT_COLUMNS],
        "classification input",
    )
    classifications = _clean_text_columns(
        classifications,
        [*TAXONOMY_REFERENCE_COLUMNS, *CLASSIFICATION_AUDIT_COLUMNS],
    )
    approved_mask = classifications["review_status"].str.lower().eq("approved")
    ignored_unapproved_rows = int((~approved_mask).sum())
    approved = classifications.loc[approved_mask].copy()
    if approved.empty:
        return ClassificationLoadResult(
            mappings=pd.DataFrame(
                columns=[
                    "local_item_key",
                    *TAXONOMY_REFERENCE_COLUMNS,
                    *TAXONOMY_OUTPUT_COLUMNS,
                    *CLASSIFICATION_AUDIT_COLUMNS,
                ]
            ),
            input_rows=input_rows,
            approved_rows=0,
            ignored_unapproved_rows=ignored_unapproved_rows,
            approved_taxonomy_rows=0,
        )

    approved = _normalize_local_keys(approved)
    _require_nonblank(
        approved,
        ["local_item_key", *TAXONOMY_REFERENCE_COLUMNS, *CLASSIFICATION_AUDIT_COLUMNS[1:]],
        "classification input",
    )
    invalid_reviewed_at = pd.to_datetime(approved["reviewed_at"], errors="coerce", utc=True).isna()
    if invalid_reviewed_at.any():
        examples = approved.loc[invalid_reviewed_at, "reviewed_at"].head(5).tolist()
        raise ClassificationValidationError(f"invalid reviewed_at values: {examples}")
    if approved["local_item_key"].duplicated(keep=False).any():
        examples = (
            approved.loc[approved["local_item_key"].duplicated(keep=False), "local_item_key"]
            .drop_duplicates()
            .head(5)
            .tolist()
        )
        raise ClassificationValidationError(
            f"approved classification must be unique per local_item_key: {examples}"
        )

    taxonomy = _read_mapping_table(taxonomy_path)
    taxonomy_required = [
        *TAXONOMY_REFERENCE_COLUMNS,
        *TAXONOMY_OUTPUT_COLUMNS,
        "review_status",
    ]
    _require_columns(taxonomy, taxonomy_required, "taxonomy")
    taxonomy = _clean_text_columns(taxonomy, taxonomy_required)
    taxonomy = taxonomy[taxonomy["review_status"].str.lower().eq("approved")].copy()
    if not taxonomy.empty:
        _require_nonblank(
            taxonomy,
            [*TAXONOMY_REFERENCE_COLUMNS, *TAXONOMY_OUTPUT_COLUMNS],
            "taxonomy",
        )
        invalid_groups = sorted(set(taxonomy["item_group_id"]) - VALID_ITEM_GROUP_IDS)
        if invalid_groups:
            raise ClassificationValidationError(
                f"taxonomy has unknown item_group_id values: {invalid_groups[:5]}"
            )
        if taxonomy.duplicated(TAXONOMY_REFERENCE_COLUMNS, keep=False).any():
            duplicates = taxonomy.loc[
                taxonomy.duplicated(TAXONOMY_REFERENCE_COLUMNS, keep=False),
                TAXONOMY_REFERENCE_COLUMNS,
            ].head(5)
            raise ClassificationValidationError(
                "approved taxonomy references must be unique: "
                f"{duplicates.to_dict(orient='records')}"
            )
        taxonomy["is_forecastable"] = _parse_forecastable(taxonomy["is_forecastable"])

    # Taxonomy fields are authoritative. Bulk approval artifacts retain candidate
    # labels for audit, so remove any overlapping copies before the validated join.
    approved = approved.drop(
        columns=[
            column
            for column in TAXONOMY_OUTPUT_COLUMNS
            if column in approved.columns
        ]
    )
    approved = approved.rename(columns={"review_status": "classification_review_status"})
    taxonomy = taxonomy.rename(columns={"review_status": "taxonomy_review_status"})
    mappings = approved.merge(
        taxonomy[
            [
                *TAXONOMY_REFERENCE_COLUMNS,
                *TAXONOMY_OUTPUT_COLUMNS,
                "taxonomy_review_status",
            ]
        ],
        on=TAXONOMY_REFERENCE_COLUMNS,
        how="left",
        validate="many_to_one",
        indicator=True,
    )
    invalid_reference = mappings["_merge"].ne("both")
    if invalid_reference.any():
        examples = mappings.loc[
            invalid_reference,
            ["local_item_key", *TAXONOMY_REFERENCE_COLUMNS],
        ].head(5)
        raise ClassificationValidationError(
            "approved classifications reference missing or unapproved taxonomy rows: "
            f"{examples.to_dict(orient='records')}"
        )
    mappings = mappings.drop(columns="_merge")
    mappings["review_status"] = mappings.pop("classification_review_status")
    return ClassificationLoadResult(
        mappings=mappings,
        input_rows=input_rows,
        approved_rows=len(mappings),
        ignored_unapproved_rows=ignored_unapproved_rows,
        approved_taxonomy_rows=len(taxonomy),
    )


def _sum_with_min_count(series: pd.Series) -> float:
    return series.sum(min_count=1)


def _classified_output_columns(include_department: bool) -> list[str]:
    dimensions = [
        "forecast_origin_month",
        "year_month",
        "institution_code",
    ]
    if include_department:
        dimensions.append("department")
    dimensions.extend(
        [
            "item_group_id",
            "item_family_id",
            "standard_family_name",
            "item_subtype_id",
            "standard_subtype_name",
            "normalized_specification",
            "unit_code",
            "unit_name",
            "taxonomy_version",
            "source_series_count",
            "source_local_item_count",
            *QUANTITY_COLUMNS,
            *POLICY_DAY_COLUMNS,
            "history_months_min",
            "history_months_max",
            *RISK_SCORE_COLUMNS,
            "approved_material_mapping_count",
            "mapped_source_series_count",
            "has_approved_material_mapping",
            "data_age_months",
            "is_stale_data",
            "prediction_type",
            "primary_model",
            "aggregation_method",
        ]
    )
    return dimensions


def _coerce_bool(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    normalized = series.astype("string").fillna("").str.strip().str.lower()
    return normalized.isin({"true", "t", "1", "yes", "y"})


def _aggregate_predictions(
    predictions: pd.DataFrame,
    mappings: pd.DataFrame,
    include_department: bool,
) -> tuple[pd.DataFrame, dict[str, int | float]]:
    required_predictions = [
        "year_month",
        "institution_code",
        "item_code",
        "predicted_usage",
        "current_stock",
        "recommended_stock",
        "recommended_order",
    ]
    if include_department:
        required_predictions.append("department")
    _require_columns(predictions, required_predictions, "local predictions")

    source = predictions.copy()
    if "local_item_key" not in source.columns:
        source["local_item_key"] = (
            source["institution_code"].astype(str) + "::" + source["item_code"].astype(str)
        )
    source = _clean_text_columns(
        source,
        ["local_item_key", "institution_code", "department", "item_code"],
    )
    for column in [*QUANTITY_COLUMNS, *POLICY_DAY_COLUMNS]:
        if column in source.columns:
            converted = pd.to_numeric(source[column], errors="coerce")
            invalid = source[column].notna() & converted.isna()
            if invalid.any():
                raise ClassificationValidationError(
                    f"local predictions have non-numeric values in {column}"
                )
            source[column] = converted
    if "approved_material_mapping_count" not in source.columns:
        source["approved_material_mapping_count"] = 0
    source["approved_material_mapping_count"] = pd.to_numeric(
        source["approved_material_mapping_count"], errors="coerce"
    ).fillna(0)
    if "has_approved_material_mapping" not in source.columns:
        source["has_approved_material_mapping"] = False
    source["has_approved_material_mapping"] = _coerce_bool(
        source["has_approved_material_mapping"]
    )

    joined = source.merge(
        mappings,
        on="local_item_key",
        how="left",
        validate="many_to_one",
        indicator=True,
        suffixes=("", "_classification"),
    )
    matched = joined["_merge"].eq("both")
    forecastable = _coerce_bool(joined["is_forecastable"])
    eligible = joined[matched & forecastable].copy()

    unique_prediction_keys = set(source["local_item_key"].unique())
    mapping_not_in_predictions = int(
        (~mappings["local_item_key"].isin(unique_prediction_keys)).sum()
    )
    total_predicted_usage = source["predicted_usage"].sum(min_count=1)
    matched_predicted_usage = eligible["predicted_usage"].sum(min_count=1)
    usage_coverage = 0.0
    if pd.notna(total_predicted_usage) and float(total_predicted_usage) != 0:
        matched_value = 0.0 if pd.isna(matched_predicted_usage) else float(matched_predicted_usage)
        usage_coverage = float(matched_value / total_predicted_usage * 100)

    stats: dict[str, int | float] = {
        "local_prediction_rows": int(len(source)),
        "matched_prediction_rows": int(matched.sum()),
        "unmatched_prediction_rows": int((~matched).sum()),
        "excluded_non_forecastable_rows": int((matched & ~forecastable).sum()),
        "eligible_prediction_rows": int(len(eligible)),
        "mapping_rows_without_prediction": mapping_not_in_predictions,
        "predicted_usage_coverage_pct": usage_coverage,
    }
    if eligible.empty:
        return pd.DataFrame(columns=_classified_output_columns(include_department)), stats

    if "forecast_origin_month" not in eligible.columns:
        eligible["forecast_origin_month"] = pd.NA
    group_columns = [
        "forecast_origin_month",
        "year_month",
        "institution_code",
    ]
    if include_department:
        group_columns.append("department")
    group_columns.extend(
        [
            "item_group_id",
            "item_family_id",
            "standard_family_name",
            "item_subtype_id",
            "standard_subtype_name",
            "normalized_specification",
            "unit_code",
            "unit_name",
            "taxonomy_version",
        ]
    )

    if "stock_item_key" not in eligible.columns:
        eligible["stock_item_key"] = eligible["local_item_key"]
        if include_department:
            eligible["stock_item_key"] = (
                eligible["local_item_key"] + "::" + eligible["department"]
            )
    aggregations: dict[str, tuple[str, object]] = {
        "source_series_count": ("stock_item_key", "nunique"),
        "source_local_item_count": ("local_item_key", "nunique"),
    }
    for column in QUANTITY_COLUMNS:
        if column in eligible.columns:
            aggregations[column] = (column, _sum_with_min_count)
    for column in POLICY_DAY_COLUMNS:
        if column in eligible.columns:
            aggregations[column] = (column, "max")
    if "history_months" in eligible.columns:
        aggregations["history_months_min"] = ("history_months", "min")
        aggregations["history_months_max"] = ("history_months", "max")
    for column in RISK_SCORE_COLUMNS:
        if column in eligible.columns:
            aggregations[column] = (column, "max")
    aggregations["approved_material_mapping_count"] = (
        "approved_material_mapping_count",
        "sum",
    )
    aggregations["mapped_source_series_count"] = (
        "has_approved_material_mapping",
        "sum",
    )
    aggregations["has_approved_material_mapping"] = (
        "has_approved_material_mapping",
        "max",
    )
    if "data_age_months" in eligible.columns:
        aggregations["data_age_months"] = ("data_age_months", "max")
    if "is_stale_data" in eligible.columns:
        eligible["is_stale_data"] = _coerce_bool(eligible["is_stale_data"])
        aggregations["is_stale_data"] = ("is_stale_data", "max")
    if "prediction_type" in eligible.columns:
        aggregations["prediction_type"] = ("prediction_type", "first")
    if "primary_model" in eligible.columns:
        aggregations["primary_model"] = ("primary_model", "first")

    grouped = (
        eligible.groupby(group_columns, observed=True, dropna=False)
        .agg(**aggregations)
        .reset_index()
    )
    grouped["aggregation_method"] = "sum_local_series_without_unit_conversion"
    for column in _classified_output_columns(include_department):
        if column not in grouped.columns:
            grouped[column] = pd.NA
    grouped = grouped[_classified_output_columns(include_department)]
    grouped = grouped.sort_values(group_columns, kind="stable").reset_index(drop=True)
    return grouped, stats


def aggregate_predictions_by_subtype(
    predictions: pd.DataFrame,
    mappings: pd.DataFrame,
    include_department: bool = True,
) -> pd.DataFrame:
    grouped, _ = _aggregate_predictions(predictions, mappings, include_department)
    return grouped


def build_classified_prediction_output(
    predictions: pd.DataFrame,
    classification_path: Path = APPROVED_ITEM_CLASSIFICATION_PATH,
    taxonomy_path: Path = ITEM_FAMILY_TAXONOMY_PATH,
    include_department: bool = True,
) -> tuple[pd.DataFrame, dict[str, object]]:
    loaded = load_approved_classifications(classification_path, taxonomy_path)
    grouped, aggregation_stats = _aggregate_predictions(
        predictions,
        loaded.mappings,
        include_department,
    )
    if loaded.approved_rows == 0:
        status = "awaiting_approved_classifications"
    elif grouped.empty:
        status = "no_eligible_matching_forecasts"
    else:
        status = "ready"
    report: dict[str, object] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "scope": "institution_department" if include_department else "institution",
        "classification_input_rows": loaded.input_rows,
        "approved_classification_rows": loaded.approved_rows,
        "ignored_unapproved_classification_rows": loaded.ignored_unapproved_rows,
        "approved_taxonomy_rows": loaded.approved_taxonomy_rows,
        **aggregation_stats,
        "output_rows": int(len(grouped)),
        "rules": {
            "classification_gate": "classification and taxonomy must both be approved",
            "grouping": "family + subtype + normalized specification + unit",
            "unit_conversion": "disabled; different unit_code values are never summed",
            "forecastable_policy": "is_forecastable is inherited from the approved taxonomy",
            "quantity_method": "sum forecasts and inventory policy values from local series",
        },
    }
    return grouped, report


def write_classified_prediction_outputs(
    predictions: pd.DataFrame,
    classification_path: Path = APPROVED_ITEM_CLASSIFICATION_PATH,
    taxonomy_path: Path = ITEM_FAMILY_TAXONOMY_PATH,
    output_path: Path = CLASSIFIED_PREDICTION_PATH,
    quality_path: Path = CLASSIFIED_PREDICTION_QUALITY_PATH,
    include_department: bool = True,
) -> tuple[pd.DataFrame, dict[str, object]]:
    grouped, report = build_classified_prediction_output(
        predictions,
        classification_path=classification_path,
        taxonomy_path=taxonomy_path,
        include_department=include_department,
    )
    ensure_dirs(output_path.parent, quality_path.parent)
    guard_not_empty(grouped, output_path, "품목군별 예측")
    grouped.to_csv(output_path, index=False)
    report["classification_path"] = str(classification_path)
    report["taxonomy_path"] = str(taxonomy_path)
    report["output_path"] = str(output_path)
    write_json(report, quality_path)
    LOGGER.info("Saved classified forecast: %s (%s rows)", output_path, len(grouped))
    LOGGER.info("Saved classified forecast quality report: %s", quality_path)
    return grouped, report


def run_classified_prediction(
    classification_path: Path = APPROVED_ITEM_CLASSIFICATION_PATH,
    taxonomy_path: Path = ITEM_FAMILY_TAXONOMY_PATH,
    prediction_path: Path = PREDICTION_PATH,
    output_path: Path = CLASSIFIED_PREDICTION_PATH,
    quality_path: Path = CLASSIFIED_PREDICTION_QUALITY_PATH,
    include_department: bool = True,
    refresh_local_predictions: bool = False,
) -> tuple[pd.DataFrame, dict[str, object]]:
    setup_logging()
    ensure_dirs(OUTPUT_DIR)
    if refresh_local_predictions or not prediction_path.exists():
        from .prediction import build_predictions

        predictions = build_predictions()
        guard_not_empty(predictions, prediction_path, "품목군별 예측")
        predictions.to_csv(prediction_path, index=False)
    else:
        predictions = pd.read_csv(
            prediction_path,
            dtype={
                "institution_code": str,
                "department": str,
                "item_code": str,
                "local_item_key": str,
            },
        )
    return write_classified_prediction_outputs(
        predictions,
        classification_path=classification_path,
        taxonomy_path=taxonomy_path,
        output_path=output_path,
        quality_path=quality_path,
        include_department=include_department,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply approved item classifications to local stock forecasts."
    )
    parser.add_argument("--classification-path", type=Path, default=APPROVED_ITEM_CLASSIFICATION_PATH)
    parser.add_argument("--taxonomy-path", type=Path, default=ITEM_FAMILY_TAXONOMY_PATH)
    parser.add_argument("--prediction-path", type=Path, default=PREDICTION_PATH)
    parser.add_argument("--output-path", type=Path, default=CLASSIFIED_PREDICTION_PATH)
    parser.add_argument("--quality-path", type=Path, default=CLASSIFIED_PREDICTION_QUALITY_PATH)
    parser.add_argument(
        "--scope",
        choices=["department", "institution"],
        default="department",
        help="Preserve department stock boundaries or aggregate within an institution.",
    )
    parser.add_argument(
        "--refresh-local-predictions",
        action="store_true",
        help="Rebuild local forecasts before applying classifications.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_classified_prediction(
        classification_path=args.classification_path,
        taxonomy_path=args.taxonomy_path,
        prediction_path=args.prediction_path,
        output_path=args.output_path,
        quality_path=args.quality_path,
        include_department=args.scope == "department",
        refresh_local_predictions=args.refresh_local_predictions,
    )
