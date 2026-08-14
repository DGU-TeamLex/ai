from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import (
    MATERIAL_APPROVAL_AUDIT_PATH,
    MATERIAL_APPROVAL_REPORT_PATH,
    MATERIAL_INVENTORY_IMPACT_BY_SPEC_PATH,
    MATERIAL_INVENTORY_IMPACT_DETAIL_PATH,
    MATERIAL_INVENTORY_IMPACT_REPORT_PATH,
    MATERIAL_INVENTORY_IMPACT_SAMPLE_PATH,
    MODEL_VALIDATION_REPORT_PATH,
    MODULE_C_RUN_REPORT_PATH,
    PREDICTION_PATH,
)
from ..utils import ensure_dirs, guard_not_empty, write_json


PREDICTION_REQUIRED_COLUMNS = {
    "year_month",
    "stock_item_key",
    "local_item_key",
    "item_name",
    "predicted_usage",
    "base_stock",
    "risk_buffer",
    "target_stock",
    "recommended_order",
    "module_c_market_price_risk",
    "module_c_supply_risk",
    "module_c_total_risk",
    "module_c_policy_applied",
    "has_approved_material_mapping",
}
DETAIL_COLUMNS = [
    "year_month",
    "institution_code",
    "department",
    "item_code",
    "stock_item_key",
    "local_item_key",
    "item_name",
    "item_family_id",
    "item_subtype_id",
    "normalized_specification",
    "unit_code",
    "approval_rule_id",
    "approved_related_materials",
    "approved_raw_material_meta_codes",
    "approved_raw_material_risk_meta_codes",
    "approved_material_mapping_versions",
    "predicted_usage",
    "current_stock",
    "module_c_market_price_risk",
    "module_c_supply_risk",
    "module_c_total_risk",
    "module_c_signal_confidence",
    "lead_time_days",
    "effective_lead_time_days",
    "dynamic_safety_stock_rate",
    "base_stock",
    "risk_buffer",
    "target_stock",
    "recommended_order",
    "module_c_policy_applied",
    "module_c_config_version",
    "module_c_calibration_status",
]


def _as_bool(series: pd.Series) -> pd.Series:
    return (
        series.astype("string")
        .fillna("")
        .str.strip()
        .str.lower()
        .isin({"true", "t", "1", "yes", "y"})
    )


def _as_bool_value(value: object) -> bool:
    return str(value).strip().lower() in {"true", "t", "1", "yes", "y"}


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _finite_float(value: object, default: float = 0.0) -> float:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return float(numeric) if pd.notna(numeric) and np.isfinite(numeric) else default


def _quantiles(series: pd.Series) -> dict[str, float]:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "mean": float(numeric.mean()),
        "p50": float(numeric.quantile(0.50)),
        "p95": float(numeric.quantile(0.95)),
        "max": float(numeric.max()),
    }


def _build_detail(
    predictions: pd.DataFrame,
    approval_audit: pd.DataFrame | None,
) -> pd.DataFrame:
    applied = _as_bool(predictions["module_c_policy_applied"])
    detail = predictions.loc[applied].copy()
    if approval_audit is not None and not approval_audit.empty:
        audit = approval_audit.copy()
        if {"local_item_key", "approval_status"}.issubset(audit.columns):
            audit = audit[
                audit["approval_status"].astype(str).str.lower().eq("approved")
            ].copy()
            audit_columns = [
                "local_item_key",
                "item_family_id",
                "item_subtype_id",
                "normalized_specification",
                "unit_code",
                "approval_rule_id",
            ]
            available = [column for column in audit_columns if column in audit.columns]
            audit = audit[available].drop_duplicates("local_item_key")
            detail = detail.merge(
                audit,
                on="local_item_key",
                how="left",
                validate="many_to_one",
            )
    for column in DETAIL_COLUMNS:
        if column not in detail.columns:
            detail[column] = ""
    return detail[DETAIL_COLUMNS].sort_values(
        ["risk_buffer", "stock_item_key"], ascending=[False, True]
    ).reset_index(drop=True)


def _build_by_spec(detail: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "item_family_id",
        "item_subtype_id",
        "normalized_specification",
        "unit_code",
        "stock_item_count",
        "positive_usage_item_count",
        "predicted_usage_sum",
        "base_stock_sum",
        "risk_buffer_sum",
        "target_stock_sum",
        "risk_buffer_pct_of_base",
        "recommended_order_sum",
    ]
    if detail.empty:
        return pd.DataFrame(columns=columns)
    group_columns = [
        "item_family_id",
        "item_subtype_id",
        "normalized_specification",
        "unit_code",
    ]
    grouped = (
        detail.assign(
            normalized_specification=detail["normalized_specification"].replace(
                "", "UNRESOLVED"
            ),
            positive_usage=detail["predicted_usage"].gt(0).astype(int),
        )
        .groupby(group_columns, as_index=False, dropna=False, observed=True)
        .agg(
            stock_item_count=("stock_item_key", "nunique"),
            positive_usage_item_count=("positive_usage", "sum"),
            predicted_usage_sum=("predicted_usage", "sum"),
            base_stock_sum=("base_stock", "sum"),
            risk_buffer_sum=("risk_buffer", "sum"),
            target_stock_sum=("target_stock", "sum"),
            recommended_order_sum=("recommended_order", "sum"),
        )
    )
    grouped["risk_buffer_pct_of_base"] = (
        100
        * grouped["risk_buffer_sum"]
        / grouped["base_stock_sum"].replace(0, np.nan)
    ).fillna(0.0)
    return grouped[columns].sort_values(
        ["risk_buffer_sum", "stock_item_count"], ascending=[False, False]
    ).reset_index(drop=True)


def _baseline_comparison(
    predictions: pd.DataFrame,
    baseline: pd.DataFrame | None,
) -> dict:
    if baseline is None:
        return {"available": False}
    keys = ["year_month", "stock_item_key"]
    columns = ["predicted_usage", "base_stock", "target_stock", "recommended_order"]
    missing = set(keys + columns) - set(baseline.columns)
    if missing:
        raise ValueError(f"Baseline predictions are missing columns: {sorted(missing)}")
    if predictions.duplicated(keys).any() or baseline.duplicated(keys).any():
        raise ValueError("Prediction comparison keys must be unique")
    comparison = predictions[keys + columns].merge(
        baseline[keys + columns],
        on=keys,
        how="outer",
        suffixes=("_after", "_before"),
        indicator=True,
        validate="one_to_one",
    )
    matched = comparison[comparison["_merge"].eq("both")].copy()
    tolerance = 1e-9
    predicted_delta = matched["predicted_usage_after"] - matched["predicted_usage_before"]
    base_delta = matched["base_stock_after"] - matched["base_stock_before"]
    target_delta = matched["target_stock_after"] - matched["target_stock_before"]
    order_delta = (
        matched["recommended_order_after"] - matched["recommended_order_before"]
    )
    return {
        "available": True,
        "before_rows": int(len(baseline)),
        "after_rows": int(len(predictions)),
        "matched_rows": int(len(matched)),
        "left_only_rows": int(comparison["_merge"].eq("left_only").sum()),
        "right_only_rows": int(comparison["_merge"].eq("right_only").sum()),
        "predicted_usage_changed_rows": int(predicted_delta.abs().gt(tolerance).sum()),
        "predicted_usage_max_abs_delta": _finite_float(predicted_delta.abs().max()),
        "base_stock_changed_rows": int(base_delta.abs().gt(tolerance).sum()),
        "base_stock_max_abs_delta": _finite_float(base_delta.abs().max()),
        "target_stock_delta_sum_raw": float(target_delta.sum()),
        "recommended_order_delta_sum_raw": float(order_delta.sum()),
    }


def build_material_inventory_report(
    predictions: pd.DataFrame,
    validation: pd.DataFrame,
    approval_report: dict,
    module_c_report: dict,
    approval_audit: pd.DataFrame | None = None,
    baseline: pd.DataFrame | None = None,
) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    missing = PREDICTION_REQUIRED_COLUMNS - set(predictions.columns)
    if missing:
        raise ValueError(f"Predictions are missing columns: {sorted(missing)}")
    detail = _build_detail(predictions, approval_audit)
    by_spec = _build_by_spec(detail)
    applied = _as_bool(predictions["module_c_policy_applied"])
    approved = _as_bool(predictions["has_approved_material_mapping"])
    positive_buffer = (
        pd.to_numeric(predictions["risk_buffer"], errors="coerce").fillna(0).gt(0)
    )
    base_stock_sum = float(
        pd.to_numeric(predictions["base_stock"], errors="coerce").fillna(0).sum()
    )
    risk_buffer_sum = float(
        pd.to_numeric(predictions["risk_buffer"], errors="coerce").fillna(0).sum()
    )

    validation_records = []
    if not validation.empty and {"model", "WAPE"}.issubset(validation.columns):
        ready = validation.copy()
        if "status" in ready.columns:
            ready = ready[ready["status"].eq("ready")].copy()
        ready["WAPE"] = pd.to_numeric(ready["WAPE"], errors="coerce")
        ready = ready[ready["WAPE"].notna()].sort_values("WAPE")
        for _, row in ready.iterrows():
            validation_records.append(
                {
                    "model": str(row["model"]),
                    "method_type": str(row.get("method_type", "")),
                    "uses_module_c": _as_bool_value(
                        row.get("uses_module_c", False)
                    ),
                    "wape": float(row["WAPE"]),
                    "selected_on_validation": _as_bool_value(
                        row.get("selected_on_validation", False)
                    ),
                }
            )

    forecast_months = sorted(predictions["year_month"].astype(str).unique().tolist())
    uplift_pct = (
        100
        * pd.to_numeric(detail["risk_buffer"], errors="coerce")
        / pd.to_numeric(detail["base_stock"], errors="coerce").replace(0, np.nan)
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "experimental_result_not_production_calibrated",
        "forecast_months": forecast_months,
        "prediction_rows": int(len(predictions)),
        "approved_mapping_rows": int(
            approval_report.get("approved_stock_item_mapping_rows", 0)
        ),
        "approved_mapping_stock_item_count": int(
            approval_report.get("approved_stock_item_count", 0)
        ),
        "current_forecast_approved_mapping_rows": int(approved.sum()),
        "module_c_policy_applied_rows": int(applied.sum()),
        "positive_risk_buffer_rows": int(positive_buffer.sum()),
        "zero_usage_applied_rows": int((applied & ~positive_buffer).sum()),
        "unapproved_nonzero_buffer_rows": int((~approved & positive_buffer).sum()),
        "predicted_usage_sum": float(
            pd.to_numeric(predictions["predicted_usage"], errors="coerce").fillna(0).sum()
        ),
        "base_stock_sum": base_stock_sum,
        "risk_buffer_sum": risk_buffer_sum,
        "target_stock_sum": float(
            pd.to_numeric(predictions["target_stock"], errors="coerce").fillna(0).sum()
        ),
        "risk_buffer_pct_of_all_base_stock": (
            100 * risk_buffer_sum / base_stock_sum if base_stock_sum else 0.0
        ),
        "applied_item_risk_buffer": _quantiles(detail["risk_buffer"]),
        "applied_item_target_uplift_pct": _quantiles(uplift_pct),
        "market_price_risk": _quantiles(detail["module_c_market_price_risk"]),
        "supply_risk": _quantiles(detail["module_c_supply_risk"]),
        "validation_models": validation_records,
        "approval": approval_report,
        "module_c": module_c_report,
        "baseline_comparison": _baseline_comparison(predictions, baseline),
        "limitations": [
            "Only explicitly approved disposable-syringe PP mappings are active.",
            "The current market input uses Brent as an indirect petrochemical proxy because direct naphtha prices are unavailable.",
            "News and disease-demand signals are disabled because no complete approved input was available.",
            "All numeric coefficients are policy seeds and require empirical calibration before production use.",
        ],
    }
    return report, detail, by_spec


def run_material_inventory_report(
    prediction_path: Path = PREDICTION_PATH,
    validation_path: Path = MODEL_VALIDATION_REPORT_PATH,
    approval_report_path: Path = MATERIAL_APPROVAL_REPORT_PATH,
    module_c_report_path: Path = MODULE_C_RUN_REPORT_PATH,
    approval_audit_path: Path = MATERIAL_APPROVAL_AUDIT_PATH,
    baseline_path: Path | None = None,
) -> dict:
    predictions = pd.read_csv(prediction_path, low_memory=False)
    validation = pd.read_csv(validation_path, low_memory=False)
    approval_audit = (
        pd.read_csv(approval_audit_path, low_memory=False, keep_default_na=False)
        if approval_audit_path.exists()
        else None
    )
    baseline = (
        pd.read_csv(
            baseline_path,
            usecols=[
                "year_month",
                "stock_item_key",
                "predicted_usage",
                "base_stock",
                "target_stock",
                "recommended_order",
            ],
            low_memory=False,
        )
        if baseline_path is not None
        else None
    )
    report, detail, by_spec = build_material_inventory_report(
        predictions,
        validation,
        _read_json(approval_report_path),
        _read_json(module_c_report_path),
        approval_audit=approval_audit,
        baseline=baseline,
    )
    ensure_dirs(
        MATERIAL_INVENTORY_IMPACT_REPORT_PATH.parent,
        MATERIAL_INVENTORY_IMPACT_SAMPLE_PATH.parent,
    )
    write_json(report, MATERIAL_INVENTORY_IMPACT_REPORT_PATH)
    guard_not_empty(detail, MATERIAL_INVENTORY_IMPACT_DETAIL_PATH, "원자재 재고영향")
    detail.to_csv(MATERIAL_INVENTORY_IMPACT_DETAIL_PATH, index=False)
    by_spec.to_csv(MATERIAL_INVENTORY_IMPACT_BY_SPEC_PATH, index=False)
    detail.head(1000).to_csv(
        MATERIAL_INVENTORY_IMPACT_SAMPLE_PATH,
        index=False,
        encoding="utf-8-sig",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize approved material mapping impact on final inventory"
    )
    parser.add_argument("--predictions", type=Path, default=PREDICTION_PATH)
    parser.add_argument("--validation", type=Path, default=MODEL_VALIDATION_REPORT_PATH)
    parser.add_argument("--approval-report", type=Path, default=MATERIAL_APPROVAL_REPORT_PATH)
    parser.add_argument("--module-c-report", type=Path, default=MODULE_C_RUN_REPORT_PATH)
    parser.add_argument("--approval-audit", type=Path, default=MATERIAL_APPROVAL_AUDIT_PATH)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    report = run_material_inventory_report(
        prediction_path=args.predictions,
        validation_path=args.validation,
        approval_report_path=args.approval_report,
        module_c_report_path=args.module_c_report,
        approval_audit_path=args.approval_audit,
        baseline_path=args.baseline,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
