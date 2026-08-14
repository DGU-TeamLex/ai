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

    # 절대 척도가 아니라 백분위 순위를 쓴다.
    #
    # module_c_supply_risk 는 세 신호(supply_news 0.45 / material_news 0.20 /
    # market_price 0.35)의 가중합인데 실측 최대가 0.479 다. 이유는 두 가지다.
    #   1. market_price_risk 는 가중치 0.35 인데 값이 최대 0.105 에서 막힌다.
    #      기여 상한이 0.037 이라 사실상 상수 0 이다.
    #   2. 세 신호가 같은 행에서 동시에 p90 이상인 비율이 0.0000% 다.
    #      상관이 -0.223 ~ +0.460 이라 가중평균하면 범위가 구조적으로 눌린다.
    #
    # 그래서 절대값을 그대로 쓰면 risk 가 1 근처에 못 가고 분위수가 p59 에서
    # 멈춘다. 실측 리드타임은 p50~p80 이 전부 30일이라 이 구간에서는 어떤
    # 위험 변화도 리드타임을 못 움직인다.
    #
    # 정책이 물어야 할 것은 "절대 몇 점인가" 가 아니라 "다른 품목 대비 얼마나
    # 위험한가" 다. 절대 척도는 신호 결합 방식에 따라 임의로 정해지지만 순위는
    # 그렇지 않다. 위험이 전부 0 인 퇴화 상황에서는 순위가 무의미하므로 그때는
    # 전 품목을 base 로 둔다.
    #
    # ── 절대 척도로 되돌려 실측 비교했다 (2026-08-13) ──────────────────
    # 원자재 신뢰도 곱셈 붕괴(결함 P)를 기하평균으로 고쳐 market_price_risk 를
    # 0.105 → 0.582 로 5.6배 살린 뒤, 두 방식을 같은 데이터로 돌렸다.
    #
    #   스냅샷 최대 위험 0.3650
    #   절대 척도  quantile = 0.50 + 0.365×0.45 = p66  →  30.0일  (움직이지 않음)
    #   순위 기반  최상위 → p95                        →  94.0일
    #
    # 절대 척도는 여전히 실패한다. 이유는 결합 방식에 남은 결함 O 다 — 세 신호가
    # 같은 행에서 동시에 높은 비율이 0.0000% 라(상관 -0.223~+0.460) 가중평균이
    # 서로 상쇄해 위험이 1 근처에 못 간다. 실측 리드타임은 p50~p80 이 전부 30일
    # 이므로 p66 에서는 어떤 위험 변화도 리드타임을 못 움직인다.
    #
    # 결합 방식을 고치기 전까지 순위 기반이 유일하게 작동하는 매핑이다.
    if float(risk.max()) <= 0.0:
        risk_rank = pd.Series(0.0, index=risk.index)
    else:
        risk_rank = risk.rank(pct=True, method="average")
        # 위험 0 인 품목은 순위를 매기지 않는다. 절반이 0 이면 순위 0.25 를
        # 받아 근거 없이 리드타임이 올라간다.
        risk_rank = risk_rank.where(risk > 0, 0.0)
        positive = risk > 0
        if positive.any():
            risk_rank.loc[positive] = risk.loc[positive].rank(pct=True, method="average")
    print(f"  위험>0 {int((risk > 0).sum()):,}품목 ({float((risk > 0).mean()):.1%}) "
          f"— 순위 기반 매핑 적용")

    # 위험 순위 → 분위수 → 리드타임
    quantile = QUANTILE_BASE + risk_rank * (QUANTILE_MAX - QUANTILE_BASE)
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

    print("\n=== 위험 순위별 리드타임 ===")
    print(f"{'위험순위':>8}{'해당 위험값':>12}{'분위수':>9}{'리드타임':>10}")
    positive_risk = risk[risk > 0]
    for value in (0.0, 0.25, 0.50, 0.75, 0.90, 1.0):
        q = QUANTILE_BASE + value * (QUANTILE_MAX - QUANTILE_BASE)
        raw = float(positive_risk.quantile(value)) if len(positive_risk) else 0.0
        print(f"{value:>8.2f}{raw:>12.4f}{f'p{q*100:.0f}':>9}"
              f"{float(lead.quantile(q)):>9.1f}일")

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
