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
    OUTPUT_DIR,
    STOCK_MATERIAL_MAPPING_PATH,
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
    "import_unit_value_yoy_change",
    "import_volume_decline_risk",
    "import_unit_value_increase_risk",
    "country_concentration_risk",
    "net_import_exposure_risk",
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
    "trade_import_unit_value_risk",
    "trade_country_concentration_risk",
    "trade_net_import_exposure_risk",
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
    "import_unit_value_yoy_change",
    "import_volume_decline_risk",
    "import_unit_value_increase_risk",
    "country_concentration_risk",
    "net_import_exposure_risk",
    "country_import_coverage",
    "trade_signal_confidence",
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
        ["month", "hs_code", "import_weight_kg", "import_unit_value_usd_per_kg"]
    ].copy()
    prior["month"] = prior["month"] + pd.offsets.DateOffset(years=1)
    return prior.rename(
        columns={
            "import_weight_kg": "prior_import_weight_kg",
            "import_unit_value_usd_per_kg": "prior_import_unit_value_usd_per_kg",
        }
    )


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
    if row["import_unit_value_increase_risk"] >= watch:
        events.append("HS_IMPORT_UNIT_VALUE_SHOCK")
    if row["country_concentration_risk"] >= watch:
        events.append("HS_IMPORT_COUNTRY_CONCENTRATION")
    if row["net_import_exposure_risk"] >= watch:
        events.append("HS_NET_IMPORT_EXPOSURE")
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
    totals["import_unit_value_usd_per_kg"] = (
        totals["import_value_usd"]
        .div(totals["import_weight_kg"].replace(0, np.nan))
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
    features["import_unit_value_yoy_change"] = (
        features["import_unit_value_usd_per_kg"]
        .div(features["prior_import_unit_value_usd_per_kg"].replace(0, np.nan))
        .sub(1.0)
    )
    features["import_volume_decline_risk"] = (
        -features["import_volume_yoy_change"]
    ).clip(lower=0).div(float(policy["import_volume_decline_threshold"])).clip(0, 1)
    features["import_unit_value_increase_risk"] = features[
        "import_unit_value_yoy_change"
    ].clip(lower=0).div(
        float(policy["import_unit_value_increase_threshold"])
    ).clip(0, 1)
    features["net_import_exposure_risk"] = (
        (features["import_value_usd"] - features["export_value_usd"])
        .clip(lower=0)
        .div(features["import_value_usd"].replace(0, np.nan))
        .fillna(0.0)
        .clip(0, 1)
    )

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
    for column in [
        "country_import_coverage",
        "country_top1_share",
        "country_hhi",
    ]:
        features[column] = pd.to_numeric(features[column], errors="coerce").fillna(0.0)
    features["country_metric_available"] = (
        features["country_metric_available"].astype("boolean").fillna(False).astype(bool)
    )
    concentration_metric = features[
        ["country_top1_share", "country_hhi"]
    ].max(axis=1)
    threshold = float(policy["country_concentration_threshold"])
    features["country_concentration_risk"] = (
        (concentration_metric - threshold) / (1.0 - threshold)
    ).clip(0, 1).where(features["country_metric_available"], 0.0)

    component_weights = {
        "import_volume_decline_risk": float(policy["import_volume_decline"]),
        "import_unit_value_increase_risk": float(
            policy["import_unit_value_increase"]
        ),
        "country_concentration_risk": float(policy["country_concentration"]),
        "net_import_exposure_risk": float(policy["net_import_exposure"]),
    }
    features["hs_trade_risk"] = sum(
        weight * features[column].fillna(0.0)
        for column, weight in component_weights.items()
    ).clip(0, 1)
    available = {
        "import_volume_decline_risk": features["import_volume_yoy_change"].notna(),
        "import_unit_value_increase_risk": features[
            "import_unit_value_yoy_change"
        ].notna(),
        "country_concentration_risk": features["country_metric_available"],
        "net_import_exposure_risk": features["import_value_usd"].gt(0),
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


def build_trade_risk_outputs(
    total_flows: pd.DataFrame,
    country_flows: pd.DataFrame | None = None,
    stock_mapping: pd.DataFrame | None = None,
    material_hs_mapping: pd.DataFrame | None = None,
    hsk_reference: pd.DataFrame | None = None,
    config: dict | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    config = config or load_module_c_config()
    factors = build_hs_trade_features(total_flows, country_flows, config)
    if factors.empty:
        return _empty_scores(), _empty_audit()

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
            STOCK_MATERIAL_MAPPING_PATH
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
    merged = paths.merge(
        factors,
        on="hs_code",
        how="inner",
        validate="many_to_many",
    )
    if merged.empty:
        return _empty_scores(), _empty_audit()
    month = pd.to_datetime(merged["STD_YYYYMM"], errors="coerce")
    merged = merged[
        month.ge(merged["valid_from"]) & month.le(merged["valid_to"])
    ].copy()
    if merged.empty:
        return _empty_scores(), _empty_audit()

    merged["path_weight"] = (
        merged["mapping_weight"]
        * merged["exposure_score"]
        * merged["mapping_confidence_score"]
        * pd.to_numeric(merged["hs_mapping_weight"], errors="coerce").fillna(0.0)
        * pd.to_numeric(merged["hs_proxy_quality"], errors="coerce").fillna(0.0)
    ).clip(0, 1)
    merged["risk_contribution"] = (
        merged["hs_trade_risk"] * merged["path_weight"]
    ).clip(0, 1)
    for source, target in [
        ("import_volume_decline_risk", "weighted_volume_risk"),
        ("import_unit_value_increase_risk", "weighted_unit_value_risk"),
        ("country_concentration_risk", "weighted_concentration_risk"),
        ("net_import_exposure_risk", "weighted_net_import_risk"),
        ("trade_signal_confidence", "weighted_trade_confidence"),
    ]:
        merged[target] = merged[source] * merged["path_weight"]

    keys = ["STD_YYYYMM", "stock_item_key"]
    denominator = (
        merged.groupby(keys, observed=True)["path_weight"]
        .transform("sum")
        .replace(0, np.nan)
    )
    for column in [
        "weighted_volume_risk",
        "weighted_unit_value_risk",
        "weighted_concentration_risk",
        "weighted_net_import_risk",
        "weighted_trade_confidence",
    ]:
        merged[f"normalized_{column}"] = (merged[column] / denominator).fillna(0.0)

    scores = (
        merged.groupby(keys, as_index=False, observed=True)
        .agg(
            trade_risk=("risk_contribution", _compound_risk),
            trade_import_volume_risk=("normalized_weighted_volume_risk", "sum"),
            trade_import_unit_value_risk=(
                "normalized_weighted_unit_value_risk",
                "sum",
            ),
            trade_country_concentration_risk=(
                "normalized_weighted_concentration_risk",
                "sum",
            ),
            trade_net_import_exposure_risk=(
                "normalized_weighted_net_import_risk",
                "sum",
            ),
            trade_signal_confidence=(
                "normalized_weighted_trade_confidence",
                "sum",
            ),
            trade_factor_count=("hs_code", "nunique"),
            trade_hs_codes=("hs_code", _join_unique),
            trade_event_codes=("trade_event_codes", _join_unique),
        )
    )
    for column in [
        "trade_risk",
        "trade_import_volume_risk",
        "trade_import_unit_value_risk",
        "trade_country_concentration_risk",
        "trade_net_import_exposure_risk",
        "trade_signal_confidence",
    ]:
        scores[column] = scores[column].clip(0, 1)

    audit = merged.rename(
        columns={
            "hs_mapping_version": "mapping_version",
            "hs_evidence_reference": "evidence_reference",
        }
    )
    for column in AUDIT_COLUMNS:
        if column not in audit.columns:
            audit[column] = ""
    return scores[SCORE_COLUMNS], audit[AUDIT_COLUMNS]


def run_trade_risk_scoring(
    provider: str | None = None,
) -> dict[str, object]:
    setup_logging()
    ensure_dirs(OUTPUT_DIR, HSK_REFERENCE_NORMALIZED_PATH.parent)
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
    scores, audit = build_trade_risk_outputs(
        totals,
        countries,
        material_hs_mapping=material_hs,
        hsk_reference=hsk_reference,
    )
    scores.to_csv(TRADE_RISK_SCORE_PATH, index=False)
    audit.to_csv(TRADE_RISK_AUDIT_PATH, index=False)
    monthly_audit = (
        audit.drop_duplicates(["STD_YYYYMM", "hs_code"])
        if not audit.empty
        else audit
    )
    coverage_min = float(load_module_c_config()["trade_signal"]["country_coverage_min"])
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
        "country_scope_codes": load_trade_country_scope(),
        "country_coverage_threshold": coverage_min,
        "country_coverage_months_passing": int(
            monthly_audit.get(
                "country_import_coverage",
                pd.Series(dtype="float64"),
            ).ge(coverage_min).sum()
        ),
        "trade_feature_months": int(
            monthly_audit.get(
                "STD_YYYYMM",
                pd.Series(dtype="string"),
            ).nunique()
        ),
        "trade_score_rows": int(len(scores)),
        "trade_scored_stock_items": int(scores["stock_item_key"].nunique()),
        "max_hs_trade_risk": (
            float(monthly_audit["hs_trade_risk"].max())
            if not monthly_audit.empty
            else 0.0
        ),
        "max_item_trade_risk": (
            float(scores["trade_risk"].max()) if not scores.empty else 0.0
        ),
        "operational_status": (
            "ready" if not scores.empty else "blocked_no_trade_observations"
        ),
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
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score KCS import/export risk")
    parser.add_argument("--provider", choices=["disabled", "csv", "kcs"], default=None)
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    print(run_trade_risk_scoring(args.provider))
