import unittest

import pandas as pd

from src.commodity.commodity_collector import (
    collect_alpha_vantage_prices,
    collect_commodity_prices,
)
from src.commodity.commodity_risk_scorer import build_commodity_risk_outputs
from src.modeling.inventory_policy import add_inventory_recommendations
from src.module_c.config import DEFAULT_MODULE_C_CONFIG, validate_module_c_config
from src.module_c.exposure_candidates import build_module_c_exposure_candidates
from src.module_c.risk_engine import build_module_c_risk_outputs


def module_c_config() -> dict:
    return {
        section: values.copy() if isinstance(values, dict) else values
        for section, values in DEFAULT_MODULE_C_CONFIG.items()
    }


class CommodityCollectorTest(unittest.TestCase):
    def test_disabled_provider_returns_no_market_signal(self):
        result = collect_commodity_prices(provider="disabled")

        self.assertTrue(result.empty)
        self.assertIn("market_factor_id", result.columns)

    def test_alpha_vantage_adapter_returns_standard_market_schema(self):
        registry = pd.DataFrame(
            [
                {
                    "market_factor_id": "BRENT_CRUDE",
                    "provider": "alpha_vantage",
                    "series_id": "BRENT",
                    "interval": "daily",
                    "price_type": "benchmark_spot",
                    "currency": "USD",
                    "unit": "USD_PER_BARREL",
                    "is_direct_factor": True,
                }
            ]
        )

        def request_json(url, params, timeout):
            self.assertEqual(url, "https://www.alphavantage.co/query")
            self.assertEqual(params["function"], "BRENT")
            self.assertEqual(timeout, 60)
            return {
                "data": [
                    {"date": "2025-01-01", "value": "80.0"},
                    {"date": "2025-01-02", "value": "."},
                ]
            }

        result = collect_alpha_vantage_prices(
            registry,
            api_key="test-key",
            request_json=request_json,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["market_factor_id"], "BRENT_CRUDE")
        self.assertEqual(result.iloc[0]["provider"], "alpha_vantage")
        self.assertEqual(float(result.iloc[0]["price"]), 80.0)

    def test_alpha_vantage_adapter_spaces_provider_requests(self):
        registry = pd.DataFrame(
            [
                {
                    "market_factor_id": factor,
                    "provider": "alpha_vantage",
                    "series_id": series,
                    "interval": "daily",
                    "price_type": "benchmark_spot",
                    "currency": "USD",
                    "unit": "USD_PER_BARREL",
                    "is_direct_factor": True,
                }
                for factor, series in [
                    ("BRENT_CRUDE", "BRENT"),
                    ("WTI_CRUDE", "WTI"),
                ]
            ]
        )
        delays = []

        result = collect_alpha_vantage_prices(
            registry,
            api_key="test-key",
            request_json=lambda *_: {
                "data": [{"date": "2025-01-01", "value": "80.0"}]
            },
            request_delay_seconds=1.2,
            sleeper=delays.append,
        )

        self.assertEqual(len(result), 2)
        self.assertEqual(delays, [1.2])


class CommodityPropagationTest(unittest.TestCase):
    def setUp(self):
        self.prices = pd.DataFrame(
            [
                {"date": "2025-01-01", "market_factor_id": "PETROCHEMICAL_NAPHTHA", "price": 100},
                {"date": "2025-02-01", "market_factor_id": "PETROCHEMICAL_NAPHTHA", "price": 101},
                {"date": "2025-03-01", "market_factor_id": "PETROCHEMICAL_NAPHTHA", "price": 135},
                {"date": "2025-04-01", "market_factor_id": "PETROCHEMICAL_NAPHTHA", "price": 136},
            ]
        )
        self.material_market = pd.DataFrame(
            [
                {
                    "raw_material_meta_code": "POLYPROPYLENE_PP",
                    "market_factor_id": "PETROCHEMICAL_NAPHTHA",
                    "transmission_weight": 0.8,
                    "lag_days": 21,
                    "proxy_quality": 1.0,
                    "event_code": "PETROCHEMICAL_NAPHTHA_PRICE_SHOCK",
                    "review_status": "approved",
                    "evidence_reference": "test://naphtha-pp",
                    "mapping_version": "test-v1",
                }
            ]
        )

    def stock_mapping(self, review_status: str = "approved") -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "stock_item_key": "INST::DEPT::SYRINGE",
                    "related_material": "PP",
                    "raw_material_meta_code": "POLYPROPYLENE_PP",
                    "mapping_weight": 1.0,
                    "mapping_confidence": "high",
                    "exposure_score": 1.0,
                    "review_status": review_status,
                    "mapping_version": "test-item-v1",
                    "evidence_reference": "test://item-pp",
                }
            ]
        )

    def test_naphtha_shock_propagates_to_approved_pp_item(self):
        scores, audit = build_commodity_risk_outputs(
            prices=self.prices,
            mapping=self.stock_mapping(),
            material_market_mapping=self.material_market,
            config=module_c_config(),
        )
        march = scores[scores["STD_YYYYMM"].eq("2025-03")].iloc[0]

        self.assertGreater(march["commodity_risk"], 0.5)
        self.assertIn("PETROCHEMICAL_NAPHTHA", march["market_factor_ids"])
        self.assertIn("PETROCHEMICAL_NAPHTHA_PRICE_SHOCK", march["market_event_codes"])
        self.assertTrue((audit["raw_material_meta_code"] == "POLYPROPYLENE_PP").all())

    def test_unapproved_item_material_mapping_never_propagates(self):
        scores, audit = build_commodity_risk_outputs(
            prices=self.prices,
            mapping=self.stock_mapping("candidate"),
            material_market_mapping=self.material_market,
            config=module_c_config(),
        )

        self.assertTrue(scores.empty)
        self.assertTrue(audit.empty)


class ModuleCRiskEngineTest(unittest.TestCase):
    def test_demand_and_supply_mapping_gates_are_independent(self):
        news = pd.DataFrame(
            [
                {
                    "STD_YYYYMM": "2025-03",
                    "stock_item_key": "INST::DEPT::ITEM",
                    "disease_news_risk": 0.9,
                    "supply_news_risk": 0.8,
                    "material_news_risk": 0.4,
                    "news_signal_confidence": 0.8,
                    "has_approved_material_mapping": True,
                    "has_approved_demand_mapping": False,
                    "news_event_codes": "MIDEAST_NAPHTHA_PETROCHEM_SHOCK",
                }
            ]
        )
        market = pd.DataFrame(
            [
                {
                    "STD_YYYYMM": "2025-03",
                    "stock_item_key": "INST::DEPT::ITEM",
                    "commodity_risk": 0.7,
                    "market_signal_confidence": 0.9,
                    "market_factor_count": 1,
                    "market_event_codes": "PETROCHEMICAL_NAPHTHA_PRICE_SHOCK",
                }
            ]
        )

        scores, audit, alerts = build_module_c_risk_outputs(
            news, market, module_c_config()
        )
        row = scores.iloc[0]

        self.assertEqual(row["module_c_demand_risk"], 0.0)
        self.assertAlmostEqual(row["module_c_supply_risk"], 0.685)
        self.assertEqual(row["module_c_event_supply_risk_level"], "warning")
        self.assertEqual(row["module_c_trade_contribution"], 0.0)
        self.assertTrue(row["module_c_has_approved_material_mapping"])
        self.assertFalse(row["module_c_has_approved_demand_mapping"])
        self.assertIn(
            "MIDEAST_NAPHTHA_PETROCHEM_SHOCK",
            row["module_c_event_codes"],
        )
        self.assertIn(
            "PETROCHEMICAL_NAPHTHA_PRICE_SHOCK",
            row["module_c_event_codes"],
        )
        self.assertEqual(len(audit), 5)
        self.assertEqual(alerts.iloc[0]["top_driver"], "supply")

    def test_approved_trade_path_adds_bounded_supply_overlay(self):
        market = pd.DataFrame(
            [
                {
                    "STD_YYYYMM": "2025-03",
                    "stock_item_key": "INST::DEPT::ITEM",
                    "commodity_risk": 0.8,
                    "market_signal_confidence": 0.8,
                    "market_factor_count": 1,
                    "market_event_codes": "PETROCHEMICAL_NAPHTHA_PRICE_SHOCK",
                }
            ]
        )
        trade = pd.DataFrame(
            [
                {
                    "STD_YYYYMM": "2025-03",
                    "stock_item_key": "INST::DEPT::ITEM",
                    "trade_risk": 0.8,
                    "trade_signal_confidence": 0.9,
                    "trade_factor_count": 1,
                    "trade_event_codes": "HS_IMPORT_VOLUME_DROP",
                }
            ]
        )

        scores, audit, _ = build_module_c_risk_outputs(
            pd.DataFrame(),
            market,
            module_c_config(),
            trade_scores=trade,
        )
        row = scores.iloc[0]

        self.assertAlmostEqual(row["module_c_trade_contribution"], 0.144)
        self.assertAlmostEqual(row["module_c_supply_risk"], 0.424)
        self.assertTrue(row["module_c_has_approved_trade_mapping"])
        self.assertIn("HS_IMPORT_VOLUME_DROP", row["module_c_event_codes"])
        trade_audit = audit[audit["signal_type"].eq("import_export")].iloc[0]
        self.assertTrue(trade_audit["mapping_approved"])
        self.assertAlmostEqual(trade_audit["weighted_contribution"], 0.144)

    def test_approved_disease_mapping_enables_demand_signal(self):
        news = pd.DataFrame(
            [
                {
                    "STD_YYYYMM": "2025-03",
                    "stock_item_key": "INST::DEPT::ITEM",
                    "disease_news_risk": 0.8,
                    "has_approved_demand_mapping": True,
                }
            ]
        )
        scores, _, _ = build_module_c_risk_outputs(
            news, pd.DataFrame(), module_c_config()
        )
        self.assertEqual(scores.iloc[0]["module_c_demand_risk"], 0.8)
        self.assertEqual(scores.iloc[0]["module_c_supply_risk"], 0.0)


class ModuleCInventoryPolicyTest(unittest.TestCase):
    def test_module_c_adjusts_demand_lead_time_and_safety_stock(self):
        source = pd.DataFrame(
            [
                {
                    "predicted_usage": 100.0,
                    "current_stock": 40.0,
                    "lead_time_days": 15.0,
                    "review_period_days": 30.0,
                    "module_c_demand_risk": 0.4,
                    "module_c_supply_risk": 0.6,
                    "module_c_market_price_risk": 0.5,
                    "module_c_total_risk": 0.6,
                }
            ]
        )
        result = add_inventory_recommendations(
            source,
            current_stock_col="current_stock",
            lead_time_days_col="lead_time_days",
            review_period_days_col="review_period_days",
            module_c_config=module_c_config(),
        ).iloc[0]

        self.assertEqual(result["base_stock"], 180.0)
        self.assertAlmostEqual(result["risk_adjusted_predicted_usage"], 114.0)
        self.assertAlmostEqual(result["effective_lead_time_days"], 27.9)
        self.assertAlmostEqual(result["dynamic_safety_stock_rate"], 0.35)
        self.assertAlmostEqual(result["risk_buffer"], 112.5)
        self.assertAlmostEqual(result["target_stock"], 292.5)
        self.assertTrue(result["module_c_policy_applied"])

    def test_zero_module_c_signal_keeps_base_stock(self):
        source = pd.DataFrame(
            [
                {
                    "predicted_usage": 100.0,
                    "module_c_demand_risk": 0.0,
                    "module_c_supply_risk": 0.0,
                    "module_c_total_risk": 0.0,
                }
            ]
        )
        result = add_inventory_recommendations(
            source, module_c_config=module_c_config()
        ).iloc[0]
        self.assertEqual(result["base_stock"], 120.0)
        self.assertEqual(result["target_stock"], 120.0)
        self.assertFalse(result["module_c_policy_applied"])

    def test_zero_module_c_signal_has_no_floating_point_residual_buffer(self):
        source = pd.DataFrame(
            [
                {
                    "predicted_usage": 716.056027,
                    "module_c_demand_risk": 0.0,
                    "module_c_supply_risk": 0.0,
                    "module_c_total_risk": 0.0,
                }
            ]
        )
        result = add_inventory_recommendations(
            source, module_c_config=module_c_config()
        ).iloc[0]

        self.assertEqual(result["risk_buffer"], 0.0)
        self.assertEqual(result["target_stock"], result["base_stock"])

    def test_demand_signal_is_not_applied_twice_when_embedded_in_forecast(self):
        source = pd.DataFrame(
            [
                {
                    "predicted_usage": 114.0,
                    "module_c_demand_risk": 0.4,
                    "module_c_supply_risk": 0.0,
                    "module_c_total_risk": 0.4,
                    "external_demand_signal_in_forecast": True,
                }
            ]
        )

        result = add_inventory_recommendations(
            source, module_c_config=module_c_config()
        ).iloc[0]

        self.assertEqual(result["risk_adjusted_predicted_usage"], 114.0)
        self.assertEqual(result["target_stock"], result["base_stock"])
        self.assertTrue(result["module_c_demand_embedded_in_forecast"])
        self.assertFalse(result["module_c_policy_demand_uplift_applied"])


class ModuleCExposureCandidateTest(unittest.TestCase):
    def test_current_classification_candidate_requires_material_review(self):
        classification = pd.DataFrame(
            [
                {
                    "local_item_key": "INST::ITEM1",
                    "institution_code": "INST",
                    "item_code": "ITEM1",
                    "item_family_id": "DISPOSABLE_SYRINGE",
                    "item_subtype_id": "SYRINGE_USAGE_BASED",
                    "normalized_specification": "3mL",
                    "unit_code": "EA",
                    "review_status": "approved",
                }
            ]
        )
        alias = pd.DataFrame(
            [
                {
                    "local_item_key": "INST::ITEM1",
                    "representative_item_id": "REP1",
                }
            ]
        )
        integrated = pd.DataFrame(
            [
                {
                    "representative_item_id": "REP1",
                    "representative_name": "주사기 3cc",
                    "classification_selected_item_family_id": "DISPOSABLE_SYRINGE",
                    "classification_selected_item_subtype_id": "SYRINGE_USAGE_BASED",
                    "raw_material_meta_code": "POLYPROPYLENE_PP",
                    "raw_material_risk_meta_code": "GENERAL_LOW_RISK",
                    "demand_risk_meta_code": "BASELINE_DEMAND",
                    "raw_material_suggested": "PP",
                    "raw_material_evidence": "test evidence",
                    "material_confidence": "identified",
                    "material_evidence_tier": "family_candidate",
                    "material_review_status": "needs_review",
                    "usage_sum": 1000.0,
                    "occurrence_count": 20,
                }
            ]
        )
        market_mapping = pd.DataFrame(
            [
                {
                    "raw_material_meta_code": "POLYPROPYLENE_PP",
                    "market_factor_id": "PETROCHEMICAL_NAPHTHA",
                }
            ]
        )

        candidates, report = build_module_c_exposure_candidates(
            classification=classification,
            alias_map=alias,
            integrated_items=integrated,
            market_mapping=market_mapping,
            official_material_claims=pd.DataFrame(),
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates.iloc[0]["market_factor_count"], 1)
        self.assertEqual(
            candidates.iloc[0]["baseline_supply_risk_level"],
            "NORMAL",
        )
        self.assertFalse(
            candidates.iloc[0]["supply_risk_policy_needs_review"]
        )
        self.assertFalse(candidates.iloc[0]["operational_risk_eligible"])
        self.assertEqual(
            report["blocked_reason"],
            "material_candidates_require_explicit_review_before_inventory_adjustment",
        )

    def test_exact_official_product_material_claim_approves_matching_candidate(self):
        classification = pd.DataFrame(
            [
                {
                    "local_item_key": "INST::ITEM1",
                    "institution_code": "INST",
                    "item_code": "ITEM1",
                    "item_family_id": "MEDICAL_GLOVE",
                    "item_subtype_id": "MEDICAL_GLOVE",
                    "normalized_specification": "",
                    "unit_code": "EA",
                    "review_status": "approved",
                }
            ]
        )
        alias = pd.DataFrame(
            [{"local_item_key": "INST::ITEM1", "representative_item_id": "REP1"}]
        )
        integrated = pd.DataFrame(
            [
                {
                    "representative_item_id": "REP1",
                    "representative_name": "UDI 확인 라텍스 장갑",
                    "classification_selected_item_family_id": "MEDICAL_GLOVE",
                    "classification_selected_item_subtype_id": "MEDICAL_GLOVE",
                    "raw_material_meta_code": "NATURAL_RUBBER_LATEX",
                    "raw_material_risk_meta_code": "GENERAL_LOW_RISK",
                    "demand_risk_meta_code": "BASELINE_DEMAND",
                    "raw_material_suggested": "latex",
                    "raw_material_evidence": "family candidate",
                    "material_confidence": "identified",
                    "material_evidence_tier": "family_candidate",
                    "material_review_status": "needs_review",
                    "usage_sum": 100.0,
                    "occurrence_count": 5,
                }
            ]
        )
        market_mapping = pd.DataFrame(
            [
                {
                    "raw_material_meta_code": "NATURAL_RUBBER_LATEX",
                    "market_factor_id": "NATURAL_RUBBER",
                }
            ]
        )
        claims = pd.DataFrame(
            [
                {
                    "representative_item_id": "REP1",
                    "raw_material_meta_code": "NATURAL_RUBBER_LATEX",
                    "evidence_source": "mfds_device_udi_attributes",
                    "evidence_field": "LATEX_ICLS_YN",
                    "evidence_url": "https://www.data.go.kr/data/15073863/openapi.do",
                    "identity_review_status": "approved",
                    "material_review_status": "approved",
                }
            ]
        )

        candidates, report = build_module_c_exposure_candidates(
            classification=classification,
            alias_map=alias,
            integrated_items=integrated,
            market_mapping=market_mapping,
            official_material_claims=claims,
        )

        self.assertTrue(candidates.iloc[0]["official_material_claim_approved"])
        self.assertEqual(candidates.iloc[0]["material_review_status"], "approved")
        self.assertEqual(
            candidates.iloc[0]["material_evidence_tier"], "official_product_material"
        )
        self.assertTrue(candidates.iloc[0]["operational_risk_eligible"])
        self.assertEqual(report["official_material_claim_approved_count"], 1)


class ModuleCConfigTest(unittest.TestCase):
    def test_invalid_signal_weights_are_rejected(self):
        config = module_c_config()
        config["supply_signal"]["market_price"] = 0.9
        with self.assertRaisesRegex(ValueError, "sum to 1.0"):
            validate_module_c_config(config)


if __name__ == "__main__":
    unittest.main()
