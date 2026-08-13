from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .config import load_module_c_config


LOGGER = logging.getLogger(__name__)
KEY_COLUMNS = ["STD_YYYYMM", "stock_item_key"]
MODULE_C_SCORE_COLUMNS = [
    *KEY_COLUMNS,
    "module_c_demand_risk",
    "module_c_supply_news_risk",
    "module_c_material_news_risk",
    "module_c_market_price_risk",
    "module_c_trade_risk",
    "module_c_supply_news_contribution",
    "module_c_material_news_contribution",
    "module_c_market_price_contribution",
    "module_c_trade_contribution",
    "module_c_supply_risk",
    "module_c_total_risk",
    "module_c_signal_confidence",
    "module_c_has_approved_material_mapping",
    "module_c_has_approved_trade_mapping",
    "module_c_has_approved_demand_mapping",
    "module_c_adjustment_enabled",
    "module_c_risk_level",
    "module_c_event_supply_risk_level",
    "module_c_news_event_codes",
    "module_c_market_event_codes",
    "module_c_trade_event_codes",
    "module_c_event_codes",
    "module_c_config_version",
    "module_c_calibration_status",
]
AUDIT_COLUMNS = [
    *KEY_COLUMNS,
    "signal_type",
    "raw_score",
    "signal_weight",
    "weighted_contribution",
    "mapping_approved",
    "signal_confidence",
    "event_codes",
    "config_version",
    "calibration_status",
]
ALERT_COLUMNS = [
    *KEY_COLUMNS,
    "risk_level",
    "demand_risk",
    "supply_risk",
    "top_driver",
    "event_codes",
    "market_event_codes",
    "trade_event_codes",
    "recommended_action",
    "config_version",
    "calibration_status",
]


def _empty_scores() -> pd.DataFrame:
    return pd.DataFrame(columns=MODULE_C_SCORE_COLUMNS)


def _empty_audit() -> pd.DataFrame:
    return pd.DataFrame(columns=AUDIT_COLUMNS)


def _empty_alerts() -> pd.DataFrame:
    return pd.DataFrame(columns=ALERT_COLUMNS)


def _normalize_month(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    if "STD_YYYYMM" not in result.columns and "year_month" in result.columns:
        result["STD_YYYYMM"] = result["year_month"]
    if "STD_YYYYMM" not in result.columns:
        return result
    parsed = pd.to_datetime(result["STD_YYYYMM"].astype(str), errors="coerce")
    result["STD_YYYYMM"] = parsed.dt.strftime("%Y-%m")
    return result[result["STD_YYYYMM"].notna()].copy()


def _numeric(df: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    if column not in df.columns:
        return pd.Series(default, index=df.index, dtype="float64")
    return pd.to_numeric(df[column], errors="coerce").fillna(default).clip(0, 1)


def _boolean(df: pd.DataFrame, column: str, default: bool = False) -> pd.Series:
    if column not in df.columns:
        return pd.Series(default, index=df.index, dtype=bool)
    return (
        df[column]
        .astype("string")
        .fillna("")
        .str.strip()
        .str.lower()
        .isin({"true", "t", "1", "yes", "y", "approved"})
    )


def _risk_level(score: float, thresholds: dict) -> str:
    if score >= float(thresholds["critical"]):
        return "critical"
    if score >= float(thresholds["warning"]):
        return "warning"
    if score >= float(thresholds["watch"]):
        return "watch"
    return "normal"


def _prepare_news(news_scores: pd.DataFrame) -> pd.DataFrame:
    if news_scores.empty:
        return pd.DataFrame(columns=KEY_COLUMNS)
    news = _normalize_month(news_scores)
    if not set(KEY_COLUMNS).issubset(news.columns):
        raise ValueError("News risk scores require STD_YYYYMM and stock_item_key")
    keep = [
        *KEY_COLUMNS,
        "disease_news_risk",
        "supply_news_risk",
        "material_news_risk",
        "news_signal_confidence",
        "has_approved_demand_mapping",
        "has_approved_material_mapping",
        "news_event_codes",
    ]
    for column in keep[2:]:
        if column not in news.columns:
            if column.startswith("has_approved"):
                news[column] = False
            elif column == "news_event_codes":
                news[column] = ""
            elif column == "news_signal_confidence":
                news[column] = 0.60
            else:
                news[column] = 0.0
    return news[keep].drop_duplicates(KEY_COLUMNS, keep="last")


def _prepare_market(commodity_scores: pd.DataFrame) -> pd.DataFrame:
    if commodity_scores.empty:
        return pd.DataFrame(columns=KEY_COLUMNS)
    market = _normalize_month(commodity_scores)
    if not set(KEY_COLUMNS).issubset(market.columns):
        raise ValueError("Commodity risk scores require STD_YYYYMM and stock_item_key")
    keep = [
        *KEY_COLUMNS,
        "commodity_risk",
        "market_signal_confidence",
        "market_factor_count",
        "market_event_codes",
    ]
    for column in keep[2:]:
        if column not in market.columns:
            market[column] = "" if column == "market_event_codes" else 0.0
    return market[keep].drop_duplicates(KEY_COLUMNS, keep="last")


def _prepare_trade(trade_scores: pd.DataFrame) -> pd.DataFrame:
    if trade_scores.empty:
        return pd.DataFrame(columns=KEY_COLUMNS)
    trade = _normalize_month(trade_scores)
    if not set(KEY_COLUMNS).issubset(trade.columns):
        raise ValueError("Trade risk scores require STD_YYYYMM and stock_item_key")
    keep = [
        *KEY_COLUMNS,
        "trade_risk",
        "trade_signal_confidence",
        "trade_factor_count",
        "trade_event_codes",
    ]
    for column in keep[2:]:
        if column not in trade.columns:
            trade[column] = "" if column == "trade_event_codes" else 0.0
    return trade[keep].drop_duplicates(KEY_COLUMNS, keep="last")


def _build_audit(
    scores: pd.DataFrame,
    config: dict,
    scope: str = "all",
) -> pd.DataFrame:
    if scores.empty:
        return _empty_audit()
    if scope not in {"all", "latest"}:
        raise ValueError("Module C audit scope must be 'all' or 'latest'")
    source = scores
    if scope == "latest":
        source = scores[scores["STD_YYYYMM"].eq(scores["STD_YYYYMM"].max())]
    supply_weights = config["supply_signal"]
    trade_weight = float(config["trade_signal"]["module_c_overlay_weight"])
    signal_specs = [
        (
            "demand_news",
            "module_c_demand_risk",
            1.0,
            "module_c_has_approved_demand_mapping",
            "module_c_news_event_codes",
        ),
        (
            "supply_news",
            "module_c_supply_news_risk",
            float(supply_weights["supply_news"]),
            "module_c_has_approved_material_mapping",
            "module_c_news_event_codes",
        ),
        (
            "material_news",
            "module_c_material_news_risk",
            float(supply_weights["material_news"]),
            "module_c_has_approved_material_mapping",
            "module_c_news_event_codes",
        ),
        (
            "market_price",
            "module_c_market_price_risk",
            float(supply_weights["market_price"]),
            "module_c_has_approved_material_mapping",
            "module_c_market_event_codes",
        ),
        (
            "import_export",
            "module_c_trade_risk",
            trade_weight,
            "module_c_has_approved_trade_mapping",
            "module_c_trade_event_codes",
        ),
    ]
    frames = []
    for signal_type, score_column, weight, gate_column, event_column in signal_specs:
        frame = source[
            [
                *KEY_COLUMNS,
                score_column,
                gate_column,
                "module_c_signal_confidence",
                event_column,
            ]
        ].copy()
        frame["signal_type"] = signal_type
        frame["raw_score"] = pd.to_numeric(
            frame.pop(score_column),
            errors="coerce",
        ).fillna(0.0)
        frame["signal_weight"] = weight
        frame["weighted_contribution"] = (
            source.loc[frame.index, "module_c_trade_contribution"].to_numpy()
            if signal_type == "import_export"
            else frame["raw_score"] * weight
        )
        frame["mapping_approved"] = frame.pop(gate_column).astype(bool)
        frame["signal_confidence"] = pd.to_numeric(
            frame.pop("module_c_signal_confidence"),
            errors="coerce",
        ).fillna(0.0)
        frame["event_codes"] = frame.pop(event_column).fillna("")
        frame["config_version"] = config["version"]
        frame["calibration_status"] = config["calibration_status"]
        frames.append(frame[AUDIT_COLUMNS])
    return pd.concat(frames, ignore_index=True)


def _build_alerts(scores: pd.DataFrame, config: dict) -> pd.DataFrame:
    if scores.empty:
        return _empty_alerts()
    thresholds = config["alert_thresholds"]
    active = scores[scores["module_c_total_risk"].ge(float(thresholds["watch"]))]
    rows = []
    for _, row in active.iterrows():
        demand = float(row["module_c_demand_risk"])
        supply = float(row["module_c_supply_risk"])
        top_driver = "demand" if demand > supply else "supply"
        action = (
            "수요 급증 가능성을 반영해 예상 사용량과 재고 소진 속도를 재검토"
            if top_driver == "demand"
            else "조달 리드타임과 대체 공급처를 확인하고 안전재고 상향분을 검토"
        )
        rows.append(
            {
                "STD_YYYYMM": row["STD_YYYYMM"],
                "stock_item_key": row["stock_item_key"],
                "risk_level": row["module_c_risk_level"],
                "demand_risk": demand,
                "supply_risk": supply,
                "top_driver": top_driver,
                "event_codes": row["module_c_event_codes"],
                "market_event_codes": row["module_c_market_event_codes"],
                "trade_event_codes": row["module_c_trade_event_codes"],
                "recommended_action": action,
                "config_version": config["version"],
                "calibration_status": config["calibration_status"],
            }
        )
    return pd.DataFrame(rows, columns=ALERT_COLUMNS)


def build_module_c_risk_outputs(
    news_scores: pd.DataFrame,
    commodity_scores: pd.DataFrame,
    config: dict | None = None,
    trade_scores: pd.DataFrame | None = None,
    audit_scope: str = "all",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    config = config or load_module_c_config()
    news = _prepare_news(news_scores)
    market = _prepare_market(commodity_scores)
    trade = _prepare_trade(
        trade_scores if trade_scores is not None else pd.DataFrame()
    )
    if news.empty and market.empty and trade.empty:
        return _empty_scores(), _empty_audit(), _empty_alerts()

    merged = news.merge(market, on=KEY_COLUMNS, how="outer")
    merged = merged.merge(trade, on=KEY_COLUMNS, how="outer")
    demand_gate = _boolean(merged, "has_approved_demand_mapping")
    material_gate = _boolean(merged, "has_approved_material_mapping")
    material_gate |= _numeric(merged, "market_factor_count").gt(0)
    trade_gate = _numeric(merged, "trade_factor_count").gt(0)
    material_gate |= trade_gate

    demand_risk = _numeric(merged, "disease_news_risk") * demand_gate.astype(float)
    supply_news = _numeric(merged, "supply_news_risk") * material_gate.astype(float)
    material_news = _numeric(merged, "material_news_risk") * material_gate.astype(float)
    market_price = _numeric(merged, "commodity_risk") * material_gate.astype(float)
    trade_risk = _numeric(merged, "trade_risk") * trade_gate.astype(float)

    supply_weights = config["supply_signal"]
    supply_news_contribution = float(supply_weights["supply_news"]) * supply_news
    material_news_contribution = float(supply_weights["material_news"]) * material_news
    market_price_contribution = float(supply_weights["market_price"]) * market_price
    # 세 신호를 **가중최대** 로 결합한다. 가중합이 아니다.
    #
    # 가중합은 세 신호가 함께 움직일 때만 제 범위를 낸다. 실측은 반대다.
    #
    #   supply_news ↔ material_news    r = +0.460
    #   supply_news ↔ market_price     r = -0.046
    #   material_news ↔ market_price   r = -0.223
    #   세 신호가 같은 행에서 동시에 p90 이상인 비율:  0.0000%
    #
    # 상관이 낮거나 음수인 신호를 평균하면 범위가 구조적으로 눌린다. 이론 최대
    # 1.0 인데 실측 최대가 0.568 이었고, 하류에서 리드타임 분위수가 p66 을
    # 못 넘어 어떤 위험 변화도 리드타임을 못 움직였다(ai#20 결함 O).
    #
    # 공급위험은 평균이 아니라 **가장 강한 위험 하나** 로 결정되는 것이 맞다.
    # 항만이 막혔으면 원자재 가격이 안정이어도 공급은 막힌다. 가중치는 신호별
    # 중요도이므로 유지하되, 최대 가중치로 정규화해 단일 신호가 자기 값까지
    # 도달할 수 있게 한다.
    #
    #   base = max(wᵢ·sᵢ) / max(wᵢ)
    #
    # 후보 5종을 실측 비교한 결과다(표본 287,192행, 5%).
    #
    #   방식              최대     중앙    최대→리드타임   순서보존
    #   현행 가중합       0.568   0.137      30.1일          -
    #   noisy-OR         0.506   0.136      30.0일        0.9997
    #   가중최대(정규화)   0.954   0.242      79.9일        0.9713   ← 채택
    #   단순최대          0.954   0.327      79.9일        0.9512
    #   멱평균 p=3        0.737   0.231      40.2일        0.9824
    #
    # noisy-OR 는 가중합보다 오히려 낮다 — wᵢ·sᵢ 가 0.45 를 못 넘어 합집합이
    # 커지지 않는다. 단순최대는 범위는 같지만 중앙이 0.327 로 올라 약한 신호도
    # 그대로 경보가 되므로 과잉 경보 위험이 있다. 가중최대는 범위를 살리면서
    # 중앙을 0.242 로 유지하고 순서도 0.971 보존한다.
    weight_values = [
        float(supply_weights["supply_news"]),
        float(supply_weights["material_news"]),
        float(supply_weights["market_price"]),
    ]
    base_supply_risk = (
        pd.concat(
            [
                supply_news_contribution,
                material_news_contribution,
                market_price_contribution,
            ],
            axis=1,
        ).max(axis=1)
        / max(weight_values)
    ).clip(0, 1)
    trade_overlay_pressure = (
        float(config["trade_signal"]["module_c_overlay_weight"]) * trade_risk
    ).clip(0, 1)
    combined_supply_risk = (
        1.0 - (1.0 - base_supply_risk) * (1.0 - trade_overlay_pressure)
    ).clip(0, 1)
    trade_active = trade_overlay_pressure.gt(0)
    supply_risk = combined_supply_risk.where(trade_active, base_supply_risk)
    trade_contribution = (
        (supply_risk - base_supply_risk).clip(0, 1).where(trade_active, 0.0)
    )

    news_confidence = _numeric(merged, "news_signal_confidence", 0.60)
    market_confidence = _numeric(merged, "market_signal_confidence")
    trade_confidence = _numeric(merged, "trade_signal_confidence")
    any_news = supply_news.gt(0) | material_news.gt(0) | demand_risk.gt(0)
    confidence_denominator = (
        any_news.astype(float)
        + market_price.gt(0).astype(float)
        + trade_gate.astype(float)
    )
    confidence = (
        news_confidence * any_news.astype(float)
        + market_confidence * market_price.gt(0).astype(float)
        + trade_confidence * trade_gate.astype(float)
    ).div(confidence_denominator.replace(0, np.nan)).fillna(0.0).clip(0, 1)

    market_event_codes = (
        merged["market_event_codes"].fillna("")
        if "market_event_codes" in merged.columns
        else pd.Series("", index=merged.index, dtype="string")
    )
    news_event_codes = (
        merged["news_event_codes"].fillna("")
        if "news_event_codes" in merged.columns
        else pd.Series("", index=merged.index, dtype="string")
    )
    trade_event_codes = (
        merged["trade_event_codes"].fillna("")
        if "trade_event_codes" in merged.columns
        else pd.Series("", index=merged.index, dtype="string")
    )
    combined_event_codes = (
        news_event_codes.astype("string")
        .str.cat(market_event_codes.astype("string"), sep=";")
        .str.cat(trade_event_codes.astype("string"), sep=";")
        .str.replace(r";+", ";", regex=True)
        .str.strip(";")
    )
    scores = pd.DataFrame(
        {
            "STD_YYYYMM": merged["STD_YYYYMM"],
            "stock_item_key": merged["stock_item_key"].astype(str),
            "module_c_demand_risk": demand_risk,
            "module_c_supply_news_risk": supply_news,
            "module_c_material_news_risk": material_news,
            "module_c_market_price_risk": market_price,
            "module_c_trade_risk": trade_risk,
            "module_c_supply_news_contribution": supply_news_contribution,
            "module_c_material_news_contribution": material_news_contribution,
            "module_c_market_price_contribution": market_price_contribution,
            "module_c_trade_contribution": trade_contribution,
            "module_c_supply_risk": supply_risk,
            "module_c_total_risk": pd.concat([demand_risk, supply_risk], axis=1).max(axis=1),
            "module_c_signal_confidence": confidence,
            "module_c_has_approved_material_mapping": material_gate,
            "module_c_has_approved_trade_mapping": trade_gate,
            "module_c_has_approved_demand_mapping": demand_gate,
            "module_c_adjustment_enabled": material_gate | demand_gate,
            "module_c_news_event_codes": news_event_codes,
            "module_c_market_event_codes": market_event_codes,
            "module_c_trade_event_codes": trade_event_codes,
            "module_c_event_codes": combined_event_codes,
            "module_c_config_version": config["version"],
            "module_c_calibration_status": config["calibration_status"],
        }
    )
    thresholds = config["alert_thresholds"]
    for risk_column, level_column in [
        ("module_c_total_risk", "module_c_risk_level"),
        ("module_c_supply_risk", "module_c_event_supply_risk_level"),
    ]:
        values = scores[risk_column]
        scores[level_column] = np.select(
            [
                values.ge(float(thresholds["critical"])),
                values.ge(float(thresholds["warning"])),
                values.ge(float(thresholds["watch"])),
            ],
            ["critical", "warning", "watch"],
            default="normal",
        )
    scores = scores[MODULE_C_SCORE_COLUMNS].sort_values(KEY_COLUMNS).reset_index(drop=True)
    audit = _build_audit(scores, config, scope=audit_scope)
    alerts = _build_alerts(scores, config)
    return scores, audit, alerts
