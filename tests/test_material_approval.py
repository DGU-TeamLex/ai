from pathlib import Path
import tempfile
import unittest

import pandas as pd

from src.material_mapping import load_approved_stock_material_mapping
from src.module_c.material_approval import build_material_approval


POLICY = {
    "version": "test-policy-v1",
    "status": "experimental_branch_only",
    "reviewer": "test-reviewer",
    "source": "test-source",
    "rules": [
        {
            "rule_id": "SYRINGE_PP",
            "item_family_id": "DISPOSABLE_SYRINGE",
            "item_subtype_id": "SYRINGE_USAGE_BASED",
            "raw_material_meta_code": "POLYPROPYLENE_PP",
            "allowed_evidence_tiers": ["family_rule_candidate"],
            "relation_type": "direct_component",
            "usage_part": "barrel_and_plunger",
            "related_material": "polypropylene (PP)",
            "mapping_weight": 0.7,
            "mapping_confidence": "medium",
            "exposure_score": 0.7,
            "evidence_references": ["https://example.test/evidence"],
            "weight_basis": "test",
        }
    ],
}


def candidate(**overrides) -> dict:
    row = {
        "local_item_key": "INST::ITEM",
        "institution_code": "INST",
        "item_code": "ITEM",
        "representative_name": "3mL syringe",
        "item_family_id": "DISPOSABLE_SYRINGE",
        "item_subtype_id": "SYRINGE_USAGE_BASED",
        "raw_material_meta_code": "POLYPROPYLENE_PP",
        "raw_material_risk_meta_code": "PETROCHEMICAL_SHOCK",
        "material_confidence": "identified",
        "material_evidence_tier": "family_rule_candidate",
        "material_evidence_reference": "candidate://evidence",
        "classification_material_family_conflict": False,
        "market_factor_count": 2,
        "supply_risk_policy_needs_review": False,
        "usage_sum": 100.0,
    }
    row.update(overrides)
    return row


def monthly_stock() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "year_month": "2025-12-01",
                "institution_code": "INST",
                "department": department,
                "item_code": "ITEM",
                "item_name": "local syringe",
                "stock_item_key": f"INST::{department}::ITEM",
            }
            for department in ["A", "B"]
        ]
    )


class MaterialApprovalTest(unittest.TestCase):
    def test_strict_policy_approves_and_expands_departments(self):
        mapping, audit, report = build_material_approval(
            pd.DataFrame([candidate()]),
            monthly_stock(),
            POLICY,
            reviewed_at="2026-07-23T00:00:00+00:00",
        )

        self.assertEqual(len(mapping), 2)
        self.assertEqual(set(mapping["stock_item_key"]), {"INST::A::ITEM", "INST::B::ITEM"})
        self.assertTrue(mapping["review_status"].eq("approved").all())
        self.assertTrue(mapping["demand_risk_meta_code"].eq("").all())
        self.assertEqual(audit.iloc[0]["approval_reason"], "strict_policy_pass")
        self.assertEqual(report["approved_local_item_count"], 1)

    def test_policy_rejects_conflict_and_missing_market_factor(self):
        candidates = pd.DataFrame(
            [
                candidate(
                    local_item_key="INST::CONFLICT",
                    item_code="CONFLICT",
                    classification_material_family_conflict=True,
                ),
                candidate(
                    local_item_key="INST::NO_MARKET",
                    item_code="NO_MARKET",
                    market_factor_count=0,
                ),
            ]
        )
        stock = monthly_stock().iloc[0:0]

        mapping, audit, report = build_material_approval(
            candidates,
            stock,
            POLICY,
            reviewed_at="2026-07-23T00:00:00+00:00",
        )

        self.assertTrue(mapping.empty)
        self.assertEqual(
            set(audit["approval_reason"]),
            {"family_conflict", "market_factor_missing"},
        )
        self.assertEqual(report["approved_candidate_rows"], 0)

    def test_mapping_loader_accepts_separator_inside_institution_code(self):
        stock = monthly_stock().assign(
            institution_code="INST::SUB",
            stock_item_key=lambda frame: "INST::SUB::" + frame["department"] + "::ITEM",
        )
        mapping, _, _ = build_material_approval(
            pd.DataFrame(
                [
                    candidate(
                        local_item_key="INST::SUB::ITEM",
                        institution_code="INST::SUB",
                    )
                ]
            ),
            stock,
            POLICY,
            reviewed_at="2026-07-23T00:00:00+00:00",
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.csv"
            mapping.to_csv(path, index=False)
            loaded = load_approved_stock_material_mapping(path)

        self.assertEqual(len(loaded), 2)
        self.assertTrue(loaded["stock_item_key"].str.startswith("INST::SUB::").all())


if __name__ == "__main__":
    unittest.main()
