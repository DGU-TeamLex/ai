"""주사기 PP 가격위험이 공급 측 재고정책에 미치는 병렬 영향을 계산한다.

원자재 신호를 사용수요 예측치에 더하지 않는다. 승인된 일회용 주사기–PP
매핑 행만 골라 PP 가격위험을 Module C의 공급위험 경로로 변환하고, 30일
리드타임 기준 유효 리드타임·안전재고·목표재고·제안발주량의 병렬 값을
계산한다. 운영 반영은 비활성화된 상태를 유지한다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.modeling.inventory_policy import add_inventory_recommendations


DEFAULT_INPUT = ROOT / "outputs" / "stock_backtest_predictions.csv"
DEFAULT_REPORT = ROOT / "outputs" / "syringe_supply_risk_inventory_impact.csv"
DEFAULT_SUMMARY = ROOT / "outputs" / "syringe_supply_risk_inventory_summary.json"
RISK_CONFIG = ROOT / "data" / "mapping" / "module_c_risk_weights.json"
TARGET_FAMILY = "DISPOSABLE_SYRINGE"
ANALYSIS_LEAD_TIME_DAYS = 30.0
INPUT_COLUMNS = [
    "standard_item_family_id",
    "has_approved_material_mapping",
    "year_month",
    "stock_item_key",
    "predicted_usage",
    "inventory_position",
    "review_period_days",
    "rolling_std_3",
    "rolling_std_6",
    "rolling_std_12",
    "module_c_market_price_risk",
]


def _approved(values: pd.Series) -> pd.Series:
    return (
        values.astype("string")
        .fillna("")
        .str.strip()
        .str.lower()
        .isin({"true", "t", "1", "yes", "y"})
    )


def pp_supply_risk(
    market_price_risk: pd.Series,
    supply_weights: dict[str, float],
) -> pd.Series:
    """Module C의 정규화 가중최댓값 공식으로 PP 단독 공급위험을 만든다."""

    maximum_weight = max(float(value) for value in supply_weights.values())
    market_weight = float(supply_weights["market_price"])
    market = pd.to_numeric(market_price_risk, errors="coerce").fillna(0.0)
    return (market.clip(0.0, 1.0) * market_weight / maximum_weight).clip(0.0, 1.0)


def prepare_supply_scenario(
    frame: pd.DataFrame,
    config: dict,
    lead_time_days: float = ANALYSIS_LEAD_TIME_DAYS,
) -> pd.DataFrame:
    """승인 주사기 행에 PP 공급위험만 주입해 병렬 재고정책을 계산한다."""

    missing = sorted(set(INPUT_COLUMNS).difference(frame.columns))
    if missing:
        raise ValueError("필수 입력 열이 없습니다: " + ", ".join(missing))

    selected = frame[
        frame["standard_item_family_id"].eq(TARGET_FAMILY)
        & _approved(frame["has_approved_material_mapping"])
    ].copy()
    if selected.empty:
        raise ValueError("승인된 일회용 주사기–PP 분석 행이 없습니다")

    selected["analysis_lead_time_days"] = float(lead_time_days)
    selected["module_c_demand_risk"] = 0.0
    selected["module_c_supply_risk"] = pp_supply_risk(
        selected["module_c_market_price_risk"], config["supply_signal"]
    )
    selected["module_c_total_risk"] = selected["module_c_supply_risk"]
    selected["external_demand_signal_in_forecast"] = True

    result = add_inventory_recommendations(
        selected,
        prediction_col="predicted_usage",
        lead_time_days_col="analysis_lead_time_days",
        review_period_days_col="review_period_days",
        module_c_config=config,
    )
    inventory_position = pd.to_numeric(
        result["inventory_position"], errors="coerce"
    ).fillna(0.0)
    result["baseline_recommended_order"] = np.maximum(
        result["base_stock"] - inventory_position, 0.0
    )
    result["shadow_recommended_order"] = np.maximum(
        result["shadow_risk_target_stock"] - inventory_position, 0.0
    )
    return result


def _sum_change(frame: pd.DataFrame, baseline: str, shadow: str) -> dict[str, float]:
    baseline_total = float(frame[baseline].sum())
    shadow_total = float(frame[shadow].sum())
    delta = shadow_total - baseline_total
    return {
        "baseline_total": baseline_total,
        "shadow_total": shadow_total,
        "delta": delta,
        "delta_pct": (delta / baseline_total * 100.0) if baseline_total else 0.0,
    }


def summarize_supply_scenario(frame: pd.DataFrame, config: dict) -> dict:
    adjustment = config["inventory_adjustment"]
    safety = _sum_change(
        frame, "safety_stock", "shadow_risk_adjusted_safety_stock"
    )
    target = _sum_change(frame, "base_stock", "shadow_risk_target_stock")
    order = _sum_change(
        frame, "baseline_recommended_order", "shadow_recommended_order"
    )
    return {
        "scope": {
            "family": TARGET_FAMILY,
            "material": "POLYPROPYLENE_PP",
            "rows": int(len(frame)),
            "stock_items": int(frame["stock_item_key"].nunique()),
            "months": int(frame["year_month"].nunique()),
            "period_start": str(frame["year_month"].min()),
            "period_end": str(frame["year_month"].max()),
            "base_lead_time_days": ANALYSIS_LEAD_TIME_DAYS,
        },
        "risk": {
            "market_price_risk_mean": float(frame["module_c_market_price_risk"].mean()),
            "market_price_risk_min": float(frame["module_c_market_price_risk"].min()),
            "market_price_risk_max": float(frame["module_c_market_price_risk"].max()),
            "pp_supply_risk_mean": float(frame["module_c_supply_risk"].mean()),
            "market_price_weight": float(config["supply_signal"]["market_price"]),
            "normalization_weight": float(max(config["supply_signal"].values())),
        },
        "lead_time": {
            "baseline_mean_days": float(frame["lead_time_days"].mean()),
            "shadow_mean_days": float(frame["shadow_effective_lead_time_days"].mean()),
            "mean_increase_days": float(
                (frame["shadow_effective_lead_time_days"] - frame["lead_time_days"]).mean()
            ),
            "shadow_max_days": float(frame["shadow_effective_lead_time_days"].max()),
            "formula": (
                "L_eff = 30*(1 + supply_risk*"
                f"{adjustment['supply_lead_time_multiplier_max']}) + "
                f"supply_risk*{adjustment['supply_extra_lead_time_days_max']}"
            ),
        },
        "safety_stock": safety,
        "target_stock": target,
        "recommended_order": {
            **order,
            "rows_increased": int(
                (frame["shadow_recommended_order"]
                 > frame["baseline_recommended_order"] + 1e-12).sum()
            ),
        },
        "policy": {
            "operational_adjustment_enabled": bool(
                adjustment["operational_adjustment_enabled"]
            ),
            "interpretation": (
                "PP 가격·변동성 상승을 사용수요 증가로 해석하지 않고, "
                "공급차질 가능성에 대비한 리드타임·안전재고 병렬 신호로 사용"
            ),
            "limitation": (
                "구매단가·실제 입고일·결품 원인 자료가 없어 인과효과나 실제 "
                "품절 감소를 검증한 결과가 아니라 정책 민감도 시나리오임"
            ),
        },
    }


def monthly_report(frame: pd.DataFrame) -> pd.DataFrame:
    result = (
        frame.groupby("year_month", dropna=False)
        .agg(
            rows=("stock_item_key", "size"),
            stock_items=("stock_item_key", "nunique"),
            pp_market_price_risk=("module_c_market_price_risk", "mean"),
            pp_supply_risk=("module_c_supply_risk", "mean"),
            baseline_lead_time_days=("lead_time_days", "mean"),
            shadow_effective_lead_time_days=("shadow_effective_lead_time_days", "mean"),
            baseline_safety_stock=("safety_stock", "sum"),
            shadow_safety_stock=("shadow_risk_adjusted_safety_stock", "sum"),
            baseline_target_stock=("base_stock", "sum"),
            shadow_target_stock=("shadow_risk_target_stock", "sum"),
            baseline_recommended_order=("baseline_recommended_order", "sum"),
            shadow_recommended_order=("shadow_recommended_order", "sum"),
        )
        .reset_index()
    )
    result["lead_time_increase_days"] = (
        result["shadow_effective_lead_time_days"] - result["baseline_lead_time_days"]
    )
    result["safety_stock_increase_pct"] = (
        (result["shadow_safety_stock"] - result["baseline_safety_stock"])
        / result["baseline_safety_stock"].replace(0.0, np.nan) * 100.0
    ).fillna(0.0)
    result["target_stock_increase_pct"] = (
        (result["shadow_target_stock"] - result["baseline_target_stock"])
        / result["baseline_target_stock"].replace(0.0, np.nan) * 100.0
    ).fillna(0.0)
    return result


def run(
    input_path: Path = DEFAULT_INPUT,
    report_path: Path = DEFAULT_REPORT,
    summary_path: Path = DEFAULT_SUMMARY,
) -> tuple[pd.DataFrame, dict]:
    with RISK_CONFIG.open("r", encoding="utf-8") as stream:
        config = json.load(stream)
    source = pd.read_csv(input_path, usecols=INPUT_COLUMNS)
    scenario = prepare_supply_scenario(source, config)
    report = monthly_report(scenario)
    summary = summarize_supply_scenario(scenario, config)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(report_path, index=False, encoding="utf-8-sig")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()
    _, summary = run(args.input, args.report, args.summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
