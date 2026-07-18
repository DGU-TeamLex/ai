from pathlib import Path
import tempfile
import unittest

import pandas as pd

from src.modeling.classified_prediction import (
    ClassificationValidationError,
    build_classified_prediction_output,
    load_approved_classifications,
)


TAXONOMY_COLUMNS = [
    "item_family_id",
    "item_subtype_id",
    "normalized_specification",
    "unit_code",
    "taxonomy_version",
    "item_group_id",
    "is_forecastable",
    "standard_family_name",
    "standard_subtype_name",
    "unit_name",
    "review_status",
]
CLASSIFICATION_COLUMNS = [
    "institution_code",
    "item_code",
    "local_item_key",
    "item_family_id",
    "item_subtype_id",
    "normalized_specification",
    "unit_code",
    "taxonomy_version",
    "review_status",
    "reviewer",
    "reviewed_at",
    "evidence_reference",
    "classification_version",
]


def taxonomy_row(
    family_id: str,
    subtype_id: str,
    specification: str,
    unit_code: str,
    group_id: str = "MED_SUPPLY",
    forecastable: str = "t",
    review_status: str = "approved",
) -> dict[str, str]:
    return {
        "item_family_id": family_id,
        "item_subtype_id": subtype_id,
        "normalized_specification": specification,
        "unit_code": unit_code,
        "taxonomy_version": "v1.0",
        "item_group_id": group_id,
        "is_forecastable": forecastable,
        "standard_family_name": "주사기" if family_id == "SYRINGE" else "수액세트",
        "standard_subtype_name": "사용량 기준" if family_id == "SYRINGE" else "수액세트",
        "unit_name": "개수" if unit_code == "EA" else "Box",
        "review_status": review_status,
    }


def classification_row(
    item_code: str,
    family_id: str,
    subtype_id: str,
    specification: str,
    unit_code: str,
    review_status: str = "approved",
) -> dict[str, str]:
    return {
        "institution_code": "INST001",
        "item_code": item_code,
        "local_item_key": f"INST001::{item_code}",
        "item_family_id": family_id,
        "item_subtype_id": subtype_id,
        "normalized_specification": specification,
        "unit_code": unit_code,
        "taxonomy_version": "v1.0",
        "review_status": review_status,
        "reviewer": "reviewer-1",
        "reviewed_at": "2026-07-15T09:00:00+09:00",
        "evidence_reference": "TEST-EVIDENCE-001",
        "classification_version": "classification-v1",
    }


def prediction_row(item_code: str, predicted_usage: float, current_stock: float) -> dict:
    return {
        "forecast_origin_month": "2026-06-01",
        "year_month": "2026-07-01",
        "institution_code": "INST001",
        "department": "진료실",
        "item_code": item_code,
        "local_item_key": f"INST001::{item_code}",
        "stock_item_key": f"INST001::진료실::{item_code}",
        "predicted_usage": predicted_usage,
        "protection_period_demand": predicted_usage,
        "review_period_days": 30,
        "lead_time_days": 0,
        "protection_period_days": 30,
        "current_stock": current_stock,
        "safety_stock": predicted_usage * 0.2,
        "base_stock": predicted_usage * 1.2,
        "demand_risk_score": 0.0,
        "supply_risk_score": 0.0,
        "material_risk_score": 0.0,
        "demand_risk_buffer": 0.0,
        "supply_risk_buffer": 0.0,
        "material_risk_buffer": 0.0,
        "risk_buffer": 0.0,
        "target_stock": predicted_usage * 1.2,
        "recommended_stock": predicted_usage * 1.2,
        "on_order_qty": 0.0,
        "backorder_qty": 0.0,
        "inventory_position": current_stock,
        "recommended_order": max(predicted_usage * 1.2 - current_stock, 0.0),
        "history_months": 12,
        "external_risk_score": 0.0,
        "data_age_months": 0,
        "is_stale_data": False,
        "prediction_type": "future",
        "primary_model": "usage_model",
    }


class ClassifiedPredictionTest(unittest.TestCase):
    def test_item_code_may_contain_local_key_separator(self):
        item_code = "6::WA11850831"
        taxonomy = pd.DataFrame(
            [taxonomy_row("SYRINGE", "USAGE", "3mL", "EA")],
            columns=TAXONOMY_COLUMNS,
        )
        classifications = pd.DataFrame(
            [classification_row(item_code, "SYRINGE", "USAGE", "3mL", "EA")],
            columns=CLASSIFICATION_COLUMNS,
        )

        with tempfile.TemporaryDirectory() as directory:
            taxonomy_path = Path(directory) / "taxonomy.csv"
            classification_path = Path(directory) / "classifications.csv"
            taxonomy.to_csv(taxonomy_path, index=False)
            classifications.to_csv(classification_path, index=False)
            loaded = load_approved_classifications(classification_path, taxonomy_path)

        self.assertEqual(loaded.approved_rows, 1)
        self.assertEqual(loaded.mappings.iloc[0]["local_item_key"], f"INST001::{item_code}")

    def test_approved_subtype_specification_and_unit_are_aggregated(self):
        taxonomy = pd.DataFrame(
            [
                taxonomy_row("SYRINGE", "USAGE", "3mL", "EA"),
                taxonomy_row("SYRINGE", "USAGE", "5mL", "EA"),
                taxonomy_row("INFUSION_SET", "INFUSION_SET", "standard", "EA"),
                taxonomy_row("INFUSION_SET", "INFUSION_SET", "standard", "BOX"),
                taxonomy_row(
                    "WASTE_CONTAINER",
                    "NEEDLE_BOX",
                    "1L",
                    "EA",
                    group_id="WASTE",
                    forecastable="f",
                ),
            ],
            columns=TAXONOMY_COLUMNS,
        )
        classifications = pd.DataFrame(
            [
                classification_row("A", "SYRINGE", "USAGE", "3mL", "EA"),
                classification_row("B", "SYRINGE", "USAGE", "3mL", "EA"),
                classification_row("C", "SYRINGE", "USAGE", "5mL", "EA"),
                classification_row("D", "INFUSION_SET", "INFUSION_SET", "standard", "EA"),
                classification_row("E", "INFUSION_SET", "INFUSION_SET", "standard", "BOX"),
                classification_row("W", "WASTE_CONTAINER", "NEEDLE_BOX", "1L", "EA"),
                classification_row(
                    "PENDING",
                    "SYRINGE",
                    "USAGE",
                    "3mL",
                    "EA",
                    review_status="candidate",
                ),
            ],
            columns=CLASSIFICATION_COLUMNS,
        )
        predictions = pd.DataFrame(
            [
                prediction_row("A", 10.0, 5.0),
                prediction_row("B", 20.0, 8.0),
                prediction_row("C", 7.0, 2.0),
                prediction_row("D", 4.0, 1.0),
                prediction_row("E", 6.0, 3.0),
                prediction_row("W", 9.0, 4.0),
            ]
        )

        with tempfile.TemporaryDirectory() as directory:
            taxonomy_path = Path(directory) / "taxonomy.csv"
            classification_path = Path(directory) / "classifications.csv"
            taxonomy.to_csv(taxonomy_path, index=False)
            classifications.to_csv(classification_path, index=False)
            output, report = build_classified_prediction_output(
                predictions,
                classification_path=classification_path,
                taxonomy_path=taxonomy_path,
            )

        syringe_3ml = output[
            (output["item_family_id"] == "SYRINGE")
            & (output["normalized_specification"] == "3mL")
        ].iloc[0]
        infusion = output[output["item_family_id"] == "INFUSION_SET"]

        self.assertEqual(len(output), 4)
        self.assertEqual(syringe_3ml["predicted_usage"], 30.0)
        self.assertEqual(syringe_3ml["base_stock"], 36.0)
        self.assertEqual(syringe_3ml["target_stock"], 36.0)
        self.assertEqual(syringe_3ml["current_stock"], 13.0)
        self.assertEqual(syringe_3ml["source_local_item_count"], 2)
        self.assertEqual(set(infusion["unit_code"]), {"EA", "BOX"})
        self.assertNotIn("WASTE", set(output["item_group_id"]))
        self.assertEqual(report["ignored_unapproved_classification_rows"], 1)
        self.assertEqual(report["excluded_non_forecastable_rows"], 1)
        self.assertEqual(report["status"], "ready")

    def test_approved_mapping_cannot_reference_candidate_taxonomy(self):
        taxonomy = pd.DataFrame(
            [taxonomy_row("SYRINGE", "USAGE", "3mL", "EA", review_status="candidate")],
            columns=TAXONOMY_COLUMNS,
        )
        classifications = pd.DataFrame(
            [classification_row("A", "SYRINGE", "USAGE", "3mL", "EA")],
            columns=CLASSIFICATION_COLUMNS,
        )

        with tempfile.TemporaryDirectory() as directory:
            taxonomy_path = Path(directory) / "taxonomy.csv"
            classification_path = Path(directory) / "classifications.csv"
            taxonomy.to_csv(taxonomy_path, index=False)
            classifications.to_csv(classification_path, index=False)
            with self.assertRaises(ClassificationValidationError):
                load_approved_classifications(classification_path, taxonomy_path)

    def test_local_item_key_must_have_one_approved_classification(self):
        taxonomy = pd.DataFrame(
            [taxonomy_row("SYRINGE", "USAGE", "3mL", "EA")],
            columns=TAXONOMY_COLUMNS,
        )
        duplicate = classification_row("A", "SYRINGE", "USAGE", "3mL", "EA")
        classifications = pd.DataFrame([duplicate, duplicate], columns=CLASSIFICATION_COLUMNS)

        with tempfile.TemporaryDirectory() as directory:
            taxonomy_path = Path(directory) / "taxonomy.csv"
            classification_path = Path(directory) / "classifications.csv"
            taxonomy.to_csv(taxonomy_path, index=False)
            classifications.to_csv(classification_path, index=False)
            with self.assertRaises(ClassificationValidationError):
                load_approved_classifications(classification_path, taxonomy_path)


if __name__ == "__main__":
    unittest.main()
