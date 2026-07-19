from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
from fastapi import HTTPException

from src.serving.api import (
    get_predictions,
    get_predictions_by_subtype,
    inventory_policy,
    recommend_order,
)
from src.serving.schemas import RecommendOrderRequest


class ServingForecastTest(unittest.TestCase):
    def test_inventory_policy_rederives_level_and_requires_explicit_daily_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stock_predictions.csv"
            pd.DataFrame(
                [
                    {
                        "year_month": "2026-07-01",
                        "institution_code": "INST001",
                        "department": "진료실",
                        "item_code": "ITEM1",
                        "predicted_usage": 60.0,
                        "supply_risk_meta_code": "GENERAL_LOW_RISK",
                        "supply_risk_level": "CRITICAL",
                        "mean_daily_usage": 2.0,
                        "daily_demand_stddev": 3.0,
                        "lead_time_days": 20.0,
                    },
                    {
                        "year_month": "2026-07-01",
                        "institution_code": "INST001",
                        "department": "진료실",
                        "item_code": "ITEM2",
                        "predicted_usage": 10.0,
                    },
                ]
            ).to_csv(path, index=False)

            with patch("src.serving.api.PREDICTION_PATH", path):
                calculated = inventory_policy(standardCode="ITEM1")["content"][0]
                insufficient = inventory_policy(standardCode="ITEM2")["content"][0]

        self.assertEqual(calculated["baselineSupplyRiskLevel"], "NORMAL")
        self.assertEqual(calculated["zUsed"], 1.28)
        self.assertAlmostEqual(calculated["SS"], round(1.28 * 3 * (20**0.5), 2))
        self.assertEqual(calculated["calculationStatus"], "CALCULATED")
        self.assertIsNone(insufficient["ROP"])
        self.assertEqual(
            insufficient["calculationStatus"],
            "INSUFFICIENT_DAILY_VARIANCE_OR_LEAD_TIME",
        )

    def test_future_prediction_serializes_null_actual_and_blocks_stale_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stock_predictions.csv"
            pd.DataFrame(
                [
                    {
                        "year_month": "2026-01-01",
                        "institution_code": "INST001",
                        "department": "내과",
                        "item_code": "ITEM1",
                        "actual_usage": None,
                        "predicted_usage": 10.0,
                        "recommended_stock": 12.0,
                        "is_stale_data": True,
                    }
                ]
            ).to_csv(path, index=False)

            with patch("src.serving.api.PREDICTION_PATH", path):
                response = get_predictions(
                    yyyymm="2026-01",
                    item_code="ITEM1",
                    institution_code="INST001",
                    department="내과",
                )
                with self.assertRaises(HTTPException) as error:
                    recommend_order(
                        RecommendOrderRequest(
                            yyyymm="2026-01",
                            item_code="ITEM1",
                            institution_code="INST001",
                            department="내과",
                            current_stock=3,
                        )
                    )

        self.assertIsNone(response[0]["actual_usage"])
        self.assertEqual(error.exception.status_code, 409)

    def test_classified_prediction_can_be_filtered_by_specification_and_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stock_predictions_by_subtype.csv"
            pd.DataFrame(
                [
                    {
                        "year_month": "2026-07-01",
                        "institution_code": "INST001",
                        "department": "진료실",
                        "item_group_id": "MED_SUPPLY",
                        "item_family_id": "DISPOSABLE_SYRINGE",
                        "item_subtype_id": "SYRINGE_USAGE_BASED",
                        "normalized_specification": "3mL",
                        "unit_code": "EA",
                        "unit_name": "개수",
                        "predicted_usage": 30.0,
                        "current_stock": 13.0,
                        "recommended_stock": 36.0,
                        "recommended_order": 23.0,
                    }
                ]
            ).to_csv(path, index=False)

            with patch("src.serving.api.CLASSIFIED_PREDICTION_PATH", path):
                response = get_predictions_by_subtype(
                    yyyymm="2026-07",
                    institution_code="INST001",
                    department="진료실",
                    item_group_id="MED_SUPPLY",
                    item_family_id="DISPOSABLE_SYRINGE",
                    item_subtype_id="SYRINGE_USAGE_BASED",
                    normalized_specification="3mL",
                    unit_code="EA",
                    limit=50,
                )

        self.assertEqual(response[0]["predicted_usage"], 30.0)
        self.assertEqual(response[0]["unit_name"], "개수")

    def test_order_recommendation_uses_mapping_risks_and_inventory_position(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stock_predictions.csv"
            pd.DataFrame(
                [
                    {
                        "year_month": "2026-07-01",
                        "institution_code": "INST001",
                        "department": "진료실",
                        "item_code": "ITEM1",
                        "predicted_usage": 100.0,
                        "disease_news_risk": 1.0,
                        "supply_news_risk": 0.5,
                        "material_news_risk": 0.2,
                        "commodity_risk": 0.8,
                        "approved_material_mapping_count": 2,
                        "has_approved_material_mapping": True,
                        "is_stale_data": False,
                    }
                ]
            ).to_csv(path, index=False)

            with patch("src.serving.api.PREDICTION_PATH", path):
                response = recommend_order(
                    RecommendOrderRequest(
                        yyyymm="2026-07",
                        item_code="ITEM1",
                        institution_code="INST001",
                        department="진료실",
                        current_stock=40,
                        lead_time_days=15,
                        review_period_days=30,
                        on_order_qty=20,
                        backorder_qty=10,
                    )
                )

        self.assertEqual(response["base_stock"], 180.0)
        self.assertEqual(response["risk_buffer"], 57.0)
        self.assertEqual(response["target_stock"], 237.0)
        self.assertEqual(response["inventory_position"], 50.0)
        self.assertEqual(response["recommended_order"], 187.0)
        self.assertEqual(response["approved_material_mapping_count"], 2)
        self.assertTrue(response["has_approved_material_mapping"])


if __name__ == "__main__":
    unittest.main()
