import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from src.module_c.config import DEFAULT_MODULE_C_CONFIG
from src.trade.trade_weight_calibration import (
    COMPONENTS,
    WEIGHT_FLOOR,
    _normalize_weights,
    build_policy_comparison_table,
    calibrate_trade_thresholds,
    distribution_weights,
    split_calibration_months,
    validate_collection_completeness,
)


def module_c_config() -> dict:
    return {
        section: values.copy() if isinstance(values, dict) else values
        for section, values in DEFAULT_MODULE_C_CONFIG.items()
    }


class TradeWeightCalibrationTest(unittest.TestCase):
    def setUp(self):
        rows = 24
        self.features = pd.DataFrame(
            {
                "import_volume_yoy_change": np.linspace(-0.8, 0.2, rows),
                "net_import_volume_yoy_change": np.linspace(-0.7, 0.3, rows),
                "zero_import_streak_months": [0, 1, 2, 3] * 6,
                "import_unit_value_yoy_change": np.linspace(-0.1, 0.9, rows),
                "import_volume_rolling_cv": np.linspace(0.1, 0.9, rows),
                "import_unit_value_rolling_volatility": np.linspace(
                    0.05,
                    0.45,
                    rows,
                ),
                "country_top1_share": np.linspace(0.3, 0.8, rows),
                "country_hhi": np.linspace(0.2, 0.7, rows),
                "supplier_count_yoy_change": np.linspace(-0.7, 0.2, rows),
                "export_volume_yoy_change": np.linspace(-0.2, 1.0, rows),
                "country_import_coverage": np.linspace(0.78, 1.0, rows),
                "import_volume_decline_risk": np.linspace(0, 1, rows),
                "net_import_availability_decline_risk": np.linspace(
                    0.1,
                    0.9,
                    rows,
                ),
                "import_interruption_risk": [0, 0.5, 1, 1] * 6,
                "import_unit_value_increase_risk": np.linspace(0.2, 1, rows),
                "import_volume_volatility_risk": np.linspace(0, 0.8, rows),
                "import_unit_value_volatility_risk": np.linspace(
                    0.1,
                    0.7,
                    rows,
                ),
                "country_concentration_risk": np.linspace(0, 0.6, rows),
                "supplier_count_decline_risk": np.linspace(0.2, 0.9, rows),
                "net_import_exposure_risk": np.linspace(0.5, 1, rows),
                "export_volume_surge_risk": np.linspace(0, 1, rows),
            }
        )

    def test_normalized_weights_sum_to_one_and_respect_floor(self):
        weights = _normalize_weights(
            {key: index for index, key in enumerate(COMPONENTS)}
        )

        self.assertAlmostEqual(sum(weights.values()), 1.0)
        self.assertTrue(all(value >= WEIGHT_FLOOR for value in weights.values()))

    def test_normalized_weights_preserve_already_valid_policy(self):
        policy = module_c_config()["trade_signal"]
        expected = {key: float(policy[key]) for key in COMPONENTS}

        weights = _normalize_weights(expected)

        self.assertEqual(weights, expected)

    def test_thresholds_are_calibrated_within_contract(self):
        policy = module_c_config()["trade_signal"]
        thresholds = calibrate_trade_thresholds(self.features, policy)

        self.assertGreater(thresholds["import_volume_decline_threshold"], 0)
        self.assertLessEqual(thresholds["import_volume_decline_threshold"], 1)
        self.assertGreaterEqual(
            thresholds["import_interruption_streak_months"],
            1,
        )
        self.assertTrue(0.75 <= thresholds["country_coverage_min"] <= 0.95)

    def test_distribution_weights_return_component_diagnostics(self):
        policy = module_c_config()["trade_signal"]
        current_weights = {key: float(policy[key]) for key in COMPONENTS}

        weights, diagnostics = distribution_weights(
            self.features,
            current_weights,
            coverage_min=0.80,
        )

        self.assertAlmostEqual(sum(weights.values()), 1.0)
        self.assertEqual(set(weights), set(COMPONENTS))
        self.assertEqual(set(diagnostics), set(COMPONENTS))

    def test_policy_comparison_contains_all_tunable_trade_values(self):
        policy = module_c_config()["trade_signal"]
        weights = {key: float(policy[key]) for key in COMPONENTS}
        thresholds = {
            key: value
            for key, value in policy.items()
            if key.endswith("_threshold")
            or key
            in {
                "import_interruption_streak_months",
                "country_coverage_min",
            }
        }
        report = {
            "previous_trade_policy": {
                **weights,
                **thresholds,
                "module_c_overlay_weight": policy[
                    "module_c_overlay_weight"
                ],
            },
            "final_weights": weights,
            "candidate_weights": {"test": weights},
            "selected_candidate": "test",
            "component_diagnostics": {
                key: {
                    "availability_rate": 1.0,
                    "mean_absolute_component_correlation": 0.1,
                    "risk_standard_deviation": 0.2,
                }
                for key in COMPONENTS
            },
            "calibrated_thresholds": thresholds,
            "calibrated_module_c_overlay_weight": 0.2,
        }

        result = build_policy_comparison_table(report)

        self.assertEqual(
            len(result),
            len(COMPONENTS) + len(thresholds) + 1,
        )
        self.assertEqual(
            result["parameter"].nunique(),
            len(result),
        )

    def test_collection_completeness_requires_every_country_hs_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scope_path = root / "scope.csv"
            cache_path = root / "country.csv"
            state_path = root / "kcs_trade_collection_state.json"
            pd.DataFrame(
                [
                    {
                        "country_code": code,
                        "country_name": code,
                        "scope_role": "test",
                        "review_status": "approved",
                        "evidence_reference": "test",
                        "scope_version": "test",
                    }
                    for code in ["CN", "US"]
                ]
            ).to_csv(scope_path, index=False)
            state_path.write_text(
                json.dumps(
                    {
                        "completed_total_hs_codes": ["3902100000"],
                        "completed_country_hs_pairs": [
                            "CN:3902100000",
                            "US:3902100000",
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "src.trade.trade_weight_calibration."
                "load_trade_country_scope",
                return_value=["CN", "US"],
            ):
                result = validate_collection_completeness(
                    ["3902100000"],
                    country_cache_path=cache_path,
                )

        self.assertTrue(result["is_complete"])
        self.assertEqual(result["expected_country_hs_pair_count"], 2)

    def test_temporal_split_keeps_latest_months_for_validation_and_test(self):
        months = [f"2024-{month:02d}" for month in range(1, 13)]

        train, validation, test = split_calibration_months(months)

        self.assertEqual(
            validation,
            {"2024-07", "2024-08", "2024-09"},
        )
        self.assertEqual(test, {"2024-10", "2024-11", "2024-12"})
        self.assertEqual(max(train), "2024-06")


if __name__ == "__main__":
    unittest.main()
