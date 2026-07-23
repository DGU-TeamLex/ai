import numpy as np
import pandas as pd

from ..config import (
    DEFAULT_LEAD_TIME_DAYS,
    DEFAULT_REVIEW_PERIOD_DAYS,
    DEMAND_RISK_BUFFER_RATE,
    MATERIAL_RISK_BUFFER_RATE,
    MAX_RISK_BUFFER_RATE,
    SAFETY_STOCK_RATE,
    SUPPLY_RISK_BUFFER_RATE,
)
from ..module_c.config import load_module_c_config


MODULE_C_POLICY_COLUMNS = {
    "module_c_demand_risk",
    "module_c_supply_risk",
    "module_c_total_risk",
}


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
    lead_time_days = _numeric_column(
        result,
        lead_time_days_col,
        float(DEFAULT_LEAD_TIME_DAYS),
        lower=0.0,
    )
    protection_period_days = review_period_days + lead_time_days
    protection_period_demand = predicted_usage * protection_period_days / 30.0

    result["review_period_days"] = review_period_days
    result["lead_time_days"] = lead_time_days
    result["protection_period_days"] = protection_period_days
    result["protection_period_demand"] = protection_period_demand
    result["safety_stock"] = protection_period_demand * SAFETY_STOCK_RATE
    result["base_stock"] = protection_period_demand + result["safety_stock"]

    if MODULE_C_POLICY_COLUMNS.issubset(result.columns):
        config = module_c_config or load_module_c_config()
        adjustment = config["inventory_adjustment"]
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
        result["dynamic_safety_stock_rate"] = (
            SAFETY_STOCK_RATE
            + supply_risk * float(adjustment["safety_stock_rate_uplift_max"])
        )
        result["risk_adjusted_safety_stock"] = (
            result["risk_adjusted_protection_period_demand"]
            * result["dynamic_safety_stock_rate"]
        )
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
        result["module_c_policy_applied"] = policy_applied
        result["module_c_policy_demand_uplift_applied"] = demand_uplift.gt(0)
        result["module_c_config_version"] = config["version"]
        result["module_c_calibration_status"] = config["calibration_status"]
        result["inventory_policy_method"] = "module_c_continuous_target_stock"
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
        result["dynamic_safety_stock_rate"] = SAFETY_STOCK_RATE
        result["risk_adjusted_safety_stock"] = result["safety_stock"]
        result["unconstrained_target_stock"] = result["target_stock"]
        result["module_c_policy_applied"] = False
        result["module_c_demand_embedded_in_forecast"] = False
        result["module_c_policy_demand_risk"] = 0.0
        result["module_c_policy_demand_uplift_applied"] = False
        result["module_c_config_version"] = "legacy-risk-policy"
        result["module_c_calibration_status"] = "legacy-policy"
        result["inventory_policy_method"] = "legacy_fixed_rate_target_stock"
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
    return result
