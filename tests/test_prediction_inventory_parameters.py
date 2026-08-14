from pathlib import Path
import tempfile
import unittest

import pandas as pd

from src.modeling.prediction import (
    INVENTORY_STATUS_PREDICTION_COLUMNS,
    attach_current_inventory_status_parameters,
)


class PredictionInventoryParametersTest(unittest.TestCase):
    def test_current_prediction_receives_deterministic_daily_parameters(self):
        prediction = pd.DataFrame(
            [{"stock_item_key": "I::D::A", "predicted_usage": 10.0}]
        )
        status_row = {
            "stock_item_key": "I::D::A",
            "mean_daily_usage": 0.5,
            "daily_demand_stddev": 0.2,
            "raw_mean_daily_usage": 0.5,
            "raw_daily_demand_stddev": 0.2,
            "mu_forecast_3m_92d": 0.7,
            "observation_period_days": 730,
            "zero_stock_reason": "IN_STOCK",
            "inventory_action": "OK",
            "urgent_shortage": False,
            "exact_group_total_stock": 12.0,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory_status.csv"
            pd.DataFrame(
                [status_row],
                columns=INVENTORY_STATUS_PREDICTION_COLUMNS,
            ).to_csv(path, index=False)

            result = attach_current_inventory_status_parameters(
                prediction,
                path=path,
            )

        self.assertEqual(result.iloc[0]["mean_daily_usage"], 0.5)
        self.assertEqual(result.iloc[0]["zero_stock_reason"], "IN_STOCK")
        self.assertTrue(result.iloc[0]["inventory_status_parameters_available"])


if __name__ == "__main__":
    unittest.main()
