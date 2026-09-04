"""창 길이 6개월이 재고 성과에서도 이기는가 (ai#57 후속).

## 왜 또 재나

`demand_window_length.py` 에서 관측 창을 3개월에서 6개월로 늘리면 WAPE 가
0.461%p 개선됐다. 그런데 `inventory_kpi_vs_accuracy.py` 에서 **WAPE 순위가 재고
성과 순위와 어긋난다** 는 것을 우리 데이터로 확인했다(naive 가 WAPE 4위인데
충족률 3위). 그래서 WAPE 개선만으로는 채택 근거가 안 된다.

Petropoulos et al. (2024, EJOR) 의 결론이 그것이다. 정확도 지표의 개선이 재고
성과 개선을 보장하지 않는다.

여기서는 같은 정기발주 (R,S) 시뮬레이션에 창 3개월/6개월/12개월 예측을 꽂아
넣고 **충족률·품절월·평균재고** 로 판정한다.

## 설계

    원장 2024-01 ~ 2025-12 월별 수요
    평가 2025-07 ~ 2025-12 (6개월), 매달 직전 창으로 예측 (확장 원점)
    정책 R=L=1개월, 보호기간 2개월, z=1.645, 손실판매

sigma 는 **학습구간(~2025-06)** 에서만 잡는다. 앞선 분석에서 평가구간 sigma 를
쓴 것이 낙관 편향이라 적었는데, 여기서는 그 편향을 없앨 수 있다.

## 한계

* 초기재고를 첫 목표재고로 둔다.
* 손실판매 가정이다.
* 보유비/품절비를 모르므로 단일 비용으로 합치지 않는다.

실행:
    .venv/Scripts/python.exe scripts/analysis/demand_window_inventory_check.py
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.analysis.institution_hierarchy_shrinkage import _monthly, _wape  # noqa: E402

OUT_PATH = ROOT / "outputs" / "demand_window_inventory_check.json"

EVAL_START = pd.Period("2025-07", freq="M")
WINDOWS = [3, 6, 12]
PROTECTION = 2.0
Z_SERVICE = 1.645


def _simulate(target: np.ndarray, actual: np.ndarray) -> dict[str, float]:
    """정기발주 (R,S). target/actual 은 (계열 x 평가월) 행렬."""
    n_series, n_months = actual.shape
    on_hand = target[:, 0].copy()
    pipeline = np.zeros(n_series)
    unmet = np.zeros(n_series)
    demand_total = np.zeros(n_series)
    on_hand_sum = np.zeros(n_series)
    stockout_months = np.zeros(n_series)

    for t in range(n_months):
        arriving, pipeline = pipeline, np.zeros(n_series)
        on_hand = on_hand + arriving
        pipeline = np.clip(target[:, t] - (on_hand + pipeline), 0, None)

        demand = actual[:, t]
        served = np.minimum(on_hand, demand)
        short = demand - served
        on_hand = on_hand - served

        unmet += short
        demand_total += demand
        on_hand_sum += on_hand
        stockout_months += (short > 0).astype(float)

    positive = demand_total > 0
    return {
        "fill_rate": float(1 - unmet[positive].sum() / demand_total[positive].sum()),
        "stockout_month_rate": float(stockout_months.sum() / (n_series * n_months)),
        "mean_on_hand": float(on_hand_sum.sum() / (n_series * n_months)),
    }


def main() -> None:
    monthly = _monthly()
    keys = ["보건기관코드_en", "물품코드"]
    months = sorted(monthly["ym"].unique())
    eval_months = [m for m in months if m >= EVAL_START]
    print(f"원장 {months[0]} ~ {months[-1]} / 평가 {eval_months[0]} ~ {eval_months[-1]}"
          f" ({len(eval_months)}개월)")

    wide = (monthly.pivot_table(index=keys, columns="ym",
                                values="정상출고량", aggfunc="sum")
            .reindex(columns=months).fillna(0.0))
    train_columns = [m for m in months if m < EVAL_START]
    # 학습구간에 한 번이라도 움직인 계열만. 전 구간 0 인 계열은 정책이 무의미하다.
    active = wide[train_columns].sum(axis=1) > 0
    wide = wide[active]
    print(f"계열 {len(wide):,}개 (학습구간 무활동 제외)")

    actual = wide[eval_months].to_numpy(float)
    # sigma 는 학습구간에서만. 평가구간을 보지 않는다.
    sigma = wide[train_columns].to_numpy(float).std(axis=1, ddof=0)
    safety = Z_SERVICE * sigma * np.sqrt(PROTECTION)

    results = {}
    for window in WINDOWS:
        # 확장 원점: 각 평가월 t 의 예측은 t 직전 window 개월 평균.
        forecast = np.empty_like(actual)
        for index, month in enumerate(eval_months):
            source = [m for m in months if month - window <= m < month]
            forecast[:, index] = wide[source].to_numpy(float).mean(axis=1)
        target = np.clip(forecast, 0, None) * PROTECTION + safety[:, None]
        kpi = _simulate(target, actual)
        kpi["wape"] = _wape(actual, forecast)
        results[f"mean{window}"] = kpi

    print(f"\n{'창':<10}{'WAPE':>10}{'충족률':>10}{'품절월':>10}{'평균재고':>11}")
    for label, kpi in results.items():
        mark = " (현행)" if label == "mean3" else ""
        print(f"  {label:<8}{kpi['wape']:>10.3f}{kpi['fill_rate']*100:>9.2f}%"
              f"{kpi['stockout_month_rate']*100:>9.2f}%{kpi['mean_on_hand']:>11.1f}{mark}")

    base = results["mean3"]
    best = results["mean6"]
    fill_gain = (best["fill_rate"] - base["fill_rate"]) * 100
    stock_change = best["mean_on_hand"] - base["mean_on_hand"]
    print(f"\n  6개월 대비 현행: 충족률 {fill_gain:+.3f}%p / "
          f"평균재고 {stock_change:+.1f} ({stock_change/base['mean_on_hand']*100:+.2f}%)")

    if fill_gain > 0.05 and stock_change <= 0:
        verdict = "6개월 창이 충족률을 올리면서 재고도 줄인다. 채택할 만하다."
    elif fill_gain > 0.05:
        verdict = (f"6개월 창이 충족률을 {fill_gain:+.3f}%p 올리나 재고가 "
                   f"{stock_change:+.1f} 늘어난다. 맞바꿈이다.")
    elif fill_gain < -0.05:
        verdict = "6개월 창은 재고 기준으로 오히려 나쁘다. WAPE 개선이 재고로 오지 않았다."
    else:
        verdict = "6개월 창의 재고 성과 차이는 무시할 수준이다."
    print(f"  판정: {verdict}")

    OUT_PATH.write_text(json.dumps({
        "eval_months": [str(m) for m in eval_months],
        "series": int(len(wide)),
        "policy": {"protection_months": PROTECTION, "z": Z_SERVICE,
                   "sigma_source": "train_only", "unmet": "lost_sales"},
        "results": results,
        "verdict": verdict,
        "caveats": [
            "초기재고를 첫 목표재고로 둠",
            "손실판매 가정",
            "보유비/품절비 미상이라 단일 비용으로 합치지 않음",
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {OUT_PATH}")


if __name__ == "__main__":
    main()
