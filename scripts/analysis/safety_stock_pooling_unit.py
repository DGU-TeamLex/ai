"""안전재고 산출 단위를 실측으로 고른다 (ai#44).

## 왜

지금은 안전재고를 **기관 × 품목** 조합마다 따로 계산한다. 그런데 대부분 조합은
관측이 몇 개 안 된다. 관측 3개로 계산한 표준편차는 믿을 수 없는 값이고, 그것이
그대로 발주량이 된다.

이 이슈는 "비슷한 것끼리 묶어서 같이 계산하자" 는 제안이다. 문제는 **어느
단위로 묶느냐** 다. 너무 잘게 묶으면 표본이 여전히 부족하고, 너무 크게 묶으면
서로 다른 품목이 같은 안전재고를 받는다.

## 무엇을 재나

후보 단위마다 이 둘을 본다.

    표본 충분성   조합당 관측 월수가 몇 개인가. 12개월 미만이면 계절을 못 본다.
    동질성        같은 그룹 안에서 수요 변동계수(CV)가 얼마나 흩어지는가.
                  흩어지면 묶은 것이 서로 다른 품목이라는 뜻이다.

두 지표는 상충한다. 크게 묶을수록 표본은 늘고 동질성은 떨어진다. **그 교환을
숫자로 보고 고르자는 것이 이 스크립트의 목적이다.**

## 후보

표준품목 3계층을 쓴다. #45 의 3계층 폴백과 같은 취지다.

    subtype  37개    가장 크게 묶인다
    family   736개
    group    14개    ※ 이름과 달리 group 이 가장 크다

    기관x품목   현행. 가장 잘다.
    품목        기관을 합친다.
    기관x계층 / 계층 단독

실행:
    python scripts/analysis/safety_stock_pooling_unit.py
"""
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

MIN_MONTHS = 12          # 계절을 한 바퀴 보려면 최소 12개월
FEATURE_PATH = ROOT / "outputs" / "stock_feature_table.parquet"
OUT_PATH = ROOT / "outputs" / "safety_stock_pooling_unit.json"


def _coefficient_of_variation(block: pd.Series) -> float:
    mean = block.mean()
    return float(block.std() / mean) if mean and np.isfinite(mean) and mean > 0 else np.nan


def analyze_pooling_candidates(panel: pd.DataFrame) -> dict:
    panel = panel.copy()
    panel["institution"] = panel["stock_item_key"].astype(str).str.split("::").str[0]
    representative = panel["representative_item_id"].astype("string").fillna("").str.strip()
    panel["representative_item_id"] = representative.where(
        representative.ne(""),
        "UNRESOLVED::" + panel["stock_item_key"].astype(str),
    )
    # 표준품목 3계층: subtype(잘다) < family < group(크다). #45 의 3계층 폴백과 같은 취지다.
    for column, label in (("standard_item_subtype_id", "subtype"),
                          ("standard_item_family_id", "family"),
                          ("standard_item_group_id", "group")):
        panel[label] = panel[column].astype(str).replace({"": "UNKNOWN", "None": "UNKNOWN"}).fillna("UNKNOWN")

    series_month = (
        panel.groupby(
            [
                "institution",
                "representative_item_id",
                "subtype",
                "family",
                "group",
                "year_month",
            ],
            as_index=False,
            observed=True,
        )["consumption_qty"]
        .sum()
    )
    series_keys = ["institution", "representative_item_id"]
    series_stats = (
        series_month.groupby(series_keys, as_index=False, observed=True)
        .agg(
            series_cv=("consumption_qty", _coefficient_of_variation),
            subtype=("subtype", "first"),
            family=("family", "first"),
            group=("group", "first"),
        )
    )

    candidates = {
        "기관x대표품목 (현행)": ["institution", "representative_item_id"],
        "대표품목": ["representative_item_id"],
        "기관x subtype": ["institution", "subtype"],
        "subtype": ["subtype"],
        "기관x family": ["institution", "family"],
        "family": ["family"],
        "group": ["group"],
    }

    report = {}
    for label, keys in candidates.items():
        months = series_month.groupby(keys, observed=True)[
            "year_month"
        ].nunique()
        group_cv_iqr = series_stats.groupby(keys, observed=True)[
            "series_cv"
        ].apply(
            lambda values: (
                values.dropna().quantile(0.75)
                - values.dropna().quantile(0.25)
                if len(values.dropna())
                else np.nan
            )
        )
        enough = float(months.ge(MIN_MONTHS).mean())
        valid_iqr = group_cv_iqr.dropna()
        report[label] = {
            "groups": int(len(months)),
            "median_months": float(months.median()),
            "share_ge_12_months": enough,
            "median_within_group_series_cv_iqr": (
                float(valid_iqr.median()) if len(valid_iqr) else None
            ),
            "p90_within_group_series_cv_iqr": (
                float(valid_iqr.quantile(0.90)) if len(valid_iqr) else None
            ),
        }
    return report


def main() -> None:
    import json

    columns = ["stock_item_key", "year_month", "consumption_qty",
               "representative_item_id", "standard_item_family_id",
               "standard_item_subtype_id", "standard_item_group_id",
               "data_period"]
    panel = pd.read_parquet(FEATURE_PATH, columns=columns)
    panel = panel[panel["data_period"].astype(str).eq("current")]
    report = analyze_pooling_candidates(panel)
    print(f"패널 {len(panel):,}행")
    print(f"\n{'단위':<22}{'조합수':>10}{'월수 중앙':>10}{'12개월+':>9}{'CV IQR':>10}")
    for label, values in report.items():
        spread = values["median_within_group_series_cv_iqr"]
        print(
            f"  {label:<20}{values['groups']:>10,}"
            f"{values['median_months']:>10.0f}"
            f"{values['share_ge_12_months']:>9.1%}"
            f"{spread if spread is not None else np.nan:>10.2f}"
        )

    print(f"\n해석")
    현행 = report["기관x대표품목 (현행)"]
    print(f"  현행은 조합당 중앙 {현행['median_months']:.0f}개월, "
          f"{현행['share_ge_12_months']:.1%} 만 12개월 이상이다.")
    best = max(
        (k for k in report if k != "기관x대표품목 (현행)"),
        key=lambda k: report[k]["share_ge_12_months"],
    )
    print(f"  표본이 가장 넉넉한 것은 '{best}' "
          f"({report[best]['share_ge_12_months']:.1%} 가 12개월 이상)")
    print(
        "  동질성은 그룹 안 개별 기관×대표품목 CV의 IQR로 비교한다. "
        "최종 단위 선택은 별도 holdout의 서비스수준·품절월·평균재고가 필요하다."
    )

    OUT_PATH.write_text(
        json.dumps({"min_months": MIN_MONTHS, "candidates": report},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n저장: {OUT_PATH}")
    print("\n어느 단위를 쓸지는 표본 충분성과 동질성의 교환이라 정책 판단이다.")
    print("이 표는 그 교환을 숫자로 보여줄 뿐 대신 정하지 않는다.")


if __name__ == "__main__":
    main()
