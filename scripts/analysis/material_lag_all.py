"""전 원자재 대칭 검증 — 특정 원자재를 기준으로 삼지 않는다.

검증 대상은 두 개의 등록 매핑이며, 원자재마다 동일한 절차를 적용한다.

  1) material_hs_mapping.csv        원자재 -> 관세청 HS (한국 수입단가 산출용)
  2) material_market_factor_mapping.csv  원자재 -> 국제 시장요인 (+ 가정된 lag_days, transmission_weight)

각 원자재에 대해 "국제가격 -> 한국 수입단가" 지연을 0~6개월 교차상관으로 실측하고,
등록된 가정값과 비교한다. 수요 비중을 함께 붙여 어느 원자재가 중요한지도 같이 본다.
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd

# 레포 루트를 sys.path 에 넣는다. 임시 폴더에서 옮겨온 스크립트라
# 절대경로가 박혀 있었다(다른 사람 PC 에서는 실행되지 않는다).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from src.config import STOCK_MATERIAL_MAPPING_PATH  # noqa: E402

ROOT = str(pathlib.Path(__file__).resolve().parents[2])
MAX_LAG = 6
MIN_OVERLAP = 12


def customs_unit_price():
    d = pd.read_csv(
        rf"{ROOT}\data\external\trade\kcs_trade_total_monthly.csv", dtype={"hs_code": str}
    )
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
    total = g["q"].sum()
    s = j.groupby("raw_material_meta_code")["q"].sum() / total * 100
    return s.to_dict()


def scan_lag(upstream: pd.Series, downstream: pd.Series):
    up, down = upstream.pct_change(fill_method=None), downstream.pct_change(fill_method=None)
    rows = []
    for lag in range(0, MAX_LAG + 1):
        j = pd.concat([up.shift(lag), down], axis=1).dropna()
        if len(j) < MIN_OVERLAP:
            continue
        r = j.iloc[:, 0].corr(j.iloc[:, 1])
        if pd.notna(r):
            rows.append({"lag_months": lag, "corr": float(r), "n": int(len(j))})
    if not rows:
        return None, rows
    return max(rows, key=lambda x: abs(x["corr"])), rows


def main():
    customs = customs_unit_price()
    intl = international_price()
    share = demand_share()
    hs_map = pd.read_csv(rf"{ROOT}\data\mapping\material_hs_mapping.csv", dtype=str)
    mf_map = pd.read_csv(rf"{ROOT}\data\mapping\material_market_factor_mapping.csv")

    print(f"관세청 단가 {customs.shape[0]}개월 × {customs.shape[1]}HS | 국제가격 {intl.shape[0]}개월 × {intl.shape[1]}시리즈")
    print()

    # 원자재 -> HS 목록
    hs_by_material = hs_map.groupby("raw_material_meta_code")["hs_code"].apply(list).to_dict()

    results = []
    for _, row in mf_map.iterrows():
        material = row["raw_material_meta_code"]
        factor = row["market_factor_id"]
        assumed_lag_days = float(row["lag_days"])
        weight = float(row["transmission_weight"])
        quality = float(row["proxy_quality"])

        codes = [c for c in hs_by_material.get(material, []) if c in customs.columns]
        entry = {
            "material": material,
            "market_factor": factor,
            "assumed_lag_days": assumed_lag_days,
            "assumed_weight": weight,
            "assumed_proxy_quality": quality,
            "demand_share_pct": round(share.get(material, 0.0), 3),
            "hs_codes_available": codes,
        }
        if factor not in intl.columns or not codes:
            entry["status"] = "no_data"
            results.append(entry)
            continue

        # 여러 HS 가 있으면 수입단가를 평균한다.
        korea = customs[codes].mean(axis=1)
        best, allrows = scan_lag(intl[factor], korea)
        if best is None:
            entry["status"] = "insufficient_overlap"
        else:
            entry["status"] = "measured"
            entry["measured_lag_months"] = best["lag_months"]
            entry["measured_lag_days"] = best["lag_months"] * 30
            entry["corr"] = round(best["corr"], 3)
            entry["n"] = best["n"]
            entry["lag_gap_days"] = entry["measured_lag_days"] - assumed_lag_days
            entry["curve"] = allrows
        results.append(entry)

    measured = [r for r in results if r["status"] == "measured"]
    measured.sort(key=lambda r: -r["demand_share_pct"])

    print("=== 전 원자재 대칭 검증 (수요 비중 순) ===")
    print(f"{'원자재':<32}{'수요%':>7}{'시장요인':<24}{'가정':>7}{'실측':>7}{'차이':>7}{'상관':>8}{'n':>4}")
    for r in measured:
        print(
            f"{r['material']:<32}{r['demand_share_pct']:>7.2f}{r['market_factor']:<24}"
            f"{int(r['assumed_lag_days']):>6}일{r['measured_lag_days']:>6}일"
            f"{r['lag_gap_days']:>+6.0f}일{r['corr']:>8.3f}{r['n']:>4}"
        )

    print()
    print("=== 데이터 없음 / 표본 부족 ===")
    for r in results:
        if r["status"] != "measured":
            print(f"  {r['material']:<32}{r['market_factor']:<24}{r['status']}  (수요 {r['demand_share_pct']:.2f}%)")

    print()
    strong = [r for r in measured if abs(r["corr"]) >= 0.4]
    weak = [r for r in measured if abs(r["corr"]) < 0.2]
    negative = [r for r in measured if r["corr"] < -0.2]
    print(f"상관 0.4 이상(신뢰 가능) : {len(strong)}종 → {[r['material'] for r in strong]}")
    print(f"상관 0.2 미만(근거 약함) : {len(weak)}종 → {[r['material'] for r in weak]}")
    print(f"음의 상관(방향 반대)     : {len(negative)}종 → {[r['material'] for r in negative]}")
    gap = [r for r in measured if abs(r["lag_gap_days"]) >= 30]
    print(f"가정-실측 30일 이상 괴리 : {len(gap)}종 → {[(r['material'], r['lag_gap_days']) for r in gap]}")

    out = rf"{ROOT}\outputs\material_lag_all.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"results": results}, f, ensure_ascii=False, indent=2)
    print("\nsaved:", out)


if __name__ == "__main__":
    main()
