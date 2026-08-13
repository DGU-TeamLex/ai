"""전 품목 공통 리드타임 분포 기반 안전재고 — 현행 고정률과 비교.

## 왜 품목별이 아니라 전 품목 공통인가

이슈 #39 에서 품목별 리드타임의 근거가 무너졌다.

  * 두 기간 공통 1,831품목의 품목별 median 상관 = **0.079**
    → 리드타임은 품목 고유 특성이 아니다. 발주 시점·공급자 사정에 좌우된다.
  * 기간 간 out-of-sample 비교에서 품목별 raw median 은 MAE 34.30 으로
    **전 품목 공통값 21.61 보다 나빴다**. 표본이 얇아 과적합이다.
  * 2024-25 기준 표본 5건 이상 품목은 8,796종 중 3,619종뿐이다.

따라서 전 품목 공통 분포를 쓰고, 품목별로는 **수요** 만 개별화한다.

## 왜 고정률 0.20 을 바꾸는가

현행은 `SS = 보호기간수요 x 0.20` 이다. 리드타임 분산이 전혀 들어가지 않는다.
그런데 실측 분포는 p10 3일 ~ p90 197일로 **변동폭 65배** 다. 이 분산을 무시하면
서비스수준이 통제되지 않는다.

리드타임과 수요가 모두 확률변수일 때의 표준 안전재고 공식을 쓴다.

    SS = z_α · sqrt( L̄·σ_d²  +  d̄²·σ_L² )

  좌항은 수요 불확실성, 우항은 **리드타임 불확실성** 이다. 현행 고정률에는
  우항이 통째로 없다.

  근거: Silver, E. A., Pyke, D. F. & Peterson, R. (1998),
        *Inventory Management and Production Planning and Scheduling*, 3rd ed., Ch. 7.
        확률적 리드타임 하의 base-stock 은 Eppen & Martin (1988),
        "Determining Safety Stock in the Presence of Stochastic Lead Time and Demand",
        *Management Science* 34(11):1380-1390 참조.

## 외부신호 연동

이슈 #72 의 결론 — 외부신호는 수요예측이 아니라 **리드타임 경로** 로 붙인다.
PP 관계가 소비량이 아니라 마감재고에서 나왔기 때문이다(가격 충격이 수요를
바꾸는 것이 아니라 조달을 막아 재고를 깎는다).

    L̄_adj = L̄ · (1 + risk · multiplier_max) + risk · extra_days_max

계수는 `data/mapping/module_c_risk_weights.json` 의 캘리브레이션 값을 쓴다.

실행:
    python scripts/analysis/global_lead_time_safety_stock.py
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

REVIEW_PERIOD_DAYS = 30.0
CURRENT_SAFETY_STOCK_RATE = 0.20
SERVICE_LEVELS = {0.90: 1.2816, 0.95: 1.6449, 0.98: 2.0537}
RISK_SCENARIOS = (0.0, 0.25, 0.50, 0.75, 1.0)

OUT_PATH = ROOT / "outputs" / "global_lead_time_safety_stock.json"


def lead_time_distribution() -> dict:
    """전 품목 공통 리드타임 분포. **조달청 실측 L_계약** 에서 모수를 잡는다.

    처음에는 원장 품절지속(M)에서 잡았는데 안전재고가 현행의 11~17배가 나왔다.
    원인은 M 의 분산이다.

        원장 M      p10 3  p25 11  median 35  p75 96  p90 197   → sd 197일
        조달청 L    p10 30 p25 30  median 30  p75 30  p90 60    → sd 41일

    M 의 긴 꼬리는 **조달이 오래 걸린 것이 아니라 발주 자체를 안 한 휴면 구간**
    이다. 그 분산을 σ_L 로 쓰면 리드타임 불확실성을 몇 배로 부풀린다.
    L 은 발주→납기라는 정의가 명확하고 30일에 몰려 있다.

    한계: L_계약은 계약상 납기지 실제 도착일이 아니다. 기관코드 비식별화로
    원장 입고일과 대조할 수 없어 납기 초과분은 반영되지 않는다. 즉 σ_L 은
    과소추정 쪽이다.
    """
    frame = pd.read_json(
        ROOT / "data" / "processed" / "procurement_delivery_requests.jsonl", lines=True
    ).replace("--", None)
    for column in ("dlvrReqRcptDate", "maxDlvrTmlmtDate"):
        frame[column] = pd.to_datetime(frame[column], errors="coerce")
    lead = (frame["maxDlvrTmlmtDate"] - frame["dlvrReqRcptDate"]).dt.days
    lead = lead[lead.between(0, 365)]
    return {
        "source": "procurement_contract_lead_time_2024_01_06",
        "n": int(len(lead)),
        "p10": float(lead.quantile(0.10)), "p25": float(lead.quantile(0.25)),
        "median": float(lead.median()), "p75": float(lead.quantile(0.75)),
        "p90": float(lead.quantile(0.90)),
        "mean_days": round(float(lead.mean()), 1),
        "sd_days": round(float(lead.std()), 1),
        "note": "계약상 납기. 실제 도착일이 아니므로 sd 는 과소추정 쪽이다.",
    }


def demand_moments() -> pd.DataFrame:
    """품목별 월 수요 평균·표준편차. 수요만 개별화한다."""
    frame = pd.read_parquet(
        ROOT / "outputs" / "stock_feature_table.parquet",
        columns=["stock_item_key", "year_month", "consumption_qty", "data_period"],
    )
    frame = frame[frame["data_period"].astype(str).eq("current")]
    moments = (
        frame.groupby("stock_item_key")["consumption_qty"]
        .agg(["mean", "std", "count"])
        .rename(columns={"mean": "demand_mean", "std": "demand_sd", "count": "months"})
    )
    # 관측 6개월 미만은 표준편차가 불안정하다.
    moments = moments[moments["months"] >= 6].dropna()
    return moments[moments["demand_mean"] > 0]


def safety_stock(mean_daily, sd_daily, lead_mean, lead_sd, z_value):
    """SS = z · sqrt(L·σ_d² + d̄²·σ_L²)"""
    demand_term = lead_mean * sd_daily**2
    lead_term = (mean_daily**2) * (lead_sd**2)
    return z_value * np.sqrt(demand_term + lead_term)


def main() -> None:
    weights = json.loads(
        (ROOT / "data" / "mapping" / "module_c_risk_weights.json").read_text(
            encoding="utf-8"
        )
    )["inventory_adjustment"]
    multiplier_max = float(weights["supply_lead_time_multiplier_max"])
    extra_days_max = float(weights["supply_extra_lead_time_days_max"])

    distribution = lead_time_distribution()
    lead_mean = distribution["mean_days"]
    lead_sd = distribution["sd_days"]

    print("=== 전 품목 공통 리드타임 분포 (조달청 실측 L_계약) ===")
    print(
        f"  p10 {distribution['p10']:.0f} / p25 {distribution['p25']:.0f} / "
        f"median {distribution['median']:.0f} / p75 {distribution['p75']:.0f} / "
        f"p90 {distribution['p90']:.0f} 일"
    )
    print(f"  평균 {lead_mean:.1f}일, 표준편차 {lead_sd:.1f}일 (n={distribution[chr(39)+chr(39)]})" if False else f"  평균 {lead_mean:.1f}일, 표준편차 {lead_sd:.1f}일")
    print(f"  표본 {distribution['n']:,}건 · 출처: 조달청 납품요구 실측")

    moments = demand_moments()
    print(f"\n품목 {len(moments):,}종 (관측 6개월 이상)")

    daily_mean = moments["demand_mean"] / 30.0
    daily_sd = moments["demand_sd"] / np.sqrt(30.0)

    rows = []
    for risk in RISK_SCENARIOS:
        adjusted_mean = lead_mean * (1 + risk * multiplier_max) + risk * extra_days_max
        adjusted_sd = lead_sd * (1 + risk * multiplier_max)
        protection_days = REVIEW_PERIOD_DAYS + adjusted_mean
        protection_demand = daily_mean * protection_days

        current_ss = protection_demand * CURRENT_SAFETY_STOCK_RATE
        entry = {
            "risk": risk,
            "lead_mean_days": round(adjusted_mean, 1),
            "protection_days": round(protection_days, 1),
            "current_ss_sum": float(current_ss.sum()),
        }
        for level, z_value in SERVICE_LEVELS.items():
            proposed = safety_stock(daily_mean, daily_sd, adjusted_mean, adjusted_sd, z_value)
            entry[f"ss_sum_z{int(level*100)}"] = float(proposed.sum())
            entry[f"ratio_z{int(level*100)}"] = float(proposed.sum() / current_ss.sum())
        rows.append(entry)

    print(f"\n=== 안전재고 비교 (현행 고정 {CURRENT_SAFETY_STOCK_RATE:.0%} 대비) ===")
    print(
        f"{'위험':>6}{'리드타임':>10}{'보호기간':>10}"
        + "".join(f"{f'z={l:.0%}':>12}" for l in SERVICE_LEVELS)
    )
    for row in rows:
        ratios = "".join(
            f"{row[f'ratio_z{int(l*100)}']:>11.2f}x" for l in SERVICE_LEVELS
        )
        print(
            f"{row['risk']:>6.2f}{row['lead_mean_days']:>9.1f}일"
            f"{row['protection_days']:>9.1f}일{ratios}"
        )

    OUT_PATH.write_text(
        json.dumps(
            {
                "lead_time_distribution": distribution,
                "coefficients": {
                    "supply_lead_time_multiplier_max": multiplier_max,
                    "supply_extra_lead_time_days_max": extra_days_max,
                },
                "items": int(len(moments)),
                "scenarios": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n저장: {OUT_PATH}")
    print(
        "\n현행 고정률에는 리드타임 분산 항(d̄²·σ_L²)이 없다. 위 비율은 그 항을\n"
        "넣었을 때 안전재고가 몇 배가 되는지를 뜻한다."
    )


if __name__ == "__main__":
    main()
