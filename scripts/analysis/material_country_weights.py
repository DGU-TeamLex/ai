"""원자재별 국가 가중치 산출 — 관세청 실측 수입액 기반.

## 왜 필요한가

뉴스 공급차질 사건을 리드타임에 반영할 때, **어느 나라 사건인지** 가 결정적이다.
지금 수집된 사건은 미국·이란 비중이 큰데, 한국의 실제 수입 상대국과 다르다.

    Baltimore Port closure      ← 미국. 한국 수입 비중 0.3%
    Iran port explosion         ← 이란. 수입 비중 0%

`data/mapping/country_weight.csv` 가 그 자리인데 **0 KB 로 비어 있다.**
비어 있으면 이란 항구 폭발과 부산항 파업이 같은 무게로 들어간다.

## 왜 단일 국가 가중치가 아니라 원자재별인가

원자재마다 조달 국가가 다르다. 나프타는 중동, PP 는 다른 구성일 수 있다.
전체 합계로 하나의 국가 가중치를 만들면 수입액이 큰 원자재의 국가 구성이
전체를 지배한다. `(원자재 x 국가)` 로 만들어야 "PP 공장이 멈춘 나라" 와
"우리가 PP 를 사오는 나라" 가 맞는지 판별할 수 있다.

## 근거

관세청 수입액(USD)은 조달 의존도의 직접 측정치다. 물량(kg)이 아니라 금액을
쓰는 이유는 원자재 간 단가 차이를 흡수하기 위해서다. 원자재 **내부** 국가
비중을 보는 것이므로 원자재 간 비교에는 쓰지 않는다.

실행:
    python scripts/analysis/material_country_weights.py
"""
import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

WINDOW = ("2024-01", "2025-06")
# 꼬리 국가까지 두면 잡음이 커진다. 누적 비중이 이 값에 닿을 때까지만 남긴다.
COVERAGE_TARGET = 0.98
MIN_WEIGHT = 0.005

OUT_PATH = ROOT / "data" / "mapping" / "material_country_weight.csv"
REPORT_PATH = ROOT / "outputs" / "material_country_weight_report.csv"

# 매핑에 없지만 수입액 1위인 나프타를 보강한다.
# HS 2710124000 = 석유와 역청유(원유 제외) 중 나프타. 관세청 HS 부호 기준.
EXTRA_HS = {"2710124000": "PETROCHEMICAL_NAPHTHA"}


def main() -> None:
    mapping = pd.read_csv(
        ROOT / "data" / "mapping" / "material_hs_mapping.csv", dtype={"hs_code": str}
    )
    mapping = mapping[mapping["review_status"].eq("approved")]
    trade = pd.read_csv(
        ROOT / "data" / "external" / "trade" / "kcs_trade_country_monthly.csv",
        low_memory=False,
        dtype={"hs_code": str},
    )
    trade["STD_YYYYMM"] = trade["STD_YYYYMM"].astype(str)
    trade = trade[trade["STD_YYYYMM"].between(*WINDOW)]

    hs_to_material = dict(zip(mapping["hs_code"], mapping["raw_material_meta_code"]))
    hs_to_material.update(EXTRA_HS)
    trade["material"] = trade["hs_code"].map(hs_to_material)

    unmapped = trade[trade["material"].isna()]
    if len(unmapped):
        print(
            f"[주의] 매핑 없는 HS {unmapped['hs_code'].nunique()}종, "
            f"수입액 {unmapped['import_value_usd'].sum():,.0f} USD 제외: "
            f"{sorted(unmapped['hs_code'].unique())}"
        )
    trade = trade.dropna(subset=["material"])

    grouped = (
        trade.groupby(["material", "country_code", "country_name"], as_index=False)[
            "import_value_usd"
        ]
        .sum()
        .query("import_value_usd > 0")
    )
    totals = grouped.groupby("material")["import_value_usd"].transform("sum")
    grouped["region_weight"] = grouped["import_value_usd"] / totals
    grouped = grouped.sort_values(
        ["material", "region_weight"], ascending=[True, False]
    )

    # 누적 98% 까지 + 최소 비중 이상만 남긴다.
    grouped["cumulative"] = grouped.groupby("material")["region_weight"].cumsum()
    previous = grouped.groupby("material")["cumulative"].shift(fill_value=0.0)
    keep = previous.lt(COVERAGE_TARGET) & grouped["region_weight"].ge(MIN_WEIGHT)
    trimmed = grouped[keep].copy()

    print(f"\n=== 원자재별 국가 가중치 ({WINDOW[0]}~{WINDOW[1]}) ===")
    for material, block in trimmed.groupby("material"):
        share = ", ".join(
            f"{row.country_name}({row.country_code}) {row.region_weight:.0%}"
            for row in block.itertuples()
        )
        print(f"  {material:<32}{share}")

    trimmed[
        ["material", "country_code", "country_name", "region_weight", "import_value_usd"]
    ].to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    grouped.to_csv(REPORT_PATH, index=False, encoding="utf-8-sig")

    print(f"\n원자재 {trimmed['material'].nunique()}종 / 행 {len(trimmed)}")
    print(f"저장: {OUT_PATH}")
    print(f"전체(절사 전): {REPORT_PATH}")


if __name__ == "__main__":
    main()
