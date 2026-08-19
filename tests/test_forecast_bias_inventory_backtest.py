import unittest

import numpy as np

from scripts.analysis.forecast_bias_inventory_backtest import (
    InventoryPolicy,
    forecast_metrics,
    simulate_periodic_review,
)


class ForecastBiasInventoryBacktestTest(unittest.TestCase):
    def test_forecast_metrics_uses_signed_total_bias(self):
        metrics = forecast_metrics(
            np.array([10.0, 20.0]),
            np.array([9.0, 18.0]),
        )
        self.assertAlmostEqual(metrics["WAPE"], 10.0)
        self.assertAlmostEqual(metrics["BIAS_PCT"], -10.0)

    def test_same_opening_stock_and_one_month_lead_are_respected(self):
        policy = InventoryPolicy(review_days=30.0, lead_days=30.0, service_z=0.0)
        metrics, monthly = simulate_periodic_review(
            prediction=np.array([[10.0, 10.0]]),
            actual=np.array([[5.0, 5.0]]),
            origin_sigma=np.zeros((1, 2)),
            opening_stock=np.array([0.0]),
            policy=policy,
        )
        self.assertAlmostEqual(monthly.loc[0, "arriving_sum"], 0.0)
        self.assertAlmostEqual(monthly.loc[0, "order_sum"], 20.0)
        self.assertAlmostEqual(monthly.loc[0, "unmet_sum"], 5.0)
        self.assertAlmostEqual(monthly.loc[1, "arriving_sum"], 20.0)
        self.assertAlmostEqual(metrics["unmet_demand_sum"], 5.0)

    def test_evaluation_actual_does_not_change_safety_stock(self):
        policy = InventoryPolicy(review_days=30.0, lead_days=30.0, service_z=1.645)
        prediction = np.array([[10.0, 10.0]])
        sigma = np.array([[2.0, 2.0]])
        opening = np.array([100.0])
        _, low = simulate_periodic_review(
            prediction, np.array([[1.0, 1.0]]), sigma, opening, policy
        )
        _, high = simulate_periodic_review(
            prediction, np.array([[50.0, 50.0]]), sigma, opening, policy
        )
        self.assertAlmostEqual(low.loc[0, "order_sum"], high.loc[0, "order_sum"])


if __name__ == "__main__":
    unittest.main()
