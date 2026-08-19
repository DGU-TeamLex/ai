import unittest

import pandas as pd

from scripts.analysis.meta_code_normalization_research_audit import (
    audit_historical_effect,
    audit_meta_codes,
    audit_standard_mapping,
)


class MetaCodeNormalizationResearchAuditTest(unittest.TestCase):
    def test_standard_mapping_separates_unmatched_history_from_training(self):
        mapping = pd.DataFrame(
            {
                "data_period": ["current", "historical", "historical"],
                "local_item_key": ["c1", "h1", "h2"],
                "standard_item_key": ["s1", "s1", "s2"],
                "standard_item_definition_key": ["d1", "d1", "d2"],
                "standard_item_group_id": ["g", "g", "g"],
                "standard_item_family_id": ["f", "f", "f"],
                "standard_item_subtype_id": ["", "", ""],
                "standard_item_specification": ["", "", ""],
                "standard_item_unit_code": ["EA", "EA", "EA"],
                "standardization_match_method": [
                    "current_semantic_definition",
                    "historical_strict_semantic",
                    "historical_name_fallback",
                ],
                "standardization_confidence": [1.0, 0.9, 0.5],
                "historical_training_eligible": [True, True, False],
            }
        )
        report = {
            "mapping_rows": 3,
            "current_local_items": 1,
            "historical_local_items": 2,
            "historical_training_eligible_items": 1,
            "standard_item_count": 2,
        }
        audit = audit_standard_mapping(mapping, report)
        self.assertEqual(audit["historical_training_eligible_pct"], 50.0)
        self.assertEqual(audit["historical_unmatched_items_excluded"], 1)
        self.assertTrue(audit["quality_gate_passed"])

    def test_standard_mapping_rejects_duplicate_local_key(self):
        mapping = pd.DataFrame(
            {
                "data_period": ["current", "current"],
                "local_item_key": ["same", "same"],
                "standard_item_key": ["s1", "s2"],
                "standard_item_definition_key": ["d1", "d2"],
                "standard_item_group_id": ["g", "g"],
                "standard_item_family_id": ["f", "f"],
                "standard_item_subtype_id": ["", ""],
                "standard_item_specification": ["", ""],
                "standard_item_unit_code": ["", ""],
                "standardization_match_method": ["a", "b"],
                "standardization_confidence": [1.0, 1.0],
                "historical_training_eligible": [True, True],
            }
        )
        with self.assertRaisesRegex(ValueError, "품질 게이트 실패"):
            audit_standard_mapping(mapping, {})

    def test_meta_code_audit_distinguishes_candidates_from_approved_mapping(self):
        integrated = pd.DataFrame(
            {
                "representative_item_id": ["r1", "r2"],
                "effective_item_family_id": ["DISPOSABLE_SYRINGE", "UNSPECIFIED_ITEM"],
                "effective_item_subtype_id": ["", ""],
                "effective_specification": ["10ML", ""],
                "effective_unit_code": ["EA", ""],
                "classification_classification_status": ["approved_external_family", "unresolved"],
                "classification_review_status": ["approved", "needs_external_evidence"],
                "classification_verification_status": ["verified_family", "candidate_classification"],
                "raw_material_meta_code": ["POLYPROPYLENE_PP", "MATERIAL_UNSPECIFIED"],
                "raw_material_risk_meta_code": ["MIDEAST_NAPHTHA_PETROCHEM_SHOCK", "UNCLASSIFIED_MATERIAL_RISK"],
                "demand_risk_meta_code": ["NOT_APPLICABLE", "LOW_PRIORITY_NO_TRIGGER"],
                "material_review_status": ["needs_review", "needs_review"],
            }
        )
        approved = pd.DataFrame(
            {
                "stock_item_key": ["s1"],
                "raw_material_meta_code": ["POLYPROPYLENE_PP"],
                "raw_material_risk_meta_code": ["MIDEAST_NAPHTHA_PETROCHEM_SHOCK"],
                "demand_risk_meta_code": [""],
                "relation_type": ["direct_component"],
                "review_status": ["approved"],
            }
        )
        report = {
            "approved_candidate_rows": 1,
            "approved_local_item_count": 1,
            "approved_stock_item_mapping_rows": 1,
            "applied": False,
        }
        audit = audit_meta_codes(integrated, approved, report)
        self.assertEqual(audit["specific_family_rows"], 1)
        self.assertEqual(audit["classification_review_required_rows"], 1)
        self.assertEqual(audit["raw_material_meta_codes"]["actionable_rows"], 1)
        self.assertEqual(audit["approved_stock_item_mapping_rows"], 1)

    def test_historical_effect_uses_validation_only_and_recomputes_change(self):
        report = {
            "selection_metric": "minimum validation WAPE",
            "selection_did_not_use_test": True,
            "validation_start": "2025-01",
            "validation_end": "2025-06",
            "selected_historical_weight": 1.0,
            "selected_validation_metrics": {"WAPE": 37.246, "historical_rows": 952002},
            "current_only_validation_metrics": {"WAPE": 39.223},
            "validation_wape_change": -1.977,
        }
        audit = audit_historical_effect(report)
        self.assertEqual(audit["validation_period"], "2025-01~2025-06")
        self.assertAlmostEqual(audit["validation_wape_change_pct_point"], -1.977)
        self.assertTrue(audit["selection_did_not_use_test"])


if __name__ == "__main__":
    unittest.main()
