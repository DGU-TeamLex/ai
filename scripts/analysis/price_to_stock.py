"""원자재 가격 -> 보건소 실제 소비·재고 영향 분석.

지금까지는 '가격 -> 가격'(국제가격 -> 한국 수입단가)만 봤다.
정작 이 과제가 답해야 할 것은 '가격 -> 보건소 재고·소비'다.

원장은 두 구간이 있다.
  과거 2018-01 ~ 2019-12  (1,460,785 월별행)
  현재 2024-01 ~ 2025-12  (3,729,983 월별행)

각 원자재에 매핑된 재고품목의 월별 소비량·마감재고를 집계하고,
그 원자재의 가격(국제 또는 관세청 수입단가)과의 관계를 시차별로 본다.

주의: 소비량은 계절성과 추세가 강하므로 로그차분 후 본다.
      가격 충격이 '수요'가 아니라 '공급(재고 보유)'에 먼저 나타날 것으로 예상되므로
      소비량과 마감재고를 나눠서 본다.
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

import contextlib, io as _io
from statsmodels.tsa.stattools import grangercausalitytests as _gct  # noqa: E402


def grangercausalitytests(*a, **kw):
    with contextlib.redirect_stdout(_io.StringIO()):
        return _gct(*a, **kw)

from src.config import STOCK_MATERIAL_MAPPING_PATH  # noqa: E402

ROOT = str(pathlib.Path(__file__).resolve().parents[2])
MAX_LAG = 6
MIN_OBS = 18

# 시차 선택도 모델 적합이므로 검증 구간을 반드시 분리한다.
# 설계 두 가지를 동시에 돌려 결과가 일치하는지 본다.
#   A: 2018-19 로 추정 -> 2024-25 로 검증 (4년 공백을 사이에 둔 엄격한 홀드아웃)
#   B: 2024-01~2025-09 로 추정 -> 2025-10~12 로 검증 (보고서의 최종확인 구간과 동일)
DESIGNS = {
    "A_1819_to_2425": {
        "est": ("2018-01", "2019-12"),
        "val": ("2024-01", "2025-12"),
    },
    "B_recent_holdout": {
        "est": ("2024-01", "2025-09"),
        "val": ("2025-10", "2025-12"),
    },
}


def material_to_stock_keys():
    mp = pd.read_parquet(STOCK_MATERIAL_MAPPING_PATH)[
        ["stock_item_key", "raw_material_meta_code", "market_signal_eligible"]
    ]
    mp = mp[mp["market_signal_eligible"].astype(str).isin(["True", "true"])]
    return mp.groupby("raw_material_meta_code")["stock_item_key"].apply(set).to_dict()


def monthly_stock_panel():
    frames = []
    for path, period in (
        (rf"{ROOT}\data\processed\stock_monthly.parquet", "current"),
        (rf"{ROOT}\data\processed\stock_monthly_2018_2019_auxiliary.parquet", "historical"),
    ):
        d = pd.read_parquet(path, columns=["year_month", "stock_item_key", "consumption_qty", "month_end_stock"])
        d["data_period"] = period
        frames.append(d)
    panel = pd.concat(frames, ignore_index=True)
    panel["STD_YYYYMM"] = pd.to_datetime(panel["year_month"]).dt.strftime("%Y-%m")
    panel["consumption_qty"] = panel["consumption_qty"].clip(lower=0)
    return panel


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


def granger(price: pd.Series, outcome: pd.Series, label: str, design: dict):
    """추정 구간에서만 시차를 고르고, 검증 구간에서 부호 유지 여부를 본다."""
    df = pd.concat([np.log(price.rename("price")), np.log(outcome.rename("outcome"))], axis=1)
    df = df.replace([np.inf, -np.inf], np.nan).dropna()
    (e0, e1), (v0, v1) = design["est"], design["val"]

    est = df[(df.index >= e0) & (df.index <= e1)].diff().dropna()
    if len(est) < MIN_OBS:
        return {"label": label, "status": "insufficient_estimation", "n_estimation": int(len(est))}
    try:
        maxlag = min(MAX_LAG, max(1, len(est) // 5))
        res = grangercausalitytests(est[["outcome", "price"]], maxlag=maxlag)
        pv = {int(k): round(float(v[0]["ssr_ftest"][1]), 4) for k, v in res.items()}
        best = int(min(pv, key=pv.get))
        out = {
            "label": label, "status": "ok",
            "n_estimation": int(len(est)),
            "pvalues": pv, "best_lag_months": best, "best_pvalue": pv[best],
            "significant": [int(k) for k, v in pv.items() if v < 0.10],
        }
        full = df.diff().dropna()
        joined = pd.concat([full["price"].shift(best), full["outcome"]], axis=1).dropna()
        ep = joined[(joined.index >= e0) & (joined.index <= e1)]
        vp = joined[(joined.index >= v0) & (joined.index <= v1)]
        out["n_validation"] = int(len(vp))
        if len(ep) >= 6 and len(vp) >= 3:
            ec = ep.iloc[:, 0].corr(ep.iloc[:, 1])
            vc = vp.iloc[:, 0].corr(vp.iloc[:, 1])
            if pd.notna(ec) and pd.notna(vc):
                out["estimation_corr"] = round(float(ec), 3)
                out["validation_corr"] = round(float(vc), 3)
                out["sign_holds"] = bool(np.sign(ec) == np.sign(vc))
        return out
    except Exception as exc:
        return {"label": label, "status": "error", "error": str(exc)[:100]}


def main():
    keys_by_material = material_to_stock_keys()
    panel = monthly_stock_panel()
    intl = international_price()
    customs = customs_unit_price()
    hs_map = pd.read_csv(rf"{ROOT}\data\mapping\material_hs_mapping.csv", dtype=str)
    mf_map = pd.read_csv(rf"{ROOT}\data\mapping\material_market_factor_mapping.csv")
    hs_by_material = hs_map.groupby("raw_material_meta_code")["hs_code"].apply(list).to_dict()

    print(f"원장 {panel['STD_YYYYMM'].nunique()}개월 ({panel['STD_YYYYMM'].min()}~{panel['STD_YYYYMM'].max()})")
    print(f"국제가격 {intl.shape[0]}개월 | 관세청 {customs.shape[0]}개월")

    total_demand = panel["consumption_qty"].sum()
    materials = sorted(
        {m for m in mf_map["raw_material_meta_code"].unique() if m in keys_by_material},
        key=lambda m: -panel[panel["stock_item_key"].isin(keys_by_material[m])]["consumption_qty"].sum(),
    )

    results = []
    for design_name, design in DESIGNS.items():
        print()
        print(f"########## 설계 {design_name}  추정 {design['est'][0]}~{design['est'][1]}  검증 {design['val'][0]}~{design['val'][1]} ##########")
        print(f"{'원자재':<24}{'수요%':>7}{'가격원천':<20}{'대상':<12}{'n추정':>6}{'시차':>5}{'p':>8}{'유의':<8}{'추정r':>7}{'검증r':>7}{'부호':>6}")
        for material in materials:
            keys = keys_by_material[material]
            sub = panel[panel["stock_item_key"].isin(keys)]
            if sub.empty:
                continue
            share = sub["consumption_qty"].sum() / total_demand * 100
            agg = sub.groupby("STD_YYYYMM").agg(
                consumption=("consumption_qty", "sum"),
                end_stock=("month_end_stock", "sum"),
            ).sort_index()
            agg = agg[agg["consumption"] > 0]

            sources = {}
            for f in mf_map.loc[mf_map["raw_material_meta_code"].eq(material), "market_factor_id"].unique():
                if f in intl.columns:
                    sources[f"intl:{f}"] = intl[f]
            codes = [c for c in hs_by_material.get(material, []) if c in customs.columns]
            if codes:
                sources["customs"] = customs[codes].mean(axis=1)

            for src_name, price in sources.items():
                for outcome_name in ("consumption", "end_stock"):
                    r = granger(price, agg[outcome_name], f"{material}|{src_name}|{outcome_name}", design)
                    r.update({"design": design_name, "material": material, "price_source": src_name,
                              "outcome": outcome_name, "demand_share_pct": round(share, 3)})
                    results.append(r)
                    if r["status"] == "ok":
                        sig = ",".join(str(x) for x in r["significant"]) or "-"
                        print(f"{material:<24}{share:>7.2f}{src_name:<20}{outcome_name:<12}"
                              f"{r['n_estimation']:>6}{r['best_lag_months']:>5}{r['best_pvalue']:>8.3f}{sig:<8}"
                              f"{r.get('estimation_corr',float('nan')):>7.2f}{r.get('validation_corr',float('nan')):>7.2f}"
                              f"{str(r.get('sign_holds','-')):>6}")

    print()
    print("########## 종합 판정 ##########")
    ok = [r for r in results if r.get("status") == "ok"]
    # 두 설계 모두에서 유의하고 부호가 유지된 조합
    key = lambda r: (r["material"], r["price_source"], r["outcome"])
    byk = {}
    for r in ok:
        byk.setdefault(key(r), {})[r["design"]] = r
    robust = []
    for k, v in byk.items():
        if len(v) < 2:
            continue
        if all(x.get("significant") and x.get("sign_holds") for x in v.values()):
            robust.append((k, v))
    print(f"검정 {len(ok)}건 | 두 설계 모두 통과: {len(robust)}건")
    for k, v in sorted(robust, key=lambda x: -x[1][list(x[1])[0]]["demand_share_pct"]):
        share = v[list(v)[0]]["demand_share_pct"]
        lags = {d: x["best_lag_months"] for d, x in v.items()}
        print(f"  {k[0]} ({share:.2f}%) | {k[1]} → {k[2]} | 시차 {lags}")
    if not robust:
        print("  없음 - 어떤 원자재 가격도 두 설계 모두에서 보건소 소비·재고를 설명하지 못함")

    out = rf"{ROOT}\outputs\price_to_stock_analysis.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"designs": DESIGNS, "results": results}, f, ensure_ascii=False, indent=2, default=str)
    print()
    print("saved:", out)


if __name__ == "__main__":
    main()
