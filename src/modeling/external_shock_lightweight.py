"""강제종료를 피하기 위한 외부위험 경량 체크포인트 실행기.

공정한 외부신호 ablation을 위해 모든 직접 비교 모델에 같은 LightGBM 용량을 적용한다.
운영 champion 재현용이 아니라 자원 제한 탐색 실험이며, 기존 v1 체크포인트는 보존하고
별도 v2 디렉터리를 사용한다.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import external_shock_experiment as experiment
from . import external_shock_resumable as resumable
from . import training


N_JOBS = 1
N_ESTIMATORS = 400
NUM_LEAVES = 31
COOLDOWN_SECONDS = 30
BOOTSTRAP_DRAWS = 500
CHECKPOINT_DIR = resumable.OUTPUT_DIR / "external_shock_experiment_checkpoints_v2"
PROFILE_PATH = CHECKPOINT_DIR / "resource_profile.json"

_ORIGINAL_BUILDER = training._build_estimator


def _limit_lightgbm_resources(_requested_n_jobs: int) -> None:
    """현재 Python 프로세스 안에서만 모델 CPU와 트리 용량을 제한한다."""

    def limited_builder(objective: str):
        estimator, algorithm = _ORIGINAL_BUILDER(objective)
        if hasattr(estimator, "get_params") and hasattr(estimator, "set_params"):
            parameters = estimator.get_params(deep=False)
            overrides = {}
            if "n_jobs" in parameters:
                overrides["n_jobs"] = N_JOBS
            if "n_estimators" in parameters:
                overrides["n_estimators"] = N_ESTIMATORS
            if "num_leaves" in parameters:
                overrides["num_leaves"] = NUM_LEAVES
            if overrides:
                estimator.set_params(**overrides)
        return estimator, algorithm

    training._build_estimator = limited_builder
    experiment._build_estimator = limited_builder


def main() -> None:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    profile = {
        "experiment_role": "resource_bounded_exploratory_ablation",
        "not_production_champion_reproduction": True,
        "n_jobs": N_JOBS,
        "n_estimators": N_ESTIMATORS,
        "num_leaves": NUM_LEAVES,
        "cooldown_seconds": COOLDOWN_SECONDS,
        "bootstrap_draws": BOOTSTRAP_DRAWS,
    }
    PROFILE_PATH.write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # 기존 실행기가 호출하는 limiter만 현재 프로세스에서 교체한다.
    resumable._limit_lightgbm_threads = _limit_lightgbm_resources
    try:
        summary = resumable.run_resumable_experiment(
            include_residual=True,
            bootstrap_draws=BOOTSTRAP_DRAWS,
            n_jobs=N_JOBS,
            cooldown_seconds=COOLDOWN_SECONDS,
            checkpoint_dir=CHECKPOINT_DIR,
        )
    except Exception as error:
        resumable._write_status(
            "failed",
            "failed",
            execution_mode="resource_bounded_exploratory_ablation",
            error_type=type(error).__name__,
            error=str(error),
        )
        raise

    summary["resource_profile"] = profile
    experiment.SUMMARY_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
