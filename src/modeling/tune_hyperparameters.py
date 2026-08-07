"""LightGBM 하이퍼파라미터를 Optuna로 탐색한다.

`training.py`의 `_build_estimator()`는 파라미터가 손으로 고정돼 있다
(n_estimators=160, learning_rate=0.05, num_leaves=31 ...). 이 스크립트는
챔피언 선정과 **같은 조건**으로 탐색해서 기존 검증 결과와 직접 비교 가능한
수치를 남긴다.

같은 조건이란 다음을 뜻한다(ai#60 리뷰 반영).

- `config.VALIDATION_FOLDS` 의 rolling 2-fold (2025_q1, 2025_q2)
- fold 마다 `select_training_window(train_end, historical_weight)` 로 학습셋 구성
- 과거자료(2018~19) 가중치는 `historical_training_policy.json` 정책값을
  `training_sample_weights()` 로 그대로 적용
- objective 는 fold 예측을 합친 **통합 WAPE**

TEST 구간(`TEST_START` 이후)은 절대 건드리지 않는다. `_load_feature_table()`
자체가 `VALID_END` 까지만 읽으므로 테이블에 애초에 들어오지 않는다.

사용법(로컬에서 오래 돌리는 용도 — CI에 태우지 않는다):
    python -m src.modeling.tune_hyperparameters --variant stock_model_a_usage_only --n-trials 60
    python -m src.modeling.tune_hyperparameters --variant stock_model_a_usage_only --timeout 3600

탐색이 끝나면 outputs/tuned_hyperparameters_<variant>.json 에 결과를 저장한다.
`selected_n_estimators` 가 실제 반영할 트리 수이고 `search_max_estimators` 는
탐색 중 상한(early stopping 용)일 뿐이다. 둘을 혼동하면 안 된다.
이 스크립트는 training.py 를 자동으로 고치지 않는다 — 검토 후 손으로 반영한다.
"""
import argparse
import json
import logging
import time
import warnings

import numpy as np
import pandas as pd

from ..config import (
    HISTORICAL_TRAIN_END,
    HISTORICAL_TRAIN_START,
    MODEL_VARIANTS,
    OUTPUT_DIR,
    RANDOM_STATE,
    TARGET_COLUMN,
    TRAIN_START,
    VALIDATION_FOLDS,
)
from ..utils import ensure_dirs, setup_logging
from .metrics import regression_metrics
from .standardized_history import MAPPING_VERSION
from .training import (
    _fit_preprocessor,
    _load_feature_table,
    _missing_external_signal,
    load_historical_training_policy,
    select_feature_columns,
    select_training_window,
    training_sample_weights,
    transform_features,
)


LOGGER = logging.getLogger(__name__)
TUNED_PARAMS_PATH_TEMPLATE = "tuned_hyperparameters_{variant}.json"
TUNING_VERSION = "hyperparameter-tuning-v2.0-rolling-folds"
EARLY_STOPPING_ROUNDS = 50
SEARCH_MAX_ESTIMATORS = 2000


def _fold_frames(
    feature_table: pd.DataFrame,
    historical_weight: float,
) -> list[dict]:
    """VALIDATION_FOLDS 각각에 대해 (train, valid) 를 만든다.

    train 은 운영 학습과 같은 select_training_window() 로 고르므로
    fold 의 train_end 이후 데이터는 학습에 들어가지 않는다.
    """
    folds = []
    for spec in VALIDATION_FOLDS:
        train = select_training_window(
            feature_table,
            spec["train_end"],
            historical_weight,
        )
        valid = feature_table[
            feature_table["year_month"].between(
                pd.Timestamp(spec["valid_start"]),
                pd.Timestamp(spec["valid_end"]),
            )
        ]
        if train.empty or valid.empty:
            raise ValueError(
                f"Empty fold detected: {spec['fold']} "
                f"train={len(train)}, valid={len(valid)}"
            )
        # 학습 구간과 검증 구간이 겹치면 누수다. 명시적으로 막는다.
        overlap = train["year_month"].ge(pd.Timestamp(spec["valid_start"]))
        if bool(overlap.any()):
            raise ValueError(
                f"Fold {spec['fold']} train window overlaps its validation window"
            )
        folds.append({"spec": spec, "train": train, "valid": valid})
    return folds


def _prepare_folds(variant: str) -> tuple[list[dict], list[str], str, float]:
    if variant not in MODEL_VARIANTS:
        raise ValueError(f"Unknown model variant: {variant}. Choices: {sorted(MODEL_VARIANTS)}")
    options = MODEL_VARIANTS[variant]
    feature_table = _load_feature_table(options)
    valid_rows = feature_table["rolling_mean_3"].notna()
    if not valid_rows.all():
        feature_table = feature_table.loc[valid_rows]

    # 운영 학습과 같은 품질 게이트. 외부 신호가 전부 0 이면 그 variant 는
    # 의미 없는 조합을 최적화하게 되므로 탐색 자체를 막는다(fail closed).
    missing = _missing_external_signal(feature_table, options)
    if missing is not None:
        raise ValueError(f"Cannot tune variant {variant}: {missing}")

    historical_weight = float(
        load_historical_training_policy().get("selected_historical_weight", 0.0)
    )
    folds = _fold_frames(feature_table, historical_weight)
    feature_cols = select_feature_columns(
        feature_table,
        use_news=options["use_news"],
        use_commodity=options["use_commodity"],
        use_module_c=options.get("use_module_c", False),
    )
    first_train = folds[0]["train"]
    feature_cols = [column for column in feature_cols if not first_train[column].isna().all()]
    return folds, feature_cols, options["objective"], historical_weight


def _suggest_params(trial, base_objective: str, random_state: int) -> dict:
    params = {
        "objective": base_objective,
        "n_estimators": SEARCH_MAX_ESTIMATORS,
        "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
        "num_leaves": trial.suggest_int("num_leaves", 15, 255),
        "min_child_samples": trial.suggest_int("min_child_samples", 20, 400),
        "subsample": trial.suggest_float("subsample", 0.6, 1.0),
        "subsample_freq": 1,
        "colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 1.0),
        "reg_lambda": trial.suggest_float("reg_lambda", 1e-3, 10.0, log=True),
        "reg_alpha": trial.suggest_float("reg_alpha", 1e-3, 10.0, log=True),
        "random_state": random_state,
        "n_jobs": 4,
        "force_col_wise": True,
        "verbosity": -1,
    }
    if base_objective == "tweedie":
        params["tweedie_variance_power"] = trial.suggest_float(
            "tweedie_variance_power", 1.05, 1.9,
        )
    return params


def _prepare_matrices(
    folds: list[dict],
    feature_cols: list[str],
    historical_weight: float,
) -> list[dict]:
    """fold 마다 전처리기를 fit 하고 행렬을 미리 만들어 둔다.

    전처리기는 해당 fold 의 train 으로만 fit 한다(검증 구간 정보 누수 방지).
    trial 마다 다시 만들지 않으므로 탐색이 크게 빨라진다.
    """
    prepared = []
    for fold in folds:
        train, valid = fold["train"], fold["valid"]
        pre = _fit_preprocessor(train, feature_cols)
        prepared.append(
            {
                "fold": fold["spec"]["fold"],
                "spec": fold["spec"],
                "x_train": transform_features(train, pre),
                "y_train": train[TARGET_COLUMN].astype("float64"),
                "w_train": training_sample_weights(train, historical_weight),
                "x_valid": transform_features(valid, pre),
                "y_valid": valid[TARGET_COLUMN].astype("float64"),
                "cat_idx": list(range(len(pre["cat_cols"]))),
                "train_rows": int(len(train)),
                "valid_rows": int(len(valid)),
                "historical_rows": int(
                    train["year_month"].le(pd.Timestamp(HISTORICAL_TRAIN_END)).sum()
                ),
            }
        )
        LOGGER.info(
            "fold %s: train=%s (historical=%s), valid=%s",
            prepared[-1]["fold"],
            f"{prepared[-1]['train_rows']:,}",
            f"{prepared[-1]['historical_rows']:,}",
            f"{prepared[-1]['valid_rows']:,}",
        )
    return prepared


def _fit_and_predict(params: dict, fold: dict) -> tuple[np.ndarray, int]:
    from lightgbm import LGBMRegressor, early_stopping, log_evaluation

    model = LGBMRegressor(**params)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model.fit(
            fold["x_train"],
            fold["y_train"],
            sample_weight=fold["w_train"],
            eval_set=[(fold["x_valid"], fold["y_valid"])],
            eval_metric="l1",
            categorical_feature=fold["cat_idx"],
            callbacks=[
                early_stopping(EARLY_STOPPING_ROUNDS, verbose=False),
                log_evaluation(0),
            ],
        )
        prediction = np.clip(
            model.predict(fold["x_valid"], num_iteration=model.best_iteration_),
            0,
            None,
        )
    return prediction, int(model.best_iteration_)


def _evaluate_params(params: dict, prepared: list[dict]) -> dict:
    """모든 fold 를 학습·평가하고 fold 예측을 합쳐 통합 WAPE 를 낸다."""
    per_fold = []
    actuals, predictions = [], []
    iterations = []
    for fold in prepared:
        prediction, best_iteration = _fit_and_predict(params, fold)
        metrics = regression_metrics(fold["y_valid"], prediction)
        per_fold.append(
            {
                "fold": fold["fold"],
                "valid_start": fold["spec"]["valid_start"],
                "valid_end": fold["spec"]["valid_end"],
                "train_rows": fold["train_rows"],
                "historical_rows": fold["historical_rows"],
                "validation_rows": fold["valid_rows"],
                "best_iteration": best_iteration,
                "WAPE": float(metrics["WAPE"]),
                "MAE": float(metrics["MAE"]),
                "RMSE": float(metrics["RMSE"]),
                "BIAS": float(metrics["BIAS"]),
                "BIAS_PCT": float(metrics["BIAS_PCT"]),
            }
        )
        actuals.append(np.asarray(fold["y_valid"], dtype="float64"))
        predictions.append(np.asarray(prediction, dtype="float64"))
        iterations.append(best_iteration)

    combined = regression_metrics(
        pd.Series(np.concatenate(actuals)),
        np.concatenate(predictions),
    )
    return {
        "per_fold": per_fold,
        "combined": {key: float(value) for key, value in combined.items()},
        "selected_n_estimators": int(max(iterations)),
        "fold_best_iterations": iterations,
    }


def _make_objective(prepared: list[dict], base_objective: str, n_trials: int | None):
    state = {"start": time.monotonic(), "best": None}

    def objective(trial) -> float:
        params = _suggest_params(trial, base_objective, RANDOM_STATE)
        result = _evaluate_params(params, prepared)
        wape = result["combined"]["WAPE"]

        trial.set_user_attr("per_fold", result["per_fold"])
        trial.set_user_attr("combined", result["combined"])
        trial.set_user_attr("selected_n_estimators", result["selected_n_estimators"])

        if state["best"] is None or wape < state["best"]:
            state["best"] = wape
        elapsed = time.monotonic() - state["start"]
        done = trial.number + 1
        eta = ""
        if n_trials:
            remaining = max(n_trials - done, 0)
            eta = f" | ETA {elapsed / done * remaining / 60:.1f}min"
        LOGGER.info(
            "[trial %s/%s] WAPE=%.4f (fold %s) | best=%.4f | %.1fmin elapsed%s",
            done,
            n_trials if n_trials else "?",
            wape,
            ", ".join(f"{f['fold']}={f['WAPE']:.2f}" for f in result["per_fold"]),
            state["best"],
            elapsed / 60,
            eta,
        )
        return wape

    return objective


def _baseline_params(base_objective: str) -> dict:
    """training.py::_build_estimator() 의 현재 고정값. 같은 조건 비교용."""
    params = {
        "objective": base_objective,
        "n_estimators": 160,
        "learning_rate": 0.05,
        "num_leaves": 31,
        "min_child_samples": 100,
        "subsample": 0.9,
        "colsample_bytree": 0.9,
        "reg_lambda": 0.1,
        "random_state": RANDOM_STATE,
        "n_jobs": 4,
        "force_col_wise": True,
        "histogram_pool_size": 256,
        "verbosity": -1,
    }
    if base_objective == "tweedie":
        params["tweedie_variance_power"] = 1.3
    return params


def tune(variant: str, n_trials: int | None, timeout: int | None) -> dict:
    import optuna
    from optuna.samplers import TPESampler

    setup_logging()
    ensure_dirs(OUTPUT_DIR)
    LOGGER.info("Loading feature table for variant=%s", variant)
    folds, feature_cols, base_objective, historical_weight = _prepare_folds(variant)
    LOGGER.info(
        "%s folds, %s features, historical_weight=%s",
        len(folds), len(feature_cols), historical_weight,
    )
    prepared = _prepare_matrices(folds, feature_cols, historical_weight)

    LOGGER.info("Evaluating current fixed parameters as baseline ...")
    baseline = _evaluate_params(_baseline_params(base_objective), prepared)
    LOGGER.info(
        "BASELINE combined WAPE=%.4f (fold %s)",
        baseline["combined"]["WAPE"],
        ", ".join(f"{f['fold']}={f['WAPE']:.2f}" for f in baseline["per_fold"]),
    )

    study = optuna.create_study(
        direction="minimize",
        sampler=TPESampler(seed=RANDOM_STATE),
        study_name=f"{variant}_wape",
    )
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study.optimize(
        _make_objective(prepared, base_objective, n_trials),
        n_trials=n_trials,
        timeout=timeout,
        show_progress_bar=False,
    )

    best = study.best_trial
    selected_n_estimators = int(best.user_attrs["selected_n_estimators"])
    best_params = {**study.best_params, "n_estimators": selected_n_estimators}
    delta = best.value - baseline["combined"]["WAPE"]

    result = {
        "version": TUNING_VERSION,
        "variant": variant,
        "objective": base_objective,
        "selection_metric": "combined WAPE over rolling validation folds",
        "selection_did_not_use_test": True,
        "validation_folds": [
            {k: v for k, v in spec.items()} for spec in VALIDATION_FOLDS
        ],
        "historical_weight_used": historical_weight,
        "historical_training_policy_version": load_historical_training_policy().get("version"),
        "standard_item_mapping_version": MAPPING_VERSION,
        "training_period": {
            "historical": f"{HISTORICAL_TRAIN_START}~{HISTORICAL_TRAIN_END}",
            "current_start": TRAIN_START,
        },
        "random_state": RANDOM_STATE,
        "n_trials_completed": len(study.trials),
        "search_max_estimators": SEARCH_MAX_ESTIMATORS,
        "selected_n_estimators": selected_n_estimators,
        "best_params": best_params,
        "best_combined_metrics": best.user_attrs["combined"],
        "best_per_fold": best.user_attrs["per_fold"],
        "baseline_params": _baseline_params(base_objective),
        "baseline_combined_metrics": baseline["combined"],
        "baseline_per_fold": baseline["per_fold"],
        "wape_delta_pp": float(delta),
    }
    output_path = OUTPUT_DIR / TUNED_PARAMS_PATH_TEMPLATE.format(variant=variant)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    LOGGER.info(
        "BEST combined WAPE=%.4f vs BASELINE %.4f (%+.4f%%p), selected_n_estimators=%s",
        best.value,
        baseline["combined"]["WAPE"],
        delta,
        selected_n_estimators,
    )
    LOGGER.info("Saved: %s", output_path)
    LOGGER.info(
        "다음 단계: best_params 를 training.py 의 _build_estimator() 에 반영할 때 "
        "n_estimators 는 selected_n_estimators(%s)를 쓴다. search_max_estimators(%s)가 아니다. "
        "반영 후 파라미터를 고정한 뒤에만 TEST 구간을 1회 평가한다.",
        selected_n_estimators,
        SEARCH_MAX_ESTIMATORS,
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Optuna로 LightGBM 하이퍼파라미터 탐색 "
            "(rolling validation folds 통합 WAPE 최소화, TEST 미사용)"
        ),
    )
    parser.add_argument(
        "--variant",
        default="stock_model_a_usage_only",
        choices=sorted(MODEL_VARIANTS),
        help="탐색할 모델 변형 (기본값: 현재 챔피언)",
    )
    parser.add_argument("--n-trials", type=int, default=60, help="탐색 시도 횟수")
    parser.add_argument(
        "--timeout", type=int, default=None,
        help="초 단위 시간 제한. 지정하면 n-trials보다 먼저 끝날 수 있음(둘 중 먼저 도달하는 조건)",
    )
    arguments = parser.parse_args()
    tune(arguments.variant, arguments.n_trials, arguments.timeout)
