from pathlib import Path
import tempfile
import unittest

import pandas as pd

from src.item_classification import (
    TAXONOMY_COLUMNS,
    build_approved_taxonomy,
    build_local_classifications,
    build_manual_decisions,
    build_representative_classifications,
    parse_nedrug_html,
)
from src.item_enrichment import normalize_match_name


def worklist_row(
    item_id: str,
    name: str,
    group_id: str,
    family_id: str = "",
    family_name: str = "",
    subtype_id: str = "",
    subtype_name: str = "",
    specification: str = "",
    unit_code: str = "",
    dosage_form: str = "",
    strength: str = "",
    pack_unit: str = "",
) -> dict:
    return {
        "representative_item_id": item_id,
        "representative_name": name,
        "match_name_core": normalize_match_name(
            name,
            remove_parenthetical=True,
            remove_trailing_pack=True,
        ),
        "item_group_id_candidate": group_id,
        "item_group_candidates": group_id,
        "item_family_id_candidate": family_id,
        "standard_family_name_candidate": family_name,
        "item_subtype_id_candidate": subtype_id,
        "standard_subtype_name_candidate": subtype_name,
        "normalized_specification_candidate": specification,
        "standard_unit_candidate": unit_code,
        "dosage_form_candidate": dosage_form,
        "strength_candidate": strength,
        "pack_unit_candidate": pack_unit,
        "candidate_status": "candidate_consistent",
        "raw_name_examples": name,
        "local_code_examples": "ITEM1",
        "institution_count": 1,
        "occurrence_count": 10,
        "usage_sum": 100.0,
    }


def suggestion_row(
    item_id: str,
    family_id: str = "UNSPECIFIED_ITEM",
    family_name: str = "미분류",
    basis: str = "unresolved",
    evidence_note: str = "",
    dosage_form: str = "",
) -> dict:
    return {
        "representative_item_id": item_id,
        "item_family_id_suggested": family_id,
        "standard_family_name_suggested": family_name,
        "family_basis": basis,
        "evidence_note": evidence_note,
        "dosage_form_suggested": dosage_form,
        "retrieved_at": "2026-07-15T00:00:00+00:00",
        "family_review_status": "needs_review",
    }


class ItemClassificationTest(unittest.TestCase):
    def test_taxonomy_rebuild_removes_stale_generated_rows_but_keeps_seeds(self):
        classification = build_representative_classifications(
            pd.DataFrame(
                [
                    worklist_row(
                        "CURRENT",
                        "주사기 3cc",
                        "MED_SUPPLY",
                        "DISPOSABLE_SYRINGE",
                        "주사기",
                        "SYRINGE_USAGE_BASED",
                        "주사기(사용량 기준)",
                        "3mL",
                        "EA",
                    )
                ]
            ),
            pd.DataFrame([suggestion_row("CURRENT")]),
            pd.DataFrame(),
            reviewed_at="2026-07-15T01:00:00+00:00",
        )
        decisions = build_manual_decisions(classification)

        def taxonomy_row(
            family_id: str,
            version: str,
            status: str,
            material_status: str,
        ) -> dict[str, str]:
            return {
                "source_item_name": family_id,
                "source_subtype_name": "세부유형",
                "source_specification": "1EA",
                "item_family_id": family_id,
                "standard_family_name": family_id,
                "item_subtype_id": "SUBTYPE",
                "standard_subtype_name": "세부유형",
                "item_group_id": "MED_SUPPLY",
                "is_forecastable": "t",
                "normalized_specification": "1EA",
                "unit_code": "EA",
                "unit_name": "개수",
                "material_candidate": "",
                "material_mapping_status": material_status,
                "review_status": status,
                "taxonomy_version": version,
            }

        existing = pd.DataFrame(
            [
                taxonomy_row(
                    "STALE_GENERATED",
                    "v1.0",
                    "approved",
                    "separate_approved_mapping_required",
                ),
                taxonomy_row("SEED", "v0.2", "candidate", "needs_evidence"),
                taxonomy_row("MANUAL", "v1.0", "approved", "verified"),
            ],
            columns=TAXONOMY_COLUMNS,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "taxonomy.csv"
            existing.to_csv(path, index=False)
            rebuilt = build_approved_taxonomy(decisions, path)

        families = set(rebuilt["item_family_id"])
        self.assertNotIn("STALE_GENERATED", families)
        self.assertIn("SEED", families)
        self.assertIn("MANUAL", families)
        self.assertIn("DISPOSABLE_SYRINGE", families)

    def test_nedrug_html_requires_product_heading_and_item_code(self):
        html = (
            "<html><body><h1>리나치올캡슐375밀리그램(카르보시스테인)</h1>"
            "<div>품목기준코드 197800466</div></body></html>"
        ).encode("utf-8")

        parsed = parse_nedrug_html(html, "197800466")

        self.assertIn("리나치올캡슐", parsed["source_item_name"])
        with self.assertRaises(ValueError):
            parse_nedrug_html(html, "000000000")

    def test_official_family_and_drug_evidence_create_approved_decisions(self):
        syringe = worklist_row(
            "ITEM_SYRINGE",
            "주사기 3cc",
            "MED_SUPPLY",
            "DISPOSABLE_SYRINGE",
            "주사기",
            "SYRINGE_USAGE_BASED",
            "주사기(사용량 기준)",
            "3mL",
            "EA",
        )
        drug_name = "리나치올캅셀375mg(현대약품)-1캅셀"
        drug = worklist_row(
            "ITEM_DRUG",
            drug_name,
            "MED_ORAL",
            dosage_form="캡슐",
            strength="375mg",
            pack_unit="캅셀",
        )
        worklist = pd.DataFrame([syringe, drug])
        suggestions = pd.DataFrame(
            [
                suggestion_row("ITEM_SYRINGE"),
                suggestion_row(
                    "ITEM_DRUG",
                    "CARBOCISTEINE",
                    "카르보시스테인(거담제)",
                    "web_search_2026_07_15",
                    (
                        "공식 근거 — https://nedrug.mfds.go.kr/pbp/CCBBB01/"
                        "getItemDetail?itemSeq=197800466"
                    ),
                    "캡슐",
                ),
            ]
        )
        official = pd.DataFrame(
            [
                {
                    "item_seq": "197800466",
                    "source_item_name": "리나치올캡슐375밀리그램(카르보시스테인)",
                    "source_url": (
                        "https://nedrug.mfds.go.kr/pbp/CCBBB01/"
                        "getItemDetail?itemSeq=197800466"
                    ),
                    "retrieved_at": "2026-07-15T00:00:00+00:00",
                    "verified_family_ids": "CARBOCISTEINE",
                    "verified_strength_tokens": "375mg",
                }
            ]
        )

        classified = build_representative_classifications(
            worklist,
            suggestions,
            official,
            reviewed_at="2026-07-15T01:00:00+00:00",
        ).set_index("representative_item_id")
        decisions = build_manual_decisions(classified.reset_index())

        self.assertEqual(
            classified.loc["ITEM_SYRINGE", "classification_status"],
            "approved_external_family",
        )
        self.assertEqual(
            classified.loc["ITEM_DRUG", "classification_status"],
            "approved_external_item",
        )
        self.assertEqual(classified.loc["ITEM_DRUG", "selected_unit_code"], "CAPSULE")
        self.assertEqual(len(decisions), 2)

    def test_local_item_with_two_taxonomies_is_not_approved(self):
        classifications = pd.DataFrame(
            [
                {
                    "representative_item_id": "REP1",
                    "classification_status": "approved_external_family",
                    "review_status": "approved",
                    "item_group_id_candidate": "MED_SUPPLY",
                    "selected_item_family_id": "DISPOSABLE_SYRINGE",
                    "selected_standard_family_name": "주사기",
                    "selected_item_subtype_id": "SYRINGE_USAGE_BASED",
                    "selected_standard_subtype_name": "주사기(사용량 기준)",
                    "selected_specification": "3mL",
                    "selected_unit_code": "EA",
                    "decision_action": "APPROVE_FAMILY",
                    "reviewer": "reviewer",
                    "reviewed_at": "2026-07-15T01:00:00+00:00",
                    "evidence_source": "official",
                    "evidence_record_id": "A",
                    "evidence_url": "https://example.test/a",
                },
                {
                    "representative_item_id": "REP2",
                    "classification_status": "approved_external_family",
                    "review_status": "approved",
                    "item_group_id_candidate": "MED_SUPPLY",
                    "selected_item_family_id": "INJECTION_NEEDLE",
                    "selected_standard_family_name": "주사침",
                    "selected_item_subtype_id": "INJECTION_NEEDLE",
                    "selected_standard_subtype_name": "주사침",
                    "selected_specification": "23G",
                    "selected_unit_code": "EA",
                    "decision_action": "APPROVE_FAMILY",
                    "reviewer": "reviewer",
                    "reviewed_at": "2026-07-15T01:00:00+00:00",
                    "evidence_source": "official",
                    "evidence_record_id": "B",
                    "evidence_url": "https://example.test/b",
                },
            ]
        )
        links = pd.DataFrame(
            [
                {
                    "institution_id": "INST",
                    "local_item_code": "CONFLICT",
                    "local_item_key": "INST::CONFLICT",
                    "representative_item_id": "REP1",
                },
                {
                    "institution_id": "INST",
                    "local_item_code": "CONFLICT",
                    "local_item_key": "INST::CONFLICT",
                    "representative_item_id": "REP2",
                },
                {
                    "institution_id": "INST",
                    "local_item_code": "SAFE",
                    "local_item_key": "INST::SAFE",
                    "representative_item_id": "REP1",
                },
            ]
        )

        local, approved = build_local_classifications(links, classifications)

        status = local.set_index("local_item_key")["local_review_status"]
        self.assertEqual(status["INST::CONFLICT"], "needs_review")
        self.assertEqual(status["INST::SAFE"], "approved")
        self.assertEqual(approved["local_item_key"].tolist(), ["INST::SAFE"])


if __name__ == "__main__":
    unittest.main()
