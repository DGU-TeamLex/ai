import unittest

import pandas as pd

from src.item_review_export import build_noteworthy_sample


class ItemReviewExportTest(unittest.TestCase):
    def test_sample_is_unique_and_keeps_priority_categories(self):
        records = []
        for index in range(40):
            status = "candidate_family"
            family = "OTHER"
            nedrug = ""
            if index < 3:
                status = "conflict"
            elif index < 6:
                nedrug = str(1000 + index)
            elif index < 10:
                family = "MEDICAL_WASTE_CONTAINER"
            records.append(
                {
                    "representative_item_id": f"REP{index:03d}",
                    "representative_name": f"품목 {index}",
                    "classification_status": status,
                    "item_family_id_candidate": family,
                    "selected_item_family_id": family,
                    "nedrug_item_seq": nedrug,
                    "usage_sum": float(1000 - index),
                    "occurrence_count": 100 - index,
                    "family_basis": "unresolved",
                }
            )
        classifications = pd.DataFrame(records)
        materials = pd.DataFrame(
            {
                "representative_item_id": [f"REP{index:03d}" for index in range(40)],
                "raw_material_suggested": ["UNKNOWN"] * 40,
                "raw_material_evidence": [""] * 40,
                "raw_material_meta_code": ["UNKNOWN"] * 40,
                "material_confidence": ["low"] * 40,
                "material_review_status": ["needs_review"] * 40,
                "supply_cluster_id": ["OTHER"] * 40,
                "supply_cluster_name": ["기타"] * 40,
            }
        )

        sample = build_noteworthy_sample(classifications, materials, sample_size=20)

        self.assertEqual(len(sample), 20)
        self.assertTrue(sample["representative_item_id"].is_unique)
        self.assertIn("classification_conflict", set(sample["attention_category"]))
        self.assertIn("official_drug_evidence_review", set(sample["attention_category"]))
        self.assertIn("medical_waste_boundary", set(sample["attention_category"]))
        self.assertEqual(sample["attention_rank"].tolist(), list(range(1, 21)))

    def test_rejects_sample_larger_than_input(self):
        classifications = pd.DataFrame(
            [
                {
                    "representative_item_id": "REP1",
                    "classification_status": "unresolved",
                    "item_family_id_candidate": "",
                    "selected_item_family_id": "",
                    "nedrug_item_seq": "",
                    "usage_sum": 1,
                    "occurrence_count": 1,
                }
            ]
        )
        materials = pd.DataFrame(
            [
                {
                    "representative_item_id": "REP1",
                    "raw_material_suggested": "UNKNOWN",
                    "raw_material_evidence": "",
                    "raw_material_meta_code": "UNKNOWN",
                    "material_confidence": "low",
                    "material_review_status": "needs_review",
                    "supply_cluster_id": "OTHER",
                    "supply_cluster_name": "기타",
                }
            ]
        )

        with self.assertRaisesRegex(ValueError, "Not enough"):
            build_noteworthy_sample(classifications, materials, sample_size=2)


if __name__ == "__main__":
    unittest.main()
