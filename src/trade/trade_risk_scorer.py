from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import (
    HSK_REFERENCE_NORMALIZED_PATH,
    HSK_REFERENCE_SOURCE_PATH,
    MATERIAL_HS_MAPPING_PATH,
    MONTHLY_STOCK_PATH,
    OUTPUT_DIR,
    STOCK_MATERIAL_MAPPING_PATH,
    TRADE_HS_FEATURE_PATH,
    TRADE_HS_FEATURE_SAMPLE_PATH,
    TRADE_RISK_AUDIT_PATH,
    TRADE_RISK_SCORE_PATH,
    TRADE_RUN_REPORT_PATH,
)
from ..material_mapping import load_approved_stock_material_mapping
from ..module_c.config import load_module_c_config
from ..utils import ensure_dirs, setup_logging, write_json
from .hsk_reference import build_hsk_reference_outputs, load_hsk_reference
from .trade_collector import (
    collect_trade_flows,
    load_trade_country_scope,
    normalize_trade_flows,
)


LOGGER = logging.getLogger(__name__)
ITEM_MONTH_KEY_COLUMNS = ["STD_YYYYMM", "stock_item_key"]
MATERIAL_HS_COLUMNS = [
    "raw_material_meta_code",
    "hs_code",
    "hs_item_name_ko",
    "relation_type",
    "mapping_weight",
    "proxy_quality",
    "review_status",
    "evidence_reference",
    "valid_from",
    "valid_to",
    "mapping_version",
]
HS_FEATURE_COLUMNS = [
    "STD_YYYYMM",
    "hs_code",
    "hs_trade_risk",
    "import_volume_yoy_change",
    "import_value_yoy_change",
    "import_unit_value_yoy_change",
    "net_import_volume_yoy_change",
    "export_volume_yoy_change",
    "import_volume_rolling_cv",
    "import_unit_value_rolling_volatility",
    "zero_import_streak_months",
    "supplier_count",
    "supplier_count_yoy_change",
    "country_hhi_yoy_change",
    "import_volume_decline_risk",
    "net_import_availability_decline_risk",
    "import_interruption_risk",
    "import_unit_value_increase_risk",
    "import_volume_volatility_risk",
    "import_unit_value_volatility_risk",
    "country_concentration_risk",
    "supplier_count_decline_risk",
    "net_import_exposure_risk",
    "export_volume_surge_risk",
    "country_import_coverage",
    "country_top1_share",
    "country_hhi",
    "trade_signal_confidence",
    "trade_event_codes",
]
SCORE_COLUMNS = [
    "STD_YYYYMM",
    "stock_item_key",
    "trade_risk",
    "trade_import_volume_risk",
    "trade_net_import_availability_risk",
    "trade_import_interruption_risk",
    "trade_import_unit_value_risk",
    "trade_import_volume_volatility_risk",
    "trade_import_unit_value_volatility_risk",
    "trade_country_concentration_risk",
    "trade_supplier_count_risk",
    "trade_net_import_exposure_risk",
    "trade_export_volume_surge_risk",
    "trade_signal_confidence",
    "trade_factor_count",
    "trade_hs_codes",
    "trade_event_codes",
]
AUDIT_COLUMNS = [
    "STD_YYYYMM",
    "stock_item_key",
    "raw_material_meta_code",
    "hs_code",
    "hs_item_name_ko",
    "relation_type",
    "hs_trade_risk",
    "path_weight",
    "risk_contribution",
    "import_volume_yoy_change",
    "import_value_yoy_change",
    "import_unit_value_yoy_change",
    "net_import_volume_yoy_change",
    "export_volume_yoy_change",
    "import_volume_rolling_cv",
    "import_unit_value_rolling_volatility",
    "zero_import_streak_months",
    "supplier_count",
    "supplier_count_yoy_change",
    "country_hhi_yoy_change",
    "import_volume_decline_risk",
    "net_import_availability_decline_risk",
    "import_interruption_risk",
    "import_unit_value_increase_risk",
    "import_volume_volatility_risk",
    "import_unit_value_volatility_risk",
    "country_concentration_risk",
    "supplier_count_decline_risk",
    "net_import_exposure_risk",
    "export_volume_surge_risk",
    "country_import_coverage",
    "country_top1_share",
    "country_hhi",
    "trade_signal_confidence",
    "trade_event_codes",
    "mapping_version",
    "evidence_reference",
]


def _empty_scores() -> pd.DataFrame:
    return pd.DataFrame(columns=SCORE_COLUMNS)


def _empty_audit() -> pd.DataFrame:
    return pd.DataFrame(columns=AUDIT_COLUMNS)


def load_material_hs_mapping(
    path: Path = MATERIAL_HS_MAPPING_PATH,
    hsk_reference: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=MATERIAL_HS_COLUMNS)
    mapping = pd.read_csv(
        path,
        dtype={"hs_code": "string"},
        keep_default_na=False,
    )
    missing = [column for column in MATERIAL_HS_COLUMNS if column not in mapping.columns]
    if missing:
        raise ValueError(f"Material-HS mapping is missing columns: {missing}")
    mapping = mapping[
        mapping["review_status"].astype(str).str.strip().str.lower().eq("approved")
    ].copy()
    mapping["hs_code"] = mapping["hs_code"].astype("string").str.strip()
    invalid_hs = ~mapping["hs_code"].str.fullmatch(r"\d{10}")
    if invalid_hs.any():
        raise ValueError(
            "Approved material-HS mappings require 10-digit HSK codes: "
            f"{mapping.loc[invalid_hs, 'hs_code'].tolist()}"
        )
    for column in ["mapping_weight", "proxy_quality"]:
        mapping[column] = pd.to_numeric(mapping[column], errors="coerce")
        if mapping[column].isna().any() or ~mapping[column].between(0, 1).all():
            raise ValueError(f"{column} must be within 0..1")
    mapping["valid_from"] = pd.to_datetime(mapping["valid_from"], errors="coerce")
    mapping["valid_to"] = pd.to_datetime(mapping["valid_to"], errors="coerce")
    if mapping[["valid_from", "valid_to"]].isna().any(axis=None):
        raise ValueError("Material-HS mapping contains invalid effective dates")
    duplicate = mapping.duplicated(
        ["raw_material_meta_code", "hs_code"], keep=False
    )
    if duplicate.any():
        raise ValueError("Material-HS mapping contains duplicate approved paths")

    if hsk_reference is not None and not mapping.empty:
        leaf = hsk_reference[hsk_reference["is_trade_leaf"]][
            ["hs_code", "item_name_ko"]
        ].drop_duplicates("hs_code").rename(
            columns={"item_name_ko": "official_hs_item_name_ko"}
        )
        checked = mapping.merge(
            leaf,
            on="hs_code",
            how="left",
            validate="many_to_one",
        )
        missing_hsk = checked["official_hs_item_name_ko"].isna()
        if missing_hsk.any():
            raise ValueError(
                "Approved material-HS codes are absent from the official HSK: "
                f"{checked.loc[missing_hsk, 'hs_code'].tolist()}"
            )
        name_mismatch = checked["hs_item_name_ko"].ne(
            checked["official_hs_item_name_ko"]
        )
        if name_mismatch.any():
            rows = checked.loc[
                name_mismatch,
                ["hs_code", "hs_item_name_ko", "official_hs_item_name_ko"],
            ]
            raise ValueError(
                "Material-HS names do not match the official HSK: "
                f"{rows.to_dict(orient='records')}"
            )
    return mapping[MATERIAL_HS_COLUMNS].reset_index(drop=True)


def _previous_year_values(features: pd.DataFrame) -> pd.DataFrame:
    prior = features[
        [
            "month",
            "hs_code",
            "import_weight_kg",
            "import_value_usd",
            "import_unit_value_usd_per_kg",
            "export_weight_kg",
            "net_import_weight_kg",
        ]
    ].copy()
    prior["month"] = prior["month"] + pd.offsets.DateOffset(years=1)
    return prior.rename(
        columns={
            "import_weight_kg": "prior_import_weight_kg",
            "import_value_usd": "prior_import_value_usd",
            "import_unit_value_usd_per_kg": "prior_import_unit_value_usd_per_kg",
            "export_weight_kg": "prior_export_weight_kg",
            "net_import_weight_kg": "prior_net_import_weight_kg",
        }
    )


def _zero_streak(values: pd.Series) -> pd.Series:
    streak = 0
    result = []
    for value in pd.to_numeric(values, errors="coerce").fillna(0.0):
        if value <= 0:
            streak += 1
        else:
            streak = 0
        result.append(streak)
    return pd.Series(result, index=values.index, dtype="int64")


def _country_metrics(
    totals: pd.DataFrame,
    country_flows: pd.DataFrame,
    coverage_min: float,
) -> pd.DataFrame:
    columns = [
        "STD_YYYYMM",
        "hs_code",
        "country_import_coverage",
        "country_top1_share",
        "country_hhi",
        "supplier_count",
        "country_metric_available",
    ]
    if country_flows.empty:
        return pd.DataFrame(columns=columns)
    countries = normalize_trade_flows(country_flows)
    countries = countries[countries["country_code"].ne("ALL")].copy()
    if countries.empty:
        return pd.DataFrame(columns=columns)
    countries = (
        countries.groupby(
            ["STD_YYYYMM", "hs_code", "country_code"],
            as_index=False,
            observed=True,
        )["import_value_usd"]
        .sum()
    )
    total_values = totals[
        ["STD_YYYYMM", "hs_code", "import_value_usd"]
    ].rename(columns={"import_value_usd": "total_import_value_usd"})
    countries = countries.merge(
        total_values,
        on=["STD_YYYYMM", "hs_code"],
        how="inner",
        validate="many_to_one",
    )
    countries["country_share"] = (
        countries["import_value_usd"]
        .div(countries["total_import_value_usd"].replace(0, np.nan))
        .fillna(0.0)
        .clip(0, 1)
    )
    grouped = (
        countries.groupby(
            ["STD_YYYYMM", "hs_code"],
            as_index=False,
            observed=True,
        )
        .agg(
            tracked_import_value_usd=("import_value_usd", "sum"),
            total_import_value_usd=("total_import_value_usd", "first"),
            country_top1_share=("country_share", "max"),
            country_hhi=("country_share", lambda values: float(np.square(values).sum())),
            supplier_count=(
                "import_value_usd",
                lambda values: int(pd.Series(values).gt(0).sum()),
            ),
        )
    )
    grouped["country_import_coverage"] = (
        grouped["tracked_import_value_usd"]
        .div(grouped["total_import_value_usd"].replace(0, np.nan))
        .fillna(0.0)
        .clip(0, 1)
    )
    grouped["country_metric_available"] = grouped[
        "country_import_coverage"
    ].ge(coverage_min)
    return grouped[columns]


def _event_codes(row: pd.Series, watch: float) -> str:
    events = []
    if row["import_volume_decline_risk"] >= watch:
        events.append("HS_IMPORT_VOLUME_DROP")
    if row["net_import_availability_decline_risk"] >= watch:
        events.append("HS_NET_IMPORT_AVAILABILITY_DROP")
    if row["import_interruption_risk"] >= watch:
        events.append("HS_IMPORT_INTERRUPTION")
    if row["import_unit_value_increase_risk"] >= watch:
        events.append("HS_IMPORT_UNIT_VALUE_SHOCK")
    if row["import_volume_volatility_risk"] >= watch:
        events.append("HS_IMPORT_VOLUME_VOLATILITY")
    if row["import_unit_value_volatility_risk"] >= watch:
        events.append("HS_IMPORT_UNIT_VALUE_VOLATILITY")
    if row["country_concentration_risk"] >= watch:
        events.append("HS_IMPORT_COUNTRY_CONCENTRATION")
    if row["supplier_count_decline_risk"] >= watch:
        events.append("HS_IMPORT_SUPPLIER_COUNT_DROP")
    if row["net_import_exposure_risk"] >= watch:
        events.append("HS_NET_IMPORT_EXPOSURE")
    if row["export_volume_surge_risk"] >= watch:
        events.append("HS_EXPORT_VOLUME_SURGE")
    return ";".join(events)


def build_hs_trade_features(
    total_flows: pd.DataFrame,
    country_flows: pd.DataFrame | None = None,
    config: dict | None = None,
) -> pd.DataFrame:
    if total_flows.empty:
        return pd.DataFrame(columns=HS_FEATURE_COLUMNS)
    config = config or load_module_c_config()
    policy = config["trade_signal"]
    totals = normalize_trade_flows(total_flows)
    totals = totals[totals["country_code"].eq("ALL")].copy()
    totals = (
        totals.groupby(
            ["STD_YYYYMM", "hs_code"],
            as_index=False,
            observed=True,
        )[
            [
                "export_weight_kg",
                "export_value_usd",
                "import_weight_kg",
                "import_value_usd",
            ]
        ]
        .sum()
    )
    if totals.empty:
        return pd.DataFrame(columns=HS_FEATURE_COLUMNS)

    totals["month"] = pd.to_datetime(totals["STD_YYYYMM"], errors="coerce")
    totals = totals.sort_values(["hs_code", "month"]).reset_index(drop=True)
    totals["import_unit_value_usd_per_kg"] = (
        totals["import_value_usd"]
        .div(totals["import_weight_kg"].replace(0, np.nan))
    )
    totals["net_import_weight_kg"] = (
        totals["import_weight_kg"] - totals["export_weight_kg"]
    ).clip(lower=0)
    rolling_window = int(policy["rolling_window_months"])
    rolling_min = int(policy["rolling_min_periods"])
    hs_groups = totals.groupby("hs_code", sort=False, group_keys=False)
    totals["import_volume_rolling_cv"] = hs_groups[
        "import_weight_kg"
    ].transform(
        lambda values: values.rolling(
            rolling_window,
            min_periods=rolling_min,
        )
        .std(ddof=0)
        .div(
            values.rolling(
                rolling_window,
                min_periods=rolling_min,
            ).mean().replace(0, np.nan)
        )
    )
    unit_value_log_change = hs_groups[
        "import_unit_value_usd_per_kg"
    ].transform(lambda values: np.log(values.where(values.gt(0))).diff())
    totals["import_unit_value_rolling_volatility"] = (
        unit_value_log_change.groupby(totals["hs_code"], sort=False).transform(
            lambda values: values.rolling(
                rolling_window,
                min_periods=rolling_min,
            ).std(ddof=0)
        )
    )
    totals["zero_import_streak_months"] = hs_groups[
        "import_weight_kg"
    ].transform(_zero_streak)
    totals["prior_positive_import_available"] = hs_groups[
        "import_weight_kg"
    ].transform(
        lambda values: values.shift(1)
        .rolling(12, min_periods=1)
        .max()
        .fillna(0)
        .gt(0)
    )
    features = totals.merge(
        _previous_year_values(totals),
        on=["month", "hs_code"],
        how="left",
        validate="one_to_one",
    )
    features["import_volume_yoy_change"] = (
        features["import_weight_kg"]
        .div(features["prior_import_weight_kg"].replace(0, np.nan))
        .sub(1.0)
    )
    features["import_value_yoy_change"] = (
        features["import_value_usd"]
        .div(features["prior_import_value_usd"].replace(0, np.nan))
        .sub(1.0)
    )
    features["import_unit_value_yoy_change"] = (
        features["import_unit_value_usd_per_kg"]
        .div(features["prior_import_unit_value_usd_per_kg"].replace(0, np.nan))
        .sub(1.0)
    )
    features["net_import_volume_yoy_change"] = (
        features["net_import_weight_kg"]
        .div(features["prior_net_import_weight_kg"].replace(0, np.nan))
        .sub(1.0)
    )
    features["export_volume_yoy_change"] = (
        features["export_weight_kg"]
        .div(features["prior_export_weight_kg"].replace(0, np.nan))
        .sub(1.0)
    )
    features["import_volume_decline_risk"] = (
        -features["import_volume_yoy_change"]
    ).clip(lower=0).div(float(policy["import_volume_decline_threshold"])).clip(0, 1)
    features["net_import_availability_decline_risk"] = (
        -features["net_import_volume_yoy_change"]
    ).clip(lower=0).div(
        float(policy["net_import_availability_decline_threshold"])
    ).clip(0, 1)
    interruption_available = (
        features["import_weight_kg"].gt(0)
        | features["prior_positive_import_available"].fillna(False)
    )
    features["import_interruption_risk"] = (
        features["zero_import_streak_months"]
        .div(float(policy["import_interruption_streak_months"]))
        .clip(0, 1)
        .where(interruption_available, 0.0)
    )
    features["import_unit_value_increase_risk"] = features[
        "import_unit_value_yoy_change"
    ].clip(lower=0).div(
        float(policy["import_unit_value_increase_threshold"])
    ).clip(0, 1)
    features["import_volume_volatility_risk"] = features[
        "import_volume_rolling_cv"
    ].div(float(policy["import_volume_volatility_threshold"])).clip(0, 1)
    features["import_unit_value_volatility_risk"] = features[
        "import_unit_value_rolling_volatility"
    ].div(float(policy["import_unit_value_volatility_threshold"])).clip(0, 1)
    features["net_import_exposure_risk"] = (
        (features["import_value_usd"] - features["export_value_usd"])
        .clip(lower=0)
        .div(features["import_value_usd"].replace(0, np.nan))
        .fillna(0.0)
        .clip(0, 1)
    )
    features["export_volume_surge_risk"] = features[
        "export_volume_yoy_change"
    ].clip(lower=0).div(
        float(policy["export_volume_surge_threshold"])
    ).clip(0, 1)

    country = _country_metrics(
        totals,
        country_flows if country_flows is not None else pd.DataFrame(),
        float(policy["country_coverage_min"]),
    )
    features = features.merge(
        country,
        on=["STD_YYYYMM", "hs_code"],
        how="left",
        validate="one_to_one",
    )
    country_prior = country.copy()
    if not country_prior.empty:
        prior_month = pd.to_datetime(
            country_prior["STD_YYYYMM"],
            errors="coerce",
        ) + pd.offsets.DateOffset(years=1)
        country_prior["STD_YYYYMM"] = prior_month.dt.strftime("%Y-%m")
    country_prior = country_prior.rename(
        columns={
            "country_import_coverage": "prior_country_import_coverage",
            "country_top1_share": "prior_country_top1_share",
            "country_hhi": "prior_country_hhi",
            "supplier_count": "prior_supplier_count",
            "country_metric_available": "prior_country_metric_available",
        }
    )
    features = features.merge(
        country_prior[
            [
                "STD_YYYYMM",
                "hs_code",
                "prior_country_import_coverage",
                "prior_country_top1_share",
                "prior_country_hhi",
                "prior_supplier_count",
                "prior_country_metric_available",
            ]
        ],
        on=["STD_YYYYMM", "hs_code"],
        how="left",
        validate="one_to_one",
    )
    for column in [
        "country_import_coverage",
        "country_top1_share",
        "country_hhi",
        "supplier_count",
        "prior_country_import_coverage",
        "prior_country_top1_share",
        "prior_country_hhi",
        "prior_supplier_count",
    ]:
        features[column] = pd.to_numeric(features[column], errors="coerce").fillna(0.0)
    features["country_metric_available"] = (
        features["country_metric_available"].astype("boolean").fillna(False).astype(bool)
    )
    features["prior_country_metric_available"] = (
        features["prior_country_metric_available"]
        .astype("boolean")
        .fillna(False)
        .astype(bool)
    )
    country_yoy_available = (
        features["country_metric_available"]
        & features["prior_country_metric_available"]
        & features["prior_supplier_count"].gt(0)
    )
    features["supplier_count_yoy_change"] = (
        features["supplier_count"]
        .div(features["prior_supplier_count"].replace(0, np.nan))
        .sub(1.0)
        .where(country_yoy_available)
    )
    features["country_hhi_yoy_change"] = (
        features["country_hhi"]
        .div(features["prior_country_hhi"].replace(0, np.nan))
        .sub(1.0)
        .where(country_yoy_available & features["prior_country_hhi"].gt(0))
    )
    concentration_metric = features[
        ["country_top1_share", "country_hhi"]
    ].max(axis=1)
    threshold = float(policy["country_concentration_threshold"])
    features["country_concentration_risk"] = (
        (concentration_metric - threshold) / (1.0 - threshold)
    ).clip(0, 1).where(features["country_metric_available"], 0.0)
    features["supplier_count_decline_risk"] = (
        -features["supplier_count_yoy_change"]
    ).clip(lower=0).div(
        float(policy["supplier_count_decline_threshold"])
    ).clip(0, 1).fillna(0.0)

    component_weights = {
        "import_volume_decline_risk": float(policy["import_volume_decline"]),
        "net_import_availability_decline_risk": float(
            policy["net_import_availability_decline"]
        ),
        "import_interruption_risk": float(policy["import_interruption"]),
        "import_unit_value_increase_risk": float(
            policy["import_unit_value_increase"]
        ),
        "import_volume_volatility_risk": float(
            policy["import_volume_volatility"]
        ),
        "import_unit_value_volatility_risk": float(
            policy["import_unit_value_volatility"]
        ),
        "country_concentration_risk": float(policy["country_concentration"]),
        "supplier_count_decline_risk": float(
            policy["supplier_count_decline"]
        ),
        "net_import_exposure_risk": float(policy["net_import_exposure"]),
        "export_volume_surge_risk": float(policy["export_volume_surge"]),
    }
    features["hs_trade_risk"] = sum(
        weight * features[column].fillna(0.0)
        for column, weight in component_weights.items()
    ).clip(0, 1)
    available = {
        "import_volume_decline_risk": features["import_volume_yoy_change"].notna(),
        "net_import_availability_decline_risk": features[
            "net_import_volume_yoy_change"
        ].notna(),
        "import_interruption_risk": interruption_available,
        "import_unit_value_increase_risk": features[
            "import_unit_value_yoy_change"
        ].notna(),
        "import_volume_volatility_risk": features[
            "import_volume_rolling_cv"
        ].notna(),
        "import_unit_value_volatility_risk": features[
            "import_unit_value_rolling_volatility"
        ].notna(),
        "country_concentration_risk": features["country_metric_available"],
        "supplier_count_decline_risk": country_yoy_available,
        "net_import_exposure_risk": features["import_value_usd"].gt(0),
        "export_volume_surge_risk": features["prior_export_weight_kg"].gt(0),
    }
    features["trade_signal_confidence"] = sum(
        component_weights[column] * mask.astype(float)
        for column, mask in available.items()
    ).clip(0, 1)
    watch = float(config["alert_thresholds"]["watch"])
    features["trade_event_codes"] = features.apply(
        _event_codes,
        axis=1,
        watch=watch,
    )
    return features[HS_FEATURE_COLUMNS].sort_values(
        ["hs_code", "STD_YYYYMM"]
    ).reset_index(drop=True)


def _mapping_confidence_score(value: object) -> float:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.notna(numeric):
        return float(np.clip(numeric, 0, 1))
    return {
        "verified": 1.0,
        "high": 0.90,
        "medium": 0.65,
        "low": 0.35,
    }.get(str(value).strip().lower(), 0.50)


def _stock_material_paths(mapping: pd.DataFrame) -> pd.DataFrame:
    if mapping.empty:
        return mapping.copy()
    result = mapping.copy()
    if "review_status" in result.columns:
        result = result[
            result["review_status"].astype(str).str.strip().str.lower().eq("approved")
        ].copy()
    for column, default in {
        "raw_material_meta_code": "",
        "mapping_weight": 1.0,
        "exposure_score": 1.0,
        "mapping_confidence": "medium",
    }.items():
        if column not in result.columns:
            result[column] = default
    result = result[
        result["raw_material_meta_code"].astype(str).str.strip().ne("")
    ].copy()
    result["raw_material_meta_code"] = result["raw_material_meta_code"].str.split(";")
    result = result.explode("raw_material_meta_code")
    result["raw_material_meta_code"] = result["raw_material_meta_code"].str.strip()
    result["mapping_weight"] = pd.to_numeric(
        result["mapping_weight"], errors="coerce"
    ).fillna(0.0).clip(0, 1)
    result["exposure_score"] = pd.to_numeric(
        result["exposure_score"], errors="coerce"
    ).fillna(0.0).clip(0, 1)
    result["mapping_confidence_score"] = result["mapping_confidence"].map(
        _mapping_confidence_score
    )
    return result


def _compound_risk(values: pd.Series) -> float:
    clipped = pd.to_numeric(values, errors="coerce").fillna(0.0).clip(0, 1)
    return float(1.0 - np.prod(1.0 - clipped.to_numpy()))


def _join_unique(values: pd.Series) -> str:
    return ";".join(sorted({str(value).strip() for value in values if str(value).strip()}))


def _item_event_codes(scores: pd.DataFrame, watch: float) -> pd.Series:
    result = pd.Series("", index=scores.index, dtype="string")
    components = [
        ("trade_import_volume_risk", "HS_IMPORT_VOLUME_DROP"),
        (
            "trade_net_import_availability_risk",
            "HS_NET_IMPORT_AVAILABILITY_DROP",
        ),
        ("trade_import_interruption_risk", "HS_IMPORT_INTERRUPTION"),
        ("trade_import_unit_value_risk", "HS_IMPORT_UNIT_VALUE_SHOCK"),
        (
            "trade_import_volume_volatility_risk",
            "HS_IMPORT_VOLUME_VOLATILITY",
        ),
        (
            "trade_import_unit_value_volatility_risk",
            "HS_IMPORT_UNIT_VALUE_VOLATILITY",
        ),
        (
            "trade_country_concentration_risk",
            "HS_IMPORT_COUNTRY_CONCENTRATION",
        ),
        ("trade_supplier_count_risk", "HS_IMPORT_SUPPLIER_COUNT_DROP"),
        ("trade_net_import_exposure_risk", "HS_NET_IMPORT_EXPOSURE"),
        ("trade_export_volume_surge_risk", "HS_EXPORT_VOLUME_SURGE"),
    ]
    for column, event_code in components:
        mask = pd.to_numeric(scores[column], errors="coerce").fillna(0).ge(watch)
        existing = result.loc[mask]
        result.loc[mask] = existing.where(
            existing.eq(""),
            existing + ";",
        ) + event_code
    return result


def build_trade_risk_outputs(
    total_flows: pd.DataFrame,
    country_flows: pd.DataFrame | None = None,
    stock_mapping: pd.DataFrame | None = None,
    material_hs_mapping: pd.DataFrame | None = None,
    hsk_reference: pd.DataFrame | None = None,
    config: dict | None = None,
    hs_features: pd.DataFrame | None = None,
    score_months: set[str] | None = None,
    score_stock_item_months: pd.DataFrame | None = None,
    audit_scope: str = "all",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    config = config or load_module_c_config()
    factors = (
        hs_features.copy()
        if hs_features is not None
        else build_hs_trade_features(total_flows, country_flows, config)
    )
    if factors.empty:
        return _empty_scores(), _empty_audit()
    if score_months is not None:
        factors = factors[
            factors["STD_YYYYMM"].astype(str).isin(score_months)
        ].copy()
    if factors.empty:
        return _empty_scores(), _empty_audit()
    if audit_scope not in {"all", "latest"}:
        raise ValueError("audit_scope must be 'all' or 'latest'")
    observed_items_by_month: dict[str, set[str]] = {}
    if score_stock_item_months is not None:
        missing_keys = set(ITEM_MONTH_KEY_COLUMNS) - set(
            score_stock_item_months.columns
        )
        if missing_keys:
            raise ValueError(
                "score_stock_item_months is missing columns: "
                f"{sorted(missing_keys)}"
            )
        observed = score_stock_item_months[
            ITEM_MONTH_KEY_COLUMNS
        ].drop_duplicates().copy()
        observed["STD_YYYYMM"] = observed["STD_YYYYMM"].astype(str)
        observed["stock_item_key"] = observed["stock_item_key"].astype(str)
        observed_items_by_month = {
            str(month): set(rows["stock_item_key"])
            for month, rows in observed.groupby(
                "STD_YYYYMM",
                sort=False,
                observed=True,
            )
        }

    if hsk_reference is None:
        hsk_reference = load_hsk_reference()
    if material_hs_mapping is None:
        material_hs_mapping = load_material_hs_mapping(
            hsk_reference=hsk_reference
        )
    else:
        material_hs_mapping = material_hs_mapping.copy()
        material_hs_mapping = material_hs_mapping[
            material_hs_mapping["review_status"]
            .astype(str)
            .str.strip()
            .str.lower()
            .eq("approved")
        ]
        material_hs_mapping["valid_from"] = pd.to_datetime(
            material_hs_mapping["valid_from"], errors="coerce"
        )
        material_hs_mapping["valid_to"] = pd.to_datetime(
            material_hs_mapping["valid_to"], errors="coerce"
        )
        for column in ["mapping_weight", "proxy_quality"]:
            material_hs_mapping[column] = pd.to_numeric(
                material_hs_mapping[column], errors="coerce"
            ).fillna(0.0).clip(0, 1)
    if stock_mapping is None:
        stock_mapping = load_approved_stock_material_mapping(
            STOCK_MATERIAL_MAPPING_PATH,
            eligibility_column="identity_approved",
        )
    stock_paths = _stock_material_paths(stock_mapping)
    if stock_paths.empty or material_hs_mapping.empty:
        return _empty_scores(), _empty_audit()

    hs_paths = material_hs_mapping.rename(
        columns={
            "mapping_weight": "hs_mapping_weight",
            "proxy_quality": "hs_proxy_quality",
            "mapping_version": "hs_mapping_version",
            "evidence_reference": "hs_evidence_reference",
        }
    )
    paths = stock_paths.merge(
        hs_paths,
        on="raw_material_meta_code",
        how="inner",
        validate="many_to_many",
    )
    if paths.empty:
        return _empty_scores(), _empty_audit()
    paths["stock_item_key"] = paths["stock_item_key"].astype(str)

    weighted_components = [
        ("import_volume_decline_risk", "weighted_volume_risk"),
        (
            "net_import_availability_decline_risk",
            "weighted_net_import_availability_risk",
        ),
        ("import_interruption_risk", "weighted_import_interruption_risk"),
        ("import_unit_value_increase_risk", "weighted_unit_value_risk"),
        (
            "import_volume_volatility_risk",
            "weighted_import_volume_volatility_risk",
        ),
        (
            "import_unit_value_volatility_risk",
            "weighted_import_unit_value_volatility_risk",
        ),
        ("country_concentration_risk", "weighted_concentration_risk"),
        ("supplier_count_decline_risk", "weighted_supplier_count_risk"),
        ("net_import_exposure_risk", "weighted_net_import_risk"),
        ("export_volume_surge_risk", "weighted_export_volume_surge_risk"),
        ("trade_signal_confidence", "weighted_trade_confidence"),
    ]
    weighted_to_score = {
        "weighted_volume_risk": "trade_import_volume_risk",
        "weighted_net_import_availability_risk": (
            "trade_net_import_availability_risk"
        ),
        "weighted_import_interruption_risk": "trade_import_interruption_risk",
        "weighted_unit_value_risk": "trade_import_unit_value_risk",
        "weighted_import_volume_volatility_risk": (
            "trade_import_volume_volatility_risk"
        ),
        "weighted_import_unit_value_volatility_risk": (
            "trade_import_unit_value_volatility_risk"
        ),
        "weighted_concentration_risk": "trade_country_concentration_risk",
        "weighted_supplier_count_risk": "trade_supplier_count_risk",
        "weighted_net_import_risk": "trade_net_import_exposure_risk",
        "weighted_export_volume_surge_risk": "trade_export_volume_surge_risk",
        "weighted_trade_confidence": "trade_signal_confidence",
    }
    normalized_score_columns = [
        "trade_import_volume_risk",
        "trade_net_import_availability_risk",
        "trade_import_interruption_risk",
        "trade_import_unit_value_risk",
        "trade_import_volume_volatility_risk",
        "trade_import_unit_value_volatility_risk",
        "trade_country_concentration_risk",
        "trade_supplier_count_risk",
        "trade_net_import_exposure_risk",
        "trade_export_volume_surge_risk",
        "trade_signal_confidence",
    ]
    item_hs = (
        paths[["stock_item_key", "hs_code"]]
        .drop_duplicates()
        .sort_values(["stock_item_key", "hs_code"])
    )
    path_metadata = (
        item_hs.groupby("stock_item_key", as_index=False, observed=True)
        .agg(
            trade_factor_count=("hs_code", "nunique"),
            trade_hs_codes=("hs_code", _join_unique),
        )
    )
    watch = float(config["alert_thresholds"]["watch"])
    latest_month = factors["STD_YYYYMM"].max()
    score_frames = []
    audit_frames = []
    for month_value, monthly_factors in factors.groupby(
        "STD_YYYYMM",
        sort=True,
        observed=True,
    ):
        month_paths = paths
        if observed_items_by_month:
            observed_items = observed_items_by_month.get(str(month_value), set())
            if not observed_items:
                continue
            month_paths = paths[
                paths["stock_item_key"].isin(observed_items)
            ]
        merged = month_paths.merge(
            monthly_factors,
            on="hs_code",
            how="inner",
            validate="many_to_many",
        )
        if merged.empty:
            continue
        month_date = pd.Timestamp(f"{month_value}-01")
        merged = merged[
            merged["valid_from"].le(month_date)
            & merged["valid_to"].ge(month_date)
        ].copy()
        if merged.empty:
            continue
        merged["path_weight"] = (
            merged["mapping_weight"]
            * merged["exposure_score"]
            * merged["mapping_confidence_score"]
            * pd.to_numeric(
                merged["hs_mapping_weight"],
                errors="coerce",
            ).fillna(0.0)
            * pd.to_numeric(
                merged["hs_proxy_quality"],
                errors="coerce",
            ).fillna(0.0)
        ).clip(0, 1)
        merged["risk_contribution"] = (
            merged["hs_trade_risk"] * merged["path_weight"]
        ).clip(0, 1)
        for source, target in weighted_components:
            merged[target] = merged[source] * merged["path_weight"]

        survival = (
            1.0 - merged["risk_contribution"].clip(0, 1)
        ).clip(lower=np.finfo(float).tiny)
        merged["log_survival"] = np.log(survival)
        sum_columns = [
            "path_weight",
            "log_survival",
            *weighted_to_score.keys(),
        ]
        monthly_scores = merged.groupby(
            "stock_item_key",
            as_index=False,
            observed=True,
        )[sum_columns].sum()
        monthly_scores = monthly_scores.rename(
            columns={
                "path_weight": "path_weight_sum",
                **weighted_to_score,
            }
        )
        monthly_scores.insert(0, "STD_YYYYMM", month_value)
        monthly_scores["trade_risk"] = (
            1.0 - np.exp(monthly_scores.pop("log_survival"))
        ).clip(0, 1)
        denominator = monthly_scores["path_weight_sum"].replace(0, np.nan)
        for column in normalized_score_columns:
            monthly_scores[column] = (
                monthly_scores[column].div(denominator).fillna(0.0)
            )
        monthly_scores = monthly_scores.drop(columns=["path_weight_sum"]).merge(
            path_metadata,
            on="stock_item_key",
            how="left",
            validate="one_to_one",
        )
        monthly_scores["trade_event_codes"] = _item_event_codes(
            monthly_scores,
            watch,
        )
        score_frames.append(monthly_scores)

        if audit_scope == "all" or month_value == latest_month:
            monthly_audit = merged.rename(
                columns={
                    "hs_mapping_version": "mapping_version",
                    "hs_evidence_reference": "evidence_reference",
                }
            )
            for column in AUDIT_COLUMNS:
                if column not in monthly_audit.columns:
                    monthly_audit[column] = ""
            audit_frames.append(monthly_audit[AUDIT_COLUMNS].copy())

    if not score_frames:
        return _empty_scores(), _empty_audit()
    scores = pd.concat(score_frames, ignore_index=True)
    audit = (
        pd.concat(audit_frames, ignore_index=True)
        if audit_frames
        else _empty_audit()
    )
    for column in [
        "trade_risk",
        *normalized_score_columns,
    ]:
        scores[column] = scores[column].clip(0, 1)
    return scores[SCORE_COLUMNS], audit[AUDIT_COLUMNS]


def run_trade_risk_scoring(
    provider: str | None = None,
) -> dict[str, object]:
    setup_logging()
    ensure_dirs(
        OUTPUT_DIR,
        HSK_REFERENCE_NORMALIZED_PATH.parent,
        TRADE_HS_FEATURE_SAMPLE_PATH.parent,
    )
    selected_provider = (
        provider or os.getenv("TRADE_PROVIDER", "disabled")
    ).strip().lower()
    if not HSK_REFERENCE_SOURCE_PATH.exists():
        if selected_provider not in {"disabled", "none"}:
            raise FileNotFoundError(
                "An official HSK workbook is required when trade scoring is enabled: "
                f"{HSK_REFERENCE_SOURCE_PATH}"
            )
        scores = _empty_scores()
        audit = _empty_audit()
        features = pd.DataFrame(columns=HS_FEATURE_COLUMNS)
        features.to_csv(TRADE_HS_FEATURE_PATH, index=False)
        features.to_csv(TRADE_HS_FEATURE_SAMPLE_PATH, index=False)
        scores.to_csv(TRADE_RISK_SCORE_PATH, index=False)
        audit.to_csv(TRADE_RISK_AUDIT_PATH, index=False)
        report = {
            "module": "trade",
            "hsk_reference_version": "",
            "hsk_reference_rows": 0,
            "approved_material_hs_paths": 0,
            "approved_material_codes": [],
            "approved_hs_codes": [],
            "total_trade_observations": 0,
            "country_trade_observations": 0,
            "trade_score_rows": 0,
            "trade_scored_stock_items": 0,
            "operational_status": "blocked_missing_hsk_reference",
            "hs_feature_path": str(TRADE_HS_FEATURE_PATH),
            "hs_feature_sample_path": str(TRADE_HS_FEATURE_SAMPLE_PATH),
            "score_path": str(TRADE_RISK_SCORE_PATH),
            "audit_path": str(TRADE_RISK_AUDIT_PATH),
        }
        write_json(report, TRADE_RUN_REPORT_PATH)
        return report
    hsk_report = build_hsk_reference_outputs()
    hsk_reference = pd.read_parquet(HSK_REFERENCE_NORMALIZED_PATH)
    material_hs = load_material_hs_mapping(hsk_reference=hsk_reference)
    hs_codes = material_hs["hs_code"].drop_duplicates().tolist()
    totals, countries = collect_trade_flows(hs_codes, provider=provider)
    module_c_config = load_module_c_config()
    features = build_hs_trade_features(totals, countries, module_c_config)
    features.to_csv(TRADE_HS_FEATURE_PATH, index=False)
    (
        features.sort_values(
            ["hs_trade_risk", "STD_YYYYMM", "hs_code"],
            ascending=[False, False, True],
        )
        .head(1000)
        .to_csv(TRADE_HS_FEATURE_SAMPLE_PATH, index=False)
    )
    score_months: set[str] | None = None
    stock_item_months: pd.DataFrame | None = None
    if MONTHLY_STOCK_PATH.exists():
        stock_months = pd.read_parquet(
            MONTHLY_STOCK_PATH,
            columns=["year_month", "stock_item_key"],
        )
        stock_months["STD_YYYYMM"] = pd.to_datetime(
            stock_months.pop("year_month"),
            errors="coerce",
        ).dt.strftime("%Y-%m")
        stock_item_months = stock_months[
            ["STD_YYYYMM", "stock_item_key"]
        ].dropna()
        score_months = set(
            stock_item_months["STD_YYYYMM"].dropna().tolist()
        )
    scores, audit = build_trade_risk_outputs(
        totals,
        countries,
        material_hs_mapping=material_hs,
        hsk_reference=hsk_reference,
        config=module_c_config,
        hs_features=features,
        score_months=score_months,
        score_stock_item_months=stock_item_months,
        audit_scope="latest",
    )
    scores.to_csv(TRADE_RISK_SCORE_PATH, index=False)
    audit.to_csv(TRADE_RISK_AUDIT_PATH, index=False)
    coverage_min = float(module_c_config["trade_signal"]["country_coverage_min"])
    risk_columns = [
        column
        for column in HS_FEATURE_COLUMNS
        if column.endswith("_risk") and column != "hs_trade_risk"
    ]
    event_counts = (
        features["trade_event_codes"]
        .fillna("")
        .str.split(";")
        .explode()
        .loc[lambda values: values.ne("")]
        .value_counts()
        .sort_index()
        .to_dict()
        if not features.empty
        else {}
    )
    report = {
        "module": "trade",
        "hsk_reference_version": hsk_report["reference_version"],
        "hsk_reference_rows": hsk_report["rows"],
        "approved_material_hs_paths": int(len(material_hs)),
        "approved_material_codes": sorted(
            material_hs["raw_material_meta_code"].unique().tolist()
        ),
        "approved_hs_codes": sorted(material_hs["hs_code"].unique().tolist()),
        "total_trade_observations": int(len(totals)),
        "country_trade_observations": int(len(countries)),
        "configured_country_scope_codes": load_trade_country_scope(),
        "observed_country_codes": sorted(
            countries["country_code"].dropna().unique().tolist()
        ),
        "country_coverage_threshold": coverage_min,
        "country_coverage_hs_months_passing": int(
            features.get(
                "country_import_coverage",
                pd.Series(dtype="float64"),
            ).ge(coverage_min).sum()
        ),
        "country_coverage_hs_months_total": int(len(features)),
        "trade_feature_months": int(
            features.get(
                "STD_YYYYMM",
                pd.Series(dtype="string"),
            ).nunique()
        ),
        "trade_score_months": int(
            scores.get(
                "STD_YYYYMM",
                pd.Series(dtype="string"),
            ).nunique()
        ),
        "trade_score_month_min": (
            str(scores["STD_YYYYMM"].min()) if not scores.empty else ""
        ),
        "trade_score_month_max": (
            str(scores["STD_YYYYMM"].max()) if not scores.empty else ""
        ),
        "trade_audit_scope": "latest_scored_month_path_snapshot",
        "trade_score_scope": "observed_stock_item_months_only",
        "trade_score_rows": int(len(scores)),
        "trade_scored_stock_items": int(scores["stock_item_key"].nunique()),
        "trade_feature_variable_count": len(risk_columns),
        "trade_feature_variables": risk_columns,
        "trade_feature_maxima": {
            column: float(features[column].max()) if not features.empty else 0.0
            for column in risk_columns
        },
        "trade_event_counts": {
            str(code): int(count) for code, count in event_counts.items()
        },
        "max_hs_trade_risk": (
            float(features["hs_trade_risk"].max())
            if not features.empty
            else 0.0
        ),
        "max_item_trade_risk": (
            float(scores["trade_risk"].max()) if not scores.empty else 0.0
        ),
        "operational_status": (
            "ready" if not scores.empty else "blocked_no_trade_observations"
        ),
        "hs_feature_path": str(TRADE_HS_FEATURE_PATH),
        "hs_feature_sample_path": str(TRADE_HS_FEATURE_SAMPLE_PATH),
        "score_path": str(TRADE_RISK_SCORE_PATH),
        "audit_path": str(TRADE_RISK_AUDIT_PATH),
    }
    write_json(report, TRADE_RUN_REPORT_PATH)
    LOGGER.info(
        "Saved trade risk scores: %s (%s rows)",
        TRADE_RISK_SCORE_PATH,
        len(scores),
    )
    LOGGER.info(
        "Saved trade risk audit: %s (%s rows)",
        TRADE_RISK_AUDIT_PATH,
        len(audit),
    )
    status = str(report.get("operational_status", ""))
    if status.startswith("blocked"):
        # 차단 상태에서도 리포트는 정상 저장되므로, 로그가 없으면 배치가
        # 성공한 것처럼 보인다. 무역 신호가 0 인 이유를 반드시 드러낸다.
        LOGGER.warning(
            "Trade risk scoring is blocked (%s): 0 rows scored. "
            "Check TRADE_PROVIDER (current=%s), the HSK workbook, and the KCS cache.",
            status,
            selected_provider,
        )
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score KCS import/export risk")
    parser.add_argument("--provider", choices=["disabled", "csv", "kcs"], default=None)
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    print(run_trade_risk_scoring(args.provider))
