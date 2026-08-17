import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.modeling.prediction import (
    _apply_pattern_ensemble,
    _apply_temporal_ensemble,
)
from src.modeling.temporal_ensemble_tuning import (
    generate_weight_grid,
    select_validation_weights,
    split_validation_test_months,
)


class TemporalEnsembleTuningTest(unittest.TestCase):
    def test_latest_three_months_are_reserved_for_test(self):
        validation, test = split_validation_test_months(
            [
                "2025-08",
                "2025-09",
                "2025-10",
                "2025-11",
                "2025-12",
            ]
        )

        self.assertEqual(validation, ["2025-08", "2025-09"])
        self.assertEqual(test, ["2025-10", "2025-11", "2025-12"])

    def test_weight_grid_and_selection_use_validation_error(self):
        validation = pd.DataFrame(
            {
                "actual_usage": [3.0, 6.0, 9.0, 12.0],
                "model_a_pred": [0.0, 0.0, 0.0, 0.0],
                "model_b_pred": [4.0, 8.0, 12.0, 16.0],
            }
        )

        selected, candidates = select_validation_weights(
            validation,
            ["model_a_pred", "model_b_pred"],
        )

        self.assertEqual(len(generate_weight_grid(
            ["model_a_pred", "model_b_pred"]
        )), 21)
        self.assertEqual(len(candidates), 21)
        self.assertEqual(
            selected,
            {"model_a_pred": 0.25, "model_b_pred": 0.75},
        )
        self.assertAlmostEqual(candidates.iloc[0]["WAPE"], 0.0)

    def test_prediction_applies_enabled_policy_weights(self):
        frame = pd.DataFrame(
            {
                "model_a_pred": [10.0, 20.0],
                "model_b_pred": [30.0, 40.0],
            }
        )
        policy = {
            "version": "test",
            "apply_to_prediction": True,
            "selected_weights": {
                "model_a_pred": 0.25,
                "model_b_pred": 0.75,
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(policy), encoding="utf-8")
            with patch(
                "src.modeling.prediction."
                "FORECAST_ENSEMBLE_POLICY_PATH",
                path,
            ):
                result, loaded = _apply_temporal_ensemble(frame)

        self.assertEqual(
            result["temporal_ensemble_pred"].tolist(),
            [25.0, 35.0],
        )
        self.assertEqual(loaded["version"], "test")

    def test_prediction_skips_policy_with_unavailable_model(self):
        frame = pd.DataFrame({"model_a_pred": [10.0]})
        policy = {
            "version": "stale-test",
            "apply_to_prediction": True,
            "selected_weights": {
                "model_a_pred": 0.5,
                "skipped_model_pred": 0.5,
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(json.dumps(policy), encoding="utf-8")
            with patch(
                "src.modeling.prediction."
                "FORECAST_ENSEMBLE_POLICY_PATH",
                path,
            ):
                result, loaded = _apply_temporal_ensemble(frame)

        self.assertIsNone(loaded)
        self.assertNotIn("temporal_ensemble_pred", result.columns)
        self.assertEqual(result["model_a_pred"].tolist(), [10.0])

    def test_pattern_router_uses_segment_weights(self):
        frame = pd.DataFrame(
            {
                "demand_pattern": ["smooth", "all_zero"],
                "model_a_pred": [10.0, 10.0],
                "model_b_pred": [30.0, 30.0],
                "temporal_ensemble_pred": [20.0, 20.0],
            }
        )
        policy = {
            "selected_strategy": "pattern_weight_router",
            "pattern_weights": {
                "smooth": {
                    "model_a_pred": 0.25,
                    "model_b_pred": 0.75,
                },
                "all_zero": {
                    "model_a_pred": 1.0,
                    "model_b_pred": 0.0,
                },
            },
        }

        result = _apply_pattern_ensemble(frame, policy)

        self.assertEqual(
            result["temporal_ensemble_pred"].tolist(),
            [25.0, 10.0],
        )


if __name__ == "__main__":
    unittest.main()
