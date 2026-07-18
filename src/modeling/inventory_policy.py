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
    scale = (risk_buffer_cap / raw_risk_buffer.replace(0, np.nan)).clip(upper=1.0).fillna(1.0)
    for column in ["demand_risk_buffer", "supply_risk_buffer", "material_risk_buffer"]:
        result[column] = result[column] * scale
    result["risk_buffer"] = result[
        ["demand_risk_buffer", "supply_risk_buffer", "material_risk_buffer"]
    ].sum(axis=1)
    result["target_stock"] = result["base_stock"] + result["risk_buffer"]
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
