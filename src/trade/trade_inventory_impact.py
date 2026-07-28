from __future__ import annotations

import logging

import pandas as pd

from ..config import (
    PREDICTION_PATH,
    TRADE_INVENTORY_IMPACT_REPORT_PATH,
    TRADE_INVENTORY_IMPACT_SAMPLE_PATH,
)
from ..modeling.inventory_policy import add_inventory_recommendations
from ..module_c.config import load_module_c_config
from ..utils import ensure_dirs, setup_logging, write_json


LOGGER = logging.getLogger(__name__)
REPORT_VERSION = "trade-inventory-impact-v1.0"
REQUIRED_COLUMNS = {
    "stock_item_key",
    "predicted_usage",
    "current_stock",
    "target_stock",
    "risk_adjusted_safety_stock",
    "risk_buffer",
    "recommended_order",
    "module_c_demand_risk",
    "module_c_supply_news_risk",
    "module_c_material_news_risk",
    "module_c_market_price_risk",
    "module_c_trade_risk",
    "module_c_supply_risk",
    "module_c_total_risk",
}


def build_trade_inventory_impact(
    predictions: pd.DataFrame,
) -> tuple[dict[str, object], pd.DataFrame]:
    missing = sorted(REQUIRED_COLUMNS - set(predictions.columns))
    if missing:
        raise ValueError(
            f"Trade inventory impact input is missing columns: {missing}"
        )
    config = load_module_c_config()
    supply_weights = config["supply_signal"]
    current = predictions.copy()
    base_supply_risk = (
        float(supply_weights["supply_news"])
        * pd.to_numeric(
            current["module_c_supply_news_risk"],
            errors="coerce",
        ).fillna(0.0)
        + float(supply_weights["material_news"])
        * pd.to_numeric(
            current["module_c_material_news_risk"],
            errors="coerce",
        ).fillna(0.0)
        + float(supply_weights["market_price"])
        * pd.to_numeric(
            current["module_c_market_price_risk"],
            errors="coerce",
        ).fillna(0.0)
    ).clip(0, 1)

    no_trade = current.copy()
    no_trade["module_c_trade_risk"] = 0.0
    no_trade["module_c_supply_risk"] = base_supply_risk
    no_trade["module_c_total_risk"] = pd.concat(
        [
            pd.to_numeric(
                no_trade["module_c_demand_risk"],
                errors="coerce",
            ).fillna(0.0),
            base_supply_risk,
        ],
        axis=1,
    ).max(axis=1)
    no_trade = add_inventory_recommendations(
        no_trade,
        prediction_col="predicted_usage",
        current_stock_col="current_stock",
        module_c_config=config,
    )

    impact = current.copy()
    impact["counterfactual_supply_risk_without_trade"] = base_supply_risk
    for actual_column, counterfactual_column, impact_column in [
        (
            "target_stock",
            "counterfactual_target_stock",
            "trade_attributable_target_stock",
        ),
        (
            "risk_adjusted_safety_stock",
            "counterfactual_safety_stock",
            "trade_attributable_safety_stock",
        ),
        (
            "risk_buffer",
            "counterfactual_risk_buffer",
            "trade_attributable_risk_buffer",
        ),
        (
            "recommended_order",
            "counterfactual_recommended_order",
            "trade_attributable_recommended_order",
        ),
    ]:
        impact[counterfactual_column] = pd.to_numeric(
            no_trade[actual_column],
            errors="coerce",
        ).fillna(0.0)
        impact[impact_column] = (
            pd.to_numeric(
                impact[actual_column],
                errors="coerce",
            ).fillna(0.0)
            - impact[counterfactual_column]
        ).clip(lower=0.0)

    trade_risk = pd.to_numeric(
        impact["module_c_trade_risk"],
        errors="coerce",
    ).fillna(0.0)
    affected = impact[trade_risk.gt(0)].copy()
    target_delta = affected["trade_attributable_target_stock"]
    order_delta = affected["trade_attributable_recommended_order"]
    sample_columns = [
        column
        for column in [
            "forecast_origin_month",
            "year_month",
            "institution_code",
            "department",
            "item_code",
            "item_name",
            "stock_item_key",
            "approved_raw_material_meta_codes",
            "module_c_trade_risk",
            "module_c_supply_risk",
            "counterfactual_supply_risk_without_trade",
            "predicted_usage",
            "current_stock",
            "target_stock",
            "counterfactual_target_stock",
            "trade_attributable_target_stock",
            "risk_adjusted_safety_stock",
            "counterfactual_safety_stock",
            "trade_attributable_safety_stock",
            "recommended_order",
            "counterfactual_recommended_order",
            "trade_attributable_recommended_order",
        ]
        if column in affected.columns
    ]
    sample = (
        affected.sort_values(
            [
                "trade_attributable_target_stock",
                "module_c_trade_risk",
                "stock_item_key",
            ],
            ascending=[False, False, True],
        )
        .head(1000)[sample_columns]
        .reset_index(drop=True)
    )
    report = {
        "report_version": REPORT_VERSION,
        "module_c_config_version": config["version"],
        "calibration_status": config["calibration_status"],
        "comparison": "current_module_c_v1_2_vs_same_policy_with_trade_signal_zero",
        "forecast_rows": int(len(impact)),
        "trade_exposed_forecast_rows": int(len(affected)),
        "trade_exposed_stock_items": int(
            affected["stock_item_key"].nunique()
        ),
        "trade_risk_mean_exposed": (
            float(trade_risk.loc[affected.index].mean())
            if not affected.empty
            else 0.0
        ),
        "trade_risk_max": float(trade_risk.max()) if len(impact) else 0.0,
        "target_stock_increased_rows": int(target_delta.gt(1e-9).sum()),
        "recommended_order_increased_rows": int(order_delta.gt(1e-9).sum()),
        "trade_attributable_target_stock_mixed_unit_sum": float(
            target_delta.sum()
        ),
        "trade_attributable_safety_stock_mixed_unit_sum": float(
            affected["trade_attributable_safety_stock"].sum()
        ),
        "trade_attributable_recommended_order_mixed_unit_sum": float(
            order_delta.sum()
        ),
        "target_stock_delta_p50": (
            float(target_delta.quantile(0.50)) if not affected.empty else 0.0
        ),
        "target_stock_delta_p95": (
            float(target_delta.quantile(0.95)) if not affected.empty else 0.0
        ),
        "target_stock_delta_max": (
            float(target_delta.max()) if not affected.empty else 0.0
        ),
        "mixed_unit_sum_caution": (
            "품목별 단위가 다르므로 합계는 방향성 확인용이며 물량 총계로 해석하지 않는다."
        ),
        "sample_rows": int(len(sample)),
        "sample_path": str(TRADE_INVENTORY_IMPACT_SAMPLE_PATH),
    }
    return report, sample


def write_trade_inventory_impact(
    predictions: pd.DataFrame,
) -> dict[str, object]:
    ensure_dirs(
        TRADE_INVENTORY_IMPACT_REPORT_PATH.parent,
        TRADE_INVENTORY_IMPACT_SAMPLE_PATH.parent,
    )
    report, sample = build_trade_inventory_impact(predictions)
    write_json(report, TRADE_INVENTORY_IMPACT_REPORT_PATH)
    sample.to_csv(
        TRADE_INVENTORY_IMPACT_SAMPLE_PATH,
        index=False,
        encoding="utf-8-sig",
    )
    LOGGER.info(
        "Saved trade inventory impact report: %s",
        TRADE_INVENTORY_IMPACT_REPORT_PATH,
    )
    return report


def run_trade_inventory_impact() -> dict[str, object]:
    setup_logging()
    if not PREDICTION_PATH.exists():
        raise FileNotFoundError(
            f"Run prediction before trade impact analysis: {PREDICTION_PATH}"
        )
    predictions = pd.read_csv(PREDICTION_PATH, low_memory=False)
    return write_trade_inventory_impact(predictions)


if __name__ == "__main__":
    print(run_trade_inventory_impact())
