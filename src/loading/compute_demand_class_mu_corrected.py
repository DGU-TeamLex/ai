from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

from ..config import (
    CENSORED_DEMAND_METRICS_PATH,
    CSV_CHUNK_SIZE,
    DEMAND_CLASS_HANDOFF_PATH,
    DEMAND_CLASS_REPORT_PATH,
    DEMAND_CLASS_SAMPLE_PATH,
    RAW_STOCK_DIR,
    RAW_STOCK_FILE_PATTERN,
)
from ..data_loader import (
    _read_stock_chunks,
    discover_raw_stock_files,
    normalize_stock_chunk,
)
from ..utils import ensure_dirs, setup_logging


LOGGER = logging.getLogger(__name__)
LOCAL_KEYS = ["institution_code", "department", "item_code"]
INSTITUTION_ITEM_KEYS = ["institution_code", "item_code"]
VALID_DEMAND_CLASSES = {"ACTIVE", "CENSORED", "DORMANT", "NOT_VERIFIED"}
ZERO_RATIO_THRESHOLD = 0.50
CONTROL_ZERO_RATIO_MAX = 0.10
MIN_INVENTORY_COVERAGE = 0.90
MIN_HELD_DAYS_FOR_AUTOMATIC_LOAD = 30
MAX_POSITIVE_NAIVE_CORRECTION_FACTOR = 8.0
MIN_PRIOR_EXPOSURE_DAYS = 365
MIN_INSTITUTIONS_FOR_ITEM_PRIOR = 5
K_FALLBACK_CAP_PERCENTILE = 90
RATIO_CAP_PERCENTILE = 95
EXPECTED_CENSORED_SHARE_MIN = 0.20
EXPECTED_CENSORED_SHARE_MAX = 0.25
DEFINITION_VERSION = "censored-demand-daily-v1.0"
CORRECTION_POLICY_VERSION = "buhlmann-daily-exposure-v1.1"


def _require_columns(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing required columns: {missing}")


def _duration_metrics_for_daily_rows(
    daily_rows: pd.DataFrame,
    period_end: pd.Timestamp,
) -> pd.DataFrame:
    required = [
        *LOCAL_KEYS,
        "closing_date",
        "closing_stock",
        "consumption_qty",
        "negative_consumption_rows",
    ]
    _require_columns(daily_rows, required, "daily stock rows")
    if daily_rows.empty:
        return pd.DataFrame()

    daily = daily_rows.copy()
    daily["closing_date"] = pd.to_datetime(daily["closing_date"], errors="coerce")
    if daily["closing_date"].isna().any():
        raise ValueError("Daily stock rows contain invalid closing dates")
    period_end = pd.Timestamp(period_end).normalize()
    if daily["closing_date"].max() > period_end:
        raise ValueError("period_end precedes a stock observation")

    daily = daily.sort_values([*LOCAL_KEYS, "closing_date"], kind="mergesort")
    grouped = daily.groupby(LOCAL_KEYS, sort=False, observed=True)
    next_date = grouped["closing_date"].shift(-1)
    end_exclusive = next_date.fillna(period_end + pd.Timedelta(days=1))
    duration_days = (end_exclusive - daily["closing_date"]).dt.days
    if duration_days.le(0).any():
        raise ValueError("Stock observation intervals must be positive")

    daily["interval_days"] = duration_days.astype("int32")
    known = daily["closing_stock"].notna()
    daily["zero_stock_days"] = daily["interval_days"].where(
        known & daily["closing_stock"].le(0),
        0,
    )
    daily["held_stock_days"] = daily["interval_days"].where(
        known & daily["closing_stock"].gt(0),
        0,
    )
    daily["unknown_stock_days"] = daily["interval_days"].where(~known, 0)

    metrics_grouped = daily.groupby(LOCAL_KEYS, sort=False, observed=True)
    metrics = metrics_grouped.agg(
        first_observation_date=("closing_date", "min"),
        last_observation_date=("closing_date", "max"),
        transaction_days=("closing_date", "nunique"),
        demand_total=("consumption_qty", "sum"),
        total_days=("interval_days", "sum"),
        zero_stock_days=("zero_stock_days", "sum"),
        held_stock_days=("held_stock_days", "sum"),
        unknown_stock_days=("unknown_stock_days", "sum"),
        negative_consumption_rows=("negative_consumption_rows", "sum"),
    ).reset_index()
    metrics["period_end"] = period_end
    metrics["zero_ratio"] = metrics["zero_stock_days"].div(
        metrics["total_days"].replace(0, np.nan)
    )
    metrics["inventory_coverage"] = (
        metrics["zero_stock_days"] + metrics["held_stock_days"]
    ).div(metrics["total_days"].replace(0, np.nan))
    metrics["mu_naive"] = metrics["demand_total"].div(
        metrics["total_days"].replace(0, np.nan)
    )
    metrics["mu_available_only_raw"] = metrics["demand_total"].div(
        metrics["held_stock_days"].replace(0, np.nan)
    )
    metrics["underestimation_factor_raw"] = metrics[
        "mu_available_only_raw"
    ].div(metrics["mu_naive"].replace(0, np.nan))
    metrics["metric_grain"] = "institution_department_item"
    metrics["definition_version"] = DEFINITION_VERSION
    return metrics


def build_local_censored_metrics(
    normalized_stock: pd.DataFrame,
    period_end: pd.Timestamp | None = None,
) -> pd.DataFrame:
    required = [
        *LOCAL_KEYS,
        "closing_date",
        "closing_stock",
        "consumption_qty",
    ]
    _require_columns(normalized_stock, required, "normalized stock")
    if normalized_stock.empty:
        raise ValueError("Normalized stock input is empty")

    rows = normalized_stock[required].copy()
    rows["closing_date"] = pd.to_datetime(rows["closing_date"], errors="coerce")
    rows = rows[rows["closing_date"].notna()].copy()
    if rows.empty:
        raise ValueError("No valid stock dates are available")
    rows["_source_order"] = np.arange(len(rows), dtype="int64")
    consumption = pd.to_numeric(rows["consumption_qty"], errors="coerce").fillna(0.0)
    rows["negative_consumption_rows"] = consumption.lt(0).astype("int16")
    rows["consumption_qty"] = consumption.clip(lower=0.0)
    rows["closing_stock"] = pd.to_numeric(rows["closing_stock"], errors="coerce")
    rows = rows.sort_values("_source_order", kind="mergesort")
    daily = rows.groupby(
        [*LOCAL_KEYS, "closing_date"],
        as_index=False,
        sort=False,
        observed=True,
    ).agg(
        closing_stock=("closing_stock", "last"),
        consumption_qty=("consumption_qty", "sum"),
        negative_consumption_rows=("negative_consumption_rows", "sum"),
    )
    return _duration_metrics_for_daily_rows(
        daily,
        period_end=period_end or rows["closing_date"].max(),
    )


def _write_raw_partitions(
    raw_dir: Path,
    pattern: str,
    chunk_size: int,
    bucket_count: int,
    partition_dir: Path,
) -> tuple[list[Path], pd.Timestamp, int]:
    files = discover_raw_stock_files(raw_dir, pattern)
    if not files:
        raise FileNotFoundError(
            f"No raw_stock DAT files found under {raw_dir} with pattern {pattern}"
        )
    if bucket_count <= 0:
        raise ValueError("bucket_count must be positive")

    partition_paths = [partition_dir / f"daily_bucket_{index:03d}.csv" for index in range(bucket_count)]
    source_offset = 0
    period_end: pd.Timestamp | None = None
    valid_rows = 0
    for path in files:
        LOGGER.info("Partitioning censored-demand input: %s", path)
        for raw_chunk in _read_stock_chunks(path, chunk_size):
            normalized = normalize_stock_chunk(raw_chunk)
            if normalized.empty:
                continue
            partial = normalized[
                [*LOCAL_KEYS, "closing_date", "closing_stock", "consumption_qty"]
            ].copy()
            partial["_source_order"] = np.arange(
                source_offset,
                source_offset + len(partial),
                dtype="int64",
            )
            source_offset += len(partial)
            valid_rows += len(partial)
            chunk_end = partial["closing_date"].max()
            period_end = chunk_end if period_end is None else max(period_end, chunk_end)
            hash_values = pd.util.hash_pandas_object(
                partial[LOCAL_KEYS].astype(str),
                index=False,
            )
            partial["_bucket"] = (hash_values % bucket_count).astype("int16")
            for bucket, bucket_frame in partial.groupby("_bucket", sort=False):
                destination = partition_paths[int(bucket)]
                bucket_frame.drop(columns="_bucket").to_csv(
                    destination,
                    mode="a",
                    header=not destination.exists(),
                    index=False,
                )
    if period_end is None or valid_rows == 0:
        raise ValueError("No valid raw_stock rows remained after normalization")
    return [path for path in partition_paths if path.exists()], period_end, valid_rows


def build_censored_metrics_from_raw(
    raw_dir: Path = RAW_STOCK_DIR,
    pattern: str = RAW_STOCK_FILE_PATTERN,
    chunk_size: int = CSV_CHUNK_SIZE,
    bucket_count: int = 64,
    temp_dir: Path | None = None,
) -> pd.DataFrame:
    with tempfile.TemporaryDirectory(
        prefix="wep_stock_censored_",
        dir=str(temp_dir) if temp_dir else None,
    ) as temporary_directory:
        partition_dir = Path(temporary_directory)
        partition_paths, period_end, valid_rows = _write_raw_partitions(
            raw_dir,
            pattern,
            chunk_size,
            bucket_count,
            partition_dir,
        )
        LOGGER.info(
            "Building daily duration metrics from %s rows in %s buckets",
            valid_rows,
            len(partition_paths),
        )
        local_outputs = []
        for index, partition_path in enumerate(partition_paths, start=1):
            LOGGER.info(
                "Processing censored-demand bucket %s/%s",
                index,
                len(partition_paths),
            )
            rows = pd.read_csv(
                partition_path,
                dtype={column: str for column in LOCAL_KEYS},
                parse_dates=["closing_date"],
            )
            rows = rows.sort_values("_source_order", kind="mergesort")
            consumption = pd.to_numeric(
                rows["consumption_qty"], errors="coerce"
            ).fillna(0.0)
            rows["negative_consumption_rows"] = consumption.lt(0).astype("int16")
            rows["consumption_qty"] = consumption.clip(lower=0.0)
            rows["closing_stock"] = pd.to_numeric(
                rows["closing_stock"], errors="coerce"
            )
            daily = rows.groupby(
                [*LOCAL_KEYS, "closing_date"],
                as_index=False,
                sort=False,
                observed=True,
            ).agg(
                closing_stock=("closing_stock", "last"),
                consumption_qty=("consumption_qty", "sum"),
                negative_consumption_rows=("negative_consumption_rows", "sum"),
            )
            local_outputs.append(
                _duration_metrics_for_daily_rows(daily, period_end=period_end)
            )
        result = pd.concat(local_outputs, ignore_index=True)
    return result.sort_values(LOCAL_KEYS, kind="mergesort").reset_index(drop=True)


def aggregate_institution_item_metrics(local_metrics: pd.DataFrame) -> pd.DataFrame:
    required = [
        *LOCAL_KEYS,
        "first_observation_date",
        "last_observation_date",
        "transaction_days",
        "demand_total",
        "total_days",
        "zero_stock_days",
        "held_stock_days",
        "unknown_stock_days",
        "negative_consumption_rows",
        "period_end",
    ]
    _require_columns(local_metrics, required, "local censored-demand metrics")
    result = local_metrics.groupby(
        INSTITUTION_ITEM_KEYS,
        as_index=False,
        sort=False,
        observed=True,
    ).agg(
        department_count=("department", "nunique"),
        first_observation_date=("first_observation_date", "min"),
        last_observation_date=("last_observation_date", "max"),
        transaction_days=("transaction_days", "sum"),
        demand_total=("demand_total", "sum"),
        total_days=("total_days", "sum"),
        zero_stock_days=("zero_stock_days", "sum"),
        held_stock_days=("held_stock_days", "sum"),
        unknown_stock_days=("unknown_stock_days", "sum"),
        negative_consumption_rows=("negative_consumption_rows", "sum"),
        period_end=("period_end", "max"),
    )
    result["zero_ratio"] = result["zero_stock_days"].div(
        result["total_days"].replace(0, np.nan)
    )
    result["inventory_coverage"] = (
        result["zero_stock_days"] + result["held_stock_days"]
    ).div(result["total_days"].replace(0, np.nan))
    result["mu_naive"] = result["demand_total"].div(
        result["total_days"].replace(0, np.nan)
    )
    result["mu_available_only_raw"] = result["demand_total"].div(
        result["held_stock_days"].replace(0, np.nan)
    )
    result["underestimation_factor_raw"] = result[
        "mu_available_only_raw"
    ].div(result["mu_naive"].replace(0, np.nan))
    result["metric_grain"] = "institution_item"
    result["definition_version"] = DEFINITION_VERSION
    return result


def _buhlmann_item_parameters(reliable: pd.DataFrame) -> pd.DataFrame:
    records = []
    for item_code, group in reliable.groupby("item_code", sort=False, observed=True):
        weights = group["held_stock_days"].to_numpy(dtype="float64")
        rates = group["available_rate"].to_numpy(dtype="float64")
        item_mean = float(np.average(rates, weights=weights))
        count = len(group)
        credibility_k = np.nan
        if count >= MIN_INSTITUTIONS_FOR_ITEM_PRIOR:
            process_variance = float(
                np.average(item_mean / weights, weights=weights)
            )
            observed_variance = float(
                np.average((rates - item_mean) ** 2, weights=weights)
            )
            between_variance = observed_variance - process_variance
            if between_variance > 0:
                credibility_k = process_variance / between_variance
        records.append(
            {
                "item_code": item_code,
                "prior_mean": item_mean,
                "credibility_k": credibility_k,
                "prior_institution_count": count,
            }
        )
    return pd.DataFrame(records)


def classify_and_correct_demand(
    institution_metrics: pd.DataFrame,
) -> pd.DataFrame:
    required = [
        *INSTITUTION_ITEM_KEYS,
        "demand_total",
        "total_days",
        "zero_stock_days",
        "held_stock_days",
        "unknown_stock_days",
        "zero_ratio",
        "inventory_coverage",
        "mu_naive",
    ]
    _require_columns(institution_metrics, required, "institution-item metrics")
    result = institution_metrics.copy()

    verified = result["inventory_coverage"].ge(MIN_INVENTORY_COVERAGE)
    result["demand_class"] = np.select(
        [
            ~verified,
            result["demand_total"].eq(0)
            & result["zero_ratio"].lt(ZERO_RATIO_THRESHOLD),
            result["zero_ratio"].ge(ZERO_RATIO_THRESHOLD),
        ],
        ["NOT_VERIFIED", "DORMANT", "CENSORED"],
        default="ACTIVE",
    )
    result["available_rate"] = result["demand_total"].div(
        result["held_stock_days"].replace(0, np.nan)
    )
    reliable = result[
        verified
        & result["zero_ratio"].le(CONTROL_ZERO_RATIO_MAX)
        & result["held_stock_days"].ge(MIN_PRIOR_EXPOSURE_DAYS)
        & result["demand_total"].gt(0)
        & result["available_rate"].notna()
    ].copy()
    if reliable.empty:
        raise ValueError("No reliable inventory-available series exist for prior estimation")

    item_parameters = _buhlmann_item_parameters(reliable)
    finite_k = item_parameters["credibility_k"].dropna()
    k_cap = (
        float(finite_k.quantile(K_FALLBACK_CAP_PERCENTILE / 100.0))
        if not finite_k.empty
        else float(MIN_PRIOR_EXPOSURE_DAYS)
    )
    if not np.isfinite(k_cap) or k_cap <= 0:
        k_cap = float(MIN_PRIOR_EXPOSURE_DAYS)
    item_parameters["credibility_k"] = item_parameters[
        "credibility_k"
    ].fillna(k_cap).clip(lower=0.0, upper=k_cap)
    established_k = item_parameters.loc[
        item_parameters["prior_institution_count"].ge(
            MIN_INSTITUTIONS_FOR_ITEM_PRIOR
        ),
        "credibility_k",
    ]
    global_k_fallback = (
        float(established_k.median())
        if not established_k.empty
        else k_cap
    )
    item_parameters.loc[
        item_parameters["prior_institution_count"].lt(
            MIN_INSTITUTIONS_FOR_ITEM_PRIOR
        ),
        "credibility_k",
    ] = global_k_fallback
    global_prior_mean = float(
        np.average(
            reliable["available_rate"],
            weights=reliable["held_stock_days"],
        )
    )

    result = result.merge(
        item_parameters,
        on="item_code",
        how="left",
        validate="many_to_one",
    )
    result["prior_mean"] = result["prior_mean"].fillna(global_prior_mean)
    result["credibility_k"] = result["credibility_k"].fillna(
        global_k_fallback
    )
    result["prior_institution_count"] = result[
        "prior_institution_count"
    ].fillna(0).astype("int32")
    alpha = result["prior_mean"] * result["credibility_k"]
    result["mu_shrink"] = (alpha + result["demand_total"]).div(
        result["credibility_k"] + result["held_stock_days"]
    )

    true_zero_demand = (
        verified
        & result["demand_total"].eq(0)
        & result["zero_ratio"].le(CONTROL_ZERO_RATIO_MAX)
        & result["total_days"].ge(MIN_PRIOR_EXPOSURE_DAYS)
    )
    result.loc[true_zero_demand, "mu_shrink"] = result.loc[
        true_zero_demand,
        "mu_naive",
    ]
    reliable_with_prior = reliable.merge(
        item_parameters[["item_code", "prior_mean"]],
        on="item_code",
        how="left",
        validate="many_to_one",
    )
    reliable_ratio = reliable_with_prior["mu_naive"].div(
        reliable_with_prior["prior_mean"].replace(0, np.nan)
    )
    finite_ratio = reliable_ratio.replace([np.inf, -np.inf], np.nan).dropna()
    max_ratio = (
        float(finite_ratio.quantile(RATIO_CAP_PERCENTILE / 100.0))
        if not finite_ratio.empty
        else 10.0
    )
    if not np.isfinite(max_ratio) or max_ratio <= 0:
        max_ratio = 10.0
    needs_cap = result["held_stock_days"].lt(MIN_PRIOR_EXPOSURE_DAYS)
    cap_value = result["prior_mean"] * max_ratio
    result["mu_corrected"] = result["mu_shrink"]
    over_cap = needs_cap & result["mu_corrected"].gt(cap_value)
    result.loc[over_cap, "mu_corrected"] = cap_value[over_cap]
    positive_naive = result["mu_naive"].gt(0)
    hard_ratio_cap = result["mu_naive"] * MAX_POSITIVE_NAIVE_CORRECTION_FACTOR
    over_hard_ratio_cap = positive_naive & result["mu_corrected"].gt(
        hard_ratio_cap
    )
    result.loc[over_hard_ratio_cap, "mu_corrected"] = hard_ratio_cap[
        over_hard_ratio_cap
    ]
    result.loc[result["demand_class"].eq("DORMANT"), "mu_corrected"] = 0.0
    result.loc[result["demand_class"].eq("NOT_VERIFIED"), "mu_corrected"] = result.loc[
        result["demand_class"].eq("NOT_VERIFIED"),
        "mu_naive",
    ]
    result["correction_capped"] = over_cap | over_hard_ratio_cap
    result["hard_ratio_cap_applied"] = over_hard_ratio_cap
    result["correction_factor"] = result["mu_corrected"].div(
        result["mu_naive"].replace(0, np.nan)
    )
    no_observed_demand_rate = (
        result["demand_class"].eq("CENSORED")
        & result["mu_naive"].eq(0)
    )
    insufficient_held_days = (
        result["demand_class"].eq("CENSORED")
        & result["held_stock_days"].lt(MIN_HELD_DAYS_FOR_AUTOMATIC_LOAD)
    )
    result["review_required"] = (
        result["demand_class"].eq("NOT_VERIFIED")
        | no_observed_demand_rate
        | insufficient_held_days
    )
    review_reasons = pd.Series("", index=result.index, dtype="string")
    review_reasons = review_reasons.mask(
        result["demand_class"].eq("NOT_VERIFIED"),
        "INSUFFICIENT_INVENTORY_COVERAGE",
    )
    review_reasons = review_reasons.mask(
        no_observed_demand_rate,
        review_reasons.where(review_reasons.eq(""), review_reasons + ";")
        + "ZERO_NAIVE_DEMAND_UNDER_CENSORING",
    )
    review_reasons = review_reasons.mask(
        insufficient_held_days,
        review_reasons.where(review_reasons.eq(""), review_reasons + ";")
        + "HELD_DAYS_BELOW_30",
    )
    result["review_reason"] = review_reasons
    result["load_eligible"] = ~result["review_required"]
    result["correction_method"] = CORRECTION_POLICY_VERSION
    result["correction_policy_version"] = CORRECTION_POLICY_VERSION
    result["definition_version"] = DEFINITION_VERSION

    if not set(result["demand_class"]).issubset(VALID_DEMAND_CLASSES):
        raise ValueError("Unexpected demand class was generated")
    corrected = pd.to_numeric(result["mu_corrected"], errors="coerce")
    if corrected.isna().any() or ~np.isfinite(corrected).all() or corrected.lt(0).any():
        raise ValueError("mu_corrected must be finite and non-negative")
    return result.sort_values(
        INSTITUTION_ITEM_KEYS,
        kind="mergesort",
    ).reset_index(drop=True)


def build_quality_report(
    local_metrics: pd.DataFrame,
    classified: pd.DataFrame,
) -> dict[str, object]:
    class_counts = {
        str(key): int(value)
        for key, value in classified["demand_class"].value_counts().items()
    }
    verified = classified[classified["demand_class"].ne("NOT_VERIFIED")]
    censored_share = (
        float(verified["demand_class"].eq("CENSORED").mean())
        if not verified.empty
        else 0.0
    )
    share_in_expected_range = (
        EXPECTED_CENSORED_SHARE_MIN
        <= censored_share
        <= EXPECTED_CENSORED_SHARE_MAX
    )
    invalid_corrected = int(
        pd.to_numeric(classified["mu_corrected"], errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .isna()
        .sum()
    )
    duplicate_keys = int(
        classified[INSTITUTION_ITEM_KEYS].duplicated().sum()
    )
    review_load_overlap = int(
        (classified["review_required"] & classified["load_eligible"]).sum()
    )
    load_eligible_count = int(classified["load_eligible"].sum())
    eligible_subset_release_allowed = bool(
        share_in_expected_range
        and invalid_corrected == 0
        and duplicate_keys == 0
        and review_load_overlap == 0
        and load_eligible_count > 0
    )
    review_required_count = int(classified["review_required"].sum())
    full_batch_release_allowed = bool(
        eligible_subset_release_allowed and review_required_count == 0
    )
    return {
        "version": DEFINITION_VERSION,
        "correction_policy_version": CORRECTION_POLICY_VERSION,
        "max_positive_naive_correction_factor": (
            MAX_POSITIVE_NAIVE_CORRECTION_FACTOR
        ),
        "source": "raw_stock_daily_closing_stock_duration",
        "metric_grain": "institution_department_item",
        "handoff_grain": "institution_item",
        "zero_ratio_definition": "zero_stock_duration_days / total_duration_days",
        "local_series_count": int(len(local_metrics)),
        "institution_item_count": int(len(classified)),
        "class_counts": class_counts,
        "censored_share_verified": censored_share,
        "expected_censored_share_range": [
            EXPECTED_CENSORED_SHARE_MIN,
            EXPECTED_CENSORED_SHARE_MAX,
        ],
        "censored_share_in_expected_range": share_in_expected_range,
        "not_verified_count": int(class_counts.get("NOT_VERIFIED", 0)),
        "review_required_count": review_required_count,
        "load_eligible_count": load_eligible_count,
        "review_load_overlap_count": review_load_overlap,
        "hard_ratio_cap_applied_count": int(
            classified["hard_ratio_cap_applied"].sum()
        ),
        "negative_consumption_rows_excluded": int(
            local_metrics["negative_consumption_rows"].sum()
        ),
        "invalid_mu_corrected_count": invalid_corrected,
        "duplicate_handoff_key_count": duplicate_keys,
        "status_update_included": False,
        "institution_mapping_required_for_load": True,
        "review_rows_excluded_from_load": True,
        "eligible_subset_release_allowed": eligible_subset_release_allowed,
        "full_batch_release_allowed": full_batch_release_allowed,
        # Backward-compatible loader gate. Only load_eligible rows may pass it.
        "batch_release_allowed": eligible_subset_release_allowed,
        "quality_status": (
            "PASS"
            if full_batch_release_allowed
            else "PASS_WITH_REVIEW"
            if eligible_subset_release_allowed
            else "REVIEW"
        ),
    }


def _presentation_sample(classified: pd.DataFrame, sample_size: int) -> pd.DataFrame:
    if sample_size <= 0:
        raise ValueError("sample_size must be positive")
    ordered = classified.sort_values(
        ["demand_class", "zero_ratio", "demand_total", *INSTITUTION_ITEM_KEYS],
        ascending=[True, False, False, True, True],
        kind="mergesort",
    )
    per_class = max(1, sample_size // max(1, ordered["demand_class"].nunique()))
    selected = ordered.groupby(
        "demand_class",
        sort=True,
        group_keys=False,
    ).head(per_class)
    if len(selected) < sample_size:
        remaining = ordered.loc[~ordered.index.isin(selected.index)].head(
            sample_size - len(selected)
        )
        selected = pd.concat([selected, remaining])
    return selected.head(sample_size).reset_index(drop=True)


def run_compute(
    raw_dir: Path = RAW_STOCK_DIR,
    pattern: str = RAW_STOCK_FILE_PATTERN,
    metrics_path: Path = CENSORED_DEMAND_METRICS_PATH,
    handoff_path: Path = DEMAND_CLASS_HANDOFF_PATH,
    report_path: Path = DEMAND_CLASS_REPORT_PATH,
    sample_path: Path = DEMAND_CLASS_SAMPLE_PATH,
    chunk_size: int = CSV_CHUNK_SIZE,
    bucket_count: int = 64,
    sample_size: int = 1000,
    temp_dir: Path | None = None,
    reuse_metrics: bool = False,
) -> dict[str, Path]:
    setup_logging()
    if reuse_metrics:
        if not metrics_path.exists():
            raise FileNotFoundError(
                f"Cannot reuse missing censored-demand metrics: {metrics_path}"
            )
        local_metrics = pd.read_parquet(metrics_path)
        if (
            "definition_version" not in local_metrics.columns
            or not local_metrics["definition_version"].eq(DEFINITION_VERSION).all()
        ):
            raise ValueError("Existing censored-demand metrics use another definition")
    else:
        local_metrics = build_censored_metrics_from_raw(
            raw_dir=raw_dir,
            pattern=pattern,
            chunk_size=chunk_size,
            bucket_count=bucket_count,
            temp_dir=temp_dir,
        )
    institution_metrics = aggregate_institution_item_metrics(local_metrics)
    classified = classify_and_correct_demand(institution_metrics)
    report = build_quality_report(local_metrics, classified)

    ensure_dirs(
        metrics_path.parent,
        handoff_path.parent,
        report_path.parent,
        sample_path.parent,
    )
    local_metrics.to_parquet(metrics_path, index=False, compression="zstd")
    handoff_columns = [
        "institution_code",
        "item_code",
        "demand_class",
        "mu_corrected",
        "mu_naive",
        "zero_ratio",
        "inventory_coverage",
        "demand_total",
        "total_days",
        "zero_stock_days",
        "held_stock_days",
        "unknown_stock_days",
        "department_count",
        "prior_mean",
        "credibility_k",
        "correction_capped",
        "hard_ratio_cap_applied",
        "correction_factor",
        "review_required",
        "review_reason",
        "load_eligible",
        "correction_method",
        "correction_policy_version",
        "definition_version",
    ]
    handoff_source = classified[handoff_columns].copy()
    handoff = handoff_source.rename(
        columns={
            "institution_code": "anon_institution_code",
            "item_code": "standard_code",
        }
    )
    handoff.to_csv(handoff_path, index=False, encoding="utf-8-sig")
    sample = _presentation_sample(handoff_source, sample_size).rename(
        columns={
            "institution_code": "anon_institution_code",
            "item_code": "standard_code",
        }
    )
    sample.to_csv(
        sample_path,
        index=False,
        encoding="utf-8-sig",
    )
    with report_path.open("w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
    LOGGER.info("Saved censored-demand metrics: %s (%s rows)", metrics_path, len(local_metrics))
    LOGGER.info("Saved demand-class handoff: %s (%s rows)", handoff_path, len(handoff))
    LOGGER.info("Saved demand-class quality report: %s", report_path)
    return {
        "metrics": metrics_path,
        "handoff": handoff_path,
        "report": report_path,
        "sample": sample_path,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute stock-availability demand classes and corrected daily mu"
    )
    parser.add_argument("--raw-dir", type=Path, default=RAW_STOCK_DIR)
    parser.add_argument("--pattern", default=RAW_STOCK_FILE_PATTERN)
    parser.add_argument("--metrics-path", type=Path, default=CENSORED_DEMAND_METRICS_PATH)
    parser.add_argument("--handoff-path", type=Path, default=DEMAND_CLASS_HANDOFF_PATH)
    parser.add_argument("--report-path", type=Path, default=DEMAND_CLASS_REPORT_PATH)
    parser.add_argument("--sample-path", type=Path, default=DEMAND_CLASS_SAMPLE_PATH)
    parser.add_argument("--chunk-size", type=int, default=CSV_CHUNK_SIZE)
    parser.add_argument("--bucket-count", type=int, default=64)
    parser.add_argument("--sample-size", type=int, default=1000)
    parser.add_argument("--temp-dir", type=Path)
    parser.add_argument("--reuse-metrics", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    paths = run_compute(
        raw_dir=args.raw_dir,
        pattern=args.pattern,
        metrics_path=args.metrics_path,
        handoff_path=args.handoff_path,
        report_path=args.report_path,
        sample_path=args.sample_path,
        chunk_size=args.chunk_size,
        bucket_count=args.bucket_count,
        sample_size=args.sample_size,
        temp_dir=args.temp_dir,
        reuse_metrics=args.reuse_metrics,
    )
    for label, path in paths.items():
        print(f"{label}: {path}")


if __name__ == "__main__":
    main()
