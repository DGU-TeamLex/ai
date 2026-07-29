from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

import pandas as pd

from src.config import ITEM_MATERIAL_PIPELINE_DIR
from src.material_mapping import (
    REQUIRED_MAPPING_COLUMNS,
    attach_approved_material_mapping_metadata,
    load_approved_stock_material_mapping,
)
from src.material_pipeline import PIPELINE_VERSION, run_material_pipeline


class MaterialPipelineTest(unittest.TestCase):
    def test_upstream_rules_create_review_only_candidates(self):
        source = pd.DataFrame(
            [
                {
                    "representative_item_id": "ITEM1",
                    "representative_name": "일회용 주사기 3cc",
                    "item_group_id_candidate": "MED_SUPPLY",
                    "usage_sum": 100,
                    "occurrence_count": 20,
                    "institution_count": 5,
                    "local_codes": "USE001",
                },
                {
                    "representative_item_id": "ITEM2",
                    "representative_name": "페니라민정2mg-1정",
                    "item_group_id_candidate": "MED_ORAL",
                    "usage_sum": 50,
                    "occurrence_count": 10,
                    "institution_count": 3,
                    "local_codes": "W0002",
                },
                {
                    "representative_item_id": "ITEM3",
                    "representative_name": "확인불가 임시물품",
                    "item_group_id_candidate": "UNCLASSIFIED",
                    "usage_sum": 0,
                    "occurrence_count": 1,
                    "institution_count": 1,
                    "local_codes": "TMP003",
                },
            ]
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "items.csv"
            output_dir = root / "output"
            source.to_csv(input_path, index=False)
            report = run_material_pipeline(input_path, output_dir, with_excel=False)
            candidates = pd.read_csv(output_dir / "item_material_event_mapping_full.csv")
            glossary = pd.read_csv(output_dir / "meta_code_glossary_full.csv")

        self.assertEqual(report["rows"], 3)
        self.assertEqual(report["status"], "candidate_output_ready_for_review")
        self.assertEqual(report["operational_mapping_rows"], 0)
        self.assertNotIn("raw_material_verified", candidates.columns)
        self.assertIn("raw_material_suggested", candidates.columns)
        self.assertEqual(set(candidates["material_review_status"]), {"needs_review"})
        self.assertEqual(set(candidates["material_pipeline_version"]), {PIPELINE_VERSION})
        self.assertFalse(glossary.duplicated(["category", "meta_code"]).any())
        self.assertTrue(
            (
                glossary["meta_code"].eq("NOT_APPLICABLE")
                & glossary["category"].eq("demand_risk")
            ).any()
        )

    def test_structured_context_wins_and_gauge_remains_a_specification(self):
        source = pd.DataFrame(
            [
                {
                    "representative_item_id": "LANCET",
                    "representative_name": "Lanset 30G 100개",
                    "item_group_id_candidate": "MED_SUPPLY",
                    "item_family_id_candidate": "BLOOD_LANCET",
                    "standard_family_name_candidate": "채혈침",
                    "item_subtype_id_candidate": "BLOOD_LANCET",
                    "normalized_specification_candidate": "30G",
                    "standard_unit_candidate": "EA",
                    "needle_gauge": "30G",
                    "candidate_status": "candidate_consistent",
                    "occurrence_count": 10,
                    "institution_count": 2,
                    "usage_sum": 50,
                },
                {
                    "representative_item_id": "MASK",
                    "representative_name": "산소마스크 성인용",
                    "item_group_id_candidate": "MED_SUPPLY",
                    "item_family_id_candidate": "MEDICAL_MASK",
                    "standard_family_name_candidate": "산소마스크",
                    "candidate_status": "candidate_consistent",
                    "occurrence_count": 5,
                    "institution_count": 2,
                    "usage_sum": 20,
                },
                {
                    "representative_item_id": "WASTE",
                    "representative_name": "주사침 폐기통 2L",
                    "item_group_id_candidate": "WASTE",
                    "item_family_id_candidate": "MEDICAL_WASTE_CONTAINER",
                    "standard_family_name_candidate": "의료폐기물 전용용기",
                    "item_subtype_id_candidate": "RIGID_NEEDLE_BOX",
                    "normalized_specification_candidate": "2L",
                    "standard_unit_candidate": "EA",
                    "candidate_status": "candidate_consistent",
                    "occurrence_count": 8,
                    "institution_count": 3,
                    "usage_sum": 30,
                },
                {
                    "representative_item_id": "SWAB",
                    "representative_name": "피고셉 알코올솜 100매",
                    "item_group_id_candidate": "DISINFECT",
                    "item_family_id_candidate": "ALCOHOL_SWAB",
                    "standard_family_name_candidate": "알코올스왑",
                    "candidate_status": "candidate_consistent",
                    "occurrence_count": 12,
                    "institution_count": 3,
                    "usage_sum": 80,
                },
                {
                    "representative_item_id": "NEEDLE",
                    "representative_name": "일회용 주사침 24G 100개",
                    "item_group_id_candidate": "MED_SUPPLY",
                    "item_family_id_candidate": "INJECTION_NEEDLE",
                    "standard_family_name_candidate": "주사침",
                    "item_subtype_id_candidate": "INJECTION_NEEDLE",
                    "normalized_specification_candidate": "24G",
                    "standard_unit_candidate": "EA",
                    "needle_gauge": "24G",
                    "pack_quantity": "100",
                    "pack_unit": "EA",
                    "candidate_status": "candidate_consistent",
                    "occurrence_count": 9,
                    "institution_count": 2,
                    "usage_sum": 40,
                },
                {
                    "representative_item_id": "LANCETS_PLURAL",
                    "representative_name": "Safety Lancets 23G 100개",
                    "item_group_id_candidate": "UNCLASSIFIED",
                    "candidate_status": "candidate_incomplete",
                    "occurrence_count": 4,
                    "institution_count": 2,
                    "usage_sum": 10,
                },
                {
                    "representative_item_id": "SWAB_VARIANT",
                    "representative_name": "피고셉(알콜소독솜)",
                    "item_group_id_candidate": "UNCLASSIFIED",
                    "candidate_status": "candidate_incomplete",
                    "occurrence_count": 4,
                    "institution_count": 2,
                    "usage_sum": 10,
                },
                {
                    "representative_item_id": "GLUCOSE_SET",
                    "representative_name": "혈당소모품세트(스틱1,란셋1,알코올솜1)",
                    "item_group_id_candidate": "MED_SUPPLY",
                    "item_family_id_candidate": "BLOOD_LANCET",
                    "standard_family_name_candidate": "채혈침",
                    "candidate_status": "candidate_consistent",
                    "occurrence_count": 4,
                    "institution_count": 2,
                    "usage_sum": 10,
                },
                {
                    "representative_item_id": "STERILE_GAUZE",
                    "representative_name": "멸균거즈(개별포장)10X10",
                    "item_group_id_candidate": "MED_SUPPLY",
                    "item_family_id_candidate": "MEDICAL_GAUZE",
                    "standard_family_name_candidate": "의료용 거즈",
                    "candidate_status": "candidate_consistent",
                    "occurrence_count": 4,
                    "institution_count": 2,
                    "usage_sum": 10,
                },
                {
                    "representative_item_id": "EO_PACKAGING",
                    "representative_name": "EO가스 멸균포장재 15cm*200M",
                    "item_group_id_candidate": "MED_SUPPLY",
                    "candidate_status": "candidate_incomplete",
                    "occurrence_count": 4,
                    "institution_count": 2,
                    "usage_sum": 10,
                },
                {
                    "representative_item_id": "SYRINGE_NEEDLE",
                    "representative_name": "주사기바늘25G",
                    "item_group_id_candidate": "MED_SUPPLY",
                    "item_family_id_candidate": "INJECTION_NEEDLE",
                    "standard_family_name_candidate": "주사침",
                    "candidate_status": "candidate_consistent",
                    "occurrence_count": 4,
                    "institution_count": 2,
                    "usage_sum": 10,
                },
            ]
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "items.csv"
            attributes_path = root / "attributes.parquet"
            output_dir = root / "output"
            source.to_csv(input_path, index=False)
            source.to_parquet(attributes_path, index=False)
            report = run_material_pipeline(
                input_path,
                output_dir,
                with_excel=False,
                attributes_path=attributes_path,
            )
            candidates = pd.read_csv(
                output_dir / "item_material_event_mapping_full.csv",
                dtype=str,
                keep_default_na=False,
            ).set_index("representative_item_id")
            parents = pd.read_csv(
                output_dir / "item_parent_concept_grouping_full.csv",
                dtype=str,
                keep_default_na=False,
            ).set_index("representative_item_id")

        expected = {
            "LANCET": "BLOOD_LANCET",
            "MASK": "MEDICAL_MASK",
            "WASTE": "MEDICAL_WASTE_CONTAINER",
            "SWAB": "ALCOHOL_SWAB",
            "NEEDLE": "INJECTION_NEEDLE",
            "LANCETS_PLURAL": "BLOOD_LANCET",
            "SWAB_VARIANT": "ALCOHOL_SWAB",
            "GLUCOSE_SET": "BLOOD_GLUCOSE_TESTING_SET",
            "STERILE_GAUZE": "MEDICAL_GAUZE",
            "EO_PACKAGING": "EO_STERILIZATION_PACKAGING",
            "SYRINGE_NEEDLE": "INJECTION_NEEDLE",
        }
        self.assertEqual(candidates["item_family_id_suggested"].to_dict(), expected)
        self.assertEqual(candidates.loc["NEEDLE", "needle_gauge"], "24G")
        self.assertEqual(float(candidates.loc["NEEDLE", "pack_quantity"]), 100.0)
        self.assertNotEqual(candidates.loc["NEEDLE", "needle_gauge"], "100")
        self.assertEqual(
            candidates.loc["WASTE", "raw_material_meta_code"], "POLYPROPYLENE_PP"
        )
        self.assertEqual(
            candidates.loc["GLUCOSE_SET", "material_evidence_tier"],
            "composite_set_requires_bom",
        )
        self.assertEqual(
            candidates.loc["GLUCOSE_SET", "raw_material_meta_code"],
            "MATERIAL_UNSPECIFIED",
        )
        self.assertEqual(parents.loc["LANCET", "parent_concept_id"], "BLOOD_LANCET")
        self.assertEqual(report["sentinel_material_code_rows"], 0)
        self.assertTrue(report["quality_gates"]["parent_concept_ids_match"])

    def test_structured_family_conflict_is_preserved_for_review(self):
        source = pd.DataFrame(
            [
                {
                    "representative_item_id": "CONFLICT",
                    "representative_name": "리도카인 거즈 드레싱",
                    "item_group_id_candidate": "MED_SUPPLY",
                    "item_family_id_candidate": "MEDICAL_GAUZE",
                    "standard_family_name_candidate": "의료용 거즈",
                    "candidate_status": "candidate_consistent",
                    "occurrence_count": 3,
                    "institution_count": 2,
                    "usage_sum": 5,
                }
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "items.csv"
            attributes_path = root / "attributes.parquet"
            output_dir = root / "output"
            source.to_csv(input_path, index=False)
            source.to_parquet(attributes_path, index=False)
            run_material_pipeline(
                input_path,
                output_dir,
                attributes_path=attributes_path,
            )
            row = pd.read_csv(
                output_dir / "item_material_event_mapping_full.csv",
                dtype=str,
                keep_default_na=False,
            ).iloc[0]

        self.assertEqual(row["item_family_id_suggested"], "MEDICAL_GAUZE")
        self.assertEqual(row["name_rule_item_family_id"], "LIDOCAINE")
        self.assertEqual(row["family_conflict_flag"], "true")
        self.assertIn("preferred", row["family_resolution_status"])

    def test_only_approved_material_mappings_are_loaded(self):
        rows = [
            {
                "stock_item_key": "INST::DEPT::ITEM1",
                "item_name": "주사기",
                "item_type": "INJECTION_PHLEBOTOMY",
                "relation_type": "direct_component",
                "usage_part": "barrel",
                "related_material": "oil_plastic",
                "mapping_weight": 1.0,
                "mapping_confidence": "reviewed",
                "exposure_score": 0.8,
                "evidence_reference": "EVIDENCE-001",
                "review_status": "approved",
                "reviewer": "reviewer-1",
                "reviewed_at": "2026-07-15T09:00:00+09:00",
                "mapping_version": "v1",
                "source": "manual_review",
            },
            {
                "stock_item_key": "INST::DEPT::ITEM2",
                "item_name": "후보품목",
                "item_type": "OTHER",
                "relation_type": "",
                "usage_part": "",
                "related_material": "general_material",
                "mapping_weight": "",
                "mapping_confidence": "group_coarse",
                "exposure_score": "",
                "evidence_reference": "",
                "review_status": "needs_review",
                "reviewer": "",
                "reviewed_at": "",
                "mapping_version": "candidate-v1",
                "source": "material_pipeline",
            },
        ]
        mapping = pd.DataFrame(rows)
        for column in REQUIRED_MAPPING_COLUMNS:
            if column not in mapping.columns:
                mapping[column] = ""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.csv"
            mapping.to_csv(path, index=False)
            approved = load_approved_stock_material_mapping(path)

        self.assertEqual(len(approved), 1)
        self.assertEqual(approved.iloc[0]["stock_item_key"], "INST::DEPT::ITEM1")

    def test_approved_mapping_requires_evidence(self):
        row = {column: "value" for column in REQUIRED_MAPPING_COLUMNS}
        row.update(
            {
                "stock_item_key": "INST::DEPT::ITEM1",
                "mapping_weight": 1.0,
                "exposure_score": 0.5,
                "evidence_reference": "",
                "review_status": "approved",
                "reviewer": "reviewer-1",
                "reviewed_at": "2026-07-15T09:00:00+09:00",
            }
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.csv"
            pd.DataFrame([row]).to_csv(path, index=False)
            with self.assertRaises(ValueError):
                load_approved_stock_material_mapping(path)

    def test_prediction_metadata_ignores_unapproved_material_candidates(self):
        rows = [
            {
                "stock_item_key": "INST::DEPT::ITEM1",
                "item_name": "주사기",
                "item_type": "INJECTION_PHLEBOTOMY",
                "relation_type": "direct_component",
                "usage_part": "barrel",
                "related_material": "polypropylene",
                "raw_material_meta_code": "RM_PP",
                "raw_material_risk_meta_code": "RMR_OIL_PLASTIC",
                "demand_risk_meta_code": "DR_INJECTION",
                "mapping_weight": 1.0,
                "mapping_confidence": "reviewed",
                "exposure_score": 0.8,
                "evidence_reference": "EVIDENCE-001",
                "review_status": "approved",
                "reviewer": "reviewer-1",
                "reviewed_at": "2026-07-15T09:00:00+09:00",
                "mapping_version": "v1",
                "source": "manual_review",
            },
            {
                "stock_item_key": "INST::DEPT::ITEM2",
                "item_name": "후보품목",
                "item_type": "OTHER",
                "relation_type": "direct_component",
                "usage_part": "unknown",
                "related_material": "candidate_material",
                "mapping_weight": 1.0,
                "mapping_confidence": "candidate",
                "exposure_score": 0.5,
                "evidence_reference": "CANDIDATE-ONLY",
                "review_status": "needs_review",
                "reviewer": "",
                "reviewed_at": "",
                "mapping_version": "candidate-v1",
                "source": "material_pipeline",
            },
        ]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.csv"
            pd.DataFrame(rows).to_csv(path, index=False)
            predictions = pd.DataFrame(
                {
                    "stock_item_key": [
                        "INST::DEPT::ITEM1",
                        "INST::DEPT::ITEM2",
                    ]
                }
            )
            attached = attach_approved_material_mapping_metadata(predictions, path)

        approved = attached.iloc[0]
        candidate = attached.iloc[1]
        self.assertTrue(approved["has_approved_material_mapping"])
        self.assertEqual(approved["approved_material_mapping_count"], 1)
        self.assertEqual(approved["approved_raw_material_meta_codes"], "RM_PP")
        self.assertFalse(candidate["has_approved_material_mapping"])
        self.assertEqual(candidate["approved_material_mapping_count"], 0)

    def test_research_consolidation_preserves_existing_brand_dictionary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "data"
            research_dir = root / "research"
            data_dir.mkdir()
            research_dir.mkdir()
            pd.DataFrame(
                [
                    {
                        "keyword": "기존브랜드",
                        "family_id": "EXISTING_API",
                        "display": "기존 성분",
                        "basis": "general_knowledge_unverified",
                        "evidence": "기존 근거",
                    }
                ]
            ).to_csv(data_dir / "brand_dict_extra.tsv", sep="\t", index=False)
            (research_dir / "research_batch_0.txt").write_text(
                "신규브랜드 | NEW_API | 신규 성분 | SEARCHED | 공식 근거\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PIPE_DATA_DIR"] = str(data_dir)
            env["PIPE_RESEARCH_DIR"] = str(research_dir)
            subprocess.run(
                [
                    sys.executable,
                    str(ITEM_MATERIAL_PIPELINE_DIR / "scripts" / "consolidate_research.py"),
                ],
                check=True,
                env=env,
                capture_output=True,
                text=True,
            )
            merged = pd.read_csv(data_dir / "brand_dict_extra.tsv", sep="\t")

        self.assertEqual(set(merged["keyword"]), {"기존브랜드", "신규브랜드"})


if __name__ == "__main__":
    unittest.main()
