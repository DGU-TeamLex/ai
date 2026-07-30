import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import (
    FEATURE_TABLE_PATH,
    MODEL_DIR,
    MODEL_MANIFEST_PATH,
    MONTHLY_STOCK_PATH,
    OUTPUT_DIR,
    PREDICTION_PATH,
    SAMPLE_DATA_DIR,
    SERIES_KEYS,
)
from ..utils import ensure_dirs, setup_logging
from .baseline import BASELINE_PREDICTION_COLUMNS, add_baseline_predictions
from .inventory_policy import add_inventory_recommendations
from .prediction import (
    RISK_COLUMNS,
    _add_trained_model_predictions,
    _apply_pattern_ensemble,
    _apply_temporal_ensemble,
    _load_bundle,
    _selected_prediction_column,
)


LOGGER = logging.getLogger(__name__)
SIMULATION_VERSION = "recursive-inventory-simulation-v1.0"
DEFAULT_START_MONTH = "2026-01"
DEFAULT_END_MONTH = "2026-08"
DEFAULT_USAGE_DEVIATION = 0.10
DEFAULT_RANDOM_SEED = 42
SOURCE_STOCK_OUTLIER_REVIEW_THRESHOLD = 100_000.0

IDENTITY_COLUMNS = [
    "institution_code",
    "department",
    "item_code",
    "item_name",
    "stock_item_key",
]
STANDARD_ITEM_COLUMNS = [
    "standard_item_key",
    "standard_item_definition_key",
    "standard_item_group_id",
    "standard_item_family_id",
    "standard_item_subtype_id",
    "standard_item_specification",
    "standard_item_unit_code",
    "standardization_match_method",
    "normalization_status",
    "item_group_id_candidate",
    "item_family_id_candidate",
]
STATUS_COLUMNS = [
    "demand_pattern",
    "observed_months",
    "adi",
    "cv2",
    "observation_period_days",
    "raw_mean_daily_usage",
    "raw_daily_demand_stddev",
    "mu_is_floored",
    "sigma_is_floored",
    "mean_daily_usage",
    "daily_demand_stddev",
    "mu_forecast_3m_92d",
    "exact_group_total_stock",
    "zero_stock_reason",
    "demand_class",
    "urgent_shortage",
    "inventory_action",
    "inventory_status_parameters_available",
]
MATERIAL_COLUMNS = [
    "approved_material_mapping_count",
    "approved_related_materials",
    "approved_raw_material_meta_codes",
    "approved_raw_material_risk_meta_codes",
    "approved_demand_risk_meta_codes",
    "approved_material_mapping_versions",
    "has_approved_material_mapping",
]
FORECAST_POLICY_COLUMNS = [
    "external_demand_signal_in_forecast",
    "forecast_ensemble_policy_version",
]
QUALITY_COLUMNS = [
    "source_month_end_stock",
    "source_stock_outlier_flag",
    "stock_aggregate_eligible",
]
SEED_VALIDATION_COLUMNS = [
    "forecast_origin_month",
    "year_month",
    "predicted_usage",
    "target_stock",
    "recommended_order",
]
POLICY_OUTPUT_COLUMNS = [
    "review_period_days",
    "raw_lead_time_days",
    "lead_time_days",
    "lead_time_fallback_applied",
    "lead_time_cap_applied",
    "lead_time_policy_version",
    "protection_period_days",
    "protection_period_demand",
    "safety_stock",
    "base_stock",
    "demand_risk_score",
    "supply_risk_score",
    "material_risk_score",
    "trade_risk_score",
    "external_risk_score",
    "module_c_demand_embedded_in_forecast",
    "module_c_policy_demand_risk",
    "risk_adjusted_predicted_usage",
    "effective_lead_time_days",
    "risk_adjusted_protection_period_days",
    "risk_adjusted_protection_period_demand",
    "dynamic_safety_stock_rate",
    "risk_adjusted_safety_stock",
    "unconstrained_target_stock",
    "demand_risk_buffer",
    "supply_risk_buffer",
    "material_risk_buffer",
    "risk_buffer",
    "target_stock",
    "recommended_stock",
    "module_c_policy_applied",
    "module_c_policy_demand_uplift_applied",
    "module_c_config_version",
    "module_c_calibration_status",
    "inventory_policy_method",
    "inventory_position",
    "recommended_order",
    "raw_recommended_order",
    "order_recommendation_suppressed",
    "order_recommendation_suppression_reason",
]
STATE_COLUMNS = [
    "year_month",
    "forecast_month",
    *IDENTITY_COLUMNS,
    "data_period",
    "consumption_qty",
    "demand_qty",
    "inbound_qty",
    "month_end_stock",
    "stockout_rate",
    "disposal_qty",
    "auto_disposal_adjustment_qty",
    "negative_consumption_flag",
    "target_usage",
    "history_months",
    "series_observation_count",
    "lag_1",
    "lag_2",
    "lag_3",
    "lag_6",
    "lag_12",
    "inbound_qty_lag_1",
    "inbound_qty_lag_2",
    "inbound_qty_lag_3",
    "month_end_stock_lag_1",
    "month_end_stock_lag_2",
    "month_end_stock_lag_3",
    "stockout_rate_lag_1",
    "stockout_rate_lag_2",
    "stockout_rate_lag_3",
    "disposal_qty_lag_1",
    "disposal_qty_lag_2",
    "disposal_qty_lag_3",
    "auto_disposal_adjustment_qty_lag_1",
    "rolling_mean_3",
    "rolling_std_3",
    "rolling_mean_6",
    "rolling_std_6",
    "rolling_mean_12",
    "rolling_std_12",
    "rolling_median_3",
    "expanding_mean",
    "zero_rate_6",
    "zero_rate_12",
    "same_month_last_year",
    "yoy_growth_rate",
    "year",
    "month",
    "quarter",
    "is_winter",
    "is_summer",
    *STANDARD_ITEM_COLUMNS[:8],
    *RISK_COLUMNS,
]


def bounded_usage_factors(
    stock_item_keys: pd.Series,
    forecast_month: pd.Timestamp,
    seed: int = DEFAULT_RANDOM_SEED,
    deviation: float = DEFAULT_USAGE_DEVIATION,
) -> np.ndarray:
    if not 0 <= deviation < 1:
        raise ValueError("usage deviation must be at least 0 and less than 1")
    tokens = (
        stock_item_keys.astype("string").fillna("")
        + "|"
        + pd.Timestamp(forecast_month).strftime("%Y-%m")
        + "|"
        + str(seed)
    )
    hashed = pd.util.hash_pandas_object(tokens, index=False).to_numpy(dtype="uint64")
    uniform = (hashed >> np.uint64(11)).astype("float64") / float(1 << 53)
    if deviation == 0:
        return np.ones(len(stock_item_keys), dtype="float64")

    lower = 1.0 - deviation
    upper = 1.0 + deviation
    span_product = 2.0 * deviation * deviation
    return np.where(
        uniform < 0.5,
        lower + np.sqrt(uniform * span_product),
        upper - np.sqrt((1.0 - uniform) * span_product),
    )


def simulate_inventory_transition(
    policy: pd.DataFrame,
    usage_factors: np.ndarray,
) -> pd.DataFrame:
    if len(policy) != len(usage_factors):
        raise ValueError("policy rows and usage factors must have the same length")
    predicted_usage = pd.to_numeric(
        policy["predicted_usage"], errors="coerce"
    ).fillna(0.0).clip(lower=0.0)
    opening_stock = pd.to_numeric(
        policy["current_stock"], errors="coerce"
    ).fillna(0.0).clip(lower=0.0)
    recommended_order = pd.to_numeric(
        policy["recommended_order"], errors="coerce"
    )
    suppressed = (
        policy["order_recommendation_suppressed"]
        .astype("string")
        .fillna("")
        .str.lower()
        .isin({"true", "t", "1", "yes", "y"})
    )
    order_eligible = recommended_order.notna() & ~suppressed
    order_applied = order_eligible & recommended_order.gt(0)
    inbound = recommended_order.where(order_eligible, 0.0).clip(lower=0.0)
    virtual_usage = predicted_usage * usage_factors
    available = opening_stock + inbound
    fulfilled_usage = pd.Series(
        np.minimum(
            virtual_usage.to_numpy(dtype="float64"),
            available.to_numpy(dtype="float64"),
        ),
        index=policy.index,
    )
    unmet_usage = (virtual_usage - fulfilled_usage).clip(lower=0.0)
    closing_stock = (available - fulfilled_usage).clip(lower=0.0)
    fill_rate = pd.Series(1.0, index=policy.index, dtype="float64")
    positive_demand = virtual_usage.gt(0)
    fill_rate.loc[positive_demand] = (
        fulfilled_usage.loc[positive_demand]
        / virtual_usage.loc[positive_demand]
    ).clip(0.0, 1.0)

    return pd.DataFrame(
        {
            "opening_stock": opening_stock,
            "planned_order_qty": recommended_order,
            "simulation_order_eligible": order_eligible,
            "simulation_order_applied": order_applied,
            "simulated_inbound_qty": inbound,
            "available_stock_qty": available,
            "virtual_usage_factor": usage_factors,
            "virtual_requested_usage_qty": virtual_usage,
            "simulated_consumption_qty": fulfilled_usage,
            "unmet_demand_qty": unmet_usage,
            "predicted_month_end_stock": closing_stock,
            "stockout_flag": unmet_usage.gt(1e-9),
            "simulated_fill_rate": fill_rate,
            "ledger_balance_residual": closing_stock
            - (opening_stock + inbound - fulfilled_usage),
        },
        index=policy.index,
    )


def _load_manifest() -> list[dict]:
    with MODEL_MANIFEST_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def _read_available_csv(path: Path, requested: list[str]) -> pd.DataFrame:
    available = set(pd.read_csv(path, nrows=0).columns)
    columns = [column for column in requested if column in available]
    return pd.read_csv(path, usecols=columns, low_memory=False)


def _load_seed_and_state(
    manifest: list[dict],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp]:
    requested_seed_columns = list(
        dict.fromkeys(
            [
                *IDENTITY_COLUMNS,
                *STANDARD_ITEM_COLUMNS,
                *STATUS_COLUMNS,
                *MATERIAL_COLUMNS,
                *FORECAST_POLICY_COLUMNS,
                *SEED_VALIDATION_COLUMNS,
            ]
        )
    )
    seed = _read_available_csv(PREDICTION_PATH, requested_seed_columns)
    if seed.empty:
        raise ValueError(f"seed prediction is empty: {PREDICTION_PATH}")
    seed["stock_item_key"] = seed["stock_item_key"].astype(str)
    if seed["stock_item_key"].duplicated().any():
        raise ValueError("seed prediction must be unique by stock_item_key")
    origins = pd.to_datetime(seed["forecast_origin_month"]).dropna().unique()
    targets = pd.to_datetime(seed["year_month"]).dropna().unique()
    if len(origins) != 1 or len(targets) != 1:
        raise ValueError("seed prediction must contain exactly one origin and target month")
    origin_month = pd.Timestamp(origins[0]).to_period("M").to_timestamp()

    feature_columns = set(STATE_COLUMNS)
    for row in manifest:
        if row.get("method_type") == "machine_learning" and row.get("status") == "ready":
            feature_columns.update(_load_bundle(row["model"])["feature_cols"])
    state = pd.read_parquet(
        FEATURE_TABLE_PATH,
        columns=sorted(feature_columns),
        filters=[("year_month", "=", origin_month)],
    )
    state["stock_item_key"] = state["stock_item_key"].astype(str)
    state = state[
        state["lag_1"].notna()
        & state["lag_1"].ge(0)
        & state["rolling_mean_3"].notna()
    ].copy()
    state = seed[["stock_item_key"]].merge(
        state,
        on="stock_item_key",
        how="left",
        validate="one_to_one",
    )
    if state["year_month"].isna().any():
        missing = state.loc[state["year_month"].isna(), "stock_item_key"].head(5)
        raise ValueError(
            "seed prediction keys are missing from latest feature state: "
            f"{missing.tolist()}"
        )
    seed = seed.set_index("stock_item_key", drop=False).loc[
        state["stock_item_key"]
    ].reset_index(drop=True)
    return seed, state, origin_month


def _load_demand_history(
    state: pd.DataFrame,
    origin_month: pd.Timestamp,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, float]]:
    history_lengths = pd.to_numeric(
        state["history_months"], errors="coerce"
    ).fillna(1).clip(lower=1).astype(int)
    width = int(max(12, history_lengths.max()))
    months = pd.date_range(
        origin_month - pd.DateOffset(months=width - 1),
        origin_month,
        freq="MS",
    )
    history = pd.read_parquet(
        MONTHLY_STOCK_PATH,
        columns=["year_month", "stock_item_key", "consumption_qty"],
        filters=[
            ("year_month", ">=", months.min()),
            ("year_month", "<=", origin_month),
        ],
    )
    history["stock_item_key"] = history["stock_item_key"].astype(str)
    history = history[
        history["stock_item_key"].isin(state["stock_item_key"])
    ].copy()
    if history.duplicated(["stock_item_key", "year_month"]).any():
        raise ValueError("monthly stock history is not unique by stock item and month")
    history["demand_qty"] = pd.to_numeric(
        history["consumption_qty"], errors="coerce"
    ).where(lambda values: values.ge(0))
    wide = history.pivot(
        index="stock_item_key",
        columns="year_month",
        values="demand_qty",
    ).reindex(index=state["stock_item_key"], columns=months)
    values = wide.to_numpy(dtype="float64")
    positions = np.arange(width)
    active = positions[None, :] >= width - history_lengths.to_numpy()[:, None]
    values[~active] = np.nan

    valid_count = np.sum(~np.isnan(values), axis=1).astype("int32")
    cumulative_sum = np.nansum(values, axis=1)
    usage_history = values[:, -12:].copy()
    if usage_history.shape[1] < 12:
        usage_history = np.pad(
            usage_history,
            ((0, 0), (12 - usage_history.shape[1], 0)),
            constant_values=np.nan,
        )

    lag_mismatch_count = 0
    lag_max_abs_error = 0.0
    for lag in [1, 2, 3, 6, 12]:
        expected = usage_history[:, -lag]
        actual = pd.to_numeric(state[f"lag_{lag}"], errors="coerce").to_numpy()
        mismatch = ~np.isclose(expected, actual, equal_nan=True, atol=1e-5)
        lag_mismatch_count += int(mismatch.sum())
        both = np.isfinite(expected) & np.isfinite(actual)
        if both.any():
            lag_max_abs_error = max(
                lag_max_abs_error,
                float(np.max(np.abs(expected[both] - actual[both]))),
            )
    expanding_expected = np.divide(
        cumulative_sum,
        valid_count,
        out=np.full(len(state), np.nan, dtype="float64"),
        where=valid_count > 0,
    )
    expanding_actual = pd.to_numeric(
        state["expanding_mean"], errors="coerce"
    ).to_numpy()
    expanding_mismatch = ~np.isclose(
        expanding_expected,
        expanding_actual,
        equal_nan=True,
        atol=1e-4,
    )
    if lag_mismatch_count or expanding_mismatch.any():
        raise ValueError(
            "feature history does not match monthly stock source: "
            f"lag_mismatches={lag_mismatch_count}, "
            f"expanding_mismatches={int(expanding_mismatch.sum())}"
        )
    validation = {
        "lag_mismatch_count": lag_mismatch_count,
        "lag_max_abs_error": lag_max_abs_error,
        "expanding_mean_mismatch_count": int(expanding_mismatch.sum()),
    }
    return usage_history, cumulative_sum, valid_count, validation


def _predict_usage(
    state: pd.DataFrame,
    seed: pd.DataFrame,
    manifest: list[dict],
) -> tuple[pd.DataFrame, dict | None]:
    predicted = add_baseline_predictions(state)
    predicted = _add_trained_model_predictions(predicted, manifest)
    predicted, ensemble_policy = _apply_temporal_ensemble(predicted)
    predicted["demand_pattern"] = seed["demand_pattern"].to_numpy()
    if ensemble_policy is not None:
        predicted = _apply_pattern_ensemble(predicted, ensemble_policy)
        primary_column = "temporal_ensemble_pred"
    else:
        prediction_columns = {
            column
            for column in predicted.columns
            if column.endswith("_pred")
        }
        primary_column = _selected_prediction_column(
            manifest,
            prediction_columns,
        )
    predicted["primary_model"] = primary_column
    predicted["predicted_usage"] = pd.to_numeric(
        predicted[primary_column], errors="coerce"
    ).fillna(0.0).clip(lower=0.0)
    output_columns = [
        *BASELINE_PREDICTION_COLUMNS,
        *[
            column
            for column in predicted.columns
            if column.endswith("_pred")
            and column not in BASELINE_PREDICTION_COLUMNS
        ],
        "primary_model",
        "predicted_usage",
    ]
    return predicted[output_columns].copy(), ensemble_policy


def _build_policy_input(
    state: pd.DataFrame,
    seed: pd.DataFrame,
    predictions: pd.DataFrame,
) -> pd.DataFrame:
    static_columns = [
        column
        for column in [
            *IDENTITY_COLUMNS,
            *STANDARD_ITEM_COLUMNS,
            *STATUS_COLUMNS,
            *MATERIAL_COLUMNS,
            *QUALITY_COLUMNS,
            *FORECAST_POLICY_COLUMNS,
        ]
        if column in seed.columns
    ]
    policy = seed[static_columns].copy()
    policy["predicted_usage"] = predictions["predicted_usage"].to_numpy()
    policy["current_stock"] = pd.to_numeric(
        state["month_end_stock"], errors="coerce"
    ).fillna(0.0).clip(lower=0.0).to_numpy()
    for column in RISK_COLUMNS:
        if column in state.columns:
            policy[column] = pd.to_numeric(
                state[column], errors="coerce"
            ).fillna(0.0).to_numpy()
    return add_inventory_recommendations(
        policy,
        prediction_col="predicted_usage",
        current_stock_col="current_stock",
    )


def _rolling_stats(
    usage_history: np.ndarray,
    window: int,
) -> tuple[np.ndarray, np.ndarray]:
    values = usage_history[:, -window:]
    valid = np.sum(~np.isnan(values), axis=1)
    total = np.nansum(values, axis=1)
    mean = np.divide(
        total,
        valid,
        out=np.full(len(values), np.nan, dtype="float64"),
        where=valid > 0,
    )
    centered = values - mean[:, None]
    sum_squares = np.nansum(centered * centered, axis=1)
    std = np.sqrt(
        np.divide(
            sum_squares,
            valid - 1,
            out=np.full(len(values), np.nan, dtype="float64"),
            where=valid > 1,
        )
    )
    return mean, std


def _zero_rate(usage_history: np.ndarray, window: int) -> np.ndarray:
    values = usage_history[:, -window:]
    valid = np.sum(~np.isnan(values), axis=1)
    zero_count = np.sum(values == 0, axis=1)
    return np.divide(
        zero_count,
        valid,
        out=np.full(len(values), np.nan, dtype="float64"),
        where=valid > 0,
    )


def _shift_lags(
    state: pd.DataFrame,
    prefix: str,
    current_values: np.ndarray,
    maximum_lag: int = 3,
) -> None:
    previous = {
        lag: pd.to_numeric(
            state[f"{prefix}_lag_{lag}"], errors="coerce"
        ).to_numpy()
        for lag in range(1, maximum_lag + 1)
    }
    state[f"{prefix}_lag_1"] = current_values
    for lag in range(2, maximum_lag + 1):
        state[f"{prefix}_lag_{lag}"] = previous[lag - 1]


def advance_feature_state(
    state: pd.DataFrame,
    usage_history: np.ndarray,
    cumulative_sum: np.ndarray,
    valid_count: np.ndarray,
    transition: pd.DataFrame,
    origin_month: pd.Timestamp,
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray]:
    result = state.copy()
    fulfilled = transition["simulated_consumption_qty"].to_numpy(dtype="float64")
    inbound = transition["simulated_inbound_qty"].to_numpy(dtype="float64")
    closing = transition["predicted_month_end_stock"].to_numpy(dtype="float64")
    stockout = transition["stockout_flag"].astype("float64").to_numpy()
    zeros = np.zeros(len(result), dtype="float64")

    usage_history = np.roll(usage_history, -1, axis=1)
    usage_history[:, -1] = fulfilled
    cumulative_sum = cumulative_sum + fulfilled
    valid_count = valid_count + 1

    for lag in [1, 2, 3, 6, 12]:
        result[f"lag_{lag}"] = usage_history[:, -lag]
    for window in [3, 6, 12]:
        mean, std = _rolling_stats(usage_history, window)
        result[f"rolling_mean_{window}"] = mean
        result[f"rolling_std_{window}"] = std
    result["rolling_median_3"] = np.nanmedian(
        usage_history[:, -3:],
        axis=1,
    )
    result["expanding_mean"] = cumulative_sum / valid_count
    result["zero_rate_6"] = _zero_rate(usage_history, 6)
    result["zero_rate_12"] = _zero_rate(usage_history, 12)
    result["same_month_last_year"] = result["lag_12"]
    lag_1 = pd.to_numeric(result["lag_1"], errors="coerce")
    lag_12 = pd.to_numeric(result["lag_12"], errors="coerce")
    result["yoy_growth_rate"] = (
        (lag_1 - lag_12) / lag_12.where(lag_12.ne(0))
    ).fillna(0.0)

    _shift_lags(result, "inbound_qty", inbound)
    _shift_lags(result, "month_end_stock", closing)
    _shift_lags(result, "stockout_rate", stockout)
    _shift_lags(result, "disposal_qty", zeros)
    result["auto_disposal_adjustment_qty_lag_1"] = zeros

    current_month = pd.Timestamp(origin_month).to_period("M").to_timestamp()
    next_forecast_month = current_month + pd.offsets.MonthBegin(1)
    result["year_month"] = current_month
    result["forecast_month"] = next_forecast_month
    result["year"] = next_forecast_month.year
    result["month"] = next_forecast_month.month
    result["quarter"] = next_forecast_month.quarter
    result["is_winter"] = int(next_forecast_month.month in {12, 1, 2})
    result["is_summer"] = int(next_forecast_month.month in {6, 7, 8})
    result["history_months"] = (
        pd.to_numeric(result["history_months"], errors="coerce")
        .fillna(0)
        .astype("int64")
        + 1
    )
    result["series_observation_count"] = (
        pd.to_numeric(result["series_observation_count"], errors="coerce")
        .fillna(0)
        .astype("int64")
        + 1
    )
    result["consumption_qty"] = fulfilled
    result["demand_qty"] = fulfilled
    result["inbound_qty"] = inbound
    result["month_end_stock"] = closing
    result["stockout_rate"] = stockout
    result["disposal_qty"] = zeros
    result["auto_disposal_adjustment_qty"] = zeros
    result["negative_consumption_flag"] = 0
    result["target_usage"] = np.nan
    return result, usage_history, cumulative_sum, valid_count


def _maximum_absolute_difference(
    actual: pd.Series,
    expected: pd.Series,
) -> tuple[float, int]:
    left = pd.to_numeric(actual, errors="coerce").to_numpy(dtype="float64")
    right = pd.to_numeric(expected, errors="coerce").to_numpy(dtype="float64")
    nan_mismatch = np.isnan(left) ^ np.isnan(right)
    both = np.isfinite(left) & np.isfinite(right)
    maximum = float(np.max(np.abs(left[both] - right[both]))) if both.any() else 0.0
    mismatch = nan_mismatch | (
        both & ~np.isclose(left, right, atol=1e-5, rtol=1e-7)
    )
    return maximum, int(mismatch.sum())


def _build_detail(
    seed: pd.DataFrame,
    predictions: pd.DataFrame,
    policy: pd.DataFrame,
    transition: pd.DataFrame,
    forecast_origin_month: pd.Timestamp,
    forecast_month: pd.Timestamp,
    source_data_month: pd.Timestamp,
    usage_deviation: float,
    random_seed: int,
) -> pd.DataFrame:
    static_columns = [
        column
        for column in [
            *IDENTITY_COLUMNS,
            *STANDARD_ITEM_COLUMNS,
            *STATUS_COLUMNS,
            *MATERIAL_COLUMNS,
            *QUALITY_COLUMNS,
        ]
        if column in seed.columns
    ]
    detail = seed[static_columns].copy()
    detail.insert(0, "simulation_version", SIMULATION_VERSION)
    detail.insert(1, "forecast_origin_month", forecast_origin_month)
    detail.insert(2, "year_month", forecast_month)
    detail["source_inventory_data_month"] = source_data_month
    detail["synthetic_usage"] = True
    detail["usage_deviation_limit"] = usage_deviation
    detail["random_seed"] = random_seed
    detail["future_risk_assumption"] = "carry_forward_latest_observed"
    detail["future_status_assumption"] = "carry_forward_latest_observed"
    detail["order_arrival_assumption"] = "recommended_order_arrives_within_month"
    for column in predictions.columns:
        detail[column] = predictions[column].to_numpy()
    for column in RISK_COLUMNS:
        if column in policy.columns:
            detail[column] = policy[column].to_numpy()
    for column in POLICY_OUTPUT_COLUMNS:
        if column in policy.columns:
            detail[column] = policy[column].to_numpy()
    for column in transition.columns:
        detail[column] = transition[column].to_numpy()
    detail["virtual_usage_lower_bound"] = (
        detail["predicted_usage"] * (1.0 - usage_deviation)
    )
    detail["virtual_usage_upper_bound"] = (
        detail["predicted_usage"] * (1.0 + usage_deviation)
    )
    return detail


def _monthly_summary(detail: pd.DataFrame) -> dict[str, object]:
    return {
        "year_month": str(pd.Timestamp(detail["year_month"].iloc[0]).date()),
        "rows": int(len(detail)),
        "predicted_usage_sum": float(detail["predicted_usage"].sum()),
        "virtual_requested_usage_sum": float(
            detail["virtual_requested_usage_qty"].sum()
        ),
        "simulated_consumption_sum": float(
            detail["simulated_consumption_qty"].sum()
        ),
        "unmet_demand_sum": float(detail["unmet_demand_qty"].sum()),
        "planned_order_sum": float(detail["planned_order_qty"].fillna(0.0).sum()),
        "simulated_inbound_sum": float(detail["simulated_inbound_qty"].sum()),
        "opening_stock_sum": float(detail["opening_stock"].sum()),
        "predicted_month_end_stock_sum": float(
            detail["predicted_month_end_stock"].sum()
        ),
        "aggregate_eligible_month_end_stock_sum": float(
            detail.loc[
                detail["stock_aggregate_eligible"],
                "predicted_month_end_stock",
            ].sum()
        ),
        "source_stock_outlier_item_count": int(
            detail["source_stock_outlier_flag"].sum()
        ),
        "source_stock_outlier_month_end_stock_sum": float(
            detail.loc[
                detail["source_stock_outlier_flag"],
                "predicted_month_end_stock",
            ].sum()
        ),
        "stockout_item_count": int(detail["stockout_flag"].sum()),
        "stockout_item_rate": float(detail["stockout_flag"].mean()),
        "order_applied_item_count": int(
            detail["simulation_order_applied"].sum()
        ),
        "order_eligible_item_count": int(
            detail["simulation_order_eligible"].sum()
        ),
        "order_suppressed_item_count": int(
            detail["order_recommendation_suppressed"].sum()
        ),
        "module_c_policy_item_count": int(detail["module_c_policy_applied"].sum()),
        "virtual_usage_factor_min": float(detail["virtual_usage_factor"].min()),
        "virtual_usage_factor_mean": float(detail["virtual_usage_factor"].mean()),
        "virtual_usage_factor_max": float(detail["virtual_usage_factor"].max()),
        "simulated_fill_rate": float(
            detail["simulated_consumption_qty"].sum()
            / max(detail["virtual_requested_usage_qty"].sum(), 1e-12)
        ),
        "ledger_balance_max_abs_error": float(
            detail["ledger_balance_residual"].abs().max()
        ),
    }


def _build_group_summary(detail: pd.DataFrame) -> pd.DataFrame:
    source = detail.copy()
    source["aggregate_eligible_month_end_stock"] = source[
        "predicted_month_end_stock"
    ].where(source["stock_aggregate_eligible"], 0.0)
    source["source_stock_outlier_month_end_stock"] = source[
        "predicted_month_end_stock"
    ].where(source["source_stock_outlier_flag"], 0.0)
    summary = (
        source.groupby(
            "standard_item_group_id",
            dropna=False,
            observed=True,
        )
        .agg(
            item_count=("stock_item_key", "size"),
            predicted_usage_sum=("predicted_usage", "sum"),
            virtual_requested_usage_sum=("virtual_requested_usage_qty", "sum"),
            simulated_consumption_sum=("simulated_consumption_qty", "sum"),
            unmet_demand_sum=("unmet_demand_qty", "sum"),
            predicted_month_end_stock_sum=("predicted_month_end_stock", "sum"),
            aggregate_eligible_month_end_stock_sum=(
                "aggregate_eligible_month_end_stock",
                "sum",
            ),
            source_stock_outlier_item_count=(
                "source_stock_outlier_flag",
                "sum",
            ),
            source_stock_outlier_month_end_stock_sum=(
                "source_stock_outlier_month_end_stock",
                "sum",
            ),
            stockout_item_count=("stockout_flag", "sum"),
        )
        .reset_index()
    )
    summary["stockout_item_rate"] = (
        summary["stockout_item_count"] / summary["item_count"]
    )
    return summary.sort_values(
        "predicted_month_end_stock_sum",
        ascending=False,
        kind="mergesort",
    )


def run_recursive_inventory_simulation(
    start_month: str = DEFAULT_START_MONTH,
    end_month: str = DEFAULT_END_MONTH,
    usage_deviation: float = DEFAULT_USAGE_DEVIATION,
    random_seed: int = DEFAULT_RANDOM_SEED,
    sample_size: int = 1000,
) -> dict[str, object]:
    start = pd.Timestamp(start_month).to_period("M").to_timestamp()
    end = pd.Timestamp(end_month).to_period("M").to_timestamp()
    if end < start:
        raise ValueError("end month must not be earlier than start month")
    if sample_size < 1:
        raise ValueError("sample size must be positive")
    if not 0 <= usage_deviation < 1:
        raise ValueError("usage deviation must be at least 0 and less than 1")

    ensure_dirs(OUTPUT_DIR, SAMPLE_DATA_DIR, MODEL_DIR)
    manifest = _load_manifest()
    seed, state, source_data_month = _load_seed_and_state(manifest)
    seed["source_month_end_stock"] = pd.to_numeric(
        state["month_end_stock"], errors="coerce"
    ).fillna(0.0).clip(lower=0.0).to_numpy()
    seed["source_stock_outlier_flag"] = seed["source_month_end_stock"].ge(
        SOURCE_STOCK_OUTLIER_REVIEW_THRESHOLD
    )
    seed["stock_aggregate_eligible"] = ~seed["source_stock_outlier_flag"]
    seed_target = pd.Timestamp(seed["year_month"].iloc[0]).to_period("M").to_timestamp()
    if start != seed_target:
        raise ValueError(
            "start month must match the current system forecast target: "
            f"requested={start.date()}, available={seed_target.date()}"
        )
    usage_history, cumulative_sum, valid_count, history_validation = (
        _load_demand_history(state, source_data_month)
    )

    period_slug = f"{start:%Y_%m}_{end:%Y_%m}"
    detail_dir = OUTPUT_DIR / f"inventory_simulation_{period_slug}"
    ensure_dirs(detail_dir)
    summary_path = OUTPUT_DIR / f"inventory_simulation_{period_slug}_summary.csv"
    final_csv_path = OUTPUT_DIR / f"inventory_forecast_{end:%Y_%m}.csv"
    group_summary_path = (
        OUTPUT_DIR / f"inventory_forecast_{end:%Y_%m}_by_item_group.csv"
    )
    report_path = OUTPUT_DIR / f"inventory_simulation_{period_slug}_report.json"
    sample_path = SAMPLE_DATA_DIR / f"inventory_simulation_{period_slug}_sample_1000.csv"

    summaries = []
    sample_frames = []
    detail_paths = []
    seed_validation = {}
    months = pd.date_range(start, end, freq="MS")
    per_month_sample = max(1, int(np.ceil(sample_size / len(months))))

    for month_index, forecast_month in enumerate(months):
        forecast_origin_month = forecast_month - pd.offsets.MonthBegin(1)
        predictions, _ = _predict_usage(state, seed, manifest)
        policy = _build_policy_input(state, seed, predictions)

        if month_index == 0:
            predicted_max, predicted_mismatch = _maximum_absolute_difference(
                predictions["predicted_usage"],
                seed["predicted_usage"],
            )
            target_max, target_mismatch = _maximum_absolute_difference(
                policy["target_stock"],
                seed["target_stock"],
            )
            order_max, order_mismatch = _maximum_absolute_difference(
                policy["recommended_order"],
                seed["recommended_order"],
            )
            seed_validation = {
                "predicted_usage_max_abs_error": predicted_max,
                "predicted_usage_mismatch_count": predicted_mismatch,
                "target_stock_max_abs_error": target_max,
                "target_stock_mismatch_count": target_mismatch,
                "recommended_order_max_abs_error": order_max,
                "recommended_order_mismatch_count": order_mismatch,
            }
            if predicted_mismatch or target_mismatch or order_mismatch:
                raise ValueError(
                    "recursive simulation does not reproduce the current January output: "
                    f"{seed_validation}"
                )

        usage_factors = bounded_usage_factors(
            seed["stock_item_key"],
            forecast_month,
            seed=random_seed,
            deviation=usage_deviation,
        )
        transition = simulate_inventory_transition(policy, usage_factors)
        detail = _build_detail(
            seed,
            predictions,
            policy,
            transition,
            forecast_origin_month,
            forecast_month,
            source_data_month,
            usage_deviation,
            random_seed,
        )
        factor_out_of_bounds = (
            detail["virtual_usage_factor"].lt(1.0 - usage_deviation - 1e-12)
            | detail["virtual_usage_factor"].gt(1.0 + usage_deviation + 1e-12)
        )
        if factor_out_of_bounds.any():
            raise ValueError("generated virtual usage factor exceeded its configured bound")
        if detail["ledger_balance_residual"].abs().max() > 1e-8:
            raise ValueError("simulated inventory ledger balance check failed")
        if (
            detail[
                [
                    "simulated_inbound_qty",
                    "simulated_consumption_qty",
                    "unmet_demand_qty",
                    "predicted_month_end_stock",
                ]
            ]
            .lt(-1e-9)
            .any()
            .any()
        ):
            raise ValueError("simulated quantity contains a negative value")

        detail_path = detail_dir / f"{forecast_month:%Y-%m}.parquet"
        detail.to_parquet(detail_path, index=False, compression="zstd")
        detail_paths.append(str(detail_path.relative_to(Path.cwd())))
        summaries.append(_monthly_summary(detail))
        sample_frames.append(
            detail.sample(
                n=min(per_month_sample, len(detail)),
                random_state=random_seed + month_index,
            )
        )
        if forecast_month == end:
            detail.to_csv(final_csv_path, index=False, encoding="utf-8-sig")
            _build_group_summary(detail).to_csv(
                group_summary_path,
                index=False,
                encoding="utf-8-sig",
            )

        state, usage_history, cumulative_sum, valid_count = advance_feature_state(
            state,
            usage_history,
            cumulative_sum,
            valid_count,
            transition,
            forecast_month,
        )
        LOGGER.info(
            "Simulated %s: rows=%s, closing_stock=%.3f, stockouts=%s",
            forecast_month.strftime("%Y-%m"),
            len(detail),
            detail["predicted_month_end_stock"].sum(),
            int(detail["stockout_flag"].sum()),
        )

    summary = pd.DataFrame(summaries)
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    sample = pd.concat(sample_frames, ignore_index=True)
    if len(sample) > sample_size:
        sample = sample.sample(n=sample_size, random_state=random_seed)
    sample = sample.sort_values(
        ["year_month", "stock_item_key"],
        kind="mergesort",
    )
    sample.to_csv(sample_path, index=False, encoding="utf-8-sig")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "simulation_version": SIMULATION_VERSION,
        "status": "synthetic_scenario_not_observed_inventory",
        "source_inventory_data_month": str(source_data_month.date()),
        "forecast_start_month": str(start.date()),
        "forecast_end_month": str(end.date()),
        "forecast_month_count": int(len(months)),
        "stock_item_count": int(len(seed)),
        "detail_row_count": int(len(seed) * len(months)),
        "usage_generation": {
            "distribution": "deterministic_keyed_symmetric_triangular",
            "deviation_limit": usage_deviation,
            "factor_lower_bound": 1.0 - usage_deviation,
            "factor_mode": 1.0,
            "factor_upper_bound": 1.0 + usage_deviation,
            "random_seed": random_seed,
            "factor_applies_to": "predicted_usage",
        },
        "inventory_flow": {
            "formula": (
                "predicted_month_end_stock = opening_stock + "
                "simulated_inbound_qty - simulated_consumption_qty"
            ),
            "virtual_demand_formula": (
                "virtual_requested_usage_qty = predicted_usage * "
                "virtual_usage_factor"
            ),
            "fulfilled_usage_formula": (
                "simulated_consumption_qty = min("
                "virtual_requested_usage_qty, available_stock_qty)"
            ),
            "order_arrival_assumption": (
                "non-suppressed recommended_order arrives within the same month"
            ),
        },
        "future_signal_assumptions": {
            "module_c_risk": "carry_forward_from_2025-12_origin",
            "inventory_status": "carry_forward_from_2025-12_origin",
            "disposal_qty": 0.0,
            "auto_disposal_adjustment_qty": 0.0,
            "backorder_qty": 0.0,
        },
        "aggregate_quality_gate": {
            "source_stock_outlier_review_threshold": (
                SOURCE_STOCK_OUTLIER_REVIEW_THRESHOLD
            ),
            "threshold_basis": (
                "docs/2026-07-23_05_CURRENT_FORECAST_MODEL_EVALUATION.md"
            ),
            "handling": (
                "preserve item rows and quantities; exclude flagged source "
                "stock only from aggregate_eligible summaries"
            ),
            "flagged_stock_item_count": int(
                seed["source_stock_outlier_flag"].sum()
            ),
        },
        "history_validation": history_validation,
        "seed_output_validation": seed_validation,
        "monthly_summary": summaries,
        "outputs": {
            "monthly_detail_parquet": detail_paths,
            "monthly_summary_csv": str(summary_path.relative_to(Path.cwd())),
            "final_month_csv": str(final_csv_path.relative_to(Path.cwd())),
            "final_month_group_summary_csv": str(
                group_summary_path.relative_to(Path.cwd())
            ),
            "sample_csv": str(sample_path.relative_to(Path.cwd())),
        },
        "limitations": [
            "The generated usage and inventory are synthetic scenario values, not observations.",
            "No 2026 realized inbound, disposal, backorder, lead-time, or stock count is available.",
            "Module C risk and inventory-status inputs are carried forward from the latest observed origin.",
            "The same-month order-arrival assumption must be replaced when purchase-order lead-time data becomes available.",
        ],
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    LOGGER.info("Saved recursive simulation report: %s", report_path)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate recursive synthetic usage and inventory through a future month."
    )
    parser.add_argument("--start-month", default=DEFAULT_START_MONTH)
    parser.add_argument("--end-month", default=DEFAULT_END_MONTH)
    parser.add_argument(
        "--usage-deviation",
        type=float,
        default=DEFAULT_USAGE_DEVIATION,
    )
    parser.add_argument("--random-seed", type=int, default=DEFAULT_RANDOM_SEED)
    parser.add_argument("--sample-size", type=int, default=1000)
    return parser.parse_args()


def main() -> None:
    setup_logging()
    args = parse_args()
    run_recursive_inventory_simulation(
        start_month=args.start_month,
        end_month=args.end_month,
        usage_deviation=args.usage_deviation,
        random_seed=args.random_seed,
        sample_size=args.sample_size,
    )


if __name__ == "__main__":
    main()
