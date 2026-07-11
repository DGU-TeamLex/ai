import json
import logging
import pickle

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import OrdinalEncoder

from ..config import (
    CATEGORICAL_FEATURES,
    FEATURE_TABLE_PATH,
    MODEL_DIR,
    MODEL_MANIFEST_PATH,
    MODEL_VALIDATION_REPORT_PATH,
    MODEL_VARIANTS,
    OUTPUT_DIR,
    RANDOM_STATE,
    TARGET_COLUMN,
    TEST_START,
    TRAIN_END,
    VALID_END,
    VALID_START,
)
from ..feature_engineering import run_feature_engineering
from ..utils import ensure_dirs, setup_logging
from .metrics import regression_metrics


LOGGER = logging.getLogger(__name__)

CURRENT_MONTH_COLUMNS = {
    "consumption_qty",
    "month_opening_stock",
    "month_end_stock",
    "minimum_stock",
    "maximum_stock",
    "average_stock",
    "purchase_in_qty",
    "transfer_in_qty",
    "return_in_qty",
    "inbound_qty",
    "transfer_out_qty",
    "return_out_qty",
    "disposal_qty",
    "auto_disposal_adjustment_qty",
    "correction_out_qty",
    "other_outbound_qty",
    "net_stock_change",
    "stockout_rate",
    "stock_observation_count",
    "stockout_observation_count",
    "negative_stock_observation_count",
    "unit_price_count",
    "average_unit_price",
}
IDENTIFIER_COLUMNS = {"stock_item_key", "item_name", "vendor_code", "first_date", "last_date"}

NEWS_COLUMNS = ["disease_news_risk", "supply_news_risk", "material_news_risk", "total_news_risk"]
COMMODITY_COLUMNS = ["commodity_risk", "material_return_30d", "material_volatility_30d"]


def _load_feature_table() -> pd.DataFrame:
    if not FEATURE_TABLE_PATH.exists():
        run_feature_engineering()
    return pd.read_csv(FEATURE_TABLE_PATH, parse_dates=["year_month"])


def split_time_series(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    train = df[df["year_month"] <= pd.Timestamp(TRAIN_END)]
    valid = df[(df["year_month"] >= pd.Timestamp(VALID_START)) & (df["year_month"] <= pd.Timestamp(VALID_END))]
    test = df[df["year_month"] >= pd.Timestamp(TEST_START)]
    if train.empty or valid.empty or test.empty:
        raise ValueError(f"Empty split detected: train={len(train)}, valid={len(valid)}, test={len(test)}")
    return train, valid, test


def select_feature_columns(df: pd.DataFrame, use_news: bool, use_commodity: bool) -> list[str]:
    excluded = CURRENT_MONTH_COLUMNS | IDENTIFIER_COLUMNS | {TARGET_COLUMN, "year_month"}
    if not use_news:
        excluded.update(NEWS_COLUMNS)
    if not use_commodity:
        excluded.update(COMMODITY_COLUMNS)
    return [col for col in df.columns if col not in excluded and not col.endswith("_pred")]


def _build_estimator():
    try:
        from lightgbm import LGBMRegressor

        return LGBMRegressor(
            objective="regression",
            n_estimators=120,
            learning_rate=0.06,
            num_leaves=31,
            subsample=0.9,
            colsample_bytree=0.9,
            random_state=RANDOM_STATE,
            n_jobs=4,
            force_row_wise=True,
            verbosity=-1,
        ), "lightgbm"
    except ImportError:
        return RandomForestRegressor(
            n_estimators=300,
            min_samples_leaf=2,
            random_state=RANDOM_STATE,
            n_jobs=-1,
        ), "random_forest"


def _fit_preprocessor(train: pd.DataFrame, feature_cols: list[str]) -> dict:
    cat_cols = [col for col in CATEGORICAL_FEATURES if col in feature_cols]
    num_cols = [col for col in feature_cols if col not in cat_cols]
    encoder = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)
    encoder.fit(train[cat_cols].astype(str))
    imputer = SimpleImputer(strategy="median")
    imputer.fit(train[num_cols])
    return {"encoder": encoder, "imputer": imputer, "cat_cols": cat_cols, "num_cols": num_cols}


def transform_features(df: pd.DataFrame, preprocess: dict) -> np.ndarray:
    cat_values = preprocess["encoder"].transform(df[preprocess["cat_cols"]].astype(str))
    num_values = preprocess["imputer"].transform(df[preprocess["num_cols"]])
    return np.hstack([cat_values, num_values])


def train_model_variant(
    name: str,
    train: pd.DataFrame,
    valid: pd.DataFrame,
    feature_cols: list[str],
) -> dict:
    feature_cols = [column for column in feature_cols if not train[column].isna().all()]
    preprocess = _fit_preprocessor(train, feature_cols)
    x_train = transform_features(train, preprocess)
    x_valid = transform_features(valid, preprocess)
    y_train = train[TARGET_COLUMN].astype("float64")
    y_valid = valid[TARGET_COLUMN].astype("float64")

    estimator, algorithm = _build_estimator()
    if algorithm == "lightgbm":
        estimator.fit(x_train, y_train, eval_set=[(x_valid, y_valid)], eval_metric="l1")
    else:
        estimator.fit(x_train, y_train)

    valid_pred = np.clip(estimator.predict(x_valid), 0, None)
    return {
        "name": name,
        "algorithm": algorithm,
        "model": estimator,
        "preprocess": preprocess,
        "feature_cols": feature_cols,
        "feature_names": preprocess["cat_cols"] + preprocess["num_cols"],
        "validation_metrics": regression_metrics(y_valid, valid_pred),
    }


def save_model_bundle(bundle: dict) -> None:
    path = MODEL_DIR / f"{bundle['name']}.pkl"
    with path.open("wb") as f:
        pickle.dump(bundle, f)


def run_training() -> None:
    setup_logging()
    ensure_dirs(MODEL_DIR, OUTPUT_DIR)
    feature_table = _load_feature_table().dropna(subset=[TARGET_COLUMN, "lag_1", "rolling_mean_3"])
    train, valid, _ = split_time_series(feature_table)

    manifest = []
    for name, options in MODEL_VARIANTS.items():
        feature_cols = select_feature_columns(feature_table, **options)
        bundle = train_model_variant(name, train, valid, feature_cols)
        save_model_bundle(bundle)
        row = {
            "model": name,
            "algorithm": bundle["algorithm"],
            "n_features": len(bundle["feature_cols"]),
            **bundle["validation_metrics"],
        }
        manifest.append(row)
        LOGGER.info("Saved %s with validation WAPE=%s", name, row.get("WAPE"))

    report = pd.DataFrame(manifest).sort_values("WAPE", na_position="last")
    report.to_csv(MODEL_VALIDATION_REPORT_PATH, index=False)
    with MODEL_MANIFEST_PATH.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    run_training()
