from __future__ import annotations

import argparse
import json
import logging
from math import inf
from pathlib import Path
from statistics import NormalDist
from typing import Iterable

import numpy as np
import pandas as pd

from ..config import (
    BACKTEST_PREDICTION_PATH,
    MODEL_COMBINATION_INVENTORY_EVALUATION_PATH,
    MODEL_COMBINATION_POINT_EVALUATION_PATH,
    MODEL_COMBINATION_POLICY_PATH,
    MODEL_COMBINATION_SAMPLE_PATH,
    MODEL_COMBINATION_SEGMENT_EVALUATION_PATH,
    MONTHLY_STOCK_PATH,
    OUTPUT_DIR,
    PROJECT_ROOT,
    SAMPLE_DATA_DIR,
)
from ..utils import ensure_dirs, setup_logging
from .artifact_paths import portable_artifact_path
from .metrics import regression_metrics


LOGGER = logging.getLogger(__name__)
EXPERIMENT_VERSION = "stock-combination-v1.0"
DEFAULT_SERVICE_LEVEL = 0.90
SERVICE_LEVEL_SENSITIVITY = [0.90, 0.95]
DEFAULT_CALIBRATION_MONTH_COUNT = 2
MIN_POOL_SERIES = 100
MIN_BUFFER_GROUP_ROWS = 200
LOG_SIZE_PRIOR_DF = 20.0
SCALE_BINS = [-inf, 0.0, 1.0, 5.0, 20.0, 100.0, 500.0, inf]
SCALE_LABELS = [
    "ZERO",
    "LE_1",
    "LE_5",
    "LE_20",
    "LE_100",
    "LE_500",
    "GT_500",
]
REQUIRED_FORECAST_COLUMNS = [
    "baseline_last_month_pred",
    "baseline_rolling_mean_3_pred",
    "baseline_rolling_median_3_pred",
    "baseline_rolling_mean_6_pred",
    "baseline_same_month_last_year_pred",
    "baseline_expanding_mean_pred",
    "stock_model_a_usage_only_pred",
    "stock_model_a_usage_tweedie_pred",
]
OPTIONAL_FORECAST_COLUMNS = [
    # 외부신호 커버리지 게이트를 통과했을 때만 학습되는 모형이다.
    "stock_model_d_module_c_pred",
]
BASE_FORECAST_COLUMNS = [
    *REQUIRED_FORECAST_COLUMNS,
    *OPTIONAL_FORECAST_COLUMNS,
]
BUFFER_METHODS = [
    "none",
    "fixed_20pct",
    "normal_pooled",
    "empirical_pooled",
]


def _require_columns(frame: pd.DataFrame, columns: Iterable[str], label: str) -> None:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing required columns: {missing}")


def available_forecast_columns(header: Iterable[str]) -> list[str]:
    """현재 실행에서 실제 생성된 비교 예측열만 반환한다.

    Module C 같은 외부위험 모형은 입력 신호가 비면 학습 단계에서 의도적으로
    제외된다. 그 상태에서 과거 실행의 후보 목록을 필수 스키마로 요구하면
    수요전용 조합실험까지 막히므로, 핵심 수요모형은 필수로 유지하고 외부위험
    모형만 가용할 때 추가한다.
    """
    available = set(header)
    missing_required = sorted(set(REQUIRED_FORECAST_COLUMNS) - available)
    if missing_required:
        raise ValueError(
            "Backtest predictions are missing required forecast columns: "
            f"{missing_required}"
        )
    skipped_optional = sorted(set(OPTIONAL_FORECAST_COLUMNS) - available)
    if skipped_optional:
        LOGGER.info(
            "Combination experiment omits unavailable optional forecasts: %s",
            ", ".join(skipped_optional),
        )
    return [column for column in BASE_FORECAST_COLUMNS if column in available]


def selected_backtest_columns(
    header: Iterable[str],
    required: Iterable[str],
    optional_metadata: Iterable[str],
    candidate_forecasts: Iterable[str],
) -> list[str]:
    """읽을 메타데이터와 실제 가용 예측후보를 함께 반환한다.

    후보 탐색은 CSV 헤더에서 수행되므로, 선택된 선택적 예측열을 ``usecols``에
    다시 포함해야 한다. 그렇지 않으면 헤더에서는 가용하다고 판단한 외부위험
    모형이 실제 데이터 프레임에서 사라진다.
    """
    available = set(header)
    return sorted(
        set(required)
        | set(candidate_forecasts)
        | (set(optional_metadata) & available)
    )


def _json_value(value):
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _split_months(
    frame: pd.DataFrame,
    calibration_month_count: int = DEFAULT_CALIBRATION_MONTH_COUNT,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str], list[str]]:
    months = sorted(pd.to_datetime(frame["year_month"]).dt.to_period("M").unique())
    if calibration_month_count <= 0 or len(months) <= calibration_month_count:
        raise ValueError(
            "At least one evaluation month must remain after calibration: "
            f"months={len(months)}, calibration={calibration_month_count}"
        )
    calibration_months = months[:calibration_month_count]
    evaluation_months = months[calibration_month_count:]
    period = pd.to_datetime(frame["year_month"]).dt.to_period("M")
    calibration = frame.loc[period.isin(calibration_months)].copy()
    evaluation = frame.loc[period.isin(evaluation_months)].copy()
    return (
        calibration,
        evaluation,
        [str(month) for month in calibration_months],
        [str(month) for month in evaluation_months],
    )


def _pool_key(frame: pd.DataFrame, pooling_mode: str) -> pd.Series:
    pattern = frame["demand_pattern"].fillna("new_series").astype(str)
    if pooling_mode == "pattern":
        return pattern
    if pooling_mode == "pattern_item_group":
        item_group = (
            frame["item_group_id_candidate"]
            .fillna("UNCLASSIFIED")
            .astype(str)
        )
        return pattern + "::" + item_group
    raise ValueError(f"Unsupported pooling mode: {pooling_mode}")


def _series_monthly_statistics(history: pd.DataFrame) -> pd.DataFrame:
    demand = pd.to_numeric(history["consumption_qty"], errors="coerce")
    history = history.loc[demand.notna() & demand.ge(0)].copy()
    demand = pd.to_numeric(history["consumption_qty"], errors="coerce")
    history["_positive"] = demand.gt(0).astype("int16")
    history["_log_size"] = 0.0
    positive = demand.gt(0)
    history.loc[positive, "_log_size"] = np.log(demand[positive])
    history["_log_size_sq"] = history["_log_size"] ** 2
    stats = history.groupby("stock_item_key", observed=True).agg(
        observed_months=("consumption_qty", "size"),
        positive_months=("_positive", "sum"),
        log_size_sum=("_log_size", "sum"),
        log_size_sq_sum=("_log_size_sq", "sum"),
    )
    stats["log_size_mean"] = stats["log_size_sum"].div(
        stats["positive_months"].replace(0, np.nan)
    )
    numerator = (
        stats["log_size_sq_sum"]
        - stats["positive_months"] * stats["log_size_mean"].pow(2)
    ).clip(lower=0.0)
    stats["log_size_variance"] = numerator.div(
        (stats["positive_months"] - 1).replace(0, np.nan)
    )
    return stats.reset_index()


def _estimate_pool_parameters(
    stats: pd.DataFrame,
    pool_col: str,
) -> pd.DataFrame:
    records: list[dict[str, float | str | int]] = []
    for pool, group in stats.groupby(pool_col, dropna=False, observed=True):
        n = group["observed_months"].to_numpy(dtype="float64")
        m = group["positive_months"].to_numpy(dtype="float64")
        total_n = float(n.sum())
        total_m = float(m.sum())
        raw_probability = total_m / total_n if total_n > 0 else 0.0
        probability = float(np.clip(raw_probability, 1e-4, 1 - 1e-4))
        rates = np.divide(m, n, out=np.zeros_like(m), where=n > 0)
        rate_variance = float(
            np.average((rates - probability) ** 2, weights=np.maximum(n, 1.0))
        )
        sampling_variance = float(
            np.average(
                probability * (1 - probability) / np.maximum(n, 1.0),
                weights=np.maximum(n, 1.0),
            )
        )
        latent_variance = max(
            rate_variance - sampling_variance,
            probability * (1 - probability) / 1001.0,
        )
        concentration = float(
            np.clip(
                probability * (1 - probability) / latent_variance - 1.0,
                2.0,
                1000.0,
            )
        )

        positive = group[group["positive_months"].gt(0)]
        positive_count = float(positive["positive_months"].sum())
        if positive_count > 0:
            log_mean = float(
                positive["log_size_sum"].sum() / positive_count
            )
            total_sse = float(
                (
                    positive["log_size_sq_sum"]
                    - positive["positive_months"]
                    * positive["log_size_mean"].pow(2)
                )
                .clip(lower=0.0)
                .sum()
            )
            within_df = float((positive["positive_months"] - 1).clip(lower=0).sum())
            total_variance = max(
                float(
                    (
                        positive["log_size_sq_sum"].sum()
                        - positive_count * log_mean**2
                    )
                    / max(positive_count - 1.0, 1.0)
                ),
                1e-4,
            )
            process_variance = (
                max(total_sse / within_df, 1e-4)
                if within_df > 0
                else total_variance
            )
            mean_variance = float(
                np.average(
                    (positive["log_size_mean"] - log_mean) ** 2,
                    weights=positive["positive_months"],
                )
            )
            noise_variance = float(
                np.average(
                    process_variance
                    / positive["positive_months"].clip(lower=1),
                    weights=positive["positive_months"],
                )
            )
            between_variance = max(
                mean_variance - noise_variance,
                process_variance / 100.0,
                1e-4,
            )
        else:
            log_mean = 0.0
            process_variance = 1.0
            between_variance = 1.0

        records.append(
            {
                pool_col: pool,
                "pool_series_count": int(len(group)),
                "pool_positive_count": int(total_m),
                "occurrence_mean": probability,
                "occurrence_concentration": concentration,
                "log_size_mean_prior": log_mean,
                "log_size_process_variance": process_variance,
                "log_size_between_variance": between_variance,
            }
        )
    return pd.DataFrame(records)


def _attach_pool_parameters(
    stats: pd.DataFrame,
    pooling_mode: str,
) -> pd.DataFrame:
    result = stats.copy()
    result["_pattern_pool"] = _pool_key(result, "pattern")
    pattern_parameters = _estimate_pool_parameters(result, "_pattern_pool")

    global_stats = result.copy()
    global_stats["_global_pool"] = "GLOBAL"
    global_parameters = _estimate_pool_parameters(
        global_stats,
        "_global_pool",
    ).iloc[0]

    result["_requested_pool"] = _pool_key(result, pooling_mode)
    requested_parameters = _estimate_pool_parameters(result, "_requested_pool")
    result = result.merge(
        requested_parameters,
        on="_requested_pool",
        how="left",
        validate="many_to_one",
    )
    pattern_parameter_names = [
        column
        for column in pattern_parameters.columns
        if column != "_pattern_pool"
    ]
    result = result.merge(
        pattern_parameters.rename(
            columns={column: f"{column}_pattern" for column in pattern_parameter_names}
        ),
        on="_pattern_pool",
        how="left",
        validate="many_to_one",
    )

    use_pattern_fallback = (
        result["pool_series_count"].lt(MIN_POOL_SERIES)
        | result["pool_positive_count"].lt(20)
    )
    parameter_names = [
        "occurrence_mean",
        "occurrence_concentration",
        "log_size_mean_prior",
        "log_size_process_variance",
        "log_size_between_variance",
    ]
    for column in parameter_names:
        result[column] = result[column].where(
            ~use_pattern_fallback,
            result[f"{column}_pattern"],
        )
        result[column] = result[column].fillna(float(global_parameters[column]))
    result["pool_fallback_used"] = use_pattern_fallback
    return result


def build_tsb_hb_predictions(
    monthly: pd.DataFrame,
    requests: pd.DataFrame,
    pooling_mode: str = "pattern",
) -> pd.Series:
    _require_columns(
        monthly,
        ["year_month", "stock_item_key", "consumption_qty"],
        "monthly stock",
    )
    _require_columns(
        requests,
        [
            "forecast_origin_month",
            "stock_item_key",
            "demand_pattern",
            "item_group_id_candidate",
        ],
        "TSB-HB requests",
    )
    request_frame = requests.reset_index().rename(columns={"index": "_request_index"})
    result = pd.Series(np.nan, index=requests.index, dtype="float64")
    metadata = (
        request_frame[
            ["stock_item_key", "demand_pattern", "item_group_id_candidate"]
        ]
        .drop_duplicates("stock_item_key")
        .copy()
    )
    history_source = monthly[
        monthly["stock_item_key"].isin(metadata["stock_item_key"])
    ][["year_month", "stock_item_key", "consumption_qty"]].copy()
    history_source["year_month"] = pd.to_datetime(history_source["year_month"])

    for origin, origin_requests in request_frame.groupby(
        "forecast_origin_month",
        sort=True,
    ):
        origin_timestamp = pd.Timestamp(origin)
        history = history_source[history_source["year_month"].le(origin_timestamp)]
        stats = _series_monthly_statistics(history)
        stats = metadata.merge(
            stats,
            on="stock_item_key",
            how="left",
            validate="one_to_one",
        )
        numeric_defaults = {
            "observed_months": 0,
            "positive_months": 0,
            "log_size_sum": 0.0,
            "log_size_sq_sum": 0.0,
        }
        for column, default in numeric_defaults.items():
            stats[column] = stats[column].fillna(default)
        stats = _attach_pool_parameters(stats, pooling_mode)

        n = stats["observed_months"].to_numpy(dtype="float64")
        m = stats["positive_months"].to_numpy(dtype="float64")
        concentration = stats["occurrence_concentration"].to_numpy(dtype="float64")
        occurrence = (
            stats["occurrence_mean"].to_numpy(dtype="float64") * concentration
            + m
        ) / (concentration + n)

        group_process_variance = stats[
            "log_size_process_variance"
        ].to_numpy(dtype="float64")
        individual_variance = stats["log_size_variance"].to_numpy(dtype="float64")
        individual_df = np.maximum(m - 1.0, 0.0)
        process_variance = (
            LOG_SIZE_PRIOR_DF * group_process_variance
            + individual_df * np.nan_to_num(individual_variance, nan=0.0)
        ) / (LOG_SIZE_PRIOR_DF + individual_df)
        between_variance = stats[
            "log_size_between_variance"
        ].to_numpy(dtype="float64")
        credibility_k = process_variance / np.maximum(between_variance, 1e-4)
        size_weight = m / (m + credibility_k)
        local_log_mean = stats["log_size_mean"].fillna(
            stats["log_size_mean_prior"]
        ).to_numpy(dtype="float64")
        prior_log_mean = stats["log_size_mean_prior"].to_numpy(dtype="float64")
        posterior_log_mean = (
            size_weight * local_log_mean
            + (1.0 - size_weight) * prior_log_mean
        )
        positive_size = np.exp(
            np.clip(
                posterior_log_mean + 0.5 * process_variance,
                -20.0,
                20.0,
            )
        )
        stats["_prediction"] = np.clip(occurrence * positive_size, 0.0, None)
        prediction_lookup = stats.set_index("stock_item_key")["_prediction"]
        predictions = origin_requests["stock_item_key"].map(prediction_lookup)
        result.loc[origin_requests["_request_index"].to_numpy()] = (
            predictions.fillna(0.0).to_numpy()
        )
    return result


def add_blend_candidates(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    result = frame.copy()
    blends = {
        "blend_l1_75_tweedie_25_pred": (
            0.75 * result["stock_model_a_usage_only_pred"]
            + 0.25 * result["stock_model_a_usage_tweedie_pred"]
        ),
        "blend_l1_50_tweedie_50_pred": (
            0.50 * result["stock_model_a_usage_only_pred"]
            + 0.50 * result["stock_model_a_usage_tweedie_pred"]
        ),
        "blend_l1_25_tweedie_75_pred": (
            0.25 * result["stock_model_a_usage_only_pred"]
            + 0.75 * result["stock_model_a_usage_tweedie_pred"]
        ),
    }
    for column, values in blends.items():
        result[column] = values.clip(lower=0.0)
    return result, list(blends)


def select_pattern_router(
    calibration: pd.DataFrame,
    candidate_columns: list[str],
) -> tuple[dict[str, str], str]:
    global_scores = []
    for column in candidate_columns:
        metrics = regression_metrics(calibration["actual_usage"], calibration[column])
        global_scores.append((float(metrics["WAPE"]), column))
    global_fallback = min(global_scores)[1]

    router: dict[str, str] = {}
    for pattern, group in calibration.groupby(
        "demand_pattern",
        dropna=False,
        observed=True,
    ):
        scores = []
        for column in candidate_columns:
            metrics = regression_metrics(group["actual_usage"], group[column])
            score = (
                float(metrics["WAPE"])
                if metrics["WAPE"] is not None
                else float(metrics["MAE"])
            )
            scores.append((score, column))
        router[str(pattern)] = min(scores)[1]
    return router, global_fallback


def apply_pattern_router(
    frame: pd.DataFrame,
    router: dict[str, str],
    global_fallback: str,
) -> pd.Series:
    selected_columns = (
        frame["demand_pattern"].astype(str).map(router).fillna(global_fallback)
    )
    values = np.zeros(len(frame), dtype="float64")
    for column in selected_columns.unique():
        mask = selected_columns.eq(column).to_numpy()
        values[mask] = pd.to_numeric(
            frame.loc[mask, column],
            errors="coerce",
        ).fillna(0.0)
    return pd.Series(values, index=frame.index)


def _scale_band(prediction: pd.Series) -> pd.Series:
    return pd.cut(
        pd.to_numeric(prediction, errors="coerce").fillna(0.0),
        bins=SCALE_BINS,
        labels=SCALE_LABELS,
        include_lowest=True,
        right=True,
    ).astype("string")


def _buffer_statistic(
    error: pd.Series,
    method: str,
    service_level: float,
) -> float:
    numeric = pd.to_numeric(error, errors="coerce").dropna()
    if numeric.empty:
        return 0.0
    if method == "empirical_pooled":
        value = float(numeric.quantile(service_level, interpolation="higher"))
    elif method == "normal_pooled":
        standard_deviation = float(numeric.std(ddof=1)) if len(numeric) > 1 else 0.0
        value = float(
            numeric.mean()
            + NormalDist().inv_cdf(service_level) * standard_deviation
        )
    else:
        raise ValueError(f"Unsupported pooled buffer method: {method}")
    return max(value, 0.0)


def fit_pooled_buffer(
    calibration: pd.DataFrame,
    prediction_col: str,
    method: str,
    service_level: float,
) -> dict[str, object]:
    if method not in {"normal_pooled", "empirical_pooled"}:
        raise ValueError(f"Cannot fit non-pooled buffer method: {method}")
    working = calibration[
        ["demand_pattern", "actual_usage", prediction_col]
    ].copy()
    working["_scale_band"] = _scale_band(working[prediction_col])
    working["_error"] = working["actual_usage"] - working[prediction_col]

    exact: dict[str, float] = {}
    for keys, group in working.groupby(
        ["demand_pattern", "_scale_band"],
        dropna=False,
        observed=True,
    ):
        if len(group) >= MIN_BUFFER_GROUP_ROWS:
            exact[f"{keys[0]}::{keys[1]}"] = _buffer_statistic(
                group["_error"],
                method,
                service_level,
            )
    pattern: dict[str, float] = {}
    for key, group in working.groupby(
        "demand_pattern",
        dropna=False,
        observed=True,
    ):
        if len(group) >= MIN_BUFFER_GROUP_ROWS:
            pattern[str(key)] = _buffer_statistic(
                group["_error"],
                method,
                service_level,
            )
    return {
        "method": method,
        "service_level": service_level,
        "exact": exact,
        "pattern": pattern,
        "global": _buffer_statistic(
            working["_error"],
            method,
            service_level,
        ),
    }


def apply_buffer(
    frame: pd.DataFrame,
    prediction_col: str,
    method: str,
    service_level: float,
    fitted: dict[str, object] | None = None,
) -> tuple[pd.Series, pd.Series]:
    if (
        prediction_col == "current_system_reference"
        and method == "existing_target_stock"
    ):
        if "target_stock" not in frame.columns:
            raise ValueError("Current system reference requires target_stock")
        prediction = pd.to_numeric(
            frame["target_stock"], errors="coerce"
        ).fillna(0.0)
    else:
        prediction = pd.to_numeric(
            frame[prediction_col], errors="coerce"
        ).fillna(0.0)
    if method in {"none", "existing_target_stock"}:
        buffer = pd.Series(0.0, index=frame.index)
    elif method == "fixed_20pct":
        buffer = prediction * 0.20
    elif method in {"normal_pooled", "empirical_pooled"}:
        if fitted is None:
            raise ValueError(f"Fitted parameters are required for {method}")
        scale_band = _scale_band(prediction)
        exact_key = (
            frame["demand_pattern"].astype(str) + "::" + scale_band.astype(str)
        )
        buffer = exact_key.map(fitted["exact"])
        buffer = buffer.fillna(
            frame["demand_pattern"].astype(str).map(fitted["pattern"])
        )
        buffer = buffer.fillna(float(fitted["global"]))
    else:
        raise ValueError(f"Unknown buffer method: {method}")
    buffer = pd.to_numeric(buffer, errors="coerce").fillna(0.0).clip(lower=0.0)
    return buffer, prediction + buffer


def inventory_metrics(
    actual,
    target,
    service_level: float = DEFAULT_SERVICE_LEVEL,
) -> dict[str, float | int | None]:
    actual = np.asarray(actual, dtype="float64")
    target = np.asarray(target, dtype="float64")
    finite = np.isfinite(actual) & np.isfinite(target)
    actual = np.clip(actual[finite], 0.0, None)
    target = np.clip(target[finite], 0.0, None)
    if len(actual) == 0:
        return {
            "N": 0,
            "ACTUAL_SUM": None,
            "TARGET_SUM": None,
            "PINBALL_LOSS": None,
            "PINBALL_WAPE": None,
            "ROW_SERVICE_RATE": None,
            "POSITIVE_ROW_SERVICE_RATE": None,
            "UNIT_FILL_RATE": None,
            "UNDERAGE_SUM": None,
            "OVERAGE_SUM": None,
            "TARGET_TO_ACTUAL_RATIO": None,
        }
    residual = actual - target
    pinball = np.maximum(
        service_level * residual,
        (service_level - 1.0) * residual,
    )
    positive = actual > 0
    actual_sum = float(actual.sum())
    target_sum = float(target.sum())
    underage = np.maximum(residual, 0.0)
    overage = np.maximum(-residual, 0.0)
    return {
        "N": int(len(actual)),
        "ACTUAL_SUM": actual_sum,
        "TARGET_SUM": target_sum,
        "PINBALL_LOSS": float(pinball.mean()),
        "PINBALL_WAPE": (
            float(pinball.sum() / actual_sum * 100)
            if actual_sum > 0
            else None
        ),
        "ROW_SERVICE_RATE": float(np.mean(target >= actual) * 100),
        "POSITIVE_ROW_SERVICE_RATE": (
            float(np.mean(target[positive] >= actual[positive]) * 100)
            if positive.any()
            else None
        ),
        "UNIT_FILL_RATE": (
            float(np.minimum(target, actual).sum() / actual_sum * 100)
            if actual_sum > 0
            else None
        ),
        "UNDERAGE_SUM": float(underage.sum()),
        "OVERAGE_SUM": float(overage.sum()),
        "TARGET_TO_ACTUAL_RATIO": (
            float(target_sum / actual_sum) if actual_sum > 0 else None
        ),
    }


def _evaluate_point_strategies(
    calibration: pd.DataFrame,
    evaluation: pd.DataFrame,
    strategy_columns: list[str],
) -> pd.DataFrame:
    rows = []
    for split_name, split in [
        ("calibration", calibration),
        ("evaluation", evaluation),
    ]:
        for column in strategy_columns:
            rows.append(
                {
                    "split": split_name,
                    "strategy": column,
                    **regression_metrics(split["actual_usage"], split[column]),
                }
            )
    return pd.DataFrame(rows).sort_values(
        ["split", "WAPE", "strategy"],
        kind="mergesort",
    )


def _evaluate_inventory_combinations(
    calibration: pd.DataFrame,
    evaluation: pd.DataFrame,
    strategy_columns: list[str],
    service_levels: list[float],
) -> tuple[
    pd.DataFrame,
    dict[tuple[str, str, float], dict[str, object]],
]:
    fitted_buffers: dict[tuple[str, str, float], dict[str, object]] = {}
    rows = []
    for service_level in service_levels:
        for strategy in strategy_columns:
            for method in BUFFER_METHODS:
                fitted = None
                if method in {"normal_pooled", "empirical_pooled"}:
                    fitted = fit_pooled_buffer(
                        calibration,
                        strategy,
                        method,
                        service_level,
                    )
                    fitted_buffers[(strategy, method, service_level)] = fitted
                for split_name, split in [
                    ("calibration", calibration),
                    ("evaluation", evaluation),
                ]:
                    _, target = apply_buffer(
                        split,
                        strategy,
                        method,
                        service_level,
                        fitted=fitted,
                    )
                    rows.append(
                        {
                            "split": split_name,
                            "forecast_strategy": strategy,
                            "buffer_method": method,
                            "service_level": service_level,
                            **inventory_metrics(
                                split["actual_usage"],
                                target,
                                service_level,
                            ),
                        }
                    )
        if (
            "target_stock" in calibration.columns
            and "target_stock" in evaluation.columns
        ):
            for split_name, split in [
                ("calibration", calibration),
                ("evaluation", evaluation),
            ]:
                rows.append(
                    {
                        "split": split_name,
                        "forecast_strategy": "current_system_reference",
                        "buffer_method": "existing_target_stock",
                        "service_level": service_level,
                        **inventory_metrics(
                            split["actual_usage"],
                            split["target_stock"],
                            service_level,
                        ),
                    }
                )
    return (
        pd.DataFrame(rows).sort_values(
            ["split", "PINBALL_LOSS", "forecast_strategy", "buffer_method"],
            kind="mergesort",
        ),
        fitted_buffers,
    )


def _selected_combination(
    inventory_evaluation: pd.DataFrame,
    service_level: float,
) -> tuple[pd.Series, pd.Series]:
    calibration = inventory_evaluation[
        inventory_evaluation["split"].eq("calibration")
        & inventory_evaluation["service_level"].eq(service_level)
    ]
    selected = calibration.sort_values(
        ["PINBALL_LOSS", "TARGET_SUM", "forecast_strategy", "buffer_method"],
        kind="mergesort",
    ).iloc[0]
    evaluation = inventory_evaluation[
        inventory_evaluation["split"].eq("evaluation")
        & inventory_evaluation["forecast_strategy"].eq(
            selected["forecast_strategy"]
        )
        & inventory_evaluation["buffer_method"].eq(selected["buffer_method"])
        & inventory_evaluation["service_level"].eq(service_level)
    ]
    if evaluation.empty:
        raise ValueError("Selected calibration combination is absent in evaluation")
    return selected, evaluation.iloc[0]


def _segment_evaluation(
    evaluation: pd.DataFrame,
    forecast_strategy: str,
    buffer_method: str,
    service_level: float,
    fitted_buffer: dict[str, object] | None,
) -> pd.DataFrame:
    buffer, target = apply_buffer(
        evaluation,
        forecast_strategy,
        buffer_method,
        service_level,
        fitted=fitted_buffer,
    )
    working = evaluation.copy()
    working["_prediction"] = target - buffer
    working["_target"] = target
    rows = []
    for pattern, group in working.groupby(
        "demand_pattern",
        dropna=False,
        observed=True,
    ):
        rows.append(
            {
                "demand_pattern": pattern,
                "forecast_strategy": forecast_strategy,
                "buffer_method": buffer_method,
                **regression_metrics(
                    group["actual_usage"],
                    group["_prediction"],
                ),
                **{
                    f"INVENTORY_{key}": value
                    for key, value in inventory_metrics(
                        group["actual_usage"],
                        group["_target"],
                        service_level,
                    ).items()
                },
            }
        )
    return pd.DataFrame(rows).sort_values("demand_pattern", kind="mergesort")


def _sample_output(
    evaluation: pd.DataFrame,
    forecast_strategy: str,
    buffer_method: str,
    service_level: float,
    fitted_buffer: dict[str, object] | None,
    sample_size: int,
) -> pd.DataFrame:
    buffer, target = apply_buffer(
        evaluation,
        forecast_strategy,
        buffer_method,
        service_level,
        fitted=fitted_buffer,
    )
    columns = [
        "forecast_origin_month",
        "year_month",
        "institution_code",
        "department",
        "item_code",
        "item_name",
        "stock_item_key",
        "actual_usage",
        "demand_pattern",
        "item_group_id_candidate",
    ]
    sample = evaluation[[column for column in columns if column in evaluation.columns]].copy()
    sample["selected_forecast_strategy"] = forecast_strategy
    sample["selected_point_forecast"] = target - buffer
    sample["selected_buffer_method"] = buffer_method
    sample["selected_safety_buffer"] = buffer
    sample["selected_target_stock_proxy"] = target
    sample["underage_proxy"] = (
        sample["actual_usage"] - sample["selected_target_stock_proxy"]
    ).clip(lower=0.0)
    sample["overage_proxy"] = (
        sample["selected_target_stock_proxy"] - sample["actual_usage"]
    ).clip(lower=0.0)
    ordered = sample.sort_values(
        ["underage_proxy", "overage_proxy", "actual_usage"],
        ascending=[False, False, False],
        kind="mergesort",
    )
    return ordered.head(sample_size).reset_index(drop=True)


def run_combination_experiment(
    backtest_path: Path = BACKTEST_PREDICTION_PATH,
    monthly_path: Path = MONTHLY_STOCK_PATH,
    point_output_path: Path = MODEL_COMBINATION_POINT_EVALUATION_PATH,
    inventory_output_path: Path = MODEL_COMBINATION_INVENTORY_EVALUATION_PATH,
    segment_output_path: Path = MODEL_COMBINATION_SEGMENT_EVALUATION_PATH,
    policy_output_path: Path = MODEL_COMBINATION_POLICY_PATH,
    sample_output_path: Path = MODEL_COMBINATION_SAMPLE_PATH,
    calibration_month_count: int = DEFAULT_CALIBRATION_MONTH_COUNT,
    service_level: float = DEFAULT_SERVICE_LEVEL,
    sample_size: int = 1000,
    include_tsb: bool = True,
) -> dict[str, Path]:
    setup_logging()
    if not 0.5 < service_level < 1.0:
        raise ValueError("service_level must be between 0.5 and 1.0")
    if sample_size <= 0:
        raise ValueError("sample_size must be positive")

    header = pd.read_csv(backtest_path, nrows=0).columns
    candidate_columns = available_forecast_columns(header)
    required = {
        "forecast_origin_month",
        "year_month",
        "stock_item_key",
        "actual_usage",
        "demand_pattern",
        "item_group_id_candidate",
        *REQUIRED_FORECAST_COLUMNS,
    }
    missing = sorted(required - set(header))
    if missing:
        raise ValueError(f"Backtest predictions are missing columns: {missing}")
    optional = {
        "institution_code",
        "department",
        "item_code",
        "item_name",
        "target_stock",
    }
    usecols = selected_backtest_columns(
        header,
        required,
        optional,
        candidate_columns,
    )
    frame = pd.read_csv(
        backtest_path,
        usecols=usecols,
        parse_dates=["forecast_origin_month", "year_month"],
    )
    if include_tsb:
        monthly = pd.read_parquet(
            monthly_path,
            columns=["year_month", "stock_item_key", "consumption_qty"],
        )
        for pooling_mode in ["pattern", "pattern_item_group"]:
            column = f"tsb_hb_moment_{pooling_mode}_pred"
            LOGGER.info("Building %s", column)
            frame[column] = build_tsb_hb_predictions(
                monthly,
                frame,
                pooling_mode=pooling_mode,
            )
            candidate_columns.append(column)
        del monthly

    frame, blend_columns = add_blend_candidates(frame)
    candidate_columns.extend(blend_columns)
    calibration, evaluation, calibration_months, evaluation_months = _split_months(
        frame,
        calibration_month_count,
    )
    router, router_fallback = select_pattern_router(
        calibration,
        candidate_columns,
    )
    frame["calibration_pattern_router_pred"] = apply_pattern_router(
        frame,
        router,
        router_fallback,
    )
    candidate_columns.append("calibration_pattern_router_pred")
    calibration, evaluation, _, _ = _split_months(
        frame,
        calibration_month_count,
    )

    point_evaluation = _evaluate_point_strategies(
        calibration,
        evaluation,
        candidate_columns,
    )
    inventory_evaluation, fitted_buffers = _evaluate_inventory_combinations(
        calibration,
        evaluation,
        candidate_columns,
        sorted(set([*SERVICE_LEVEL_SENSITIVITY, service_level])),
    )
    selected_calibration, selected_evaluation = _selected_combination(
        inventory_evaluation,
        service_level,
    )
    selected_strategy = str(selected_calibration["forecast_strategy"])
    selected_buffer_method = str(selected_calibration["buffer_method"])
    selected_fitted_buffer = fitted_buffers.get(
        (selected_strategy, selected_buffer_method, service_level)
    )
    segment_evaluation = _segment_evaluation(
        evaluation,
        selected_strategy,
        selected_buffer_method,
        service_level,
        selected_fitted_buffer,
    )
    sample = _sample_output(
        evaluation,
        selected_strategy,
        selected_buffer_method,
        service_level,
        selected_fitted_buffer,
        sample_size,
    )

    calibration_point = point_evaluation[
        point_evaluation["split"].eq("calibration")
    ].sort_values(["WAPE", "strategy"], kind="mergesort").iloc[0]
    selected_point_strategy = str(calibration_point["strategy"])
    selected_point_evaluation = point_evaluation[
        point_evaluation["split"].eq("evaluation")
        & point_evaluation["strategy"].eq(selected_point_strategy)
    ].iloc[0]
    sensitivity = {}
    evaluated_service_levels = sorted(
        set([*SERVICE_LEVEL_SENSITIVITY, service_level])
    )
    for evaluated_level in evaluated_service_levels:
        level_calibration, level_evaluation = _selected_combination(
            inventory_evaluation,
            evaluated_level,
        )
        sensitivity[str(evaluated_level)] = {
            "forecast_strategy": str(level_calibration["forecast_strategy"]),
            "buffer_method": str(level_calibration["buffer_method"]),
            "calibration_pinball_loss": _json_value(
                level_calibration["PINBALL_LOSS"]
            ),
            "evaluation_pinball_loss": _json_value(
                level_evaluation["PINBALL_LOSS"]
            ),
            "evaluation_positive_row_service_rate": _json_value(
                level_evaluation["POSITIVE_ROW_SERVICE_RATE"]
            ),
            "evaluation_unit_fill_rate": _json_value(
                level_evaluation["UNIT_FILL_RATE"]
            ),
            "evaluation_target_to_actual_ratio": _json_value(
                level_evaluation["TARGET_TO_ACTUAL_RATIO"]
            ),
        }
    current_reference = inventory_evaluation[
        inventory_evaluation["split"].eq("evaluation")
        & inventory_evaluation["service_level"].eq(service_level)
        & inventory_evaluation["forecast_strategy"].eq(
            "current_system_reference"
        )
    ]
    policy = {
        "version": EXPERIMENT_VERSION,
        "status": "reused_evaluation_slice_not_operational",
        "source": portable_artifact_path(backtest_path, PROJECT_ROOT),
        "calibration_months": calibration_months,
        "evaluation_months": evaluation_months,
        "service_level": service_level,
        "evaluated_service_levels": evaluated_service_levels,
        "selection_rule": (
            "minimum calibration PINBALL_LOSS at target service level; "
            "evaluation months are not used for selection, but were already "
            "inspected by earlier experiments and are not a final untouched test"
        ),
        "evaluation_contract": {
            "role": "diagnostic_reused_evaluation",
            "clean_final_test": False,
            "next_clean_test_requirement": (
                "2026 raw_stock or a future rolling holdout unused by model "
                "selection, buffer calibration, and policy tuning"
            ),
            "policy_parameters_fit_before_evaluation": True,
        },
        "point_forecast_selection": {
            "strategy": selected_point_strategy,
            "calibration_wape": _json_value(calibration_point["WAPE"]),
            "evaluation_wape": _json_value(selected_point_evaluation["WAPE"]),
            "evaluation_bias_pct": _json_value(
                selected_point_evaluation["BIAS_PCT"]
            ),
        },
        "inventory_combination_selection": {
            "forecast_strategy": selected_strategy,
            "buffer_method": selected_buffer_method,
            "calibration_pinball_loss": _json_value(
                selected_calibration["PINBALL_LOSS"]
            ),
            "evaluation_pinball_loss": _json_value(
                selected_evaluation["PINBALL_LOSS"]
            ),
            "evaluation_positive_row_service_rate": _json_value(
                selected_evaluation["POSITIVE_ROW_SERVICE_RATE"]
            ),
            "evaluation_unit_fill_rate": _json_value(
                selected_evaluation["UNIT_FILL_RATE"]
            ),
            "evaluation_target_to_actual_ratio": _json_value(
                selected_evaluation["TARGET_TO_ACTUAL_RATIO"]
            ),
        },
        "service_level_sensitivity": sensitivity,
        "current_system_reference": (
            {
                key: _json_value(current_reference.iloc[0][key])
                for key in [
                    "PINBALL_LOSS",
                    "POSITIVE_ROW_SERVICE_RATE",
                    "UNIT_FILL_RATE",
                    "TARGET_TO_ACTUAL_RATIO",
                ]
            }
            if not current_reference.empty
            else None
        ),
        "pattern_router": {
            "fallback": router_fallback,
            "routes": router,
        },
        "candidate_forecasts": candidate_columns,
        "candidate_notes": {
            "tsb_hb_moment_pattern_pred": (
                "Occurrence-size hierarchical Bayesian approximation using "
                "moment-matched empirical-Bayes hyperparameters by demand pattern."
            ),
            "tsb_hb_moment_pattern_item_group_pred": (
                "The same approximation with demand-pattern and item-group pooling; "
                "small pools fall back to demand pattern."
            ),
            "calibration_pattern_router_pred": (
                "Demand-pattern route selected only from calibration months."
            ),
        },
        "buffer_methods": BUFFER_METHODS,
        "excluded_methods": {
            "full_period_mu_corrected": (
                "Excluded from holdout ranking because the current artifact uses "
                "observations through 2025-12 and would leak evaluation targets. "
                "It also has institution-item rather than department-item grain."
            ),
            "robbins_poisson": (
                "Excluded because observation exposure differs by series, demand "
                "contains batch quantities, and censored demand violates the model."
            ),
            "garch": (
                "Excluded because only five forecast-error months are available; "
                "per-series conditional variance estimation is not identifiable."
            ),
        },
        "limitations": [
            "The inventory evaluation is a one-month demand-coverage proxy, not a procurement lead-time simulation.",
            "Actual order-to-receipt lead times, backorders, and lost-sales labels are unavailable.",
            "The evaluation holdout contains only three months.",
            "The same 2025 evaluation period has been inspected by prior work and cannot be called a clean final test.",
            "Bulk-approved taxonomy may include low-evidence classifications; TSB pooling falls back by demand pattern for small groups.",
        ],
    }

    ensure_dirs(
        OUTPUT_DIR,
        SAMPLE_DATA_DIR,
        point_output_path.parent,
        inventory_output_path.parent,
        segment_output_path.parent,
        policy_output_path.parent,
        sample_output_path.parent,
    )
    point_evaluation.to_csv(point_output_path, index=False)
    inventory_evaluation.to_csv(inventory_output_path, index=False)
    segment_evaluation.to_csv(segment_output_path, index=False)
    sample.to_csv(sample_output_path, index=False, encoding="utf-8-sig")
    with policy_output_path.open("w", encoding="utf-8") as file:
        json.dump(policy, file, ensure_ascii=False, indent=2)
    LOGGER.info("Saved combination policy: %s", policy_output_path)
    return {
        "point_evaluation": point_output_path,
        "inventory_evaluation": inventory_output_path,
        "segment_evaluation": segment_output_path,
        "policy": policy_output_path,
        "sample": sample_output_path,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Leakage-controlled forecast and safety-stock combination experiment"
    )
    parser.add_argument("--backtest-path", type=Path, default=BACKTEST_PREDICTION_PATH)
    parser.add_argument("--monthly-path", type=Path, default=MONTHLY_STOCK_PATH)
    parser.add_argument(
        "--calibration-month-count",
        type=int,
        default=DEFAULT_CALIBRATION_MONTH_COUNT,
    )
    parser.add_argument(
        "--service-level",
        type=float,
        default=DEFAULT_SERVICE_LEVEL,
    )
    parser.add_argument("--sample-size", type=int, default=1000)
    parser.add_argument("--skip-tsb", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_combination_experiment(
        backtest_path=args.backtest_path,
        monthly_path=args.monthly_path,
        calibration_month_count=args.calibration_month_count,
        service_level=args.service_level,
        sample_size=args.sample_size,
        include_tsb=not args.skip_tsb,
    )


if __name__ == "__main__":
    main()
