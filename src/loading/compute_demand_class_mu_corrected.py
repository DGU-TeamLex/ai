"""
이슈 #25 첫 산출물: demand_class(DORMANT/CENSORED/ACTIVE) + mu_corrected

[PR #26 리뷰 반영 - 3차]
- censored_demand.parquet(choigod1023 제공, data/handoff/) 기반으로
  zero_ratio를 정확하게 계산 (월 패널 closing 근사치 대체)
- grain 차이(기관×부서×물품 416,128 vs DB 기준 기관×물품)를 리뷰 가이드대로
  부서 합산 후 비율 재계산 (합산 전 비율을 합치면 안 됨)
- mu_corrected는 계속 Buhlmann(v3)/캡(v5) 방식 사용 (censored_demand.parquet의
  mu_보정 컬럼은 진단값이라 그대로 쓰면 안 된다는 안내 반영 - 원래도 안 썼음)
"""

import os

import numpy as np
import pandas as pd

CONTROL_THRESHOLD = 0.9
MIN_OBS_FOR_PRIOR = 12
MIN_INSTITUTIONS_FOR_ITEM_K = 5
K_FALLBACK_CAP_PERCENTILE = 90
RATIO_CAP_PERCENTILE = 95

STOCK_PANEL_PATH = os.environ.get(
    "STOCK_PANEL_PATH", "output_full/backtest/stock_monthly_panel.parquet"
)
CENSORED_DEMAND_PATH = os.environ.get(
    "CENSORED_DEMAND_PATH", "data/handoff/censored_demand.parquet"
)

# --- 1) mu 계산용: 기존과 동일하게 기관+물품+월 단위로 부서 합산 ---
panel = pd.read_parquet(STOCK_PANEL_PATH)
agg = panel.groupby(["보건기관코드_en", "물품코드", "ym"], as_index=False, observed=True).agg(
    demand=("demand", "sum"),
    observed=("observed", "max"),
)
series = agg.groupby(["보건기관코드_en", "물품코드"], as_index=False, observed=True).agg(
    months=("ym", "count"),
    obs_months=("observed", "sum"),
    demand_total=("demand", "sum"),
)
series["mu_naive"] = series["demand_total"] / series["months"]
series["observed_ratio"] = series["obs_months"] / series["months"]

# --- 2) zero_ratio: censored_demand.parquet에서 정확히 계산 (부서 합산 후 재계산) ---
censored = pd.read_parquet(CENSORED_DEMAND_PATH)
censored_agg = censored.groupby(["보건기관코드_en", "물품코드"], as_index=False, observed=True).agg(
    재고0일=("재고0일", "sum"),
    T=("T", "max"),
)
censored_agg["zero_ratio"] = (censored_agg["재고0일"] / censored_agg["T"]).clip(0, 1)

series = series.merge(
    censored_agg[["보건기관코드_en", "물품코드", "zero_ratio"]],
    on=["보건기관코드_en", "물품코드"],
    how="left",
)
missing_zero_ratio = series["zero_ratio"].isna().sum()
print(f"censored_demand.parquet과 매칭 안 된 series: {missing_zero_ratio}건 "
      f"(grain 차이로 인한 소수 불일치는 정상, 대량이면 매칭키 재확인 필요)")

# --- 3) demand_class 분류 ---
series["demand_class"] = np.select(
    [
        (series["demand_total"] == 0) & (series["zero_ratio"] < 0.5),
        series["zero_ratio"] >= 0.5,
    ],
    ["DORMANT", "CENSORED"],
    default="ACTIVE",
)
print("\n=== demand_class 분포 (censored_demand.parquet 기반, 정확한 값) ===")
print(series["demand_class"].value_counts())
print(series["demand_class"].value_counts(normalize=True).round(3))
print("\n검증 기준(리뷰어 제시): CENSORED 91,798건(22.4%) 근처여야 함")

is_true_zero_demand = (series["demand_total"] == 0) & (series["observed_ratio"] >= CONTROL_THRESHOLD)

# --- 4) v3: Buhlmann shrinkage (기존 로직 그대로 - 리뷰에서 계속 사용 확인받음) ---
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


item_params = reliable.groupby("물품코드", observed=True).apply(buhlmann_item_params, include_groups=False)
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

series.loc[series["demand_class"] == "DORMANT", "mu_corrected"] = 0.0

print(f"\n기관+물품 series 수: {len(series)}")

output = series[
    ["보건기관코드_en", "물품코드", "demand_class", "mu_corrected", "mu_naive", "zero_ratio"]
].rename(columns={"보건기관코드_en": "anon_institution_code", "물품코드": "standard_code"})

output.to_csv("output_full/backtest/demand_class_mu_corrected_handoff.csv", index=False, encoding="utf-8-sig")
print(f"\n저장 완료: output_full/backtest/demand_class_mu_corrected_handoff.csv ({len(output)}행)")
