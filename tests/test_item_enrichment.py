from pathlib import Path
import tempfile
import unittest

import pandas as pd

from src.item_enrichment import (
    build_product_worklist,
    discover_official_master_paths,
    match_official_masters,
    normalize_match_name,
    parse_data_go_response,
    validate_official_csv,
)


class ItemEnrichmentTest(unittest.TestCase):
    def test_master_discovery_ignores_other_official_evidence_caches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = root / "mfds_drug_permit.parquet"
            expected.touch()
            (root / "mfds_nedrug_web.parquet").touch()

            discovered = discover_official_master_paths(root)

        self.assertEqual(discovered, [expected])

    def test_match_name_normalizes_units_pack_suffix_and_parentheses(self):
        self.assertEqual(normalize_match_name("아마릴정 2밀리그램-1정"), "아마릴정2mg1정")
        self.assertEqual(
            normalize_match_name(
                "아마릴정2mg(한독약품)-1정",
                remove_parenthetical=True,
                remove_trailing_pack=True,
            ),
            "아마릴정2mg",
        )
        self.assertNotEqual(normalize_match_name("란셋 100개"), normalize_match_name("란셋 200개"))
        self.assertEqual(normalize_match_name("멸균거즈 4*4"), "멸균거즈4x4")
        self.assertNotEqual(normalize_match_name("0.5mg"), normalize_match_name("05mg"))

    def test_different_syringe_capacities_keep_distinct_representative_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            aliases = pd.DataFrame(
                [
                    self._alias("A", "USE1", "주사기 3cc", "주사기 3cc", 10),
                    self._alias("A", "USE2", "주사기 5cc", "주사기 5cc", 10),
                ]
            )
            alias_path = root / "aliases.parquet"
            worklist_path = root / "worklist.parquet"
            links_path = root / "links.parquet"
            aliases.to_parquet(alias_path, index=False)

            result = build_product_worklist(alias_path, worklist_path, links_path)
            links = pd.read_parquet(links_path)

        self.assertEqual(result["representative_items"], 2)
        self.assertEqual(links["representative_item_id"].nunique(), 2)

    def test_build_worklist_groups_same_product_across_institutions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            aliases = pd.DataFrame(
                [
                    self._alias("A", "USE1", "[방문]혈당스틱", "혈당스틱", 10),
                    self._alias("B", "USE9", "혈당스틱", "혈당스틱", 20),
                ]
            )
            alias_path = root / "aliases.parquet"
            worklist_path = root / "worklist.parquet"
            links_path = root / "links.parquet"
            aliases.to_parquet(alias_path, index=False)

            result = build_product_worklist(alias_path, worklist_path, links_path)
            worklist = pd.read_parquet(worklist_path)
            links = pd.read_parquet(links_path)

        self.assertEqual(result["representative_items"], 1)
        self.assertEqual(len(worklist), 1)
        self.assertEqual(worklist.iloc[0]["institution_count"], 2)
        self.assertEqual(worklist.iloc[0]["occurrence_count"], 30)
        self.assertEqual(links["representative_item_id"].nunique(), 1)

    def test_binary_file_is_rejected_as_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "official.csv"
            path.write_bytes(b"\xff\xd8\xff\xe0not-a-csv")
            with self.assertRaisesRegex(ValueError, "binary file"):
                validate_official_csv(path, {"한글상품명"})

    def test_data_go_json_response_is_parsed(self):
        body = (
            b'{"response":{"header":{"resultCode":"00"},"body":'
            b'{"items":{"item":[{"ITEM_SEQ":"1","ITEM_NAME":"test"}]},'
            b'"totalCount":1}}}'
        )
        items, total = parse_data_go_response(body, "application/json")
        self.assertEqual(total, 1)
        self.assertEqual(items[0]["ITEM_SEQ"], "1")

    def test_only_unique_official_code_is_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worklist = pd.DataFrame(
                [
                    self._worklist("ITEM_1", "아마릴정2mg", "OFFICIAL100"),
                    self._worklist("ITEM_2", "중복제품"),
                    self._worklist("ITEM_3", "이름만일치"),
                ]
            )
            master = pd.DataFrame(
                [
                    self._master("100", "아마릴정2mg", "OFFICIAL100"),
                    self._master("200", "중복제품"),
                    self._master("201", "중복제품"),
                    self._master("300", "이름만일치"),
                ]
            )
            worklist_path = root / "worklist.parquet"
            master_path = root / "master.parquet"
            grouped_path = root / "grouped.parquet"
            review_path = root / "review.csv"
            sample_path = root / "sample.csv"
            worklist.to_parquet(worklist_path, index=False)
            master.to_parquet(master_path, index=False)

            match_official_masters(
                worklist_path,
                [master_path],
                grouped_path,
                review_path,
                sample_path,
                sample_size=10,
            )
            grouped = pd.read_parquet(grouped_path).set_index("representative_item_id")
            sample_columns = pd.read_csv(sample_path, encoding="utf-8-sig").columns

        self.assertEqual(grouped.loc["ITEM_1", "verification_status"], "verified_identity")
        self.assertEqual(grouped.loc["ITEM_1", "canonical_item_id"], "mfds_drug_permit::100")
        self.assertEqual(grouped.loc["ITEM_2", "verification_status"], "ambiguous")
        self.assertEqual(grouped.loc["ITEM_2", "canonical_item_id"], "")
        self.assertEqual(grouped.loc["ITEM_3", "verification_status"], "candidate_identity")
        self.assertEqual(grouped.loc["ITEM_3", "canonical_item_id"], "")
        self.assertEqual(
            grouped.loc["ITEM_3", "canonical_item_id_candidate"],
            "mfds_drug_permit::300",
        )
        self.assertIn("item_group_id_candidate", sample_columns)

    @staticmethod
    def _alias(institution: str, code: str, raw_name: str, product_name: str, count: int):
        return {
            "institution_id": institution,
            "local_item_code": code,
            "local_item_key": f"{institution}::{code}",
            "raw_item_name": raw_name,
            "product_name_candidate": product_name,
            "item_group_id_candidate": "LAB_REAGENT",
            "item_family_id_candidate": "BLOOD_GLUCOSE_TEST_STRIP",
            "standard_family_name_candidate": "혈당검사지",
            "item_subtype_id_candidate": "BLOOD_GLUCOSE_TEST_STRIP",
            "standard_subtype_name_candidate": "혈당검사지",
            "normalized_specification_candidate": "",
            "standard_unit_candidate": "",
            "dosage_form_candidate": "",
            "strength_candidate": "",
            "pack_quantity_candidate": "",
            "pack_unit_candidate": "",
            "occurrence_count": count,
            "usage_sum": float(count),
        }

    @staticmethod
    def _worklist(item_id: str, name: str, local_code: str = ""):
        return {
            "representative_item_id": item_id,
            "representative_name": name,
            "match_name_strict": normalize_match_name(name),
            "match_name_core": normalize_match_name(name, remove_parenthetical=True),
            "item_group_id_candidate": "MED_ORAL",
            "occurrence_count": 1,
            "local_codes": local_code,
            "canonical_item_id_candidate": "",
            "canonical_item_id": "",
            "matched_source_item_name": "",
            "verified_item_name": "",
            "evidence_source": "",
            "evidence_record_id": "",
            "evidence_url": "",
            "retrieved_at": "",
            "match_method": "",
            "match_score": 0.0,
            "verification_status": "not_verified",
            "review_status": "needs_external_evidence",
        }

    @staticmethod
    def _master(record_id: str, name: str, source_code: str = ""):
        return {
            "source_id": "mfds_drug_permit",
            "source_title": "MFDS",
            "source_record_id": record_id,
            "source_item_name": name,
            "source_company": "제약사",
            "source_code": source_code,
            "match_name_strict": normalize_match_name(name),
            "match_name_core": normalize_match_name(name, remove_parenthetical=True),
            "group_scope": "MED_ORAL;MED_INJECT;MED_TOPICAL",
            "evidence_url": "https://www.data.go.kr/data/15095677/openapi.do",
            "retrieved_at": "2026-07-13T00:00:00+00:00",
        }


if __name__ == "__main__":
    unittest.main()
