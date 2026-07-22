"""
이슈 #25 첫 산출물: demand_class(DORMANT/CENSORED/ACTIVE) + mu_corrected

[PR #26 리뷰 반영 - 2차]
- zero_ratio를 재고(closing) 기반으로 재정의 (기존: 거래유무 기반 observed_ratio의
  보수(1-observed_ratio)를 썼는데, 이는 ai#24의 "재고0 비율"과 다른 정의였음 - 리뷰 지적)
  -> 월말 마감재고(closing) 스냅샷 기준으로 재정의 (리뷰가 제시한 옵션2: 근사치)
  -> 한계: 월말 시점만 보므로 월중 결품(예: 15일에 소진, 25일에 재입고)을 놓쳐
     zero_ratio가 실제보다 과소추정될 수 있음. 정확한 값은 censored_demand.parquet
     (원장 기준 일별 재고0일수, choigod1023 보유) 확보 후 교체 예정.
- compute_demand_class_mu_corrected.py 의 입력 경로를 환경변수로 분리 (리뷰 지적)
"""

import os

import numpy as np
import pandas as pd

CONTROL_THRESHOLD = 0.9
MIN_OBS_FOR_PRIOR = 12
MIN_INSTITUTIONS_FOR_ITEM_K = 5
K_FALLBACK_CAP_PERCENTILE = 90
RATIO_CAP_PERCENTILE = 95

# 리뷰 반영: 다른 저장소(wep-stock-item-material-pipeline) 경로를 하드코딩하지 않고
# 환경변수로 받음. 기본값은 기존 로컬 개발 경로를 유지.
STOCK_PANEL_PATH = os.environ.get(
    "STOCK_PANEL_PATH", "output_full/backtest/stock_monthly_panel.parquet"
)

panel = pd.read_parquet(STOCK_PANEL_PATH)

# --- 1) 기관+물품+월 단위로 부서 합산 (demand, closing 모두 합산) ---
agg = panel.groupby(["보건기관코드_en", "물품코드", "ym"], as_index=False).agg(
    demand=("demand", "sum"),
    observed=("observed", "max"),
    closing=("closing", "sum"),  # 부서 합산 재고 (기관 전체 관점의 재고 보유 여부 판단용)
)
agg["stock_zero_month"] = agg["closing"] <= 0

# --- 2) 기관+물품 단위 series 통계 ---
series = agg.groupby(["보건기관코드_en", "물품코드"], as_index=False).agg(
    months=("ym", "count"),
    obs_months=("observed", "sum"),
    demand_total=("demand", "sum"),
    zero_stock_months=("stock_zero_month", "sum"),
)
series["mu_naive"] = series["demand_total"] / series["months"]
series["observed_ratio"] = series["obs_months"] / series["months"]

# [수정] zero_ratio: 거래유무가 아니라 월말 재고(closing) 기준으로 재정의.
# NOTE: 월말 스냅샷 근사치이므로 월중 결품은 놓침 -> 과소추정 가능성 있음.
#       censored_demand.parquet(원장 기준 일별) 확보 시 이 컬럼을 교체할 것.
series["zero_ratio"] = series["zero_stock_months"] / series["months"]

# --- 3) demand_class 분류 (정의는 리뷰 지적대로 유지, zero_ratio만 재고기반으로 교체됨) ---
series["demand_class"] = np.select(
    [
        (series["demand_total"] == 0) & (series["zero_ratio"] < 0.5),
        series["zero_ratio"] >= 0.5,
    ],
    ["DORMANT", "CENSORED"],
    default="ACTIVE",
)
print("=== demand_class 분포 (재고 기반 zero_ratio, 월 패널 근사치) ===")
print(series["demand_class"].value_counts())
print(series["demand_class"].value_counts(normalize=True).round(3))
print("\n주의: 월말 스냅샷 근사치입니다. 원장 기준 일별 값(censored_demand.parquet)"
      " 확보 시 재계산 필요.")

is_true_zero_demand = (series["demand_total"] == 0) & (series["observed_ratio"] >= CONTROL_THRESHOLD)

# --- 4) v3: Buhlmann shrinkage (기존 로직 그대로) ---
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

series.loc[series["demand_class"] == "DORMANT", "mu_corrected"] = 0.0

print(f"\n기관+물품 series 수: {len(series)}")

output = series[
    ["보건기관코드_en", "물품코드", "demand_class", "mu_corrected", "mu_naive", "zero_ratio"]
].rename(columns={"보건기관코드_en": "anon_institution_code", "물품코드": "standard_code"})

output.to_csv("output_full/backtest/demand_class_mu_corrected_handoff.csv", index=False, encoding="utf-8-sig")
print(f"\n저장 완료: output_full/backtest/demand_class_mu_corrected_handoff.csv ({len(output)}행)")
