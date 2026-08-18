import json
import unittest
from pathlib import Path

import pandas as pd

from scripts.analysis.syringe_supply_risk_inventory_impact import (
    pp_supply_risk,
    prepare_supply_scenario,
    summarize_supply_scenario,
)


ROOT = Path(__file__).resolve().parents[1]


class SyringeSupplyRiskInventoryImpactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads(
            (ROOT / "data/mapping/module_c_risk_weights.json").read_text(
                encoding="utf-8"
            )
        )

    def test_market_risk_uses_normalized_supply_weight(self):
        risk = pp_supply_risk(
            pd.Series([0.0, 0.45, 1.0]),
            {"supply_news": 0.45, "material_news": 0.2, "market_price": 0.35},
        )
        self.assertAlmostEqual(risk.iloc[0], 0.0)
        self.assertAlmostEqual(risk.iloc[1], 0.35)
        self.assertAlmostEqual(risk.iloc[2], 0.35 / 0.45)

    def test_supply_risk_changes_shadow_not_operational_policy(self):
        frame = pd.DataFrame(
            {
                "standard_item_family_id": ["DISPOSABLE_SYRINGE"],
                "has_approved_material_mapping": [True],
                "year_month": ["2025-08-01"],
                "stock_item_key": ["syringe-1"],
                "predicted_usage": [30.0],
                "inventory_position": [0.0],
                "review_period_days": [30.0],
                "rolling_std_3": [5.0],
                "rolling_std_6": [5.0],
                "rolling_std_12": [5.0],
                "module_c_market_price_risk": [0.45],
            }
        )
        result = prepare_supply_scenario(frame, self.config)
        row = result.iloc[0]
        self.assertEqual(row["risk_adjusted_predicted_usage"], 30.0)
        self.assertEqual(row["lead_time_days"], 30.0)
        self.assertGreater(row["shadow_effective_lead_time_days"], 30.0)
        self.assertGreater(
            row["shadow_risk_adjusted_safety_stock"], row["safety_stock"]
        )
        self.assertGreater(row["shadow_risk_target_stock"], row["base_stock"])
        self.assertFalse(row["module_c_operational_adjustment_enabled"])

        summary = summarize_supply_scenario(result, self.config)
        self.assertFalse(summary["policy"]["operational_adjustment_enabled"])
        self.assertEqual(summary["scope"]["base_lead_time_days"], 30.0)


if __name__ == "__main__":
    unittest.main()
