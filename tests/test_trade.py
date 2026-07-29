import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.module_c.config import DEFAULT_MODULE_C_CONFIG
from src.modeling.inventory_policy import add_inventory_recommendations
from src.trade.hsk_reference import SOURCE_TO_NORMALIZED, load_hsk_reference
from src.trade.trade_inventory_impact import build_trade_inventory_impact
from src.trade.trade_collector import (
    KCS_COUNTRY_ENDPOINT,
    KCS_TOTAL_ENDPOINT,
    collect_trade_flows,
    collect_kcs_trade_totals,
    load_trade_country_scope,
    parse_kcs_trade_xml,
)
from src.trade.trade_risk_scorer import (
    build_hs_trade_features,
    build_trade_risk_outputs,
    load_material_hs_mapping,
)


def module_c_config() -> dict:
    return {
        section: values.copy() if isinstance(values, dict) else values
        for section, values in DEFAULT_MODULE_C_CONFIG.items()
    }


class HskReferenceTest(unittest.TestCase):
    def test_hsk_loader_preserves_leaf_and_hierarchy_codes(self):
        rows = []
        for code, name in [
            ("3902100000", "폴리프로필렌"),
            ("01029090", "기타"),
        ]:
            row = {column: "" for column in SOURCE_TO_NORMALIZED}
            row.update(
                {
                    "HS부호": code,
                    "적용시작일자": "2026-01-01",
                    "적용종료일자": "2026-12-31",
                    "한글품목명": name,
                    "영문품목명": "Polypropylene" if len(code) == 10 else "Other",
                }
            )
            rows.append(row)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hsk.xlsx"
            pd.DataFrame(rows).to_excel(path, index=False)
            result = load_hsk_reference(path)

        leaf = result[result["hs_code"].eq("3902100000")].iloc[0]
        hierarchy = result[result["hs_code"].eq("01029090")].iloc[0]
        self.assertTrue(leaf["is_trade_leaf"])
        self.assertFalse(hierarchy["is_trade_leaf"])
        self.assertEqual(hierarchy["hs_code"], "01029090")

    def test_approved_mapping_must_match_official_hsk_name(self):
        reference = pd.DataFrame(
            [
                {
                    "hs_code": "3902100000",
                    "item_name_ko": "폴리프로필렌",
                    "is_trade_leaf": True,
                }
            ]
        )
        mapping = pd.DataFrame(
            [
                {
                    "raw_material_meta_code": "POLYPROPYLENE_PP",
                    "hs_code": "3902100000",
                    "hs_item_name_ko": "잘못된 명칭",
                    "relation_type": "direct_raw_material",
                    "mapping_weight": 1.0,
                    "proxy_quality": 1.0,
                    "review_status": "approved",
                    "evidence_reference": "test",
                    "valid_from": "2026-01-01",
                    "valid_to": "2026-12-31",
                    "mapping_version": "test",
                }
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.csv"
            mapping.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "do not match"):
                load_material_hs_mapping(path, reference)


class KcsTradeCollectorTest(unittest.TestCase):
    def test_country_scope_loads_only_approved_unique_codes(self):
        scope = pd.DataFrame(
            [
                {
                    "country_code": "cn",
                    "country_name": "중국",
                    "scope_role": "observed",
                    "review_status": "approved",
                    "evidence_reference": "test",
                    "scope_version": "v1",
                },
                {
                    "country_code": "IN",
                    "country_name": "인도",
                    "scope_role": "candidate",
                    "review_status": "needs_review",
                    "evidence_reference": "test",
                    "scope_version": "v1",
                },
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scope.csv"
            scope.to_csv(path, index=False)
            result = load_trade_country_scope(path)

        self.assertEqual(result, ["CN"])

    def test_xml_adapter_returns_normalized_monthly_trade(self):
        payload = b"""<?xml version="1.0" encoding="UTF-8"?>
        <response><header><resultCode>00</resultCode><resultMsg>OK</resultMsg></header>
        <body><items><item>
          <year>2025.01</year><hsCode>3902100000</hsCode>
          <expWgt>10</expWgt><expDlr>20</expDlr>
          <impWgt>100</impWgt><impDlr>200</impDlr>
          <balPayments>-180</balPayments>
        </item></items></body></response>"""

        result = parse_kcs_trade_xml(payload)

        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["STD_YYYYMM"], "2025-01")
        self.assertEqual(result.iloc[0]["hs_code"], "3902100000")
        self.assertEqual(result.iloc[0]["country_code"], "ALL")
        self.assertEqual(result.iloc[0]["import_value_usd"], 200)

    def test_collector_splits_requests_into_at_most_twelve_months(self):
        requests = []

        def request_xml(url, params, timeout):
            requests.append((url, params, timeout))
            return b"<response><header><resultCode>00</resultCode></header><body/></response>"

        result = collect_kcs_trade_totals(
            ["3902100000"],
            "2023-01",
            "2025-02",
            "test-key",
            request_xml=request_xml,
        )

        self.assertTrue(result.empty)
        self.assertEqual(len(requests), 3)
        self.assertTrue(all(request[0] == KCS_TOTAL_ENDPOINT for request in requests))
        self.assertEqual(requests[0][1]["strtYymm"], "202301")
        self.assertEqual(requests[0][1]["endYymm"], "202312")

    def test_collection_stops_before_exceeding_request_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {
                "TRADE_START_MONTH": "2023-01",
                "TRADE_END_MONTH": "2025-12",
                "TRADE_COUNTRY_CODES": "CN,IN",
                "TRADE_MAX_REQUESTS": "1",
                "DATA_GO_KR_SERVICE_KEY": "test-key",
            }
            with patch.dict("os.environ", env, clear=False):
                with self.assertRaisesRegex(RuntimeError, "budget exceeded"):
                    collect_trade_flows(
                        ["3902100000"],
                        provider="kcs",
                        total_cache_path=Path(directory) / "total.csv",
                        country_cache_path=Path(directory) / "country.csv",
                        refresh=True,
                        request_xml=lambda *_: b"",
                    )

    def test_incremental_collection_requests_only_missing_country(self):
        total = pd.DataFrame(
            [
                {
                    "STD_YYYYMM": "2025-01",
                    "hs_code": "3902100000",
                    "country_code": "ALL",
                    "export_weight_kg": 0,
                    "export_value_usd": 0,
                    "import_weight_kg": 100,
                    "import_value_usd": 100,
                }
            ]
        )
        country = total.assign(country_code="CN")
        requests = []

        def request_xml(url, params, timeout):
            requests.append((url, params, timeout))
            return b"<response><header><resultCode>00</resultCode></header><body/></response>"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            total_path = root / "total.csv"
            country_path = root / "country.csv"
            total.to_csv(total_path, index=False)
            country.to_csv(country_path, index=False)
            env = {
                "TRADE_START_MONTH": "2025-01",
                "TRADE_END_MONTH": "2025-01",
                "TRADE_COUNTRY_CODES": "CN,IN",
                "TRADE_MAX_REQUESTS": "10",
                "DATA_GO_KR_SERVICE_KEY": "test-key",
            }
            with patch.dict("os.environ", env, clear=False):
                collect_trade_flows(
                    ["3902100000"],
                    provider="kcs",
                    total_cache_path=total_path,
                    country_cache_path=country_path,
                    state_path=root / "state.json",
                    refresh=True,
                    request_xml=request_xml,
                )

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0][0], KCS_COUNTRY_ENDPOINT)
        self.assertEqual(requests[0][1]["cntyCd"], "IN")


class TradeRiskScorerTest(unittest.TestCase):
    def setUp(self):
        self.totals = pd.DataFrame(
            [
                {
                    "STD_YYYYMM": "2024-01",
                    "hs_code": "3902100000",
                    "country_code": "ALL",
                    "export_weight_kg": 0,
                    "export_value_usd": 0,
                    "import_weight_kg": 100,
                    "import_value_usd": 100,
                },
                {
                    "STD_YYYYMM": "2025-01",
                    "hs_code": "3902100000",
                    "country_code": "ALL",
                    "export_weight_kg": 0,
                    "export_value_usd": 0,
                    "import_weight_kg": 50,
                    "import_value_usd": 100,
                },
            ]
        )
        self.countries = pd.DataFrame(
            [
                {
                    "STD_YYYYMM": "2025-01",
                    "hs_code": "3902100000",
                    "country_code": "CN",
                    "country_name": "중국",
                    "export_weight_kg": 0,
                    "export_value_usd": 0,
                    "import_weight_kg": 45,
                    "import_value_usd": 90,
                },
                {
                    "STD_YYYYMM": "2025-01",
                    "hs_code": "3902100000",
                    "country_code": "MY",
                    "country_name": "말레이시아",
                    "export_weight_kg": 0,
                    "export_value_usd": 0,
                    "import_weight_kg": 5,
                    "import_value_usd": 10,
                },
            ]
        )

    def test_trade_features_detect_volume_price_and_concentration_risk(self):
        result = build_hs_trade_features(
            self.totals,
            self.countries,
            module_c_config(),
        )
        row = result[result["STD_YYYYMM"].eq("2025-01")].iloc[0]

        self.assertEqual(row["import_volume_decline_risk"], 1.0)
        self.assertEqual(row["import_unit_value_increase_risk"], 1.0)
        self.assertGreater(row["country_concentration_risk"], 0.6)
        self.assertEqual(row["net_import_exposure_risk"], 1.0)
        self.assertAlmostEqual(row["hs_trade_risk"], 0.58)
        self.assertAlmostEqual(row["trade_signal_confidence"], 0.70)

    def test_trade_features_detect_net_inflow_supplier_and_export_risk(self):
        totals = pd.DataFrame(
            [
                {
                    "STD_YYYYMM": "2024-01",
                    "hs_code": "3902100000",
                    "country_code": "ALL",
                    "export_weight_kg": 10,
                    "export_value_usd": 10,
                    "import_weight_kg": 100,
                    "import_value_usd": 100,
                },
                {
                    "STD_YYYYMM": "2025-01",
                    "hs_code": "3902100000",
                    "country_code": "ALL",
                    "export_weight_kg": 30,
                    "export_value_usd": 30,
                    "import_weight_kg": 80,
                    "import_value_usd": 100,
                },
            ]
        )
        countries = pd.DataFrame(
            [
                {
                    "STD_YYYYMM": "2024-01",
                    "hs_code": "3902100000",
                    "country_code": country,
                    "import_weight_kg": value,
                    "import_value_usd": value,
                    "export_weight_kg": 0,
                    "export_value_usd": 0,
                }
                for country, value in [("CN", 40), ("US", 30), ("JP", 30)]
            ]
            + [
                {
                    "STD_YYYYMM": "2025-01",
                    "hs_code": "3902100000",
                    "country_code": "CN",
                    "import_weight_kg": 80,
                    "import_value_usd": 100,
                    "export_weight_kg": 0,
                    "export_value_usd": 0,
                }
            ]
        )

        result = build_hs_trade_features(totals, countries, module_c_config())
        row = result[result["STD_YYYYMM"].eq("2025-01")].iloc[0]

        self.assertEqual(row["net_import_availability_decline_risk"], 1.0)
        self.assertEqual(row["supplier_count_decline_risk"], 1.0)
        self.assertEqual(row["export_volume_surge_risk"], 1.0)
        self.assertIn("HS_IMPORT_SUPPLIER_COUNT_DROP", row["trade_event_codes"])
        self.assertIn("HS_EXPORT_VOLUME_SURGE", row["trade_event_codes"])

    def test_trade_features_detect_import_interruption_and_volatility(self):
        months = pd.period_range("2024-01", "2024-07", freq="M")
        weights = [100, 20, 120, 15, 130, 10, 0]
        totals = pd.DataFrame(
            [
                {
                    "STD_YYYYMM": month.strftime("%Y-%m"),
                    "hs_code": "3902100000",
                    "country_code": "ALL",
                    "export_weight_kg": 0,
                    "export_value_usd": 0,
                    "import_weight_kg": weight,
                    "import_value_usd": weight * 2,
                }
                for month, weight in zip(months, weights)
            ]
        )

        result = build_hs_trade_features(totals, pd.DataFrame(), module_c_config())
        row = result[result["STD_YYYYMM"].eq("2024-07")].iloc[0]

        self.assertEqual(row["zero_import_streak_months"], 1)
        self.assertEqual(row["import_interruption_risk"], 0.5)
        self.assertEqual(row["import_volume_volatility_risk"], 1.0)
        self.assertIn("HS_IMPORT_INTERRUPTION", row["trade_event_codes"])
        self.assertIn("HS_IMPORT_VOLUME_VOLATILITY", row["trade_event_codes"])

    def test_only_approved_stock_and_hs_paths_propagate(self):
        stock = pd.DataFrame(
            [
                {
                    "stock_item_key": "INST::SYRINGE",
                    "raw_material_meta_code": "POLYPROPYLENE_PP",
                    "mapping_weight": 1.0,
                    "mapping_confidence": "verified",
                    "exposure_score": 1.0,
                    "review_status": "approved",
                }
            ]
        )
        material_hs = pd.DataFrame(
            [
                {
                    "raw_material_meta_code": "POLYPROPYLENE_PP",
                    "hs_code": "3902100000",
                    "hs_item_name_ko": "폴리프로필렌",
                    "relation_type": "direct_raw_material",
                    "mapping_weight": 1.0,
                    "proxy_quality": 1.0,
                    "review_status": "approved",
                    "evidence_reference": "test://hsk",
                    "valid_from": "2002-01-01",
                    "valid_to": "2026-12-31",
                    "mapping_version": "test-v1",
                }
            ]
        )

        scores, audit = build_trade_risk_outputs(
            self.totals,
            self.countries,
            stock_mapping=stock,
            material_hs_mapping=material_hs,
            hsk_reference=pd.DataFrame(),
            config=module_c_config(),
        )
        january = scores[scores["STD_YYYYMM"].eq("2025-01")].iloc[0]

        self.assertAlmostEqual(january["trade_risk"], 0.58)
        self.assertEqual(january["trade_hs_codes"], "3902100000")
        self.assertTrue((audit["raw_material_meta_code"] == "POLYPROPYLENE_PP").all())

        stock["review_status"] = "candidate"
        blocked, _ = build_trade_risk_outputs(
            self.totals,
            self.countries,
            stock_mapping=stock,
            material_hs_mapping=material_hs,
            hsk_reference=pd.DataFrame(),
            config=module_c_config(),
        )
        self.assertTrue(blocked.empty)


class TradeInventoryImpactTest(unittest.TestCase):
    def test_trade_counterfactual_increases_target_stock(self):
        source = pd.DataFrame(
            [
                {
                    "stock_item_key": "INST::SYRINGE",
                    "item_name": "주사기",
                    "predicted_usage": 100.0,
                    "current_stock": 0.0,
                    "module_c_demand_risk": 0.0,
                    "module_c_supply_news_risk": 0.0,
                    "module_c_material_news_risk": 0.0,
                    "module_c_market_price_risk": 0.0,
                    "module_c_trade_risk": 0.8,
                    "module_c_supply_risk": 0.2,
                    "module_c_total_risk": 0.2,
                }
            ]
        )
        current = add_inventory_recommendations(
            source,
            prediction_col="predicted_usage",
            current_stock_col="current_stock",
            module_c_config=module_c_config(),
        )

        report, sample = build_trade_inventory_impact(current)

        self.assertEqual(report["trade_exposed_forecast_rows"], 1)
        self.assertEqual(report["target_stock_increased_rows"], 1)
        self.assertGreater(
            sample.iloc[0]["trade_attributable_target_stock"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
