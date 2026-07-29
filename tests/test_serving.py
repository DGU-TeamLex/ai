import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd
from fastapi import HTTPException
import uvicorn

from src.serving.api import (
    app,
    get_predictions,
    get_predictions_by_subtype,
    inventory_policy,
    recommend_order,
)
from src.serving.schemas import RecommendOrderRequest


def http_json_request(
    base_url: str,
    method: str,
    path: str,
    params: dict[str, str] | None = None,
    json_body: dict | None = None,
) -> tuple[int, object]:
    query = f"?{urlencode(params)}" if params else ""
    body = json.dumps(json_body).encode("utf-8") if json_body is not None else None
    headers = {"Content-Type": "application/json"} if json_body is not None else {}
    request = Request(
        f"{base_url}{path}{query}",
        data=body,
        headers=headers,
        method=method,
    )
    with urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


class ServingForecastTest(unittest.TestCase):
    def test_health_predictions_and_recommend_order_endpoints(self):
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
                        "is_stale_data": False,
                    }
                ]
            ).to_csv(path, index=False)

            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.bind(("127.0.0.1", 0))
            except PermissionError:
                if sock is not None:
                    sock.close()
                self.skipTest("environment policy blocks local TCP integration tests")
            sock.listen(128)
            port = sock.getsockname()[1]
            server = uvicorn.Server(
                uvicorn.Config(app, log_level="critical", lifespan="off")
            )
            thread = threading.Thread(
                target=server.run,
                kwargs={"sockets": [sock]},
                daemon=True,
            )

            with patch("src.serving.api.PREDICTION_PATH", path):
                thread.start()
                try:
                    deadline = time.monotonic() + 5
                    while not server.started and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(server.started, "test API server did not start")
                    base_url = f"http://127.0.0.1:{port}"
                    health_response = http_json_request(base_url, "GET", "/health")
                    prediction_response = http_json_request(
                        base_url,
                        "GET",
                        "/predictions",
                        params={
                            "yyyymm": "2026-07",
                            "item_code": "ITEM1",
                            "institution_code": "INST001",
                            "department": "진료실",
                        },
                    )
                    order_response = http_json_request(
                        base_url,
                        "POST",
                        "/recommend-order",
                        json_body={
                            "yyyymm": "2026-07",
                            "item_code": "ITEM1",
                            "institution_code": "INST001",
                            "department": "진료실",
                            "current_stock": 40,
                        },
                    )
                finally:
                    server.should_exit = True
                    thread.join(timeout=5)
                    sock.close()

        self.assertFalse(thread.is_alive(), "test API server did not stop")

        self.assertEqual(health_response[0], 200)
        self.assertEqual(health_response[1]["status"], "ok")
        self.assertEqual(prediction_response[0], 200)
        self.assertEqual(prediction_response[1][0]["predicted_usage"], 100.0)
        self.assertEqual(order_response[0], 200)
        self.assertEqual(order_response[1]["base_stock"], 180.0)
        self.assertEqual(order_response[1]["recommended_order"], 140.0)

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
                        "current_stock": 8.0,
                    },
                    {
                        "year_month": "2026-07-01",
                        "institution_code": "INST001",
                        "department": "진료실",
                        "item_code": "ITEM2",
                        "predicted_usage": 10.0,
                    },
                    {
                        "year_month": "2026-07-01",
                        "institution_code": "INST001",
                        "department": "진료실",
                        "item_code": "ITEM3",
                        "predicted_usage": 0.0,
                        "mean_daily_usage": 0.0,
                        "daily_demand_stddev": 0.0,
                        "lead_time_days": 0.0,
                        "current_stock": 0.0,
                        "zero_stock_reason": "NOT_OPERATED",
                        "inventory_action": "FILTERED_NOT_OPERATED",
                    },
                    {
                        "year_month": "2026-07-01",
                        "institution_code": "INST001",
                        "department": "진료실",
                        "item_code": "ITEM4",
                        "predicted_usage": 2.0,
                        "mean_daily_usage": 0.2,
                        "daily_demand_stddev": 0.3,
                        "lead_time_days": 0.0,
                        "current_stock": 0.0,
                        "zero_stock_reason": "DATA_MISSING",
                        "inventory_action": "REVIEW_DATA_QUALITY",
                    },
                ]
            ).to_csv(path, index=False)

            with patch("src.serving.api.PREDICTION_PATH", path):
                calculated = inventory_policy(standardCode="ITEM1")["content"][0]
                insufficient = inventory_policy(standardCode="ITEM2")["content"][0]
                not_operated = inventory_policy(standardCode="ITEM3")["content"][0]
                data_missing = inventory_policy(standardCode="ITEM4")["content"][0]

        self.assertEqual(calculated["baselineSupplyRiskLevel"], "NORMAL")
        self.assertEqual(calculated["zUsed"], 1.28)
        self.assertAlmostEqual(calculated["SS"], round(1.28 * 3 * (20**0.5), 2))
        self.assertEqual(calculated["onHand"], 8.0)
        self.assertEqual(calculated["levelBasedInventoryStatus"], "BELOW_ROP")
        self.assertIsNotNone(calculated["levelBasedTargetStock"])
        self.assertIsNotNone(calculated["levelBasedOrderRecommendation"])
        self.assertFalse(calculated["assumedLeadTime"])
        self.assertFalse(calculated["leadTimeFallbackApplied"])
        self.assertFalse(calculated["leadTimeCapApplied"])
        self.assertFalse(calculated["muFloorApplied"])
        self.assertFalse(calculated["sigmaFloorApplied"])
        self.assertEqual(calculated["calculationStatus"], "CALCULATED")
        self.assertIsNone(insufficient["ROP"])
        self.assertEqual(
            insufficient["calculationStatus"],
            "INSUFFICIENT_DAILY_VARIANCE_OR_LEAD_TIME",
        )
        self.assertEqual(not_operated["rawLevelBasedOrderRecommendation"], 0)
        self.assertEqual(not_operated["levelBasedOrderRecommendation"], 0)
        self.assertEqual(not_operated["baseLeadTimeDays"], 15.0)
        self.assertTrue(not_operated["leadTimeFallbackApplied"])
        self.assertEqual(
            not_operated["operationalInventoryStatus"],
            "NOT_OPERATED",
        )
        self.assertIsNone(data_missing["levelBasedOrderRecommendation"])
        self.assertEqual(
            data_missing["operationalInventoryStatus"],
            "DATA_MISSING",
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
