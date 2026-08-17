import unittest

import numpy as np
import pandas as pd

from src.modeling.combination_experiment import (
    _split_months,
    apply_buffer,
    apply_pattern_router,
    build_tsb_hb_predictions,
    fit_pooled_buffer,
    inventory_metrics,
    select_pattern_router,
)


class CombinationExperimentTest(unittest.TestCase):
    def test_split_months_reserves_later_months_for_evaluation(self):
        frame = pd.DataFrame(
            {
                "year_month": pd.to_datetime(
                    ["2025-08-01", "2025-09-01", "2025-10-01"]
                ),
                "value": [1, 2, 3],
            }
        )
        calibration, evaluation, calibration_months, evaluation_months = (
            _split_months(frame, calibration_month_count=2)
        )
        self.assertEqual(calibration["value"].tolist(), [1, 2])
        self.assertEqual(evaluation["value"].tolist(), [3])
        self.assertEqual(calibration_months, ["2025-08", "2025-09"])
        self.assertEqual(evaluation_months, ["2025-10"])

    def test_pattern_router_selects_candidates_from_calibration(self):
        calibration = pd.DataFrame(
            {
                "demand_pattern": ["smooth", "smooth", "lumpy", "lumpy"],
                "actual_usage": [10.0, 12.0, 2.0, 8.0],
                "model_a": [10.0, 11.0, 8.0, 2.0],
                "model_b": [5.0, 5.0, 2.0, 8.0],
            }
        )
        router, fallback = select_pattern_router(
            calibration,
            ["model_a", "model_b"],
        )
        self.assertEqual(router["smooth"], "model_a")
        self.assertEqual(router["lumpy"], "model_b")
        routed = apply_pattern_router(calibration, router, fallback)
        np.testing.assert_allclose(routed, [10.0, 11.0, 2.0, 8.0])

    def test_empirical_buffer_uses_only_fitted_calibration_error(self):
        calibration = pd.DataFrame(
            {
                "demand_pattern": ["smooth"] * 250,
                "actual_usage": np.arange(250, dtype="float64") + 10.0,
                "prediction": np.arange(250, dtype="float64"),
            }
        )
        fitted = fit_pooled_buffer(
            calibration,
            "prediction",
            "empirical_pooled",
            0.90,
        )
        evaluation = pd.DataFrame(
            {
                "demand_pattern": ["smooth"],
                "prediction": [100.0],
                "actual_usage": [10000.0],
            }
        )
        buffer, target = apply_buffer(
            evaluation,
            "prediction",
            "empirical_pooled",
            0.90,
            fitted,
        )
        self.assertEqual(buffer.iloc[0], 10.0)
        self.assertEqual(target.iloc[0], 110.0)

    def test_tsb_prediction_does_not_use_demand_after_origin(self):
        monthly = pd.DataFrame(
            {
                "year_month": pd.to_datetime(
                    [
                        "2025-01-01",
                        "2025-02-01",
                        "2025-03-01",
                        "2025-01-01",
                        "2025-02-01",
                    ]
                ),
                "stock_item_key": ["a", "a", "a", "b", "b"],
                "consumption_qty": [0.0, 2.0, 1000.0, 1.0, 1.0],
            }
        )
        requests = pd.DataFrame(
            {
                "forecast_origin_month": pd.to_datetime(
                    ["2025-02-01", "2025-02-01"]
                ),
                "stock_item_key": ["a", "b"],
                "demand_pattern": ["intermittent", "intermittent"],
                "item_group_id_candidate": ["MED_SUPPLY", "MED_SUPPLY"],
            }
        )
        first = build_tsb_hb_predictions(monthly, requests)
        monthly.loc[
            monthly["year_month"].eq(pd.Timestamp("2025-03-01")),
            "consumption_qty",
        ] = 1_000_000.0
        second = build_tsb_hb_predictions(monthly, requests)
        np.testing.assert_allclose(first, second)
        self.assertTrue(first.ge(0).all())

    def test_inventory_metrics_report_service_and_fill(self):
        metrics = inventory_metrics(
            actual=[10.0, 20.0],
            target=[10.0, 10.0],
            service_level=0.90,
        )
        self.assertEqual(metrics["ROW_SERVICE_RATE"], 50.0)
        self.assertAlmostEqual(metrics["UNIT_FILL_RATE"], 100 * 20 / 30)
        self.assertEqual(metrics["UNDERAGE_SUM"], 10.0)

    def test_current_system_reference_uses_existing_target_stock(self):
        frame = pd.DataFrame(
            {
                "actual_usage": [10.0, 20.0],
                "target_stock": [12.0, 18.0],
                "demand_pattern": ["smooth", "lumpy"],
            }
        )
        buffer, target = apply_buffer(
            frame,
            "current_system_reference",
            "existing_target_stock",
            0.90,
        )
        np.testing.assert_allclose(buffer, [0.0, 0.0])
        np.testing.assert_allclose(target, [12.0, 18.0])


if __name__ == "__main__":
    unittest.main()
