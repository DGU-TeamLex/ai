import gc
import json
import logging
import pickle
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from ..config import (
    BACKTEST_PREDICTION_PATH,
    EVALUATION_REPORT_PATH,
    EVALUATION_SEGMENT_REPORT_PATH,
    FEATURE_TABLE_PATH,
    FORECAST_ENSEMBLE_POLICY_PATH,
    INVENTORY_STATUS_PATH,
    MODEL_DIR,
    MODEL_MANIFEST_PATH,
    OUTPUT_DIR,
    PREDICTION_PATH,
    SERIES_KEYS,
    TARGET_COLUMN,
    TEST_START,
    TRAIN_START,
    VALID_END,
)
from ..feature_engineering import run_feature_engineering
from ..material_mapping import attach_approved_material_mapping_metadata
from ..trade.trade_inventory_impact import write_trade_inventory_impact
from ..utils import guard_not_empty, ensure_dirs, setup_logging
from .baseline import BASELINE_PREDICTION_COLUMNS, add_baseline_predictions
from .classified_prediction import write_classified_prediction_outputs
from .data_quality import attach_standardization_metadata
from .evaluation import (
    build_evaluation_report,
    build_segment_evaluation_report,
    classify_demand_patterns,
)
from .inventory_policy import add_inventory_recommendations
from .training import run_training, transform_features


LOGGER = logging.getLogger(__name__)
INVENTORY_STATUS_PREDICTION_COLUMNS = [
    "stock_item_key",
    "mean_daily_usage",
    "daily_demand_stddev",
    "raw_mean_daily_usage",
    "raw_daily_demand_stddev",
    "mu_is_floored",
    "sigma_is_floored",
    "mu_forecast_3m_92d",
    "observation_period_days",
    "zero_stock_reason",
    "demand_class",
    "inventory_action",
    "urgent_shortage",
    "exact_group_total_stock",
]
RISK_COLUMNS = [
    "disease_news_risk",
    "supply_news_risk",
    "material_news_risk",
    "total_news_risk",
    "commodity_risk",
    "module_c_demand_risk",
    "module_c_supply_news_risk",
    "module_c_material_news_risk",
    "module_c_market_price_risk",
    "module_c_trade_risk",
    "module_c_supply_risk",
    "module_c_total_risk",
    "module_c_signal_confidence",
]
STANDARD_ITEM_OUTPUT_COLUMNS = [
    "standard_item_key",
    "standard_item_definition_key",
    "standard_item_group_id",
    "standard_item_family_id",
    "standard_item_subtype_id",
    "standard_item_specification",
    "standard_item_unit_code",
    "standardization_match_method",
    "data_period",
]
PREDICTION_REQUIRED_FEATURE_COLUMNS = {
    "year_month",
    "forecast_month",
    *SERIES_KEYS,
    "item_name",
    "stock_item_key",
    "standard_item_key",
    "standard_item_definition_key",
    "standard_item_group_id",
    "standard_item_family_id",
    "standard_item_subtype_id",
    "standard_item_specification",
    "standard_item_unit_code",
    "standardization_match_method",
    "data_period",
    "month_end_stock",
    "history_months",
    "demand_qty",
    TARGET_COLUMN,
    "rolling_std_3",
    "rolling_std_6",
    "rolling_std_12",
    "lag_1",
    "rolling_mean_3",
    "rolling_median_3",
    "rolling_mean_6",
    "same_month_last_year",
    "expanding_mean",
    *RISK_COLUMNS,
}
PREDICTION_OPTIONAL_FEATURE_COLUMNS = {
    # 전처리 원장에는 있지만 현재 축약 특성표에는 없을 수 있다. 결과 표시용일
    # 뿐 모형 입력·예측 계산에는 필요하지 않다.
    "average_unit_price",
}


def attach_current_inventory_status_parameters(
    frame: pd.DataFrame,
    path: Path = INVENTORY_STATUS_PATH,
) -> pd.DataFrame:
    if not path.exists():
        LOGGER.warning(
            "Inventory status output is unavailable; daily SS/ROP inputs are omitted: %s",
            path,
        )
        return frame
    header = pd.read_csv(path, nrows=0).columns
    missing = sorted(set(INVENTORY_STATUS_PREDICTION_COLUMNS) - set(header))
    if missing:
        raise ValueError(f"Inventory status output is missing columns: {missing}")
    status = pd.read_csv(
        path,
        usecols=INVENTORY_STATUS_PREDICTION_COLUMNS,
        dtype={"stock_item_key": str},
        low_memory=False,
    )
    if status["stock_item_key"].duplicated().any():
        raise ValueError("Inventory status output is not unique by stock_item_key")
    result = frame.merge(
        status,
        on="stock_item_key",
        how="left",
        validate="many_to_one",
    )
    result["inventory_status_parameters_available"] = result[
        ["mean_daily_usage", "daily_demand_stddev"]
    ].notna().all(axis=1)
    return result


def _load_feature_table(manifest: list[dict]) -> pd.DataFrame:
    if not FEATURE_TABLE_PATH.exists():
        run_feature_engineering()
    columns = set(PREDICTION_REQUIRED_FEATURE_COLUMNS)
    columns.update(PREDICTION_OPTIONAL_FEATURE_COLUMNS)
    for row in manifest:
        if row.get("method_type") == "machine_learning" and row.get("status") == "ready":
            columns.update(_load_bundle(row["model"])["feature_cols"])

    available = set(pq.ParquetFile(FEATURE_TABLE_PATH).schema_arrow.names)
    missing_required = sorted(
        columns - available - PREDICTION_OPTIONAL_FEATURE_COLUMNS
    )
    if missing_required:
        raise ValueError(
            "Prediction feature table is missing required columns: "
            f"{missing_required}"
        )
    missing_optional = sorted(PREDICTION_OPTIONAL_FEATURE_COLUMNS - available)
    if missing_optional:
        LOGGER.info(
            "Prediction feature table omits optional display columns: %s",
            ", ".join(missing_optional),
        )
    columns.intersection_update(available)
    return pd.read_parquet(
        FEATURE_TABLE_PATH,
        columns=sorted(columns),
        filters=[
            ("year_month", ">=", pd.Timestamp(TEST_START)),
        ],
    )


def _load_demand_pattern_history() -> pd.DataFrame:
    return pd.read_parquet(
        FEATURE_TABLE_PATH,
        columns=[
            "year_month",
            *SERIES_KEYS,
            "demand_qty",
        ],
        filters=[
            ("year_month", ">=", pd.Timestamp(TRAIN_START)),
            ("year_month", "<=", pd.Timestamp(VALID_END)),
        ],
    )


def _load_manifest() -> list[dict]:
    if not MODEL_MANIFEST_PATH.exists():
        run_training()
    with MODEL_MANIFEST_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def _load_bundle(model_name: str) -> dict:
    path = MODEL_DIR / f"{model_name}.pkl"
    if not path.exists():
        raise FileNotFoundError(f"Trained model bundle not found: {path}")
    with path.open("rb") as file:
        return pickle.load(file)


def _selected_prediction_column(manifest: list[dict], available_columns: set[str]) -> str:
    selected = [row for row in manifest if row.get("selected_on_validation")]
    ranked = selected or sorted(
        [row for row in manifest if row.get("WAPE") is not None],
        key=lambda row: row["WAPE"],
    )
    for row in ranked:
        prediction_column = f"{row['model']}_pred"
        if prediction_column in available_columns:
            return prediction_column
    raise ValueError("No validation-selected prediction column is available")


def _add_trained_model_predictions(
    frame: pd.DataFrame,
    manifest: list[dict],
) -> pd.DataFrame:
    result = frame
    trained_models = [
        row["model"]
        for row in manifest
        if row.get("method_type") == "machine_learning" and row.get("status") == "ready"
    ]
    for model_name in trained_models:
        bundle = _load_bundle(model_name)
        features = transform_features(result, bundle["preprocess"])
        if str(bundle["algorithm"]).startswith("lightgbm"):
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="X does not have valid feature names")
                raw_prediction = bundle["model"].predict(features, validate_features=False)
        else:
            raw_prediction = bundle["model"].predict(features)
        result[f"{model_name}_pred"] = np.clip(raw_prediction, 0, None)
    return result


def _apply_temporal_ensemble(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, dict | None]:
    if not FORECAST_ENSEMBLE_POLICY_PATH.exists():
        return frame, None
    with FORECAST_ENSEMBLE_POLICY_PATH.open(
        "r",
        encoding="utf-8",
    ) as file:
        policy = json.load(file)
    if not policy.get("apply_to_prediction", False):
        return frame, None
    weights = {
        str(column): float(weight)
        for column, weight in policy.get(
            "selected_weights",
            {},
        ).items()
    }
    if not weights:
        raise ValueError("Forecast ensemble policy has no selected weights")
    if any(weight < 0 for weight in weights.values()):
        raise ValueError("Forecast ensemble weights must be non-negative")
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        raise ValueError("Forecast ensemble weights must sum to 1.0")
    missing = sorted(set(weights) - set(frame.columns))
    if missing:
        # 모델 재학습에서 어떤 변형이 품질 게이트로 제외되면 이전 실행의
        # 앙상블 정책은 더 이상 현재 모델 집합과 호환되지 않는다. 초기 예측은
        # 새 temporal tuning 의 입력이므로 여기서 오래된 정책 때문에 막지 않고
        # 단일 모델 예측을 만든 뒤, 다음 단계가 사용 가능한 열로 정책을 갱신한다.
        LOGGER.warning(
            "Skipping incompatible forecast ensemble policy; "
            "model columns are unavailable: %s",
            ", ".join(missing),
        )
        return frame, None
    prediction = np.zeros(len(frame), dtype="float64")
    for column, weight in weights.items():
        prediction += (
            pd.to_numeric(frame[column], errors="coerce")
            .fillna(0.0)
            .to_numpy(dtype="float64")
            * weight
        )
    result = frame.copy()
    result["temporal_ensemble_pred"] = np.clip(
        prediction,
        0,
        None,
    )
    return result, policy


def _apply_pattern_ensemble(
    frame: pd.DataFrame,
    policy: dict,
) -> pd.DataFrame:
    if policy.get("selected_strategy") != "pattern_weight_router":
        return frame
    result = frame.copy()
    pattern = result["demand_pattern"].fillna("new_series").astype(str)
    for pattern_name, raw_weights in policy.get(
        "pattern_weights",
        {},
    ).items():
        weights = {
            str(column): float(weight)
            for column, weight in raw_weights.items()
        }
        if any(weight < 0 for weight in weights.values()):
            raise ValueError(
                "Pattern ensemble weights must be non-negative"
            )
        if abs(sum(weights.values()) - 1.0) > 1e-9:
            raise ValueError(
                "Pattern ensemble weights must sum to 1.0"
            )
        missing = sorted(set(weights) - set(result.columns))
        if missing:
            raise ValueError(
                f"Pattern ensemble model columns are missing: {missing}"
            )
        mask = pattern.eq(str(pattern_name))
        if not mask.any():
            continue
        prediction = np.zeros(int(mask.sum()), dtype="float64")
        for column, weight in weights.items():
            prediction += (
                pd.to_numeric(
                    result.loc[mask, column],
                    errors="coerce",
                )
                .fillna(0.0)
                .to_numpy(dtype="float64")
                * weight
            )
        result.loc[mask, "temporal_ensemble_pred"] = np.clip(
            prediction,
            0,
            None,
        )
    return result


def _build_prediction_frame(
    source: pd.DataFrame,
    manifest: list[dict],
    demand_patterns: pd.DataFrame,
    prediction_type: str,
) -> pd.DataFrame:
    enriched = add_baseline_predictions(source)
    enriched = _add_trained_model_predictions(enriched, manifest)
    enriched, ensemble_policy = _apply_temporal_ensemble(enriched)
    model_prediction_columns = [
        column
        for column in enriched.columns
        if column.endswith("_pred") and column not in BASELINE_PREDICTION_COLUMNS
    ]
    prediction_columns = [*BASELINE_PREDICTION_COLUMNS, *model_prediction_columns]

    output_columns = [
        "year_month",
        "forecast_month",
        "institution_code",
        "department",
        "item_code",
        "item_name",
        "stock_item_key",
        *STANDARD_ITEM_OUTPUT_COLUMNS,
        "month_end_stock",
        "history_months",
        TARGET_COLUMN,
        "rolling_std_3",
        "rolling_std_6",
        "rolling_std_12",
        "average_unit_price",
        *RISK_COLUMNS,
        *prediction_columns,
    ]
    output = enriched[[column for column in output_columns if column in enriched.columns]].copy()
    output = output.rename(
        columns={
            "year_month": "forecast_origin_month",
            "forecast_month": "year_month",
            TARGET_COLUMN: "actual_usage",
        }
    )
    if prediction_type == "future":
        output["actual_usage"] = pd.NA

    output = output.merge(demand_patterns, on=SERIES_KEYS, how="left", validate="many_to_one")
    output["demand_pattern"] = output["demand_pattern"].fillna("new_series")
    if ensemble_policy is not None:
        output = _apply_pattern_ensemble(output, ensemble_policy)
    output = attach_standardization_metadata(output)

    primary_column = (
        "temporal_ensemble_pred"
        if ensemble_policy is not None
        else _selected_prediction_column(
            manifest,
            set(prediction_columns),
        )
    )
    output["primary_model"] = primary_column
    output["predicted_usage"] = output[primary_column]
    if ensemble_policy is not None:
        manifest_by_prediction = {
            f"{row['model']}_pred": row for row in manifest
        }
        fallback_weights = ensemble_policy["selected_weights"]
        pattern_weights = ensemble_policy.get("pattern_weights", {})
        effective_external_weight = pd.Series(0.0, index=output.index)
        effective_module_c_weight = pd.Series(0.0, index=output.index)
        patterns = output["demand_pattern"].fillna("new_series").astype(str)
        for pattern_name in patterns.unique():
            weights = (
                pattern_weights.get(pattern_name, fallback_weights)
                if ensemble_policy.get("selected_strategy") == "pattern_weight_router"
                else fallback_weights
            )
            mask = patterns.eq(pattern_name)
            effective_external_weight.loc[mask] = sum(
                float(weight)
                for column, weight in weights.items()
                if any(
                    manifest_by_prediction.get(column, {}).get(flag, False)
                    for flag in ["uses_news", "uses_commodity", "uses_module_c"]
                )
            )
            effective_module_c_weight.loc[mask] = sum(
                float(weight)
                for column, weight in weights.items()
                if manifest_by_prediction.get(column, {}).get("uses_module_c", False)
            )
        output["external_signal_effective_weight"] = effective_external_weight
        output["module_c_effective_weight"] = effective_module_c_weight
        output["external_demand_signal_in_forecast"] = effective_external_weight.gt(0)
        output["forecast_ensemble_policy_version"] = (
            ensemble_policy["version"]
        )
    else:
        primary_name = primary_column.removesuffix("_pred")
        primary_spec = next(
            (
                row
                for row in manifest
                if row.get("model") == primary_name
            ),
            {},
        )
        output["external_demand_signal_in_forecast"] = bool(
            primary_spec.get("uses_news", False)
            or primary_spec.get("uses_commodity", False)
            or primary_spec.get("uses_module_c", False)
        )
        output["forecast_ensemble_policy_version"] = ""
    output["current_stock"] = output["month_end_stock"].fillna(0.0)
    output["prediction_type"] = prediction_type
    output = attach_approved_material_mapping_metadata(output)
    if prediction_type == "future":
        output = attach_current_inventory_status_parameters(output)

    current_month = pd.Timestamp.now().to_period("M").to_timestamp()
    origin_month = pd.to_datetime(output["forecast_origin_month"])
    output["data_age_months"] = (
        (current_month.year - origin_month.dt.year) * 12
        + current_month.month
        - origin_month.dt.month
    )
    output["is_stale_data"] = output["data_age_months"].gt(1)
    # lead_time_days_col 을 넘기지 않으면 _resolve_lead_time 이 항상 NaN 을 보고
    # 전 행에 fallback 15일을 적용한다. 정책 파일은 method="stockout_duration_p25"
    # 즉 품목별 추정을 쓰겠다고 선언하고 있으므로, 상류가 품목별 값을 실어 보내면
    # 그것을 존중해야 한다. 컬럼이 없으면 종전과 동일하게 fallback 이 적용된다.
    return add_inventory_recommendations(
        output,
        prediction_col="predicted_usage",
        current_stock_col="current_stock",
        lead_time_days_col="lead_time_days",
        review_period_days_col="review_period_days",
    )


def build_prediction_outputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    manifest = _load_manifest()
    demand_history = _load_demand_pattern_history()
    demand_patterns = classify_demand_patterns(demand_history)
    del demand_history
    gc.collect()
    feature_table = _load_feature_table(manifest)
    eligible_mask = (
        feature_table["lag_1"].notna()
        & feature_table["lag_1"].ge(0)
        & feature_table["rolling_mean_3"].notna()
    )
    test = feature_table[
        eligible_mask
        & feature_table[TARGET_COLUMN].notna()
        & feature_table[TARGET_COLUMN].ge(0)
        & feature_table["year_month"].ge(pd.Timestamp(TEST_START))
    ].copy()
    latest_origin = feature_table.loc[eligible_mask, "year_month"].max()
    future = feature_table[eligible_mask & feature_table["year_month"].eq(latest_origin)].copy()
    del feature_table, eligible_mask
    gc.collect()
    if test.empty or future.empty:
        raise ValueError(f"Prediction inputs are empty: test={len(test)}, future={len(future)}")

    backtest = _build_prediction_frame(
        test,
        manifest,
        demand_patterns,
        prediction_type="backtest",
    )
    current_forecast = _build_prediction_frame(
        future,
        manifest,
        demand_patterns,
        prediction_type="future",
    )
    return backtest, current_forecast


def build_predictions() -> pd.DataFrame:
    _, current_forecast = build_prediction_outputs()
    return current_forecast


def run_prediction() -> None:
    setup_logging()
    ensure_dirs(OUTPUT_DIR)
    backtest, current_forecast = build_prediction_outputs()
    guard_not_empty(backtest, BACKTEST_PREDICTION_PATH, "백테스트 예측")
    guard_not_empty(current_forecast, PREDICTION_PATH, "현재 예측")
    backtest.to_csv(BACKTEST_PREDICTION_PATH, index=False)
    current_forecast.to_csv(PREDICTION_PATH, index=False)
    write_classified_prediction_outputs(current_forecast)
    write_trade_inventory_impact(current_forecast)

    report = build_evaluation_report(backtest)
    report.to_csv(EVALUATION_REPORT_PATH, index=False)
    segment_report = build_segment_evaluation_report(backtest)
    segment_report.to_csv(EVALUATION_SEGMENT_REPORT_PATH, index=False)
    LOGGER.info("Saved current forecast: %s (%s rows)", PREDICTION_PATH, len(current_forecast))
    LOGGER.info("Saved backtest predictions: %s (%s rows)", BACKTEST_PREDICTION_PATH, len(backtest))
    LOGGER.info("Saved evaluation report: %s", EVALUATION_REPORT_PATH)


if __name__ == "__main__":
    run_prediction()
