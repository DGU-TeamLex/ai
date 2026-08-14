import logging
import math
from pathlib import Path
from typing import Any

import numpy as np
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
from ..utils import guard_not_empty, ensure_dirs, setup_logging
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
    mapping = load_approved_stock_material_mapping(
        eligibility_column="news_signal_eligible"
    )
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

    grouped = scored.groupby(
        ["STD_YYYYMM", "stock_item_key", "risk_bucket"], as_index=False
    )["article_score"].sum()

    # 포화 변환 risk = 1 − exp(−S/λ).
    #
    # λ 가 없던 종전 식(1 − exp(−S))은 기사 점수가 0.003 수준일 때 맞춰진 것이다.
    # 가중치 결합을 곱셈에서 기하평균으로 바꾸자 기사 점수가 0.6 대로 올라
    # 합 S 가 커졌고, **중앙값이 1.0 이 되어 전 품목이 최대 위험**이 됐다.
    # 순위상관이 nan(값이 상수) 이 나와 판정 기준 ②를 통과하지 못했다.
    #
    # λ 를 점수 분포에서 잡아 척도를 맞춘다. 비영 합의 p90 을 λ 로 두면
    # 그 지점이 1 − exp(−1) ≈ 0.63 이 되어 상위 10% 가 0.63 이상에 놓인다.
    # 상수를 손으로 고르지 않고 데이터에서 정하므로 원천이 바뀌어도 따라간다.
    # λ 는 **버킷별로** 잡는다. 전체를 하나로 묶으면 안 된다.
    #
    # 전 품목 공통 경로(__ALL_ITEMS__)는 그 달 공급 기사를 전부 하나로 합치므로
    # 합이 350 까지 간다. 일반 품목은 자기에게 매칭된 몇 건만 받아 합이 1~2 다.
    # 두 계열을 같은 λ 로 나누면 센티넬이 무조건 1.0 이 되어 전 품목이 최대
    # 위험을 받는다(실측: supply_news_risk 중앙값 1.0, 순위상관 nan).
    #
    # 버킷마다 자기 분포의 p90 을 쓰면 각 계열이 제 척도로 퍼진다.
    scales = {}
    risks = pd.Series(0.0, index=grouped.index)
    for bucket, block in grouped.groupby("risk_bucket", sort=False):
        positive = block.loc[block["article_score"] > 0, "article_score"]
        scale = float(positive.quantile(0.90)) if len(positive) else 1.0
        if scale <= 0:
            scale = 1.0
        scales[bucket] = scale
        risks.loc[block.index] = 1 - np.exp(-block["article_score"] / scale)
        LOGGER.info(
            "포화 변환 [%s] λ=%.4f | 합 중앙 %.4f 최대 %.4f (n=%s)",
            bucket, scale,
            float(positive.median()) if len(positive) else 0.0,
            float(positive.max()) if len(positive) else 0.0,
            f"{len(positive):,}",
        )
    grouped["risk"] = risks.clip(0, 1)
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

    pivot = _broadcast_supply_wide(pivot)

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


def _one_mapping_per_stock_item(mapping: pd.DataFrame) -> pd.DataFrame:
    if mapping.empty or "stock_item_key" not in mapping.columns:
        return mapping
    ranked = mapping.copy()
    ranked["_mapping_rank"] = pd.to_numeric(
        ranked.get("mapping_weight", 1.0),
        errors="coerce",
    ).fillna(0.0)
    return (
        ranked.sort_values("_mapping_rank", ascending=False, kind="stable")
        .drop_duplicates("stock_item_key", keep="first")
        .drop(columns="_mapping_rank")
    )


# 기사 점수를 만드는 가중치 이름. 순서는 의미 없다.
SCORE_WEIGHT_NAMES = (
    "event_type", "severity", "confidence", "source", "item_relevance",
    "mapping", "exposure", "country", "recency", "novelty",
)


def _combine_weights(weights: dict[str, float]) -> float:
    """가중치를 결합해 기사 점수를 만든다. **기하평균** 을 쓴다.

    종전에는 10개를 그냥 곱했다. 개별 값이 0.5~0.8 로 "보통" 이어도 열 번 곱하면
    0.5^10 ≈ 0.001 이 되어 점수가 0 에 수렴한다. 실측:

        article_score  중앙 0.0034  최대 0.0111
        → module_c_supply_risk 가 0~1 척도인데 최대 0.19 에서 막혔다
        → 리드타임 조정폭이 최대 6.5일에 그쳤다

    기하평균은 같은 순서를 주면서 값의 범위를 0~1 로 유지한다. 어떤 가중치가
    0 이면 결과도 0 이라는 곱셈의 성질(하나라도 무관하면 전체가 무관)도 보존된다.

        ∏wᵢ          → 0.5^10 = 0.00098
        (∏wᵢ)^(1/n)  → 0.5

    상수 가중치는 제외한다. 모든 기사가 같은 값을 받는 가중치는 **점수를 깎기만
    할 뿐 기사 간 구분에 기여하지 않는다.** 예: source 0.5, item_relevance 0.6 이
    전 기사 동일하면 이 둘은 0.3 배 상수일 뿐이다.
    """
    values = [
        float(value)
        for value in weights.values()
        if value is not None and float(value) > 0
    ]
    if not values:
        return 0.0
    if any(float(value) <= 0 for value in weights.values()):
        return 0.0
    return float(np.exp(np.mean(np.log(values))))


def _broadcast_supply_wide(pivot: pd.DataFrame) -> pd.DataFrame:
    """전 품목 공통 공급위험을 실제 품목 행으로 펼치고 임시 키를 제거한다.

    항만 파업·전쟁·수출통제는 원자재를 가리지 않으므로 그 달의 모든 품목에
    같은 값이 적용된다. 품목별로 감사행을 만들면 3.7억 행이 되므로 사건당
    1행(임시 키)만 만들어 두고 여기서 펼친다.

    기존 품목별 supply_news_risk 와는 **최댓값**으로 합친다. 합산하면 같은
    사건이 두 경로로 들어와 이중 계상된다.
    """
    if ALL_ITEMS_SENTINEL not in set(pivot["stock_item_key"]):
        return pivot

    wide = pivot[pivot["stock_item_key"].eq(ALL_ITEMS_SENTINEL)]
    monthly = wide.set_index("STD_YYYYMM")["supply_news_risk"]
    result = pivot[pivot["stock_item_key"].ne(ALL_ITEMS_SENTINEL)].copy()
    if result.empty:
        return result
    broadcast = result["STD_YYYYMM"].map(monthly).fillna(0.0)
    result["supply_news_risk"] = result["supply_news_risk"].combine(broadcast, max)
    LOGGER.info(
        "전 품목 공통 공급위험 적용: %s개월, 대상 %s행 (월 최대 %.4f)",
        f"{len(monthly):,}",
        f"{len(result):,}",
        float(monthly.max()),
    )
    return result


# 특정 원자재에 귀속되지 않고 조달 전반을 늦추는 사건. 전 품목에 적용한다.
# 분류기(news_llm_analyzer)가 이들에 material="general_material" 을 부여한다.
SUPPLY_WIDE_EVENT_TYPES = frozenset(
    {
        "port_or_logistics_disruption",
        "export_restriction_or_sanction",
        "war_or_armed_conflict",
        "factory_shutdown",
        "policy_regulation_uncertainty",
    }
)
# factory_shutdown 은 라텍스 등 원자재가 특정되면 그쪽으로 먼저 매칭되고,
# 특정되지 않은 경우에만 여기로 내려온다.
GENERIC_MATERIALS = frozenset({"general_material", "", "None", "nan"})
# 전 품목 공통 신호를 담는 임시 키. 집계 후 실제 품목으로 펼치고 제거한다.
ALL_ITEMS_SENTINEL = "__ALL_ITEMS__"


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
            return _one_mapping_per_stock_item(matched), "demand"

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
            return _one_mapping_per_stock_item(matched), "material"
    material = analysis.get("disease_or_material")
    matched = mapping[mapping["related_material"].eq(material)]
    if not matched.empty:
        path = "demand" if event_type == "infectious_disease_outbreak" else "material"
        return _one_mapping_per_stock_item(matched), path

    # 전 품목 공통 공급 사건 — 특정 원자재에 귀속되지 않는다.
    #
    # 항만 파업·전쟁·수출통제·물류 차질은 원자재를 가리지 않고 조달 전반을
    # 늦춘다. 분류기는 이런 사건에 material="general_material" 을 부여하는데,
    # 그 이름의 원자재 매핑이 없어 **전량 폐기**되고 있었다.
    #
    # 실측: 수집 기사 6,844건을 분류기에 통과시키면 공급 사건이 6,127건(89.5%)
    # 인데, 산출물에 남은 고유 기사는 36건뿐이고 그중 공급 사건은 0건이었다.
    # 그래서 supply_news_risk 가 비영 0% 였고, supply_signal 가중치의 45% 가
    # 죽은 채로 남아 리드타임 조정폭이 최대 1.2일에 그쳤다.
    #
    # 원자재별 매핑을 아무리 넓혀도 이 경로는 열리지 않는다. 특정 원자재에
    # 붙일 수 없는 사건이기 때문이다. 전 품목에 적용하는 것이 정의상 맞다.
    if event_type in SUPPLY_WIDE_EVENT_TYPES and str(material) in GENERIC_MATERIALS:
        # 전 품목에 같은 값이 붙으므로 품목별 감사행을 남길 이유가 없다.
        # 실제로 남기면 사건 1건이 6만 행이 되고, 공급사건 6,127건이면
        # 3.7억 행(약 190GB)이 되어 디스크가 먼저 터진다.
        # 대표 1행만 만들고 집계 단계에서 전 품목으로 펼친다.
        sentinel = pd.DataFrame(
            [{"stock_item_key": ALL_ITEMS_SENTINEL, "mapping_weight": 1.0}]
        )
        return sentinel, "supply_wide"

    return _one_mapping_per_stock_item(matched), (
        "demand" if event_type == "infectious_disease_outbreak" else "material"
    )


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
        if event_type == "none" or analysis.get("risk_direction") not in {
            "demand_increase",
            "supply_decrease",
        }:
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
            article_score = _combine_weights(
                {
                    "event_type": event_type_weight,
                    "severity": severity_weight,
                    "confidence": confidence,
                    "source": source_weight,
                    "item_relevance": item_relevance,
                    "mapping": mapping_weight,
                    "exposure": exposure_weight,
                    "country": country_weight,
                    "recency": recency_weight,
                    "novelty": novelty_weight,
                }
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


def _save_article_audit(article_scores: pd.DataFrame) -> Path:
    """기사별 감사 산출물 저장. 기본은 parquet+zstd.

    감사행은 기사 수에 정비례하고 컬럼이 30개다. CSV 로 두면 실측 8.7GB 까지
    커져 디스크·메모리를 압박한다(오늘 실행 중 중단시켜야 했다). PR #69 가
    측정한 압축비가 148배라 parquet 으로 두면 같은 내용이 수십 MB 다.

    이 파일은 **점수 계산에 쓰이지 않는다.** 근거 기록용이므로 형식만 바꾸면
    결과는 동일하다.

    NEWS_ARTICLE_SCORE_FORMAT=csv 로 종전 형식을 유지할 수 있다.
    """
    import os

    fmt = os.environ.get("NEWS_ARTICLE_SCORE_FORMAT", "parquet").strip().lower()
    if fmt == "csv":
        article_scores.to_csv(NEWS_ARTICLE_SCORE_PATH, index=False)
        return NEWS_ARTICLE_SCORE_PATH

    target = NEWS_ARTICLE_SCORE_PATH.with_suffix(".parquet")
    article_scores.to_parquet(target, index=False, compression="zstd")
    # 종전 CSV 가 남아 있으면 옛 결과를 최신으로 오해한다.
    if NEWS_ARTICLE_SCORE_PATH.exists():
        NEWS_ARTICLE_SCORE_PATH.unlink()
        LOGGER.info("이전 CSV 감사파일을 제거했다: %s", NEWS_ARTICLE_SCORE_PATH.name)
    return target


def run_news_risk_scoring() -> None:
    setup_logging()
    ensure_dirs(OUTPUT_DIR)
    scores, article_scores = build_news_risk_outputs()
    guard_not_empty(scores, NEWS_RISK_SCORE_PATH, "뉴스 위험 점수")
    scores.to_csv(NEWS_RISK_SCORE_PATH, index=False)
    audit_path = _save_article_audit(article_scores)
    LOGGER.info("Saved news risk scores: %s (%s rows)", NEWS_RISK_SCORE_PATH, len(scores))
    LOGGER.info(
        "Saved news article score audit: %s (%s rows, %.1f MB)",
        audit_path,
        f"{len(article_scores):,}",
        audit_path.stat().st_size / 1024 / 1024,
    )


if __name__ == "__main__":
    run_news_risk_scoring()
