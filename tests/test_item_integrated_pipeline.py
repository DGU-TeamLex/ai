from pathlib import Path
import tempfile
import unittest

import pandas as pd

from src.item_integrated_pipeline import (
    compose_integrated_classification,
    select_integrated_sample,
)


class ItemIntegratedPipelineTest(unittest.TestCase):
    def test_compose_uses_structured_family_and_keeps_gauge_as_specification(self):
        material = pd.DataFrame(
            [
                {
                    "representative_item_id": "ITEM1",
                    "representative_name": "주사침 24G 100개",
                    "item_group_id_candidate": "MED_SUPPLY",
                    "item_family_id_suggested": "INJECTION_NEEDLE",
                    "standard_family_name_suggested": "주사침",
                    "item_subtype_id_candidate": "INJECTION_NEEDLE",
                    "standard_subtype_name_candidate": "주사침",
                    "normalized_specification_candidate": "24G",
                    "standard_unit_candidate": "EA",
                    "needle_gauge": "24G",
                    "pack_quantity": 100,
                    "pack_unit": "EA",
                    "family_source": "local_structured_family",
                    "family_resolution_status": "local_structured_family_name_rule_agree",
                    "family_conflict_flag": "false",
                    "name_rule_item_family_id": "INJECTION_NEEDLE",
                    "material_source_family_id": "INJECTION_NEEDLE",
                    "material_source_subtype_id": "INJECTION_NEEDLE",
                    "raw_material_suggested": "스테인리스강 + PP",
                    "raw_material_meta_code": "STAINLESS_STEEL;POLYPROPYLENE_PP",
                    "material_evidence_tier": "family_rule_candidate",
                    "material_review_status": "needs_review",
                    "activity_scope": "active_low",
                    "occurrence_count": 10,
                    "institution_count": 2,
                    "usage_sum": 20,
                },
                {
                    "representative_item_id": "SET1",
                    "representative_name": "혈당소모품세트(스틱1,란셋1,알코올솜1)",
                    "item_group_id_candidate": "MED_SUPPLY",
                    "item_family_id_suggested": "BLOOD_GLUCOSE_TESTING_SET",
                    "standard_family_name_suggested": "혈당측정 소모품 세트",
                    "item_subtype_id_candidate": "BLOOD_LANCET",
                    "standard_subtype_name_candidate": "채혈침",
                    "normalized_specification_candidate": "",
                    "standard_unit_candidate": "EA",
                    "family_source": "context_explicit_rule",
                    "family_resolution_status": "family_conflict_context_explicit_rule_preferred",
                    "family_conflict_flag": "true",
                    "name_rule_item_family_id": "BLOOD_GLUCOSE_TESTING_SET",
                    "material_source_family_id": "BLOOD_GLUCOSE_TESTING_SET",
                    "material_source_subtype_id": "BLOOD_LANCET",
                    "raw_material_suggested": "복합 세트",
                    "raw_material_meta_code": "MATERIAL_UNSPECIFIED",
                    "material_evidence_tier": "composite_set_requires_bom",
                    "material_review_status": "needs_review",
                    "activity_scope": "active_low",
                    "occurrence_count": 4,
                    "institution_count": 2,
                    "usage_sum": 10,
                },
            ]
        )
        classification = pd.DataFrame(
            [
                {
                    "representative_item_id": "ITEM1",
                    "selected_item_family_id": "INJECTION_NEEDLE",
                    "selected_standard_family_name": "주사침",
                    "selected_item_subtype_id": "INJECTION_NEEDLE",
                    "selected_standard_subtype_name": "주사침",
                    "selected_specification": "24G",
                    "selected_unit_code": "EA",
                    "classification_status": "candidate_complete",
                    "classification_basis": "local_explicit_family_rule",
                    "classification_confidence": 0.6,
                    "review_status": "needs_taxonomy_review",
                    "review_reason": "family_or_detail_requires_review",
                    "verification_status": "candidate_classification",
                    "is_forecastable": True,
                    "classification_version": "classification-v1.0",
                },
                {
                    "representative_item_id": "SET1",
                    "selected_item_family_id": "BLOOD_LANCET",
                    "selected_standard_family_name": "채혈침",
                    "selected_item_subtype_id": "BLOOD_LANCET",
                    "selected_standard_subtype_name": "채혈침",
                    "selected_specification": "",
                    "selected_unit_code": "EA",
                    "classification_status": "candidate_complete",
                    "classification_basis": "local_explicit_family_rule",
                    "classification_confidence": 0.6,
                    "review_status": "needs_taxonomy_review",
                    "review_reason": "family_or_detail_requires_review",
                    "verification_status": "candidate_classification",
                    "is_forecastable": True,
                    "classification_version": "classification-v1.0",
                },
            ]
        )
        parent = pd.DataFrame(
            [
                {
                    "representative_item_id": "ITEM1",
                    "parent_concept_id": "INJECTION_NEEDLE",
                    "parent_concept_name": "주사침",
                    "parent_concept_source": "structured_family",
                    "child_original_name": "주사침 24G 100개",
                    "concept_match_key": "주사침24g100개",
                    "forecast_grouping_key_candidate": (
                        "INJECTION_NEEDLE::INJECTION_NEEDLE::24G::EA"
                    ),
                },
                {
                    "representative_item_id": "SET1",
                    "parent_concept_id": "BLOOD_GLUCOSE_TESTING_SET",
                    "parent_concept_name": "혈당측정 소모품 세트",
                    "parent_concept_source": "structured_family",
                    "child_original_name": "혈당소모품세트(스틱1,란셋1,알코올솜1)",
                    "concept_match_key": "혈당소모품세트스틱1란셋1알코올솜1",
                    "forecast_grouping_key_candidate": (
                        "BLOOD_GLUCOSE_TESTING_SET::BLOOD_LANCET::UNSPECIFIED_SPEC::EA"
                    ),
                },
            ]
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            material_path = root / "material.csv"
            classification_path = root / "classification.parquet"
            parent_path = root / "parent.csv"
            material.to_csv(material_path, index=False)
            classification.to_parquet(classification_path, index=False)
            parent.to_csv(parent_path, index=False)
            integrated = compose_integrated_classification(
                material_path, classification_path, parent_path
            )
            sample = select_integrated_sample(integrated, sample_size=2)

        rows = integrated.set_index("representative_item_id")
        row = rows.loc["ITEM1"]
        self.assertEqual(row["effective_item_family_id"], "INJECTION_NEEDLE")
        self.assertEqual(row["effective_specification"], "24G")
        self.assertEqual(row["effective_unit_code"], "EA")
        self.assertEqual(row["forecast_eligibility_candidate"], "ready_after_review")
        composite = rows.loc["SET1"]
        self.assertEqual(
            composite["effective_item_subtype_id"], "BLOOD_GLUCOSE_TESTING_SET"
        )
        self.assertEqual(composite["effective_specification"], "")
        self.assertEqual(composite["effective_unit_code"], "EA")
        self.assertEqual(len(sample), 2)
        self.assertTrue(sample["attention_flags"].str.contains("needle_gauge_parsed").any())


if __name__ == "__main__":
    unittest.main()
