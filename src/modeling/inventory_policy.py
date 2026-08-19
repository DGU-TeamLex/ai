import json
from statistics import NormalDist

import numpy as np
import pandas as pd

from ..config import (
    DEFAULT_REVIEW_PERIOD_DAYS,
    DEMAND_RISK_BUFFER_RATE,
    INVENTORY_OPTIMIZATION_POLICY_PATH,
    MATERIAL_RISK_BUFFER_RATE,
    MAX_RISK_BUFFER_RATE,
    SUPPLY_RISK_BUFFER_RATE,
)
from ..module_c.config import load_module_c_config
from ..module_c.supply_risk_policy import load_supply_risk_policy
from .order_quality_gate import apply_order_quality_gates


MODULE_C_POLICY_COLUMNS = {
    "module_c_demand_risk",
    "module_c_supply_risk",
    "module_c_total_risk",
}


def load_inventory_optimization_policy() -> dict:
    with INVENTORY_OPTIMIZATION_POLICY_PATH.open("r", encoding="utf-8") as file:
        policy = json.load(file)
    costs = policy["costs"]
    if float(costs["overage_cost_per_excess_unit"]) <= 0:
        raise ValueError("Inventory overage cost must be positive")
    if float(costs["underage_cost_per_unfilled_unit"]) <= 0:
        raise ValueError("Inventory underage cost must be positive")
    return policy


def _cost_service_parameters(
    index: pd.Index,
    policy: dict,
    supply_risk: pd.Series | None = None,
) -> tuple[pd.Series, pd.Series]:
    costs = policy["costs"]
    uncertainty = policy["uncertainty"]
    risk = (
        supply_risk.reindex(index).fillna(0.0).clip(0, 1)
        if supply_risk is not None
        else pd.Series(0.0, index=index)
    )
    overage = float(costs["overage_cost_per_excess_unit"])
    underage = float(costs["underage_cost_per_unfilled_unit"]) * (
        1
        + risk
        * float(costs.get("supply_risk_underage_multiplier_max", 0.0))
    )
    critical_ratio = (underage / (underage + overage)).clip(
        float(uncertainty["minimum_critical_ratio"]),
        float(uncertainty["maximum_critical_ratio"]),
    )
    service_z = critical_ratio.map(NormalDist().inv_cdf)
    return critical_ratio, service_z


def _monthly_uncertainty(
    frame: pd.DataFrame,
    predicted_usage: pd.Series,
    protection_days: pd.Series,
    service_z: pd.Series,
    policy: dict,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    uncertainty = policy["uncertainty"]
    monthly_std = pd.Series(np.nan, index=frame.index, dtype="float64")
    source = pd.Series("", index=frame.index, dtype="string")
    for column in uncertainty["preferred_columns"]:
        if column not in frame.columns:
            continue
        candidate = pd.to_numeric(frame[column], errors="coerce")
        usable = monthly_std.isna() & candidate.notna() & candidate.ge(0)
        monthly_std = monthly_std.where(~usable, candidate)
        source = source.where(~usable, column)

    scale = np.sqrt(protection_days.clip(lower=1.0) / 30.0)
    fallback_rate = float(uncertainty["fallback_safety_stock_rate"])
    fallback_std = (
        predicted_usage
        * fallback_rate
        * scale
        / service_z.replace(0, np.nan)
    ).fillna(0.0)
    fallback = monthly_std.isna()
    monthly_std = monthly_std.where(~fallback, fallback_std).clip(lower=0.0)
    source = source.where(~fallback, "fixed_rate_fallback")
    return monthly_std, source, scale


def _numeric_column(
    df: pd.DataFrame,
    column: str | None,
    default: float,
    lower: float | None = None,
    upper: float | None = None,
) -> pd.Series:
    if column and column in df.columns:
        values = pd.to_numeric(df[column], errors="coerce").fillna(default)
    else:
        values = pd.Series(default, index=df.index, dtype="float64")
    return values.clip(lower=lower, upper=upper)


def _approved_mapping_gate(df: pd.DataFrame) -> pd.Series:
    gate = pd.Series(True, index=df.index, dtype=bool)
    if "has_approved_material_mapping" in df.columns:
        approved = (
            df["has_approved_material_mapping"]
            .astype("string")
            .fillna("")
            .str.strip()
            .str.lower()
            .isin({"true", "t", "1", "yes", "y"})
        )
        gate &= approved
    if "approved_material_mapping_count" in df.columns:
        mapping_count = _numeric_column(
            df,
            "approved_material_mapping_count",
            0.0,
            lower=0.0,
        )
        gate &= mapping_count.gt(0)
    return gate


def _boolean_column(df: pd.DataFrame, column: str, default: bool = False) -> pd.Series:
    if column not in df.columns:
        return pd.Series(default, index=df.index, dtype=bool)
    return (
        df[column]
        .astype("string")
        .fillna("")
        .str.strip()
        .str.lower()
        .isin({"true", "t", "1", "yes", "y"})
    )


def _resolve_lead_time(
    df: pd.DataFrame,
    column: str | None,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series, dict]:
    policy = load_supply_risk_policy()
    lead_time_policy = policy["lead_time_estimation"]
    minimum = float(lead_time_policy["minimum_days"])
    fallback = float(lead_time_policy["fallback_days"])
    maximum = float(lead_time_policy["maximum_days"])
    if column and column in df.columns:
        raw = pd.to_numeric(df[column], errors="coerce").astype("float64")
    else:
        raw = pd.Series(np.nan, index=df.index, dtype="float64")
    finite = pd.Series(
        np.isfinite(raw.to_numpy(dtype="float64")),
        index=df.index,
    )
    fallback_applied = ~finite | raw.lt(minimum)
    cap_applied = finite & raw.gt(maximum)
    resolved = raw.where(~fallback_applied, fallback).clip(
        lower=minimum,
        upper=maximum,
    )
    return raw, resolved, fallback_applied, cap_applied, policy


def calculate_risk_components(df: pd.DataFrame) -> pd.DataFrame:
    mapping_gate = _approved_mapping_gate(df).astype(float)
    demand = _numeric_column(df, "disease_news_risk", 0.0, 0.0, 1.0) * mapping_gate
    supply = _numeric_column(df, "supply_news_risk", 0.0, 0.0, 1.0) * mapping_gate
    material_news = (
        _numeric_column(df, "material_news_risk", 0.0, 0.0, 1.0) * mapping_gate
    )
    commodity = _numeric_column(df, "commodity_risk", 0.0, 0.0, 1.0) * mapping_gate
    return pd.DataFrame(
        {
            "demand_risk_score": demand,
            "supply_risk_score": supply,
            "material_risk_score": pd.concat(
                [material_news, commodity], axis=1
            ).max(axis=1),
        },
        index=df.index,
    )


def calculate_external_risk_score(df: pd.DataFrame) -> pd.Series:
    risk = calculate_risk_components(df)
    return (
        0.4 * risk["demand_risk_score"]
        + 0.3 * risk["supply_risk_score"]
        + 0.3 * risk["material_risk_score"]
    ).clip(0, 1)


def add_inventory_recommendations(
    df: pd.DataFrame,
    prediction_col: str = "predicted_usage",
    current_stock_col: str | None = None,
    lead_time_days_col: str | None = None,
    review_period_days_col: str | None = None,
    on_order_qty_col: str | None = None,
    backorder_qty_col: str | None = None,
    module_c_config: dict | None = None,
    inventory_optimization_policy: dict | None = None,
) -> pd.DataFrame:
    result = df.copy()
    if prediction_col not in result.columns:
        raise ValueError(f"Prediction column not found: {prediction_col}")

    predicted_usage = _numeric_column(result, prediction_col, 0.0, lower=0.0)
    review_period_days = _numeric_column(
        result,
        review_period_days_col,
        float(DEFAULT_REVIEW_PERIOD_DAYS),
        lower=1.0,
    )
    (
        raw_lead_time_days,
        lead_time_days,
        lead_time_fallback_applied,
        lead_time_cap_applied,
        lead_time_policy,
    ) = _resolve_lead_time(
        result,
        lead_time_days_col,
    )
    protection_period_days = review_period_days + lead_time_days
    protection_period_demand = predicted_usage * protection_period_days / 30.0
    optimization = inventory_optimization_policy or load_inventory_optimization_policy()
    critical_ratio, service_z = _cost_service_parameters(
        result.index,
        optimization,
    )
    monthly_uncertainty, uncertainty_source, protection_scale = _monthly_uncertainty(
        result,
        predicted_usage,
        protection_period_days,
        service_z,
        optimization,
    )
    safety_stock = service_z * monthly_uncertainty * protection_scale

    result["review_period_days"] = review_period_days
    result["raw_lead_time_days"] = raw_lead_time_days
    result["lead_time_days"] = lead_time_days
    result["lead_time_fallback_applied"] = lead_time_fallback_applied
    result["lead_time_cap_applied"] = lead_time_cap_applied
    result["lead_time_policy_version"] = lead_time_policy["version"]
    result["protection_period_days"] = protection_period_days
    result["protection_period_demand"] = protection_period_demand
    result["inventory_optimization_policy_version"] = optimization["version"]
    result["inventory_critical_ratio"] = critical_ratio
    result["inventory_service_z"] = service_z
    result["monthly_demand_uncertainty"] = monthly_uncertainty
    result["demand_uncertainty_source"] = uncertainty_source
    result["safety_stock"] = safety_stock
    result["base_stock"] = protection_period_demand + result["safety_stock"]

    if MODULE_C_POLICY_COLUMNS.issubset(result.columns):
        config = module_c_config or load_module_c_config()
        adjustment = config["inventory_adjustment"]
        operational_adjustment_enabled = bool(
            adjustment.get("operational_adjustment_enabled", False)
        )
        demand_risk = _numeric_column(
            result, "module_c_demand_risk", 0.0, lower=0.0, upper=1.0
        )
        supply_risk = _numeric_column(
            result, "module_c_supply_risk", 0.0, lower=0.0, upper=1.0
        )
        total_risk = _numeric_column(
            result, "module_c_total_risk", 0.0, lower=0.0, upper=1.0
        )
        result["demand_risk_score"] = demand_risk
        result["supply_risk_score"] = supply_risk
        result["material_risk_score"] = _numeric_column(
            result,
            "module_c_market_price_risk",
            0.0,
            lower=0.0,
            upper=1.0,
        )
        result["trade_risk_score"] = _numeric_column(
            result,
            "module_c_trade_risk",
            0.0,
            lower=0.0,
            upper=1.0,
        )
        result["external_risk_score"] = total_risk

        demand_embedded = _boolean_column(
            result,
            "external_demand_signal_in_forecast",
        )
        policy_demand_risk = demand_risk.where(~demand_embedded, 0.0)
        result["module_c_demand_embedded_in_forecast"] = demand_embedded
        result["module_c_policy_demand_risk"] = policy_demand_risk
        demand_uplift = policy_demand_risk * float(
            adjustment["demand_usage_uplift_max"]
        )
        result["risk_adjusted_predicted_usage"] = predicted_usage * (
            1 + demand_uplift
        )
        lead_time_multiplier = 1 + supply_risk * float(
            adjustment["supply_lead_time_multiplier_max"]
        )
        extra_lead_time_days = supply_risk * float(
            adjustment["supply_extra_lead_time_days_max"]
        )
        result["effective_lead_time_days"] = (
            lead_time_days * lead_time_multiplier + extra_lead_time_days
        )
        result["risk_adjusted_protection_period_days"] = (
            review_period_days + result["effective_lead_time_days"]
        )
        result["risk_adjusted_protection_period_demand"] = (
            result["risk_adjusted_predicted_usage"]
            * result["risk_adjusted_protection_period_days"]
            / 30.0
        )
        dynamic_ratio, dynamic_z = _cost_service_parameters(
            result.index,
            optimization,
            supply_risk,
        )
        uncertainty_multiplier = 1 + supply_risk * float(
            optimization["uncertainty"]["supply_uncertainty_multiplier_max"]
        )
        adjusted_monthly_uncertainty = (
            monthly_uncertainty * (1 + demand_uplift) * uncertainty_multiplier
        )
        adjusted_scale = np.sqrt(
            result["risk_adjusted_protection_period_days"].clip(lower=1.0) / 30.0
        )
        result["risk_adjusted_safety_stock"] = (
            dynamic_z * adjusted_monthly_uncertainty * adjusted_scale
        )
        result["inventory_critical_ratio"] = dynamic_ratio
        result["inventory_service_z"] = dynamic_z
        result["dynamic_safety_stock_rate"] = (
            result["risk_adjusted_safety_stock"]
            / result["risk_adjusted_protection_period_demand"].replace(0, np.nan)
        ).fillna(0.0)
        result["unconstrained_target_stock"] = (
            result["risk_adjusted_protection_period_demand"]
            + result["risk_adjusted_safety_stock"]
        )

        policy_applied = demand_risk.gt(0) | supply_risk.gt(0)
        result["unconstrained_target_stock"] = result[
            "unconstrained_target_stock"
        ].where(policy_applied, result["base_stock"])

        result["demand_risk_buffer"] = result["base_stock"] * demand_uplift
        result["supply_risk_buffer"] = (
            result["unconstrained_target_stock"]
            - result["base_stock"]
            - result["demand_risk_buffer"]
        ).clip(lower=0.0)
        result["material_risk_buffer"] = 0.0
        raw_risk_buffer = (
            result["unconstrained_target_stock"] - result["base_stock"]
        ).clip(lower=0.0)
        risk_buffer_cap = protection_period_demand * float(
            adjustment["total_risk_buffer_rate_cap"]
        )
        result["risk_buffer"] = pd.concat(
            [raw_risk_buffer, risk_buffer_cap], axis=1
        ).min(axis=1)
        scale = (
            result["risk_buffer"] / raw_risk_buffer.replace(0, np.nan)
        ).fillna(1.0).clip(upper=1.0)
        result["demand_risk_buffer"] *= scale
        result["supply_risk_buffer"] *= scale
        result["target_stock"] = result["base_stock"] + result["risk_buffer"]
        result["shadow_risk_adjusted_predicted_usage"] = result[
            "risk_adjusted_predicted_usage"
        ]
        result["shadow_effective_lead_time_days"] = result[
            "effective_lead_time_days"
        ]
        result["shadow_risk_adjusted_safety_stock"] = result[
            "risk_adjusted_safety_stock"
        ]
        result["shadow_risk_target_stock"] = result["target_stock"]
        result["shadow_risk_buffer"] = result["risk_buffer"]
        result["module_c_operational_adjustment_enabled"] = (
            operational_adjustment_enabled
        )
        result["module_c_policy_block_reason"] = ""
        if not operational_adjustment_enabled:
            result["risk_adjusted_predicted_usage"] = predicted_usage
            result["effective_lead_time_days"] = lead_time_days
            result["risk_adjusted_protection_period_days"] = (
                protection_period_days
            )
            result["risk_adjusted_protection_period_demand"] = (
                protection_period_demand
            )
            result["risk_adjusted_safety_stock"] = result["safety_stock"]
            result["dynamic_safety_stock_rate"] = (
                result["safety_stock"]
                / protection_period_demand.replace(0, np.nan)
            ).fillna(0.0)
            result["unconstrained_target_stock"] = result["base_stock"]
            result["demand_risk_buffer"] = 0.0
            result["supply_risk_buffer"] = 0.0
            result["material_risk_buffer"] = 0.0
            result["risk_buffer"] = 0.0
            result["target_stock"] = result["base_stock"]
            result["module_c_policy_block_reason"] = np.where(
                policy_applied,
                "shadow_only_empirical_holdout_not_passed",
                "",
            )
        result["module_c_policy_applied"] = (
            policy_applied & operational_adjustment_enabled
        )
        result["module_c_policy_demand_uplift_applied"] = (
            demand_uplift.gt(0) & operational_adjustment_enabled
        )
        result["module_c_config_version"] = config["version"]
        result["module_c_calibration_status"] = config["calibration_status"]
        # 이름이 "continuous" 였으나 실제 계산은 정기검토다. 보호기간을
        # 검토주기 + 리드타임으로 잡고 있으므로 이름을 계산에 맞춘다.
        #
        # 검토주기 자체는 상수가 아니다. ai#54 실측에서 품목별 중앙값이
        # 1~434일로 흩어지고 기본값 30일 부근(21~40일)에 드는 품목은 7.6%
        # 뿐이었다. 품목별 값은 data/mapping/review_period_by_item.csv 에
        # 있고 `review_period_days_col` 로 행 단위 주입한다. 주입이 없으면
        # DEFAULT_REVIEW_PERIOD_DAYS 로 폴백한다.
        result["inventory_policy_method"] = "cost_optimized_periodic_newsvendor"
    else:
        risk = calculate_risk_components(result)
        for column in risk.columns:
            result[column] = risk[column]
        result["external_risk_score"] = calculate_external_risk_score(result)

        result["demand_risk_buffer"] = (
            protection_period_demand
            * result["demand_risk_score"]
            * DEMAND_RISK_BUFFER_RATE
        )
        result["supply_risk_buffer"] = (
            protection_period_demand
            * result["supply_risk_score"]
            * SUPPLY_RISK_BUFFER_RATE
        )
        result["material_risk_buffer"] = (
            protection_period_demand
            * result["material_risk_score"]
            * MATERIAL_RISK_BUFFER_RATE
        )

        raw_risk_buffer = result[
            ["demand_risk_buffer", "supply_risk_buffer", "material_risk_buffer"]
        ].sum(axis=1)
        risk_buffer_cap = protection_period_demand * MAX_RISK_BUFFER_RATE
        scale = (
            risk_buffer_cap / raw_risk_buffer.replace(0, np.nan)
        ).clip(upper=1.0).fillna(1.0)
        for column in [
            "demand_risk_buffer",
            "supply_risk_buffer",
            "material_risk_buffer",
        ]:
            result[column] = result[column] * scale
        result["risk_buffer"] = result[
            ["demand_risk_buffer", "supply_risk_buffer", "material_risk_buffer"]
        ].sum(axis=1)
        result["target_stock"] = result["base_stock"] + result["risk_buffer"]
        result["risk_adjusted_predicted_usage"] = predicted_usage
        result["effective_lead_time_days"] = lead_time_days
        result["risk_adjusted_protection_period_days"] = protection_period_days
        result["risk_adjusted_protection_period_demand"] = protection_period_demand
        result["dynamic_safety_stock_rate"] = (
            result["safety_stock"]
            / protection_period_demand.replace(0, np.nan)
        ).fillna(0.0)
        result["risk_adjusted_safety_stock"] = result["safety_stock"]
        result["unconstrained_target_stock"] = result["target_stock"]
        result["module_c_policy_applied"] = False
        result["module_c_demand_embedded_in_forecast"] = False
        result["module_c_policy_demand_risk"] = 0.0
        result["module_c_policy_demand_uplift_applied"] = False
        result["module_c_config_version"] = "legacy-risk-policy"
        result["module_c_calibration_status"] = "legacy-policy"
        result["inventory_policy_method"] = "cost_optimized_with_legacy_risk_buffers"
    result["recommended_stock"] = result["target_stock"]

    if current_stock_col and current_stock_col in result.columns:
        current_stock = _numeric_column(result, current_stock_col, 0.0, lower=0.0)
        on_order_qty = _numeric_column(result, on_order_qty_col, 0.0, lower=0.0)
        backorder_qty = _numeric_column(result, backorder_qty_col, 0.0, lower=0.0)
        result["current_stock"] = current_stock
        result["on_order_qty"] = on_order_qty
        result["backorder_qty"] = backorder_qty
        result["inventory_position"] = current_stock + on_order_qty - backorder_qty
        result["recommended_order"] = np.maximum(
            result["target_stock"] - result["inventory_position"],
            0,
        )
        result = apply_order_quality_gates(
            result,
            order_col="recommended_order",
            raw_order_col="raw_recommended_order",
            suppressed_col="order_recommendation_suppressed",
            reason_col="order_recommendation_suppression_reason",
        )
    return result
