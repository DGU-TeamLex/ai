import logging

import pandas as pd

from ..config import COMMODITY_RISK_SCORE_PATH, OUTPUT_DIR, STOCK_MATERIAL_MAPPING_PATH
from ..material_mapping import load_approved_stock_material_mapping
from ..utils import ensure_dirs, setup_logging
from .commodity_collector import collect_commodity_prices
from .commodity_features import add_commodity_features


LOGGER = logging.getLogger(__name__)


def _normalize(series: pd.Series) -> pd.Series:
    low = series.min()
    high = series.max()
    if pd.isna(low) or pd.isna(high) or high == low:
        return pd.Series(0.0, index=series.index)
    return ((series - low) / (high - low)).clip(0, 1)


def _ensure_mapping() -> pd.DataFrame:
    if not STOCK_MATERIAL_MAPPING_PATH.exists():
        LOGGER.warning("Stock item material mapping not found: %s", STOCK_MATERIAL_MAPPING_PATH)
    mapping = load_approved_stock_material_mapping()
    if mapping.empty:
        LOGGER.warning("No approved stock item material mappings are available")
    return mapping


def score_commodity_risk() -> pd.DataFrame:
    features = add_commodity_features(collect_commodity_prices())
    features["return_risk"] = _normalize(features["return_30d"].clip(lower=0))
    features["volatility_risk"] = _normalize(features["volatility_30d"].clip(lower=0))
    features["price_level_risk"] = _normalize(features["price_vs_90d_mean"].clip(lower=0))
    features["commodity_risk"] = (
        0.5 * features["return_risk"] + 0.3 * features["volatility_risk"] + 0.2 * features["price_level_risk"]
    ).clip(0, 1)
    features["STD_YYYYMM"] = features["date"].dt.strftime("%Y-%m")

    mapping = _ensure_mapping()
    if mapping.empty:
        return pd.DataFrame(
            columns=[
                "STD_YYYYMM",
                "stock_item_key",
                "commodity_risk",
                "material_return_30d",
                "material_volatility_30d",
            ]
        )
    merged = mapping.merge(features, left_on="related_material", right_on="material", how="left")
    merged["mapping_weight"] = pd.to_numeric(merged["mapping_weight"], errors="coerce").fillna(1.0)
    for col in ["commodity_risk", "return_30d", "volatility_30d"]:
        merged[col] = merged[col].fillna(0.0)
        merged[f"weighted_{col}"] = merged[col] * merged["mapping_weight"]

    denominator = merged.groupby(["STD_YYYYMM", "stock_item_key"])["mapping_weight"].transform("sum").replace(0, 1)
    merged["commodity_risk"] = merged["weighted_commodity_risk"] / denominator
    merged["material_return_30d"] = merged["weighted_return_30d"] / denominator
    merged["material_volatility_30d"] = merged["weighted_volatility_30d"] / denominator

    return (
        merged.groupby(["STD_YYYYMM", "stock_item_key"], as_index=False)
        .agg(
            commodity_risk=("commodity_risk", "sum"),
            material_return_30d=("material_return_30d", "sum"),
            material_volatility_30d=("material_volatility_30d", "sum"),
        )
        .assign(commodity_risk=lambda df: df["commodity_risk"].clip(0, 1))
    )


def run_commodity_risk_scoring() -> None:
    setup_logging()
    ensure_dirs(OUTPUT_DIR)
    scores = score_commodity_risk()
    scores.to_csv(COMMODITY_RISK_SCORE_PATH, index=False)
    LOGGER.info("Saved commodity risk scores: %s (%s rows)", COMMODITY_RISK_SCORE_PATH, len(scores))


if __name__ == "__main__":
    run_commodity_risk_scoring()
