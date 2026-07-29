import unittest

import pandas as pd

from src.module_c.material_inventory_report import build_material_inventory_report


class MaterialInventoryReportTest(unittest.TestCase):
    def test_report_separates_approved_adjustment_from_base_forecast(self):
        predictions = pd.DataFrame(
            [
                {
                    "year_month": "2026-01-01",
                    "stock_item_key": "INST::DEPT::SYRINGE",
                    "local_item_key": "INST::SYRINGE",
                    "item_name": "3mL syringe",
                    "predicted_usage": 100.0,
                    "base_stock": 120.0,
                    "risk_buffer": 6.0,
                    "target_stock": 126.0,
                    "recommended_order": 26.0,
                    "module_c_market_price_risk": 0.2,
                    "module_c_supply_risk": 0.1,
                    "module_c_total_risk": 0.1,
                    "module_c_policy_applied": True,
                    "has_approved_material_mapping": True,
                },
                {
                    "year_month": "2026-01-01",
                    "stock_item_key": "INST::DEPT::OTHER",
                    "local_item_key": "INST::OTHER",
                    "item_name": "other",
                    "predicted_usage": 50.0,
                    "base_stock": 60.0,
                    "risk_buffer": 0.0,
                    "target_stock": 60.0,
                    "recommended_order": 0.0,
                    "module_c_market_price_risk": 0.0,
                    "module_c_supply_risk": 0.0,
                    "module_c_total_risk": 0.0,
                    "module_c_policy_applied": False,
                    "has_approved_material_mapping": False,
                },
            ]
        )
        baseline = predictions.assign(
            risk_buffer=0.0,
            target_stock=[120.0, 60.0],
            recommended_order=[20.0, 0.0],
        )
        validation = pd.DataFrame(
            [
                {
                    "model": "usage_only",
                    "method_type": "machine_learning",
                    "status": "ready",
                    "uses_module_c": False,
                    "WAPE": 40.0,
                    "selected_on_validation": True,
                }
            ]
        )
        approval_audit = pd.DataFrame(
            [
                {
                    "local_item_key": "INST::SYRINGE",
                    "approval_status": "approved",
                    "item_family_id": "DISPOSABLE_SYRINGE",
                    "item_subtype_id": "SYRINGE_USAGE_BASED",
                    "normalized_specification": "3mL",
                    "unit_code": "EA",
                    "approval_rule_id": "SYRINGE_PP",
                }
            ]
        )

        report, detail, by_spec = build_material_inventory_report(
            predictions,
            validation,
            {
                "approved_stock_item_mapping_rows": 1,
                "approved_stock_item_count": 1,
            },
            {},
            approval_audit=approval_audit,
            baseline=baseline,
        )

        self.assertEqual(report["module_c_policy_applied_rows"], 1)
        self.assertEqual(report["unapproved_nonzero_buffer_rows"], 0)
        self.assertEqual(report["risk_buffer_sum"], 6.0)
        self.assertEqual(
            report["baseline_comparison"]["predicted_usage_changed_rows"], 0
        )
        self.assertEqual(len(detail), 1)
        self.assertEqual(detail.iloc[0]["normalized_specification"], "3mL")
        self.assertEqual(by_spec.iloc[0]["risk_buffer_sum"], 6.0)


if __name__ == "__main__":
    unittest.main()
