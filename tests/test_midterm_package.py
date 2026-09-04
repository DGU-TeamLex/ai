import unittest
from copy import deepcopy

import pandas as pd

from src.module_c.config import DEFAULT_MODULE_C_CONFIG
from src.presentation.midterm_package import (
    PREDICTION_COLUMNS,
    build_decision_evidence,
    build_final_inventory_output,
    build_simple_inventory_output,
    build_weight_selection_output,
    validate_presentation_outputs,
)


def prediction_row(**overrides) -> dict:
    row = {
        "forecast_origin_month": "2025-12-01",
        "year_month": "2026-01-01",
        "institution_code": "A1234",
        "department": "DEPT",
        "item_code": "ITEM1",
        "item_name": "ITEM ONE",
        "stock_item_key": "A1234::DEPT::ITEM1",
        "history_months": 12,
        "demand_pattern": "smooth",
        "normalization_status": "group_candidate",
        "item_group_id_candidate": "MED_SUPPLY",
        "primary_model": "stock_model_a_usage_only_pred",
        "predicted_usage": 100.0,
        "current_stock": 20.0,
        "review_period_days": 30.0,
        "lead_time_days": 15.0,
        "safety_stock": 30.0,
        "base_stock": 180.0,
        "target_stock": 180.0,
        "recommended_order": 160.0,
        "data_age_months": 7,
        "is_stale_data": True,
    }
    row.update(overrides)
    return row


class MidtermPackageTest(unittest.TestCase):
    def setUp(self):
        self.config = deepcopy(DEFAULT_MODULE_C_CONFIG)

    def test_simple_output_checks_formula_and_filters_malformed_institution(self):
        source = pd.DataFrame(
            [
                prediction_row(),
                prediction_row(
                    institution_code="A1;34",
                    stock_item_key="A1;34::DEPT::ITEM2",
                    item_code="ITEM2",
                ),
            ],
            columns=PREDICTION_COLUMNS,
        )

        result = build_simple_inventory_output(source, sample_size=10)

        self.assertEqual(len(result), 1)
        self.assertTrue(result.iloc[0]["base_stock_formula_check"])
        self.assertEqual(result.iloc[0]["order_decision"], "ORDER")
        self.assertEqual(result.iloc[0]["release_status"], "DEMO_ONLY_STALE_INPUT")

    def test_weight_output_applies_weights_and_approval_gates(self):
        result = build_weight_selection_output(self.config)
        blocked = result[
            result["scenario_id"].eq("UNAPPROVED_HIGH_SIGNALS_BLOCKED")
        ].iloc[0]
        supply = result[
            result["scenario_id"].eq("APPROVED_SUPPLY_DISRUPTION")
        ].iloc[0]

        self.assertEqual(blocked["module_c_total_risk"], 0.0)
        self.assertFalse(blocked["module_c_adjustment_enabled"])
        # 가중최대 결합(ai#20 결함 O). APPROVED_SUPPLY_DISRUPTION 은
        # supply_news 0.8 / material_news 0.6 / market_price 0.5 이고
        # 가중 기여가 0.360 / 0.120 / 0.175 이므로 0.360/0.45 = 0.8 이다.
        # 종전 가중합은 셋을 더해 0.655 였다.
        self.assertAlmostEqual(supply["module_c_supply_risk"], 0.8)
        # 가중최대로 바뀌면서 단일 강신호(supply_news 0.8)가 critical(>=0.75)에
        # 닿는다. 공급 차질 시나리오가 critical 인 것은 의미상 맞다. 다만
        # alert_thresholds 는 가중합 척도(실측 최대 0.568)에 맞춰진 값이라
        # 재조정이 필요하다 — ai#20 에 올렸다. 실데이터에서는 critical 이
        # 여전히 0% 라 당장의 운영 영향은 없다.
        self.assertEqual(supply["module_c_risk_level"], "critical")
        self.assertTrue(result["supply_weight_formula_check"].all())
        self.assertFalse(result["operational_use_allowed"].any())

    def test_final_output_keeps_current_base_and_expands_scenarios(self):
        source = pd.DataFrame(
            [
                prediction_row(),
                prediction_row(
                    institution_code="B1234",
                    stock_item_key="B1234::DEPT::ITEM2",
                    item_code="ITEM2",
                    item_name="ITEM TWO",
                    item_group_id_candidate="DISINFECT",
                    current_stock=200.0,
                    recommended_order=0.0,
                ),
            ],
            columns=PREDICTION_COLUMNS,
        )
        simple = build_simple_inventory_output(source, sample_size=10)
        weights = build_weight_selection_output(self.config)

        result = build_final_inventory_output(
            simple,
            weights,
            config=self.config,
            item_count=2,
        )
        current = result[
            result["scenario_id"].eq("CURRENT_OPERATION_NO_APPROVED_RELATION")
        ]
        combined = result[
            result["scenario_id"].eq("APPROVED_COMBINED_CRITICAL")
        ]

        self.assertEqual(len(result), 8)
        self.assertTrue(
            (current["target_stock"] == current["simple_base_stock"]).all()
        )
        self.assertTrue(
            (combined["target_stock"] == combined["simple_base_stock"]).all()
        )
        self.assertTrue(
            (
                combined["shadow_risk_target_stock"]
                > combined["simple_base_stock"]
            ).all()
        )
        self.assertFalse(result["module_c_operational_adjustment_enabled"].any())
        self.assertFalse(result["operational_use_allowed"].any())

    def test_decision_evidence_records_selected_model_and_missing_variant(self):
        manifest = [
            {
                "model": "stock_model_a_usage_only",
                "status": "ready",
                "WAPE": 40.0,
                "selected_on_validation": True,
            }
        ]

        test_report = pd.DataFrame(
            [
                {
                    "model": "stock_model_a_usage_only_pred",
                    "WAPE": 38.0,
                    "BIAS_PCT": -10.0,
                }
            ]
        )
        result = build_decision_evidence(
            manifest,
            self.config,
            test_report=test_report,
        )

        selected = result[result["selected"]]
        self.assertIn("stock_model_a_usage_only", set(selected["candidate"]))
        self.assertEqual(selected.iloc[0]["test_wape_pct"], 38.0)
        module_c = result[result["candidate"].eq("stock_model_d_module_c")].iloc[0]
        self.assertEqual(
            module_c["current_status"],
            "configured_not_in_current_manifest",
        )

    def test_output_validation_rejects_operational_demo_scenario(self):
        source = pd.DataFrame([prediction_row()], columns=PREDICTION_COLUMNS)
        simple = build_simple_inventory_output(source, sample_size=1)
        weights = build_weight_selection_output(self.config)
        final = build_final_inventory_output(
            simple,
            weights,
            config=self.config,
            item_count=1,
        )
        decisions = build_decision_evidence(
            [
                {
                    "model": "stock_model_a_usage_only",
                    "status": "ready",
                    "WAPE": 40.0,
                    "selected_on_validation": True,
                }
            ],
            self.config,
        )
        weights.loc[0, "operational_use_allowed"] = True

        with self.assertRaisesRegex(ValueError, "must not be operationally enabled"):
            validate_presentation_outputs(simple, weights, final, decisions)


if __name__ == "__main__":
    unittest.main()
