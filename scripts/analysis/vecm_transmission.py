"""원자재 가격 전달 구조를 VECM 으로 재추정한다.

기존 `material_market_factor_mapping.csv` 의 `lag_days`/`transmission_weight` 는
가정값이고, 내가 앞서 쓴 단순 교차상관도 이 분야 표준이 아니다.

문헌 표준 절차를 따른다.
  1) 단위근 검정(ADF): 로그수준이 I(1) 인지 확인
  2) Johansen 공적분 검정: 장기 균형관계 존재 여부
  3) 공적분이면 VECM: 오차수정항(alpha) 유의성 = 장기 인과
     공적분이 아니면 차분 VAR
  4) Granger 인과검정: 단기 인과
  5) 정보기준(AIC)으로 시차 선택 — 상관 최대값이 아니라

참고
  - Mineral Economics (2022): 원자재 가격 관계 분석의 표준은 공적분·VECM·Granger
  - De Mello & Ripple (2017) The Energy Journal 38(4): PP 가격은 내생적이며
    나프타·원유 충격은 서로가 주도하고 PP 로부터의 영향은 미미
"""
import json
import pathlib
import sys
import warnings

import numpy as np
import pandas as pd

warnings.simplefilter("ignore")
# 레포 루트를 sys.path 에 넣는다. 임시 폴더에서 옮겨온 스크립트라
# 절대경로가 박혀 있었다(다른 사람 PC 에서는 실행되지 않는다).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from statsmodels.tsa.stattools import adfuller, grangercausalitytests  # noqa: E402
from statsmodels.tsa.vector_ar.vecm import (  # noqa: E402
    VECM,
    coint_johansen,
    select_order,
)

from src.config import STOCK_MATERIAL_MAPPING_PATH  # noqa: E402

ROOT = str(pathlib.Path(__file__).resolve().parents[2])
MAX_LAG = 4
MIN_OBS = 20


def customs_unit_price():
    d = pd.read_csv(rf"{ROOT}\data\external\trade\kcs_trade_total_monthly.csv", dtype={"hs_code": str})
    for c in ("import_weight_kg", "import_value_usd"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d[(d["import_weight_kg"] > 0) & (d["import_value_usd"] > 0)].copy()
    d["usd_per_tonne"] = d["import_value_usd"] / d["import_weight_kg"] * 1000
    return d.pivot_table(index="STD_YYYYMM", columns="hs_code", values="usd_per_tonne", aggfunc="mean").sort_index()


def international_price():
    p = pd.read_csv(rf"{ROOT}\data\external\market\commodity_prices.csv")
    p["date"] = pd.to_datetime(p["date"], errors="coerce")
    p["STD_YYYYMM"] = p["date"].dt.strftime("%Y-%m")
    p["price"] = pd.to_numeric(p["price"], errors="coerce")
    return p.pivot_table(index="STD_YYYYMM", columns="market_factor_id", values="price", aggfunc="mean").sort_index()


def demand_share():
    mp = pd.read_parquet(STOCK_MATERIAL_MAPPING_PATH)[
        ["stock_item_key", "raw_material_meta_code", "market_signal_eligible"]
    ]
    mp = mp[mp["market_signal_eligible"].astype(str).isin(["True", "true"])]
    d = pd.read_parquet(rf"{ROOT}\data\processed\stock_monthly.parquet",
                        columns=["stock_item_key", "consumption_qty"])
    d["q"] = d["consumption_qty"].clip(lower=0)
    g = d.groupby("stock_item_key", as_index=False)["q"].sum()
    j = mp.merge(g, on="stock_item_key", how="left").fillna({"q": 0})
    return (j.groupby("raw_material_meta_code")["q"].sum() / g["q"].sum() * 100).to_dict()


def adf_is_unit_root(series, alpha=0.10):
    """귀무가설=단위근 존재. p>alpha 면 단위근(비정상)으로 본다."""
    s = series.dropna()
    if len(s) < 10:
        return None
    try:
        return float(adfuller(s, autolag="AIC")[1]) > alpha
    except Exception:
        return None


def analyse(intl: pd.Series, korea: pd.Series, label: str):
    df = pd.concat([np.log(intl.rename("intl")), np.log(korea.rename("korea"))], axis=1).dropna()
    df = df.replace([np.inf, -np.inf], np.nan).dropna()
    out = {"label": label, "n_obs": int(len(df))}
    if len(df) < MIN_OBS:
        out["status"] = "insufficient_sample"
        return out

    out["intl_unit_root"] = adf_is_unit_root(df["intl"])
    out["korea_unit_root"] = adf_is_unit_root(df["korea"])

    # 시차 선택: 정보기준
    try:
        maxlag = min(MAX_LAG, max(1, len(df) // 6))
        sel = select_order(df, maxlags=maxlag, deterministic="ci")
        k_ar_diff = int(sel.aic) if sel.aic and sel.aic > 0 else 1
    except Exception:
        k_ar_diff = 1
    out["selected_lag_aic"] = k_ar_diff

    # Johansen 공적분 검정
    try:
        jr = coint_johansen(df, det_order=0, k_ar_diff=k_ar_diff)
        trace = float(jr.lr1[0])
        crit95 = float(jr.cvt[0, 1])
        out["johansen_trace"] = round(trace, 3)
        out["johansen_crit95"] = round(crit95, 3)
        out["cointegrated"] = bool(trace > crit95)
    except Exception as exc:
        out["cointegrated"] = None
        out["johansen_error"] = str(exc)[:120]

    # VECM: 오차수정항 alpha 로 장기 인과 판정
    if out.get("cointegrated"):
        try:
            model = VECM(df, k_ar_diff=k_ar_diff, coint_rank=1, deterministic="ci").fit()
            alpha = np.asarray(model.alpha).flatten()
            pvals = np.asarray(model.pvalues_alpha).flatten()
            out["vecm_alpha_korea"] = round(float(alpha[1]), 4)
            out["vecm_alpha_korea_pvalue"] = round(float(pvals[1]), 4)
            # 한국 수입단가가 균형으로 조정되면(음의 유의 alpha) 국제가격이 장기 주도
            out["long_run_intl_leads_korea"] = bool(pvals[1] < 0.10 and alpha[1] < 0)
            # 반감기: 조정속도로부터
            if alpha[1] < 0 and abs(alpha[1]) < 1:
                out["half_life_months"] = round(float(np.log(0.5) / np.log(1 + alpha[1])), 2)
        except Exception as exc:
            out["vecm_error"] = str(exc)[:120]

    # Granger 인과: 국제가격 -> 한국 (차분 기준)
    d1 = df.diff().dropna()
    try:
        res = grangercausalitytests(d1[["korea", "intl"]], maxlag=min(MAX_LAG, max(1, len(d1) // 5)))
        gr = {int(lag): round(float(v[0]["ssr_ftest"][1]), 4) for lag, v in res.items()}
        out["granger_pvalues"] = gr
        sig = {k: v for k, v in gr.items() if v < 0.10}
        out["granger_significant_lags"] = [int(x) for x in sorted(sig)]
        out["granger_best_lag"] = int(min(gr, key=gr.get))
        out["granger_best_pvalue"] = gr[out["granger_best_lag"]]
    except Exception as exc:
        out["granger_error"] = str(exc)[:120]

    out["status"] = "ok"
    return out


def main():
    customs, intl, share = customs_unit_price(), international_price(), demand_share()
    hs_map = pd.read_csv(rf"{ROOT}\data\mapping\material_hs_mapping.csv", dtype=str)
    mf_map = pd.read_csv(rf"{ROOT}\data\mapping\material_market_factor_mapping.csv")
    hs_by_material = hs_map.groupby("raw_material_meta_code")["hs_code"].apply(list).to_dict()

    print(f"관세청 {customs.shape[0]}개월 | 국제가격 {intl.shape[0]}개월")
    print()

    results = []
    for _, row in mf_map.iterrows():
        material, factor = row["raw_material_meta_code"], row["market_factor_id"]
        codes = [c for c in hs_by_material.get(material, []) if c in customs.columns]
        base = {
            "material": material, "market_factor": factor,
            "assumed_lag_days": float(row["lag_days"]),
            "assumed_weight": float(row["transmission_weight"]),
            "demand_share_pct": round(share.get(material, 0.0), 3),
        }
        if factor not in intl.columns or not codes:
            base["status"] = "no_data"
            results.append(base)
            continue
        res = analyse(intl[factor], customs[codes].mean(axis=1), f"{factor} → {material}")
        results.append({**base, **res})

    ok = [r for r in results if r.get("status") == "ok"]
    ok.sort(key=lambda r: -r["demand_share_pct"])

    print("=== VECM/Granger 재추정 (수요 비중 순) ===")
    print(f"{'원자재':<26}{'수요%':>7}{'n':>4}{'공적분':>7}{'장기주도':>9}{'반감기':>8}{'Granger최적':>11}{'p':>8}{'가정':>7}")
    for r in ok:
        print(
            f"{r['material']:<26}{r['demand_share_pct']:>7.2f}{r['n_obs']:>4}"
            f"{str(r.get('cointegrated')):>7}{str(r.get('long_run_intl_leads_korea','-')):>9}"
            f"{str(r.get('half_life_months','-')):>8}"
            f"{str(r.get('granger_best_lag','-')):>11}{r.get('granger_best_pvalue',float('nan')):>8.3f}"
            f"{int(r['assumed_lag_days']):>6}일"
        )

    print()
    print("=== 판정 요약 ===")
    coint = [r for r in ok if r.get("cointegrated")]
    lead = [r for r in ok if r.get("long_run_intl_leads_korea")]
    gsig = [r for r in ok if r.get("granger_significant_lags")]
    print(f"공적분 성립           : {len(coint)}/{len(ok)} → {[r['material'] for r in coint]}")
    print(f"국제가격이 장기 주도  : {len(lead)}/{len(ok)} → {[r['material'] for r in lead]}")
    print(f"Granger 단기 인과 유의: {len(gsig)}/{len(ok)} → {[(r['material'], r['granger_significant_lags']) for r in gsig]}")

    print()
    print("=== 데이터 없음 ===")
    for r in results:
        if r.get("status") in ("no_data", "insufficient_sample"):
            print(f"  {r['material']:<30}{r['market_factor']:<24}{r['status']} (수요 {r['demand_share_pct']:.2f}%)")

    out = rf"{ROOT}\outputs\material_vecm_transmission.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"results": results}, f, ensure_ascii=False, indent=2, default=str)
    print("\nsaved:", out)


if __name__ == "__main__":
    main()
