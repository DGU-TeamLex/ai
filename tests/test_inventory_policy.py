import unittest

import pandas as pd

from src.modeling.inventory_policy import add_inventory_recommendations


class InventoryPolicyTest(unittest.TestCase):
    def test_forecast_uncertainty_replaces_fixed_rate_safety_stock(self):
        source = pd.DataFrame(
            [
                {
                    "predicted_usage": 100.0,
                    "rolling_std_6": 30.0,
                    "lead_time_days": 15.0,
                    "review_period_days": 30.0,
                }
            ]
        )

        result = add_inventory_recommendations(
            source,
            lead_time_days_col="lead_time_days",
            review_period_days_col="review_period_days",
        ).iloc[0]

        self.assertEqual(result["demand_uncertainty_source"], "rolling_std_6")
        self.assertAlmostEqual(result["inventory_critical_ratio"], 0.90)
        self.assertAlmostEqual(result["inventory_service_z"], 1.2815515655)
        self.assertAlmostEqual(
            result["safety_stock"],
            1.2815515655 * 30.0 * (1.5**0.5),
        )

    def test_higher_shortage_cost_increases_optimal_stock(self):
        source = pd.DataFrame(
            [{"predicted_usage": 100.0, "rolling_std_6": 30.0}]
        )
        baseline = {
            "version": "test",
            "costs": {
                "overage_cost_per_excess_unit": 1.0,
                "underage_cost_per_unfilled_unit": 1.0,
                "supply_risk_underage_multiplier_max": 0.0,
            },
            "uncertainty": {
                "preferred_columns": ["rolling_std_6"],
                "fallback_safety_stock_rate": 0.2,
                "supply_uncertainty_multiplier_max": 0.0,
                "minimum_critical_ratio": 0.5,
                "maximum_critical_ratio": 0.995,
            },
        }
        shortage_heavy = {
            **baseline,
            "costs": {
                **baseline["costs"],
                "underage_cost_per_unfilled_unit": 9.0,
            },
        }

        low = add_inventory_recommendations(
            source,
            inventory_optimization_policy=baseline,
        ).iloc[0]
        high = add_inventory_recommendations(
            source,
            inventory_optimization_policy=shortage_heavy,
        ).iloc[0]

        self.assertGreater(high["inventory_critical_ratio"], low["inventory_critical_ratio"])
        self.assertGreater(high["target_stock"], low["target_stock"])

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

        self.assertAlmostEqual(result["base_stock"], 180.0)
        self.assertAlmostEqual(result["risk_buffer"], 0.0)
        self.assertAlmostEqual(result["target_stock"], 180.0)
        self.assertAlmostEqual(result["recommended_order"], 155.0)
        self.assertTrue(result["lead_time_fallback_applied"])

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

        self.assertAlmostEqual(result["risk_buffer"], 75.0)
        self.assertAlmostEqual(result["target_stock"], 255.0)

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
        self.assertEqual(result["target_stock"], 180.0)

    def test_dormant_and_data_quality_rows_are_not_auto_ordered(self):
        source = pd.DataFrame(
            [
                {
                    "predicted_usage": 100.0,
                    "current_stock": 0.0,
                    "demand_class": "DORMANT",
                    "zero_stock_reason": "NOT_OPERATED",
                },
                {
                    "predicted_usage": 100.0,
                    "current_stock": 0.0,
                    "demand_class": "",
                    "zero_stock_reason": "DATA_MISSING",
                },
            ]
        )

        result = add_inventory_recommendations(
            source,
            current_stock_col="current_stock",
        )

        self.assertGreater(result.iloc[0]["raw_recommended_order"], 0)
        self.assertEqual(result.iloc[0]["recommended_order"], 0)
        self.assertEqual(
            result.iloc[0]["order_recommendation_suppression_reason"],
            "NOT_OPERATED",
        )
        self.assertTrue(pd.isna(result.iloc[1]["recommended_order"]))
        self.assertEqual(
            result.iloc[1]["order_recommendation_suppression_reason"],
            "DATA_MISSING",
        )

    def test_continuous_policy_caps_raw_lead_time(self):
        source = pd.DataFrame(
            [
                {
                    "predicted_usage": 30.0,
                    "lead_time_days": 547.5,
                }
            ]
        )

        result = add_inventory_recommendations(
            source,
            lead_time_days_col="lead_time_days",
        ).iloc[0]

        self.assertEqual(result["raw_lead_time_days"], 547.5)
        self.assertEqual(result["lead_time_days"], 120.0)
        self.assertTrue(result["lead_time_cap_applied"])
        self.assertFalse(result["lead_time_fallback_applied"])


if __name__ == "__main__":
    unittest.main()
