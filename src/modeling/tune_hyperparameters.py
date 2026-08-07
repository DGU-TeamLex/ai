"""LightGBM 하이퍼파라미터를 Optuna로 탐색한다.

`training.py`의 `_build_estimator()`는 파라미터가 손으로 고정돼 있다
(n_estimators=160, learning_rate=0.05, num_leaves=31 ...). 이 스크립트는
같은 시간순 분할(TRAIN/VALID, config.py)로 WAPE를 목적함수 삼아 탐색하고,
가장 좋은 조합을 콘솔·JSON에 남긴다. TEST 구간은 절대 건드리지 않는다
(탐색에 쓰면 챔피언 선정의 최종 검증이 오염된다).

사용법(로컬에서 오래 돌리는 용도 — CI에 태우지 않는다):
    python -m src.modeling.tune_hyperparameters --variant stock_model_a_usage_only --n-trials 200
    python -m src.modeling.tune_hyperparameters --variant stock_model_a_usage_only --timeout 3600

탐색이 끝나면 outputs/tuned_hyperparameters_<variant>.json 에 best_params 를 저장한다.
그 값을 training.py 의 `_build_estimator()` `parameters` 딕셔너리에 그대로 옮겨 붙이면 된다
(이 스크립트가 자동으로 반영하지는 않는다 — 검토 후 손으로 반영하는 게 안전하다).
"""
import argparse
import json
import logging
import warnings

import numpy as np
import pandas as pd

from ..config import MODEL_VARIANTS, OUTPUT_DIR, RANDOM_STATE, TARGET_COLUMN
from ..utils import ensure_dirs, setup_logging
from .metrics import regression_metrics
from .training import (
    _fit_preprocessor,
    _load_feature_table,
    select_feature_columns,
    split_time_series,
    transform_features,
)


LOGGER = logging.getLogger(__name__)
TUNED_PARAMS_PATH_TEMPLATE = "tuned_hyperparameters_{variant}.json"
EARLY_STOPPING_ROUNDS = 50
MAX_ESTIMATORS = 2000


def _prepare_split(variant: str) -> tuple[pd.DataFrame, pd.DataFrame, list[str], str]:
    if variant not in MODEL_VARIANTS:
        raise ValueError(f"Unknown model variant: {variant}. Choices: {sorted(MODEL_VARIANTS)}")
    options = MODEL_VARIANTS[variant]
    feature_table = _load_feature_table(options)
    valid_rows = feature_table["rolling_mean_3"].notna()
    if not valid_rows.all():
        feature_table = feature_table.loc[valid_rows]
    train, valid, _test = split_time_series(feature_table)
    feature_cols = select_feature_columns(
        feature_table,
        use_news=options["use_news"],
        use_commodity=options["use_commodity"],
        use_module_c=options.get("use_module_c", False),
    )
    feature_cols = [column for column in feature_cols if not train[column].isna().all()]
    return train, valid, feature_cols, options["objective"]


def _suggest_params(trial, base_objective: str, random_state: int) -> dict:
    params = {
        "objective": base_objective,
        "n_estimators": MAX_ESTIMATORS,
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


def _make_objective(
    train: pd.DataFrame,
    valid: pd.DataFrame,
    feature_cols: list[str],
    base_objective: str,
):
    from lightgbm import LGBMRegressor, early_stopping, log_evaluation

    preprocess = _fit_preprocessor(train, feature_cols)
    x_train = transform_features(train, preprocess)
    y_train = train[TARGET_COLUMN].astype("float64")
    x_valid = transform_features(valid, preprocess)
    y_valid = valid[TARGET_COLUMN].astype("float64")
    categorical_feature = list(range(len(preprocess["cat_cols"])))

    def objective(trial) -> float:
        params = _suggest_params(trial, base_objective, RANDOM_STATE)
        model = LGBMRegressor(**params)
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="X does not have valid feature names")
            model.fit(
                x_train,
                y_train,
                eval_set=[(x_valid, y_valid)],
                eval_metric="l1",
                categorical_feature=categorical_feature,
                callbacks=[
                    early_stopping(EARLY_STOPPING_ROUNDS, verbose=False),
                    log_evaluation(0),
                ],
            )
            prediction = np.clip(
                model.predict(x_valid, num_iteration=model.best_iteration_),
                0,
                None,
            )
        wape = regression_metrics(y_valid, prediction)["WAPE"]
        trial.set_user_attr("best_iteration", int(model.best_iteration_))
        return wape

    return objective


def tune(variant: str, n_trials: int | None, timeout: int | None) -> dict:
    import optuna
    from optuna.samplers import TPESampler

    setup_logging()
    ensure_dirs(OUTPUT_DIR)
    LOGGER.info("Loading feature table for variant=%s", variant)
    train, valid, feature_cols, base_objective = _prepare_split(variant)
    LOGGER.info(
        "train=%s rows, valid=%s rows, %s features",
        len(train), len(valid), len(feature_cols),
    )

    objective = _make_objective(train, valid, feature_cols, base_objective)
    study = optuna.create_study(
        direction="minimize",
        sampler=TPESampler(seed=RANDOM_STATE),
        study_name=f"{variant}_wape",
    )
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study.optimize(objective, n_trials=n_trials, timeout=timeout, show_progress_bar=True)

    result = {
        "variant": variant,
        "objective": base_objective,
        "best_wape": study.best_value,
        "best_params": {**study.best_params, "n_estimators": MAX_ESTIMATORS},
        "best_iteration": study.best_trial.user_attrs.get("best_iteration"),
        "n_trials_completed": len(study.trials),
        "random_state": RANDOM_STATE,
    }
    output_path = OUTPUT_DIR / TUNED_PARAMS_PATH_TEMPLATE.format(variant=variant)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    LOGGER.info("Best WAPE: %.4f (n_estimators capped, actual best_iteration=%s)",
                result["best_wape"], result["best_iteration"])
    LOGGER.info("Saved: %s", output_path)
    LOGGER.info(
        "다음 단계: 위 best_params 를 training.py 의 _build_estimator() parameters 에 "
        "옮겨 붙이고 (n_estimators 는 best_iteration 근처 값으로), "
        "run_validation() 으로 TEST 구간 WAPE 를 다시 확인할 것.",
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Optuna로 LightGBM 하이퍼파라미터 탐색 (VALID 구간 WAPE 최소화, TEST 미사용)",
    )
    parser.add_argument(
        "--variant",
        default="stock_model_a_usage_only",
        choices=sorted(MODEL_VARIANTS),
        help="탐색할 모델 변형 (기본값: 현재 챔피언)",
    )
    parser.add_argument("--n-trials", type=int, default=200, help="탐색 시도 횟수")
    parser.add_argument(
        "--timeout", type=int, default=None,
        help="초 단위 시간 제한. 지정하면 n-trials보다 먼저 끝날 수 있음(둘 중 먼저 도달하는 조건)",
    )
    arguments = parser.parse_args()
    tune(arguments.variant, arguments.n_trials, arguments.timeout)
