"""Forecast-bias and inventory-policy backtest on the full held-out panel.

This experiment compares the already-trained LightGBM L1 and Tweedie forecasts
on a 1 percentage-point blend grid.  It then runs the same periodic-review
inventory policy for every blend with:

* one common opening stock per item;
* a 30-day review period and a 30-day lead time;
* demand uncertainty observed at forecast origin (rolling standard deviation);
* no use of evaluation-period actual demand to estimate safety stock; and
* one-month delayed receipts under a lost-sales assumption.

The held-out test is used for evaluation only.  Any weight that looks best on
the test is reported as a retrospective proposal and is not written to the
active forecast policy.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "outputs" / "stock_backtest_predictions.csv"
DEFAULT_OUTPUT_PREFIX = ROOT / "outputs" / "forecast_bias_inventory_backtest"

L1_COLUMN = "stock_model_a_usage_only_pred"
TWEEDIE_COLUMN = "stock_model_a_usage_tweedie_pred"
TEMPORAL_COLUMN = "temporal_ensemble_pred"
ACTUAL_COLUMN = "actual_usage"

POLICY_VERSION = "forecast-bias-inventory-backtest-v2.0"
DEFAULT_VALIDATION_MONTHS = ("2025-08", "2025-09")
DEFAULT_TEST_MONTHS = ("2025-10", "2025-11", "2025-12")
DEFAULT_TARGET_WAPE = 37.16
DEFAULT_TARGET_BIAS = -3.5
DEFAULT_REVIEW_DAYS = 30.0
DEFAULT_LEAD_DAYS = 30.0
DEFAULT_SERVICE_Z = 1.645
DEFAULT_STOCK_OUTLIER_THRESHOLD = 100_000.0


@dataclass(frozen=True)
class InventoryPolicy:
    review_days: float = DEFAULT_REVIEW_DAYS
    lead_days: float = DEFAULT_LEAD_DAYS
    service_z: float = DEFAULT_SERVICE_Z

    @property
    def protection_months(self) -> float:
        return (self.review_days + self.lead_days) / 30.0

    @property
    def lead_months(self) -> int:
        rounded = int(round(self.lead_days / 30.0))
        if not np.isclose(rounded * 30.0, self.lead_days):
            raise ValueError("This monthly backtest requires lead_days in 30-day units")
        return rounded


def forecast_metrics(actual: np.ndarray, prediction: np.ndarray) -> dict[str, float | int]:
    actual = np.asarray(actual, dtype="float64")
    prediction = np.clip(np.asarray(prediction, dtype="float64"), 0.0, None)
    valid = np.isfinite(actual) & np.isfinite(prediction)
    actual = actual[valid]
    prediction = prediction[valid]
    denominator = max(float(np.abs(actual).sum()), 1e-12)
    error = prediction - actual
    return {
        "N": int(len(actual)),
        "ACTUAL_SUM": float(actual.sum()),
        "PREDICTION_SUM": float(prediction.sum()),
        "WAPE": float(100.0 * np.abs(error).sum() / denominator),
        "BIAS_PCT": float(100.0 * error.sum() / denominator),
        "MAE": float(np.abs(error).mean()) if len(error) else float("nan"),
        "RMSE": float(np.sqrt(np.square(error).mean())) if len(error) else float("nan"),
    }


def simulate_periodic_review(
    prediction: np.ndarray,
    actual: np.ndarray,
    origin_sigma: np.ndarray,
    opening_stock: np.ndarray,
    policy: InventoryPolicy,
) -> tuple[dict[str, float | int], pd.DataFrame]:
    """Run a monthly (R,S) policy with a common opening stock.

    Orders are placed before the month's demand and arrive after ``lead_months``.
    The input sigma is a forecast-origin feature; actual evaluation demand is
    never used to estimate it.
    """
    prediction = np.clip(np.asarray(prediction, dtype="float64"), 0.0, None)
    actual = np.clip(np.asarray(actual, dtype="float64"), 0.0, None)
    sigma = np.clip(np.nan_to_num(origin_sigma, nan=0.0), 0.0, None)
    on_hand = np.clip(np.nan_to_num(opening_stock, nan=0.0), 0.0, None).copy()
    if prediction.shape != actual.shape or prediction.shape != sigma.shape:
        raise ValueError("prediction, actual, and origin_sigma must share a shape")
    if prediction.shape[0] != on_hand.shape[0]:
        raise ValueError("opening_stock length must equal the number of series")

    lead_months = policy.lead_months
    pipeline = [np.zeros_like(on_hand) for _ in range(max(lead_months, 1))]
    monthly_rows: list[dict[str, float | int]] = []
    unmet_total = 0.0
    demand_total = 0.0
    ending_stock_sum = 0.0
    stockout_count = 0
    order_total = 0.0

    for month_index in range(actual.shape[1]):
        arriving = pipeline.pop(0) if lead_months else np.zeros_like(on_hand)
        on_hand += arriving
        target = (
            prediction[:, month_index] * policy.protection_months
            + policy.service_z
            * sigma[:, month_index]
            * np.sqrt(policy.protection_months)
        )
        outstanding = np.sum(pipeline, axis=0) if pipeline else np.zeros_like(on_hand)
        order = np.clip(target - (on_hand + outstanding), 0.0, None)
        if lead_months:
            pipeline.append(order)
        else:
            on_hand += order

        demand = actual[:, month_index]
        served = np.minimum(on_hand, demand)
        unmet = demand - served
        on_hand -= served

        demand_sum = float(demand.sum())
        unmet_sum = float(unmet.sum())
        stockouts = int(np.count_nonzero(unmet > 1e-12))
        row = {
            "month_index": month_index,
            "demand_sum": demand_sum,
            "served_sum": float(served.sum()),
            "unmet_sum": unmet_sum,
            "fill_rate": float(1.0 - unmet_sum / max(demand_sum, 1e-12)),
            "stockout_series": stockouts,
            "stockout_series_rate": float(stockouts / max(len(on_hand), 1)),
            "order_sum": float(order.sum()),
            "arriving_sum": float(arriving.sum()),
            "ending_stock_sum": float(on_hand.sum()),
            "mean_ending_stock": float(on_hand.mean()),
        }
        monthly_rows.append(row)
        unmet_total += unmet_sum
        demand_total += demand_sum
        ending_stock_sum += float(on_hand.sum())
        stockout_count += stockouts
        order_total += float(order.sum())

    ending_pipeline = float(sum(float(x.sum()) for x in pipeline))
    periods = actual.shape[1]
    metrics = {
        "series": int(actual.shape[0]),
        "months": int(periods),
        "fill_rate": float(1.0 - unmet_total / max(demand_total, 1e-12)),
        "unmet_demand_sum": float(unmet_total),
        "stockout_series_month_rate": float(
            stockout_count / max(actual.shape[0] * periods, 1)
        ),
        "mean_ending_stock": float(
            ending_stock_sum / max(actual.shape[0] * periods, 1)
        ),
        "order_sum": float(order_total),
        "ending_pipeline_sum": ending_pipeline,
    }
    return metrics, pd.DataFrame(monthly_rows)


def _month_text(series: pd.Series) -> pd.Series:
    return series.astype(str).str.slice(0, 7)


def _origin_sigma(frame: pd.DataFrame) -> pd.Series:
    ordered = ["rolling_std_6", "rolling_std_3", "rolling_std_12"]
    numeric = frame[ordered].apply(pd.to_numeric, errors="coerce")
    return numeric.bfill(axis=1).iloc[:, 0].fillna(0.0).clip(lower=0.0)


def load_backtest(path: Path) -> pd.DataFrame:
    columns = [
        "year_month",
        "stock_item_key",
        "demand_pattern",
        ACTUAL_COLUMN,
        L1_COLUMN,
        TWEEDIE_COLUMN,
        TEMPORAL_COLUMN,
        "current_stock",
        "rolling_std_3",
        "rolling_std_6",
        "rolling_std_12",
    ]
    frame = pd.read_csv(path, usecols=columns)
    frame["year_month"] = _month_text(frame["year_month"])
    for column in [
        ACTUAL_COLUMN,
        L1_COLUMN,
        TWEEDIE_COLUMN,
        TEMPORAL_COLUMN,
        "current_stock",
    ]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["origin_sigma"] = _origin_sigma(frame)
    return frame


def _metric_grid(
    frame: pd.DataFrame,
    validation_months: tuple[str, ...],
    test_months: tuple[str, ...],
    target_wape: float,
    target_bias: float,
) -> pd.DataFrame:
    actual = frame[ACTUAL_COLUMN].to_numpy(dtype="float64")
    l1 = frame[L1_COLUMN].to_numpy(dtype="float64")
    tweedie = frame[TWEEDIE_COLUMN].to_numpy(dtype="float64")
    months = frame["year_month"]
    validation_mask = months.isin(validation_months).to_numpy()
    test_mask = months.isin(test_months).to_numpy()
    rows: list[dict[str, float | int | str | bool]] = []
    for integer_weight in range(101):
        weight = integer_weight / 100.0
        prediction = (1.0 - weight) * l1 + weight * tweedie
        validation = forecast_metrics(actual[validation_mask], prediction[validation_mask])
        test = forecast_metrics(actual[test_mask], prediction[test_mask])
        rows.append(
            {
                "candidate": f"blend_tweedie_{integer_weight:03d}",
                "tweedie_weight": weight,
                **{f"validation_{key}": value for key, value in validation.items()},
                **{f"test_{key}": value for key, value in test.items()},
                "test_target_distance": float(
                    abs(float(test["WAPE"]) - target_wape)
                    + abs(float(test["BIAS_PCT"]) - target_bias)
                ),
                "is_predeclared_50_50": integer_weight == 50,
            }
        )
    for candidate, column in [("current_temporal_ensemble", TEMPORAL_COLUMN)]:
        prediction = frame[column].to_numpy(dtype="float64")
        validation = forecast_metrics(actual[validation_mask], prediction[validation_mask])
        test = forecast_metrics(actual[test_mask], prediction[test_mask])
        rows.append(
            {
                "candidate": candidate,
                "tweedie_weight": np.nan,
                **{f"validation_{key}": value for key, value in validation.items()},
                **{f"test_{key}": value for key, value in test.items()},
                "test_target_distance": float(
                    abs(float(test["WAPE"]) - target_wape)
                    + abs(float(test["BIAS_PCT"]) - target_bias)
                ),
                "is_predeclared_50_50": False,
            }
        )
    return pd.DataFrame(rows)


def _inventory_panel(
    frame: pd.DataFrame,
    test_months: tuple[str, ...],
) -> tuple[pd.DataFrame, dict[str, np.ndarray], dict[str, int]]:
    test = frame[frame["year_month"].isin(test_months)].copy()
    test = test.drop_duplicates(["stock_item_key", "year_month"], keep="last")
    pivots = {
        column: test.pivot(index="stock_item_key", columns="year_month", values=column)
        .reindex(columns=test_months)
        for column in [
            ACTUAL_COLUMN,
            L1_COLUMN,
            TWEEDIE_COLUMN,
            TEMPORAL_COLUMN,
            "origin_sigma",
            "current_stock",
        ]
    }
    complete = (
        pivots[ACTUAL_COLUMN].notna().all(axis=1)
        & pivots[L1_COLUMN].notna().all(axis=1)
        & pivots[TWEEDIE_COLUMN].notna().all(axis=1)
        & pivots[TEMPORAL_COLUMN].notna().all(axis=1)
        & pivots["current_stock"][test_months[0]].notna()
    )
    index = pivots[ACTUAL_COLUMN].index[complete]
    arrays = {
        key: value.loc[index].to_numpy(dtype="float64")
        for key, value in pivots.items()
    }
    opening = arrays["current_stock"][:, 0]
    quality = np.isfinite(opening) & (opening >= 0.0)
    meta = {
        "test_rows": int(len(test)),
        "test_unique_series": int(test["stock_item_key"].nunique()),
        "complete_common_series": int(len(index)),
        "negative_or_invalid_opening_series": int((~quality).sum()),
    }
    panel = pd.DataFrame({"stock_item_key": index, "quality_nonnegative": quality})
    return panel, arrays, meta


def run_experiment(
    source: Path,
    output_prefix: Path,
    *,
    target_wape: float = DEFAULT_TARGET_WAPE,
    target_bias: float = DEFAULT_TARGET_BIAS,
    stock_outlier_threshold: float = DEFAULT_STOCK_OUTLIER_THRESHOLD,
) -> dict[str, object]:
    frame = load_backtest(source)
    metric_grid = _metric_grid(
        frame,
        DEFAULT_VALIDATION_MONTHS,
        DEFAULT_TEST_MONTHS,
        target_wape,
        target_bias,
    )
    panel, arrays, panel_meta = _inventory_panel(frame, DEFAULT_TEST_MONTHS)
    opening = arrays["current_stock"][:, 0]
    panel["quality_eligible"] = (
        panel["quality_nonnegative"]
        & (opening <= stock_outlier_threshold)
    )
    panel_meta["stock_outlier_series"] = int(
        np.count_nonzero(np.isfinite(opening) & (opening > stock_outlier_threshold))
    )
    panel_meta["quality_eligible_series"] = int(panel["quality_eligible"].sum())

    policy = InventoryPolicy()
    inventory_rows: list[dict[str, object]] = []
    monthly_frames: list[pd.DataFrame] = []
    actual = arrays[ACTUAL_COLUMN]
    l1 = arrays[L1_COLUMN]
    tweedie = arrays[TWEEDIE_COLUMN]
    sigma = arrays["origin_sigma"]

    candidate_specs: list[tuple[str, float | None, np.ndarray]] = [
        (f"blend_tweedie_{weight:03d}", weight / 100.0,
         (1.0 - weight / 100.0) * l1 + weight / 100.0 * tweedie)
        for weight in range(101)
    ]
    candidate_specs.append(("current_temporal_ensemble", None, arrays[TEMPORAL_COLUMN]))

    for candidate, weight, prediction in candidate_specs:
        for population, mask in [
            ("all_common_series", panel["quality_nonnegative"].to_numpy()),
            ("quality_eligible", panel["quality_eligible"].to_numpy()),
        ]:
            metrics, monthly = simulate_periodic_review(
                prediction[mask], actual[mask], sigma[mask], opening[mask], policy
            )
            inventory_rows.append(
                {
                    "candidate": candidate,
                    "tweedie_weight": weight,
                    "population": population,
                    **metrics,
                }
            )
            monthly.insert(0, "population", population)
            monthly.insert(0, "tweedie_weight", weight)
            monthly.insert(0, "candidate", candidate)
            monthly["year_month"] = [
                DEFAULT_TEST_MONTHS[int(index)] for index in monthly["month_index"]
            ]
            monthly_frames.append(monthly)

    inventory = pd.DataFrame(inventory_rows)
    monthly = pd.concat(monthly_frames, ignore_index=True)
    combined = metric_grid.merge(inventory, on=["candidate", "tweedie_weight"], how="left")

    predeclared = combined[
        combined["is_predeclared_50_50"]
        & combined["population"].eq("quality_eligible")
    ].iloc[0]
    retrospective = combined[
        combined["population"].eq("quality_eligible")
        & combined["tweedie_weight"].notna()
    ].sort_values(
        ["test_target_distance", "stockout_series_month_rate", "mean_ending_stock"],
        kind="mergesort",
    ).iloc[0]
    validation_governed_pool = combined[
        combined["population"].eq("quality_eligible")
        & combined["tweedie_weight"].notna()
        & combined["validation_BIAS_PCT"].abs().le(5.0)
    ]
    if validation_governed_pool.empty:
        validation_governed_pool = combined[
            combined["population"].eq("quality_eligible")
            & combined["tweedie_weight"].notna()
        ].assign(validation_abs_bias=lambda x: x["validation_BIAS_PCT"].abs())
        validation_governed = validation_governed_pool.sort_values(
            ["validation_abs_bias", "validation_WAPE"]
        ).iloc[0]
    else:
        validation_governed = validation_governed_pool.sort_values(
            ["validation_WAPE", "validation_BIAS_PCT"],
            key=lambda x: x.abs() if x.name == "validation_BIAS_PCT" else x,
        ).iloc[0]

    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    report_path = output_prefix.with_name(output_prefix.name + "_report.csv")
    grid_path = output_prefix.with_name(output_prefix.name + "_forecast_grid.csv")
    monthly_path = output_prefix.with_name(output_prefix.name + "_monthly.csv")
    summary_path = output_prefix.with_name(output_prefix.name + "_summary.json")
    proposal_path = output_prefix.with_name(output_prefix.name + "_policy_proposal.json")
    combined.to_csv(report_path, index=False, encoding="utf-8-sig")
    metric_grid.to_csv(grid_path, index=False, encoding="utf-8-sig")
    monthly.to_csv(monthly_path, index=False, encoding="utf-8-sig")

    def selected_payload(row: pd.Series) -> dict[str, object]:
        keys = [
            "candidate",
            "tweedie_weight",
            "validation_WAPE",
            "validation_BIAS_PCT",
            "test_WAPE",
            "test_BIAS_PCT",
            "fill_rate",
            "stockout_series_month_rate",
            "mean_ending_stock",
            "order_sum",
        ]
        return {
            key: (None if pd.isna(row[key]) else row[key].item()
                  if hasattr(row[key], "item") else row[key])
            for key in keys
        }

    summary = {
        "version": POLICY_VERSION,
        "status": "completed_not_applied",
        "source": str(source.relative_to(ROOT)),
        "full_rows": int(len(frame)),
        "validation_months": list(DEFAULT_VALIDATION_MONTHS),
        "test_months": list(DEFAULT_TEST_MONTHS),
        "candidate_count": int(len(candidate_specs)),
        "target_reference": {"WAPE": target_wape, "BIAS_PCT": target_bias},
        "inventory_policy": {
            "review_days": policy.review_days,
            "lead_days": policy.lead_days,
            "protection_months": policy.protection_months,
            "service_z": policy.service_z,
            "unmet_demand": "lost_sales",
            "receipt_timing": "one_month_after_order",
            "opening_stock": "same first-test-month current_stock for every candidate",
            "uncertainty": "forecast-origin rolling_std_6, fallback 3 then 12",
            "evaluation_actual_used_for_uncertainty": False,
        },
        "panel": panel_meta,
        "predeclared_balanced_50_50": selected_payload(predeclared),
        "validation_governed_candidate": selected_payload(validation_governed),
        "retrospective_continuity_fit": selected_payload(retrospective),
        "decision": (
            "The 50/50 blend is a challenger proposal only. Keep the active policy "
            "unchanged until a new out-of-sample month confirms the trade-off."
        ),
        "limitations": [
            "Only three test months are available for inventory-policy evaluation.",
            "The retrospective continuity fit uses test outcomes and must not be deployed directly.",
            "Actual replenishment dates, holding costs, shortage costs, expiry, and emergency orders are unavailable.",
            "Lost sales are assumed; backorders may change service metrics.",
            "Opening-stock outliers above the threshold are retained in all_common_series but excluded from quality_eligible.",
        ],
        "outputs": {
            "report": str(report_path.relative_to(ROOT)),
            "forecast_grid": str(grid_path.relative_to(ROOT)),
            "monthly": str(monthly_path.relative_to(ROOT)),
            "summary": str(summary_path.relative_to(ROOT)),
            "policy_proposal": str(proposal_path.relative_to(ROOT)),
        },
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    proposal_path.write_text(
        json.dumps(
            {
                "version": "forecast-balanced-50-50-proposal-v1.0",
                "status": "proposal_not_applied",
                "l1_weight": 0.5,
                "tweedie_weight": 0.5,
                "evidence": summary["predeclared_balanced_50_50"],
                "deployment_gate": "confirm on at least one new out-of-sample month",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--target-wape", type=float, default=DEFAULT_TARGET_WAPE)
    parser.add_argument("--target-bias", type=float, default=DEFAULT_TARGET_BIAS)
    parser.add_argument(
        "--stock-outlier-threshold",
        type=float,
        default=DEFAULT_STOCK_OUTLIER_THRESHOLD,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_experiment(
        args.source,
        args.output_prefix,
        target_wape=args.target_wape,
        target_bias=args.target_bias,
        stock_outlier_threshold=args.stock_outlier_threshold,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
