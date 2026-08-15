"""기관코드 계층(지역군)이 수요예측을 개선하는가 (ai#57).

## 왜 이걸 보나

어제 배율 보정 3종이 다 실패한 뒤 "튜닝 여지가 없다" 로 정리했다. 그런데 그건
**행 단위 사후 보정** 만 해본 결과다. 계층 구조를 쓰는 것은 성격이 다르다.

근거: Hyndman, Ahmed, Athanasopoulos & Shang (2011), "Optimal combination
forecasts for hierarchical time series", CSDA 55(9) — 계층 조정이 개별 예측보다
일관되게 낫다. 우리 상황이 그 전제에 맞는다. 기관 3,530 x 품목이라 계열당
관측이 짧고(중앙값 6개월), 짧은 계열일수록 자기 평균이 못 미덥다.

이미 리드타임에는 Bühlmann-Straub 축소를 쓰고 있다. 같은 논리를 수요에 적용해
보는 것이다.

## 기관코드에서 무엇을 뽑았나

`보건기관코드_en` 은 5자리 고정이고 첫 글자가 16종(D~S)이다. 뒤 4자리에는
':' ';' '<' 가 섞이는데, 이는 ASCII 에서 '9' 바로 다음 문자들이다. 즉 원본
숫자에 일정한 자리이동을 준 가명코드로 보인다.

**원본을 복원하지 않는다.** 필요한 것은 "같은 첫 글자끼리 묶인다" 는 사실뿐이고,
그 묶음이 무엇을 뜻하는지는 몰라도 계층 검정이 된다. 가명화를 풀 이유가 없다.

    region_group = 기관코드[0]      16개 군, 기관 18~570개
    (실제로 무엇인지는 특정하지 않는다)

## 검정

    학습 2024-01 ~ 2025-09  /  평가 2025-10 ~ 2025-12

    A. own_roll3      직전 3개월 자기 평균 (현행 mu_forecast 방식)
    B. item_global    품목 전체 평균으로 완전 축소
    C. shrink_global  자기 평균 <-> 품목 전체 평균 신뢰도 가중
    D. shrink_region  자기 평균 <-> 품목x지역군 평균 신뢰도 가중
    E. roll3_global   **roll3** <-> 품목 전체 roll3 신뢰도 가중
    F. roll3_region   **roll3** <-> 품목x지역군 roll3 신뢰도 가중

    Z = n / (n + k),  n = 관측 개월수

k 는 학습구간에서만 고른다. 평가구간을 보고 고르면 과적합이다.

E·F 를 따로 두는 이유: C·D 는 **장기 자기평균** 을 축소한다. 현행 방식은
장기평균이 아니라 roll3 다. 장기평균은 그 자체로 roll3 보다 나쁘므로(아래
실측 +2.5%p), C·D 가 지는 것이 계층 탓인지 장기평균 탓인지 구분되지 않는다.
같은 개체 추정량(roll3) 위에서 축소해야 계층의 기여만 분리된다.

## 한계

* 지역군이 무엇인지 모른다. 지역이 아닐 수도 있다. 검정은 "첫 글자로 묶으면
  설명력이 있는가" 까지만 말한다.
* WAPE 로 잰다. `inventory_kpi_vs_accuracy.py` 에서 WAPE 순위가 재고 성과
  순위와 어긋난다는 것을 확인했으므로, 이기면 재고 기준으로 다시 재야 한다.
* 3개월 평가다. 계절이 한쪽에 치우친다.

실행:
    .venv/Scripts/python.exe scripts/analysis/institution_hierarchy_shrinkage.py
"""
import json
import os
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

LEDGER = os.environ.get(
    "LEDGER_2024_2025", os.path.expanduser("~/Desktop/ledger_2024_2025.parquet")
)
OUT_PATH = ROOT / "outputs" / "institution_hierarchy_shrinkage.json"

TRAIN_END = pd.Period("2025-09", freq="M")
EVAL_MONTHS = [pd.Period(m, freq="M") for m in ("2025-10", "2025-11", "2025-12")]
K_GRID = [0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 8, 12, 20]


def _monthly() -> pd.DataFrame:
    frame = pd.read_parquet(
        LEDGER, columns=["보건기관코드_en", "물품코드", "재고마감일", "정상출고량"]
    )
    frame["date"] = pd.to_datetime(
        frame["재고마감일"].astype(str).str.strip(), format="%Y%m%d", errors="coerce"
    )
    frame = frame.dropna(subset=["date", "보건기관코드_en", "물품코드"])
    frame["ym"] = frame["date"].dt.to_period("M")
    monthly = (frame.groupby(["보건기관코드_en", "물품코드", "ym"], observed=True)
               ["정상출고량"].sum().clip(lower=0).reset_index())
    monthly["region"] = monthly["보건기관코드_en"].str[0]
    return monthly


def _wape(actual: np.ndarray, pred: np.ndarray) -> float:
    return float(np.abs(actual - pred).sum() / max(np.abs(actual).sum(), 1e-9) * 100)


def main() -> None:
    monthly = _monthly()
    print(f"월별 수요 {len(monthly):,}행 / 기관 {monthly['보건기관코드_en'].nunique():,} "
          f"/ 품목 {monthly['물품코드'].nunique():,}")
    sizes = monthly.groupby("region")["보건기관코드_en"].nunique().sort_values()
    print(f"지역군 {len(sizes)}개, 기관수 {sizes.min()}~{sizes.max()}\n")

    train = monthly[monthly["ym"] <= TRAIN_END]
    evaluation = monthly[monthly["ym"].isin(EVAL_MONTHS)]
    print(f"학습 {len(train):,}행 (~{TRAIN_END}) / 평가 {len(evaluation):,}행")

    # --- 학습구간에서 각 축의 대표값 ---------------------------------------
    own = (train.groupby(["보건기관코드_en", "물품코드"], observed=True)["정상출고량"]
           .agg(own_mean="mean", n_months="size"))
    # 현행 방식과 맞추기 위해 직전 3개월 평균도 만든다.
    last3 = train[train["ym"] > TRAIN_END - 3]
    roll3 = (last3.groupby(["보건기관코드_en", "물품코드"], observed=True)["정상출고량"]
             .mean().rename("roll3"))
    item_global = train.groupby("물품코드", observed=True)["정상출고량"].mean().rename("item_mean")
    item_region = (train.groupby(["물품코드", "region"], observed=True)["정상출고량"]
                   .mean().rename("item_region_mean"))
    # roll3 축소를 위한 상위 축의 roll3. 같은 3개월 창에서 뽑는다.
    item_roll3 = last3.groupby("물품코드", observed=True)["정상출고량"].mean().rename("item_roll3")
    item_region_roll3 = (last3.groupby(["물품코드", "region"], observed=True)["정상출고량"]
                         .mean().rename("item_region_roll3"))

    frame = (evaluation.join(own, on=["보건기관코드_en", "물품코드"])
             .join(roll3, on=["보건기관코드_en", "물품코드"])
             .join(item_global, on="물품코드")
             .join(item_region, on=["물품코드", "region"])
             .join(item_roll3, on="물품코드")
             .join(item_region_roll3, on=["물품코드", "region"]))
    # 학습구간에 없던 계열은 어떤 예측기도 값을 못 낸다. 공정하게 제외한다.
    frame = frame.dropna(subset=["own_mean", "item_mean", "item_region_mean"])
    frame["roll3"] = frame["roll3"].fillna(frame["own_mean"])
    print(f"평가 대상 {len(frame):,}행 "
          f"(학습에 없던 계열 {len(evaluation)-len(frame):,}행 제외)\n")

    actual = frame["정상출고량"].to_numpy(float)
    n_months = frame["n_months"].to_numpy(float)
    own_mean = frame["own_mean"].to_numpy(float)

    results = {
        "own_roll3": _wape(actual, frame["roll3"].to_numpy(float)),
        "own_mean": _wape(actual, own_mean),
        "item_global": _wape(actual, frame["item_mean"].to_numpy(float)),
    }

    # --- k 는 학습구간 안에서만 고른다 -------------------------------------
    # 학습구간을 다시 8/1 로 쪼개 마지막 달을 내부 검증으로 쓴다.
    inner_end = TRAIN_END - 1
    inner_train = monthly[monthly["ym"] <= inner_end]
    inner_eval = monthly[monthly["ym"] == TRAIN_END]
    i_own = (inner_train.groupby(["보건기관코드_en", "물품코드"], observed=True)["정상출고량"]
             .agg(own_mean="mean", n_months="size"))
    i_last3 = inner_train[inner_train["ym"] > inner_end - 3]
    i_roll3 = (i_last3.groupby(["보건기관코드_en", "물품코드"], observed=True)["정상출고량"]
               .mean().rename("roll3"))
    i_item = inner_train.groupby("물품코드", observed=True)["정상출고량"].mean().rename("item_mean")
    i_region = (inner_train.groupby(["물품코드", "region"], observed=True)["정상출고량"]
                .mean().rename("item_region_mean"))
    i_item_roll3 = i_last3.groupby("물품코드", observed=True)["정상출고량"].mean().rename("item_roll3")
    i_region_roll3 = (i_last3.groupby(["물품코드", "region"], observed=True)["정상출고량"]
                      .mean().rename("item_region_roll3"))
    inner = (inner_eval.join(i_own, on=["보건기관코드_en", "물품코드"])
             .join(i_roll3, on=["보건기관코드_en", "물품코드"])
             .join(i_item, on="물품코드")
             .join(i_region, on=["물품코드", "region"])
             .join(i_item_roll3, on="물품코드")
             .join(i_region_roll3, on=["물품코드", "region"])
             .dropna(subset=["own_mean", "item_mean", "item_region_mean"]))
    inner["roll3"] = inner["roll3"].fillna(inner["own_mean"])
    inner["item_roll3"] = inner["item_roll3"].fillna(inner["item_mean"])
    inner["item_region_roll3"] = inner["item_region_roll3"].fillna(inner["item_region_mean"])

    def _best_k(individual_column: str, prior_column: str) -> tuple[float, float]:
        a = inner["정상출고량"].to_numpy(float)
        o = inner[individual_column].to_numpy(float)
        p = inner[prior_column].to_numpy(float)
        n = inner["n_months"].to_numpy(float)
        scores = []
        for k in K_GRID:
            z = n / (n + k)
            scores.append((_wape(a, z * o + (1 - z) * p), k))
        best_score, best = min(scores)
        return best, best_score

    frame["item_roll3"] = frame["item_roll3"].fillna(frame["item_mean"])
    frame["item_region_roll3"] = frame["item_region_roll3"].fillna(frame["item_region_mean"])

    for label, individual, column in (
        ("shrink_global", "own_mean", "item_mean"),
        ("shrink_region", "own_mean", "item_region_mean"),
        ("roll3_global", "roll3", "item_roll3"),
        ("roll3_region", "roll3", "item_region_roll3"),
    ):
        k, inner_score = _best_k(individual, column)
        z = n_months / (n_months + k)
        pred = z * frame[individual].to_numpy(float) + (1 - z) * frame[column].to_numpy(float)
        results[label] = _wape(actual, pred)
        print(f"  {label}: k={k} (내부검증 WAPE {inner_score:.3f})")

    print(f"\n{'예측기':<16}{'WAPE':>10}{'현행 대비':>12}")
    base = results["own_roll3"]
    for label, score in sorted(results.items(), key=lambda x: x[1]):
        print(f"  {label:<14}{score:>10.3f}{score - base:>+11.3f}%p")

    winner = min(results, key=results.get)
    gain = base - results[winner]
    print()
    if winner in ("shrink_region", "roll3_region") and gain > 0.1:
        print(f"  판정: 지역군 계층이 이긴다. {gain:.3f}%p 개선.")
        print("        WAPE 기준이므로 재고 기준으로 다시 재야 한다.")
    elif winner == "shrink_global" and gain > 0.1:
        print(f"  판정: 축소는 이기지만 **지역군은 기여하지 않는다.**")
        print(f"        품목 전체 평균으로 축소하는 것만으로 {gain:.3f}%p 를 얻는다.")
        print(f"        지역군 차이 {results['shrink_global'] - results['shrink_region']:+.3f}%p")
    else:
        print(f"  판정: 계층 축소가 현행을 이기지 못한다 (최선 {winner}, {gain:+.3f}%p).")

    OUT_PATH.write_text(json.dumps({
        "train_end": str(TRAIN_END),
        "eval_months": [str(m) for m in EVAL_MONTHS],
        "eval_rows": int(len(frame)),
        "region_groups": int(len(sizes)),
        "wape": results,
        "winner": winner,
        "gain_vs_own_roll3_pp": float(gain),
        "caveats": [
            "지역군이 실제로 무엇인지 특정하지 않았다. 첫 글자 묶음일 뿐이다.",
            "WAPE 로 쟀다. 재고 성과 순위와 어긋날 수 있다.",
            "평가 3개월이라 계절이 한쪽에 치우친다.",
            "학습구간에 없던 계열은 제외했다.",
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {OUT_PATH}")


if __name__ == "__main__":
    main()
