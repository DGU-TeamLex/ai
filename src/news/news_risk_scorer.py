import logging

import pandas as pd

from ..config import (
    COUNTRY_WEIGHT_PATH,
    DEVICE_MATERIAL_MAPPING_PATH,
    MONTHLY_USAGE_PATH,
    NEWS_RISK_SCORE_PATH,
    OUTPUT_DIR,
)
from ..utils import ensure_dirs, setup_logging
from .news_collector import collect_news
from .news_filter import filter_relevant_news
from .news_llm_analyzer import analyze_news_row


LOGGER = logging.getLogger(__name__)


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


def _ensure_device_mapping() -> pd.DataFrame:
    if DEVICE_MATERIAL_MAPPING_PATH.exists():
        return pd.read_csv(DEVICE_MATERIAL_MAPPING_PATH)
    if MONTHLY_USAGE_PATH.exists():
        codes = pd.read_csv(MONTHLY_USAGE_PATH, usecols=["MED_DEVICE_5"])["MED_DEVICE_5"].astype(str).unique()
    else:
        codes = ["A1002", "B0004", "K0001"]

    materials = ["oil_plastic", "latex", "general_material", "respiratory disease"]
    rows = [
        {
            "MED_DEVICE_5": code,
            "item_name": f"item_{code}",
            "related_material": materials[i % len(materials)],
            "mapping_weight": 1.0,
        }
        for i, code in enumerate(sorted(codes))
    ]
    mapping = pd.DataFrame(rows)
    ensure_dirs(DEVICE_MATERIAL_MAPPING_PATH.parent)
    mapping.to_csv(DEVICE_MATERIAL_MAPPING_PATH, index=False)
    return mapping


def _recency_weight(news_date: pd.Timestamp, max_date: pd.Timestamp) -> float:
    months = max((max_date.to_period("M") - news_date.to_period("M")).n, 0)
    return float(max(0.3, 1 - months * 0.08))


def score_news_risk() -> pd.DataFrame:
    news = filter_relevant_news(collect_news())
    if news.empty:
        return pd.DataFrame(
            columns=[
                "STD_YYYYMM",
                "MED_DEVICE_5",
                "disease_news_risk",
                "supply_news_risk",
                "material_news_risk",
                "total_news_risk",
            ]
        )

    country_weights = _ensure_country_weight()
    mapping = _ensure_device_mapping()
    weight_map = dict(zip(country_weights["country"], country_weights["region_weight"]))
    max_date = pd.to_datetime(news["date"]).max()

    rows = []
    for _, row in news.iterrows():
        analysis = analyze_news_row(row)
        event_type = analysis["event_type"]
        if event_type == "none":
            continue
        news_date = pd.to_datetime(row["date"])
        material = analysis["disease_or_material"]
        matched = mapping[mapping["related_material"].eq(material)]
        if matched.empty:
            matched = mapping
        base_score = (
            analysis["severity"]
            * analysis["confidence"]
            * weight_map.get(analysis["country"], 0.5)
            * _recency_weight(news_date, max_date)
        )
        for _, item in matched.iterrows():
            rows.append(
                {
                    "STD_YYYYMM": news_date.strftime("%Y-%m"),
                    "MED_DEVICE_5": item["MED_DEVICE_5"],
                    "event_type": event_type,
                    "risk": min(base_score * float(item["mapping_weight"]), 1.0),
                }
            )

    scored = pd.DataFrame(rows)
    pivot = (
        scored.pivot_table(
            index=["STD_YYYYMM", "MED_DEVICE_5"],
            columns="event_type",
            values="risk",
            aggfunc="mean",
            fill_value=0,
        )
        .reset_index()
        .rename(
            columns={
                "infectious_disease": "disease_news_risk",
                "supply_risk": "supply_news_risk",
                "material_price": "material_news_risk",
            }
        )
    )
    for col in ["disease_news_risk", "supply_news_risk", "material_news_risk"]:
        if col not in pivot.columns:
            pivot[col] = 0.0
    pivot["total_news_risk"] = pivot[["disease_news_risk", "supply_news_risk", "material_news_risk"]].sum(axis=1).clip(0, 1)
    return pivot[["STD_YYYYMM", "MED_DEVICE_5", "disease_news_risk", "supply_news_risk", "material_news_risk", "total_news_risk"]]


def run_news_risk_scoring() -> None:
    setup_logging()
    ensure_dirs(OUTPUT_DIR)
    scores = score_news_risk()
    scores.to_csv(NEWS_RISK_SCORE_PATH, index=False)
    LOGGER.info("Saved news risk scores: %s (%s rows)", NEWS_RISK_SCORE_PATH, len(scores))


if __name__ == "__main__":
    run_news_risk_scoring()

