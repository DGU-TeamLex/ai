from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import (
    INVENTORY_STATUS_PATH,
    INVENTORY_STATUS_POLICY_PATH,
    INVENTORY_STATUS_REPORT_PATH,
    INVENTORY_STATUS_SAMPLE_PATH,
    MONTHLY_STOCK_PATH,
)
from ..utils import ensure_dirs, setup_logging
from .classified_prediction import load_approved_classifications


LOGGER = logging.getLogger(__name__)
SERIES_KEYS = ["institution_code", "department", "item_code"]
LEDGER_COLUMNS = [
    "ledger_document_rule_violation_count",
    "ledger_physical_violation_count",
    "ledger_opening_stock_missing_count",
]
DEMAND_MOMENT_COLUMNS = [
    "normal_outbound_nonnegative_sum",
    "normal_outbound_squared_sum",
]
CLASSIFICATION_COLUMNS = [
    "item_group_id",
    "item_family_id",
    "standard_family_name",
    "item_subtype_id",
    "standard_subtype_name",
    "normalized_specification",
    "unit_code",
    "unit_name",
    "is_forecastable",
    "review_status",
    "taxonomy_version",
    "classification_version",
]


def _require_columns(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing required columns: {missing}")


def validate_inventory_status_policy(policy: dict[str, Any]) -> dict[str, Any]:
    if not str(policy.get("version", "")).strip():
        raise ValueError("Inventory status policy version is required")
    recent_months = int(policy.get("recent_activity_months", 0))
    if recent_months <= 0:
        raise ValueError("recent_activity_months must be positive")
    maximum_lag = int(policy.get("maximum_observation_lag_months", -1))
    if maximum_lag < 0:
        raise ValueError("maximum_observation_lag_months must be non-negative")
    demand_parameters = policy.get("demand_parameters", {})
    mean_floor = float(demand_parameters.get("mean_daily_usage_floor", -1))
    stddev_floor = float(
        demand_parameters.get("daily_demand_stddev_floor", -1)
    )
    forecast_window_days = int(
        demand_parameters.get("forecast_window_days", 0)
    )
    if mean_floor < 0 or stddev_floor < 0:
        raise ValueError("Demand parameter floors must be non-negative")
    if forecast_window_days <= 0:
        raise ValueError("Demand forecast window must be positive")
    exact_fields = policy.get("substitution_group", {}).get("exact_fields", [])
    expected = [
        "item_family_id",
        "item_subtype_id",
        "normalized_specification",
        "unit_code",
    ]
    if exact_fields != expected:
        raise ValueError(
            "Substitution grouping must use approved family/subtype/specification/unit"
        )
    if (
        policy.get("ledger_rule", {}).get("automatic_data_missing_basis")
        != "ledger_physical_violation_count"
    ):
        raise ValueError(
            "Automatic DATA_MISSING classification must use the physical ledger rule"
        )
    return policy


def load_inventory_status_policy(
    path: Path = INVENTORY_STATUS_POLICY_PATH,
) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return validate_inventory_status_policy(json.load(file))


def _month_difference(later: pd.Timestamp, earlier: pd.Series) -> pd.Series:
    return (
        (later.year - earlier.dt.year) * 12
        + later.month
        - earlier.dt.month
    ).astype("int32")


def _prepare_series_status(
    monthly_stock: pd.DataFrame,
    policy: dict[str, Any],
) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    required = [
        "year_month",
        *SERIES_KEYS,
        "item_name",
        "month_end_stock",
        "consumption_qty",
        "first_date",
        "last_date",
        *DEMAND_MOMENT_COLUMNS,
        *LEDGER_COLUMNS,
    ]
    _require_columns(monthly_stock, required, "monthly stock")
    if monthly_stock.empty:
        raise ValueError("monthly stock is empty")

    stock = monthly_stock.copy()
    stock["year_month"] = pd.to_datetime(stock["year_month"], errors="coerce")
    if stock["year_month"].isna().any():
        raise ValueError("monthly stock contains invalid year_month values")
    for column in ["first_date", "last_date"]:
        stock[column] = pd.to_datetime(stock[column], errors="coerce")
    if stock[["first_date", "last_date"]].isna().any().any():
        raise ValueError("monthly stock contains invalid first_date/last_date values")
    numeric_columns = [
        "month_end_stock",
        "consumption_qty",
        *DEMAND_MOMENT_COLUMNS,
        *LEDGER_COLUMNS,
    ]
    for column in SERIES_KEYS:
        stock[column] = stock[column].astype("string").fillna("").str.strip()
    if stock[SERIES_KEYS].eq("").any().any():
        raise ValueError("monthly stock contains blank series keys")
    for column in numeric_columns:
        stock[column] = pd.to_numeric(stock[column], errors="coerce")
    if stock[[*DEMAND_MOMENT_COLUMNS, *LEDGER_COLUMNS]].isna().any().any():
        raise ValueError(
            "monthly stock contains invalid demand moments or ledger counters"
        )
    if stock[DEMAND_MOMENT_COLUMNS].lt(0).any().any():
        raise ValueError("monthly stock contains negative demand moments")
    stock["consumption_qty"] = stock["consumption_qty"].fillna(0)

    latest_data_month = (
        pd.Timestamp(stock["year_month"].max()).to_period("M").to_timestamp()
    )
    latest_data_date = pd.Timestamp(stock["last_date"].max()).normalize()
    recent_months = int(policy["recent_activity_months"])
    recent_start = latest_data_month - pd.DateOffset(months=recent_months - 1)
    stock["recent_demand_component"] = stock[
        "normal_outbound_nonnegative_sum"
    ].where(
        stock["year_month"].between(recent_start, latest_data_month),
        0.0,
    )

    ordered = stock.sort_values([*SERIES_KEYS, "year_month"], kind="mergesort")
    latest = ordered.groupby(SERIES_KEYS, sort=False, as_index=False).tail(1)
    latest = latest[
        [
            *SERIES_KEYS,
            "item_name",
            "year_month",
            "month_end_stock",
        ]
    ].rename(
        columns={
            "year_month": "latest_observation_month",
            "month_end_stock": "reported_on_hand",
        }
    )
    totals = (
        stock.groupby(SERIES_KEYS, sort=False, observed=True)
        .agg(
            all_time_normal_outbound=(
                "normal_outbound_nonnegative_sum",
                "sum",
            ),
            recent_normal_outbound=("recent_demand_component", "sum"),
            observation_month_count=("year_month", "nunique"),
            first_observation_date=("first_date", "min"),
            normal_outbound_squared_sum=(
                "normal_outbound_squared_sum",
                "sum",
            ),
            ledger_document_rule_violation_count=(
                "ledger_document_rule_violation_count",
                "sum",
            ),
            ledger_physical_violation_count=(
                "ledger_physical_violation_count",
                "sum",
            ),
            ledger_opening_stock_missing_count=(
                "ledger_opening_stock_missing_count",
                "sum",
            ),
        )
        .reset_index()
    )
    result = latest.merge(totals, on=SERIES_KEYS, how="inner", validate="one_to_one")
    result["stock_item_key"] = (
        result["institution_code"]
        + "::"
        + result["department"]
        + "::"
        + result["item_code"]
    )
    result["local_item_key"] = (
        result["institution_code"] + "::" + result["item_code"]
    )
    result["observation_lag_months"] = _month_difference(
        latest_data_month,
        result["latest_observation_month"],
    )
    result["is_stale_observation"] = result["observation_lag_months"].gt(
        int(policy["maximum_observation_lag_months"])
    )
    result["on_hand_missing"] = result["reported_on_hand"].isna()
    result["on_hand"] = (
        result["reported_on_hand"].fillna(0).clip(lower=0).astype("float64")
    )
    result["observation_period_days"] = (
        latest_data_date - result["first_observation_date"]
    ).dt.days.add(1).astype("int32")
    if result["observation_period_days"].le(0).any():
        raise ValueError("inventory observation period must be positive")
    result["raw_mean_daily_usage"] = result["all_time_normal_outbound"].div(
        result["observation_period_days"]
    )
    second_moment = result["normal_outbound_squared_sum"].div(
        result["observation_period_days"]
    )
    variance = (
        second_moment - result["raw_mean_daily_usage"].pow(2)
    ).clip(lower=0)
    result["raw_daily_demand_stddev"] = variance.pow(0.5)
    demand_parameters = policy["demand_parameters"]
    result["mean_daily_usage"] = result["raw_mean_daily_usage"].clip(
        lower=float(demand_parameters["mean_daily_usage_floor"])
    )
    result["daily_demand_stddev"] = result[
        "raw_daily_demand_stddev"
    ].clip(lower=float(demand_parameters["daily_demand_stddev_floor"]))
    forecast_window_days = int(demand_parameters["forecast_window_days"])
    result["mu_forecast_3m_92d"] = result["recent_normal_outbound"].div(
        forecast_window_days
    )
    return result, latest_data_month, latest_data_date


def _attach_classification(
    series: pd.DataFrame,
    classification_mapping: pd.DataFrame,
) -> pd.DataFrame:
    _require_columns(
        classification_mapping,
        ["local_item_key", *CLASSIFICATION_COLUMNS],
        "approved classification mapping",
    )
    if classification_mapping["local_item_key"].duplicated().any():
        raise ValueError("approved classification mapping is not unique by local_item_key")

    mapping = classification_mapping[
        ["local_item_key", *CLASSIFICATION_COLUMNS]
    ].copy()
    result = series.merge(
        mapping,
        on="local_item_key",
        how="left",
        validate="many_to_one",
        indicator=True,
    )
    result["classification_approved"] = result["_merge"].eq("both")
    result = result.drop(columns="_merge")
    result["is_forecastable"] = result["is_forecastable"].eq(True)
    return result


def _attach_group_stock(
    classified: pd.DataFrame,
    policy: dict[str, Any],
) -> pd.DataFrame:
    result = classified.copy()
    exact_fields = policy["substitution_group"]["exact_fields"]
    groupable = (
        result["classification_approved"]
        & ~result["is_stale_observation"]
        & ~result["on_hand_missing"]
    )
    result["exact_substitution_group_key"] = ""
    result.loc[groupable, "exact_substitution_group_key"] = (
        result.loc[groupable, ["institution_code", *exact_fields]]
        .astype("string")
        .agg("::".join, axis=1)
    )
    result["broad_family_group_key"] = ""
    result.loc[groupable, "broad_family_group_key"] = (
        result.loc[groupable, ["institution_code", "item_family_id"]]
        .astype("string")
        .agg("::".join, axis=1)
    )

    exact = (
        result.loc[groupable]
        .groupby("exact_substitution_group_key", observed=True)
        .agg(
            exact_group_total_stock=("on_hand", "sum"),
            exact_group_series_count=("stock_item_key", "nunique"),
            exact_group_item_code_count=("item_code", "nunique"),
        )
        .reset_index()
    )
    broad = (
        result.loc[groupable]
        .groupby("broad_family_group_key", observed=True)
        .agg(
            broad_family_total_stock=("on_hand", "sum"),
            broad_family_series_count=("stock_item_key", "nunique"),
            broad_family_item_code_count=("item_code", "nunique"),
        )
        .reset_index()
    )
    result = result.merge(
        exact,
        on="exact_substitution_group_key",
        how="left",
        validate="many_to_one",
    )
    result = result.merge(
        broad,
        on="broad_family_group_key",
        how="left",
        validate="many_to_one",
    )
    count_columns = [
        "exact_group_series_count",
        "exact_group_item_code_count",
        "broad_family_series_count",
        "broad_family_item_code_count",
    ]
    result[count_columns] = result[count_columns].fillna(0).astype("int32")
    for column in ["exact_group_total_stock", "broad_family_total_stock"]:
        result[column] = result[column].fillna(0.0)
    return result


def _classify_zero_stock_reason(
    frame: pd.DataFrame,
    policy: dict[str, Any],
) -> pd.DataFrame:
    result = frame.copy()
    threshold = float(policy["nonpositive_stock_threshold"])
    nonpositive = result["on_hand"].le(threshold)
    reason = pd.Series("IN_STOCK", index=result.index, dtype="string")
    reason = reason.mask(
        nonpositive & result["all_time_normal_outbound"].eq(0),
        "NOT_OPERATED",
    )
    reason = reason.mask(
        nonpositive
        & result["all_time_normal_outbound"].gt(0)
        & result["ledger_physical_violation_count"].gt(0),
        "DATA_MISSING",
    )
    reason = reason.mask(
        nonpositive
        & result["all_time_normal_outbound"].gt(0)
        & result["ledger_physical_violation_count"].eq(0),
        "TRUE_STOCKOUT",
    )
    reason = reason.mask(
        result["is_stale_observation"] | result["on_hand_missing"],
        "STALE_OR_MISSING_OBSERVATION",
    )
    result["zero_stock_reason"] = reason
    result["recent_demand_positive"] = result["recent_normal_outbound"].gt(0)
    result["alert_suppressed_by_exact_group_stock"] = (
        result["zero_stock_reason"].eq("TRUE_STOCKOUT")
        & result["classification_approved"]
        & result["exact_group_total_stock"].gt(threshold)
    )
    result["broad_family_has_stock_display_only"] = (
        result["classification_approved"]
        & result["broad_family_total_stock"].gt(threshold)
    )
    result["urgent_shortage"] = (
        result["zero_stock_reason"].eq("TRUE_STOCKOUT")
        & result["recent_demand_positive"]
        & result["classification_approved"]
        & result["is_forecastable"]
        & ~result["alert_suppressed_by_exact_group_stock"]
    )
    result["data_quality_alert"] = (
        result["zero_stock_reason"].eq("DATA_MISSING")
        & result["recent_demand_positive"]
    )
    return result


def _assign_action_status(
    frame: pd.DataFrame,
    policy: dict[str, Any],
) -> pd.DataFrame:
    result = frame.copy()
    action = pd.Series("OK", index=result.index, dtype="string")
    action = action.mask(
        result["zero_stock_reason"].eq("STALE_OR_MISSING_OBSERVATION"),
        "REVIEW_STALE_OR_MISSING_OBSERVATION",
    )
    action = action.mask(
        result["zero_stock_reason"].eq("NOT_OPERATED"),
        "FILTERED_NOT_OPERATED",
    )
    action = action.mask(
        result["zero_stock_reason"].eq("DATA_MISSING"),
        "REVIEW_DATA_QUALITY",
    )
    action = action.mask(
        result["zero_stock_reason"].eq("TRUE_STOCKOUT")
        & ~result["recent_demand_positive"],
        "MONITOR_NO_RECENT_DEMAND",
    )
    action = action.mask(
        result["zero_stock_reason"].eq("TRUE_STOCKOUT")
        & result["recent_demand_positive"]
        & ~result["classification_approved"],
        "REVIEW_CLASSIFICATION",
    )
    action = action.mask(
        result["zero_stock_reason"].eq("TRUE_STOCKOUT")
        & result["recent_demand_positive"]
        & result["classification_approved"]
        & ~result["is_forecastable"],
        "FILTERED_NON_FORECASTABLE",
    )
    action = action.mask(
        result["alert_suppressed_by_exact_group_stock"]
        & result["recent_demand_positive"]
        & result["is_forecastable"],
        "COVERED_BY_APPROVED_EQUIVALENT_STOCK",
    )
    action = action.mask(result["urgent_shortage"], "URGENT_SHORTAGE")
    result["inventory_action"] = action
    result["inventory_status_policy_version"] = policy["version"]
    return result


def build_inventory_status(
    monthly_stock: pd.DataFrame,
    classification_mapping: pd.DataFrame,
    policy: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, dict[str, object]]:
    policy = policy or load_inventory_status_policy()
    policy = validate_inventory_status_policy(policy)
    series, latest_data_month, latest_data_date = _prepare_series_status(
        monthly_stock,
        policy,
    )
    classified = _attach_classification(series, classification_mapping)
    grouped = _attach_group_stock(classified, policy)
    result = _assign_action_status(
        _classify_zero_stock_reason(grouped, policy),
        policy,
    )
    result = result.sort_values(
        ["inventory_action", *SERIES_KEYS],
        kind="mergesort",
    ).reset_index(drop=True)

    nonpositive = result["on_hand"].le(
        float(policy["nonpositive_stock_threshold"])
    ) & ~result["zero_stock_reason"].eq("STALE_OR_MISSING_OBSERVATION")
    raw_nonpositive_count = int(nonpositive.sum())
    urgent_count = int(result["urgent_shortage"].sum())
    report = {
        "version": policy["version"],
        "generated_from_latest_data_month": latest_data_month.strftime("%Y-%m"),
        "generated_from_latest_data_date": latest_data_date.strftime("%Y-%m-%d"),
        "series_count": int(len(result)),
        "raw_nonpositive_stock_count": raw_nonpositive_count,
        "classification_approved_count": int(
            result["classification_approved"].sum()
        ),
        "classification_coverage": float(
            result["classification_approved"].mean()
        ),
        "zero_stock_reason_counts": {
            str(key): int(value)
            for key, value in result["zero_stock_reason"].value_counts().items()
        },
        "inventory_action_counts": {
            str(key): int(value)
            for key, value in result["inventory_action"].value_counts().items()
        },
        "urgent_shortage_count": urgent_count,
        "recent_data_quality_alert_count": int(
            result["data_quality_alert"].sum()
        ),
        "exact_group_stock_suppression_count": int(
            result["alert_suppressed_by_exact_group_stock"].sum()
        ),
        "broad_family_stock_display_only_count": int(
            result["broad_family_has_stock_display_only"].sum()
        ),
        "document_rule_violation_rows": int(
            result["ledger_document_rule_violation_count"].sum()
        ),
        "physical_rule_violation_rows": int(
            result["ledger_physical_violation_count"].sum()
        ),
        "opening_stock_missing_rows": int(
            result["ledger_opening_stock_missing_count"].sum()
        ),
        "urgent_candidate_reduction_vs_raw_nonpositive": (
            float(1 - urgent_count / raw_nonpositive_count)
            if raw_nonpositive_count
            else 0.0
        ),
        "urgent_candidate_funnel": {
            "raw_nonpositive": raw_nonpositive_count,
            "true_stockout_with_recent_demand": int(
                (
                    result["zero_stock_reason"].eq("TRUE_STOCKOUT")
                    & result["recent_demand_positive"]
                ).sum()
            ),
            "approved_forecastable": int(
                (
                    result["zero_stock_reason"].eq("TRUE_STOCKOUT")
                    & result["recent_demand_positive"]
                    & result["classification_approved"]
                    & result["is_forecastable"]
                ).sum()
            ),
            "suppressed_by_exact_group_stock": int(
                (
                    result["alert_suppressed_by_exact_group_stock"]
                    & result["recent_demand_positive"]
                    & result["is_forecastable"]
                ).sum()
            ),
            "urgent_shortage": urgent_count,
        },
        "candidate_reduction_is_accuracy_metric": False,
        "group_suppression_scope": (
            "approved institution + family + subtype + specification + unit"
        ),
        "broad_family_grouping_is_display_only": True,
        "pdf_reference_counts_reused_as_measured_results": False,
        "daily_demand_parameter_method": (
            "mu=sum(q)/T; sigma=sqrt(sum(q^2)/T-mu^2); missing days are zero"
        ),
        "daily_demand_parameter_floors": {
            "mean_daily_usage": float(
                policy["demand_parameters"]["mean_daily_usage_floor"]
            ),
            "daily_demand_stddev": float(
                policy["demand_parameters"]["daily_demand_stddev_floor"]
            ),
        },
        "negative_normal_outbound_handling": policy["demand_parameters"][
            "negative_normal_outbound_handling"
        ],
    }
    return result, report


def select_inventory_status_sample(
    frame: pd.DataFrame,
    sample_size: int = 1000,
) -> pd.DataFrame:
    if sample_size <= 0:
        raise ValueError("sample_size must be positive")
    if frame.empty:
        return frame.copy()
    ordered = frame.sort_values(
        [
            "inventory_action",
            "recent_normal_outbound",
            "all_time_normal_outbound",
            *SERIES_KEYS,
        ],
        ascending=[True, False, False, True, True, True],
        kind="mergesort",
    )
    groups = max(1, ordered["inventory_action"].nunique())
    per_group = max(1, sample_size // groups)
    selected = ordered.groupby(
        "inventory_action",
        sort=True,
        group_keys=False,
    ).head(per_group)
    if len(selected) < sample_size:
        remaining = ordered.loc[~ordered.index.isin(selected.index)].head(
            sample_size - len(selected)
        )
        selected = pd.concat([selected, remaining])
    return selected.head(sample_size).reset_index(drop=True)


def run_inventory_status(
    monthly_path: Path = MONTHLY_STOCK_PATH,
    output_path: Path = INVENTORY_STATUS_PATH,
    report_path: Path = INVENTORY_STATUS_REPORT_PATH,
    sample_path: Path = INVENTORY_STATUS_SAMPLE_PATH,
    policy_path: Path = INVENTORY_STATUS_POLICY_PATH,
    sample_size: int = 1000,
) -> dict[str, Path]:
    setup_logging()
    if not monthly_path.exists():
        raise FileNotFoundError(
            f"Monthly stock not found: {monthly_path}. Run preprocessing first."
        )
    monthly = pd.read_parquet(monthly_path)
    classifications = load_approved_classifications()
    policy = load_inventory_status_policy(policy_path)
    result, report = build_inventory_status(
        monthly,
        classifications.mappings,
        policy=policy,
    )
    report["approved_classification_rows"] = int(classifications.approved_rows)
    report["ignored_unapproved_classification_rows"] = int(
        classifications.ignored_unapproved_rows
    )
    sample = select_inventory_status_sample(result, sample_size=sample_size)

    ensure_dirs(output_path.parent, report_path.parent, sample_path.parent)
    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    sample.to_csv(sample_path, index=False, encoding="utf-8-sig")
    with report_path.open("w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
    LOGGER.info("Saved inventory status: %s (%s rows)", output_path, len(result))
    LOGGER.info("Saved inventory status report: %s", report_path)
    LOGGER.info("Saved inventory status sample: %s (%s rows)", sample_path, len(sample))
    return {
        "output": output_path,
        "report": report_path,
        "sample": sample_path,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Classify zero-stock reasons and approved equivalent-family coverage"
    )
    parser.add_argument("--monthly-path", type=Path, default=MONTHLY_STOCK_PATH)
    parser.add_argument("--output-path", type=Path, default=INVENTORY_STATUS_PATH)
    parser.add_argument("--report-path", type=Path, default=INVENTORY_STATUS_REPORT_PATH)
    parser.add_argument("--sample-path", type=Path, default=INVENTORY_STATUS_SAMPLE_PATH)
    parser.add_argument(
        "--policy-path",
        type=Path,
        default=INVENTORY_STATUS_POLICY_PATH,
    )
    parser.add_argument("--sample-size", type=int, default=1000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_inventory_status(
        monthly_path=args.monthly_path,
        output_path=args.output_path,
        report_path=args.report_path,
        sample_path=args.sample_path,
        policy_path=args.policy_path,
        sample_size=args.sample_size,
    )


if __name__ == "__main__":
    main()
