import logging
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import (
    COMMODITY_RISK_AUDIT_PATH,
    COMMODITY_RISK_SCORE_PATH,
    MATERIAL_MARKET_FACTOR_MAPPING_PATH,
    OUTPUT_DIR,
    STOCK_MATERIAL_MAPPING_PATH,
)
from ..material_mapping import load_approved_stock_material_mapping
from ..module_c.config import load_module_c_config
from ..utils import ensure_dirs, setup_logging
from .commodity_collector import collect_commodity_prices
from .commodity_features import add_commodity_features


LOGGER = logging.getLogger(__name__)
MARKET_MAPPING_COLUMNS = [
    "raw_material_meta_code",
    "market_factor_id",
    "transmission_weight",
    "lag_days",
    "proxy_quality",
    "event_code",
    "review_status",
    "evidence_reference",
    "mapping_version",
]
SCORE_COLUMNS = [
    "STD_YYYYMM",
    "stock_item_key",
    "commodity_risk",
    "material_return_30d",
    "material_volatility_30d",
    "material_price_vs_90d_mean",
    "market_signal_confidence",
    "market_factor_count",
    "market_factor_ids",
    "market_event_codes",
]


def _empty_scores() -> pd.DataFrame:
    return pd.DataFrame(columns=SCORE_COLUMNS)


def _empty_audit() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "STD_YYYYMM",
            "stock_item_key",
            "raw_material_meta_code",
            "market_factor_id",
            "event_code",
            "market_factor_risk",
            "path_weight",
            "risk_contribution",
            "return_30d",
            "volatility_30d",
            "price_vs_90d_mean",
            "mapping_weight",
            "exposure_score",
            "transmission_weight",
            "proxy_quality",
            "mapping_confidence_score",
            "lag_days",
            "mapping_version",
            "evidence_reference",
        ]
    )


def load_material_market_factor_mapping(
    path: Path = MATERIAL_MARKET_FACTOR_MAPPING_PATH,
) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=MARKET_MAPPING_COLUMNS)
    mapping = pd.read_csv(path, keep_default_na=False)
    missing = [column for column in MARKET_MAPPING_COLUMNS if column not in mapping.columns]
    if missing:
        raise ValueError(f"Material market factor mapping is missing columns: {missing}")
    mapping = mapping[
        mapping["review_status"].astype(str).str.strip().str.lower().eq("approved")
    ].copy()
    for column in ["transmission_weight", "proxy_quality"]:
        mapping[column] = pd.to_numeric(mapping[column], errors="coerce")
        if mapping[column].isna().any() or ~mapping[column].between(0, 1).all():
            raise ValueError(f"{column} must be within 0..1")
    mapping["lag_days"] = pd.to_numeric(mapping["lag_days"], errors="coerce")
    if mapping["lag_days"].isna().any() or mapping["lag_days"].lt(0).any():
        raise ValueError("lag_days must be non-negative")
    duplicate = mapping.duplicated(
        ["raw_material_meta_code", "market_factor_id"], keep=False
    )
    if duplicate.any():
        raise ValueError("Material market factor mapping contains duplicate paths")
    return mapping.reset_index(drop=True)


def score_market_factor_risk(
    prices: pd.DataFrame,
    config: dict | None = None,
) -> pd.DataFrame:
    config = config or load_module_c_config()
    features = add_commodity_features(prices)
    if features.empty:
        return features.assign(
            return_risk=pd.Series(dtype="float64"),
            volatility_risk=pd.Series(dtype="float64"),
            price_level_risk=pd.Series(dtype="float64"),
            market_factor_risk=pd.Series(dtype="float64"),
            STD_YYYYMM=pd.Series(dtype="string"),
        )

    weights = config["market_signal"]
    features["return_risk"] = (
        features["return_30d"].clip(lower=0)
        / float(weights["return_risk_threshold"])
    ).clip(0, 1)
    features["volatility_risk"] = (
        features["volatility_30d"].clip(lower=0)
        / float(weights["volatility_risk_threshold"])
    ).clip(0, 1)
    features["price_level_risk"] = (
        features["price_vs_90d_mean"].clip(lower=0)
        / float(weights["price_level_risk_threshold"])
    ).clip(0, 1)
    features["market_factor_risk"] = (
        float(weights["return_30d"]) * features["return_risk"]
        + float(weights["volatility_30d"]) * features["volatility_risk"]
        + float(weights["price_vs_90d_mean"]) * features["price_level_risk"]
    ).clip(0, 1)
    features["STD_YYYYMM"] = features["date"].dt.strftime("%Y-%m")
    return (
        features.sort_values(["market_factor_id", "date"])
        .groupby(["STD_YYYYMM", "market_factor_id"], as_index=False, observed=True)
        .tail(1)
        .reset_index(drop=True)
    )


def _ensure_mapping() -> pd.DataFrame:
    if not STOCK_MATERIAL_MAPPING_PATH.exists():
        LOGGER.warning("Stock item material mapping not found: %s", STOCK_MATERIAL_MAPPING_PATH)
    mapping = load_approved_stock_material_mapping()
    if mapping.empty:
        LOGGER.warning("No approved stock item material mappings are available")
    return mapping


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


def _approved_input_mapping(mapping: pd.DataFrame) -> pd.DataFrame:
    result = mapping.copy()
    if "review_status" in result.columns:
        result = result[
            result["review_status"].astype(str).str.strip().str.lower().eq("approved")
        ].copy()
    for column, default in {
        "mapping_weight": 1.0,
        "exposure_score": 1.0,
        "mapping_confidence": "medium",
        "raw_material_meta_code": "",
        "related_material": "",
    }.items():
        if column not in result.columns:
            result[column] = default
    result["mapping_weight"] = pd.to_numeric(
        result["mapping_weight"], errors="coerce"
    ).fillna(1.0).clip(0, 1)
    result["exposure_score"] = pd.to_numeric(
        result["exposure_score"], errors="coerce"
    ).fillna(1.0).clip(0, 1)
    result["mapping_confidence_score"] = result["mapping_confidence"].map(
        _mapping_confidence_score
    )
    return result


def _build_market_paths(
    stock_mapping: pd.DataFrame,
    material_market_mapping: pd.DataFrame,
) -> pd.DataFrame:
    mapping = _approved_input_mapping(stock_mapping)
    if mapping.empty:
        return mapping

    coded = mapping[mapping["raw_material_meta_code"].astype(str).str.strip().ne("")].copy()
    if not coded.empty:
        coded["raw_material_meta_code"] = coded["raw_material_meta_code"].str.split(";")
        coded = coded.explode("raw_material_meta_code")
        coded["raw_material_meta_code"] = coded["raw_material_meta_code"].str.strip()
        coded = coded.merge(
            material_market_mapping,
            on="raw_material_meta_code",
            how="inner",
            validate="many_to_many",
            suffixes=("", "_market"),
        )

    legacy = mapping[mapping["raw_material_meta_code"].astype(str).str.strip().eq("")].copy()
    if not legacy.empty:
        legacy["raw_material_meta_code"] = legacy["related_material"]
        legacy["market_factor_id"] = legacy["related_material"]
        legacy["transmission_weight"] = 1.0
        legacy["proxy_quality"] = 1.0
        legacy["lag_days"] = 0.0
        legacy["event_code"] = "LEGACY_MATERIAL_PRICE_SHOCK"
        legacy["mapping_version_market"] = "legacy-direct-v1"
        legacy["evidence_reference_market"] = legacy.get("evidence_reference", "")

    frames = [frame for frame in [coded, legacy] if not frame.empty]
    return pd.concat(frames, ignore_index=True) if frames else mapping.iloc[0:0]


def _compound_risk(values: pd.Series) -> float:
    clipped = pd.to_numeric(values, errors="coerce").fillna(0.0).clip(0, 1)
    return float(1.0 - np.prod(1.0 - clipped.to_numpy()))


def _join_unique(values: pd.Series) -> str:
    return ";".join(sorted({str(value).strip() for value in values if str(value).strip()}))


def build_commodity_risk_outputs(
    prices: pd.DataFrame | None = None,
    mapping: pd.DataFrame | None = None,
    material_market_mapping: pd.DataFrame | None = None,
    config: dict | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    config = config or load_module_c_config()
    factors = score_market_factor_risk(
        collect_commodity_prices() if prices is None else prices,
        config,
    )
    mapping = _ensure_mapping() if mapping is None else mapping
    material_market_mapping = (
        load_material_market_factor_mapping()
        if material_market_mapping is None
        else material_market_mapping
    )
    if factors.empty or mapping.empty:
        return _empty_scores(), _empty_audit()

    paths = _build_market_paths(mapping, material_market_mapping)
    if paths.empty:
        return _empty_scores(), _empty_audit()
    merged = paths.merge(
        factors,
        on="market_factor_id",
        how="inner",
        validate="many_to_many",
    )
    if merged.empty:
        return _empty_scores(), _empty_audit()

    for column in ["transmission_weight", "proxy_quality", "mapping_weight", "exposure_score"]:
        merged[column] = pd.to_numeric(merged[column], errors="coerce").fillna(0.0).clip(0, 1)
    merged["path_weight"] = merged[
        [
            "transmission_weight",
            "proxy_quality",
            "mapping_weight",
            "exposure_score",
            "mapping_confidence_score",
        ]
    ].prod(axis=1)
    merged["risk_contribution"] = (
        merged["market_factor_risk"] * merged["path_weight"]
    ).clip(0, 1)
    merged["weighted_return"] = merged["return_30d"] * merged["path_weight"]
    merged["weighted_volatility"] = merged["volatility_30d"] * merged["path_weight"]
    merged["weighted_price_level"] = merged["price_vs_90d_mean"] * merged["path_weight"]
    merged["weighted_confidence"] = (
        merged["mapping_confidence_score"] * merged["proxy_quality"] * merged["path_weight"]
    )

    key_columns = ["STD_YYYYMM", "stock_item_key"]
    denominator = merged.groupby(key_columns, observed=True)["path_weight"].transform("sum").replace(0, np.nan)
    merged["normalized_return"] = (merged["weighted_return"] / denominator).fillna(0.0)
    merged["normalized_volatility"] = (merged["weighted_volatility"] / denominator).fillna(0.0)
    merged["normalized_price_level"] = (merged["weighted_price_level"] / denominator).fillna(0.0)
    merged["normalized_confidence"] = (merged["weighted_confidence"] / denominator).fillna(0.0)

    alert_threshold = float(config["alert_thresholds"]["watch"])
    merged["active_event_code"] = merged["event_code"].where(
        merged["market_factor_risk"].ge(alert_threshold), ""
    )
    scores = (
        merged.groupby(key_columns, as_index=False, observed=True)
        .agg(
            commodity_risk=("risk_contribution", _compound_risk),
            material_return_30d=("normalized_return", "sum"),
            material_volatility_30d=("normalized_volatility", "sum"),
            material_price_vs_90d_mean=("normalized_price_level", "sum"),
            market_signal_confidence=("normalized_confidence", "sum"),
            market_factor_count=("market_factor_id", "nunique"),
            market_factor_ids=("market_factor_id", _join_unique),
            market_event_codes=("active_event_code", _join_unique),
        )
    )
    scores["commodity_risk"] = scores["commodity_risk"].clip(0, 1)
    scores["market_signal_confidence"] = scores["market_signal_confidence"].clip(0, 1)

    audit = merged.rename(
        columns={
            "mapping_version_market": "mapping_version",
            "evidence_reference_market": "evidence_reference",
        }
    )
    for column in _empty_audit().columns:
        if column not in audit.columns:
            audit[column] = ""
    return scores[SCORE_COLUMNS], audit[_empty_audit().columns.tolist()]


def score_commodity_risk() -> pd.DataFrame:
    scores, _ = build_commodity_risk_outputs()
    return scores


def run_commodity_risk_scoring() -> None:
    setup_logging()
    ensure_dirs(OUTPUT_DIR)
    scores, audit = build_commodity_risk_outputs()
    scores.to_csv(COMMODITY_RISK_SCORE_PATH, index=False)
    audit.to_csv(COMMODITY_RISK_AUDIT_PATH, index=False)
    LOGGER.info("Saved commodity risk scores: %s (%s rows)", COMMODITY_RISK_SCORE_PATH, len(scores))
    LOGGER.info("Saved commodity risk audit: %s (%s rows)", COMMODITY_RISK_AUDIT_PATH, len(audit))


if __name__ == "__main__":
    run_commodity_risk_scoring()
