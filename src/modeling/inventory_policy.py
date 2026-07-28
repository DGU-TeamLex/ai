import numpy as np
import pandas as pd

from ..config import MAX_RISK_BUFFER_RATE, SAFETY_STOCK_RATE


def calculate_external_risk_score(df: pd.DataFrame) -> pd.Series:
    disease = df.get("disease_news_risk", 0)
    supply = df.get("supply_news_risk", 0)
    commodity = df.get("commodity_risk", 0)
    return (0.4 * disease + 0.3 * supply + 0.3 * commodity).clip(0, 1)


def add_inventory_recommendations(
    df: pd.DataFrame,
    prediction_col: str = "predicted_usage",
    current_stock_col: str | None = None,
    lead_time_days_col: str | None = None,
) -> pd.DataFrame:
    result = df.copy()
    result["external_risk_score"] = calculate_external_risk_score(result)
    result["safety_stock"] = result[prediction_col].clip(lower=0) * SAFETY_STOCK_RATE
    risk_buffer_rate = (result["external_risk_score"] * MAX_RISK_BUFFER_RATE).clip(0, MAX_RISK_BUFFER_RATE)
    result["risk_buffer"] = result[prediction_col].clip(lower=0) * risk_buffer_rate
    result["recommended_stock"] = result[prediction_col].clip(lower=0) + result["safety_stock"] + result["risk_buffer"]

    if current_stock_col and current_stock_col in result.columns:
        lead_time_buffer = 0
        if lead_time_days_col and lead_time_days_col in result.columns:
            lead_time_buffer = result[prediction_col].clip(lower=0) * result[lead_time_days_col].fillna(0) / 30
        result["recommended_order"] = np.maximum(
            result["recommended_stock"] - result[current_stock_col].fillna(0) + lead_time_buffer,
            0,
        )
    return result

