import json
import unittest
from pathlib import Path

import pandas as pd

from scripts.analysis.syringe_supply_risk_inventory_impact import (
    approved_pp_direct_mappings,
    pp_supply_risk,
    prepare_supply_scenario,
    summarize_supply_scenario,
)


ROOT = Path(__file__).resolve().parents[1]


def _input_frame(keys: list[str]) -> pd.DataFrame:
    size = len(keys)
    return pd.DataFrame(
        {
            "standard_item_family_id": ["DISPOSABLE_SYRINGE"] * size,
            "has_approved_material_mapping": [True] * size,
            "year_month": ["2025-08-01"] * size,
            "stock_item_key": keys,
            "predicted_usage": [30.0] * size,
            "current_stock": [0.0] * size,
            "on_order_qty": [0.0] * size,
            "backorder_qty": [0.0] * size,
            "review_period_days": [30.0] * size,
            "rolling_std_3": [5.0] * size,
            "rolling_std_6": [5.0] * size,
            "rolling_std_12": [5.0] * size,
            "module_c_market_price_risk": [0.45] * size,
        }
    )


def _mapping(
    keys: list[str],
    *,
    materials: list[str] | None = None,
    relations: list[str] | None = None,
    statuses: list[str] | None = None,
) -> pd.DataFrame:
    size = len(keys)
    return pd.DataFrame(
        {
            "stock_item_key": keys,
            "raw_material_meta_code": materials or ["POLYPROPYLENE_PP"] * size,
            "relation_type": relations or ["direct_component"] * size,
            "review_status": statuses or ["approved"] * size,
        }
    )


def _status(
    keys: list[str],
    demand_classes: list[str | None] | None = None,
    zero_reasons: list[str] | None = None,
) -> pd.DataFrame:
    size = len(keys)
    return pd.DataFrame(
        {
            "stock_item_key": keys,
            "demand_class": demand_classes or [None] * size,
            "zero_stock_reason": zero_reasons or ["IN_STOCK"] * size,
        }
    )


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

    def test_mapping_filter_requires_approved_pp_direct_component(self):
        keys = ["included", "non-pp", "pending", "indirect"]
        mapping = _mapping(
            keys,
            materials=[
                "POLYPROPYLENE_PP",
                "STAINLESS_STEEL",
                "POLYPROPYLENE_PP",
                "POLYPROPYLENE_PP",
            ],
            relations=[
                "direct_component",
                "direct_component",
                "direct_component",
                "packaging",
            ],
            statuses=["approved", "approved", "pending", "approved"],
        )
        selected, audit = approved_pp_direct_mappings(mapping)
        self.assertEqual(selected["stock_item_key"].tolist(), ["included"])
        self.assertEqual(audit["approved_pp_direct_rows"], 1)

        result = prepare_supply_scenario(
            _input_frame(keys), self.config, mapping, _status(keys)
        )
        self.assertEqual(result["stock_item_key"].tolist(), ["included"])

    def test_supply_risk_changes_shadow_not_demand_forecast(self):
        keys = ["syringe-1"]
        result = prepare_supply_scenario(
            _input_frame(keys), self.config, _mapping(keys), _status(keys)
        )
        row = result.iloc[0]
        self.assertEqual(row["predicted_usage"], 30.0)
        self.assertEqual(row["risk_adjusted_predicted_usage"], 30.0)
        self.assertFalse(row["external_demand_signal_in_forecast"])
        self.assertEqual(row["module_c_policy_demand_risk"], 0.0)
        self.assertEqual(row["lead_time_days"], 30.0)
        self.assertGreater(row["shadow_effective_lead_time_days"], 30.0)
        self.assertGreater(
            row["shadow_risk_adjusted_safety_stock"], row["safety_stock"]
        )
        self.assertGreater(row["shadow_risk_target_stock"], row["base_stock"])
        self.assertFalse(row["module_c_operational_adjustment_enabled"])

        summary = summarize_supply_scenario(result, self.config)
        self.assertFalse(summary["policy"]["operational_adjustment_enabled"])
        self.assertFalse(summary["policy"]["external_demand_signal_in_forecast"])
        self.assertEqual(summary["scope"]["base_lead_time_days"], 30.0)

    def test_baseline_and_shadow_use_identical_order_quality_gates(self):
        keys = ["normal", "dormant", "not-operated", "stale"]
        result = prepare_supply_scenario(
            _input_frame(keys),
            self.config,
            _mapping(keys),
            _status(
                keys,
                demand_classes=[None, "DORMANT", None, None],
                zero_reasons=[
                    "IN_STOCK",
                    "IN_STOCK",
                    "NOT_OPERATED",
                    "STALE_OR_MISSING_OBSERVATION",
                ],
            ),
        ).set_index("stock_item_key")

        self.assertGreater(result.loc["normal", "baseline_recommended_order"], 0)
        self.assertGreater(result.loc["normal", "shadow_recommended_order"], 0)
        for key in ["dormant", "not-operated"]:
            self.assertEqual(result.loc[key, "baseline_recommended_order"], 0.0)
            self.assertEqual(result.loc[key, "shadow_recommended_order"], 0.0)
        self.assertTrue(pd.isna(result.loc["stale", "baseline_recommended_order"]))
        self.assertTrue(pd.isna(result.loc["stale", "shadow_recommended_order"]))
        self.assertEqual(
            result["order_recommendation_suppression_reason"].tolist(),
            result["shadow_order_recommendation_suppression_reason"].tolist(),
        )


if __name__ == "__main__":
    unittest.main()
