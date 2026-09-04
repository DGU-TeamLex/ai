"""Optuna 탐색값을 TEST 로 판정한다 (ai#60).

## 상태

`src/modeling/tune_hyperparameters.py` 와 실데이터 탐색 결과
`outputs/tuned_hyperparameters_stock_model_a_usage_only.json` 은 **이미 dev 에
들어와 있다.** 그런데 `training.py::production_lgbm_params()` 는 탐색 당시
baseline(n_estimators=160, lr=0.05, num_leaves=31)이었다. **적용만 안 된
상태였다.** 이 검정을 통과해 지금은 적용돼 있다.

탐색 결과는 이렇다(4개 회전 검증 폴드 합산).

    baseline  WAPE 38.587
    tuned     WAPE 36.961
    delta          -1.626%p

탐색은 `selection_did_not_use_test: True` 로 TEST 를 보지 않았다. 그래서 TEST 는
아직 오염되지 않은 판정면이다. **여기서 이겨야 채택한다.**

## 왜 검증 폴드 결과만으로 채택하면 안 되나

60회 시행으로 검증 폴드 WAPE 를 최소화한 값이다. 그 면에서 좋은 것은 당연하다.
탐색에 쓴 면에서의 우위는 채택 근거가 못 된다. 이것이 TEST 를 따로 떼어둔 이유다.

## 무엇을 재나

    TRAIN  2018-01~2019-12(가중) + 2024-01~2024-12
    VALID  2025-01~2025-06     (참고용. 탐색에 쓰인 면)
    TEST   2025-07~           (판정면)

WAPE 와 함께 BIAS 도 본다. 그리고 `inventory_kpi_vs_accuracy.py` 에서 확인한
대로 **WAPE 순위가 재고 성과 순위와 어긋날 수 있으므로**, 이기더라도 재고
기준 재검증을 별도로 걸어야 한다. 이 스크립트는 거기까지 하지 않는다.

## baseline 을 어디서 읽나

`production_lgbm_params()` 는 이 탐색값이 적용되면서 tuned 로 바뀌었다. 그것을
baseline 으로 쓰면 tuned 끼리 비교하게 된다. 그래서 탐색 기록에 남아 있는
`baseline_params` (적용 전 운영값)를 읽는다.

## 한계

* 단일 시드(42)다. 시드 변동은 보지 않는다.
* n_estimators 1999 는 baseline 160 의 12배다. 학습·추론 비용이 늘어난다.
  그 비용은 WAPE 에 안 잡힌다.

실행:
    .venv/Scripts/python.exe scripts/analysis/tuned_params_test_check.py
"""
import json
import pathlib
import sys
import time

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.config import (  # noqa: E402
    FEATURE_TABLE_PATH, MODEL_VARIANTS, OUTPUT_DIR, TARGET_COLUMN, TEST_START,
    VALID_END, VALID_START,
)
from src.modeling.training import (  # noqa: E402
    _fit_preprocessor, load_historical_training_policy,
    select_feature_columns, split_time_series, training_sample_weights,
    transform_features,
)

VARIANT = "stock_model_a_usage_only"
TUNED_PATH = OUTPUT_DIR / f"tuned_hyperparameters_{VARIANT}.json"
OUT_PATH = OUTPUT_DIR / "tuned_params_test_check.json"


def _metrics(actual: np.ndarray, pred: np.ndarray) -> dict[str, float]:
    error = pred - actual
    denominator = max(np.abs(actual).sum(), 1e-9)
    return {
        "N": int(len(actual)),
        "WAPE": float(np.abs(error).sum() / denominator * 100),
        "MAE": float(np.abs(error).mean()),
        "RMSE": float(np.sqrt((error ** 2).mean())),
        "BIAS": float(error.mean()),
        "BIAS_PCT": float(error.sum() / denominator * 100),
    }


def main() -> None:
    tuned = json.loads(TUNED_PATH.read_text(encoding="utf-8"))
    print(f"탐색 결과: {TUNED_PATH.name}")
    print(f"  검증 폴드 WAPE  baseline {tuned['baseline_combined_metrics']['WAPE']:.3f}"
          f" -> tuned {tuned['best_combined_metrics']['WAPE']:.3f}"
          f"  ({tuned['wape_delta_pp']:+.3f}%p)")
    print(f"  TEST 미사용: {tuned['selection_did_not_use_test']}\n")

    options = MODEL_VARIANTS[VARIANT]
    # 학습용 로더는 VALID_END 까지만 읽는다. TEST 판정을 하려면 전 구간이 필요하다.
    from src.modeling.training import (
        COMMODITY_COLUMNS, CURRENT_MONTH_COLUMNS, IDENTIFIER_COLUMNS,
        MODULE_C_COLUMNS, NEWS_COLUMNS,
    )
    schema = pq.ParquetFile(FEATURE_TABLE_PATH).schema_arrow.names
    excluded = set(CURRENT_MONTH_COLUMNS) | set(IDENTIFIER_COLUMNS)
    if not options["use_news"]:
        excluded.update(NEWS_COLUMNS)
    if not options["use_commodity"]:
        excluded.update(COMMODITY_COLUMNS)
    if not options.get("use_module_c", False):
        excluded.update(MODULE_C_COLUMNS)
    columns = [c for c in schema if c not in excluded]
    if "historical_training_eligible" in schema:
        columns.append("historical_training_eligible")

    frame = pd.read_parquet(
        FEATURE_TABLE_PATH, columns=columns,
        filters=[(TARGET_COLUMN, ">=", 0), ("lag_1", ">=", 0)],
    )
    for column in frame.select_dtypes(include=["float64"]).columns:
        frame[column] = frame[column].astype("float32")
    frame = frame[frame["rolling_mean_3"].notna()]
    print(f"피처표 {len(frame):,}행")

    train, valid, test = split_time_series(frame)
    print(f"  TRAIN {len(train):,} / VALID {len(valid):,} ({VALID_START}~{VALID_END})"
          f" / TEST {len(test):,} ({TEST_START}~)\n")

    weight = float(load_historical_training_policy()["selected_historical_weight"])
    feature_cols = select_feature_columns(
        frame, options["use_news"], options["use_commodity"],
        options.get("use_module_c", False),
    )
    preprocessor = _fit_preprocessor(train, feature_cols)
    x_train = transform_features(train, preprocessor)
    x_valid = transform_features(valid, preprocessor)
    x_test = transform_features(test, preprocessor)
    y_train = train[TARGET_COLUMN].to_numpy(float)
    sample_weight = training_sample_weights(train, weight)

    from lightgbm import LGBMRegressor

    objective = options.get("objective", "regression_l1")
    candidates = {
        "baseline": dict(tuned["baseline_params"]),
        "tuned": dict(tuned["best_params"]),
    }

    results = {}
    for label, parameters in candidates.items():
        started = time.time()
        model = LGBMRegressor(**parameters)
        model.fit(x_train, y_train, sample_weight=sample_weight)
        elapsed = time.time() - started
        entry = {
            "params_n_estimators": int(parameters.get("n_estimators", -1)),
            "fit_seconds": round(elapsed, 1),
            "VALID": _metrics(valid[TARGET_COLUMN].to_numpy(float),
                              np.clip(model.predict(x_valid), 0, None)),
            "TEST": _metrics(test[TARGET_COLUMN].to_numpy(float),
                             np.clip(model.predict(x_test), 0, None)),
        }
        results[label] = entry
        print(f"  {label:<9} 학습 {elapsed:>6.1f}s  "
              f"VALID WAPE {entry['VALID']['WAPE']:.3f}  "
              f"TEST WAPE {entry['TEST']['WAPE']:.3f}  "
              f"TEST BIAS% {entry['TEST']['BIAS_PCT']:+.2f}")

    valid_delta = results["tuned"]["VALID"]["WAPE"] - results["baseline"]["VALID"]["WAPE"]
    test_delta = results["tuned"]["TEST"]["WAPE"] - results["baseline"]["TEST"]["WAPE"]
    cost = results["tuned"]["fit_seconds"] / max(results["baseline"]["fit_seconds"], 1e-9)

    print(f"\n  VALID delta {valid_delta:+.3f}%p  (탐색에 쓰인 면)")
    print(f"  TEST  delta {test_delta:+.3f}%p  <- 판정")
    print(f"  학습시간 {cost:.1f}배")

    if test_delta < -0.3:
        verdict = f"TEST 에서 {-test_delta:.3f}%p 개선. 채택 후보다. 재고 기준 재검증이 남았다."
    elif test_delta < 0:
        verdict = f"TEST 개선폭이 {-test_delta:.3f}%p 로 작다. 학습비용 {cost:.1f}배를 감안해 판단해야 한다."
    else:
        verdict = f"TEST 에서 {test_delta:+.3f}%p 로 개선이 없다. 검증 폴드 우위는 과적합이었다. 채택하지 않는다."
    print(f"\n  판정: {verdict}")

    OUT_PATH.write_text(json.dumps({
        "variant": VARIANT,
        "tuning_validation_delta_pp": tuned["wape_delta_pp"],
        "results": results,
        "valid_delta_pp": valid_delta,
        "test_delta_pp": test_delta,
        "fit_time_ratio": cost,
        "verdict": verdict,
        "caveats": [
            "단일 시드(42). 시드 변동 미확인",
            "학습·추론 비용 증가는 WAPE 에 잡히지 않는다",
            "WAPE 기준. 재고 성과 재검증이 별도로 필요하다",
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {OUT_PATH}")


if __name__ == "__main__":
    main()
