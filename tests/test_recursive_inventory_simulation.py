import unittest

import numpy as np
import pandas as pd

from src.modeling.recursive_inventory_simulation import (
    advance_feature_state,
    bounded_usage_factors,
    simulate_inventory_transition,
)


class RecursiveInventorySimulationTest(unittest.TestCase):
    def test_bounded_usage_factors_are_reproducible_and_within_ten_percent(self):
        keys = pd.Series(["A", "B", "C", "D"])
        first = bounded_usage_factors(
            keys,
            pd.Timestamp("2026-08-01"),
            seed=42,
            deviation=0.10,
        )
        second = bounded_usage_factors(
            keys,
            pd.Timestamp("2026-08-01"),
            seed=42,
            deviation=0.10,
        )

        np.testing.assert_array_equal(first, second)
        self.assertTrue(np.all(first >= 0.9))
        self.assertTrue(np.all(first <= 1.1))

    def test_inventory_transition_preserves_balance_and_suppresses_order(self):
        policy = pd.DataFrame(
            {
                "predicted_usage": [100.0, 100.0],
                "current_stock": [20.0, 20.0],
                "recommended_order": [160.0, 160.0],
                "order_recommendation_suppressed": [False, True],
            }
        )
        transition = simulate_inventory_transition(
            policy,
            np.array([1.1, 1.1]),
        )

        self.assertEqual(transition.loc[0, "simulated_inbound_qty"], 160.0)
        self.assertTrue(transition.loc[0, "simulation_order_eligible"])
        self.assertTrue(transition.loc[0, "simulation_order_applied"])
        self.assertAlmostEqual(
            transition.loc[0, "predicted_month_end_stock"],
            70.0,
        )
        self.assertEqual(transition.loc[1, "simulated_inbound_qty"], 0.0)
        self.assertFalse(transition.loc[1, "simulation_order_eligible"])
        self.assertFalse(transition.loc[1, "simulation_order_applied"])
        self.assertEqual(transition.loc[1, "simulated_consumption_qty"], 20.0)
        self.assertAlmostEqual(transition.loc[1, "unmet_demand_qty"], 90.0)
        self.assertTrue(
            np.allclose(transition["ledger_balance_residual"], 0.0)
        )

    def test_advance_state_rebuilds_recursive_demand_features(self):
        state = pd.DataFrame(
            {
                "history_months": [12],
                "series_observation_count": [12],
                "lag_1": [12.0],
                "lag_2": [11.0],
                "lag_3": [10.0],
                "lag_6": [7.0],
                "lag_12": [1.0],
                "inbound_qty_lag_1": [3.0],
                "inbound_qty_lag_2": [2.0],
                "inbound_qty_lag_3": [1.0],
                "month_end_stock_lag_1": [30.0],
                "month_end_stock_lag_2": [20.0],
                "month_end_stock_lag_3": [10.0],
                "stockout_rate_lag_1": [0.0],
                "stockout_rate_lag_2": [0.0],
                "stockout_rate_lag_3": [0.0],
                "disposal_qty_lag_1": [0.0],
                "disposal_qty_lag_2": [0.0],
                "disposal_qty_lag_3": [0.0],
                "auto_disposal_adjustment_qty_lag_1": [0.0],
            }
        )
        usage_history = np.arange(1, 13, dtype="float64")[None, :]
        transition = pd.DataFrame(
            {
                "simulated_consumption_qty": [13.0],
                "simulated_inbound_qty": [4.0],
                "predicted_month_end_stock": [21.0],
                "stockout_flag": [False],
            }
        )

        updated, history, cumulative, count = advance_feature_state(
            state,
            usage_history,
            np.array([78.0]),
            np.array([12], dtype="int32"),
            transition,
            pd.Timestamp("2026-01-01"),
        )

        self.assertEqual(updated.loc[0, "lag_1"], 13.0)
        self.assertEqual(updated.loc[0, "lag_12"], 2.0)
        self.assertEqual(updated.loc[0, "rolling_mean_3"], 12.0)
        self.assertEqual(updated.loc[0, "same_month_last_year"], 2.0)
        self.assertEqual(updated.loc[0, "inbound_qty_lag_1"], 4.0)
        self.assertEqual(updated.loc[0, "month_end_stock_lag_1"], 21.0)
        self.assertEqual(updated.loc[0, "forecast_month"], pd.Timestamp("2026-02-01"))
        self.assertEqual(history[0, -1], 13.0)
        self.assertEqual(cumulative[0], 91.0)
        self.assertEqual(count[0], 13)


if __name__ == "__main__":
    unittest.main()
