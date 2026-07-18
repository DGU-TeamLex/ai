import csv
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from src.item_attribute_parser import (
    DICTIONARY_COLUMNS,
    ItemAttributeParser,
    load_verified_dictionary,
)


class ItemAttributeParserTest(unittest.TestCase):
    def setUp(self):
        self.parser = ItemAttributeParser(
            {
                "레보드로프로피진": "LEVODROPROPIZINE",
                "암로디핀베실산염": "AMLODIPINE",
            }
        )

    def test_syringe_capacity_gauge_length_and_count_are_separate(self):
        parsed = self.parser.parse(
            "주사기(3cc/23g*24mm/100개입)",
            item_group_id="MED_SUPPLY",
            item_family_id="DISPOSABLE_SYRINGE",
            item_subtype_id="SYRINGE_USAGE_BASED",
            standard_unit="EA",
        )

        self.assertEqual(parsed["base_item_name_candidate"], "주사기")
        self.assertEqual(parsed["capacity_normalized"], "3mL")
        self.assertEqual(parsed["needle_gauge"], "23G")
        self.assertEqual(parsed["needle_length"], "24mm")
        self.assertEqual(parsed["pack_quantity"], "100")
        self.assertEqual(parsed["pack_unit"], "EA")
        self.assertEqual(parsed["active_strengths"], "")

        x_separator = self.parser.parse(
            "주사기(3cc/23gX24mm/100개입)",
            item_group_id="MED_SUPPLY",
            item_family_id="DISPOSABLE_SYRINGE",
            standard_unit="EA",
        )
        self.assertEqual(x_separator["base_item_name_candidate"], "주사기")
        self.assertEqual(x_separator["needle_gauge"], "23G")
        self.assertEqual(x_separator["needle_length"], "24mm")

    def test_drug_gram_is_strength_without_needle_context(self):
        parsed = self.parser.parse(
            "세프트리악손주1G",
            item_group_id="MED_INJECT",
            dosage_form="주사제",
        )

        self.assertEqual(parsed["needle_gauge"], "")
        self.assertEqual(parsed["active_strengths"], "1g")

    def test_drug_name_is_split_into_ingredient_company_concentration_and_pack(self):
        parsed = self.parser.parse(
            "레드로프시럽(레보드로프로피진)_(3g/500mL)(한국넬슨제약(주))-500(1)mL/병",
            item_group_id="MED_ORAL",
            dosage_form="시럽",
        )

        self.assertEqual(parsed["base_item_name_candidate"], "레드로프시럽")
        self.assertEqual(parsed["ingredient_ids"], "LEVODROPROPIZINE")
        self.assertEqual(parsed["manufacturer_candidate"], "한국넬슨제약")
        self.assertEqual(parsed["concentrations"], "3g/500mL")
        self.assertEqual(parsed["capacity_normalized"], "500mL")
        self.assertEqual(parsed["pack_quantity"], "1")
        self.assertEqual(parsed["pack_unit"], "BOTTLE")
        self.assertEqual(parsed["unresolved_tokens"], "")

    def test_supply_mass_is_net_weight_not_gauge_or_drug_strength(self):
        parsed = self.parser.parse(
            "멸균바세린거즈 8g",
            item_group_id="MED_SUPPLY",
            item_family_id="MEDICAL_GAUZE",
            standard_unit="EA",
        )

        self.assertEqual(parsed["net_weight"], "8g")
        self.assertEqual(parsed["needle_gauge"], "")
        self.assertEqual(parsed["active_strengths"], "")

    def test_trailing_topical_mass_is_net_weight_and_per_unit_is_pack(self):
        parsed = self.parser.parse(
            "후시딘연고(동화약품공업)-10G(1EA)",
            item_group_id="MED_TOPICAL",
            dosage_form="연고",
        )
        per_unit = self.parser.parse(
            "로마졸크림-50g/개",
            item_group_id="MED_TOPICAL",
            dosage_form="크림",
        )

        self.assertEqual(parsed["net_weight"], "10g")
        self.assertEqual(parsed["active_strengths"], "")
        self.assertEqual(parsed["pack_quantity"], "1")
        self.assertEqual(parsed["pack_unit"], "EA")
        self.assertEqual(per_unit["net_weight"], "50g")
        self.assertEqual(per_unit["pack_quantity"], "1")
        self.assertEqual(per_unit["pack_unit"], "EA")

    def test_mass_over_mass_concentration_does_not_duplicate_package_weight(self):
        parser = ItemAttributeParser(
            {
                "후시딘": "FUSIDIC_ACID",
                "퓨시드산나트륨": "SODIUM_FUSIDATE",
            }
        )
        parsed = parser.parse(
            "후시딘연고(퓨시드산나트륨)(0.2g/20g)(동화약품(주))-20g/개",
            item_group_id="MED_TOPICAL",
            dosage_form="연고",
        )

        self.assertEqual(parsed["base_item_name_candidate"], "후시딘연고")
        self.assertEqual(parsed["ingredient_ids"], "SODIUM_FUSIDATE")
        self.assertEqual(parsed["concentrations"], "0.2g/20g")
        self.assertEqual(parsed["active_strengths"], "0.2g")
        self.assertEqual(parsed["net_weight"], "20g")

        brand_only = parser.parse(
            "후시딘연고-5g/개",
            item_group_id="MED_TOPICAL",
            dosage_form="연고",
        )
        self.assertEqual(brand_only["base_item_name_candidate"], "후시딘연고")
        self.assertIn(
            "ingredient_mapping_requires_external_verification",
            brand_only["external_match_needed_reasons"],
        )

    def test_strength_per_tablet_is_dose_basis_not_package_count(self):
        parsed = self.parser.parse(
            "노바스크정(암로디핀베실산염)_(6.944mg/1정)-30정",
            item_group_id="MED_ORAL",
            dosage_form="정제",
        )

        self.assertEqual(parsed["concentrations"], "6.944mg/1TABLET")
        self.assertEqual(parsed["active_strengths"], "6.944mg")
        self.assertEqual(parsed["base_item_name_candidate"], "노바스크정")
        self.assertEqual(parsed["pack_quantity"], "30")
        self.assertEqual(parsed["pack_unit"], "TABLET")

    def test_volume_per_sachet_infers_one_package(self):
        parsed = self.parser.parse(
            "알마겔현탁액-15mL/포",
            item_group_id="MED_ORAL",
            dosage_form="현탁액",
        )

        self.assertEqual(parsed["capacity_normalized"], "15mL")
        self.assertEqual(parsed["pack_quantity"], "1")
        self.assertEqual(parsed["pack_unit"], "SACHET")

    def test_unmapped_literal_ingredient_is_preserved_without_guessing_id(self):
        parsed = self.parser.parse(
            "유한메트포르민서방정500mg(메트포르민염산염)-1정",
            item_group_id="MED_ORAL",
            dosage_form="정제",
        )

        self.assertEqual(parsed["base_item_name_candidate"], "유한메트포르민서방정")
        self.assertEqual(parsed["manufacturer_candidate"], "")
        self.assertEqual(parsed["ingredient_names"], "메트포르민염산염")
        self.assertTrue(parsed["ingredient_ids"].startswith("UNMAPPED::"))
        self.assertIn(
            "ingredient_requires_canonical_verification",
            parsed["external_match_needed_reasons"],
        )

    def test_only_official_verified_dictionary_rows_are_loadable(self):
        rows = [
            {
                "dictionary_id": "verified",
                "entry_type": "product_alias",
                "match_key": "검증품",
                "canonical_id": "VERIFIED_PRODUCT",
                "canonical_value": "검증품",
                "applicable_context": "medication",
                "source_name": "official",
                "source_record_id": "1",
                "source_url": "https://nedrug.mfds.go.kr/example",
                "source_tier": "official_regulator",
                "verification_status": "verified_official",
                "confidence": "0.99",
                "retrieved_at": "2026-07-16T00:00:00+00:00",
                "evidence_note": "official evidence",
                "dictionary_version": "test",
            },
            {
                "dictionary_id": "candidate",
                "entry_type": "product_alias",
                "match_key": "후보품",
                "canonical_id": "CANDIDATE_PRODUCT",
                "canonical_value": "후보품",
                "applicable_context": "medication",
                "source_name": "secondary",
                "source_record_id": "2",
                "source_url": "https://example.com/item",
                "source_tier": "secondary_reference",
                "verification_status": "candidate_unverified",
                "confidence": "0.50",
                "retrieved_at": "2026-07-16T00:00:00+00:00",
                "evidence_note": "candidate evidence",
                "dictionary_version": "test",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dictionary.csv"
            with path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=DICTIONARY_COLUMNS)
                writer.writeheader()
                writer.writerows(rows)
            loaded = load_verified_dictionary(path)

        self.assertEqual(loaded["dictionary_id"].tolist(), ["verified"])

    def test_verified_product_dictionary_can_supply_offline_attributes(self):
        dictionary = pd.DataFrame(
            [
                {
                    "dictionary_id": "product",
                    "entry_type": "product_alias",
                    "match_key": "고덱스캡슐",
                    "canonical_id": "GODEX",
                    "canonical_value": "고덱스캡슐",
                    "confidence": "0.98",
                },
                {
                    "dictionary_id": "ingredient",
                    "entry_type": "product_ingredient",
                    "match_key": "고덱스캡슐",
                    "canonical_id": "CARNITINE_OROTATE",
                    "canonical_value": "오로트산카르니틴",
                    "confidence": "0.98",
                },
            ]
        )
        parser = ItemAttributeParser({}, dictionary)
        parsed = parser.parse(
            "고덱스캡슐_(1캡슐)-1캡슐",
            item_group_id="MED_ORAL",
            dosage_form="캡슐",
        )

        self.assertEqual(parsed["canonical_product_id"], "GODEX")
        self.assertEqual(parsed["ingredient_ids"], "CARNITINE_OROTATE")
        self.assertEqual(parsed["attribute_parse_status"], "verified_external_dictionary")


if __name__ == "__main__":
    unittest.main()
