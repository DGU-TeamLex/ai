from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import (
    EVALUATION_REPORT_PATH,
    MODEL_MANIFEST_PATH,
    MODEL_VARIANTS,
    OUTPUT_DIR,
    PREDICTION_PATH,
)
from ..modeling.inventory_policy import add_inventory_recommendations
from ..module_c.config import load_module_c_config
from ..module_c.risk_engine import build_module_c_risk_outputs, combine_supply_signals


DEFAULT_OUTPUT_DIR = OUTPUT_DIR / "midterm_2026-07-23"
SIMPLE_OUTPUT_NAME = "01_simple_inventory_output.csv"
WEIGHT_OUTPUT_NAME = "02_weight_selection_output.csv"
FINAL_OUTPUT_NAME = "03_final_inventory_output.csv"
DECISION_OUTPUT_NAME = "04_decision_evidence.csv"

PREDICTION_COLUMNS = [
    "forecast_origin_month",
    "year_month",
    "institution_code",
    "department",
    "item_code",
    "item_name",
    "stock_item_key",
    "history_months",
    "demand_pattern",
    "normalization_status",
    "item_group_id_candidate",
    "primary_model",
    "predicted_usage",
    "current_stock",
    "review_period_days",
    "lead_time_days",
    "safety_stock",
    "base_stock",
    "target_stock",
    "recommended_order",
    "data_age_months",
    "is_stale_data",
]

SCENARIOS = [
    {
        "scenario_order": 1,
        "scenario_id": "CURRENT_OPERATION_NO_APPROVED_RELATION",
        "scenario_description": "Current gated state with no approved risk relation",
        "demand_news_risk": 0.0,
        "supply_news_risk": 0.0,
        "material_news_risk": 0.0,
        "commodity_risk": 0.0,
        "approved_demand_mapping": False,
        "approved_material_mapping": False,
        "is_synthetic_signal": False,
    },
    {
        "scenario_order": 2,
        "scenario_id": "UNAPPROVED_HIGH_SIGNALS_BLOCKED",
        "scenario_description": "High raw signals blocked by approval gates",
        "demand_news_risk": 0.8,
        "supply_news_risk": 0.8,
        "material_news_risk": 0.7,
        "commodity_risk": 0.9,
        "approved_demand_mapping": False,
        "approved_material_mapping": False,
        "is_synthetic_signal": True,
    },
    {
        "scenario_order": 3,
        "scenario_id": "APPROVED_NORMAL",
        "scenario_description": "Approved low-level signals",
        "demand_news_risk": 0.1,
        "supply_news_risk": 0.1,
        "material_news_risk": 0.1,
        "commodity_risk": 0.1,
        "approved_demand_mapping": True,
        "approved_material_mapping": True,
        "is_synthetic_signal": True,
    },
    {
        "scenario_order": 4,
        "scenario_id": "APPROVED_DEMAND_SURGE",
        "scenario_description": "Approved disease-driven demand surge",
        "demand_news_risk": 0.8,
        "supply_news_risk": 0.1,
        "material_news_risk": 0.1,
        "commodity_risk": 0.1,
        "approved_demand_mapping": True,
        "approved_material_mapping": True,
        "is_synthetic_signal": True,
    },
    {
        "scenario_order": 5,
        "scenario_id": "APPROVED_SUPPLY_DISRUPTION",
        "scenario_description": "Approved supply and market disruption",
        "demand_news_risk": 0.1,
        "supply_news_risk": 0.8,
        "material_news_risk": 0.6,
        "commodity_risk": 0.5,
        "approved_demand_mapping": True,
        "approved_material_mapping": True,
        "is_synthetic_signal": True,
    },
    {
        "scenario_order": 6,
        "scenario_id": "APPROVED_COMBINED_CRITICAL",
        "scenario_description": "Approved combined demand and supply shock",
        "demand_news_risk": 0.7,
        "supply_news_risk": 0.9,
        "material_news_risk": 0.8,
        "commodity_risk": 0.9,
        "approved_demand_mapping": True,
        "approved_material_mapping": True,
        "is_synthetic_signal": True,
    },
]


def _require_columns(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"{label} is missing required columns: {missing}")


def _round_numeric(frame: pd.DataFrame, digits: int = 3) -> pd.DataFrame:
    result = frame.copy()
    numeric_columns = result.select_dtypes(include=["number"]).columns
    result[numeric_columns] = result[numeric_columns].round(digits)
    return result


def build_simple_inventory_output(
    predictions: pd.DataFrame,
    sample_size: int = 100,
) -> pd.DataFrame:
    if sample_size <= 0:
        raise ValueError("sample_size must be positive")
    _require_columns(predictions, PREDICTION_COLUMNS, "prediction output")

    result = predictions[PREDICTION_COLUMNS].copy()
    numeric_columns = [
        "history_months",
        "predicted_usage",
        "current_stock",
        "review_period_days",
        "lead_time_days",
        "safety_stock",
        "base_stock",
        "target_stock",
        "recommended_order",
        "data_age_months",
    ]
    for column in numeric_columns:
        result[column] = pd.to_numeric(result[column], errors="coerce")

    result = result[
        result["predicted_usage"].gt(0)
        & result["history_months"].ge(6)
        & result["current_stock"].ge(0)
        & result["institution_code"].astype(str).str.fullmatch(r"[A-Z][0-9]{4}")
        & result["item_group_id_candidate"].fillna("").ne("UNCLASSIFIED")
    ].copy()
    if result.empty:
        raise ValueError("No eligible rows are available for the presentation sample")

    result["order_decision"] = np.where(
        result["recommended_order"].gt(0),
        "ORDER",
        "HOLD",
    )
    result["order_gap_ratio"] = (
        result["recommended_order"] / result["base_stock"].replace(0, np.nan)
    ).fillna(0.0)
    result = result.sort_values(
        [
            "item_group_id_candidate",
            "order_decision",
            "order_gap_ratio",
            "predicted_usage",
            "stock_item_key",
        ],
        ascending=[True, True, False, False, True],
        kind="mergesort",
    )
    stratified = result.groupby(
        ["item_group_id_candidate", "order_decision"],
        dropna=False,
        group_keys=False,
    ).head(5)
    selected_index = list(stratified.index[:sample_size])
    if len(selected_index) < sample_size:
        remaining = result.loc[~result.index.isin(selected_index)].sort_values(
            ["recommended_order", "predicted_usage", "stock_item_key"],
            ascending=[False, False, True],
            kind="mergesort",
        )
        selected_index.extend(list(remaining.index[: sample_size - len(selected_index)]))

    sample = result.loc[selected_index].copy()
    recomputed = (
        sample["predicted_usage"]
        * (sample["review_period_days"] + sample["lead_time_days"])
        / 30.0
        * 1.20
    )
    sample["base_stock_formula_error"] = (sample["base_stock"] - recomputed).abs()
    sample["base_stock_formula_check"] = sample["base_stock_formula_error"].le(1e-6)
    sample["quantity_unit_status"] = "LOCAL_SOURCE_UNIT_NOT_STANDARDIZED"
    stale = sample["is_stale_data"].astype(str).str.lower().isin({"true", "1", "t"})
    sample["release_status"] = np.where(
        stale,
        "DEMO_ONLY_STALE_INPUT",
        "REVIEW_REQUIRED",
    )

    columns = [
        "forecast_origin_month",
        "year_month",
        "institution_code",
        "department",
        "item_code",
        "item_name",
        "stock_item_key",
        "item_group_id_candidate",
        "normalization_status",
        "demand_pattern",
        "history_months",
        "primary_model",
        "predicted_usage",
        "review_period_days",
        "lead_time_days",
        "safety_stock",
        "base_stock",
        "current_stock",
        "recommended_order",
        "order_decision",
        "base_stock_formula_error",
        "base_stock_formula_check",
        "quantity_unit_status",
        "data_age_months",
        "is_stale_data",
        "release_status",
    ]
    return _round_numeric(sample[columns].reset_index(drop=True))


def build_weight_selection_output(config: dict | None = None) -> pd.DataFrame:
    config = config or load_module_c_config()
    scenario_frame = pd.DataFrame(SCENARIOS)
    news = pd.DataFrame(
        {
            "STD_YYYYMM": "2026-07",
            "stock_item_key": scenario_frame["scenario_id"],
            "disease_news_risk": scenario_frame["demand_news_risk"],
            "supply_news_risk": scenario_frame["supply_news_risk"],
            "material_news_risk": scenario_frame["material_news_risk"],
            "news_signal_confidence": 0.85,
            "has_approved_demand_mapping": scenario_frame[
                "approved_demand_mapping"
            ],
            "has_approved_material_mapping": scenario_frame[
                "approved_material_mapping"
            ],
            "news_event_codes": "PRESENTATION_SCENARIO",
        }
    )
    market = pd.DataFrame(
        {
            "STD_YYYYMM": "2026-07",
            "stock_item_key": scenario_frame["scenario_id"],
            "commodity_risk": scenario_frame["commodity_risk"],
            "market_signal_confidence": 0.80,
            "market_factor_count": scenario_frame[
                "approved_material_mapping"
            ].astype(int),
            "market_event_codes": "PRESENTATION_MARKET_SCENARIO",
        }
    )
    scores, _, _ = build_module_c_risk_outputs(news, market, config=config)
    scores = scores.rename(columns={"stock_item_key": "scenario_id"})
    result = scenario_frame.merge(scores, on="scenario_id", how="left", validate="one_to_one")

    supply_weights = config["supply_signal"]
    result["weight_supply_news"] = float(supply_weights["supply_news"])
    result["weight_material_news"] = float(supply_weights["material_news"])
    result["weight_market_price"] = float(supply_weights["market_price"])
    # 수식을 여기서 다시 쓰지 않는다. risk_engine 의 단일 정의를 그대로 호출한다.
    # 종전에는 가중합을 이 파일이 따로 갖고 있어서, risk_engine 이 가중최대로
    # 바뀌자 "supply-weight formula mismatch" 로 검증이 터졌다(ai#20 결함 O).
    expected_supply = combine_supply_signals(
        result["module_c_supply_news_risk"],
        result["module_c_material_news_risk"],
        result["module_c_market_price_risk"],
        supply_weights,
    )
    result["supply_weight_formula_error"] = (
        result["module_c_supply_risk"] - expected_supply
    ).abs()
    result["supply_weight_formula_check"] = result[
        "supply_weight_formula_error"
    ].le(1e-9)
    result["operational_use_allowed"] = False
    result["output_status"] = np.where(
        result["is_synthetic_signal"],
        "DEMO_SCENARIO_ONLY",
        "CURRENT_GATED_STATE",
    )

    columns = [
        "scenario_order",
        "scenario_id",
        "scenario_description",
        "is_synthetic_signal",
        "approved_demand_mapping",
        "approved_material_mapping",
        "demand_news_risk",
        "supply_news_risk",
        "material_news_risk",
        "commodity_risk",
        "weight_supply_news",
        "weight_material_news",
        "weight_market_price",
        "module_c_supply_news_contribution",
        "module_c_material_news_contribution",
        "module_c_market_price_contribution",
        "module_c_demand_risk",
        "module_c_supply_news_risk",
        "module_c_material_news_risk",
        "module_c_market_price_risk",
        "module_c_supply_risk",
        "module_c_total_risk",
        "module_c_risk_level",
        "module_c_adjustment_enabled",
        "supply_weight_formula_error",
        "supply_weight_formula_check",
        "module_c_config_version",
        "module_c_calibration_status",
        "operational_use_allowed",
        "output_status",
    ]
    return _round_numeric(result[columns].sort_values("scenario_order").reset_index(drop=True))


def build_final_inventory_output(
    simple_output: pd.DataFrame,
    weight_output: pd.DataFrame,
    config: dict | None = None,
    item_count: int = 12,
) -> pd.DataFrame:
    if item_count <= 0:
        raise ValueError("item_count must be positive")
    config = config or load_module_c_config()
    _require_columns(
        simple_output,
        [
            "stock_item_key",
            "item_name",
            "item_group_id_candidate",
            "predicted_usage",
            "current_stock",
            "review_period_days",
            "lead_time_days",
            "base_stock",
            "recommended_order",
            "data_age_months",
        ],
        "simple inventory output",
    )
    _require_columns(
        weight_output,
        [
            "scenario_id",
            "module_c_demand_risk",
            "module_c_supply_risk",
            "module_c_market_price_risk",
            "module_c_total_risk",
            "is_synthetic_signal",
        ],
        "weight output",
    )

    presentation_items = simple_output[simple_output["predicted_usage"].ge(10)].copy()
    if len(presentation_items) < item_count:
        presentation_items = simple_output.copy()
    order_candidates = presentation_items[
        presentation_items["recommended_order"].gt(0)
    ].sort_values(
        ["item_group_id_candidate", "recommended_order", "stock_item_key"],
        ascending=[True, False, True],
        kind="mergesort",
    )
    hold_candidates = presentation_items[
        presentation_items["recommended_order"].le(0)
    ].sort_values(
        ["item_group_id_candidate", "current_stock", "stock_item_key"],
        ascending=[True, False, True],
        kind="mergesort",
    )
    orders = order_candidates.groupby(
        "item_group_id_candidate", dropna=False, group_keys=False
    ).head(1).head((item_count + 1) // 2)
    holds = hold_candidates.groupby(
        "item_group_id_candidate", dropna=False, group_keys=False
    ).head(1).head(item_count // 2)
    selected = pd.concat([orders, holds], ignore_index=True)
    if len(selected) < item_count:
        used = set(selected["stock_item_key"])
        extra = presentation_items[
            ~presentation_items["stock_item_key"].isin(used)
        ].head(item_count - len(selected))
        selected = pd.concat([selected, extra], ignore_index=True)
    selected = selected.head(item_count).copy()
    if selected.empty:
        raise ValueError("No items are available for the combined output")

    scenario_ids = [
        "CURRENT_OPERATION_NO_APPROVED_RELATION",
        "APPROVED_DEMAND_SURGE",
        "APPROVED_SUPPLY_DISRUPTION",
        "APPROVED_COMBINED_CRITICAL",
    ]
    scenarios = weight_output[weight_output["scenario_id"].isin(scenario_ids)].copy()
    selected["_join_key"] = 1
    scenarios["_join_key"] = 1
    combined = selected.merge(scenarios, on="_join_key", how="inner").drop(
        columns="_join_key"
    )
    combined = combined.rename(
        columns={
            "base_stock": "simple_base_stock",
            "recommended_order": "simple_recommended_order",
        }
    )
    combined["external_demand_signal_in_forecast"] = False

    adjusted = add_inventory_recommendations(
        combined,
        prediction_col="predicted_usage",
        current_stock_col="current_stock",
        lead_time_days_col="lead_time_days",
        review_period_days_col="review_period_days",
        module_c_config=config,
    )
    adjusted["target_stock_delta"] = (
        adjusted["target_stock"] - adjusted["simple_base_stock"]
    )
    adjusted["recommended_order_delta"] = (
        adjusted["recommended_order"] - adjusted["simple_recommended_order"]
    )
    adjusted.loc[
        adjusted["target_stock_delta"].abs().le(0.005),
        "target_stock_delta",
    ] = 0.0
    adjusted.loc[
        adjusted["recommended_order_delta"].abs().le(0.005),
        "recommended_order_delta",
    ] = 0.0
    adjusted["target_stock_increase_pct"] = (
        adjusted["target_stock_delta"]
        / adjusted["simple_base_stock"].replace(0, np.nan)
        * 100.0
    ).fillna(0.0)
    adjusted["quantity_unit_status"] = "LOCAL_SOURCE_UNIT_NOT_STANDARDIZED"
    adjusted["operational_use_allowed"] = False
    adjusted["release_status"] = np.where(
        adjusted["is_synthetic_signal"],
        "DEMO_ONLY_SYNTHETIC_SIGNAL",
        "BLOCKED_STALE_INPUT_AND_MAPPING_GATE",
    )

    columns = [
        "scenario_id",
        "is_synthetic_signal",
        "stock_item_key",
        "item_name",
        "item_group_id_candidate",
        "predicted_usage",
        "current_stock",
        "review_period_days",
        "lead_time_days",
        "module_c_demand_risk",
        "module_c_supply_risk",
        "module_c_total_risk",
        "simple_base_stock",
        "simple_recommended_order",
        "risk_adjusted_predicted_usage",
        "effective_lead_time_days",
        "dynamic_safety_stock_rate",
        "risk_buffer",
        "target_stock",
        "target_stock_delta",
        "target_stock_increase_pct",
        "inventory_position",
        "recommended_order",
        "recommended_order_delta",
        "module_c_policy_applied",
        "module_c_config_version",
        "module_c_calibration_status",
        "quantity_unit_status",
        "data_age_months",
        "operational_use_allowed",
        "release_status",
    ]
    return _round_numeric(
        adjusted[columns]
        .sort_values(["stock_item_key", "scenario_id"], kind="mergesort")
        .reset_index(drop=True)
    )


def build_decision_evidence(
    manifest: list[dict],
    config: dict | None = None,
    test_report: pd.DataFrame | None = None,
) -> pd.DataFrame:
    config = config or load_module_c_config()
    test_metrics: dict[str, dict] = {}
    if test_report is not None and not test_report.empty:
        _require_columns(
            test_report,
            ["model", "WAPE", "BIAS_PCT"],
            "test evaluation report",
        )
        test_metrics = {
            str(row["model"]): row
            for row in test_report.to_dict(orient="records")
        }
    rows: list[dict] = []
    decision_order = 1
    manifest_models = set()
    for model in manifest:
        model_name = str(model.get("model", ""))
        manifest_models.add(model_name)
        wape = model.get("WAPE")
        holdout = test_metrics.get(f"{model_name}_pred", {})
        rows.append(
            {
                "decision_order": decision_order,
                "decision_area": "usage_forecast_model",
                "candidate": model_name,
                "evidence_metric": "validation_WAPE_pct",
                "evidence_value": wape,
                "validation_wape_pct": wape,
                "test_wape_pct": holdout.get("WAPE"),
                "test_bias_pct": holdout.get("BIAS_PCT"),
                "selected": bool(model.get("selected_on_validation", False)),
                "current_status": model.get("status", "unknown"),
                "selection_reason": (
                    "Lowest chronological validation WAPE"
                    if model.get("selected_on_validation", False)
                    else model.get("skip_reason") or "Higher validation error"
                ),
                "remaining_work": (
                    "Monitor underprediction bias and segment error"
                    if model.get("selected_on_validation", False)
                    else "Re-evaluate only when inputs or model design changes"
                ),
            }
        )
        decision_order += 1

    for model_name in MODEL_VARIANTS:
        if model_name in manifest_models:
            continue
        rows.append(
            {
                "decision_order": decision_order,
                "decision_area": "usage_forecast_model",
                "candidate": model_name,
                "evidence_metric": "artifact_presence",
                "evidence_value": 0,
                "validation_wape_pct": None,
                "test_wape_pct": None,
                "test_bias_pct": None,
                "selected": False,
                "current_status": "configured_not_in_current_manifest",
                "selection_reason": "Current model artifact predates this configured variant",
                "remaining_work": "Rebuild training manifest after approved non-zero signals exist",
            }
        )
        decision_order += 1

    policy_rows = [
        {
            "decision_area": "validation_design",
            "candidate": "chronological_train_validation_test",
            "evidence_metric": "data_leakage_control",
            "evidence_value": "train<=2024-12; valid=2025-01..06; test>=2025-07",
            "selected": True,
            "current_status": "applied",
            "selection_reason": "Inventory demand must be evaluated on future months",
            "remaining_work": "Add rolling production monitoring",
        },
        {
            "decision_area": "risk_weight_basis",
            "candidate": config["version"],
            "evidence_metric": "calibration_status",
            "evidence_value": config["calibration_status"],
            "selected": True,
            "current_status": "policy_seed_not_empirical",
            "selection_reason": "Bounded initial policy is needed before event labels exist",
            "remaining_work": "Backtest against stockouts, delays, and event windows",
        },
        {
            "decision_area": "risk_axis_design",
            "candidate": "separate_demand_and_supply_then_max",
            "evidence_metric": "double_counting_control",
            "evidence_value": "demand and supply retained separately",
            "selected": True,
            "current_status": "implemented",
            "selection_reason": "Demand changes usage while supply changes lead time and safety stock",
            "remaining_work": "Calibrate each axis independently",
        },
        {
            "decision_area": "mapping_gate",
            "candidate": "approved_relations_only",
            "evidence_metric": "approved_material_relation_count",
            "evidence_value": 0,
            "selected": True,
            "current_status": "blocking_operational_adjustment",
            "selection_reason": "Unverified item-material relations must not change stock",
            "remaining_work": "Approve priority item-material and demand-trigger relations",
        },
        {
            "decision_area": "risk_buffer_control",
            "candidate": "bounded_continuous_buffer",
            "evidence_metric": "protection_demand_cap",
            "evidence_value": config["inventory_adjustment"][
                "total_risk_buffer_rate_cap"
            ],
            "selected": True,
            "current_status": "implemented",
            "selection_reason": "Limit excessive stock growth from uncertain external signals",
            "remaining_work": "Tune cap using service-level and overstock simulation",
        },
        {
            "decision_area": "release_gate",
            "candidate": "PASS_rows_only",
            "evidence_metric": "current_PASS_rows",
            "evidence_value": 0,
            "selected": True,
            "current_status": "batch_release_blocked",
            "selection_reason": "REVIEW and BLOCK rows cannot drive automated orders",
            "remaining_work": "Resolve meta-code axis and policy mapping errors",
        },
        {
            "decision_area": "quantity_unit_policy",
            "candidate": "no_cross_unit_aggregation",
            "evidence_metric": "current_presentation_unit_status",
            "evidence_value": "local_source_unit_not_standardized",
            "selected": True,
            "current_status": "aggregation_restricted",
            "selection_reason": "Gauge, capacity, package count, and quantity are different concepts",
            "remaining_work": "Approve subtype-specific unit and conversion contracts",
        },
    ]
    for row in policy_rows:
        rows.append(
            {
                "decision_order": decision_order,
                "validation_wape_pct": None,
                "test_wape_pct": None,
                "test_bias_pct": None,
                **row,
            }
        )
        decision_order += 1
    return pd.DataFrame(rows)


def validate_presentation_outputs(
    simple_output: pd.DataFrame,
    weight_output: pd.DataFrame,
    final_output: pd.DataFrame,
    decision_output: pd.DataFrame,
) -> None:
    if not simple_output["base_stock_formula_check"].all():
        raise ValueError("Simple inventory output contains a formula mismatch")
    if not weight_output["supply_weight_formula_check"].all():
        raise ValueError("Weight output contains a supply-weight formula mismatch")
    if weight_output["operational_use_allowed"].any():
        raise ValueError("Presentation risk scenarios must not be operationally enabled")
    if final_output["operational_use_allowed"].any():
        raise ValueError("Presentation inventory scenarios must not be operationally enabled")

    current = final_output[
        final_output["scenario_id"].eq("CURRENT_OPERATION_NO_APPROVED_RELATION")
    ]
    if current.empty or current["target_stock_delta"].abs().gt(0.005).any():
        raise ValueError("Current gated scenario must preserve simple base stock")

    selected_models = decision_output[
        decision_output["decision_area"].eq("usage_forecast_model")
        & decision_output["selected"].eq(True)
    ]
    if len(selected_models) != 1:
        raise ValueError("Exactly one usage forecast model must be selected")


def run_midterm_package(
    prediction_path: Path = PREDICTION_PATH,
    manifest_path: Path = MODEL_MANIFEST_PATH,
    test_report_path: Path = EVALUATION_REPORT_PATH,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    sample_size: int = 100,
) -> dict[str, Path]:
    if not prediction_path.exists():
        raise FileNotFoundError(f"Prediction output not found: {prediction_path}")
    if not manifest_path.exists():
        raise FileNotFoundError(f"Model manifest not found: {manifest_path}")

    prediction_header = pd.read_csv(prediction_path, nrows=0).columns
    missing = sorted(set(PREDICTION_COLUMNS) - set(prediction_header))
    if missing:
        raise ValueError(f"Prediction CSV is missing required columns: {missing}")
    predictions = pd.read_csv(prediction_path, usecols=PREDICTION_COLUMNS)
    with manifest_path.open("r", encoding="utf-8") as file:
        manifest = json.load(file)
    test_report = (
        pd.read_csv(test_report_path)
        if test_report_path.exists()
        else pd.DataFrame()
    )

    config = load_module_c_config()
    simple_output = build_simple_inventory_output(predictions, sample_size=sample_size)
    weight_output = build_weight_selection_output(config)
    final_output = build_final_inventory_output(simple_output, weight_output, config)
    decision_output = build_decision_evidence(manifest, config, test_report=test_report)
    validate_presentation_outputs(
        simple_output,
        weight_output,
        final_output,
        decision_output,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "simple": output_dir / SIMPLE_OUTPUT_NAME,
        "weight": output_dir / WEIGHT_OUTPUT_NAME,
        "final": output_dir / FINAL_OUTPUT_NAME,
        "decision": output_dir / DECISION_OUTPUT_NAME,
    }
    simple_output.to_csv(paths["simple"], index=False)
    weight_output.to_csv(paths["weight"], index=False)
    final_output.to_csv(paths["final"], index=False)
    decision_output.to_csv(paths["decision"], index=False)
    return paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the four midterm presentation outputs."
    )
    parser.add_argument("--prediction-path", type=Path, default=PREDICTION_PATH)
    parser.add_argument("--manifest-path", type=Path, default=MODEL_MANIFEST_PATH)
    parser.add_argument("--test-report-path", type=Path, default=EVALUATION_REPORT_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--sample-size", type=int, default=100)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    paths = run_midterm_package(
        prediction_path=args.prediction_path,
        manifest_path=args.manifest_path,
        test_report_path=args.test_report_path,
        output_dir=args.output_dir,
        sample_size=args.sample_size,
    )
    for label, path in paths.items():
        print(f"{label}: {path}")


if __name__ == "__main__":
    main()
