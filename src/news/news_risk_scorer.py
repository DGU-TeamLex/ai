import logging
import math
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import (
    COUNTRY_WEIGHT_PATH,
    NEWS_ARTICLE_SCORE_PATH,
    NEWS_RISK_SCORE_PATH,
    NEWS_RISK_WEIGHT_PATH,
    OUTPUT_DIR,
    STOCK_MATERIAL_MAPPING_PATH,
)
from ..material_mapping import load_approved_stock_material_mapping
from ..utils import ensure_dirs, setup_logging
from .news_collector import collect_news
from .news_filter import filter_relevant_news
from .news_llm_analyzer import analyze_news_row


LOGGER = logging.getLogger(__name__)

NEWS_RISK_COLUMNS = ["disease_news_risk", "supply_news_risk", "material_news_risk", "total_news_risk"]
NEWS_RISK_METADATA_COLUMNS = [
    "news_signal_confidence",
    "has_approved_material_mapping",
    "has_approved_demand_mapping",
    "news_event_codes",
]
ARTICLE_SCORE_COLUMNS = [
    "article_id",
    "date",
    "STD_YYYYMM",
    "title",
    "source",
    "event_subject_codes",
    "demand_trigger_codes",
    "external_event_codes",
    "approved_mapping_path",
    "stock_item_key",
    "event_type",
    "risk_bucket",
    "event_cluster_id",
    "duplicate_count",
    "source_type",
    "severity",
    "severity_bucket",
    "event_type_weight",
    "severity_weight",
    "classifier_confidence",
    "extraction_completeness",
    "confidence",
    "source_weight",
    "item_relevance_type",
    "item_relevance_weight",
    "mapping_weight",
    "exposure_weight",
    "country_weight",
    "recency_weight",
    "novelty_weight",
    "article_score",
]

LEGACY_EVENT_TYPE_ALIASES = {
    "infectious_disease": "infectious_disease_outbreak",
    "supply_risk": "factory_shutdown",
    "material_price": "raw_material_shortage_or_price_spike",
}

DEFAULT_WEIGHT_CONFIG = {
    "event_type_weight": {
        "infectious_disease_outbreak": 1.00,
        "war_or_armed_conflict": 1.00,
        "export_restriction_or_sanction": 1.00,
        "port_or_logistics_disruption": 0.80,
        "factory_shutdown": 0.85,
        "raw_material_shortage_or_price_spike": 0.70,
        "policy_regulation_uncertainty": 0.55,
        "general_economic_uncertainty": 0.30,
    },
    "source_weight": {
        "official_government_or_international_org": 1.00,
        "expert_monitoring_network": 0.90,
        "major_news_agency": 0.75,
        "industry_media": 0.65,
        "local_media": 0.50,
        "social_media_or_blog": 0.25,
        "unknown": 0.50,
    },
    "severity_weight": {
        "critical": 1.00,
        "high": 0.80,
        "medium": 0.50,
        "low": 0.20,
    },
    "item_relevance_weight": {
        "direct_item_match": 1.00,
        "item_group_match": 0.80,
        "material_match": 0.60,
        "healthcare_supply_generic": 0.35,
        "general_macro_risk": 0.15,
    },
    "recency_half_life_days": {
        "war_or_armed_conflict": 60,
        "export_restriction_or_sanction": 60,
        "infectious_disease_outbreak": 30,
        "factory_shutdown": 30,
        "port_or_logistics_disruption": 21,
        "raw_material_shortage_or_price_spike": 21,
        "policy_regulation_uncertainty": 45,
        "general_economic_uncertainty": 30,
    },
    "defaults": {
        "source_type": "unknown",
        "severity": "medium",
        "item_relevance": "general_macro_risk",
        "exposure_weight": 0.60,
        "recency_half_life_days": 30,
        "novelty_duplicate_count": 0,
    },
    "risk_bucket": {
        "infectious_disease_outbreak": "disease_news_risk",
        "war_or_armed_conflict": "supply_news_risk",
        "export_restriction_or_sanction": "supply_news_risk",
        "port_or_logistics_disruption": "supply_news_risk",
        "factory_shutdown": "supply_news_risk",
        "policy_regulation_uncertainty": "supply_news_risk",
        "general_economic_uncertainty": "supply_news_risk",
        "raw_material_shortage_or_price_spike": "material_news_risk",
    },
}


def _ensure_country_weight() -> pd.DataFrame:
    if COUNTRY_WEIGHT_PATH.exists():
        return pd.read_csv(COUNTRY_WEIGHT_PATH)
    weights = pd.DataFrame(
        [
            {"country": "Korea", "region_weight": 1.0},
            {"country": "Malaysia", "region_weight": 0.7},
            {"country": "Global", "region_weight": 0.6},
            {"country": "Unknown", "region_weight": 0.5},
        ]
    )
    ensure_dirs(COUNTRY_WEIGHT_PATH.parent)
    weights.to_csv(COUNTRY_WEIGHT_PATH, index=False)
    return weights


def _load_stock_mapping() -> pd.DataFrame:
    if not STOCK_MATERIAL_MAPPING_PATH.exists():
        LOGGER.warning("Stock item material mapping not found: %s", STOCK_MATERIAL_MAPPING_PATH)
    mapping = load_approved_stock_material_mapping()
    if mapping.empty:
        LOGGER.warning("No approved stock item material mappings are available")
    return mapping


def _approved_input_mapping(mapping: pd.DataFrame) -> pd.DataFrame:
    result = mapping.copy()
    if "review_status" in result.columns:
        result = result[
            result["review_status"]
            .astype(str)
            .str.strip()
            .str.lower()
            .eq("approved")
        ].copy()
    return result


def _parse_scalar(value: str) -> Any:
    value = value.strip().strip('"').strip("'")
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _read_simple_yaml(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    current_section: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line:
            continue
        if not raw_line.startswith(" ") and line.endswith(":"):
            current_section = line[:-1].strip()
            result[current_section] = {}
            continue
        if current_section and ":" in line:
            key, value = line.strip().split(":", 1)
            result[current_section][key.strip()] = _parse_scalar(value)
    return result


def _deep_merge(base: dict[str, dict[str, Any]], override: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    merged = {section: values.copy() for section, values in base.items()}
    for section, values in override.items():
        merged.setdefault(section, {})
        merged[section].update(values)
    return merged


def _load_weight_config(path: Path = NEWS_RISK_WEIGHT_PATH) -> dict[str, dict[str, Any]]:
    if not path.exists():
        LOGGER.warning("News risk weight config not found: %s. Falling back to defaults.", path)
        return DEFAULT_WEIGHT_CONFIG
    return _deep_merge(DEFAULT_WEIGHT_CONFIG, _read_simple_yaml(path))


def _normalize_event_type(event_type: str | None) -> str:
    value = event_type or "none"
    return LEGACY_EVENT_TYPE_ALIASES.get(value, value)


def _infer_source_type(source: str | None) -> str:
    source_value = (source or "").lower()
    if any(token in source_value for token in ["who", "cdc", "kdca", "mfds", "government", "ministry", "gov"]):
        return "official_government_or_international_org"
    if any(token in source_value for token in ["promed", "association", "institute", "research", "monitor"]):
        return "expert_monitoring_network"
    if any(token in source_value for token in ["reuters", "ap", "bbc", "nyt", "yonhap", "연합뉴스", "sample"]):
        return "major_news_agency"
    if any(token in source_value for token in ["medical", "device", "logistics", "commodity", "industry"]):
        return "industry_media"
    if any(token in source_value for token in ["blog", "x.com", "twitter", "community"]):
        return "social_media_or_blog"
    return "unknown"


def _severity_bucket(value: Any) -> str:
    if isinstance(value, str):
        normalized = value.lower()
        if normalized in {"critical", "high", "medium", "low"}:
            return normalized
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return "medium"
    if numeric >= 0.90:
        return "critical"
    if numeric >= 0.70:
        return "high"
    if numeric >= 0.40:
        return "medium"
    return "low"


def _extraction_completeness(analysis: dict) -> float:
    checks = [
        bool(analysis.get("event_type")),
        bool(analysis.get("country")),
        bool(analysis.get("disease_or_material")),
        bool(analysis.get("related_medical_items")),
        bool(analysis.get("risk_direction")),
    ]
    return sum(checks) / len(checks)


def _classifier_probability(value: Any) -> float:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return 0.60
    return float(max(min(numeric, 1.0), 0.0))


def _combined_confidence(analysis: dict) -> float:
    classifier_probability = _classifier_probability(analysis.get("confidence"))
    extraction_completeness = _extraction_completeness(analysis)
    return float(0.7 * classifier_probability + 0.3 * extraction_completeness)


def _item_relevance_key(row: pd.Series, analysis: dict, item: pd.Series) -> str:
    text = f"{row.get('title', '')} {row.get('summary', '')}".lower()
    code = str(item.get("item_code", "")).lower()
    item_name = str(item.get("item_name", "")).lower()
    material = analysis.get("disease_or_material")
    material_codes = {
        str(value).strip() for value in analysis.get("material_meta_codes", [])
    }
    demand_codes = {
        str(value).strip()
        for value in analysis.get("demand_risk_meta_codes", [])
    }
    related_items = [str(value).lower() for value in analysis.get("related_medical_items", [])]

    if code and code in text:
        return "direct_item_match"
    if item_name and item_name in text:
        return "direct_item_match"
    if related_items and any(value in text for value in related_items):
        return "item_group_match"
    if material and str(item.get("related_material")) == str(material):
        return "material_match"
    item_material_codes = {
        value.strip()
        for value in str(item.get("raw_material_meta_code", "")).split(";")
        if value.strip()
    }
    if material_codes & item_material_codes:
        return "material_match"
    item_demand_codes = {
        value.strip()
        for value in str(item.get("demand_risk_meta_code", "")).split(";")
        if value.strip()
    }
    if demand_codes & item_demand_codes:
        return "item_group_match"
    if any(token in text for token in ["medical supplies", "healthcare products", "의료물품", "의료기기", "보건소"]):
        return "healthcare_supply_generic"
    return "general_macro_risk"


def _exposure_weight(item: pd.Series, config: dict[str, dict[str, Any]]) -> float:
    if "exposure_score" in item:
        exposure_score = pd.to_numeric(pd.Series([item.get("exposure_score")]), errors="coerce").iloc[0]
        if not pd.isna(exposure_score):
            return float(0.3 + 0.7 * max(min(exposure_score, 1.0), 0.0))
    return float(config["defaults"].get("exposure_weight", 0.60))


def _recency_weight(
    news_date: pd.Timestamp,
    reference_date: pd.Timestamp,
    event_type: str,
    config: dict[str, dict[str, Any]],
) -> float:
    age_days = max((reference_date - news_date).days, 0)
    half_life_days = float(
        config["recency_half_life_days"].get(
            event_type,
            config["defaults"].get("recency_half_life_days", 30),
        )
    )
    if half_life_days <= 0:
        return 1.0
    return float(math.exp(-math.log(2) * age_days / half_life_days))


def _event_cluster_id(row: pd.Series, analysis: dict, event_type: str, news_date: pd.Timestamp) -> str:
    explicit = row.get("event_cluster_id") or analysis.get("event_cluster_id")
    if explicit:
        return str(explicit)
    country = analysis.get("country") or row.get("country") or "Unknown"
    material = analysis.get("disease_or_material") or "generic"
    return f"{event_type}|{country}|{material}|{news_date.strftime('%Y-%m-%d')}"


def _empty_score_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "STD_YYYYMM",
            "stock_item_key",
            *NEWS_RISK_COLUMNS,
            *NEWS_RISK_METADATA_COLUMNS,
        ]
    )


def _empty_article_score_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=ARTICLE_SCORE_COLUMNS)


def _article_id(row: pd.Series, news_date: pd.Timestamp) -> str:
    explicit = row.get("article_id") or row.get("url")
    if explicit:
        return str(explicit)
    return f"{news_date.strftime('%Y-%m-%d')}|{row.get('source', '')}|{row.get('title', '')}"


def _aggregate_article_scores(scored: pd.DataFrame) -> pd.DataFrame:
    if scored.empty:
        return _empty_score_frame()

    grouped = (
        scored.groupby(["STD_YYYYMM", "stock_item_key", "risk_bucket"], as_index=False)["article_score"]
        .sum()
        .assign(risk=lambda df: 1 - (-df["article_score"]).apply(math.exp))
    )
    grouped["risk"] = grouped["risk"].clip(0, 1)
    pivot = (
        grouped.pivot_table(
            index=["STD_YYYYMM", "stock_item_key"],
            columns="risk_bucket",
            values="risk",
            aggfunc="max",
            fill_value=0,
        )
        .reset_index()
    )

    for col in NEWS_RISK_COLUMNS[:-1]:
        if col not in pivot.columns:
            pivot[col] = 0.0
    pivot["total_news_risk"] = pivot[NEWS_RISK_COLUMNS[:-1]].sum(axis=1).clip(0, 1)
    confidence = (
        scored.groupby(["STD_YYYYMM", "stock_item_key"], as_index=False, observed=True)
        .agg(news_signal_confidence=("confidence", "mean"))
    )
    pivot = pivot.merge(
        confidence,
        on=["STD_YYYYMM", "stock_item_key"],
        how="left",
        validate="one_to_one",
    )
    event_codes = (
        scored.groupby(["STD_YYYYMM", "stock_item_key"], as_index=False, observed=True)
        .agg(
            news_event_codes=(
                "external_event_codes",
                lambda values: ";".join(
                    sorted(
                        {
                            code.strip()
                            for value in values
                            for code in str(value).split(";")
                            if code.strip()
                        }
                    )
                ),
            )
        )
    )
    pivot = pivot.merge(
        event_codes,
        on=["STD_YYYYMM", "stock_item_key"],
        how="left",
        validate="one_to_one",
    )
    approvals = (
        scored.groupby(
            ["STD_YYYYMM", "stock_item_key"],
            as_index=False,
            observed=True,
        )
        .agg(
            has_approved_material_mapping=(
                "approved_mapping_path",
                lambda values: any(str(value) == "material" for value in values),
            ),
            has_approved_demand_mapping=(
                "approved_mapping_path",
                lambda values: any(str(value) == "demand" for value in values),
            ),
        )
    )
    pivot = pivot.merge(
        approvals,
        on=["STD_YYYYMM", "stock_item_key"],
        how="left",
        validate="one_to_one",
    )
    return pivot[
        [
            "STD_YYYYMM",
            "stock_item_key",
            *NEWS_RISK_COLUMNS,
            *NEWS_RISK_METADATA_COLUMNS,
        ]
    ]


def _codes_in_mapping(mapping: pd.DataFrame, column: str, codes: set[str]) -> pd.Series:
    if column not in mapping.columns or not codes:
        return pd.Series(False, index=mapping.index, dtype=bool)
    return mapping[column].astype(str).map(
        lambda value: bool(
            codes & {code.strip() for code in value.split(";") if code.strip()}
        )
    )


def _match_stock_mapping(
    mapping: pd.DataFrame,
    analysis: dict,
    event_type: str,
) -> tuple[pd.DataFrame, str]:
    if event_type == "infectious_disease_outbreak":
        demand_codes = {
            str(value).strip()
            for value in analysis.get("demand_risk_meta_codes", [])
            if str(value).strip()
        }
        matched = mapping[_codes_in_mapping(mapping, "demand_risk_meta_code", demand_codes)]
        if not matched.empty:
            return matched, "demand"

    material_codes = {
        str(value).strip()
        for value in analysis.get("material_meta_codes", [])
        if str(value).strip()
    }
    if material_codes:
        matched = mapping[
            _codes_in_mapping(mapping, "raw_material_meta_code", material_codes)
        ]
        if not matched.empty:
            return matched, "material"
    material = analysis.get("disease_or_material")
    matched = mapping[mapping["related_material"].eq(material)]
    path = "demand" if event_type == "infectious_disease_outbreak" else "material"
    return matched, path


def build_news_risk_outputs(
    news: pd.DataFrame | None = None,
    mapping: pd.DataFrame | None = None,
    country_weights: pd.DataFrame | None = None,
    config: dict[str, dict[str, Any]] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    news = filter_relevant_news(collect_news() if news is None else news)
    if news.empty:
        return _empty_score_frame(), _empty_article_score_frame()

    config = config or _load_weight_config()
    country_weights = _ensure_country_weight() if country_weights is None else country_weights
    mapping = _load_stock_mapping() if mapping is None else _approved_input_mapping(mapping)
    if mapping.empty:
        return _empty_score_frame(), _empty_article_score_frame()
    country_weight_map = dict(zip(country_weights["country"], country_weights["region_weight"]))

    analyzed_rows = []
    cluster_ids = []
    for _, row in news.iterrows():
        analysis = analyze_news_row(row)
        event_type = _normalize_event_type(analysis.get("event_type"))
        if event_type == "none":
            continue
        news_date = pd.to_datetime(row["date"])
        cluster_id = _event_cluster_id(row, analysis, event_type, news_date)
        analyzed_rows.append((row, analysis, event_type, news_date, cluster_id))
        cluster_ids.append(cluster_id)

    if not analyzed_rows:
        return _empty_score_frame(), _empty_article_score_frame()

    cluster_sizes = pd.Series(cluster_ids).value_counts().to_dict()
    event_weights = config["event_type_weight"]
    source_weights = config["source_weight"]
    severity_weights = config["severity_weight"]
    item_relevance_weights = config["item_relevance_weight"]
    risk_bucket_map = config["risk_bucket"]

    rows = []
    for row, analysis, event_type, news_date, cluster_id in analyzed_rows:
        material = analysis.get("disease_or_material")
        matched, approved_mapping_path = _match_stock_mapping(
            mapping,
            analysis,
            event_type,
        )
        if matched.empty:
            LOGGER.warning("No stock item mapping found for material=%s; article=%s", material, row.get("title", ""))
            continue

        source_type = row.get("source_type") or analysis.get("source_type") or _infer_source_type(row.get("source"))
        source_weight = float(source_weights.get(source_type, source_weights.get("unknown", 0.50)))
        severity_key = _severity_bucket(analysis.get("severity", config["defaults"].get("severity", "medium")))
        severity_weight = float(severity_weights.get(severity_key, severity_weights["medium"]))
        classifier_confidence = _classifier_probability(analysis.get("confidence"))
        extraction_completeness = _extraction_completeness(analysis)
        confidence = _combined_confidence(analysis)
        event_type_weight = float(event_weights.get(event_type, 0.30))
        country_weight = float(country_weight_map.get(analysis.get("country"), country_weight_map.get("Unknown", 0.50)))
        month_end = news_date + pd.offsets.MonthEnd(0)
        recency_weight = _recency_weight(news_date, month_end, event_type, config)
        duplicate_count = max(int(cluster_sizes.get(cluster_id, 1)) - 1, 0)
        novelty_weight = float(1 / math.sqrt(1 + duplicate_count))
        risk_bucket = risk_bucket_map.get(event_type, "supply_news_risk")

        for _, item in matched.iterrows():
            item_relevance_key = _item_relevance_key(row, analysis, item)
            item_relevance = float(item_relevance_weights.get(item_relevance_key, item_relevance_weights["general_macro_risk"]))
            mapping_weight = float(pd.to_numeric(pd.Series([item.get("mapping_weight", 1.0)]), errors="coerce").fillna(1.0).iloc[0])
            exposure_weight = _exposure_weight(item, config)
            article_score = (
                event_type_weight
                * severity_weight
                * confidence
                * source_weight
                * item_relevance
                * mapping_weight
                * exposure_weight
                * country_weight
                * recency_weight
                * novelty_weight
            )
            rows.append(
                {
                    "STD_YYYYMM": news_date.strftime("%Y-%m"),
                    "article_id": _article_id(row, news_date),
                    "date": news_date.strftime("%Y-%m-%d"),
                    "title": row.get("title", ""),
                    "source": row.get("source", ""),
                    "event_subject_codes": ";".join(
                        sorted(
                            {
                                str(value).strip()
                                for value in analysis.get("material_meta_codes", [])
                                if str(value).strip()
                            }
                        )
                    ),
                    "demand_trigger_codes": ";".join(
                        sorted(
                            {
                                str(value).strip()
                                for value in analysis.get(
                                    "demand_risk_meta_codes", []
                                )
                                if str(value).strip()
                            }
                        )
                    ),
                    "external_event_codes": ";".join(
                        sorted(
                            {
                                str(value).strip()
                                for value in analysis.get("external_event_codes", [])
                                if str(value).strip()
                            }
                        )
                    ),
                    "approved_mapping_path": approved_mapping_path,
                    "stock_item_key": item["stock_item_key"],
                    "event_type": event_type,
                    "risk_bucket": risk_bucket,
                    "event_cluster_id": cluster_id,
                    "duplicate_count": duplicate_count,
                    "source_type": source_type,
                    "severity": analysis.get("severity"),
                    "severity_bucket": severity_key,
                    "event_type_weight": event_type_weight,
                    "severity_weight": severity_weight,
                    "classifier_confidence": classifier_confidence,
                    "extraction_completeness": extraction_completeness,
                    "confidence": confidence,
                    "source_weight": source_weight,
                    "item_relevance_type": item_relevance_key,
                    "item_relevance_weight": item_relevance,
                    "mapping_weight": mapping_weight,
                    "exposure_weight": exposure_weight,
                    "country_weight": country_weight,
                    "recency_weight": recency_weight,
                    "novelty_weight": novelty_weight,
                    "article_score": max(article_score, 0.0),
                }
            )

    scored = pd.DataFrame(rows, columns=ARTICLE_SCORE_COLUMNS)
    if scored.empty:
        return _empty_score_frame(), _empty_article_score_frame()
    return _aggregate_article_scores(scored), scored


def score_news_risk() -> pd.DataFrame:
    scores, _ = build_news_risk_outputs()
    return scores


def run_news_risk_scoring() -> None:
    setup_logging()
    ensure_dirs(OUTPUT_DIR)
    scores, article_scores = build_news_risk_outputs()
    scores.to_csv(NEWS_RISK_SCORE_PATH, index=False)
    article_scores.to_csv(NEWS_ARTICLE_SCORE_PATH, index=False)
    LOGGER.info("Saved news risk scores: %s (%s rows)", NEWS_RISK_SCORE_PATH, len(scores))
    LOGGER.info("Saved news article score audit: %s (%s rows)", NEWS_ARTICLE_SCORE_PATH, len(article_scores))


if __name__ == "__main__":
    run_news_risk_scoring()
