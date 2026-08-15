"""공급중단 보고 전후로 재고·출고가 실제로 움직이는가 (ai#21, ai#43).

## 왜

`drug_shortage_exposure.py` 에서 식약처 공급중단 보고가 우리 품목에 닿는 규모가
사용량 기준 1.62% 로 작다는 것을 확인했다. 작다는 것이 곧 "효과가 없다" 는
아니다. **닿는 품목 안에서는 크게 움직일 수도** 있다. 그건 별도 질문이다.

여기서는 닿는 품목만 놓고 보고 전후를 본다.

## 설계

    처리군: 공급중단·부족 보고가 걸린 성분을 가진 물품코드
    대조군: 그 외 물품코드
    시점  : 보고월 t=0 기준 t-3 ~ t+3

지표 두 가지를 본다.

    재고0 비율   그 달에 재고가 0인 (기관,물품) 비율
    정상출고량   그 달 출고 합 (계열 중앙값으로 정규화)

**차이의 차이(DID)** 로 본다. 처리군의 전후 변화에서 대조군의 같은 기간 변화를
뺀다. 계절성과 전반적 추세가 상쇄된다.

## 한계

* 보고일자는 **보고 시점** 이지 실제 중단 시작일이 아니다. 시점이 흐려진다.
* 처리군 배정이 무작위가 아니다. 공급중단이 나는 품목은 애초에 다른 품목이다.
  DID 는 평행추세 가정에 기대는데, 그 가정을 t-3~t-1 로 눈으로만 확인한다.
* 표본이 작다. 검정력이 낮아 효과가 있어도 못 잡을 수 있다.

실행:
    .venv/Scripts/python.exe scripts/analysis/shortage_event_study.py
"""
import json
import os
import pathlib
import re
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

LEDGER = os.environ.get(
    "LEDGER_2024_2025", os.path.expanduser("~/Desktop/ledger_2024_2025.parquet")
)
SHORTAGE = ROOT / "data" / "external" / "shortage" / "mfds_drug_supply_disruption.csv"
MASTER = ROOT / "data" / "external" / "shortage" / "drug_ingredient_master.csv"
OUT_PATH = ROOT / "outputs" / "shortage_event_study.json"

WINDOW = 3  # t-3 ~ t+3


def _normalize_product(value: str) -> str:
    text = re.sub(r"\[.*?\]", "", str(value))
    text = re.sub(r"\(.*?\)", "", text)
    text = re.sub(r"-\s*\d+\s*\S*$", "", text)
    return re.sub(r"[\s\.,/·]", "", text).lower()


def _load_ledger() -> pd.DataFrame:
    frame = pd.read_parquet(
        LEDGER,
        columns=["보건기관코드_en", "물품코드", "재고마감일", "정상출고량", "마감재고량"],
    )
    # `재고마감일` 은 20241220 형태의 int/str 이다. format="mixed" 로 읽으면
    # epoch 정수로 해석돼 전 구간이 1970-01 이 된다(실측). %Y%m%d 로 못박는다.
    frame["date"] = pd.to_datetime(
        frame["재고마감일"].astype(str).str.strip(), format="%Y%m%d", errors="coerce"
    )
    frame = frame.dropna(subset=["date"])
    frame["ym"] = frame["date"].dt.to_period("M")
    return frame


def main() -> None:
    shortage = pd.read_csv(SHORTAGE, dtype=str)
    master = pd.read_csv(MASTER, dtype=str)

    master["key"] = master["약품명1"].map(_normalize_product)
    shortage["key"] = shortage["product_name"].map(_normalize_product)
    shortage["report_month"] = pd.to_datetime(
        shortage["report_date"], errors="coerce"
    ).dt.to_period("M")

    joined = shortage.merge(master[["key", "성분코드"]], on="key", how="inner")
    joined = joined.dropna(subset=["성분코드", "report_month"])

    ledger = _load_ledger()
    span = (ledger["ym"].min(), ledger["ym"].max())
    print(f"원장 {len(ledger):,}행 / {span[0]} ~ {span[1]}")

    # 원장에 실제로 있는 물품코드로 성분 -> 물품 전개
    ledger_codes = set(ledger["물품코드"].unique())
    code_to_ing = (master[master["약품코드"].isin(ledger_codes)]
                   .dropna(subset=["성분코드"])
                   .set_index("약품코드")["성분코드"])

    # 창(t-3~t+3)이 원장 구간 안에 완전히 들어오는 사건만 쓴다.
    usable = joined[
        (joined["report_month"] >= span[0] + WINDOW)
        & (joined["report_month"] <= span[1] - WINDOW)
    ]
    event_ings = usable.groupby("성분코드")["report_month"].min()
    event_ings = event_ings[event_ings.index.isin(set(code_to_ing.values))]
    print(f"사건 {len(joined):,}건 중 원장 구간 내 사용 가능 {len(usable):,}건")
    print(f"처리군이 되는 성분 {len(event_ings):,}개")

    if len(event_ings) < 5:
        print("\n표본이 5개 미만이다. 사건연구를 하지 않는다.")
        OUT_PATH.write_text(json.dumps({
            "status": "insufficient_events",
            "usable_events": int(len(usable)),
            "treated_ingredients": int(len(event_ings)),
            "ledger_span": [str(span[0]), str(span[1])],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"저장: {OUT_PATH}")
        return

    ing_to_month = {}
    for code, ing in code_to_ing.items():
        if ing in event_ings.index:
            ing_to_month[code] = event_ings[ing]

    ledger["event_month"] = ledger["물품코드"].map(ing_to_month)
    treated = ledger["event_month"].notna()
    print(f"처리군 물품코드 {ledger.loc[treated, '물품코드'].nunique():,} / "
          f"행 {treated.sum():,} ({treated.mean():.2%})")

    monthly = (ledger.assign(zero=ledger["마감재고량"].fillna(0).le(0))
               .groupby(["물품코드", "ym"], observed=True)
               .agg(zero_rate=("zero", "mean"),
                    issue=("정상출고량", "sum"),
                    pairs=("zero", "size"))
               .reset_index())
    monthly["event_month"] = monthly["물품코드"].map(ing_to_month)

    # 상대시점
    treat = monthly[monthly["event_month"].notna()].copy()
    treat["rel"] = (treat["ym"].astype("int64") - treat["event_month"].astype("int64"))
    treat = treat[treat["rel"].abs() <= WINDOW]

    # 대조군은 같은 달들의 전체 평균을 상대시점에 맞춰 쓴다.
    control_monthly = (monthly[monthly["event_month"].isna()]
                       .groupby("ym", observed=True)
                       .agg(zero_rate=("zero_rate", "mean"),
                            issue=("issue", "median")))

    rows = []
    for rel, block in treat.groupby("rel"):
        ctrl = control_monthly.reindex(block["ym"].unique())
        rows.append({
            "rel": int(rel),
            "n_item_months": int(len(block)),
            "treated_zero_rate": float(block["zero_rate"].mean()),
            "control_zero_rate": float(ctrl["zero_rate"].mean()),
            "treated_issue_median": float(block["issue"].median()),
            "control_issue_median": float(ctrl["issue"].median()),
        })
    table = pd.DataFrame(rows).sort_values("rel")
    table["gap_zero"] = table["treated_zero_rate"] - table["control_zero_rate"]

    print(f"\n{'상대월':<8}{'품목월':>9}{'처리 재고0':>11}{'대조 재고0':>11}{'격차':>10}")
    for _, row in table.iterrows():
        mark = " <- 보고월" if row["rel"] == 0 else ""
        print(f"  {int(row['rel']):>+3}{int(row['n_item_months']):>10,}"
              f"{row['treated_zero_rate']*100:>10.2f}%{row['control_zero_rate']*100:>10.2f}%"
              f"{row['gap_zero']*100:>+9.2f}%p{mark}")

    pre = table[table["rel"] < 0]["gap_zero"].mean()
    post = table[table["rel"] > 0]["gap_zero"].mean()
    did = post - pre
    print(f"\n  보고 전 격차 {pre*100:+.2f}%p / 보고 후 격차 {post*100:+.2f}%p")
    print(f"  DID = {did*100:+.2f}%p")

    from scipy import stats
    pre_block = treat[treat["rel"] < 0]["zero_rate"]
    post_block = treat[treat["rel"] > 0]["zero_rate"]
    statistic, p_value = stats.mannwhitneyu(pre_block, post_block)
    effect = 1 - (2 * statistic) / (len(pre_block) * len(post_block))
    print(f"  처리군 전후 Mann-Whitney p = {p_value:.4g} / 효과크기 {effect:+.3f}")

    verdict = ("보고 후 재고0 이 늘었다." if did > 0.01 and p_value < 0.05
               else "보고 전후로 유의한 변화가 없다.")
    print(f"\n  판정: {verdict}")

    OUT_PATH.write_text(json.dumps({
        "ledger_span": [str(span[0]), str(span[1])],
        "usable_events": int(len(usable)),
        "treated_ingredients": int(len(event_ings)),
        "treated_item_codes": int(ledger.loc[treated, "물품코드"].nunique()),
        "by_relative_month": table.to_dict("records"),
        "did_zero_rate": float(did),
        "mannwhitney_p": float(p_value),
        "rank_biserial": float(effect),
        "verdict": verdict,
        "caveats": [
            "보고일자는 보고 시점이지 실제 중단 시작일이 아니다",
            "처리군 배정이 무작위가 아니다. 평행추세는 t-3~t-1 로만 확인",
            "표본이 작아 검정력이 낮다",
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {OUT_PATH}")


if __name__ == "__main__":
    main()
