import logging
import pickle

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import OrdinalEncoder

from config import (
    CATEGORICAL_FEATURES,
    MODEL_DIR,
    OUTPUT_DIR,
    PROCESSED_DATA_DIR,
    TEST_START,
    TRAIN_END,
    VALID_END,
    VALID_START,
)
from features import create_features, get_model_feature_columns
from metrics import metrics_by_group, regression_metrics
from preprocess import load_and_aggregate_raw
from utils import ensure_dirs, setup_logging, write_json


LOGGER = logging.getLogger(__name__)


def _require_model_packages():
    try:
        from lightgbm import LGBMRegressor
    except ImportError as exc:
        raise ImportError("lightgbm is required. Install packages with: pip install -r requirements.txt") from exc

    try:
        from xgboost import XGBRegressor
    except ImportError as exc:
        raise ImportError("xgboost is required. Install packages with: pip install -r requirements.txt") from exc

    return LGBMRegressor, XGBRegressor


def _split_data(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    train = df[df["year_month"] <= pd.Timestamp(TRAIN_END)]
    valid = df[(df["year_month"] >= pd.Timestamp(VALID_START)) & (df["year_month"] <= pd.Timestamp(VALID_END))]
    test = df[df["year_month"] >= pd.Timestamp(TEST_START)]

    if train.empty or valid.empty or test.empty:
        raise ValueError(
            f"Empty split detected. train={len(train)}, valid={len(valid)}, test={len(test)}. "
            "Check data date coverage after feature generation."
        )
    return train, valid, test


def _fit_preprocessor(train: pd.DataFrame, feature_cols: list[str]):
    cat_cols = [col for col in CATEGORICAL_FEATURES if col in feature_cols]
    num_cols = [col for col in feature_cols if col not in cat_cols]

    encoder = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)
    encoder.fit(train[cat_cols].astype(str))

    imputer = SimpleImputer(strategy="median")
    imputer.fit(train[num_cols])
    return encoder, imputer, cat_cols, num_cols


def _transform(df: pd.DataFrame, encoder, imputer, cat_cols: list[str], num_cols: list[str]) -> np.ndarray:
    cat_values = encoder.transform(df[cat_cols].astype(str))
    num_values = imputer.transform(df[num_cols])
    return np.hstack([cat_values, num_values])


def _save_pickle(obj, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(obj, f)


def _feature_importance(model, feature_names: list[str]) -> pd.DataFrame:
    return (
        pd.DataFrame({"feature": feature_names, "importance": model.feature_importances_})
        .sort_values("importance", ascending=False)
        .reset_index(drop=True)
    )


def _evaluate_all(predictions: pd.DataFrame) -> dict:
    pred_cols = [
        "baseline_lag_1_pred",
        "baseline_lag_12_pred",
        "baseline_rolling_mean_3_pred",
        "lightgbm_pred",
        "xgboost_pred",
    ]
    return {
        pred_col: regression_metrics(predictions["actual_next_month_use"], predictions[pred_col])
        for pred_col in pred_cols
    }


def _build_group_metrics(predictions: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    frames = []
    for model_name, pred_col in {
        "lightgbm": "lightgbm_pred",
        "xgboost": "xgboost_pred",
    }.items():
        metrics = metrics_by_group(predictions, group_cols, pred_col)
        metrics.insert(0, "model", model_name)
        frames.append(metrics)
    return pd.concat(frames, ignore_index=True)


def run_pipeline() -> None:
    setup_logging()
    ensure_dirs(PROCESSED_DATA_DIR, OUTPUT_DIR, MODEL_DIR)
    LGBMRegressor, XGBRegressor = _require_model_packages()

    aggregated = load_and_aggregate_raw()
    dataset = create_features(aggregated)
    dataset = dataset.dropna(subset=["target_next_month"]).reset_index(drop=True)

    feature_cols = get_model_feature_columns(dataset)
    model_required = [
        "use_lag_1",
        "use_lag_12",
        "use_rolling_mean_3",
        "target_next_month",
    ]
    dataset = dataset.dropna(subset=model_required).reset_index(drop=True)
    dataset.to_csv(PROCESSED_DATA_DIR / "model_dataset.csv", index=False)
    LOGGER.info("Saved model dataset with %s rows", len(dataset))

    train, valid, test = _split_data(dataset)
    encoder, imputer, cat_cols, num_cols = _fit_preprocessor(train, feature_cols)
    transformed_feature_names = cat_cols + num_cols

    x_train = _transform(train, encoder, imputer, cat_cols, num_cols)
    y_train = train["target_next_month"].astype("float64")
    x_valid = _transform(valid, encoder, imputer, cat_cols, num_cols)
    y_valid = valid["target_next_month"].astype("float64")
    x_test = _transform(test, encoder, imputer, cat_cols, num_cols)

    lgbm = LGBMRegressor(
        objective="regression",
        n_estimators=700,
        learning_rate=0.04,
        num_leaves=63,
        subsample=0.9,
        colsample_bytree=0.9,
        random_state=42,
        n_jobs=-1,
    )
    lgbm.fit(x_train, y_train, eval_set=[(x_valid, y_valid)], eval_metric="l1")

    xgb = XGBRegressor(
        objective="reg:squarederror",
        n_estimators=700,
        learning_rate=0.04,
        max_depth=8,
        subsample=0.9,
        colsample_bytree=0.9,
        tree_method="hist",
        random_state=42,
        n_jobs=-1,
    )
    xgb.fit(x_train, y_train, eval_set=[(x_valid, y_valid)], verbose=False)

    predictions = test[["year_month", "SIDO", "MED_DEVICE_5", "target_next_month", "use_rolling_std_3"]].copy()
    predictions = predictions.rename(columns={"target_next_month": "actual_next_month_use"})
    predictions["baseline_lag_1_pred"] = test["use_lag_1"].clip(lower=0).to_numpy()
    predictions["baseline_lag_12_pred"] = test["use_lag_12"].clip(lower=0).to_numpy()
    predictions["baseline_rolling_mean_3_pred"] = test["use_rolling_mean_3"].clip(lower=0).to_numpy()
    predictions["lightgbm_pred"] = np.clip(lgbm.predict(x_test), 0, None)
    predictions["xgboost_pred"] = np.clip(xgb.predict(x_test), 0, None)
    predictions["lightgbm_error"] = predictions["actual_next_month_use"] - predictions["lightgbm_pred"]
    predictions["xgboost_error"] = predictions["actual_next_month_use"] - predictions["xgboost_pred"]

    recent_std = predictions["use_rolling_std_3"].fillna(0).clip(lower=0)
    predictions["predicted_required_stock_lgbm"] = predictions["lightgbm_pred"]
    predictions["conservative_required_stock_lgbm"] = predictions["lightgbm_pred"] + recent_std
    predictions["predicted_required_stock_xgb"] = predictions["xgboost_pred"]
    predictions["conservative_required_stock_xgb"] = predictions["xgboost_pred"] + recent_std
    # When real inventory, lead time, and service-level data become available,
    # extend these fields into safety-stock and reorder-point calculations.
    predictions = predictions.drop(columns=["use_rolling_std_3"])

    predictions.to_csv(OUTPUT_DIR / "predictions_test.csv", index=False)
    write_json(_evaluate_all(predictions), OUTPUT_DIR / "metrics_overall.json")
    _build_group_metrics(predictions, ["SIDO"]).to_csv(OUTPUT_DIR / "metrics_by_sido.csv", index=False)
    _build_group_metrics(predictions, ["MED_DEVICE_5"]).to_csv(OUTPUT_DIR / "metrics_by_item.csv", index=False)
    _build_group_metrics(predictions, ["SIDO", "MED_DEVICE_5"]).to_csv(
        OUTPUT_DIR / "metrics_by_sido_item.csv", index=False
    )

    _feature_importance(lgbm, transformed_feature_names).to_csv(
        OUTPUT_DIR / "feature_importance_lightgbm.csv", index=False
    )
    _feature_importance(xgb, transformed_feature_names).to_csv(
        OUTPUT_DIR / "feature_importance_xgboost.csv", index=False
    )

    bundle = {
        "encoder": encoder,
        "imputer": imputer,
        "cat_cols": cat_cols,
        "num_cols": num_cols,
        "feature_names": transformed_feature_names,
    }
    _save_pickle({"model": lgbm, "preprocess": bundle}, MODEL_DIR / "lightgbm_model.pkl")
    _save_pickle({"model": xgb, "preprocess": bundle}, MODEL_DIR / "xgboost_model.pkl")
    LOGGER.info("Pipeline finished")


if __name__ == "__main__":
    run_pipeline()

