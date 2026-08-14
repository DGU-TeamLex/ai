from __future__ import annotations

import argparse
import copy
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd
import pyarrow.parquet as pq

from .config import (
    ITEM_ALIAS_TO_PRODUCT_PATH,
    ITEM_CLASSIFICATION_ATTENTION_SAMPLE_PATH,
    ITEM_CLASSIFICATION_CLUSTER_METRICS_PATH,
    ITEM_CLASSIFICATION_EVALUATION_REPORT_PATH,
    ITEM_CLASSIFICATION_FIELD_METRICS_PATH,
    ITEM_CLASSIFICATION_WEIGHT_SCENARIOS_PATH,
    ITEM_INTEGRATED_CLASSIFICATION_PARQUET_PATH,
    MODULE_C_WEIGHT_SENSITIVITY_PATH,
    PROJECT_ROOT,
)
from .modeling.inventory_policy import add_inventory_recommendations
from .module_c.config import load_module_c_config, validate_module_c_config


EVALUATION_VERSION = "item-classification-evaluation-v1.0"

REFERENCE_COLUMNS = [
    "보건기관코드_en",
    "물품코드",
    "물품명",
    "표준품목ID",
    "표준물품명",
    "코드유형",
]
REFERENCE_RENAME = {
    "보건기관코드_en": "institution_id",
    "물품코드": "local_item_code",
    "물품명": "raw_item_name",
    "표준품목ID": "reference_standard_id",
    "표준물품명": "reference_standard_name",
    "코드유형": "reference_code_type",
}
LOCAL_ITEM_KEYS = ["institution_id", "local_item_code"]
EXACT_ALIAS_KEYS = LOCAL_ITEM_KEYS + ["raw_item_name"]

REGRESSION_FIELDS = [
    ("item_group_id", "verified_item_group_id", "item_group_id_candidate"),
    ("item_family_id", "item_family_id", "effective_item_family_id"),
    ("item_subtype_id", "item_subtype_id", "effective_item_subtype_id"),
    ("specification", "verified_specification", "effective_specification"),
    ("unit_code", "verified_unit", "effective_unit_code"),
]

EVIDENCE_SOURCE_WEIGHTS = {
    "verified_structured_family": 0.92,
    "verified_ingredient_dictionary": 0.90,
    "official_standard_rule": 0.88,
    "local_structured_family": 0.82,
    "context_explicit_rule": 0.78,
    "name_rule": 0.55,
}


def _text_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series("", index=frame.index, dtype="string")
    return frame[column].astype("string").fillna("").str.strip()


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return float(numerator / denominator)


def _comb2(values: pd.Series) -> int:
    numeric = pd.to_numeric(values, errors="coerce").fillna(0).astype("int64")
    return int(((numeric * (numeric - 1)) // 2).sum())


def find_reference_path() -> Path:
    candidates = sorted((PROJECT_ROOT / "regulazation").glob("*.parquet"))
    if len(candidates) != 1:
        raise FileNotFoundError(
            "Expected exactly one regulazation parquet; pass --reference-path explicitly"
        )
    return candidates[0]


def read_reference_aliases(path: Path) -> tuple[pd.DataFrame, dict[str, int]]:
    parquet = pq.ParquetFile(path)
    missing = [column for column in REFERENCE_COLUMNS if column not in parquet.schema.names]
    if missing:
        raise ValueError(f"Reference parquet is missing columns: {missing}")

    relations: set[tuple[str, str, str, str, str, str]] = set()
    rows_scanned = 0
    for row_group in range(parquet.num_row_groups):
        batch = parquet.read_row_group(row_group, columns=REFERENCE_COLUMNS).to_pandas()
        rows_scanned += len(batch)
        batch = batch.fillna("").astype(str).drop_duplicates()
        relations.update(batch.itertuples(index=False, name=None))

    reference = pd.DataFrame.from_records(list(relations), columns=REFERENCE_COLUMNS)
    reference = reference.rename(columns=REFERENCE_RENAME)
    reference = reference.sort_values(
        EXACT_ALIAS_KEYS + ["reference_standard_id", "reference_standard_name"],
        kind="stable",
    ).reset_index(drop=True)
    return reference, {
        "reference_rows_scanned": int(rows_scanned),
        "reference_unique_relations": int(len(reference)),
        "reference_row_groups": int(parquet.num_row_groups),
    }


def cluster_agreement_metrics(
    frame: pd.DataFrame,
    scope: str,
) -> dict[str, int | float | str | None]:
    if frame.empty:
        return {
            "scope": scope,
            "alias_rows": 0,
            "current_clusters": 0,
            "reference_clusters": 0,
            "pairwise_precision": None,
            "pairwise_recall": None,
            "pairwise_f1": None,
            "bcubed_precision": None,
            "bcubed_recall": None,
            "bcubed_f1": None,
        }

    current = "representative_item_id"
    reference = "reference_cluster_key"
    cells = (
        frame.groupby([current, reference], observed=True)
        .size()
        .rename("cell_size")
        .reset_index()
    )
    current_sizes = frame.groupby(current, observed=True).size().rename("current_size")
    reference_sizes = (
        frame.groupby(reference, observed=True).size().rename("reference_size")
    )
    cells = cells.join(current_sizes, on=current).join(reference_sizes, on=reference)

    true_pairs = _comb2(cells["cell_size"])
    predicted_pairs = _comb2(current_sizes)
    reference_pairs = _comb2(reference_sizes)
    pair_precision = _safe_ratio(true_pairs, predicted_pairs)
    pair_recall = _safe_ratio(true_pairs, reference_pairs)
    pair_f1 = (
        2 * pair_precision * pair_recall / (pair_precision + pair_recall)
        if pair_precision is not None
        and pair_recall is not None
        and pair_precision + pair_recall > 0
        else None
    )

    weighted = cells["cell_size"]
    bc_precision = float(
        ((cells["cell_size"] / cells["current_size"]) * weighted).sum()
        / weighted.sum()
    )
    bc_recall = float(
        ((cells["cell_size"] / cells["reference_size"]) * weighted).sum()
        / weighted.sum()
    )
    bc_f1 = (
        2 * bc_precision * bc_recall / (bc_precision + bc_recall)
        if bc_precision + bc_recall > 0
        else None
    )
    return {
        "scope": scope,
        "alias_rows": int(len(frame)),
        "current_clusters": int(frame[current].nunique()),
        "reference_clusters": int(frame[reference].nunique()),
        "true_positive_pairs": int(true_pairs),
        "predicted_pairs": int(predicted_pairs),
        "reference_pairs": int(reference_pairs),
        "pairwise_precision": pair_precision,
        "pairwise_recall": pair_recall,
        "pairwise_f1": pair_f1,
        "bcubed_precision": bc_precision,
        "bcubed_recall": bc_recall,
        "bcubed_f1": bc_f1,
    }


def evaluate_reference_clusters(
    aliases: pd.DataFrame,
    reference: pd.DataFrame,
    representative_names: pd.DataFrame,
    sample_size: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    aliases = aliases.copy()
    for column in EXACT_ALIAS_KEYS + ["representative_item_id", "product_name_candidate"]:
        aliases[column] = _text_series(aliases, column)
    if aliases.duplicated(LOCAL_ITEM_KEYS).any():
        raise ValueError("Current alias join keys must be unique")

    reference = reference.copy()
    for column in EXACT_ALIAS_KEYS + [
        "reference_standard_id",
        "reference_standard_name",
        "reference_code_type",
    ]:
        reference[column] = _text_series(reference, column)

    relation_counts = (
        reference.groupby(LOCAL_ITEM_KEYS, observed=True)["reference_standard_id"]
        .nunique()
        .rename("reference_ids_per_alias")
        .reset_index()
    )
    reference = reference.merge(relation_counts, on=LOCAL_ITEM_KEYS, how="left")
    ambiguous_keys = reference.loc[
        reference["reference_ids_per_alias"].gt(1), LOCAL_ITEM_KEYS
    ].drop_duplicates()
    unambiguous = reference.loc[reference["reference_ids_per_alias"].eq(1)].copy()
    unambiguous = unambiguous.drop_duplicates(LOCAL_ITEM_KEYS, keep="first")

    joined = aliases.merge(
        unambiguous,
        on=LOCAL_ITEM_KEYS,
        how="left",
        validate="one_to_one",
        suffixes=("", "_reference"),
    ).rename(columns={"raw_item_name_reference": "reference_raw_item_name"})
    joined["reference_standard_id"] = _text_series(joined, "reference_standard_id")
    joined["reference_raw_item_name"] = _text_series(joined, "reference_raw_item_name")
    matched = joined.loc[joined["reference_standard_id"].ne("")].copy()
    missing_reference = joined.loc[joined["reference_standard_id"].eq("")].copy()
    raw_name_mismatch = matched["raw_item_name"].ne(matched["reference_raw_item_name"])
    matched["reference_cluster_key"] = (
        _text_series(matched, "reference_code_type")
        + "::"
        + _text_series(matched, "reference_standard_id")
    )

    scopes: list[tuple[str, pd.DataFrame]] = [("all_reference", matched)]
    for code_type in sorted(matched["reference_code_type"].dropna().unique()):
        scopes.append(
            (
                f"code_type::{code_type}",
                matched.loc[matched["reference_code_type"].eq(code_type)],
            )
        )
    metrics = pd.DataFrame(
        [cluster_agreement_metrics(subset, scope) for scope, subset in scopes]
    )

    matched["reference_ids_per_current_cluster"] = matched.groupby(
        "representative_item_id", observed=True
    )["reference_cluster_key"].transform("nunique")
    matched["current_ids_per_reference_cluster"] = matched.groupby(
        "reference_cluster_key", observed=True
    )["representative_item_id"].transform("nunique")
    merge_issue = matched["reference_ids_per_current_cluster"].gt(1)
    split_issue = matched["current_ids_per_reference_cluster"].gt(1)
    matched["attention_reason"] = ""
    matched.loc[merge_issue & ~split_issue, "attention_reason"] = "possible_over_merge"
    matched.loc[~merge_issue & split_issue, "attention_reason"] = "possible_over_split"
    matched.loc[merge_issue & split_issue, "attention_reason"] = (
        "possible_over_merge_and_split"
    )

    names = representative_names[
        ["representative_item_id", "representative_name"]
    ].drop_duplicates("representative_item_id")
    attention = matched.loc[matched["attention_reason"].ne("")].merge(
        names,
        on="representative_item_id",
        how="left",
        validate="many_to_one",
    )
    name_mismatch_attention = matched.loc[raw_name_mismatch].merge(
        names,
        on="representative_item_id",
        how="left",
        validate="many_to_one",
    )
    name_mismatch_attention["attention_reason"] = "reference_raw_name_mismatch"
    name_mismatch_attention["reference_ids_per_current_cluster"] = 0
    name_mismatch_attention["current_ids_per_reference_cluster"] = 0
    missing_reference = missing_reference.drop(
        columns=[
            "reference_standard_id",
            "reference_standard_name",
            "reference_code_type",
            "reference_ids_per_alias",
        ],
        errors="ignore",
    )
    missing_reference = missing_reference.merge(
        names,
        on="representative_item_id",
        how="left",
        validate="many_to_one",
    )
    missing_reference["attention_reason"] = "missing_reference_alias"
    missing_reference["reference_ids_per_current_cluster"] = 0
    missing_reference["current_ids_per_reference_cluster"] = 0
    missing_reference["reference_standard_id"] = _text_series(
        missing_reference, "reference_standard_id"
    )
    missing_reference["reference_standard_name"] = _text_series(
        missing_reference, "reference_standard_name"
    )
    missing_reference["reference_code_type"] = _text_series(
        missing_reference, "reference_code_type"
    )
    missing_reference["reference_raw_item_name"] = ""
    attention_frames = [attention, name_mismatch_attention]
    if not missing_reference.empty:
        attention_frames.append(missing_reference)
    attention = pd.concat(attention_frames, ignore_index=True, sort=False)
    attention["sample_hash"] = pd.util.hash_pandas_object(
        attention[LOCAL_ITEM_KEYS + ["attention_reason"]], index=False
    ).astype("uint64")
    attention = attention.sort_values(
        [
            "reference_ids_per_current_cluster",
            "current_ids_per_reference_cluster",
            "sample_hash",
        ],
        ascending=[False, False, True],
        kind="stable",
    )
    attention = attention.drop_duplicates(
        ["attention_reason", "representative_item_id", "reference_standard_id"],
        keep="first",
    )
    attention_pair_candidates = len(attention)
    reasons = attention["attention_reason"].drop_duplicates().tolist()
    per_reason = max(1, sample_size // max(len(reasons), 1))
    selected = attention.groupby("attention_reason", sort=False).head(per_reason)
    if len(selected) < sample_size:
        remaining = attention.loc[~attention.index.isin(selected.index)]
        selected = pd.concat(
            [selected, remaining.head(sample_size - len(selected))], ignore_index=False
        )
    attention = selected.head(sample_size)
    attention_columns = [
        "attention_reason",
        "institution_id",
        "local_item_code",
        "raw_item_name",
        "product_name_candidate",
        "representative_item_id",
        "representative_name",
        "reference_standard_id",
        "reference_standard_name",
        "reference_code_type",
        "reference_raw_item_name",
        "reference_ids_per_current_cluster",
        "current_ids_per_reference_cluster",
    ]
    attention = attention.reindex(columns=attention_columns).reset_index(drop=True)

    current_keys = aliases[LOCAL_ITEM_KEYS].drop_duplicates()
    reference_keys = reference[LOCAL_ITEM_KEYS].drop_duplicates()
    reference_only_keys = reference_keys.merge(
        current_keys,
        on=LOCAL_ITEM_KEYS,
        how="left",
        indicator=True,
    )

    summary = {
        "current_alias_rows": int(len(aliases)),
        "matched_unambiguous_alias_rows": int(len(matched)),
        "missing_reference_alias_rows": int(
            joined["reference_standard_id"].eq("").sum()
        ),
        "reference_raw_name_mismatch_rows": int(raw_name_mismatch.sum()),
        "reference_only_local_item_keys": int(
            reference_only_keys["_merge"].eq("left_only").sum()
        ),
        "ambiguous_reference_alias_keys": int(len(ambiguous_keys)),
        "possible_over_merge_alias_rows": int(merge_issue.sum()),
        "possible_over_split_alias_rows": int(split_issue.sum()),
        "attention_pair_candidates": int(attention_pair_candidates),
        "attention_sample_rows": int(len(attention)),
    }
    return metrics, attention, summary


def evaluate_approval_regression(
    baseline: pd.DataFrame,
    integrated: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, int | float], pd.DataFrame]:
    baseline = baseline.fillna("").astype(str)
    integrated = integrated.copy()
    required = {"representative_item_id"} | {
        column for _, _, column in REGRESSION_FIELDS
    }
    missing = sorted(required - set(integrated.columns))
    if missing:
        raise ValueError(f"Integrated classification is missing columns: {missing}")

    old_columns = ["representative_item_id"] + [
        old_column for _, old_column, _ in REGRESSION_FIELDS
    ]
    old = baseline[old_columns].rename(
        columns={column: f"baseline_{column}" for column in old_columns[1:]}
    )
    current_columns = ["representative_item_id"] + [
        current_column for _, _, current_column in REGRESSION_FIELDS
    ]
    current = integrated[current_columns].copy()
    for column in current.columns:
        current[column] = _text_series(current, column)
    joined = old.merge(current, on="representative_item_id", how="left", indicator=True)

    rows: list[dict[str, int | float | str]] = []
    matches: list[pd.Series] = []
    for field, old_column, current_column in REGRESSION_FIELDS:
        old_name = f"baseline_{old_column}"
        match = joined[old_name].eq(joined[current_column]) & joined["_merge"].eq("both")
        matches.append(match)
        rows.append(
            {
                "metric_type": "frozen_approval_regression_agreement",
                "field": field,
                "matched_rows": int(match.sum()),
                "evaluated_rows": int(len(joined)),
                "agreement_rate": float(match.mean()) if len(joined) else math.nan,
            }
        )
    exact = pd.concat(matches, axis=1).all(axis=1) if matches else pd.Series(dtype=bool)
    rows.append(
        {
            "metric_type": "frozen_approval_regression_agreement",
            "field": "all_five_fields_exact",
            "matched_rows": int(exact.sum()),
            "evaluated_rows": int(len(joined)),
            "agreement_rate": float(exact.mean()) if len(joined) else math.nan,
        }
    )
    joined["all_five_fields_exact"] = exact
    disagreements = joined.loc[~joined["all_five_fields_exact"]].drop(columns="_merge")
    summary = {
        "baseline_approved_rows": int(len(baseline)),
        "baseline_rows_found": int(joined["_merge"].eq("both").sum()),
        "baseline_rows_missing": int(joined["_merge"].eq("left_only").sum()),
        "all_five_fields_exact_rows": int(exact.sum()),
        "all_five_fields_exact_rate": float(exact.mean()) if len(joined) else 0.0,
    }
    return pd.DataFrame(rows), summary, disagreements


def add_experimental_evidence_score(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    source = _text_series(result, "family_source")
    score = source.map(EVIDENCE_SOURCE_WEIGHTS).fillna(0.0).astype(float)
    resolution = _text_series(result, "family_resolution_status")
    score += resolution.str.contains("agree", regex=False).astype(float) * 0.03
    score += _text_series(result, "effective_item_subtype_id").ne("").astype(float) * 0.01
    score += _text_series(result, "effective_specification").ne("").astype(float) * 0.02
    score += _text_series(result, "effective_unit_code").ne("").astype(float) * 0.02

    status = _text_series(result, "classification_classification_status")
    confidence = pd.to_numeric(
        result.get("classification_classification_confidence", 0.0), errors="coerce"
    ).fillna(0.0)
    approved = status.str.startswith("approved_external")
    score = score.where(~approved, confidence)
    conflict = _text_series(result, "family_conflict_flag").str.lower().isin(
        {"true", "1", "t", "yes"}
    ) | status.eq("conflict")
    unresolved = _text_series(result, "effective_item_family_id").isin(
        {"", "UNSPECIFIED_ITEM"}
    )
    score = score.where(~conflict, (score - 0.35).clip(lower=0.0))
    score = score.where(~unresolved, 0.0)
    result["experimental_evidence_score"] = score.clip(0.0, 1.0)
    result["experimental_evidence_conflict"] = conflict
    result["experimental_evidence_unresolved"] = unresolved
    return result


def evaluate_weight_scenarios(
    integrated: pd.DataFrame,
    baseline_ids: Iterable[str] = (),
) -> pd.DataFrame:
    scored = add_experimental_evidence_score(integrated)
    baseline_set = {str(value) for value in baseline_ids}
    family_ready = _text_series(scored, "effective_item_family_id").ne("")
    subtype_ready = _text_series(scored, "effective_item_subtype_id").ne("")
    specification_ready = _text_series(scored, "effective_specification").ne("")
    unit_ready = _text_series(scored, "effective_unit_code").ne("")
    conflict_free = ~scored["experimental_evidence_conflict"]
    unresolved_free = ~scored["experimental_evidence_unresolved"]
    approved = _text_series(scored, "classification_review_status").eq("approved")

    definitions = [
        (
            "current_production_approval",
            "production_gate",
            None,
            approved,
        ),
        (
            "conservative_review_candidate",
            "experimental_review_only",
            0.90,
            scored["experimental_evidence_score"].ge(0.90)
            & family_ready
            & subtype_ready
            & specification_ready
            & unit_ready
            & conflict_free
            & unresolved_free,
        ),
        (
            "balanced_review_candidate",
            "experimental_review_only",
            0.82,
            scored["experimental_evidence_score"].ge(0.82)
            & family_ready
            & specification_ready
            & unit_ready
            & conflict_free
            & unresolved_free,
        ),
        (
            "aggressive_review_candidate",
            "experimental_review_only",
            0.60,
            scored["experimental_evidence_score"].ge(0.60)
            & family_ready
            & unit_ready
            & conflict_free
            & unresolved_free,
        ),
    ]
    usage = pd.to_numeric(scored.get("usage_sum", 0.0), errors="coerce").fillna(0.0)
    total_usage = float(usage.sum())
    rows = []
    for scenario, status, threshold, selected in definitions:
        selected_ids = set(
            _text_series(scored.loc[selected], "representative_item_id")
        )
        rows.append(
            {
                "scenario": scenario,
                "status": status,
                "threshold": threshold,
                "selected_representatives": int(selected.sum()),
                "representative_coverage": _safe_ratio(int(selected.sum()), len(scored)),
                "usage_weighted_coverage": _safe_ratio(
                    float(usage.loc[selected].sum()), total_usage
                ),
                "frozen_approval_rows": int(len(baseline_set)),
                "frozen_approval_retained": int(len(selected_ids & baseline_set)),
                "frozen_approval_retention": _safe_ratio(
                    len(selected_ids & baseline_set), len(baseline_set)
                ),
                "unlabelled_selected_rows": int(len(selected_ids - baseline_set)),
                "conflict_rows_selected": int(
                    (selected & scored["experimental_evidence_conflict"]).sum()
                ),
                "unresolved_rows_selected": int(
                    (selected & scored["experimental_evidence_unresolved"]).sum()
                ),
                "independent_precision_estimate": None,
            }
        )
    return pd.DataFrame(rows)


def build_module_c_weight_sensitivity() -> pd.DataFrame:
    base_config = load_module_c_config()
    scenarios = {
        "reduced_impact_075x": 0.75,
        "current_policy_seed": 1.0,
        "stress_impact_125x": 1.25,
    }
    risk_levels = [0.0, 0.25, 0.50, 0.75, 1.0]
    rows: list[pd.DataFrame] = []
    for scenario, multiplier in scenarios.items():
        config = copy.deepcopy(base_config)
        adjustment = config["inventory_adjustment"]
        for key in [
            "demand_usage_uplift_max",
            "supply_lead_time_multiplier_max",
            "supply_extra_lead_time_days_max",
            "safety_stock_rate_uplift_max",
        ]:
            adjustment[key] = float(adjustment[key]) * multiplier
        adjustment["total_risk_buffer_rate_cap"] = min(
            1.0, float(adjustment["total_risk_buffer_rate_cap"]) * multiplier
        )
        config["version"] = f"{base_config['version']}::{scenario}"
        config["calibration_status"] = "sensitivity_only_not_calibrated"
        validate_module_c_config(config)
        inputs = pd.DataFrame(
            {
                "predicted_usage": 100.0,
                "lead_time_days": 7.0,
                "review_period_days": 30.0,
                "module_c_demand_risk": risk_levels,
                "module_c_supply_risk": risk_levels,
                "module_c_total_risk": risk_levels,
                "external_demand_signal_in_forecast": False,
            }
        )
        result = add_inventory_recommendations(
            inputs,
            lead_time_days_col="lead_time_days",
            review_period_days_col="review_period_days",
            module_c_config=config,
        )
        result.insert(0, "scenario", scenario)
        result.insert(1, "impact_multiplier", multiplier)
        result["target_stock_uplift_rate"] = (
            result["target_stock"] / result["base_stock"] - 1.0
        )
        rows.append(
            result[
                [
                    "scenario",
                    "impact_multiplier",
                    "module_c_total_risk",
                    "base_stock",
                    "risk_adjusted_predicted_usage",
                    "effective_lead_time_days",
                    "dynamic_safety_stock_rate",
                    "risk_buffer",
                    "target_stock",
                    "target_stock_uplift_rate",
                ]
            ]
        )
    return pd.concat(rows, ignore_index=True)


def run_evaluation(
    baseline_approvals_path: Path | None,
    reference_path: Path,
    baseline_source: str,
    sample_size: int = 1000,
) -> dict[str, object]:
    integrated = pd.read_parquet(ITEM_INTEGRATED_CLASSIFICATION_PARQUET_PATH)
    aliases = pd.read_parquet(ITEM_ALIAS_TO_PRODUCT_PATH)
    reference, scan_summary = read_reference_aliases(reference_path)
    cluster_metrics, attention, reference_summary = evaluate_reference_clusters(
        aliases,
        reference,
        integrated,
        sample_size,
    )

    baseline_ids: list[str] = []
    if baseline_approvals_path is not None:
        baseline = pd.read_csv(
            baseline_approvals_path,
            dtype=str,
            keep_default_na=False,
            encoding="utf-8-sig",
        )
        field_metrics, regression_summary, disagreements = (
            evaluate_approval_regression(baseline, integrated)
        )
        baseline_ids = baseline["representative_item_id"].astype(str).tolist()
    else:
        field_metrics = pd.DataFrame(
            columns=[
                "metric_type",
                "field",
                "matched_rows",
                "evaluated_rows",
                "agreement_rate",
            ]
        )
        regression_summary = {"status": "baseline_not_provided"}
        disagreements = pd.DataFrame()

    weight_scenarios = evaluate_weight_scenarios(integrated, baseline_ids)
    module_c_sensitivity = build_module_c_weight_sensitivity()
    approval_count = int(
        _text_series(integrated, "classification_review_status").eq("approved").sum()
    )
    family_resolved_count = int(
        (
            ~_text_series(integrated, "effective_item_family_id").isin(
                {"", "UNSPECIFIED_ITEM"}
            )
        ).sum()
    )

    for path in [
        ITEM_CLASSIFICATION_EVALUATION_REPORT_PATH,
        ITEM_CLASSIFICATION_FIELD_METRICS_PATH,
        ITEM_CLASSIFICATION_CLUSTER_METRICS_PATH,
        ITEM_CLASSIFICATION_WEIGHT_SCENARIOS_PATH,
        ITEM_CLASSIFICATION_ATTENTION_SAMPLE_PATH,
        MODULE_C_WEIGHT_SENSITIVITY_PATH,
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
    field_metrics.to_csv(
        ITEM_CLASSIFICATION_FIELD_METRICS_PATH, index=False, encoding="utf-8-sig"
    )
    cluster_metrics.to_csv(
        ITEM_CLASSIFICATION_CLUSTER_METRICS_PATH, index=False, encoding="utf-8-sig"
    )
    weight_scenarios.to_csv(
        ITEM_CLASSIFICATION_WEIGHT_SCENARIOS_PATH, index=False, encoding="utf-8-sig"
    )
    attention.to_csv(
        ITEM_CLASSIFICATION_ATTENTION_SAMPLE_PATH, index=False, encoding="utf-8-sig"
    )
    module_c_sensitivity.to_csv(
        MODULE_C_WEIGHT_SENSITIVITY_PATH, index=False, encoding="utf-8-sig"
    )

    report: dict[str, object] = {
        "evaluation_version": EVALUATION_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "interpretation_status": "diagnostic_not_independent_accuracy",
        "independent_accuracy_available": False,
        "representative_rows": int(len(integrated)),
        "classification_approved_rows": approval_count,
        "classification_approved_coverage": _safe_ratio(approval_count, len(integrated)),
        "family_resolved_rows": family_resolved_count,
        "family_resolved_coverage": _safe_ratio(family_resolved_count, len(integrated)),
        "regression": {
            "baseline_source": baseline_source,
            **regression_summary,
            "interpretation": (
                "Frozen approval regression agreement checks stability only; the baseline "
                "was produced by the same rule family and is not an independent gold set."
            ),
        },
        "reference_cluster_evaluation": {
            "reference_path": str(reference_path),
            **scan_summary,
            **reference_summary,
            "interpretation": (
                "Official-code rows are stronger identity evidence. USE-code rows are a "
                "rule-based pseudo-reference and must not be reported as human-label accuracy."
            ),
        },
        "weight_scenarios": {
            "classification_evidence_weights": EVIDENCE_SOURCE_WEIGHTS,
            "status": "experimental_review_prioritization_only",
            "production_config_changed": False,
            "independent_precision_available": False,
        },
        "module_c_weight_sensitivity": {
            "status": "sensitivity_only_not_calibrated",
            "production_config_changed": False,
            "operational_risk_rows_available": False,
            "reason": (
                "No approved item-material relation currently passes the Module C release gate."
            ),
        },
        "quality_gates": {
            "integrated_representative_ids_unique": bool(
                ~integrated["representative_item_id"].duplicated().any()
            ),
            "alias_join_keys_unique": bool(
                ~aliases.duplicated(LOCAL_ITEM_KEYS).any()
            ),
            "reference_alias_coverage_complete": (
                reference_summary["missing_reference_alias_rows"] == 0
                and reference_summary["ambiguous_reference_alias_keys"] == 0
            ),
            "attention_sample_size_exact": len(attention)
            == min(sample_size, reference_summary["attention_pair_candidates"]),
            "regression_disagreement_rows": int(len(disagreements)),
        },
        "outputs": {
            "report": str(ITEM_CLASSIFICATION_EVALUATION_REPORT_PATH),
            "regression_metrics": str(ITEM_CLASSIFICATION_FIELD_METRICS_PATH),
            "reference_cluster_metrics": str(ITEM_CLASSIFICATION_CLUSTER_METRICS_PATH),
            "classification_weight_scenarios": str(
                ITEM_CLASSIFICATION_WEIGHT_SCENARIOS_PATH
            ),
            "attention_sample": str(ITEM_CLASSIFICATION_ATTENTION_SAMPLE_PATH),
            "module_c_weight_sensitivity": str(MODULE_C_WEIGHT_SENSITIVITY_PATH),
        },
    }
    ITEM_CLASSIFICATION_EVALUATION_REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate item-classification stability and reference clustering"
    )
    parser.add_argument("--baseline-approvals", type=Path)
    parser.add_argument("--baseline-source", default="not_provided")
    parser.add_argument("--reference-path", type=Path, default=None)
    parser.add_argument("--sample-size", type=int, default=1000)
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    result = run_evaluation(
        baseline_approvals_path=args.baseline_approvals,
        reference_path=args.reference_path or find_reference_path(),
        baseline_source=args.baseline_source,
        sample_size=args.sample_size,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
