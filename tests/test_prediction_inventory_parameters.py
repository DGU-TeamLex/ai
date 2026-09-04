from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from src.modeling.prediction import (
    INVENTORY_STATUS_PREDICTION_COLUMNS,
    PREDICTION_REQUIRED_FEATURE_COLUMNS,
    _load_feature_table,
    attach_current_inventory_status_parameters,
)
from src.modeling import prediction as prediction_module


class PredictionInventoryParametersTest(unittest.TestCase):
    @staticmethod
    def _feature_row() -> dict[str, object]:
        row: dict[str, object] = {
            column: 0.0 for column in PREDICTION_REQUIRED_FEATURE_COLUMNS
        }
        row.update(
            {
                "year_month": pd.Timestamp("2025-07-01"),
                "forecast_month": pd.Timestamp("2025-08-01"),
                "institution_code": "I",
                "department": "D",
                "item_code": "A",
                "item_name": "item",
                "stock_item_key": "I::D::A",
                "standard_item_key": "S",
                "standard_item_definition_key": "SD",
                "standard_item_group_id": "G",
                "standard_item_family_id": "F",
                "standard_item_subtype_id": "T",
                "standard_item_specification": "1EA",
                "standard_item_unit_code": "EA",
                "standardization_match_method": "strict",
                "data_period": "current",
            }
        )
        return row

    def test_optional_average_unit_price_may_be_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "features.parquet"
            pd.DataFrame([self._feature_row()]).to_parquet(path, index=False)
            with patch.object(prediction_module, "FEATURE_TABLE_PATH", path):
                result = _load_feature_table([])

        self.assertEqual(len(result), 1)
        self.assertNotIn("average_unit_price", result.columns)

    def test_missing_required_prediction_column_fails_clearly(self):
        row = self._feature_row()
        del row["rolling_mean_3"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "features.parquet"
            pd.DataFrame([row]).to_parquet(path, index=False)
            with patch.object(prediction_module, "FEATURE_TABLE_PATH", path):
                with self.assertRaisesRegex(ValueError, "rolling_mean_3"):
                    _load_feature_table([])

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
