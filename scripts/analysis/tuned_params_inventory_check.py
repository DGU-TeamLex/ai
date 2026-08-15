"""Optuna 탐색값을 재고 성과로 판정한다 (ai#60 후속).

## 왜 또 재나

`tuned_params_test_check.py` 에서 TEST WAPE 가 38.121 -> 35.676 으로 2.444%p
개선됐다. 탐색이 보지 않은 면에서 이겼으므로 과적합은 아니다.

그런데 오늘 `inventory_kpi_vs_accuracy.py` 에서 **WAPE 순위가 재고 성과 순위와
어긋난다** 는 것을 우리 데이터로 확인했다(naive 가 WAPE 4위인데 충족률 3위).
그리고 `demand_window_inventory_check.py` 에서는 WAPE 로 이긴 후보(6개월 창)가
재고에서 뒤집혀 지는 것도 겪었다. **WAPE 개선만으로 채택하지 않는다.**

근거: Petropoulos et al. (2024), EJOR, M5 데이터 재고 시뮬레이션.

## 설계

두 모형의 TEST 구간 예측을 각각 정기발주 (R,S) 에 꽂는다.

    TEST   2025-07 ~ 2025-12 (6개월)
    정책   R=L=1개월, 보호기간 2개월, z=1.645, 손실판매
    sigma  TEST 이전 구간에서만 (평가면을 보지 않는다)

## baseline 을 어디서 읽나

`production_lgbm_params()` 는 이 탐색값이 적용되면서 tuned 로 바뀌었다. 그것을
baseline 으로 쓰면 tuned 끼리 비교하게 된다. 그래서 탐색 기록에 남아 있는
`baseline_params` (적용 전 운영값)를 읽는다.

## 한계

* 초기재고를 첫 목표재고로 둔다.
* 손실판매 가정이다.
* 보유비/품절비를 모르므로 단일 비용으로 합치지 않는다.
* 단일 시드(42).

실행:
    .venv/Scripts/python.exe scripts/analysis/tuned_params_inventory_check.py
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.config import (  # noqa: E402
    FEATURE_TABLE_PATH, MODEL_VARIANTS, OUTPUT_DIR, TARGET_COLUMN, TEST_START,
)
from src.modeling.training import (  # noqa: E402
    COMMODITY_COLUMNS, CURRENT_MONTH_COLUMNS, IDENTIFIER_COLUMNS,
    MODULE_C_COLUMNS, NEWS_COLUMNS, _fit_preprocessor,
    load_historical_training_policy,
    select_feature_columns, split_time_series, training_sample_weights,
    transform_features,
)
VARIANT = "stock_model_a_usage_only"
TUNED_PATH = OUTPUT_DIR / f"tuned_hyperparameters_{VARIANT}.json"
OUT_PATH = OUTPUT_DIR / "tuned_params_inventory_check.json"
PROTECTION = 2.0
Z_SERVICE = 1.645


def _wape(actual: np.ndarray, pred: np.ndarray) -> float:
    return float(np.abs(actual - pred).sum() / max(np.abs(actual).sum(), 1e-9) * 100)


def _simulate(target: np.ndarray, actual: np.ndarray) -> dict[str, float]:
    """정기발주 (R,S). target/actual 은 (계열 x 평가월) 행렬. 손실판매 가정.

    같은 시뮬레이터가 `inventory_kpi_vs_accuracy.py` 에도 있다. 두 분석이 각각
    다른 브랜치에 있어 지금은 복제해 둔다. 합쳐질 때 한쪽으로 모은다.
    """
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
    tuned = json.loads(TUNED_PATH.read_text(encoding="utf-8"))
    options = MODEL_VARIANTS[VARIANT]

    schema = pq.ParquetFile(FEATURE_TABLE_PATH).schema_arrow.names
    excluded = set(CURRENT_MONTH_COLUMNS) | set(IDENTIFIER_COLUMNS)
    if not options["use_news"]:
        excluded.update(NEWS_COLUMNS)
    if not options["use_commodity"]:
        excluded.update(COMMODITY_COLUMNS)
    if not options.get("use_module_c", False):
        excluded.update(MODULE_C_COLUMNS)
    columns = [c for c in schema if c not in excluded]
    # 재고 시뮬레이션은 계열 식별자가 있어야 한다. 피처로는 쓰지 않는다.
    for extra in ("stock_item_key", "historical_training_eligible"):
        if extra in schema:
            columns.append(extra)

    frame = pd.read_parquet(
        FEATURE_TABLE_PATH, columns=columns,
        filters=[(TARGET_COLUMN, ">=", 0), ("lag_1", ">=", 0)],
    )
    for column in frame.select_dtypes(include=["float64"]).columns:
        frame[column] = frame[column].astype("float32")
    frame = frame[frame["rolling_mean_3"].notna()]
    train, valid, test = split_time_series(frame)
    print(f"TRAIN {len(train):,} / VALID {len(valid):,} / TEST {len(test):,}")

    weight = float(load_historical_training_policy()["selected_historical_weight"])
    feature_cols = select_feature_columns(
        frame, options["use_news"], options["use_commodity"],
        options.get("use_module_c", False),
    )
    preprocessor = _fit_preprocessor(train, feature_cols)
    x_train = transform_features(train, preprocessor)
    x_test = transform_features(test, preprocessor)
    y_train = train[TARGET_COLUMN].to_numpy(float)
    sample_weight = training_sample_weights(train, weight)

    from lightgbm import LGBMRegressor

    objective = options.get("objective", "regression_l1")
    predictions = {}
    for label, parameters in (("baseline", dict(tuned["baseline_params"])),
                              ("tuned", dict(tuned["best_params"]))):
        model = LGBMRegressor(**parameters)
        model.fit(x_train, y_train, sample_weight=sample_weight)
        predictions[label] = np.clip(model.predict(x_test), 0, None)
        print(f"  {label} 학습 완료")

    # --- 계열 x 월 행렬로 접는다 -------------------------------------------
    panel = test[["stock_item_key", "year_month", TARGET_COLUMN]].copy()
    for label, values in predictions.items():
        panel[label] = values
    panel["ym"] = panel["year_month"].dt.to_period("M")
    months = sorted(panel["ym"].unique())
    print(f"TEST 구간 {months[0]} ~ {months[-1]} ({len(months)}개월)")

    actual_wide = panel.pivot_table(index="stock_item_key", columns="ym",
                                    values=TARGET_COLUMN, aggfunc="sum")
    actual_wide = actual_wide.reindex(columns=months)
    # 전 구간이 관측된 계열만. 중간이 비면 발주-입고 연쇄가 끊긴다.
    complete = actual_wide.notna().all(axis=1)
    actual_wide = actual_wide[complete]
    print(f"계열 {len(actual_wide):,}개 (전 구간 관측)")

    # sigma 는 TEST 이전 구간(TRAIN+VALID)의 계열별 표준편차. 평가면을 안 본다.
    history = pd.concat([train, valid])[["stock_item_key", "year_month", TARGET_COLUMN]]
    history["ym"] = history["year_month"].dt.to_period("M")
    hist_wide = (history.pivot_table(index="stock_item_key", columns="ym",
                                     values=TARGET_COLUMN, aggfunc="sum")
                 .reindex(actual_wide.index).fillna(0.0))
    sigma = hist_wide.to_numpy(float).std(axis=1, ddof=0)
    safety = Z_SERVICE * sigma * np.sqrt(PROTECTION)

    actual = actual_wide.to_numpy(float)
    results = {}
    for label in ("baseline", "tuned"):
        wide = (panel.pivot_table(index="stock_item_key", columns="ym",
                                  values=label, aggfunc="sum")
                .reindex(index=actual_wide.index, columns=months).fillna(0.0))
        forecast = wide.to_numpy(float)
        target = np.clip(forecast, 0, None) * PROTECTION + safety[:, None]
        kpi = _simulate(target, actual)
        kpi["wape"] = _wape(actual, forecast)
        results[label] = kpi

    print(f"\n{'모형':<10}{'WAPE':>10}{'충족률':>10}{'품절월':>10}{'평균재고':>11}")
    for label, kpi in results.items():
        mark = " (현행)" if label == "baseline" else ""
        print(f"  {label:<8}{kpi['wape']:>10.3f}{kpi['fill_rate']*100:>9.2f}%"
              f"{kpi['stockout_month_rate']*100:>9.2f}%{kpi['mean_on_hand']:>11.1f}{mark}")

    # --- 같은 재고를 z 로 샀다면 (효율 비교) --------------------------------
    #
    # 충족률이 올라도 재고가 같이 늘면 공짜가 아니다. baseline 의 z 만 올려
    # tuned 와 같은 재고 수준을 만들고 충족률을 맞대야 "예측이 좋아진 것" 인지
    # "재고를 더 쓴 것" 인지 갈린다. 앞서 배율 보정이 이 시험에서 z 에 졌다.
    baseline_wide = (panel.pivot_table(index="stock_item_key", columns="ym",
                                       values="baseline", aggfunc="sum")
                     .reindex(index=actual_wide.index, columns=months).fillna(0.0)
                     .to_numpy(float))
    z_curve = []
    for z in (1.645, 1.75, 1.88, 2.05, 2.33, 2.58):
        target = np.clip(baseline_wide, 0, None) * PROTECTION + (
            z * sigma * np.sqrt(PROTECTION)
        )[:, None]
        kpi = _simulate(target, actual)
        z_curve.append({"z": z, **kpi})
    z_stock = np.array([k["mean_on_hand"] for k in z_curve])
    z_fill = np.array([k["fill_rate"] for k in z_curve])
    order = np.argsort(z_stock)
    equivalent = float(np.interp(results["tuned"]["mean_on_hand"],
                                 z_stock[order], z_fill[order]))
    efficiency_gap = (results["tuned"]["fill_rate"] - equivalent) * 100
    print(f"\n  같은 재고({results['tuned']['mean_on_hand']:.1f})를 z 로 샀다면 "
          f"충족률 {equivalent*100:.2f}%")
    print(f"  tuned 충족률 {results['tuned']['fill_rate']*100:.2f}% "
          f"-> 효율 격차 {efficiency_gap:+.3f}%p")

    base, best = results["baseline"], results["tuned"]
    fill_gain = (best["fill_rate"] - base["fill_rate"]) * 100
    stockout_gain = (base["stockout_month_rate"] - best["stockout_month_rate"]) * 100
    stock_change = best["mean_on_hand"] - base["mean_on_hand"]
    print(f"\n  충족률 {fill_gain:+.3f}%p / 품절월 {-stockout_gain:+.3f}%p / "
          f"평균재고 {stock_change:+.1f} ({stock_change/base['mean_on_hand']*100:+.2f}%)")

    if fill_gain > 0.05 and efficiency_gap > 0.05:
        verdict = (f"충족률 {fill_gain:+.3f}%p 개선. 같은 재고를 z 로 사는 것보다 "
                   f"{efficiency_gap:+.3f}%p 낫다. 예측이 실제로 좋아진 것이다. 채택 권고.")
    elif fill_gain > 0.05:
        verdict = (f"충족률은 {fill_gain:+.3f}%p 오르나 같은 재고를 z 로 사는 것보다 "
                   f"{efficiency_gap:+.3f}%p 다. 재고를 더 쓴 효과와 구분되지 않는다.")
    elif fill_gain < -0.05:
        verdict = "재고 기준으로 오히려 나쁘다. WAPE 개선이 재고로 오지 않았다. 채택하지 않는다."
    else:
        verdict = "재고 성과 차이가 무시할 수준이다. WAPE 개선이 재고로 오지 않았다."
    print(f"  판정: {verdict}")

    OUT_PATH.write_text(json.dumps({
        "variant": VARIANT,
        "test_months": [str(m) for m in months],
        "series": int(len(actual_wide)),
        "policy": {"protection_months": PROTECTION, "z": Z_SERVICE,
                   "sigma_source": "train_valid_only", "unmet": "lost_sales"},
        "results": results,
        "fill_rate_gain_pp": fill_gain,
        "stockout_month_gain_pp": stockout_gain,
        "mean_on_hand_change": stock_change,
        "z_equivalence_curve": z_curve,
        "fill_rate_z_equivalent": equivalent,
        "efficiency_gap_pp": efficiency_gap,
        "verdict": verdict,
        "caveats": [
            "초기재고를 첫 목표재고로 둠",
            "손실판매 가정",
            "보유비/품절비 미상이라 단일 비용으로 합치지 않음",
            "단일 시드(42)",
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {OUT_PATH}")


if __name__ == "__main__":
    main()
