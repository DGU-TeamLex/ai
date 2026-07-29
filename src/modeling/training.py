import argparse
import gc
import json
import logging
import pickle
import subprocess
import sys
import warnings

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer

from ..config import (
    CATEGORICAL_FEATURES,
    FEATURE_TABLE_PATH,
    HISTORICAL_TRAIN_END,
    HISTORICAL_TRAIN_START,
    HISTORICAL_TRAINING_POLICY_PATH,
    MODEL_CV_REPORT_PATH,
    MODEL_DIR,
    MODEL_MANIFEST_PATH,
    MODEL_VALIDATION_REPORT_PATH,
    MODEL_VARIANTS,
    OUTPUT_DIR,
    RANDOM_STATE,
    TARGET_COLUMN,
    TEST_START,
    TRAIN_END,
    TRAIN_START,
    VALID_END,
    VALIDATION_FOLDS,
    VALID_START,
)
from ..feature_engineering import run_feature_engineering
from ..utils import ensure_dirs, setup_logging
from .baseline import BASELINE_PREDICTION_COLUMNS, add_baseline_predictions
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
IDENTIFIER_COLUMNS = {
    "stock_item_key",
    "item_code",
    "item_name",
    "standard_item_key",
    "vendor_code",
    "first_date",
    "last_date",
    "forecast_month",
    "series_segment_id",
    "demand_qty",
    "negative_consumption_flag",
    "local_item_key",
    "historical_training_eligible",
    "standardization_confidence",
}

NEWS_COLUMNS = ["disease_news_risk", "supply_news_risk", "material_news_risk", "total_news_risk"]
COMMODITY_COLUMNS = ["commodity_risk", "material_return_30d", "material_volatility_30d"]
MODULE_C_COLUMNS = [
    "module_c_demand_risk",
    "module_c_supply_news_risk",
    "module_c_material_news_risk",
    "module_c_market_price_risk",
    "module_c_trade_risk",
    "module_c_supply_risk",
    "module_c_total_risk",
    "module_c_signal_confidence",
]


def _load_feature_table(
    model_options: dict[str, object] | None = None,
) -> pd.DataFrame:
    if not FEATURE_TABLE_PATH.exists():
        run_feature_engineering()
    schema_columns = pq.ParquetFile(FEATURE_TABLE_PATH).schema_arrow.names
    excluded = CURRENT_MONTH_COLUMNS | IDENTIFIER_COLUMNS
    if model_options is not None:
        if not model_options["use_news"]:
            excluded.update(NEWS_COLUMNS)
        if not model_options["use_commodity"]:
            excluded.update(COMMODITY_COLUMNS)
        if not model_options.get("use_module_c", False):
            excluded.update(MODULE_C_COLUMNS)
    training_columns = [column for column in schema_columns if column not in excluded]
    if "historical_training_eligible" in schema_columns:
        training_columns.append("historical_training_eligible")
    frame = pd.read_parquet(
        FEATURE_TABLE_PATH,
        columns=training_columns,
        filters=[
            ("year_month", "<=", pd.Timestamp(VALID_END)),
            (TARGET_COLUMN, ">=", 0),
            ("lag_1", ">=", 0),
        ],
    )
    for column in frame.select_dtypes(include=["float64"]).columns:
        frame[column] = frame[column].astype("float32")
    for column in frame.select_dtypes(include=["int64"]).columns:
        frame[column] = pd.to_numeric(frame[column], downcast="integer")
    return frame


def _count_eligible_test_rows() -> int:
    test_rows = pq.read_table(
        FEATURE_TABLE_PATH,
        columns=["year_month"],
        filters=[
            ("year_month", ">=", pd.Timestamp(TEST_START)),
            (TARGET_COLUMN, ">=", 0),
            ("lag_1", ">=", 0),
        ],
    )
    return int(test_rows.num_rows)


def split_time_series(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    historical_eligible = df.get(
        "historical_training_eligible",
        pd.Series(True, index=df.index),
    ).fillna(False)
    historical = (
        df["year_month"].between(
            pd.Timestamp(HISTORICAL_TRAIN_START),
            pd.Timestamp(HISTORICAL_TRAIN_END),
        )
        & historical_eligible
    )
    current = df["year_month"].between(
        pd.Timestamp(TRAIN_START),
        pd.Timestamp(TRAIN_END),
    )
    train = df[historical | current]
    valid = df[(df["year_month"] >= pd.Timestamp(VALID_START)) & (df["year_month"] <= pd.Timestamp(VALID_END))]
    test = df[df["year_month"] >= pd.Timestamp(TEST_START)]
    if train.empty or valid.empty or test.empty:
        raise ValueError(f"Empty split detected: train={len(train)}, valid={len(valid)}, test={len(test)}")
    return train, valid, test


def load_historical_training_policy() -> dict[str, object]:
    if not HISTORICAL_TRAINING_POLICY_PATH.exists():
        return {
            "version": "historical-training-disabled",
            "selected_historical_weight": 0.0,
            "status": "policy_missing",
        }
    with HISTORICAL_TRAINING_POLICY_PATH.open(
        "r",
        encoding="utf-8",
    ) as file:
        policy = json.load(file)
    weight = float(policy.get("selected_historical_weight", 0.0))
    if not 0.0 <= weight <= 1.0:
        raise ValueError("Historical training weight must be between 0 and 1")
    return policy


def select_training_window(
    df: pd.DataFrame,
    current_end: str,
    historical_weight: float,
) -> pd.DataFrame:
    current = df["year_month"].between(
        pd.Timestamp(TRAIN_START),
        pd.Timestamp(current_end),
    )
    if historical_weight <= 0:
        return df[current]
    historical_eligible = df.get(
        "historical_training_eligible",
        pd.Series(False, index=df.index),
    ).fillna(False)
    historical = (
        df["year_month"].between(
            pd.Timestamp(HISTORICAL_TRAIN_START),
            pd.Timestamp(HISTORICAL_TRAIN_END),
        )
        & historical_eligible
    )
    return df[current | historical]


def training_sample_weights(
    train: pd.DataFrame,
    historical_weight: float,
) -> np.ndarray:
    historical = train["year_month"].between(
        pd.Timestamp(HISTORICAL_TRAIN_START),
        pd.Timestamp(HISTORICAL_TRAIN_END),
    )
    return np.where(historical, historical_weight, 1.0).astype(
        "float32",
        copy=False,
    )


def select_feature_columns(
    df: pd.DataFrame,
    use_news: bool,
    use_commodity: bool,
    use_module_c: bool,
) -> list[str]:
    excluded = CURRENT_MONTH_COLUMNS | IDENTIFIER_COLUMNS | {TARGET_COLUMN, "year_month"}
    if not use_news:
        excluded.update(NEWS_COLUMNS)
    if not use_commodity:
        excluded.update(COMMODITY_COLUMNS)
    if not use_module_c:
        excluded.update(MODULE_C_COLUMNS)
    return [col for col in df.columns if col not in excluded and not col.endswith("_pred")]


def _build_estimator(objective: str):
    try:
        from lightgbm import LGBMRegressor

        parameters = {
            "objective": objective,
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
        if objective == "tweedie":
            parameters["tweedie_variance_power"] = 1.3
        return LGBMRegressor(**parameters), f"lightgbm_{objective}"
    except ImportError:
        loss = "poisson" if objective in {"poisson", "tweedie"} else "absolute_error"
        return HistGradientBoostingRegressor(
            loss=loss,
            max_iter=160,
            learning_rate=0.06,
            max_leaf_nodes=31,
            min_samples_leaf=100,
            random_state=RANDOM_STATE,
        ), f"hist_gradient_boosting_{loss}"


def _fit_preprocessor(train: pd.DataFrame, feature_cols: list[str]) -> dict:
    cat_cols = [col for col in CATEGORICAL_FEATURES if col in feature_cols]
    num_cols = [col for col in feature_cols if col not in cat_cols]
    category_values = {
        column: pd.Index(pd.unique(train[column].dropna()))
        for column in cat_cols
    }
    imputer = SimpleImputer(strategy="median")
    imputer.fit(train[num_cols])
    return {
        "encoder": None,
        "category_values": category_values,
        "imputer": imputer,
        "cat_cols": cat_cols,
        "num_cols": num_cols,
    }


def transform_features(df: pd.DataFrame, preprocess: dict) -> np.ndarray:
    encoder = preprocess.get("encoder")
    if encoder is not None:
        cat_values = encoder.transform(
            df[preprocess["cat_cols"]].astype(str)
        )
    else:
        category_values = preprocess["category_values"]
        cat_values = (
            np.column_stack(
                [
                    pd.Categorical(
                        df[column],
                        categories=category_values[column],
                    ).codes.astype("float32", copy=False)
                    for column in preprocess["cat_cols"]
                ]
            )
            if preprocess["cat_cols"]
            else np.empty((len(df), 0), dtype="float32")
        )
    num_values = preprocess["imputer"].transform(df[preprocess["num_cols"]])
    return np.hstack([cat_values, num_values]).astype("float32", copy=False)


def train_model_variant(
    name: str,
    train: pd.DataFrame,
    feature_cols: list[str],
    objective: str,
    valid: pd.DataFrame | None = None,
    sample_weight: np.ndarray | None = None,
) -> dict:
    feature_cols = [column for column in feature_cols if not train[column].isna().all()]
    preprocess = _fit_preprocessor(train, feature_cols)
    x_train = transform_features(train, preprocess)
    y_train = train[TARGET_COLUMN].astype("float64")

    estimator, algorithm = _build_estimator(objective)
    if algorithm.startswith("lightgbm") and valid is not None:
        x_valid = transform_features(valid, preprocess)
        y_valid = valid[TARGET_COLUMN].astype("float64")
        estimator.fit(
            x_train,
            y_train,
            sample_weight=sample_weight,
            eval_set=[(x_valid, y_valid)],
            eval_metric="l1",
            categorical_feature=list(range(len(preprocess["cat_cols"]))),
        )
    elif algorithm.startswith("lightgbm"):
        estimator.fit(
            x_train,
            y_train,
            sample_weight=sample_weight,
            categorical_feature=list(range(len(preprocess["cat_cols"]))),
        )
    else:
        estimator.fit(x_train, y_train, sample_weight=sample_weight)

    bundle = {
        "name": name,
        "algorithm": algorithm,
        "objective": objective,
        "model": estimator,
        "preprocess": preprocess,
        "feature_cols": feature_cols,
        "feature_names": preprocess["cat_cols"] + preprocess["num_cols"],
    }
    if valid is not None:
        if algorithm.startswith("lightgbm"):
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="X does not have valid feature names")
                raw_prediction = estimator.predict(x_valid, validate_features=False)
        else:
            raw_prediction = estimator.predict(x_valid)
        valid_pred = np.clip(raw_prediction, 0, None)
        bundle["validation_prediction"] = valid_pred
        bundle["validation_metrics"] = regression_metrics(y_valid, valid_pred)
    return bundle


def save_model_bundle(bundle: dict) -> None:
    path = MODEL_DIR / f"{bundle['name']}.pkl"
    with path.open("wb") as f:
        pickle.dump(bundle, f)


def _missing_external_signal(df: pd.DataFrame, options: dict) -> str | None:
    if options["use_news"] and not df[NEWS_COLUMNS].fillna(0).abs().to_numpy().any():
        return "news risk features contain no non-zero observations"
    if options["use_commodity"] and not df[COMMODITY_COLUMNS].fillna(0).abs().to_numpy().any():
        return "commodity risk features contain no non-zero observations"
    if options.get("use_module_c", False) and not df[MODULE_C_COLUMNS].fillna(0).abs().to_numpy().any():
        return "Module C risk features contain no non-zero observations"
    return None


def _baseline_validation_rows(valid: pd.DataFrame) -> list[dict]:
    required = [
        TARGET_COLUMN,
        "lag_1",
        "rolling_mean_3",
        "rolling_median_3",
        "rolling_mean_6",
        "same_month_last_year",
        "expanding_mean",
    ]
    baseline_predictions = add_baseline_predictions(valid[required])
    rows = []
    for prediction_column in BASELINE_PREDICTION_COLUMNS:
        rows.append(
            {
                "model": prediction_column.removesuffix("_pred"),
                "method_type": "baseline",
                "algorithm": "deterministic",
                "objective": None,
                "status": "ready",
                "skip_reason": None,
                "uses_news": False,
                "uses_commodity": False,
                "uses_module_c": False,
                "n_features": 1,
                **regression_metrics(
                    baseline_predictions[TARGET_COLUMN],
                    baseline_predictions[prediction_column],
                ),
            }
        )
    return rows


def _json_records(frame: pd.DataFrame) -> list[dict]:
    records = []
    for record in frame.to_dict(orient="records"):
        records.append(
            {
                key: None if pd.isna(value) else value.item() if hasattr(value, "item") else value
                for key, value in record.items()
            }
        )
    return records


def run_validation() -> list[str]:
    setup_logging()
    ensure_dirs(MODEL_DIR, OUTPUT_DIR)
    feature_table = _load_feature_table()
    historical_policy = load_historical_training_policy()
    historical_weight = float(
        historical_policy["selected_historical_weight"]
    )
    valid_rows = feature_table["rolling_mean_3"].notna()
    if not valid_rows.all():
        feature_table = feature_table.loc[valid_rows]
    validation = feature_table[
        feature_table["year_month"].between(pd.Timestamp(VALID_START), pd.Timestamp(VALID_END))
    ]
    test_rows = _count_eligible_test_rows()
    if validation.empty or test_rows == 0:
        raise ValueError(f"Empty validation or test split: validation={len(validation)}, test={test_rows}")

    manifest = _baseline_validation_rows(validation)
    cv_rows = []
    for fold in VALIDATION_FOLDS:
        fold_valid = feature_table[
            feature_table["year_month"].between(
                pd.Timestamp(fold["valid_start"]),
                pd.Timestamp(fold["valid_end"]),
            )
        ]
        for row in _baseline_validation_rows(fold_valid):
            cv_rows.append({"fold": fold["fold"], **row})
        del fold_valid
    del validation
    gc.collect()

    trained_model_names = []
    for name, options in MODEL_VARIANTS.items():
        skip_reason = None
        if options["use_news"] or options["use_commodity"] or options.get("use_module_c", False):
            signal_reference = feature_table[
                feature_table["year_month"].between(
                    pd.Timestamp(TRAIN_START),
                    pd.Timestamp(TRAIN_END),
                )
            ]
            skip_reason = _missing_external_signal(signal_reference, options)
            del signal_reference
        if skip_reason:
            manifest.append(
                {
                    "model": name,
                    "method_type": "machine_learning",
                    "algorithm": None,
                    "objective": options["objective"],
                    "status": "skipped",
                    "skip_reason": skip_reason,
                    "uses_news": bool(options["use_news"]),
                    "uses_commodity": bool(options["use_commodity"]),
                    "uses_module_c": bool(options.get("use_module_c", False)),
                    "n_features": 0,
                }
            )
            LOGGER.warning("Skipping %s: %s", name, skip_reason)
            continue

        feature_cols = select_feature_columns(
            feature_table,
            use_news=options["use_news"],
            use_commodity=options["use_commodity"],
            use_module_c=options.get("use_module_c", False),
        )
        fold_actuals = []
        fold_predictions = []
        algorithm = None
        for fold in VALIDATION_FOLDS:
            fold_train = select_training_window(
                feature_table,
                fold["train_end"],
                historical_weight,
            )
            fold_valid = feature_table[
                feature_table["year_month"].between(
                    pd.Timestamp(fold["valid_start"]),
                    pd.Timestamp(fold["valid_end"]),
                )
            ]
            if fold_train.empty or fold_valid.empty:
                raise ValueError(f"Empty cross-validation fold: {fold['fold']}")
            bundle = train_model_variant(
                name,
                fold_train,
                feature_cols,
                objective=options["objective"],
                valid=fold_valid,
                sample_weight=training_sample_weights(
                    fold_train,
                    historical_weight,
                ),
            )
            algorithm = bundle["algorithm"]
            fold_actuals.append(fold_valid[TARGET_COLUMN].to_numpy(dtype="float64"))
            fold_predictions.append(bundle["validation_prediction"])
            cv_rows.append(
                {
                    "fold": fold["fold"],
                    "model": name,
                    "method_type": "machine_learning",
                    "algorithm": algorithm,
                    "objective": options["objective"],
                    "status": "ready",
                    "skip_reason": None,
                    "uses_news": bool(options["use_news"]),
                    "uses_commodity": bool(options["use_commodity"]),
                    "uses_module_c": bool(options.get("use_module_c", False)),
                    "n_features": len(bundle["feature_cols"]),
                    "historical_weight": historical_weight,
                    "historical_rows": int(
                        fold_train["year_month"].lt(
                            pd.Timestamp(TRAIN_START)
                        ).sum()
                    ),
                    **bundle["validation_metrics"],
                }
            )
            LOGGER.info(
                "Validated %s on %s with WAPE=%s",
                name,
                fold["fold"],
                bundle["validation_metrics"].get("WAPE"),
            )
            del bundle, fold_train, fold_valid
            gc.collect()

        validation_metrics = regression_metrics(
            np.concatenate(fold_actuals),
            np.concatenate(fold_predictions),
        )
        row = {
            "model": name,
            "method_type": "machine_learning",
            "algorithm": algorithm,
            "objective": options["objective"],
            "status": "ready",
            "skip_reason": None,
            "uses_news": bool(options["use_news"]),
            "uses_commodity": bool(options["use_commodity"]),
            "uses_module_c": bool(options.get("use_module_c", False)),
            "n_features": len(feature_cols),
            "historical_weight": historical_weight,
            "historical_policy_version": historical_policy["version"],
            **validation_metrics,
        }
        manifest.append(row)
        trained_model_names.append(name)
        LOGGER.info("Cross-validated %s with WAPE=%s", name, row.get("WAPE"))
        gc.collect()

    eligible = [row for row in manifest if row.get("WAPE") is not None]
    if not eligible:
        raise ValueError("No forecast method produced validation metrics")
    selected_model = min(eligible, key=lambda row: row["WAPE"])["model"]
    for row in manifest:
        row["selected_on_validation"] = row["model"] == selected_model

    report = pd.DataFrame(manifest).sort_values("WAPE", na_position="last").reset_index(drop=True)
    report.to_csv(MODEL_VALIDATION_REPORT_PATH, index=False)
    pd.DataFrame(cv_rows).sort_values(["fold", "WAPE"], na_position="last").to_csv(
        MODEL_CV_REPORT_PATH,
        index=False,
    )
    with MODEL_MANIFEST_PATH.open("w", encoding="utf-8") as f:
        json.dump(_json_records(report), f, ensure_ascii=False, indent=2, allow_nan=False)
    LOGGER.info("Selected forecast method on validation: %s", selected_model)
    return trained_model_names


def refit_model(name: str) -> None:
    if name not in MODEL_VARIANTS:
        raise ValueError(f"Unknown model variant: {name}")
    setup_logging()
    ensure_dirs(MODEL_DIR, OUTPUT_DIR)
    options = MODEL_VARIANTS[name]
    feature_table = _load_feature_table(options)
    valid_rows = feature_table["rolling_mean_3"].notna()
    if not valid_rows.all():
        feature_table = feature_table.loc[valid_rows]
    if (
        options["use_news"]
        or options["use_commodity"]
        or options.get("use_module_c", False)
    ):
        signal_reference = feature_table[
            feature_table["year_month"].between(
                pd.Timestamp(TRAIN_START),
                pd.Timestamp(TRAIN_END),
            )
        ]
        skip_reason = _missing_external_signal(
            signal_reference,
            options,
        )
        del signal_reference
        if skip_reason:
            raise ValueError(f"Cannot refit {name}: {skip_reason}")
    feature_cols = select_feature_columns(
        feature_table,
        use_news=options["use_news"],
        use_commodity=options["use_commodity"],
        use_module_c=options.get("use_module_c", False),
    )
    historical_policy = load_historical_training_policy()
    historical_weight = float(
        historical_policy["selected_historical_weight"]
    )
    combined = select_training_window(
        feature_table,
        VALID_END,
        historical_weight,
    ).copy()
    del feature_table
    gc.collect()
    bundle = train_model_variant(
        name,
        combined,
        feature_cols,
        objective=options["objective"],
        sample_weight=training_sample_weights(
            combined,
            historical_weight,
        ),
    )
    if MODEL_MANIFEST_PATH.exists():
        manifest = json.loads(
            MODEL_MANIFEST_PATH.read_text(encoding="utf-8")
        )
        matching = [
            row for row in manifest if row.get("model") == name
        ]
        if matching:
            bundle["validation_metrics"] = {
                key: matching[0].get(key)
                for key in [
                    "N",
                    "ACTUAL_SUM",
                    "PREDICTION_SUM",
                    "MAE",
                    "RMSE",
                    "RMSLE",
                    "MAPE",
                    "SMAPE",
                    "WAPE",
                    "BIAS",
                    "BIAS_PCT",
                    "ZERO_ACTUAL_RATE",
                ]
            }
    bundle["historical_training_policy"] = historical_policy
    save_model_bundle(bundle)
    LOGGER.info("Refit and saved %s on train+validation", name)


def run_training() -> None:
    validation_command = [
        sys.executable,
        "-u",
        "-m",
        "src.modeling.training",
        "--validate-only",
    ]
    subprocess.run(validation_command, check=True)
    manifest = json.loads(
        MODEL_MANIFEST_PATH.read_text(encoding="utf-8")
    )
    trained_model_names = [
        row["model"]
        for row in manifest
        if row.get("method_type") == "machine_learning"
        and row.get("status") == "ready"
    ]
    for name in trained_model_names:
        subprocess.run(
            [
                sys.executable,
                "-u",
                "-m",
                "src.modeling.training",
                "--refit-model",
                name,
            ],
            check=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Validate and refit stock forecast models",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Write validation reports without final refits",
    )
    parser.add_argument(
        "--refit-model",
        choices=sorted(MODEL_VARIANTS),
        help="Refit one validated model in an isolated process",
    )
    arguments = parser.parse_args()
    if arguments.validate_only:
        run_validation()
    elif arguments.refit_model:
        refit_model(arguments.refit_model)
    else:
        run_training()
