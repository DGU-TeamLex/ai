import json
from datetime import datetime, timezone

import pandas as pd

from ..config import (
    FORECAST_DATA_QUALITY_REPORT_PATH,
    HISTORICAL_TRAIN_END,
    HISTORICAL_TRAIN_START,
    ITEM_ALIAS_CANDIDATE_PATH,
    TEST_START,
    TRAIN_END,
    TRAIN_START,
    VALID_END,
    VALID_START,
)


STANDARDIZATION_COLUMNS = [
    "normalization_status",
    "item_group_id_candidate",
    "item_family_id_candidate",
]

# A row carrying none of these signals says nothing about the item, so it can
# lose to a row that does without discarding any product identity.
UNRESOLVED_NORMALIZATION_STATUS = "unresolved"
UNRESOLVED_ITEM_GROUP = "UNCLASSIFIED"


def _resolve_alias_duplicates(aliases: pd.DataFrame) -> pd.DataFrame:
    """Collapse duplicate local_item_key rows without picking between products.

    Two relaxations are safe and nothing else is:

    1. Rows identical across every column we read are redundant.
    2. A key whose remaining rows are one classified row plus unresolved
       placeholders keeps the classified row. A key that is placeholders all
       the way down keeps one of them, so the key itself never disappears.

    A key left with two genuinely different classifications is a real identity
    conflict, so it still raises and has to be fixed upstream.
    """
    deduped = aliases.drop_duplicates()
    conflicting = deduped["local_item_key"].duplicated(keep=False)
    if not conflicting.any():
        return deduped

    unresolved = deduped["normalization_status"].astype("string").fillna("").eq(
        UNRESOLVED_NORMALIZATION_STATUS
    ) & deduped["item_group_id_candidate"].astype("string").fillna("").eq(
        UNRESOLVED_ITEM_GROUP
    )
    resolved_per_key = (
        (~unresolved & conflicting).groupby(deduped["local_item_key"]).sum()
    )
    ambiguous = resolved_per_key[resolved_per_key > 1].index
    if len(ambiguous):
        raise ValueError(
            "item alias mapping has conflicting classifications for "
            f"{len(ambiguous)} local_item_key(s): {sorted(ambiguous)[:5]}"
        )
    ordered = deduped.assign(_is_placeholder=unresolved).sort_values(
        ["local_item_key", "_is_placeholder"], kind="stable"
    )
    kept = ordered.drop_duplicates("local_item_key", keep="first")
    return kept.sort_index().drop(columns="_is_placeholder")


def attach_standardization_metadata(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    if not ITEM_ALIAS_CANDIDATE_PATH.exists():
        for column in STANDARDIZATION_COLUMNS:
            result[column] = pd.NA
        return result

    aliases = pd.read_parquet(
        ITEM_ALIAS_CANDIDATE_PATH,
        columns=["local_item_key", *STANDARDIZATION_COLUMNS],
    )
    aliases = _resolve_alias_duplicates(aliases)
    if aliases["local_item_key"].duplicated().any():
        raise ValueError("item alias mapping must have one row per local_item_key")
    result["local_item_key"] = (
        result["institution_code"].astype(str) + "::" + result["item_code"].astype(str)
    )
    return result.merge(aliases, on="local_item_key", how="left", validate="many_to_one")


def _split_counts(labeled: pd.DataFrame) -> dict[str, int]:
    historical_eligible = labeled.get(
        "historical_training_eligible",
        pd.Series(False, index=labeled.index),
    ).fillna(False)
    historical = (
        labeled["year_month"].between(
            pd.Timestamp(HISTORICAL_TRAIN_START),
            pd.Timestamp(HISTORICAL_TRAIN_END),
        )
        & historical_eligible
    )
    current_train = labeled["year_month"].between(
        pd.Timestamp(TRAIN_START),
        pd.Timestamp(TRAIN_END),
    )
    return {
        "train_rows": int((historical | current_train).sum()),
        "historical_train_rows": int(historical.sum()),
        "current_train_rows": int(current_train.sum()),
        "validation_rows": int(
            labeled["year_month"].between(pd.Timestamp(VALID_START), pd.Timestamp(VALID_END)).sum()
        ),
        "test_rows": int((labeled["year_month"] >= pd.Timestamp(TEST_START)).sum()),
    }


def build_forecast_data_quality_report(
    monthly_stock: pd.DataFrame,
    feature_table: pd.DataFrame,
) -> dict[str, object]:
    series = monthly_stock.groupby("stock_item_key", observed=True).agg(
        observed_months=("year_month", "nunique"),
        item_names=("item_name", "nunique"),
    )
    segment_lengths = feature_table.groupby("series_segment_id", observed=True)["history_months"].max()
    labeled = feature_table[
        feature_table["target_usage"].notna()
        & feature_table["target_usage"].ge(0)
        & feature_table["lag_1"].notna()
    ]
    latest_month = pd.Timestamp(monthly_stock["year_month"].max())
    current_month = pd.Timestamp.now().to_period("M").to_timestamp()
    data_age_months = (current_month.year - latest_month.year) * 12 + current_month.month - latest_month.month

    if "standardization_match_method" in monthly_stock.columns:
        status_input = (
            monthly_stock.groupby(
                [
                    "data_period",
                    "standardization_match_method",
                    "historical_training_eligible",
                ],
                observed=True,
                dropna=False,
            )["consumption_qty"]
            .agg(rows="size", usage_sum="sum")
            .reset_index()
            .rename(
                columns={
                    "standardization_match_method": "normalization_status",
                }
            )
        )
    else:
        status_input = (
            monthly_stock.groupby(
                ["institution_code", "item_code"],
                observed=True,
            )["consumption_qty"]
            .agg(rows="size", usage_sum="sum")
            .reset_index()
        )
        status_input = attach_standardization_metadata(status_input)
    status_group_columns = ["normalization_status"]
    for column in ["data_period", "historical_training_eligible"]:
        if column in status_input.columns:
            status_group_columns.append(column)
    status_summary = (
        status_input.groupby(
            status_group_columns,
            dropna=False,
            observed=True,
        )
        .agg(rows=("rows", "sum"), usage_sum=("usage_sum", "sum"))
        .reset_index()
    )
    status_summary = status_summary[status_summary["rows"].gt(0)]
    total_rows = int(status_input["rows"].sum())
    total_usage = float(status_input["usage_sum"].sum())
    standardization = []
    for row in status_summary.itertuples(index=False):
        status = "missing" if pd.isna(row.normalization_status) else str(row.normalization_status)
        standardization.append(
            {
                "normalization_status": status,
                "data_period": (
                    str(row.data_period)
                    if hasattr(row, "data_period")
                    else "current"
                ),
                "historical_training_eligible": (
                    bool(row.historical_training_eligible)
                    if hasattr(row, "historical_training_eligible")
                    else None
                ),
                "rows": int(row.rows),
                "row_pct": float(row.rows / total_rows * 100),
                "usage_sum": float(row.usage_sum),
                "usage_pct": float(row.usage_sum / total_usage * 100) if total_usage else None,
            }
        )

    observed_quantiles = series["observed_months"].quantile([0.25, 0.5, 0.75, 0.9, 0.95])
    segment_quantiles = segment_lengths.quantile([0.25, 0.5, 0.75, 0.9, 0.95])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "raw_stock/*.DAT",
        "monthly_rows": int(len(monthly_stock)),
        "series_count": int(len(series)),
        "first_month": str(monthly_stock["year_month"].min().date()),
        "last_month": str(latest_month.date()),
        "next_forecast_month": str((latest_month + pd.offsets.MonthBegin(1)).date()),
        "data_age_months": int(data_age_months),
        "consumption": {
            "missing_rows": int(monthly_stock["consumption_qty"].isna().sum()),
            "negative_rows": int(monthly_stock["consumption_qty"].lt(0).sum()),
            "negative_sum": float(
                monthly_stock.loc[monthly_stock["consumption_qty"].lt(0), "consumption_qty"].sum()
            ),
            "zero_rows": int(monthly_stock["consumption_qty"].eq(0).sum()),
            "zero_row_pct": float(monthly_stock["consumption_qty"].eq(0).mean() * 100),
        },
        "series_quality": {
            "multiple_monthly_name_series": int(series["item_names"].gt(1).sum()),
            "series_with_at_least_6_months": int(series["observed_months"].ge(6).sum()),
            "series_with_at_least_12_months": int(series["observed_months"].ge(12).sum()),
            "observed_month_quantiles": {
                str(key): float(value) for key, value in observed_quantiles.items()
            },
            "contiguous_segment_count": int(len(segment_lengths)),
            "contiguous_segment_length_quantiles": {
                str(key): float(value) for key, value in segment_quantiles.items()
            },
        },
        "labeled_rows": int(len(labeled)),
        "time_split": _split_counts(labeled),
        "standardization_coverage": standardization,
        "readiness": {
            "local_series_forecast": "ready_with_quality_controls",
            "standardization_required_for_local_series_forecast": False,
            "standardization_required_for_cross_institution_pooling": True,
            "standardization_used_for_model_hierarchy": True,
            "standardization_required_for_news_material_risk": True,
            "limitations": [
                "Series with fewer than six observed months have weak item-specific history.",
                "A missing month is treated as an observation gap, not as zero demand.",
                "Negative consumption is excluded from labels instead of being silently corrected.",
                "The latest available raw_stock month may be stale for current operations.",
                "Historical-only standardized names are excluded from model fitting.",
            ],
        },
    }


def write_forecast_data_quality_report(
    monthly_stock: pd.DataFrame,
    feature_table: pd.DataFrame,
) -> dict[str, object]:
    report = build_forecast_data_quality_report(monthly_stock, feature_table)
    FORECAST_DATA_QUALITY_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    FORECAST_DATA_QUALITY_REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report
