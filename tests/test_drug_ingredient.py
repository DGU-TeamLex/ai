from pathlib import Path
import tempfile
import unittest

import pandas as pd

from src.drug_ingredient import (
    attach_approved_drug_ingredients,
    build_drug_ingredient_outputs,
    ingredient_meta_code,
    parse_ingredient_name,
)


class DrugIngredientTest(unittest.TestCase):
    def test_ingredient_name_and_strength_are_separated(self):
        self.assertEqual(
            parse_ingredient_name("chlorpheniramine maleate1.5mg"),
            ("chlorpheniramine maleate", "1.5mg"),
        )
        self.assertEqual(
            ingredient_meta_code("risedronate sodium35mg"),
            "RISEDRONATE_SODIUM",
        )
        self.assertEqual(
            parse_ingredient_name(
                "sodium hyaluronate gel40mg(20mg/mL)"
            ),
            ("sodium hyaluronate gel", "40mg;20mg/mL"),
        )
        self.assertEqual(
            parse_ingredient_name("rabies antigen2.5U이상(5I.U/mL이상)"),
            ("rabies antigen", "2.5U;5I.U/mL이상"),
        )
        self.assertEqual(
            ingredient_meta_code(
                "standardized bacterial lysates 60mg (as bacterial lysates)6mg"
            ),
            "STANDARDIZED_BACTERIAL_LYSATES_AS_BACTERIAL_LYSATES",
        )

    def test_builds_exact_and_unique_fallback_approved_enrichment(self):
        aliases = pd.DataFrame(
            [
                {
                    "institution_id": "I1",
                    "local_item_code": "A",
                    "local_item_key": "I1::A",
                    "raw_item_name": "약A 10mg",
                    "product_name_candidate": "약A",
                    "representative_item_id": "R1",
                },
                {
                    "institution_id": "I2",
                    "local_item_code": "A",
                    "local_item_key": "I2::A",
                    "raw_item_name": "약A 20mg",
                    "product_name_candidate": "약A",
                    "representative_item_id": "R1",
                },
                {
                    "institution_id": "I3",
                    "local_item_code": "B",
                    "local_item_key": "I3::B",
                    "raw_item_name": "약B",
                    "product_name_candidate": "약B",
                    "representative_item_id": "R2",
                },
            ]
        )
        source = (
            "약품코드|약품명1|용도구분|약종류구분|약품단위1|성분코드|성분명|보건기관코드_en\n"
            "A|약A 10mg|진료약|경구|Tab|ING1|acetaminophen10mg|I1\n"
            "A|약A 20mg|진료약|경구|Tab|ING1|acetaminophen20mg|I2\n"
            "B|약B|진료약|경구|Tab|ING2|ibuprofen200mg|I9\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            alias_path = directory_path / "aliases.parquet"
            source_path = directory_path / "ingredients.DAT"
            aliases.to_parquet(alias_path, index=False)
            source_path.write_text(source, encoding="utf-8")
            enrichment, dictionary, report = build_drug_ingredient_outputs(
                source_path=source_path,
                alias_path=alias_path,
            )

        by_id = enrichment.set_index("representative_item_id")
        self.assertEqual(
            by_id.loc["R1", "drug_raw_material_meta_code"],
            "ACETAMINOPHEN",
        )
        self.assertEqual(
            by_id.loc["R1", "drug_ingredient_strengths"],
            "10mg;20mg",
        )
        self.assertEqual(
            by_id.loc["R2", "drug_ingredient_match_methods"],
            "drug_code_unique",
        )
        self.assertTrue(
            enrichment["drug_ingredient_review_status"].eq("approved").all()
        )
        self.assertGreater(len(dictionary), 0)
        self.assertEqual(report["approved_representative_rows"], 2)

    def test_attaches_only_approved_identity(self):
        items = pd.DataFrame(
            {
                "representative_item_id": ["R1", "R2"],
                "ingredient_ids": ["OLD", ""],
                "ingredient_names": ["기존", ""],
                "ingredient_source": ["legacy", ""],
                "material_match_readiness": ["candidate", ""],
            }
        )
        enrichment = pd.DataFrame(
            {
                "representative_item_id": ["R1", "R2"],
                "drug_raw_material_meta_code": ["ACETAMINOPHEN", ""],
                "drug_ingredient_name": ["acetaminophen", ""],
                "drug_ingredient_review_status": [
                    "approved",
                    "blocked_identity_conflict",
                ],
            }
        )

        attached = attach_approved_drug_ingredients(items, enrichment).set_index(
            "representative_item_id"
        )

        self.assertEqual(attached.loc["R1", "ingredient_ids"], "ACETAMINOPHEN")
        self.assertEqual(
            attached.loc["R1", "ingredient_source"],
            "government_drug_ingredient_dataset_approved",
        )
        self.assertEqual(attached.loc["R2", "ingredient_ids"], "")


if __name__ == "__main__":
    unittest.main()
