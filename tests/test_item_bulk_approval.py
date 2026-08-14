from pathlib import Path
import tempfile
import unittest

import pandas as pd

from src.item_bulk_approval import (
    build_bulk_classifications,
    build_bulk_material_mapping,
)
from src.material_mapping import load_approved_stock_material_mapping
from src.modeling.classified_prediction import load_approved_classifications


POLICY = {
    "version": "test-bulk-v1",
    "status": "user_requested_bulk_candidate_acceptance",
    "reviewer": "test-owner",
    "classification": {
        "activate_complete_candidates_for_forecast": True,
    },
    "material": {},
    "safety": {},
}


def local_candidate(
    key: str,
    *,
    family: str = "",
    subtype: str = "",
    specification: str = "",
    unit: str = "",
    statuses: str = "candidate_family",
) -> dict:
    institution, item_code = key.split("::")
    return {
        "local_item_key": key,
        "institution_code": institution,
        "item_code": item_code,
        "item_group_id": "MED_SUPPLY",
        "item_family_id": family,
        "standard_family_name": family or "미분류",
        "item_subtype_id": subtype,
        "standard_subtype_name": subtype,
        "normalized_specification": specification,
        "unit_code": unit,
        "representative_statuses": statuses,
    }


def material_candidate(key: str, representative_id: str) -> dict:
    institution, item_code = key.split("::")
    return {
        "local_item_key": key,
        "institution_code": institution,
        "item_code": item_code,
        "representative_item_id": representative_id,
        "representative_name": "주사기",
        "item_family_id": "DISPOSABLE_SYRINGE",
        "raw_material_meta_code": "POLYPROPYLENE_PP",
        "raw_material_risk_meta_code": "PETROCHEMICAL_SHOCK",
        "demand_risk_meta_code": "",
        "material_confidence": "identified",
        "material_evidence_tier": "family_rule_candidate",
        "material_evidence_reference": "https://example.test/pp",
        "material_review_status": "needs_review",
        "classification_material_family_conflict": False,
        "market_factor_count": 2,
        "supply_risk_policy_needs_review": False,
    }


class ItemBulkApprovalTest(unittest.TestCase):
    def test_all_rows_are_approved_but_incomplete_candidate_stays_inactive(self):
        local = pd.DataFrame(
            [
                local_candidate("I1::A"),
                local_candidate(
                    "I1::B",
                    family="DISPOSABLE_SYRINGE",
                    subtype="SYRINGE_USAGE_BASED",
                    specification="3mL",
                    unit="EA",
                    statuses="candidate_complete",
                ),
                local_candidate("I1::C"),
            ]
        )
        aliases = pd.DataFrame(
            {
                "local_item_key": ["I1::A", "I1::B", "I1::C"],
                "raw_item_name": ["기존 승인품", "주사기 3cc", "알 수 없는 물품"],
            }
        )
        strict = pd.DataFrame(
            [
                {
                    "local_item_key": "I1::A",
                    "item_family_id": "URINE_BAG",
                    "item_subtype_id": "URINE_BAG",
                    "normalized_specification": "standard",
                    "unit_code": "EA",
                    "taxonomy_version": "v1.0",
                    "reviewer": "evidence-reviewer",
                    "reviewed_at": "2026-07-01T00:00:00+00:00",
                    "evidence_reference": "DECISION::A",
                    "classification_version": "classification-v1.0",
                    "item_group_id": "MED_SUPPLY",
                    "standard_family_name": "소변백",
                    "standard_subtype_name": "소변백",
                    "unit_name": "개수",
                    "is_forecastable": True,
                }
            ]
        )

        approved, taxonomy, report = build_bulk_classifications(
            local,
            aliases,
            strict,
            POLICY,
            "2026-07-28T00:00:00+00:00",
        )

        self.assertEqual(len(approved), 3)
        self.assertTrue(approved["review_status"].eq("approved").all())
        by_key = approved.set_index("local_item_key")
        self.assertEqual(by_key.loc["I1::A", "item_family_id"], "URINE_BAG")
        self.assertTrue(by_key.loc["I1::B", "operational_eligible"])
        self.assertFalse(by_key.loc["I1::C", "operational_eligible"])
        self.assertTrue(
            by_key.loc["I1::C", "item_family_id"].startswith("BULK_PENDING_ITEM_")
        )
        self.assertEqual(report["approved_local_item_count"], 3)
        self.assertFalse(
            taxonomy.duplicated(
                [
                    "item_family_id",
                    "item_subtype_id",
                    "normalized_specification",
                    "unit_code",
                    "taxonomy_version",
                ]
            ).any()
        )

        with tempfile.TemporaryDirectory() as directory:
            classification_path = Path(directory) / "classification.parquet"
            taxonomy_path = Path(directory) / "taxonomy.parquet"
            approved.to_parquet(classification_path, index=False)
            taxonomy.to_parquet(taxonomy_path, index=False)
            loaded = load_approved_classifications(
                classification_path,
                taxonomy_path,
            )

        self.assertIn("item_group_id", loaded.mappings.columns)
        self.assertIn("standard_family_name", loaded.mappings.columns)
        self.assertEqual(len(loaded.mappings), 3)

    def test_material_rows_are_all_approved_but_loader_filters_operations(self):
        candidates = pd.DataFrame(
            [
                {
                    "local_item_key": "I1::A",
                    "institution_code": "I1",
                    "item_code": "A",
                    "representative_item_id": "R1",
                    "representative_name": "주사기",
                    "item_family_id": "DISPOSABLE_SYRINGE",
                    "raw_material_meta_code": "POLYPROPYLENE_PP",
                    "raw_material_risk_meta_code": "PETROCHEMICAL_SHOCK",
                    "demand_risk_meta_code": "",
                    "material_confidence": "identified",
                    "material_evidence_tier": "family_rule_candidate",
                    "material_evidence_reference": "https://example.test/pp",
                    "material_review_status": "needs_review",
                    "classification_material_family_conflict": False,
                    "market_factor_count": 2,
                    "supply_risk_policy_needs_review": False,
                },
                {
                    "local_item_key": "I1::B",
                    "institution_code": "I1",
                    "item_code": "B",
                    "representative_item_id": "R2",
                    "representative_name": "미분류",
                    "item_family_id": "",
                    "raw_material_meta_code": "MATERIAL_UNSPECIFIED",
                    "raw_material_risk_meta_code": "UNCLASSIFIED_MATERIAL_RISK",
                    "demand_risk_meta_code": "",
                    "material_confidence": "unmapped",
                    "material_evidence_tier": "unmapped",
                    "material_evidence_reference": "",
                    "material_review_status": "needs_review",
                    "classification_material_family_conflict": False,
                    "market_factor_count": 0,
                    "supply_risk_policy_needs_review": True,
                },
            ]
        )
        monthly = pd.DataFrame(
            [
                {
                    "year_month": "2025-12-01",
                    "institution_code": "I1",
                    "department": "D",
                    "item_code": item_code,
                    "stock_item_key": f"I1::D::{item_code}",
                }
                for item_code in ["A", "B"]
            ]
        )

        mapping, report = build_bulk_material_mapping(
            candidates,
            monthly,
            POLICY,
            "2026-07-28T00:00:00+00:00",
        )

        self.assertEqual(len(mapping), 2)
        self.assertTrue(mapping["review_status"].eq("approved").all())
        self.assertEqual(int(mapping["operational_eligible"].sum()), 1)
        self.assertEqual(report["approved_stock_mapping_rows"], 2)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bulk.parquet"
            mapping.to_parquet(path, index=False)
            all_rows = load_approved_stock_material_mapping(
                path,
                operational_only=False,
            )
            operational = load_approved_stock_material_mapping(path)

        self.assertEqual(len(all_rows), 2)
        self.assertEqual(len(operational), 1)
        self.assertEqual(
            operational.iloc[0]["raw_material_meta_code"],
            "POLYPROPYLENE_PP",
        )

    def test_history_only_candidates_are_dropped_and_counted(self):
        candidates = pd.DataFrame(
            [
                material_candidate("I1::A", "R1"),
                # Present in the historical auxiliary ledger only, so it never
                # appears in the operational monthly stock.
                material_candidate("I1::HIST", "R2"),
            ]
        )
        monthly = pd.DataFrame(
            [
                {
                    "year_month": "2025-12-01",
                    "institution_code": "I1",
                    "department": "D",
                    "item_code": "A",
                    "stock_item_key": "I1::D::A",
                }
            ]
        )

        mapping, report = build_bulk_material_mapping(
            candidates,
            monthly,
            POLICY,
            "2026-07-28T00:00:00+00:00",
        )

        self.assertEqual(len(mapping), 1)
        self.assertEqual(mapping.iloc[0]["stock_item_key"], "I1::D::A")
        self.assertEqual(report["unresolved_stock_key_candidate_rows"], 1)
        self.assertEqual(report["unresolved_stock_key_local_item_count"], 1)
        self.assertEqual(report["resolved_candidate_rows"], 1)
        self.assertEqual(report["input_candidate_rows"], 2)

    def test_no_resolvable_candidate_still_fails_loudly(self):
        candidates = pd.DataFrame([material_candidate("I1::HIST", "R1")])
        monthly = pd.DataFrame(
            [
                {
                    "year_month": "2025-12-01",
                    "institution_code": "I1",
                    "department": "D",
                    "item_code": "A",
                    "stock_item_key": "I1::D::A",
                }
            ]
        )

        with self.assertRaises(ValueError):
            build_bulk_material_mapping(
                candidates,
                monthly,
                POLICY,
                "2026-07-28T00:00:00+00:00",
            )


if __name__ == "__main__":
    unittest.main()
