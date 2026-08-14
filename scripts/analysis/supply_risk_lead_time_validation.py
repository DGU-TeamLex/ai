"""공급위험이 실제 리드타임을 설명하는가 — 계수 운영 검증 (ai#20).

## 왜 필요한가

`module_c_risk_weights.json` 이 스스로 상태를 이렇게 적고 있다.

    calibration_status: "...requires_operational_validation"

리드타임 조정 계수(`supply_lead_time_multiplier_max=0.5`,
`supply_extra_lead_time_days_max=14.0`)는 **품절 프록시** 로 캘리브레이션됐고,
**실제 리드타임으로 검증된 적이 없다.** 지금까지는 검증할 실측 리드타임 자체가
없었는데, 조달청 납품요구 수집으로 확보됐다.

## 검정

    H0: 공급위험은 그 달의 실제 리드타임을 설명하지 못한다
    H1: 위험이 높은 달에 리드타임이 길다

월 단위로 맞춘다.

    risk_t  = 그 달 module_c_supply_risk 의 대표값
    L_t     = 그 달 조달청 실측 리드타임 평균

**median 이 아니라 평균을 쓴다.** median 은 전 구간 30일로 고정이라(계약 관행)
변동이 없어 검정이 불가능하다. 평균은 35.3~49.8일로 움직이며, 이 변동은 장기
계약·특수 품목이 섞이는 정도를 반영한다.

시차도 함께 본다. 위험이 이번 달에 커지면 리드타임은 다음 달 발주부터 길어질
수 있다.

## 한계

* 표본이 월 단위 20개다. 검정력이 낮아 효과가 있어도 못 잡을 수 있다.
* L_계약은 계약상 납기이지 실제 도착일이 아니다. 납기 초과는 반영되지 않는다.
* 조달청 모집단(보건소 전체)과 위험 점수 모집단(원장 품목)이 다르다. 같은
  기관·품목을 짝지은 것이 아니라 **월 단위 집계끼리** 맞춘 것이다.

실행:
    python scripts/analysis/supply_risk_lead_time_validation.py
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

LAGS = (0, 1, 2)
OUT_PATH = ROOT / "outputs" / "supply_risk_lead_time_validation.json"


def monthly_lead_time() -> pd.DataFrame:
    frame = pd.read_json(
        ROOT / "data" / "processed" / "procurement_delivery_requests.jsonl", lines=True
    ).replace("--", None)
    for column in ("dlvrReqRcptDate", "maxDlvrTmlmtDate"):
        frame[column] = pd.to_datetime(frame[column], errors="coerce")
    frame["lead_days"] = (
        frame["maxDlvrTmlmtDate"] - frame["dlvrReqRcptDate"]
    ).dt.days
    frame = frame[frame["lead_days"].between(0, 365)]
    frame["month"] = frame["dlvrReqRcptDate"].dt.to_period("M")
    return (
        frame.groupby("month")["lead_days"]
        .agg(lead_mean="mean", lead_p75=lambda s: s.quantile(0.75), n="size")
        .reset_index()
    )


def monthly_risk() -> pd.DataFrame:
    frame = pd.read_csv(
        ROOT / "outputs" / "stock_module_c_risk_scores.csv",
        low_memory=False,
        usecols=["STD_YYYYMM", "module_c_supply_risk", "module_c_trade_risk"],
    )
    frame["month"] = pd.PeriodIndex(frame["STD_YYYYMM"], freq="M")
    return (
        frame.groupby("month")
        .agg(
            supply_risk_mean=("module_c_supply_risk", "mean"),
            supply_risk_p90=("module_c_supply_risk", lambda s: s.quantile(0.90)),
            trade_risk_mean=("module_c_trade_risk", "mean"),
        )
        .reset_index()
    )


def main() -> None:
    from scipy import stats

    lead = monthly_lead_time()
    risk = monthly_risk()
    merged = lead.merge(risk, on="month", how="inner").sort_values("month")
    print(f"겹치는 월 {len(merged)}개: {merged['month'].min()} ~ {merged['month'].max()}")
    print(
        f"리드타임 평균 {merged['lead_mean'].min():.1f}~{merged['lead_mean'].max():.1f}일 "
        f"(표준편차 {merged['lead_mean'].std():.2f})"
    )

    if len(merged) < 10:
        print("표본이 10개월 미만이라 검정하지 않는다.")
        return

    rows = []
    risk_columns = ["supply_risk_mean", "supply_risk_p90", "trade_risk_mean"]
    target_columns = ["lead_mean", "lead_p75"]
    for risk_column in risk_columns:
        for target in target_columns:
            for lag in LAGS:
                shifted = merged[risk_column].shift(lag)
                pair = pd.DataFrame(
                    {"risk": shifted, "lead": merged[target]}
                ).dropna()
                if len(pair) < 8 or pair["risk"].std() == 0:
                    continue
                pearson, p_pearson = stats.pearsonr(pair["risk"], pair["lead"])
                spearman, p_spearman = stats.spearmanr(pair["risk"], pair["lead"])
                rows.append(
                    {
                        "risk": risk_column,
                        "target": target,
                        "lag": lag,
                        "n": len(pair),
                        "pearson": round(float(pearson), 3),
                        "p_pearson": round(float(p_pearson), 4),
                        "spearman": round(float(spearman), 3),
                        "p_spearman": round(float(p_spearman), 4),
                    }
                )

    result = pd.DataFrame(rows)
    tests = len(result)
    result["bonferroni_p"] = (result["p_pearson"] * tests).clip(upper=1.0).round(4)

    print(f"\n=== 검정 {tests}건 (Bonferroni 보정 포함) ===")
    print(
        f"{'위험':<18}{'대상':<11}{'lag':>4}{'n':>4}"
        f"{'pearson':>9}{'p':>8}{'spearman':>10}{'p':>8}{'보정p':>8}"
    )
    for row in result.sort_values("p_pearson").itertuples():
        mark = " *" if row.bonferroni_p < 0.05 else ""
        print(
            f"{row.risk:<18}{row.target:<11}{row.lag:>4}{row.n:>4}"
            f"{row.pearson:>9.3f}{row.p_pearson:>8.3f}"
            f"{row.spearman:>10.3f}{row.p_spearman:>8.3f}{row.bonferroni_p:>8.3f}{mark}"
        )

    significant = result[result["bonferroni_p"] < 0.05]
    print(f"\nBonferroni 보정 후 유의: {len(significant)}/{tests}")
    raw_significant = result[result["p_pearson"] < 0.05]
    print(f"보정 전 유의: {len(raw_significant)}/{tests}")
    if len(raw_significant):
        print("보정 전 유의한 조합:")
        for row in raw_significant.itertuples():
            print(f"  {row.risk} → {row.target} (lag {row.lag}) r={row.pearson:+.3f} p={row.p_pearson:.4f}")

    OUT_PATH.write_text(
        json.dumps(
            {
                "months": int(len(merged)),
                "period": [str(merged["month"].min()), str(merged["month"].max())],
                "lead_mean_range": [
                    float(merged["lead_mean"].min()),
                    float(merged["lead_mean"].max()),
                ],
                "tests": result.to_dict("records"),
                "significant_after_bonferroni": int(len(significant)),
                "note": (
                    "월 단위 집계끼리 맞춘 것이며 같은 기관·품목을 짝지은 것이 아니다. "
                    "L_계약은 계약 납기이지 실제 도착일이 아니다."
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n저장: {OUT_PATH}")


if __name__ == "__main__":
    main()
