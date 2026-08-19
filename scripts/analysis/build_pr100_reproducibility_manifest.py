"""PR #100 핵심 지표와 입력·산출물 지문을 소형 재현 패키지로 만든다."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "outputs"
METRICS_PATH = OUTPUT_DIR / "pr100_reproducibility_metrics.csv"
MANIFEST_PATH = OUTPUT_DIR / "pr100_reproducibility_manifest.json"

FINGERPRINT_FILES = {
    "outputs/stock_backtest_predictions.csv": "local_large_model_input",
    "outputs/stock_inventory_status.csv": "local_large_quality_gate_input",
    "data/mapping/stock_item_material_mapping.csv": "approved_material_mapping",
    "data/mapping/module_c_risk_weights.json": "risk_policy_config",
    "outputs/stock_evaluation_report.csv": "demand_forecast_evidence",
    "outputs/forecast_bias_inventory_backtest_summary.json": "inventory_challenger_evidence",
    "outputs/external_shock_experiment_report.csv": "external_signal_evidence",
    "outputs/external_shock_experiment_summary.json": "external_signal_uncertainty",
    "outputs/syringe_supply_risk_inventory_summary.json": "pp_supply_policy_evidence",
}


def _git_value(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, encoding="utf-8"
    ).strip()


def _fingerprint(relative_path: str, role: str) -> dict:
    path = ROOT / relative_path
    digest = hashlib.sha256()
    newline_count = 0
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
            if path.suffix.lower() == ".csv":
                newline_count += chunk.count(b"\n")

    payload: dict[str, object] = {
        "path": relative_path,
        "role": role,
        "size_bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            payload["schema"] = next(csv.reader(stream))
        payload["rows"] = max(newline_count - 1, 0)
    elif path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        payload["schema"] = sorted(data) if isinstance(data, dict) else ["array"]
    return payload


def _metric_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []

    demand = pd.read_csv(OUTPUT_DIR / "stock_evaluation_report.csv")
    l1 = demand[demand["model"].eq("stock_model_a_usage_only_pred")].iloc[0]
    for metric in ["N", "WAPE", "BIAS_PCT", "RMSE"]:
        rows.append(
            {
                "section": "demand_forecast",
                "role": "champion",
                "model_or_policy": "usage_only_l1",
                "cohort": "all",
                "metric": metric,
                "value": l1[metric],
                "unit": "percent" if metric in {"WAPE", "BIAS_PCT"} else "count_or_units",
                "period": "2025-08~2025-12",
                "status": "research_primary",
                "source": "outputs/stock_evaluation_report.csv",
            }
        )

    challenger_summary = json.loads(
        (OUTPUT_DIR / "forecast_bias_inventory_backtest_summary.json").read_text(
            encoding="utf-8-sig"
        )
    )
    challenger = challenger_summary["fixed_balanced_50_50_candidate"]
    for metric in [
        "test_WAPE",
        "test_BIAS_PCT",
        "fill_rate",
        "stockout_series_month_rate",
        "order_sum",
    ]:
        rows.append(
            {
                "section": "inventory_policy",
                "role": "challenger",
                "model_or_policy": "fixed_l1_50_tweedie_50",
                "cohort": "quality_eligible",
                "metric": metric,
                "value": challenger[metric],
                "unit": "ratio" if metric in {"fill_rate", "stockout_series_month_rate"} else "percent_or_units",
                "period": "2025-10~2025-12",
                "status": "not_applied_reused_evaluation",
                "source": "outputs/forecast_bias_inventory_backtest_summary.json",
            }
        )

    external = pd.read_csv(OUTPUT_DIR / "external_shock_experiment_report.csv")
    for (model, cohort), group in external.groupby(["model", "cohort"], sort=True):
        actual_sum = group["ACTUAL_SUM"].sum()
        n = int(group["N"].sum())
        combined_wape = (
            (group["WAPE"] / 100.0 * group["ACTUAL_SUM"]).sum()
            / actual_sum
            * 100.0
        )
        combined_bias = (
            (group["BIAS_PCT"] / 100.0 * group["ACTUAL_SUM"]).sum()
            / actual_sum
            * 100.0
        )
        for metric, value, unit in [
            ("N", n, "count"),
            ("WAPE", combined_wape, "percent"),
            ("BIAS_PCT", combined_bias, "percent"),
        ]:
            rows.append(
                {
                    "section": "external_signal_ablation",
                    "role": "diagnostic",
                    "model_or_policy": model,
                    "cohort": cohort,
                    "metric": metric,
                    "value": value,
                    "unit": unit,
                    "period": "2024-07~2025-12 folds",
                    "status": "not_applied",
                    "source": "outputs/external_shock_experiment_report.csv",
                }
            )

    pp = json.loads(
        (OUTPUT_DIR / "syringe_supply_risk_inventory_summary.json").read_text(
            encoding="utf-8-sig"
        )
    )
    gated = pp["recommended_order_gated"]
    for metric, value, unit in [
        ("baseline_order_total", gated["baseline_total"], "units"),
        ("shadow_order_total", gated["shadow_total"], "units"),
        ("order_delta_pct", gated["delta_pct"], "percent"),
        ("eligible_rows", gated["eligible_rows"], "count"),
        ("suppressed_to_zero_rows", gated["suppressed_to_zero_rows"], "count"),
        ("review_required_rows", gated["review_required_rows"], "count"),
    ]:
        rows.append(
            {
                "section": "pp_supply_policy",
                "role": "shadow_sensitivity",
                "model_or_policy": "approved_pp_direct_quality_gated",
                "cohort": "disposable_syringe",
                "metric": metric,
                "value": value,
                "unit": unit,
                "period": "2025-08~2025-12",
                "status": "not_applied",
                "source": "outputs/syringe_supply_risk_inventory_summary.json",
            }
        )
    return rows


def main() -> None:
    metrics = pd.DataFrame(_metric_rows())
    metrics.to_csv(METRICS_PATH, index=False, encoding="utf-8-sig")
    manifest = {
        "version": "pr100-reproducibility-v1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "code_commit": _git_value("rev-parse", "HEAD"),
        "branch": _git_value("branch", "--show-current"),
        "contracts": {
            "demand_forecast_champion": "usage-only L1; lowest WAPE on the current research snapshot",
            "inventory_policy_challenger": "fixed 50:50 L1/Tweedie; not applied; confirm on a new month",
            "external_signals": "diagnostic ablation only; not included in operational demand forecast",
            "pp_supply_policy": "approved PP direct-component mappings; common order-quality gate; shadow only",
        },
        "files": [
            _fingerprint(path, role) for path, role in FINGERPRINT_FILES.items()
        ],
        "metrics_file": "outputs/pr100_reproducibility_metrics.csv",
        "path_policy": "project-relative POSIX paths only; no local user absolute paths",
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"metrics_rows": len(metrics), "files": len(manifest["files"])}))


if __name__ == "__main__":
    main()
