"""
이슈 #25 첫 산출물: demand_class(DORMANT/CENSORED/ACTIVE) + mu_corrected

- demand_class 는 이슈 #25가 명시한 규칙 그대로 적용 (기관+물품 단위, DB grain과 일치)
- mu_corrected 는 이슈#25가 제안한 단순 가드(out_sum/held_eff, *10 캡) 대신
  우리가 이슈#24에서 검증한 v3(Buhlmann shrinkage) + v5(상대배수 캡) 결과를 사용
  (더 정교하고, 대조군 검증까지 거친 값이라는 게 채택 이유)

출력: anon_institution_code, standard_code, demand_class, mu_corrected, mu_naive
"""

import numpy as np
import pandas as pd

CONTROL_THRESHOLD = 0.9
MIN_OBS_FOR_PRIOR = 12
MIN_INSTITUTIONS_FOR_ITEM_K = 5
K_FALLBACK_CAP_PERCENTILE = 90
RATIO_CAP_PERCENTILE = 95

panel = pd.read_parquet("output_full/backtest/stock_monthly_panel.parquet")

# --- 1) 기관+물품+월 단위로 부서 합산 ---
agg = panel.groupby(["보건기관코드_en", "물품코드", "ym"], as_index=False).agg(
    demand=("demand", "sum"),
    observed=("observed", "max"),
)

# --- 2) 기관+물품 단위 series 통계 ---
series = agg.groupby(["보건기관코드_en", "물품코드"], as_index=False).agg(
    months=("ym", "count"),
    obs_months=("observed", "sum"),
    demand_total=("demand", "sum"),
)
series["mu_naive"] = series["demand_total"] / series["months"]
series["observed_ratio"] = series["obs_months"] / series["months"]
series["zero_ratio"] = 1 - series["observed_ratio"]  # 재고0 비율 (월 단위 근사)

# --- 3) 이슈#25 규칙 그대로 demand_class 분류 ---
series["demand_class"] = np.select(
    [
        (series["demand_total"] == 0) & (series["zero_ratio"] < 0.5),
        series["zero_ratio"] >= 0.5,
    ],
    ["DORMANT", "CENSORED"],
    default="ACTIVE",
)
print("=== demand_class 분포 ===")
print(series["demand_class"].value_counts())
print(series["demand_class"].value_counts(normalize=True).round(3))

is_true_zero_demand = (series["demand_total"] == 0) & (series["observed_ratio"] >= CONTROL_THRESHOLD)

# --- 4) v3: Buhlmann shrinkage (기존 로직 재사용) ---
reliable = series[
    (series["observed_ratio"] >= CONTROL_THRESHOLD)
    & (series["obs_months"] >= MIN_OBS_FOR_PRIOR)
    & (series["demand_total"] > 0)
].copy()
reliable["rate"] = reliable["demand_total"] / reliable["obs_months"]


def buhlmann_item_params(g: pd.DataFrame) -> pd.Series:
    n = len(g)
    w = g["obs_months"].to_numpy()
    r = g["rate"].to_numpy()
    m = np.average(r, weights=w)
    if n < MIN_INSTITUTIONS_FOR_ITEM_K:
        return pd.Series({"item_mean": m, "k": np.nan, "n": n})
    proc_var = np.average(m / w, weights=w)
    obs_var = np.average((r - m) ** 2, weights=w)
    between_var = obs_var - proc_var
    k = np.nan if between_var <= 0 else proc_var / between_var
    return pd.Series({"item_mean": m, "k": k, "n": n})


item_params = reliable.groupby("물품코드").apply(buhlmann_item_params, include_groups=False)
finite_k = item_params["k"].dropna()
k_cap = finite_k.quantile(K_FALLBACK_CAP_PERCENTILE / 100) if len(finite_k) > 0 else 6.0
item_params["k"] = item_params["k"].fillna(k_cap).clip(upper=k_cap)
global_k_fallback = item_params.loc[item_params["n"] >= MIN_INSTITUTIONS_FOR_ITEM_K, "k"].median()
item_params.loc[item_params["n"] < MIN_INSTITUTIONS_FOR_ITEM_K, "k"] = global_k_fallback
global_prior_mean = np.average(reliable["rate"], weights=reliable["obs_months"])

series = series.merge(
    item_params.rename(columns={"item_mean": "prior_mean", "k": "beta_item"})[["prior_mean", "beta_item"]],
    on="물품코드",
    how="left",
)
series["prior_mean"] = series["prior_mean"].fillna(global_prior_mean)
series["beta_item"] = series["beta_item"].fillna(global_k_fallback)

alpha = series["prior_mean"] * series["beta_item"]
series["mu_shrink"] = (alpha + series["demand_total"]) / (series["beta_item"] + series["obs_months"])
series.loc[is_true_zero_demand, "mu_shrink"] = series.loc[is_true_zero_demand, "mu_naive"]

# --- 5) v5: 관측부족 pair만 사전 대비 배수로 캡 ---
reliable2 = series[
    (series["observed_ratio"] >= CONTROL_THRESHOLD)
    & (series["obs_months"] >= MIN_OBS_FOR_PRIOR)
    & (series["demand_total"] > 0)
].copy()
reliable2["ratio_to_prior"] = reliable2["mu_naive"] / reliable2["prior_mean"].replace(0, np.nan)
max_ratio = reliable2["ratio_to_prior"].quantile(RATIO_CAP_PERCENTILE / 100)

needs_cap = series["obs_months"] < MIN_OBS_FOR_PRIOR
cap_value = series["prior_mean"] * max_ratio
series["mu_corrected"] = series["mu_shrink"]
over_cap = needs_cap & (series["mu_shrink"] > cap_value)
series.loc[over_cap, "mu_corrected"] = cap_value[over_cap]

# DORMANT는 정의상 진짜 무사용이므로 mu_corrected도 0 유지 (보정 대상 아님)
series.loc[series["demand_class"] == "DORMANT", "mu_corrected"] = 0.0

print(f"\n기관+물품 series 수: {len(series)}")

output = series[
    ["보건기관코드_en", "물품코드", "demand_class", "mu_corrected", "mu_naive"]
].rename(columns={"보건기관코드_en": "anon_institution_code", "물품코드": "standard_code"})

output.to_csv("output_full/backtest/demand_class_mu_corrected_handoff.csv", index=False, encoding="utf-8-sig")
print(f"\n저장 완료: output_full/backtest/demand_class_mu_corrected_handoff.csv ({len(output)}행)")
