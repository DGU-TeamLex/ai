"""Run the external-shock rebuild and experiment with a durable status file."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STATUS_PATH = ROOT / "outputs" / "external_shock_pipeline_status.json"


def _write_status(status: str, stage: str, **extra) -> None:
    payload = {
        "status": status,
        "stage": stage,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        **extra,
    }
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _run(module: str, *arguments: str) -> None:
    subprocess.run(
        [sys.executable, "-u", "-m", module, *arguments],
        cwd=ROOT,
        check=True,
    )


def main() -> None:
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        _write_status("running", "feature_engineering", started_at=started)
        _run("src.feature_engineering")
        _write_status("running", "external_shock_experiment", started_at=started)
        _run("src.modeling.external_shock_experiment", "--bootstrap-draws", "500")
        _write_status(
            "complete",
            "complete",
            started_at=started,
            report="outputs/external_shock_experiment_report.csv",
            summary="outputs/external_shock_experiment_summary.json",
        )
    except Exception as error:
        _write_status(
            "failed",
            "failed",
            started_at=started,
            error_type=type(error).__name__,
            error=str(error),
        )
        raise


if __name__ == "__main__":
    main()
