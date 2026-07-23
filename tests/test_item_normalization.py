import csv
from pathlib import Path
import tempfile
import unittest

from src.data_loader import RAW_STOCK_COLUMNS
from src.item_normalization import (
    AliasStats,
    generate_full_normalization,
    generate_normalization_sample,
    normalize_alias,
)


def alias(name: str, code: str = "USE0000001") -> AliasStats:
    return AliasStats(
        institution_id="INST001",
        local_item_code=code,
        raw_item_name=name,
        example_department="방문건강관리사업",
        occurrence_count=10,
        usage_sum=20.5,
        first_seen_date="20240101",
        last_seen_date="20240131",
    )


def stock_row(name: str, code: str, usage: str = "1") -> list[str]:
    values = {column: "0" for column in RAW_STOCK_COLUMNS}
    values.update(
        {
            "부서코드": "방문건강관리사업",
            "물품코드": code,
            "물품명": name,
            "재고마감일": "20240101",
            "구입처코드": "",
            "구입단가": "",
            "정상출고량": usage,
            "보건기관코드_en": "INST001",
        }
    )
    return [values[column] for column in RAW_STOCK_COLUMNS]


class ItemNormalizationTest(unittest.TestCase):
    def test_operational_tag_and_blood_glucose_synonym_are_separated(self):
        normalized = normalize_alias(alias("[방문]혈당검사스틱"))

        self.assertEqual(normalized["product_name_candidate"], "혈당검사스틱")
        self.assertEqual(normalized["operational_tags"], "방문")
        self.assertEqual(normalized["standard_family_name_candidate"], "혈당검사지")
        self.assertEqual(normalized["item_family_id_candidate"], "BLOOD_GLUCOSE_TEST_STRIP")
        self.assertEqual(normalized["item_group_id_candidate"], "LAB_REAGENT")

    def test_medication_and_supply_forms_are_not_confused(self):
        medication = normalize_alias(alias("아마릴정2mg(한독약품)-1정", "WA07404061"))
        syringe = normalize_alias(alias("일회용 주사기 10mL", "USE0000002"))

        self.assertEqual(medication["item_group_id_candidate"], "MED_ORAL")
        self.assertEqual(medication["dosage_form_candidate"], "정제")
        self.assertEqual(medication["strength_candidate"], "2mg")
        self.assertEqual(syringe["item_group_id_candidate"], "MED_SUPPLY")

    def test_promo_purpose_overrides_product_form(self):
        normalized = normalize_alias(alias("홍보물품-한방파스"))

        self.assertEqual(normalized["item_group_id_candidate"], "PROMO")
        self.assertEqual(normalized["is_forecastable_candidate"], "f")

    def test_drug_name_containing_lax_is_not_disinfectant(self):
        normalized = normalize_alias(alias("(비급여)둘코락스에스장용정(사노피아벤티스코리아)-1정", "WMD0326951"))

        self.assertEqual(normalized["item_group_id_candidate"], "MED_ORAL")

    def test_dosage_form_has_priority_over_korean_medicine_code_hint(self):
        oral = normalize_alias(alias("레일라정(당귀·목과·방풍)-1정", "O123456"))
        topical = normalize_alias(alias("한방파스", "O654321"))

        self.assertEqual(oral["item_group_id_candidate"], "MED_ORAL")
        self.assertEqual(topical["item_group_id_candidate"], "MED_TOPICAL")

    def test_detailed_supply_taxonomy_extracts_subtype_specification_and_unit(self):
        cases = [
            ("주사기 3cc", "DISPOSABLE_SYRINGE", "SYRINGE_USAGE_BASED", "3mL", "EA", "MED_SUPPLY"),
            (
                "의료폐기물 전용용기 봉투형용기(PE) 4L",
                "MEDICAL_WASTE_CONTAINER",
                "MEDICAL_WASTE_PE_BAG",
                "4L",
                "EA",
                "WASTE",
            ),
            (
                "멸균포장재(EO가스 소독용 포장재) 15cm*200M",
                "EO_STERILIZATION_PACKAGING",
                "EO_STERILIZATION_ROLL",
                "15cm x 200m",
                "ROLL",
                "DISINFECT",
            ),
            ("수액제통 500cc 이하", "IV_FLUID_CONTAINER", "IV_FLUID_CONTAINER", "<=500mL", "EA", "MED_SUPPLY"),
            ("수액세트", "INFUSION_SET", "INFUSION_SET", "수액세트", "EA", "MED_SUPPLY"),
            ("혈액투석제통 5L 초과", "DIALYSATE_CONTAINER", "DIALYSATE_CONTAINER", ">5L", "EA", "MED_SUPPLY"),
            ("카테터(angio needle) 22G (성인용)", "ANGIO_CATHETER", "ANGIO_NEEDLE", "22G (성인용)", "EA", "MED_SUPPLY"),
            ("Urine bag", "URINE_BAG", "URINE_BAG", "Urine bag", "EA", "MED_SUPPLY"),
        ]

        for name, family_id, subtype_id, specification, unit, group_id in cases:
            with self.subTest(name=name):
                normalized = normalize_alias(alias(name))
                self.assertEqual(normalized["item_family_id_candidate"], family_id)
                self.assertEqual(normalized["item_subtype_id_candidate"], subtype_id)
                self.assertEqual(normalized["normalized_specification_candidate"], specification)
                self.assertEqual(normalized["standard_unit_candidate"], unit)
                self.assertEqual(normalized["item_group_id_candidate"], group_id)

    def test_generic_medical_waste_container_does_not_assume_material_type(self):
        normalized = normalize_alias(alias("의료폐기물 전용용기 10L"))

        self.assertEqual(normalized["item_family_id_candidate"], "MEDICAL_WASTE_CONTAINER")
        self.assertEqual(normalized["item_subtype_id_candidate"], "")
        self.assertEqual(normalized["standard_subtype_name_candidate"], "")
        self.assertEqual(normalized["normalized_specification_candidate"], "10L")
        self.assertEqual(normalized["material_candidate"], "")

    def test_sharps_waste_container_has_rigid_synthetic_resin_subtype(self):
        normalized = normalize_alias(alias("손상성 의료폐기물 전용용기 2L"))

        self.assertEqual(normalized["item_subtype_id_candidate"], "RIGID_NEEDLE_BOX")
        self.assertEqual(
            normalized["standard_subtype_name_candidate"],
            "합성수지형 용기(needle box)",
        )
        self.assertEqual(normalized["normalized_specification_candidate"], "2L")

    def test_injection_needle_container_is_waste_not_an_injection_needle(self):
        normalized = normalize_alias(alias("주사침 폐기통 2리터"))

        self.assertEqual(normalized["item_group_id_candidate"], "WASTE")
        self.assertEqual(normalized["item_family_id_candidate"], "MEDICAL_WASTE_CONTAINER")
        self.assertEqual(normalized["item_subtype_id_candidate"], "RIGID_NEEDLE_BOX")
        self.assertEqual(normalized["normalized_specification_candidate"], "2L")

        needle = normalize_alias(alias("주사침 23G"))
        self.assertEqual(needle["item_group_id_candidate"], "MED_SUPPLY")
        self.assertEqual(needle["item_family_id_candidate"], "INJECTION_NEEDLE")

    def test_lancet_spelling_variants_are_blood_lancets(self):
        for name in ["일회용 안전난셋 28G", "SafeLan 30G", "Lanset 26G"]:
            with self.subTest(name=name):
                normalized = normalize_alias(alias(name))
                self.assertEqual(normalized["item_family_id_candidate"], "BLOOD_LANCET")
                self.assertEqual(normalized["normalized_specification_candidate"], name.split()[-1])
                self.assertEqual(normalized["strength_candidate"], "")

    def test_korean_volume_units_are_normalized(self):
        liter = normalize_alias(alias("손상성 의료폐기물 용기 5리터"))
        milliliter = normalize_alias(alias("주사기 3밀리리터"))

        self.assertEqual(liter["normalized_specification_candidate"], "5L")
        self.assertEqual(milliliter["normalized_specification_candidate"], "3mL")

    def test_iv_catheters_with_gauge_use_the_angio_taxonomy(self):
        iv = normalize_alias(alias("IV카테터 22G"))
        korean_gauge = normalize_alias(alias("카테터 24게이지"))
        parenthesized_gauge = normalize_alias(alias("정맥카테터24(G)"))
        foley = normalize_alias(alias("Foley 카테터 16Fr"))

        for normalized, specification in [
            (iv, "22G"),
            (korean_gauge, "24G"),
            (parenthesized_gauge, "24G"),
        ]:
            self.assertEqual(normalized["item_family_id_candidate"], "ANGIO_CATHETER")
            self.assertEqual(normalized["item_subtype_id_candidate"], "ANGIO_NEEDLE")
            self.assertEqual(normalized["normalized_specification_candidate"], specification)
        self.assertEqual(foley["item_family_id_candidate"], "CATHETER")
        self.assertEqual(foley["normalized_specification_candidate"], "")

    def test_medical_waste_material_types_follow_explicit_name_evidence(self):
        synthetic_bag = normalize_alias(alias("의료용 폐기물전용 봉투 4L"))
        cardboard = normalize_alias(alias("의료폐기물 종이박스 10리터"))
        mixed = normalize_alias(alias("감염성폐기물박스 및 비닐 50L"))

        self.assertEqual(
            synthetic_bag["item_subtype_id_candidate"],
            "MEDICAL_WASTE_SYNTHETIC_BAG",
        )
        self.assertEqual(synthetic_bag["material_candidate"], "SYNTHETIC_RESIN")
        self.assertEqual(
            cardboard["item_subtype_id_candidate"],
            "MEDICAL_WASTE_CARDBOARD_BOX",
        )
        self.assertEqual(cardboard["material_candidate"], "PAPERBOARD")
        self.assertEqual(cardboard["normalized_specification_candidate"], "10L")
        self.assertEqual(mixed["item_family_id_candidate"], "MEDICAL_WASTE_CONTAINER")
        self.assertEqual(mixed["item_subtype_id_candidate"], "")

    def test_supply_specification_uses_the_family_measurement(self):
        syringe = normalize_alias(alias("1회용 주사기(1mL/23G)"))
        infusion_set = normalize_alias(alias("수액세트 유침 23G 1000mL"))
        fluid_container = normalize_alias(alias("수액제통 1,000mL"))

        self.assertEqual(syringe["normalized_specification_candidate"], "1mL")
        self.assertEqual(infusion_set["normalized_specification_candidate"], "수액세트")
        self.assertEqual(fluid_container["normalized_specification_candidate"], "1000mL")

    def test_needle_gauge_is_not_drug_strength_or_pack_quantity(self):
        syringe = normalize_alias(alias("주사기(3cc/23g*24mm/100개입)"))
        syringe_x = normalize_alias(alias("주사기(3cc/23gX24mm/100개입)"))
        medicine = normalize_alias(alias("세프트리악손주1G", "W123456"))

        self.assertEqual(syringe["normalized_specification_candidate"], "3mL")
        self.assertEqual(syringe["strength_candidate"], "")
        self.assertEqual(syringe["pack_quantity_candidate"], "100")
        self.assertEqual(syringe["pack_unit_candidate"], "개")
        self.assertEqual(syringe_x["strength_candidate"], "")
        self.assertEqual(syringe_x["item_family_id_candidate"], "DISPOSABLE_SYRINGE")
        self.assertEqual(medicine["strength_candidate"], "1G")

    def test_weight_is_not_misread_as_needle_gauge(self):
        gauze = normalize_alias(alias("멸균바세린거즈 8g"))
        glove = normalize_alias(alias("면장갑 35g"))
        alcohol_swab = normalize_alias(alias("알콜솜 160g/1,000mL"))

        self.assertEqual(gauze["normalized_specification_candidate"], "")
        self.assertEqual(glove["normalized_specification_candidate"], "")
        self.assertEqual(alcohol_swab["normalized_specification_candidate"], "")

    def test_waste_container_is_not_classified_as_an_injection_needle(self):
        normalized = normalize_alias(alias("폐기물통(주사침통-2L)"))

        self.assertEqual(normalized["item_group_id_candidate"], "WASTE")
        self.assertEqual(normalized["item_family_id_candidate"], "MEDICAL_WASTE_CONTAINER")
        self.assertEqual(normalized["item_subtype_id_candidate"], "RIGID_NEEDLE_BOX")
        self.assertEqual(normalized["normalized_specification_candidate"], "2L")

    def test_sterile_packed_gauze_is_not_packaging_material(self):
        normalized = normalize_alias(alias("멸균포장Y거즈 5*5"))

        self.assertEqual(normalized["item_family_id_candidate"], "MEDICAL_GAUZE")
        self.assertEqual(normalized["item_group_id_candidate"], "MED_SUPPLY")

    def test_gauze_dimensions_are_preserved_across_positions_and_separators(self):
        cases = [
            ("거즈(4*4)", "4 x 4"),
            ("거즈 5*5", "5 x 5"),
            ("거즈2*2", "2 x 2"),
            ("2*2거즈", "2 x 2"),
            ("멸균거즈 7.5cm*7.5cm", "7.5cm x 7.5cm"),
            ("거즈 10x10cm", "10cm x 10cm"),
            ("Y거즈 5×5", "5 x 5"),
            ("바세린거즈 10X10", "10 x 10"),
            ("멸균포장Y거즈 5*5", "5 x 5"),
            ("거즈 4 X 8", "4 x 8"),
        ]

        for name, specification in cases:
            with self.subTest(name=name):
                normalized = normalize_alias(alias(name))
                self.assertEqual(
                    normalized["item_family_id_candidate"],
                    "MEDICAL_GAUZE",
                )
                self.assertEqual(
                    normalized["normalized_specification_candidate"],
                    specification,
                )

    def test_known_substring_collisions_do_not_override_product_type(self):
        trental = normalize_alias(alias("트렌탈400서방정(한독약품)-1정", "W123456"))
        dishcloth = normalize_alias(alias("감염-행주", "USE0000100"))

        self.assertEqual(trental["item_group_id_candidate"], "MED_ORAL")
        self.assertNotEqual(dishcloth["item_group_id_candidate"], "MED_INJECT")

    def test_prescription_omega3_capsule_is_not_supplement(self):
        normalized = normalize_alias(
            alias("메가트리연질캡슐(오메가3산에틸에스테르90)_(1g/1캡슐)-1캡슐", "W654321")
        )

        self.assertEqual(normalized["item_group_id_candidate"], "MED_ORAL")

    def test_operational_wrapper_and_rental_program_are_separate_from_product_group(self):
        injection = normalize_alias(alias("[비급여용]삐콤헥사주사(유한양행)-1앰플", "W111111"))
        strip = normalize_alias(alias("혈당검사지(대여)", "USE0000200"))
        rental_asset = normalize_alias(alias("글루코닥터 혈당측정기 대여", "USE0000201"))

        self.assertEqual(injection["product_name_candidate"], "삐콤헥사주사(유한양행)-1앰플")
        self.assertEqual(injection["item_group_id_candidate"], "MED_INJECT")
        self.assertEqual(strip["intrinsic_item_group_id_candidate"], "LAB_REAGENT")
        self.assertEqual(strip["item_group_id_candidate"], "LAB_REAGENT")
        self.assertEqual(strip["operational_class_candidate"], "RENTAL_PROGRAM")
        self.assertEqual(rental_asset["item_group_id_candidate"], "RENTAL")

        rental_consumable = normalize_alias(alias("모유저장팩(유축기대여자)", "USE0000202"))
        self.assertNotEqual(rental_consumable["item_group_id_candidate"], "RENTAL")
        self.assertEqual(rental_consumable["operational_class_candidate"], "RENTAL_PROGRAM")

    def test_legacy_and_korean_medicine_dosage_forms_are_recognized(self):
        cases = [
            ("리나치올캅셀375mg(현대약품)-1캅셀", "MED_ORAL"),
            ("레일라정_(당귀·목과·방풍)-1정", "MED_ORAL"),
            ("한풍제통고온혈플라스타", "MED_TOPICAL"),
            ("한풍 제통고 카타플라스마", "MED_TOPICAL"),
            ("한방습포제(6매/개)", "MED_TOPICAL"),
            ("금연패취30단위", "MED_TOPICAL"),
            ("(비급여)약침(자하거)", "MED_INJECT"),
        ]

        for name, expected_group in cases:
            with self.subTest(name=name):
                self.assertEqual(normalize_alias(alias(name, "O123456"))["item_group_id_candidate"], expected_group)

    def test_unresolved_name_is_not_exposed_as_standard_family_name(self):
        normalized = normalize_alias(alias("분류근거없는품목"))

        self.assertEqual(normalized["standard_family_name_candidate"], "")
        self.assertEqual(normalized["unresolved_product_name_candidate"], "분류근거없는품목")
        self.assertEqual(normalized["normalization_status"], "unresolved")

    def test_conventional_extract_drug_is_not_korean_medicine_extract(self):
        normalized = normalize_alias(
            alias("스티렌정(애엽95%에탄올연조엑스)_(60mg/1정)-1정", "W123456")
        )

        self.assertEqual(normalized["item_group_id_candidate"], "MED_ORAL")

    def test_nonmedical_context_is_not_forced_into_medical_group(self):
        self.assertEqual(normalize_alias(alias("한약재 마사지봉 만들기 키트"))["item_group_id_candidate"], "UNCLASSIFIED")
        self.assertEqual(normalize_alias(alias("스트레칭밴드"))["item_group_id_candidate"], "UNCLASSIFIED")
        self.assertEqual(normalize_alias(alias("핸드크림"))["item_group_id_candidate"], "UNCLASSIFIED")

    def test_korean_badge_is_not_culture_media(self):
        badge = normalize_alias(alias("임산부 배지"))
        culture = normalize_alias(alias("TCBS 생배지"))
        maternal_reagent = normalize_alias(alias("임산부당부하 검사 시약"))

        self.assertNotEqual(badge["item_group_id_candidate"], "LAB_REAGENT")
        self.assertEqual(culture["item_group_id_candidate"], "LAB_REAGENT")
        self.assertEqual(maternal_reagent["item_group_id_candidate"], "LAB_REAGENT")

    def test_korean_words_ending_in_jeong_are_not_oral_dosage_forms(self):
        for name in ["혈당측정 시험지", "보건행정(칫솔)", "당화혈색소 측정 카트리지"]:
            with self.subTest(name=name):
                self.assertNotEqual(normalize_alias(alias(name))["item_group_id_candidate"], "MED_ORAL")

    def test_rental_accessory_is_not_rental_asset(self):
        normalized = normalize_alias(alias("대여용 혈압계 교체용 커프"))

        self.assertNotEqual(normalized["item_group_id_candidate"], "RENTAL")
        self.assertEqual(normalized["operational_class_candidate"], "RENTAL_PROGRAM")

    def test_generator_writes_requested_number_of_unique_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            raw_path = Path(directory) / "stock.DAT"
            output_path = Path(directory) / "sample.csv"
            with raw_path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.writer(file, delimiter="|", quotechar='"', lineterminator="\r\n")
                writer.writerow(RAW_STOCK_COLUMNS)
                writer.writerow(stock_row("[방문]혈당검사스틱", "USE0000001"))
                writer.writerow(stock_row("[방문]혈당검사스틱", "USE0000001", "2"))
                writer.writerow(stock_row("아마릴정2mg(한독약품)-1정", "WA07404061"))
                writer.writerow(stock_row("홍보물품-한방파스", "USE0000003"))

            generate_normalization_sample(raw_path.parent, raw_path.name, output_path, sample_size=3)
            with output_path.open("r", encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual(len(rows), 3)
        blood_glucose = next(row for row in rows if row["item_family_id_candidate"] == "BLOOD_GLUCOSE_TEST_STRIP")
        self.assertEqual(blood_glucose["occurrence_count"], "2")
        self.assertEqual(blood_glucose["usage_sum"], "3.0")

    def test_full_generator_preserves_all_raw_rows_and_joins_every_alias(self):
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            raw_path = directory_path / "stock.DAT"
            sample_path = directory_path / "sample.csv"
            alias_path = directory_path / "aliases.parquet"
            stock_path = directory_path / "normalized_stock.parquet"
            report_path = directory_path / "report.json"
            with raw_path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.writer(file, delimiter="|", quotechar='"', lineterminator="\r\n")
                writer.writerow(RAW_STOCK_COLUMNS)
                writer.writerow(stock_row("[방문]혈당\n검사스틱", "USE0000001", "1.25"))
                writer.writerow(stock_row("[방문]혈당\n검사스틱", "USE0000001", "2.75"))
                writer.writerow(stock_row("트렌탈400서방정-1정", "W123456", "3"))

            report = generate_full_normalization(
                raw_dir=directory_path,
                pattern=raw_path.name,
                sample_output_path=sample_path,
                alias_output_path=alias_path,
                stock_output_path=stock_path,
                report_output_path=report_path,
                sample_size=2,
            )

            aliases = pq.read_table(alias_path).to_pandas()
            stock = pq.read_table(stock_path).to_pandas()

        self.assertEqual(len(aliases), 2)
        self.assertEqual(len(stock), 3)
        self.assertEqual(stock["정상출고량"].tolist(), ["1.25", "2.75", "3"])
        self.assertEqual(report["stock_metrics"]["missing_alias_joins"], 0)
        self.assertEqual(report["alias_metrics"]["quality_gate_error_counts"], {})


if __name__ == "__main__":
    unittest.main()
