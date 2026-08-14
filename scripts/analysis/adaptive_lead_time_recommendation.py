"""현재 재고 상황 + 외부 상황 → 품목별 권장 리드타임·발주량 (ai#20).

## 무엇을 만드나

이슈 #20 "공급위험↑ 시 z·LT 동적 상향" 의 산출물이다. 담당자가 보는 것은
"지금 이 품목은 리드타임 며칠로 잡고 얼마를 발주해야 하는가" 다.

    현재 재고 + 외부 공급위험  →  권장 리드타임  →  보호기간  →  발주량

## 왜 수요예측 경로가 아니라 여기인가

외부신호를 수요예측 피처로 쓰는 경로는 실측으로 기여가 없었다.
뉴스·원자재·기관정보 3종 모두 4-fold WAPE 증분이 ±0.06%p 안이고 부호도
일관되지 않는다.

반면 #72 의 PP 실측은 관계가 **소비량이 아니라 마감재고** 에서 나왔다.
가격 충격이 수요를 바꾸는 것이 아니라 조달을 막아 재고를 깎는다는 뜻이므로,
외부신호는 리드타임·안전재고 경로에 붙는 것이 맞다.

## 계산

기준 리드타임은 조달청 실측(발주→납기)을 쓴다. 원장 품절지속(M)은 발주를
안 한 휴면 구간이 섞여 분산이 5배 부풀려지므로 쓰지 않는다.

    L_eff = L̄ · (1 + risk · multiplier_max) + risk · extra_days_max
    보호기간 = 검토주기(30일) + L_eff
    SS      = z · sqrt(L_eff·σ_d² + d̄²·σ_L²)     ← 리드타임 분산 반영
    목표재고 = 보호기간수요 + SS
    발주량   = max(0, 목표재고 − 현재고)

계수는 `module_c_risk_weights.json` 캘리브레이션 값을 쓴다.

실행:
    python scripts/analysis/adaptive_lead_time_recommendation.py
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

REVIEW_PERIOD_DAYS = 30.0
SERVICE_Z = 1.2816  # 90%
CURRENT_SAFETY_RATE = 0.20
CURRENT_FALLBACK_LEAD_DAYS = 15.0

OUT_CSV = ROOT / "outputs" / "adaptive_lead_time_recommendation.csv"
OUT_JSON = ROOT / "outputs" / "adaptive_lead_time_summary.json"


def procurement_lead_time() -> tuple[float, float]:
    frame = pd.read_json(
        ROOT / "data" / "processed" / "procurement_delivery_requests.jsonl", lines=True
    ).replace("--", None)
    for column in ("dlvrReqRcptDate", "maxDlvrTmlmtDate"):
        frame[column] = pd.to_datetime(frame[column], errors="coerce")
    lead = (frame["maxDlvrTmlmtDate"] - frame["dlvrReqRcptDate"]).dt.days
    lead = lead[lead.between(0, 365)]
    return float(lead.mean()), float(lead.std())


def main() -> None:
    weights = json.loads(
        (ROOT / "data" / "mapping" / "module_c_risk_weights.json").read_text(
            encoding="utf-8"
        )
    )["inventory_adjustment"]
    multiplier_max = float(weights["supply_lead_time_multiplier_max"])
    extra_days_max = float(weights["supply_extra_lead_time_days_max"])

    lead_mean, lead_sd = procurement_lead_time()
    print(f"기준 리드타임(조달청 실측): 평균 {lead_mean:.1f}일, 표준편차 {lead_sd:.1f}일")

    columns = [
        "stock_item_key", "institution_code", "item_name", "year_month",
        "month_end_stock", "consumption_qty", "module_c_supply_risk",
        "module_c_total_risk", "data_period",
    ]
    table = pd.read_parquet(ROOT / "outputs" / "stock_feature_table.parquet", columns=columns)
    table = table[table["data_period"].astype(str).eq("current")]

    # 수요 모수는 이력 전체로, 재고·위험은 최신 스냅샷으로.
    moments = (
        table.groupby("stock_item_key")["consumption_qty"]
        .agg(demand_mean="mean", demand_sd="std", months="count")
    )
    moments = moments[(moments["months"] >= 6) & (moments["demand_mean"] > 0)].dropna()

    latest_month = table["year_month"].max()
    snapshot = table[table["year_month"] == latest_month].set_index("stock_item_key")
    snapshot = snapshot.join(moments, how="inner")
    print(f"최신 스냅샷 {latest_month:%Y-%m} · 대상 {len(snapshot):,}품목\n")

    risk = snapshot["module_c_supply_risk"].clip(0, 1)
    print("공급위험 분포:")
    print(f"  중앙값 {risk.median():.4f} / p90 {risk.quantile(0.9):.4f} / "
          f"최대 {risk.max():.4f} / 비영 {100 * (risk > 0).mean():.1f}%")
    if risk.max() < 0.1:
        print(
            "  ⚠️ 위험 점수가 0~1 척도에서 최대 "
            f"{risk.max():.3f} 에 그친다. 이 상태로는 리드타임 조정폭이 "
            f"최대 {lead_mean * multiplier_max * risk.max() + extra_days_max * risk.max():.1f}일에 "
            "불과하다. 점수 스케일 재검토가 필요하다(ai#20)."
        )

    effective_lead = lead_mean * (1 + risk * multiplier_max) + risk * extra_days_max
    effective_sd = lead_sd * (1 + risk * multiplier_max)
    protection_days = REVIEW_PERIOD_DAYS + effective_lead

    daily_mean = snapshot["demand_mean"] / 30.0
    daily_sd = snapshot["demand_sd"] / np.sqrt(30.0)
    protection_demand = daily_mean * protection_days
    safety_stock = SERVICE_Z * np.sqrt(
        effective_lead * daily_sd**2 + (daily_mean**2) * (effective_sd**2)
    )
    target = protection_demand + safety_stock
    on_hand = snapshot["month_end_stock"].clip(lower=0)
    order = (target - on_hand).clip(lower=0)

    # 현행 정책과의 대조군
    current_protection = REVIEW_PERIOD_DAYS + CURRENT_FALLBACK_LEAD_DAYS
    current_demand = daily_mean * current_protection
    current_target = current_demand * (1 + CURRENT_SAFETY_RATE)
    current_order = (current_target - on_hand).clip(lower=0)

    result = pd.DataFrame(
        {
            "institution_code": snapshot["institution_code"].astype(str),
            "item_name": snapshot["item_name"].astype(str),
            "on_hand": on_hand.round(1),
            "monthly_demand_mean": snapshot["demand_mean"].round(2),
            "supply_risk": risk.round(4),
            "recommended_lead_time_days": effective_lead.round(1),
            "protection_period_days": protection_days.round(1),
            "safety_stock": safety_stock.round(1),
            "target_stock": target.round(1),
            "recommended_order": order.round(1),
            "current_policy_lead_days": CURRENT_FALLBACK_LEAD_DAYS,
            "current_policy_order": current_order.round(1),
        }
    ).sort_values("recommended_order", ascending=False)

    result.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    print(f"\n=== 리드타임 권고 ===")
    print(f"  현행 정책        {CURRENT_FALLBACK_LEAD_DAYS:.0f}일 고정")
    print(f"  권장(위험 0)     {effective_lead.min():.1f}일")
    print(f"  권장(중앙값)     {effective_lead.median():.1f}일")
    print(f"  권장(최대위험)   {effective_lead.max():.1f}일")

    print(f"\n=== 발주량 (전 품목 합) ===")
    print(f"  현행 정책  {current_order.sum():>15,.0f}")
    print(f"  권장       {order.sum():>15,.0f}   ({order.sum() / current_order.sum():.2f}x)")
    print(f"  발주 필요 품목  현행 {int((current_order > 0).sum()):,} → 권장 {int((order > 0).sum()):,}")

    print("\n=== 상위 5품목 (권장 발주량 기준) ===")
    for row in result.head(5).itertuples():
        print(
            f"  {row.item_name[:26]:<26} 재고 {row.on_hand:>9,.0f} "
            f"위험 {row.supply_risk:.3f} LT {row.recommended_lead_time_days:>5.1f}일 "
            f"발주 {row.recommended_order:>10,.0f}"
        )

    summary = {
        "snapshot_month": f"{latest_month:%Y-%m}",
        "items": int(len(result)),
        "base_lead_time": {"mean_days": round(lead_mean, 1), "sd_days": round(lead_sd, 1),
                           "source": "procurement_contract_2024_01_06"},
        "coefficients": {"supply_lead_time_multiplier_max": multiplier_max,
                         "supply_extra_lead_time_days_max": extra_days_max},
        "supply_risk": {"median": float(risk.median()), "p90": float(risk.quantile(0.9)),
                        "max": float(risk.max()), "nonzero_share": float((risk > 0).mean())},
        "recommended_lead_time_days": {"min": float(effective_lead.min()),
                                       "median": float(effective_lead.median()),
                                       "max": float(effective_lead.max())},
        "order_quantity": {"current_policy": float(current_order.sum()),
                           "recommended": float(order.sum()),
                           "ratio": float(order.sum() / current_order.sum())},
    }
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {OUT_CSV}\n      {OUT_JSON}")


if __name__ == "__main__":
    main()
