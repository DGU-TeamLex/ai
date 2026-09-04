"""식약처 공급중단 보고가 우리 품목에 닿는가 + 약성분 수록 여부의 판별력 (ai#43).

## 가져온 자료

1. **식약처 공급중단·부족 의약품 현황** (nedrug.mfds.go.kr, /pbp/CCBAF10·CCBAF11)
   1,936건 / 2013-02 ~ 2026-08. 공급중단 1,315 / 공급부족 621.
   컬럼: 성분, 제품명, 효능, 보고구분, 보고일자.

2. **심평원 생산수입공급중단 보고대상 의약품** (data.go.kr/data/15054463)
   3,047건. 공급중단 시 보고 의무가 걸린 품목 목록(사건이 아니라 지정).
   공공저작물 제1유형(출처표시).

3. **약성분 원장** (정보원 제공 DAT, 3,576,320행 / 고유 약품코드 80,934)
   약품코드 → 성분코드 + 성분명(영문). 이미 로컬에 있었다.

## 배경 — 왜 이걸 봤나

현행 공급위험은 GDELT 뉴스로 대리한다. 그런데 뉴스→리드타임 12개 검정이
전부 무의미했다. 규제기관이 직접 받은 실제 공급중단 기록이라면 다를 것이라
보고 가져왔다.

참고: Frontiers in Pharmacology (2025), "Drug shortage in South Korea:
machine learning-based prediction models and analysis of duration and causal
factors" 는 같은 출처 1,054건(2018-2024)으로 중단기간을 예측했다. 보고된
중단의 **69% 가 12개월 이상 또는 영구중단** 이다(≥12개월 331 + 영구 392 /
1,053). 안전재고로 버틸 길이가 아니다.

## 검정 1 — 공급중단이 우리 품목에 닿는가

제품명으로 약품마스터와 붙인 뒤 성분코드 축으로 확장해 노출을 잰다.

## 검정 2 — 약성분 수록 여부가 의약품을 가려내는가 (ai#43)

`is_medical` 판정에 약성분 수록 여부를 넣자는 제안의 근거를 잰다.

실행:
    .venv/Scripts/python.exe scripts/analysis/drug_shortage_exposure.py
"""
import json
import pathlib
import re
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SHORTAGE = ROOT / "data" / "external" / "shortage" / "mfds_drug_supply_disruption.csv"
MASTER = ROOT / "data" / "external" / "shortage" / "drug_ingredient_master.csv"
BACKTEST = ROOT / "data" / "handoff" / "backtest_predictions.parquet"
OUT_PATH = ROOT / "outputs" / "drug_shortage_exposure.json"


def _normalize_product(value: str) -> str:
    """제품명을 대조 가능한 형태로 깎는다.

    `[수출명:...]`, 업체명 괄호, 말미 포장단위(-1정)를 떼고 공백·구두점을
    없앤다. 식약처 표기와 원장 표기가 이 정도만 달랐다.
    """
    text = re.sub(r"\[.*?\]", "", str(value))
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"-\s*\d+\s*\S*$", "", text)
    return re.sub(r"[\s\.,/·]", "", text).lower()


def main() -> None:
    shortage = pd.read_csv(SHORTAGE, dtype=str)
    master = pd.read_csv(MASTER, dtype=str)
    items = pd.read_parquet(
        BACKTEST,
        columns=["item_code", "stock_item_key", "standard_item_group_id",
                 "standard_item_family_id", "actual_usage"],
    )

    result: dict = {}

    # --- 검정 2 먼저: 약성분 수록 여부의 판별력 (ai#43) -------------------
    unique_items = items.drop_duplicates("item_code").copy()
    drug_codes = set(master["약품코드"].dropna())
    unique_items["has_ingredient"] = unique_items["item_code"].isin(drug_codes)

    by_group = (unique_items.groupby("standard_item_group_id")["has_ingredient"]
                .agg(["size", "sum", "mean"])
                .sort_values("size", ascending=False))
    print("=== 약성분 수록 여부 x 품목군 (ai#43) ===")
    print(f"전체 물품코드 {len(unique_items):,} 중 수록 "
          f"{unique_items['has_ingredient'].sum():,} "
          f"({unique_items['has_ingredient'].mean():.2%})\n")
    print(f"{'품목군':<18}{'품목수':>8}{'수록':>8}{'수록률':>9}")
    for group, row in by_group.iterrows():
        print(f"  {str(group):<16}{int(row['size']):>8,}{int(row['sum']):>8,}"
              f"{row['mean']*100:>8.1f}%")
    result["ingredient_coverage_by_group"] = {
        str(k): {"items": int(v["size"]), "with_ingredient": int(v["sum"]),
                 "rate": float(v["mean"])}
        for k, v in by_group.iterrows()
    }

    # UNCLASSIFIED 중 약성분이 있는 품목은 사실상 의약품이다. 재분류 후보.
    reclass = unique_items[
        unique_items["standard_item_group_id"].astype(str).eq("UNCLASSIFIED")
        & unique_items["has_ingredient"]
    ]
    print(f"\n  UNCLASSIFIED 중 약성분 보유 {len(reclass):,}건, 의약품 재분류 후보")
    result["unclassified_with_ingredient"] = int(len(reclass))

    # --- 검정 1: 공급중단 노출 --------------------------------------------
    master["key"] = master["약품명1"].map(_normalize_product)
    shortage["key"] = shortage["product_name"].map(_normalize_product)
    joined = shortage.merge(
        master[["key", "성분코드", "약품코드"]], on="key", how="inner"
    )

    our_codes = set(unique_items["item_code"])
    our_ingredients = set(master[master["약품코드"].isin(our_codes)]["성분코드"].dropna())

    direct = joined[joined["약품코드"].isin(our_codes)]
    by_ingredient = joined[joined["성분코드"].isin(our_ingredients)]

    affected_codes = set(
        master[master["성분코드"].isin(set(by_ingredient["성분코드"]))
               & master["약품코드"].isin(our_codes)]["약품코드"]
    )
    affected = items[items["item_code"].isin(affected_codes)]
    usage_share = float(affected["actual_usage"].sum() / items["actual_usage"].sum())

    print(f"\n=== 공급중단 노출 ===")
    print(f"  식약처 사건 {len(shortage):,}건 중 약품마스터 제품명 일치 {len(joined):,}건")
    print(f"  우리 물품코드 직접 일치      {len(direct):,}건 / 품목 {direct['약품코드'].nunique():,}")
    print(f"  성분코드 축 일치            {len(by_ingredient):,}건 "
          f"({len(by_ingredient)/len(shortage):.1%})")
    print(f"  영향 물품코드 {len(affected_codes):,} / 계열 {affected['stock_item_key'].nunique():,}")
    print(f"  사용량 비중 {usage_share:.2%}")
    result["shortage_exposure"] = {
        "events_total": int(len(shortage)),
        "events_matched_to_master": int(len(joined)),
        "events_direct_item_match": int(len(direct)),
        "events_ingredient_match": int(len(by_ingredient)),
        "affected_item_codes": int(len(affected_codes)),
        "affected_series": int(affected["stock_item_key"].nunique()),
        "usage_share": usage_share,
    }

    verdict = (
        "공급중단 보고는 우리 품목에 거의 닿지 않는다. 사용량 비중 "
        f"{usage_share:.2%} 로 공급위험 신호로 쓰기에 작다."
        if usage_share < 0.05 else
        "공급중단 보고가 우리 품목에 유의한 규모로 닿는다. 신호화를 검토할 만하다."
    )
    print(f"\n  판정: {verdict}")
    result["verdict"] = verdict

    result["caveats"] = [
        "제품명 정규화 대조라 표기가 다르면 놓친다. 노출은 하한이다.",
        "성분코드 축 확장은 '같은 성분의 다른 제품'까지 세므로 상한 쪽이다.",
        "보고일자는 보고 시점이지 실제 중단 시작일이 아니다.",
        "월별 원장이 없어 사건 전후 재고·출고 변화(사건연구)는 못 했다.",
    ]
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"\n저장: {OUT_PATH}")


if __name__ == "__main__":
    main()
