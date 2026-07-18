import unittest

import pandas as pd

from src.modeling.inventory_policy import add_inventory_recommendations


class InventoryPolicyTest(unittest.TestCase):
    def test_base_stock_and_three_axis_risk_buffers_are_separate(self):
        source = pd.DataFrame(
            [
                {
                    "predicted_usage": 100.0,
                    "current_stock": 40.0,
                    "lead_time_days": 15,
                    "review_period_days": 30,
                    "on_order_qty": 20.0,
                    "backorder_qty": 10.0,
                    "disease_news_risk": 1.0,
                    "supply_news_risk": 0.5,
                    "material_news_risk": 0.2,
                    "commodity_risk": 0.8,
                }
            ]
        )

        result = add_inventory_recommendations(
            source,
            current_stock_col="current_stock",
            lead_time_days_col="lead_time_days",
            review_period_days_col="review_period_days",
            on_order_qty_col="on_order_qty",
            backorder_qty_col="backorder_qty",
        ).iloc[0]

        self.assertAlmostEqual(result["protection_period_demand"], 150.0)
        self.assertAlmostEqual(result["safety_stock"], 30.0)
        self.assertAlmostEqual(result["base_stock"], 180.0)
        self.assertAlmostEqual(result["demand_risk_buffer"], 30.0)
        self.assertAlmostEqual(result["supply_risk_buffer"], 15.0)
        self.assertAlmostEqual(result["material_risk_buffer"], 12.0)
        self.assertAlmostEqual(result["risk_buffer"], 57.0)
        self.assertAlmostEqual(result["target_stock"], 237.0)
        self.assertAlmostEqual(result["inventory_position"], 50.0)
        self.assertAlmostEqual(result["recommended_order"], 187.0)
        self.assertAlmostEqual(result["external_risk_score"], 0.79)

    def test_missing_risk_columns_default_to_zero(self):
        source = pd.DataFrame([{"predicted_usage": 100.0, "current_stock": 25.0}])

        result = add_inventory_recommendations(
            source,
            current_stock_col="current_stock",
        ).iloc[0]

        self.assertAlmostEqual(result["base_stock"], 120.0)
        self.assertAlmostEqual(result["risk_buffer"], 0.0)
        self.assertAlmostEqual(result["target_stock"], 120.0)
        self.assertAlmostEqual(result["recommended_order"], 95.0)

    def test_total_risk_buffer_is_capped_at_half_of_protection_demand(self):
        source = pd.DataFrame(
            [
                {
                    "predicted_usage": 100.0,
                    "disease_news_risk": 1.0,
                    "supply_news_risk": 1.0,
                    "material_news_risk": 1.0,
                    "commodity_risk": 1.0,
                }
            ]
        )

        result = add_inventory_recommendations(source).iloc[0]

        self.assertAlmostEqual(result["risk_buffer"], 50.0)
        self.assertAlmostEqual(result["target_stock"], 170.0)

    def test_unapproved_mapping_metadata_blocks_nonzero_risk_inputs(self):
        source = pd.DataFrame(
            [
                {
                    "predicted_usage": 100.0,
                    "disease_news_risk": 1.0,
                    "supply_news_risk": 1.0,
                    "material_news_risk": 1.0,
                    "commodity_risk": 1.0,
                    "has_approved_material_mapping": False,
                    "approved_material_mapping_count": 0,
                }
            ]
        )

        result = add_inventory_recommendations(source).iloc[0]

        self.assertEqual(result["external_risk_score"], 0.0)
        self.assertEqual(result["risk_buffer"], 0.0)
        self.assertEqual(result["target_stock"], 120.0)


if __name__ == "__main__":
    unittest.main()
