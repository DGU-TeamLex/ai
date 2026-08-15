"""관측 창 길이 — 병목이 표본 부족인가 표류인가 (ai#57 후속).

## 어디서 나온 질문인가

`institution_hierarchy_shrinkage.py` 에서 계층 축소가 졌다. 그런데 지는 방식이
말해주는 것이 있다.

    신뢰도 계수 k 의 내부검증 최적값 = 0.05  (격자 최솟값)
    Z = n/(n+0.05), n~20 이면 Z ~ 0.9975

**축소하지 말라는 뜻이다.** 상위 축(품목 전체·품목x지역군)의 정보가 개별 계열
추정을 도와주지 못한다. 즉 우리 문제는 **계열별 표본 부족이 아니다.**

같은 실행에서 이것도 나왔다.

    own_mean  (21개월 평균)  WAPE 48.984
    own_roll3 (직전 3개월)   WAPE 46.443     -2.541%p

**최근 3개월이 21개월보다 2.5%p 낫다.** 오래된 관측이 도움이 안 되는 것을 넘어
해가 된다. 이건 표본 부족이 아니라 **표류(drift)** 의 징후다.

그렇다면 방향은 정반대다. 자료를 더 모아 합치는 것이 아니라 **더 짧게 잘라야**
한다. 얼마나 짧아야 하는지는 재봐야 안다.

## 검정

    학습 2024-01 ~ 2025-09  /  평가 2025-10 ~ 2025-12

    roll1  직전 1개월
    roll2  직전 2개월
    roll3  직전 3개월  (현행)
    roll6  직전 6개월
    roll12 직전 12개월
    all    전체 평균

median 도 함께 본다. L1 손실의 최적해는 평균이 아니라 중앙값이다
(Koenker & Bassett 1978). 수요가 한쪽으로 긴 꼬리를 가지면 중앙값이 낫다.

## 한계

* 평가 3개월(2025-10~12)이다. 계절이 한쪽에 치우친다. 창 길이 최적값이
  계절마다 다를 수 있다.
* WAPE 로 잰다. 재고 성과와 순위가 어긋날 수 있음을 이미 확인했다.
  이기는 창이 나오면 재고 기준으로 다시 재야 한다.

실행:
    .venv/Scripts/python.exe scripts/analysis/demand_window_length.py
"""
import json
import os
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.analysis.institution_hierarchy_shrinkage import (  # noqa: E402
    EVAL_MONTHS, TRAIN_END, _monthly, _wape,
)

OUT_PATH = ROOT / "outputs" / "demand_window_length.json"
WINDOWS = [1, 2, 3, 6, 12, 21]


def main() -> None:
    monthly = _monthly()
    train = monthly[monthly["ym"] <= TRAIN_END]
    evaluation = monthly[monthly["ym"].isin(EVAL_MONTHS)]
    keys = ["보건기관코드_en", "물품코드"]
    print(f"학습 {len(train):,}행 / 평가 {len(evaluation):,}행")

    frame = evaluation.copy()
    for window in WINDOWS:
        block = train[train["ym"] > TRAIN_END - window]
        stats = (block.groupby(keys, observed=True)["정상출고량"]
                 .agg(**{f"mean{window}": "mean", f"med{window}": "median"}))
        frame = frame.join(stats, on=keys)

    # 가장 긴 창(전체)이 없으면 어떤 창도 없다. 그 행은 모두에서 제외한다.
    frame = frame.dropna(subset=[f"mean{WINDOWS[-1]}"])
    print(f"평가 대상 {len(frame):,}행\n")

    actual = frame["정상출고량"].to_numpy(float)
    results = {}
    for window in WINDOWS:
        for kind in ("mean", "med"):
            column = f"{kind}{window}"
            # 짧은 창에 값이 없는 계열은 가장 긴 창으로 메운다. 현행
            # mu_forecast 도 없으면 폴백하므로 같은 조건이다.
            values = frame[column].fillna(frame[f"mean{WINDOWS[-1]}"]).to_numpy(float)
            results[column] = _wape(actual, values)

    base = results["mean3"]
    print(f"{'창':<10}{'평균 WAPE':>12}{'현행 대비':>12}{'중앙값 WAPE':>14}{'현행 대비':>12}")
    for window in WINDOWS:
        mean_score, med_score = results[f"mean{window}"], results[f"med{window}"]
        label = f"{window}개월" + ("(현행)" if window == 3 else "")
        print(f"  {label:<10}{mean_score:>10.3f}{mean_score-base:>+11.3f}%p"
              f"{med_score:>13.3f}{med_score-base:>+11.3f}%p")

    winner = min(results, key=results.get)
    gain = base - results[winner]
    print(f"\n  최선 {winner}  WAPE {results[winner]:.3f}  현행 대비 {-gain:+.3f}%p")
    if gain > 0.3:
        print(f"  판정: 창 길이를 바꾸면 {gain:.3f}%p 개선된다.")
        print("        WAPE 기준이므로 재고 기준 재검증이 필요하다.")
    else:
        print(f"  판정: 창 길이로 얻을 것이 없다 ({gain:+.3f}%p).")

    OUT_PATH.write_text(json.dumps({
        "train_end": str(TRAIN_END),
        "eval_months": [str(m) for m in EVAL_MONTHS],
        "eval_rows": int(len(frame)),
        "wape": results,
        "current": "mean3",
        "winner": winner,
        "gain_pp": float(gain),
        "caveats": [
            "평가 3개월이라 계절이 한쪽에 치우친다",
            "WAPE 기준. 재고 성과 순위와 어긋날 수 있다",
            "짧은 창에 값이 없는 계열은 전체 평균으로 폴백했다",
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {OUT_PATH}")


if __name__ == "__main__":
    main()
