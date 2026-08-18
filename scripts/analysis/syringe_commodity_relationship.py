"""주사 관련 의료소모품의 승인 원자재 연결과 예측 연관성을 집계한다.

모형을 다시 학습하지 않는다. 최종 외부위험 실험의 fold/row_id를 동일한
특성표 행 순서로 복원한 뒤 수요전용 L1과 원자재 충격 L1을 비교한다.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import FEATURE_TABLE_PATH, OUTPUT_DIR, RANDOM_STATE, VALIDATION_FOLDS


PREDICTION_PATH = OUTPUT_DIR / "external_shock_experiment_predictions.parquet"
MAPPING_PATH = ROOT / "data" / "mapping" / "stock_item_material_mapping.csv"
REPORT_PATH = OUTPUT_DIR / "syringe_commodity_relationship_report.csv"
SUMMARY_PATH = OUTPUT_DIR / "syringe_commodity_relationship_summary.json"

BASELINE_MODEL = "stock_model_a_usage_only"
COMMODITY_MODEL = "stock_model_g_commodity_shock_l1"
TARGET_FAMILIES = (
    "DISPOSABLE_SYRINGE",
    "INJECTION_NEEDLE",
    "ANGIO_CATHETER",
    "PREFILLED_SYRINGE",
)
SUPPLY_FAMILIES = (
    "DISPOSABLE_SYRINGE",
    "INJECTION_NEEDLE",
    "ANGIO_CATHETER",
)
FEATURE_COLUMNS = [
    "year_month",
    "stock_item_key",
    "lag_1",
    "rolling_mean_3",
    "target_usage",
    "standard_item_family_id",
    "standard_item_subtype_id",
    "commodity_risk",
    "material_return_30d",
    "material_volatility_30d",
    "material_price_up_shock",
    "material_price_down_shock",
    "commodity_volatility_shock",
]
COMMODITY_SIGNAL_COLUMNS = [
    "commodity_risk",
    "material_return_30d",
    "material_volatility_30d",
    "material_price_up_shock",
    "material_price_down_shock",
    "commodity_volatility_shock",
]


def wape(actual: pd.Series, prediction: pd.Series) -> float:
    denominator = float(actual.abs().sum())
    if denominator == 0:
        return float("nan")
    return float((actual - prediction).abs().sum() / denominator * 100.0)


def bias_pct(actual: pd.Series, prediction: pd.Series) -> float:
    denominator = float(actual.abs().sum())
    if denominator == 0:
        return float("nan")
    return float((prediction - actual).sum() / denominator * 100.0)


def _evaluation_identity() -> pd.DataFrame:
    table = pd.read_parquet(
        FEATURE_TABLE_PATH,
        columns=FEATURE_COLUMNS,
        filters=[
            ("year_month", "<=", pd.Timestamp("2025-06")),
            ("target_usage", ">=", 0),
            ("lag_1", ">=", 0),
        ],
    )
    table = table[table["rolling_mean_3"].notna()].copy()
    frames: list[pd.DataFrame] = []
    for fold in VALIDATION_FOLDS:
        valid = table[
            table["year_month"].between(
                pd.Timestamp(fold["valid_start"]),
                pd.Timestamp(fold["valid_end"]),
            )
        ].copy()
        valid.insert(0, "row_id", np.arange(len(valid), dtype="int64"))
        valid.insert(0, "fold", str(fold["fold"]))
        frames.append(valid)
    return pd.concat(frames, ignore_index=True)


def _paired_predictions() -> pd.DataFrame:
    all_predictions = pd.read_parquet(PREDICTION_PATH)
    metadata = all_predictions[all_predictions["model"].eq(BASELINE_MODEL)][
        [
            "fold",
            "row_id",
            "year_month",
            "actual",
            "external_signal_connected",
            "strong_shock",
            "shock_score",
        ]
    ]
    predictions = all_predictions[
        all_predictions["model"].isin([BASELINE_MODEL, COMMODITY_MODEL])
    ]
    wide = predictions.pivot(
        index=["fold", "row_id", "year_month", "actual"],
        columns="model",
        values="prediction",
    ).reset_index().merge(
        metadata,
        on=["fold", "row_id", "year_month", "actual"],
        validate="one_to_one",
    )
    identity = _evaluation_identity()
    merged = identity.merge(
        wide,
        on=["fold", "row_id"],
        how="inner",
        validate="one_to_one",
        suffixes=("_feature", "_prediction"),
    )
    if len(merged) != len(identity):
        raise ValueError(
            f"Prediction identity mismatch: identity={len(identity)} merged={len(merged)}"
        )
    feature_month = pd.to_datetime(merged["year_month_feature"])
    prediction_month = pd.to_datetime(merged["year_month_prediction"])
    if not feature_month.equals(prediction_month):
        raise ValueError("Feature and prediction months do not align")
    if not np.allclose(
        merged["target_usage"].to_numpy(dtype="float64"),
        merged["actual"].to_numpy(dtype="float64"),
    ):
        raise ValueError("Feature target and prediction actual do not align")
    merged["year_month"] = feature_month
    merged["commodity_signal_connected"] = (
        merged[COMMODITY_SIGNAL_COLUMNS].fillna(0).abs().max(axis=1).gt(0)
    )
    merged["strong_commodity_shock"] = (
        merged[
            [
                "material_price_up_shock",
                "material_price_down_shock",
                "commodity_volatility_shock",
            ]
        ]
        .fillna(0)
        .max(axis=1)
        .ge(0.8)
    )
    return merged


def _bootstrap_delta(block: pd.DataFrame, draws: int = 500) -> dict[str, float]:
    monthly = block.assign(
        actual_abs=block["actual"].abs(),
        baseline_error=(block["actual"] - block[BASELINE_MODEL]).abs(),
        commodity_error=(block["actual"] - block[COMMODITY_MODEL]).abs(),
    ).groupby("year_month", sort=True).agg(
        actual_abs=("actual_abs", "sum"),
        baseline_error=("baseline_error", "sum"),
        commodity_error=("commodity_error", "sum"),
    )
    if monthly.empty or float(monthly["actual_abs"].sum()) == 0:
        return {
            "delta_wape_ci95_low": float("nan"),
            "delta_wape_ci95_high": float("nan"),
            "improvement_probability": float("nan"),
        }
    rng = np.random.default_rng(RANDOM_STATE)
    values = monthly.to_numpy(dtype="float64")
    deltas: list[float] = []
    for _ in range(draws):
        selected = values[rng.integers(0, len(values), len(values))]
        denominator = selected[:, 0].sum()
        deltas.append(float((selected[:, 2].sum() - selected[:, 1].sum()) / denominator * 100.0))
    return {
        "delta_wape_ci95_low": float(np.quantile(deltas, 0.025)),
        "delta_wape_ci95_high": float(np.quantile(deltas, 0.975)),
        "improvement_probability": float(np.mean(np.asarray(deltas) < 0.0)),
    }


def _within_item_association(block: pd.DataFrame) -> dict[str, object]:
    risk = block["commodity_risk"].fillna(0).astype("float64")
    actual = block["actual"].astype("float64")
    risk_within = risk - risk.groupby(block["stock_item_key"]).transform("mean")
    actual_within = actual - actual.groupby(block["stock_item_key"]).transform("mean")
    if float(risk_within.std()) > 0 and float(actual_within.std()) > 0:
        within_corr = float(risk_within.corr(actual_within))
    else:
        within_corr = float("nan")

    item_state = block.groupby(
        ["stock_item_key", "commodity_signal_connected"], sort=False
    )["actual"].mean().unstack()
    if False in item_state.columns and True in item_state.columns:
        paired = item_state.dropna(subset=[False, True])
        differences = paired[True] - paired[False]
    else:
        paired = item_state.iloc[0:0]
        differences = pd.Series(dtype="float64")
    return {
        "within_item_risk_actual_corr": within_corr,
        "items_with_both_signal_states": int(len(paired)),
        "mean_item_demand_difference_signal_minus_no_signal": (
            float(differences.mean()) if not differences.empty else float("nan")
        ),
        "median_item_demand_difference_signal_minus_no_signal": (
            float(differences.median()) if not differences.empty else float("nan")
        ),
    }

def _metric_row(family: str, cohort: str, block: pd.DataFrame) -> dict[str, object]:
    baseline_wape = wape(block["actual"], block[BASELINE_MODEL])
    commodity_wape = wape(block["actual"], block[COMMODITY_MODEL])
    prediction_delta = block[COMMODITY_MODEL] - block[BASELINE_MODEL]
    return {
        "family": family,
        "cohort": cohort,
        "rows": int(len(block)),
        "months": int(block["year_month"].nunique()),
        "actual_sum": float(block["actual"].sum()),
        "actual_mean": float(block["actual"].mean()),
        "commodity_signal_rows": int(block["commodity_signal_connected"].sum()),
        "commodity_signal_row_pct": float(block["commodity_signal_connected"].mean() * 100.0),
        "strong_commodity_shock_rows": int(block["strong_commodity_shock"].sum()),
        "strong_external_shock_rows": int(block["strong_shock"].sum()),
        "mean_commodity_risk": float(block["commodity_risk"].fillna(0).mean()),
        "baseline_wape": baseline_wape,
        "commodity_wape": commodity_wape,
        "delta_wape_vs_usage_only": commodity_wape - baseline_wape,
        "baseline_bias_pct": bias_pct(block["actual"], block[BASELINE_MODEL]),
        "commodity_bias_pct": bias_pct(block["actual"], block[COMMODITY_MODEL]),
        "prediction_delta_sum": float(prediction_delta.sum()),
        "prediction_delta_mean": float(prediction_delta.mean()),
        "row_abs_error_improvement_pct": float(
            (
                (block["actual"] - block[COMMODITY_MODEL]).abs()
                < (block["actual"] - block[BASELINE_MODEL]).abs()
            ).mean()
            * 100.0
        ),
        **_within_item_association(block),
        **_bootstrap_delta(block),
    }


def _mapping_summary() -> list[dict[str, object]]:
    mapping = pd.read_csv(MAPPING_PATH)
    selected = mapping[
        mapping["item_type"].isin(SUPPLY_FAMILIES)
        & mapping["review_status"].eq("approved")
    ]
    if selected.empty:
        return []
    grouped = selected.groupby(
        ["item_type", "raw_material_meta_code", "related_material"],
        dropna=False,
        sort=True,
    )
    rows = grouped.agg(
        approved_mapping_rows=("stock_item_key", "size"),
        approved_stock_items=("stock_item_key", "nunique"),
        mean_mapping_weight=("mapping_weight", "mean"),
        mean_exposure_score=("exposure_score", "mean"),
    ).reset_index()
    return rows.to_dict(orient="records")


def _json_safe(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(float(value)) else float(value)
    if isinstance(value, np.integer):
        return int(value)
    return value

def run_analysis() -> dict[str, object]:
    paired = _paired_predictions()
    selected = paired[paired["standard_item_family_id"].isin(TARGET_FAMILIES)].copy()
    rows: list[dict[str, object]] = []
    family_blocks = [(family, selected[selected["standard_item_family_id"].eq(family)]) for family in TARGET_FAMILIES]
    family_blocks.append(("SYRINGE_SUPPLY_ALL", selected[selected["standard_item_family_id"].isin(SUPPLY_FAMILIES)]))
    for family, family_block in family_blocks:
        if family_block.empty:
            continue
        cohorts = {
            "all": family_block,
            "commodity_connected": family_block[family_block["commodity_signal_connected"]],
            "commodity_unconnected": family_block[~family_block["commodity_signal_connected"]],
            "strong_commodity_shock": family_block[family_block["strong_commodity_shock"]],
        }
        for cohort, block in cohorts.items():
            if not block.empty:
                rows.append(_metric_row(family, cohort, block))
    report = pd.DataFrame(rows)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(REPORT_PATH, index=False)
    mapping_summary = _mapping_summary()
    syringe_all = report[
        report["family"].eq("DISPOSABLE_SYRINGE") & report["cohort"].eq("all")
    ].iloc[0].to_dict()
    summary: dict[str, object] = {
        "status": "complete",
        "analysis_contract": "post-hoc descriptive subgroup audit; not causal and not a model-selection test",
        "evaluation_months": ["2024-07", "2025-06"],
        "baseline_model": BASELINE_MODEL,
        "commodity_model": COMMODITY_MODEL,
        "approved_mapping_summary": mapping_summary,
        "candidate_only_not_approved": {
            "INJECTION_NEEDLE": ["STAINLESS_STEEL", "POLYPROPYLENE_PP"],
            "ANGIO_CATHETER": ["STAINLESS_STEEL", "POLYURETHANE_PU", "POLYPROPYLENE_PP"],
            "warning": "품목군 후보 규칙이며 승인된 로컬 품목-원자재 연결이 아니다",
        },
        "signal_coverage": {
            "all_external_connected_rows": int(paired["external_signal_connected"].sum()),
            "disposable_syringe_external_connected_rows": int(
                paired[
                    paired["standard_item_family_id"].eq("DISPOSABLE_SYRINGE")
                    & paired["external_signal_connected"]
                ].shape[0]
            ),
            "disposable_syringe_share_of_all_connected_pct": float(
                paired[
                    paired["standard_item_family_id"].eq("DISPOSABLE_SYRINGE")
                    & paired["external_signal_connected"]
                ].shape[0]
                / paired["external_signal_connected"].sum()
                * 100.0
            ),
        },
        "disposable_syringe_all": syringe_all,
        "interpretation_guardrails": [
            "원자재 가격·변동성은 주사기 사용수요보다 조달가격·공급가용성에 먼저 작용할 수 있다",
            "주사침·카테터는 승인 매핑이 없어 0 신호를 연관성 부재로 해석하면 안 된다",
            "평가 후 하위집단 분석이므로 유의확률을 최종 모형 선택에 사용하지 않는다",
        ],
        "report_path": str(REPORT_PATH.relative_to(ROOT)).replace("\\", "/"),
    }
    summary = _json_safe(summary)
    SUMMARY_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


if __name__ == "__main__":
    print(json.dumps(run_analysis(), ensure_ascii=False, indent=2, default=str))
