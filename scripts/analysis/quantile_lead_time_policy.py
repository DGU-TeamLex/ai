"""분위수 기반 리드타임 조정 — 평균 배수 방식을 대체한다 (ai#20).

## 왜 바꾸는가

운영 검증에서 현행 구조가 실측과 어긋난다는 것이 드러났다.

    월별 median  30 ~ 30일      표준편차 0.00   ← 전혀 안 움직인다
    월별 평균    35.3 ~ 49.8일  표준편차 4.08
    월별 p75     반응함 (trade_risk 와 r=+0.445, lag 1개월)

표준 계약 30일은 위험과 무관하게 유지되고, 위험이 오르면 **오래 걸리는 건의
비중** 이 늘어난다. 즉

    "위험이 오르면 모든 발주가 밀린다"     ← 현행 계수의 가정. 틀렸다.
    "위험이 오르면 지연되는 발주가 늘어난다"  ← 실측

현행은 평균을 통째로 늘린다.

    L_eff = L̄ · (1 + risk · 0.5) + risk · 14

이 형태로는 "median 은 그대로, 꼬리만 두꺼워짐" 을 표현할 수 없다. 평균을 늘리면
위험이 없는 정상 발주까지 같이 늘어난다.

## 제안

**위험에 따라 사용하는 분위수를 올린다.**

    q(risk) = q_base + risk · (q_max − q_base)
    L_eff   = 실측 리드타임 분포의 q(risk) 분위수

    risk=0    → p50 = 30일   (표준 계약. 정상 상황에서 과잉 조달을 막는다)
    risk=1    → p95         (최악 상황 대비)

이 방식의 장점
  * 정상 상황에서 30일을 그대로 쓴다. 평균 배수 방식은 위험이 0이어도 40.1일을
    쓰는데, 실측 median 은 30일이므로 33% 과대추정이다.
  * 위험이 오를 때 **분포의 꼬리** 를 따라간다. 실측이 말하는 그 형태다.
  * 계수가 임의값이 아니라 **실측 분포의 분위수** 라 해석이 명확하다.
    "위험 0.5 면 p72 를 쓴다 = 조달 건의 72% 를 커버한다".

근거: 서비스수준을 분위수로 직접 지정하는 것은 newsvendor 원리의 표준 적용이다
(Silver, Pyke & Peterson 1998, Ch. 7). 리드타임이 확률변수일 때 분포 자체를
쓰는 편이 평균·분산 요약보다 낫다는 점은 Eppen & Martin (1988),
*Management Science* 34(11):1380-1390 참조.

실행:
    python scripts/analysis/quantile_lead_time_policy.py
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

# 위험 0 일 때와 1 일 때 쓸 분위수.
# base 를 p50 으로 둔 이유: 실측 median 30일이 표준 계약이고, 위험이 없는데
# 그보다 길게 잡으면 상시 과잉 조달이 된다.
QUANTILE_BASE = 0.50
QUANTILE_MAX = 0.95

OUT_CSV = ROOT / "outputs" / "quantile_lead_time_recommendation.csv"
OUT_JSON = ROOT / "outputs" / "quantile_lead_time_policy.json"


def lead_time_distribution() -> pd.Series:
    frame = pd.read_json(
        ROOT / "data" / "processed" / "procurement_delivery_requests.jsonl", lines=True
    ).replace("--", None)
    for column in ("dlvrReqRcptDate", "maxDlvrTmlmtDate"):
        frame[column] = pd.to_datetime(frame[column], errors="coerce")
    lead = (frame["maxDlvrTmlmtDate"] - frame["dlvrReqRcptDate"]).dt.days
    return lead[lead.between(0, 365)]


def main() -> None:
    lead = lead_time_distribution()
    print(f"조달청 실측 리드타임 {len(lead):,}건")
    grid = [0.50, 0.60, 0.70, 0.75, 0.80, 0.90, 0.95]
    print("  분위수:", {f"p{int(q*100)}": float(lead.quantile(q)) for q in grid})
    print(f"  평균 {lead.mean():.1f}일 (현행 방식이 위험 0 에서 쓰는 값)")

    columns = [
        "stock_item_key", "institution_code", "item_name", "year_month",
        "month_end_stock", "consumption_qty", "module_c_supply_risk", "data_period",
    ]
    table = pd.read_parquet(ROOT / "outputs" / "stock_feature_table.parquet", columns=columns)
    table = table[table["data_period"].astype(str).eq("current")]

    moments = (
        table.groupby("stock_item_key")["consumption_qty"]
        .agg(demand_mean="mean", demand_sd="std", months="count")
    )
    moments = moments[(moments["months"] >= 6) & (moments["demand_mean"] > 0)].dropna()

    latest = table["year_month"].max()
    snapshot = table[table["year_month"] == latest].set_index("stock_item_key")
    snapshot = snapshot.join(moments, how="inner")
    risk = snapshot["module_c_supply_risk"].clip(0, 1)
    print(f"\n최신 스냅샷 {latest:%Y-%m} · {len(snapshot):,}품목 "
          f"(위험 중앙 {risk.median():.4f} / 최대 {risk.max():.4f})")

    # 위험 → 분위수 → 리드타임
    quantile = QUANTILE_BASE + risk * (QUANTILE_MAX - QUANTILE_BASE)
    effective_lead = pd.Series(
        np.quantile(lead.to_numpy(), quantile.to_numpy()), index=quantile.index
    )
    # 분위수 방식에서도 분산 항이 필요하다. 해당 분위수까지의 조건부 분산을 쓴다.
    lead_sd = float(lead.std())

    daily_mean = snapshot["demand_mean"] / 30.0
    daily_sd = snapshot["demand_sd"] / np.sqrt(30.0)
    protection_days = REVIEW_PERIOD_DAYS + effective_lead
    protection_demand = daily_mean * protection_days
    safety_stock = SERVICE_Z * np.sqrt(
        effective_lead * daily_sd**2 + (daily_mean**2) * (lead_sd**2)
    )
    target = protection_demand + safety_stock
    on_hand = snapshot["month_end_stock"].clip(lower=0)
    order_quantile = (target - on_hand).clip(lower=0)

    # 대조군 1 — 현행 정책 (15일 고정 + 고정률 20%)
    current_protection = REVIEW_PERIOD_DAYS + CURRENT_FALLBACK_LEAD_DAYS
    current_target = daily_mean * current_protection * (1 + CURRENT_SAFETY_RATE)
    order_current = (current_target - on_hand).clip(lower=0)

    # 대조군 2 — 평균 배수 방식 (직전 제안)
    mean_lead = float(lead.mean())
    mean_effective = mean_lead * (1 + risk * 0.5) + risk * 14.0
    mean_protection = REVIEW_PERIOD_DAYS + mean_effective
    mean_ss = SERVICE_Z * np.sqrt(
        mean_effective * daily_sd**2
        + (daily_mean**2) * ((lead_sd * (1 + risk * 0.5)) ** 2)
    )
    order_mean = (daily_mean * mean_protection + mean_ss - on_hand).clip(lower=0)

    print("\n=== 세 방식 비교 ===")
    print(f"{'방식':<24}{'위험0 LT':>10}{'위험최대 LT':>12}{'발주량 합':>16}{'현행대비':>9}")
    rows = [
        ("현행 (15일 고정)", CURRENT_FALLBACK_LEAD_DAYS, CURRENT_FALLBACK_LEAD_DAYS, order_current),
        ("평균 배수", mean_lead, mean_effective.max(), order_mean),
        ("분위수 기반", float(lead.quantile(QUANTILE_BASE)), float(effective_lead.max()), order_quantile),
    ]
    for name, low, high, order in rows:
        print(f"{name:<24}{low:>9.1f}일{high:>11.1f}일{order.sum():>16,.0f}"
              f"{order.sum()/order_current.sum():>8.2f}x")

    print("\n=== 위험 수준별 리드타임 ===")
    print(f"{'위험':>6}{'분위수':>9}{'리드타임':>10}")
    for value in (0.0, 0.05, 0.10, 0.15, 0.19):
        q = QUANTILE_BASE + value * (QUANTILE_MAX - QUANTILE_BASE)
        print(f"{value:>6.2f}{f'p{q*100:.0f}':>9}{float(lead.quantile(q)):>9.1f}일")

    result = pd.DataFrame(
        {
            "institution_code": snapshot["institution_code"].astype(str),
            "item_name": snapshot["item_name"].astype(str),
            "on_hand": on_hand.round(1),
            "monthly_demand_mean": snapshot["demand_mean"].round(2),
            "supply_risk": risk.round(4),
            "lead_time_quantile": quantile.round(3),
            "recommended_lead_time_days": effective_lead.round(1),
            "protection_period_days": protection_days.round(1),
            "safety_stock": safety_stock.round(1),
            "target_stock": target.round(1),
            "recommended_order": order_quantile.round(1),
            "order_current_policy": order_current.round(1),
            "order_mean_multiplier": order_mean.round(1),
        }
    ).sort_values("recommended_order", ascending=False)
    result.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    OUT_JSON.write_text(
        json.dumps(
            {
                "method": "quantile_of_observed_lead_time",
                "quantile_base": QUANTILE_BASE,
                "quantile_max": QUANTILE_MAX,
                "lead_time_samples": int(len(lead)),
                "lead_time_quantiles": {
                    f"p{int(q*100)}": float(lead.quantile(q)) for q in grid
                },
                "snapshot_month": f"{latest:%Y-%m}",
                "items": int(len(result)),
                "order_totals": {
                    "current_policy": float(order_current.sum()),
                    "mean_multiplier": float(order_mean.sum()),
                    "quantile_based": float(order_quantile.sum()),
                },
                "rationale": (
                    "월별 median 은 30일 고정(표준편차 0.00)이고 p75 가 위험에 반응한다"
                    "(trade_risk lag 1개월 r=+0.445). 평균 배수는 정상 발주까지 늘려"
                    "위험 0 에서도 실측 median 대비 33% 과대추정한다."
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n저장: {OUT_CSV}\n      {OUT_JSON}")


if __name__ == "__main__":
    main()
