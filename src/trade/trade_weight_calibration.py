from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from ..config import (
    MATERIAL_HS_MAPPING_PATH,
    MODULE_C_RISK_WEIGHT_PATH,
    MONTHLY_STOCK_PATH,
    STOCK_MATERIAL_MAPPING_PATH,
    TRADE_COUNTRY_CACHE_PATH,
    TRADE_COUNTRY_CACHE_SUMMARY_PATH,
    TRADE_TOTAL_CACHE_PATH,
    TRADE_WEIGHT_CALIBRATION_POLICY_PATH,
    TRADE_WEIGHT_CALIBRATION_REPORT_PATH,
    TRADE_WEIGHT_CALIBRATION_SAMPLE_PATH,
    TRADE_WEIGHT_CALIBRATION_TABLE_PATH,
)
from ..material_mapping import load_approved_stock_material_mapping
from ..module_c.config import load_module_c_config, validate_module_c_config
from ..utils import ensure_dirs, setup_logging, write_json
from .trade_collector import load_trade_country_scope, normalize_trade_flows
from .trade_risk_scorer import (
    _stock_material_paths,
    build_hs_trade_features,
    load_material_hs_mapping,
)


LOGGER = logging.getLogger(__name__)
CALIBRATION_VERSION = "trade-weight-calibration-v1.1"
COMPONENTS = {
    "import_volume_decline": "import_volume_decline_risk",
    "net_import_availability_decline": (
        "net_import_availability_decline_risk"
    ),
    "import_interruption": "import_interruption_risk",
    "import_unit_value_increase": "import_unit_value_increase_risk",
    "import_volume_volatility": "import_volume_volatility_risk",
    "import_unit_value_volatility": "import_unit_value_volatility_risk",
    "country_concentration": "country_concentration_risk",
    "supplier_count_decline": "supplier_count_decline_risk",
    "net_import_exposure": "net_import_exposure_risk",
    "export_volume_surge": "export_volume_surge_risk",
}
WEIGHT_FLOOR = 0.025
VALIDATION_MONTH_COUNT = 3
TEST_MONTH_COUNT = 3


def validate_collection_completeness(
    hs_codes: list[str],
    *,
    country_cache_path: Path = TRADE_COUNTRY_CACHE_PATH,
) -> dict[str, object]:
    expected_countries = sorted(load_trade_country_scope())
    expected_hs_codes = sorted({str(code) for code in hs_codes})
    expected_pairs = {
        f"{country}:{hs_code}"
        for country in expected_countries
        for hs_code in expected_hs_codes
    }
    state_path = country_cache_path.with_name(
        "kcs_trade_collection_state.json"
    )
    if not state_path.exists():
        raise RuntimeError(
            f"KCS collection state is missing: {state_path}"
        )
    with state_path.open("r", encoding="utf-8") as file:
        state = json.load(file)
    completed_pairs = {
        str(value)
        for value in state.get("completed_country_hs_pairs", [])
    }
    completed_total_hs = {
        str(value)
        for value in state.get("completed_total_hs_codes", [])
    }
    missing_pairs = sorted(expected_pairs - completed_pairs)
    missing_total_hs = sorted(
        set(expected_hs_codes) - completed_total_hs
    )
    unexpected_pairs = sorted(completed_pairs - expected_pairs)
    summary = {
        "expected_country_count": len(expected_countries),
        "expected_country_codes": expected_countries,
        "expected_hs_count": len(expected_hs_codes),
        "expected_country_hs_pair_count": len(expected_pairs),
        "completed_country_hs_pair_count": len(
            expected_pairs & completed_pairs
        ),
        "missing_country_hs_pair_count": len(missing_pairs),
        "missing_country_hs_pairs": missing_pairs,
        "completed_total_hs_count": len(
            set(expected_hs_codes) & completed_total_hs
        ),
        "missing_total_hs_count": len(missing_total_hs),
        "missing_total_hs_codes": missing_total_hs,
        "unexpected_country_hs_pair_count": len(unexpected_pairs),
        "collection_state_path": str(state_path),
        "collection_state_updated_at": state.get("updated_at"),
        "is_complete": not missing_pairs and not missing_total_hs,
    }
    if missing_pairs or missing_total_hs:
        raise RuntimeError(
            "KCS 20-country cache is incomplete: "
            f"{len(missing_pairs)} of {len(expected_pairs)} "
            "country-HSK pairs and "
            f"{len(missing_total_hs)} of {len(expected_hs_codes)} "
            "total HSK codes are missing"
        )
    return summary


def build_country_cache_summary(
    countries: pd.DataFrame,
    hs_codes: list[str],
    *,
    country_cache_path: Path = TRADE_COUNTRY_CACHE_PATH,
) -> pd.DataFrame:
    country_codes = sorted(load_trade_country_scope())
    expected_hs_count = len({str(code) for code in hs_codes})
    state_path = country_cache_path.with_name(
        "kcs_trade_collection_state.json"
    )
    with state_path.open("r", encoding="utf-8") as file:
        state = json.load(file)
    completed_pairs = pd.Series(
        state.get("completed_country_hs_pairs", []),
        dtype="string",
    )
    completed_country = completed_pairs.str.split(":", n=1).str[0]
    completed_counts = completed_country.value_counts().to_dict()

    normalized = normalize_trade_flows(countries)
    if normalized.empty:
        observed = pd.DataFrame(columns=["country_code"])
    else:
        observed = (
            normalized.groupby("country_code", as_index=False, observed=True)
            .agg(
                cache_row_count=("STD_YYYYMM", "size"),
                observed_hs_count=("hs_code", "nunique"),
                observed_month_count=("STD_YYYYMM", "nunique"),
                month_min=("STD_YYYYMM", "min"),
                month_max=("STD_YYYYMM", "max"),
                import_weight_kg_sum=("import_weight_kg", "sum"),
                import_value_usd_sum=("import_value_usd", "sum"),
                export_weight_kg_sum=("export_weight_kg", "sum"),
                export_value_usd_sum=("export_value_usd", "sum"),
            )
        )
    result = pd.DataFrame({"country_code": country_codes}).merge(
        observed,
        on="country_code",
        how="left",
        validate="one_to_one",
    )
    for column in [
        "cache_row_count",
        "observed_hs_count",
        "observed_month_count",
        "import_weight_kg_sum",
        "import_value_usd_sum",
        "export_weight_kg_sum",
        "export_value_usd_sum",
    ]:
        result[column] = pd.to_numeric(
            result[column],
            errors="coerce",
        ).fillna(0)
    result["expected_hs_count"] = expected_hs_count
    result["completed_hs_count"] = (
        result["country_code"].map(completed_counts).fillna(0).astype(int)
    )
    result["completed_hs_without_response_rows"] = (
        result["completed_hs_count"] - result["observed_hs_count"]
    ).clip(lower=0).astype(int)
    result["collection_complete"] = result["completed_hs_count"].eq(
        expected_hs_count
    )
    return result[
        [
            "country_code",
            "collection_complete",
            "expected_hs_count",
            "completed_hs_count",
            "observed_hs_count",
            "completed_hs_without_response_rows",
            "cache_row_count",
            "observed_month_count",
            "month_min",
            "month_max",
            "import_weight_kg_sum",
            "import_value_usd_sum",
            "export_weight_kg_sum",
            "export_value_usd_sum",
        ]
    ]


def _positive_quantile(
    values: pd.Series,
    quantile: float,
    *,
    default: float,
    lower: float,
    upper: float,
) -> float:
    numeric = pd.to_numeric(values, errors="coerce")
    positive = numeric[numeric.gt(0) & np.isfinite(numeric)]
    value = float(positive.quantile(quantile)) if len(positive) >= 10 else default
    return round(float(np.clip(value, lower, upper)), 4)


def split_calibration_months(
    months: list[str],
    *,
    validation_month_count: int = VALIDATION_MONTH_COUNT,
    test_month_count: int = TEST_MONTH_COUNT,
) -> tuple[set[str], set[str], set[str]]:
    ordered = sorted(set(months))
    if validation_month_count <= 0 or test_month_count <= 0:
        raise ValueError(
            "Validation and test month counts must be positive"
        )
    held_out_count = validation_month_count + test_month_count
    if len(ordered) <= held_out_count:
        raise ValueError(
            "Trade calibration requires training months before validation "
            f"and test: months={len(ordered)}, held_out={held_out_count}"
        )
    test = set(ordered[-test_month_count:])
    validation = set(
        ordered[-held_out_count:-test_month_count]
    )
    training = set(ordered[:-held_out_count])
    return training, validation, test


def calibrate_trade_thresholds(
    features: pd.DataFrame,
    current_policy: dict[str, object],
) -> dict[str, object]:
    concentration = features[
        ["country_top1_share", "country_hhi"]
    ].max(axis=1)
    coverage = pd.to_numeric(
        features["country_import_coverage"],
        errors="coerce",
    )
    coverage_positive = coverage[coverage.gt(0)]
    coverage_min = (
        float(coverage_positive.quantile(0.20))
        if len(coverage_positive) >= 10
        else float(current_policy["country_coverage_min"])
    )
    interruption = _positive_quantile(
        features["zero_import_streak_months"],
        0.75,
        default=float(current_policy["import_interruption_streak_months"]),
        lower=1.0,
        upper=6.0,
    )
    return {
        "import_volume_decline_threshold": _positive_quantile(
            -features["import_volume_yoy_change"],
            0.90,
            default=float(current_policy["import_volume_decline_threshold"]),
            lower=0.10,
            upper=1.0,
        ),
        "net_import_availability_decline_threshold": _positive_quantile(
            -features["net_import_volume_yoy_change"],
            0.90,
            default=float(
                current_policy["net_import_availability_decline_threshold"]
            ),
            lower=0.10,
            upper=1.0,
        ),
        "import_interruption_streak_months": int(np.ceil(interruption)),
        "import_unit_value_increase_threshold": _positive_quantile(
            features["import_unit_value_yoy_change"],
            0.90,
            default=float(
                current_policy["import_unit_value_increase_threshold"]
            ),
            lower=0.10,
            upper=1.0,
        ),
        "import_volume_volatility_threshold": _positive_quantile(
            features["import_volume_rolling_cv"],
            0.90,
            default=float(
                current_policy["import_volume_volatility_threshold"]
            ),
            lower=0.10,
            upper=1.0,
        ),
        "import_unit_value_volatility_threshold": _positive_quantile(
            features["import_unit_value_rolling_volatility"],
            0.90,
            default=float(
                current_policy["import_unit_value_volatility_threshold"]
            ),
            lower=0.05,
            upper=1.0,
        ),
        "country_concentration_threshold": _positive_quantile(
            concentration.where(coverage.gt(0)),
            0.75,
            default=float(current_policy["country_concentration_threshold"]),
            lower=0.25,
            upper=0.85,
        ),
        "supplier_count_decline_threshold": _positive_quantile(
            -features["supplier_count_yoy_change"],
            0.90,
            default=float(
                current_policy["supplier_count_decline_threshold"]
            ),
            lower=0.10,
            upper=1.0,
        ),
        "export_volume_surge_threshold": _positive_quantile(
            features["export_volume_yoy_change"],
            0.90,
            default=float(current_policy["export_volume_surge_threshold"]),
            lower=0.20,
            upper=1.0,
        ),
        "country_coverage_min": round(
            float(np.clip(coverage_min, 0.75, 0.95)),
            4,
        ),
    }


def _normalize_weights(
    raw_values: dict[str, float],
    *,
    floor: float = WEIGHT_FLOOR,
) -> dict[str, float]:
    keys = list(COMPONENTS)
    values = np.array(
        [float(raw_values.get(key, 0.0)) for key in keys],
        dtype="float64",
    )
    values = np.where(np.isfinite(values), np.maximum(values, 0.0), 0.0)
    if values.sum() <= 0:
        values = np.ones(len(keys), dtype="float64")
    values /= values.sum()
    adjusted = np.zeros_like(values)
    active = np.ones(len(values), dtype=bool)
    remaining_mass = 1.0
    while active.any():
        active_values = values[active]
        proposed = (
            remaining_mass * active_values / active_values.sum()
            if active_values.sum() > 0
            else np.full(active.sum(), remaining_mass / active.sum())
        )
        below_floor = proposed < floor
        active_indexes = np.flatnonzero(active)
        if not below_floor.any():
            adjusted[active_indexes] = proposed
            break
        floor_indexes = active_indexes[below_floor]
        adjusted[floor_indexes] = floor
        active[floor_indexes] = False
        remaining_mass -= floor * len(floor_indexes)
    rounded = {
        key: round(float(value), 6)
        for key, value in zip(keys, adjusted)
    }
    correction_key = max(rounded, key=rounded.get)
    rounded[correction_key] = round(
        rounded[correction_key] + 1.0 - sum(rounded.values()),
        6,
    )
    return rounded


def _availability_masks(
    features: pd.DataFrame,
    coverage_min: float,
) -> dict[str, pd.Series]:
    return {
        "import_volume_decline": features[
            "import_volume_yoy_change"
        ].notna(),
        "net_import_availability_decline": features[
            "net_import_volume_yoy_change"
        ].notna(),
        "import_interruption": pd.Series(True, index=features.index),
        "import_unit_value_increase": features[
            "import_unit_value_yoy_change"
        ].notna(),
        "import_volume_volatility": features[
            "import_volume_rolling_cv"
        ].notna(),
        "import_unit_value_volatility": features[
            "import_unit_value_rolling_volatility"
        ].notna(),
        "country_concentration": features[
            "country_import_coverage"
        ].ge(coverage_min),
        "supplier_count_decline": features[
            "supplier_count_yoy_change"
        ].notna(),
        "net_import_exposure": pd.Series(True, index=features.index),
        "export_volume_surge": features[
            "export_volume_yoy_change"
        ].notna(),
    }


def distribution_weights(
    features: pd.DataFrame,
    current_weights: dict[str, float],
    coverage_min: float,
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    risk = features[list(COMPONENTS.values())].apply(
        pd.to_numeric,
        errors="coerce",
    )
    filled = risk.fillna(0.0)
    correlation = filled.corr(method="spearman").abs()
    masks = _availability_masks(features, coverage_min)
    variability_values = {
        key: float(filled[risk_column].std(ddof=0))
        for key, risk_column in COMPONENTS.items()
    }
    finite_variability = [
        value for value in variability_values.values() if np.isfinite(value)
    ]
    max_variability = max(finite_variability, default=1.0) or 1.0
    diagnostics = {}
    raw = {}
    for key, risk_column in COMPONENTS.items():
        other_columns = [
            column for column in COMPONENTS.values() if column != risk_column
        ]
        mean_correlation = float(
            correlation.loc[risk_column, other_columns].mean()
        )
        if not np.isfinite(mean_correlation):
            mean_correlation = 1.0
        availability = float(masks[key].mean())
        distinctiveness = float(np.clip(1.0 - mean_correlation, 0.05, 1.0))
        observed_variability = variability_values[key]
        if not np.isfinite(observed_variability):
            observed_variability = 0.0
        variability = float(
            np.clip(observed_variability / max_variability, 0.05, 1.0)
        )
        prior = float(current_weights[key])
        raw[key] = (
            prior
            * (0.5 + availability)
            * (0.5 + distinctiveness)
            * (0.5 + variability)
        )
        diagnostics[key] = {
            "availability_rate": availability,
            "mean_absolute_component_correlation": mean_correlation,
            "distinctiveness": distinctiveness,
            "risk_standard_deviation": variability_values[key],
        }
    return _normalize_weights(raw), diagnostics


def build_next_month_stockout_proxy() -> pd.DataFrame:
    stock = pd.read_parquet(
        MONTHLY_STOCK_PATH,
        columns=[
            "year_month",
            "stock_item_key",
            "stockout_rate",
            "stockout_observation_count",
        ],
    )
    month = pd.to_datetime(stock["year_month"], errors="coerce")
    stock["STD_YYYYMM"] = (
        month - pd.offsets.DateOffset(months=1)
    ).dt.strftime("%Y-%m")
    stock["next_stockout_event"] = (
        pd.to_numeric(
            stock["stockout_observation_count"],
            errors="coerce",
        )
        .fillna(0)
        .gt(0)
        .astype(float)
    )
    stock["next_stockout_rate"] = pd.to_numeric(
        stock["stockout_rate"],
        errors="coerce",
    ).fillna(0.0).clip(0, 1)

    stock_mapping = load_approved_stock_material_mapping(
        STOCK_MATERIAL_MAPPING_PATH,
        eligibility_column="identity_approved",
    )
    stock_paths = _stock_material_paths(stock_mapping)
    material_hs = load_material_hs_mapping(MATERIAL_HS_MAPPING_PATH)
    item_hs = (
        stock_paths[["stock_item_key", "raw_material_meta_code"]]
        .merge(
            material_hs[["raw_material_meta_code", "hs_code"]],
            on="raw_material_meta_code",
            how="inner",
            validate="many_to_many",
        )[["stock_item_key", "hs_code"]]
        .drop_duplicates()
    )
    observed = stock.merge(
        item_hs,
        on="stock_item_key",
        how="inner",
        validate="many_to_many",
    )
    return (
        observed.groupby(
            ["STD_YYYYMM", "hs_code"],
            as_index=False,
            observed=True,
        )
        .agg(
            next_stockout_event_rate=("next_stockout_event", "mean"),
            next_stockout_rate=("next_stockout_rate", "mean"),
            outcome_stock_item_count=("stock_item_key", "nunique"),
        )
        .assign(
            next_supply_disruption_proxy=lambda frame: (
                0.70 * frame["next_stockout_event_rate"]
                + 0.30 * frame["next_stockout_rate"]
            )
        )
    )


def _supervised_weights(
    calibration: pd.DataFrame,
    train_mask: pd.Series,
) -> dict[str, float]:
    risk_columns = list(COMPONENTS.values())
    train = calibration.loc[train_mask]
    if len(train) < 30:
        return _normalize_weights({})
    model = Ridge(alpha=1.0, positive=True)
    model.fit(
        train[risk_columns].fillna(0.0),
        train["next_supply_disruption_proxy"],
        sample_weight=np.sqrt(
            train["outcome_stock_item_count"].clip(lower=1)
        ),
    )
    return _normalize_weights(
        {
            key: float(coefficient)
            for key, coefficient in zip(COMPONENTS, model.coef_)
        }
    )


def _candidate_metric(
    calibration: pd.DataFrame,
    fit_mask: pd.Series,
    evaluation_mask: pd.Series,
    weights: dict[str, float],
) -> dict[str, float | int | None]:
    risk_columns = [COMPONENTS[key] for key in COMPONENTS]
    weight_values = np.array([weights[key] for key in COMPONENTS])
    score = (
        calibration[risk_columns].fillna(0.0).to_numpy() @ weight_values
    )
    fit = calibration.loc[fit_mask]
    evaluation = calibration.loc[evaluation_mask]
    fit_score = score[fit_mask.to_numpy()].reshape(-1, 1)
    evaluation_score = score[evaluation_mask.to_numpy()].reshape(-1, 1)
    if len(fit) == 0 or len(evaluation) == 0:
        return {
            "evaluation_rows": int(len(evaluation)),
            "weighted_mae": None,
            "spearman": None,
        }
    calibrator = Ridge(alpha=0.1, positive=True)
    calibrator.fit(
        fit_score,
        fit["next_supply_disruption_proxy"],
        sample_weight=np.sqrt(
            fit["outcome_stock_item_count"].clip(lower=1)
        ),
    )
    prediction = np.clip(
        calibrator.predict(evaluation_score),
        0,
        1,
    )
    sample_weight = np.sqrt(
        evaluation["outcome_stock_item_count"].clip(lower=1).to_numpy()
    )
    weighted_mae = float(
        np.average(
            np.abs(
                evaluation[
                    "next_supply_disruption_proxy"
                ].to_numpy()
                - prediction
            ),
            weights=sample_weight,
        )
    )
    spearman = pd.Series(
        evaluation_score.ravel(),
        index=evaluation.index,
    ).corr(
        evaluation["next_supply_disruption_proxy"],
        method="spearman",
    )
    return {
        "evaluation_rows": int(len(evaluation)),
        "weighted_mae": weighted_mae,
        "spearman": float(spearman) if pd.notna(spearman) else None,
    }


def _constant_reference_metric(
    calibration: pd.DataFrame,
    fit_mask: pd.Series,
    evaluation_mask: pd.Series,
) -> dict[str, float | int | None]:
    fit = calibration.loc[fit_mask]
    evaluation = calibration.loc[evaluation_mask]
    if len(fit) == 0 or len(evaluation) == 0:
        return {
            "evaluation_rows": int(len(evaluation)),
            "fit_weighted_mean": None,
            "weighted_mae": None,
        }
    fit_weight = np.sqrt(
        fit["outcome_stock_item_count"].clip(lower=1).to_numpy()
    )
    evaluation_weight = np.sqrt(
        evaluation["outcome_stock_item_count"].clip(lower=1).to_numpy()
    )
    fit_mean = float(
        np.average(
            fit["next_supply_disruption_proxy"].to_numpy(),
            weights=fit_weight,
        )
    )
    weighted_mae = float(
        np.average(
            np.abs(
                evaluation[
                    "next_supply_disruption_proxy"
                ].to_numpy()
                - fit_mean
            ),
            weights=evaluation_weight,
        )
    )
    return {
        "evaluation_rows": int(len(evaluation)),
        "fit_weighted_mean": fit_mean,
        "weighted_mae": weighted_mae,
    }


def calibrate_trade_policy(
    totals: pd.DataFrame,
    countries: pd.DataFrame,
    current_config: dict[str, object],
) -> tuple[dict[str, object], pd.DataFrame, dict[str, object]]:
    initial_features = build_hs_trade_features(
        totals,
        countries,
        current_config,
    )
    outcome = build_next_month_stockout_proxy()
    outcome_months = set(outcome["STD_YYYYMM"].dropna().unique().tolist())
    months = sorted(
        set(initial_features["STD_YYYYMM"].dropna().unique().tolist())
        & outcome_months
    )
    training_months, validation_months, test_months = (
        split_calibration_months(months)
    )
    development_months = training_months | validation_months
    threshold_training_features = initial_features[
        initial_features["STD_YYYYMM"].isin(training_months)
    ]
    if threshold_training_features.empty:
        threshold_training_features = initial_features

    current_policy = current_config["trade_signal"]
    thresholds = calibrate_trade_thresholds(
        threshold_training_features,
        current_policy,
    )
    candidate_config = deepcopy(current_config)
    candidate_config["trade_signal"].update(thresholds)
    threshold_features = build_hs_trade_features(
        totals,
        countries,
        candidate_config,
    )
    current_weights = {
        key: float(current_policy[key]) for key in COMPONENTS
    }
    distribution_training_features = threshold_features[
        threshold_features["STD_YYYYMM"].isin(training_months)
    ]
    if distribution_training_features.empty:
        distribution_training_features = threshold_features
    empirical_weights, diagnostics = distribution_weights(
        distribution_training_features,
        current_weights,
        float(thresholds["country_coverage_min"]),
    )
    calibration = threshold_features.merge(
        outcome,
        on=["STD_YYYYMM", "hs_code"],
        how="inner",
        validate="one_to_one",
    ).sort_values(["STD_YYYYMM", "hs_code"]).reset_index(drop=True)
    train_mask = calibration["STD_YYYYMM"].isin(training_months)
    validation_mask = calibration["STD_YYYYMM"].isin(validation_months)
    test_mask = calibration["STD_YYYYMM"].isin(test_months)
    development_mask = train_mask | validation_mask
    supervised_weights = _supervised_weights(calibration, train_mask)
    supervised_empirical_blend = _normalize_weights(
        {
            key: 0.65 * supervised_weights[key]
            + 0.35 * empirical_weights[key]
            for key in COMPONENTS
        }
    )
    candidates = {
        "current_weights_calibrated_thresholds": _normalize_weights(
            current_weights
        ),
        "equal_weight": _normalize_weights(
            {key: 1.0 for key in COMPONENTS}
        ),
        "distribution_reliability": empirical_weights,
        "stockout_proxy_blend": supervised_empirical_blend,
    }
    candidate_metrics = {
        name: _candidate_metric(
            calibration,
            train_mask,
            validation_mask,
            weights,
        )
        for name, weights in candidates.items()
    }
    constant_validation_metric = _constant_reference_metric(
        calibration,
        train_mask,
        validation_mask,
    )
    previous_calibration = initial_features.merge(
        outcome,
        on=["STD_YYYYMM", "hs_code"],
        how="inner",
        validate="one_to_one",
    ).sort_values(["STD_YYYYMM", "hs_code"]).reset_index(drop=True)
    previous_validation_mask = previous_calibration["STD_YYYYMM"].isin(
        validation_months
    )
    previous_train_mask = previous_calibration["STD_YYYYMM"].isin(
        training_months
    )
    previous_test_mask = previous_calibration["STD_YYYYMM"].isin(
        test_months
    )
    previous_development_mask = (
        previous_train_mask | previous_validation_mask
    )
    previous_policy_validation_metric = _candidate_metric(
        previous_calibration,
        previous_train_mask,
        previous_validation_mask,
        current_weights,
    )
    eligible = [
        name
        for name, metrics in candidate_metrics.items()
        if metrics["weighted_mae"] is not None
    ]
    selected_name = (
        min(
            eligible,
            key=lambda name: (
                float(candidate_metrics[name]["weighted_mae"]),
                -float(candidate_metrics[name]["spearman"] or -1.0),
            ),
        )
        if eligible
        else "distribution_reliability"
    )
    selected_weights = candidates[selected_name]
    prior_share = (
        0.30
        if selected_name == "current_weights_calibrated_thresholds"
        else 0.20
    )
    final_weights = _normalize_weights(
        {
            key: prior_share * current_weights[key]
            + (1.0 - prior_share) * selected_weights[key]
            for key in COMPONENTS
        }
    )
    final_validation_metric = _candidate_metric(
        calibration,
        train_mask,
        validation_mask,
        final_weights,
    )
    final_test_metric = _candidate_metric(
        calibration,
        development_mask,
        test_mask,
        final_weights,
    )
    previous_policy_test_metric = _candidate_metric(
        previous_calibration,
        previous_development_mask,
        previous_test_mask,
        current_weights,
    )
    constant_test_metric = _constant_reference_metric(
        calibration,
        development_mask,
        test_mask,
    )
    current_mae = previous_policy_test_metric["weighted_mae"]
    final_mae = final_test_metric["weighted_mae"]
    weighted_mae_improvement = (
        100.0 * (float(current_mae) - float(final_mae)) / float(current_mae)
        if current_mae not in {None, 0} and final_mae is not None
        else None
    )
    constant_mae = constant_test_metric["weighted_mae"]
    weighted_mae_improvement_vs_constant = (
        100.0 * (float(constant_mae) - float(final_mae))
        / float(constant_mae)
        if constant_mae not in {None, 0} and final_mae is not None
        else None
    )
    current_spearman = previous_policy_test_metric["spearman"]
    final_spearman = final_test_metric["spearman"]
    spearman_delta = (
        float(final_spearman) - float(current_spearman)
        if current_spearman is not None and final_spearman is not None
        else None
    )
    for key, value in final_weights.items():
        candidate_config["trade_signal"][key] = value

    overlay_features = threshold_features[
        threshold_features["STD_YYYYMM"].isin(development_months)
    ]
    final_hs_risk = sum(
        final_weights[key]
        * pd.to_numeric(
            overlay_features[risk_column],
            errors="coerce",
        ).fillna(0.0)
        for key, risk_column in COMPONENTS.items()
    ).clip(0, 1)
    hs_risk_p99 = float(final_hs_risk.quantile(0.99))
    overlay = float(
        np.clip(
            0.18 / hs_risk_p99 if hs_risk_p99 > 0 else 0.25,
            0.15,
            0.30,
        )
    )
    candidate_config["trade_signal"]["module_c_overlay_weight"] = round(
        overlay,
        4,
    )
    candidate_config["version"] = "module-c-v1.4-trade20-temporal"
    candidate_config["calibration_status"] = (
        "20_country_train_validation_test_stockout_proxy_calibrated_"
        "requires_operational_validation"
    )
    candidate_config["coefficient_basis"] = (
        "trade_component_thresholds_from_20_country_quantiles_"
        "and_weights_selected_on_validation_then_evaluated_on_test"
    )
    validate_module_c_config(candidate_config)

    calibration = calibration.copy()
    calibration["calibration_split"] = np.select(
        [validation_mask, test_mask],
        ["validation", "test"],
        default="train",
    )
    for name, weights in candidates.items():
        calibration[f"{name}_risk"] = sum(
            weights[key]
            * calibration[risk_column].fillna(0.0)
            for key, risk_column in COMPONENTS.items()
        )
    calibration["final_calibrated_risk"] = sum(
        final_weights[key]
        * calibration[risk_column].fillna(0.0)
        for key, risk_column in COMPONENTS.items()
    )
    report = {
        "calibration_version": CALIBRATION_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "proxy_backtest_calibrated_requires_operational_validation",
        "source_country_codes": sorted(
            countries["country_code"].dropna().unique().tolist()
        ),
        "source_country_count": int(countries["country_code"].nunique()),
        "source_total_trade_rows": int(len(totals)),
        "source_country_trade_rows": int(len(countries)),
        "hs_feature_rows": int(len(threshold_features)),
        "calibration_observation_rows": int(len(calibration)),
        "train_months": sorted(
            calibration.loc[train_mask, "STD_YYYYMM"].unique().tolist()
        ),
        "validation_months": sorted(validation_months),
        "test_months": sorted(test_months),
        "temporal_validation_policy": (
            "Thresholds, distribution diagnostics, supervised weights, and "
            "candidate construction use training months only. Candidate "
            "selection uses validation months. Test months remain untouched "
            "until the selected policy is evaluated once."
        ),
        "outcome": (
            "next_month 70% any-stockout rate + 30% mean stockout rate "
            "aggregated by HSK"
        ),
        "previous_trade_policy": {
            **current_weights,
            **{
                key: current_policy[key]
                for key in [
                    "import_volume_decline_threshold",
                    "net_import_availability_decline_threshold",
                    "import_interruption_streak_months",
                    "import_unit_value_increase_threshold",
                    "import_volume_volatility_threshold",
                    "import_unit_value_volatility_threshold",
                    "country_concentration_threshold",
                    "supplier_count_decline_threshold",
                    "export_volume_surge_threshold",
                    "country_coverage_min",
                    "module_c_overlay_weight",
                ]
            },
        },
        "calibrated_thresholds": thresholds,
        "component_diagnostics": diagnostics,
        "candidate_weights": candidates,
        "candidate_validation_metrics": candidate_metrics,
        "candidate_metrics": candidate_metrics,
        "previous_policy_validation_metric": (
            previous_policy_validation_metric
        ),
        "constant_validation_metric": constant_validation_metric,
        "selected_candidate": selected_name,
        "prior_stability_share": prior_share,
        "final_weights": final_weights,
        "final_validation_metric": final_validation_metric,
        "previous_policy_test_metric": previous_policy_test_metric,
        "constant_test_metric": constant_test_metric,
        "final_test_metric": final_test_metric,
        "previous_policy_metric": previous_policy_test_metric,
        "constant_reference_metric": constant_test_metric,
        "final_weighted_mae_improvement_vs_current_percent": (
            weighted_mae_improvement
        ),
        "final_test_weighted_mae_improvement_vs_current_percent": (
            weighted_mae_improvement
        ),
        "final_weighted_mae_improvement_vs_constant_percent": (
            weighted_mae_improvement_vs_constant
        ),
        "final_test_weighted_mae_improvement_vs_constant_percent": (
            weighted_mae_improvement_vs_constant
        ),
        "final_spearman_delta_vs_current": spearman_delta,
        "final_test_spearman_delta_vs_current": spearman_delta,
        "final_hs_risk_p99": hs_risk_p99,
        "module_c_overlay_calibration_months": sorted(
            development_months
        ),
        "calibrated_module_c_overlay_weight": round(overlay, 4),
        "applied_config_version": candidate_config["version"],
        "limitations": [
            "Stockout is a proxy outcome and does not prove trade-risk causality.",
            "HS-month observations sharing an HSK are not independent item-level events.",
            "Operational weights require validation against procurement lead time, purchase price, and confirmed shortage incidents.",
        ],
    }
    return candidate_config, calibration, report


def build_policy_comparison_table(
    report: dict[str, object],
) -> pd.DataFrame:
    previous = report["previous_trade_policy"]
    final_weights = report["final_weights"]
    selected_weights = report["candidate_weights"][
        report["selected_candidate"]
    ]
    diagnostics = report["component_diagnostics"]
    rows = []
    for component in COMPONENTS:
        detail = diagnostics[component]
        rows.append(
            {
                "parameter_type": "component_weight",
                "parameter": component,
                "previous_value": previous[component],
                "selected_candidate_value": selected_weights[component],
                "calibrated_value": final_weights[component],
                "availability_rate": detail["availability_rate"],
                "mean_absolute_component_correlation": detail[
                    "mean_absolute_component_correlation"
                ],
                "risk_standard_deviation": detail[
                    "risk_standard_deviation"
                ],
            }
        )
    for parameter, calibrated_value in report[
        "calibrated_thresholds"
    ].items():
        rows.append(
            {
                "parameter_type": "risk_threshold",
                "parameter": parameter,
                "previous_value": previous[parameter],
                "selected_candidate_value": pd.NA,
                "calibrated_value": calibrated_value,
                "availability_rate": pd.NA,
                "mean_absolute_component_correlation": pd.NA,
                "risk_standard_deviation": pd.NA,
            }
        )
    rows.append(
        {
            "parameter_type": "module_c_overlay",
            "parameter": "module_c_overlay_weight",
            "previous_value": previous["module_c_overlay_weight"],
            "selected_candidate_value": pd.NA,
            "calibrated_value": report[
                "calibrated_module_c_overlay_weight"
            ],
            "availability_rate": pd.NA,
            "mean_absolute_component_correlation": pd.NA,
            "risk_standard_deviation": pd.NA,
        }
    )
    return pd.DataFrame(rows)


def run_trade_weight_calibration(
    *,
    apply: bool = False,
) -> dict[str, object]:
    setup_logging()
    ensure_dirs(
        TRADE_WEIGHT_CALIBRATION_REPORT_PATH.parent,
        TRADE_WEIGHT_CALIBRATION_TABLE_PATH.parent,
        TRADE_WEIGHT_CALIBRATION_POLICY_PATH.parent,
        TRADE_COUNTRY_CACHE_SUMMARY_PATH.parent,
        TRADE_WEIGHT_CALIBRATION_SAMPLE_PATH.parent,
    )
    totals = normalize_trade_flows(
        pd.read_csv(TRADE_TOTAL_CACHE_PATH, dtype={"hs_code": "string"})
    )
    countries = normalize_trade_flows(
        pd.read_csv(TRADE_COUNTRY_CACHE_PATH, dtype={"hs_code": "string"})
    )
    material_hs = load_material_hs_mapping(MATERIAL_HS_MAPPING_PATH)
    collection_completeness = validate_collection_completeness(
        material_hs["hs_code"].drop_duplicates().tolist()
    )
    current_config = load_module_c_config()
    config, calibration, report = calibrate_trade_policy(
        totals,
        countries,
        current_config,
    )
    report["collection_completeness"] = collection_completeness
    calibration.to_csv(
        TRADE_WEIGHT_CALIBRATION_TABLE_PATH,
        index=False,
    )
    (
        calibration.sort_values(
            [
                "next_supply_disruption_proxy",
                "final_calibrated_risk",
                "STD_YYYYMM",
                "hs_code",
            ],
            ascending=[False, False, False, True],
        )
        .head(1000)
        .to_csv(
            TRADE_WEIGHT_CALIBRATION_SAMPLE_PATH,
            index=False,
            encoding="utf-8-sig",
        )
    )
    report["applied"] = apply
    report["config_path"] = str(MODULE_C_RISK_WEIGHT_PATH)
    report["observation_path"] = str(
        TRADE_WEIGHT_CALIBRATION_TABLE_PATH
    )
    report["policy_comparison_path"] = str(
        TRADE_WEIGHT_CALIBRATION_POLICY_PATH
    )
    report["sample_path"] = str(TRADE_WEIGHT_CALIBRATION_SAMPLE_PATH)
    report["country_cache_summary_path"] = str(
        TRADE_COUNTRY_CACHE_SUMMARY_PATH
    )
    build_country_cache_summary(
        countries,
        material_hs["hs_code"].drop_duplicates().tolist(),
    ).to_csv(
        TRADE_COUNTRY_CACHE_SUMMARY_PATH,
        index=False,
        encoding="utf-8-sig",
    )
    build_policy_comparison_table(report).to_csv(
        TRADE_WEIGHT_CALIBRATION_POLICY_PATH,
        index=False,
        encoding="utf-8-sig",
    )
    if apply:
        write_json(config, MODULE_C_RISK_WEIGHT_PATH)
    write_json(report, TRADE_WEIGHT_CALIBRATION_REPORT_PATH)
    LOGGER.info(
        "Saved trade weight calibration report: %s",
        TRADE_WEIGHT_CALIBRATION_REPORT_PATH,
    )
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Calibrate trade thresholds and weights from KCS data"
    )
    parser.add_argument("--apply", action="store_true")
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    print(run_trade_weight_calibration(apply=args.apply))
