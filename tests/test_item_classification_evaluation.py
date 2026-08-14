import unittest

import pandas as pd

from src.item_classification_evaluation import (
    add_experimental_evidence_score,
    cluster_agreement_metrics,
    evaluate_approval_regression,
    evaluate_reference_clusters,
    evaluate_weight_scenarios,
)


class ItemClassificationEvaluationTest(unittest.TestCase):
    def test_cluster_metrics_measure_merge_and_split_errors(self):
        frame = pd.DataFrame(
            {
                "representative_item_id": ["A", "A", "A", "B"],
                "reference_cluster_key": ["X", "X", "Y", "Y"],
            }
        )

        metrics = cluster_agreement_metrics(frame, "test")

        self.assertAlmostEqual(metrics["pairwise_precision"], 1 / 3)
        self.assertAlmostEqual(metrics["pairwise_recall"], 1 / 2)
        self.assertAlmostEqual(metrics["pairwise_f1"], 0.4)
        self.assertAlmostEqual(metrics["bcubed_precision"], 2 / 3)
        self.assertAlmostEqual(metrics["bcubed_recall"], 3 / 4)

    def test_frozen_approval_regression_is_field_specific(self):
        baseline = pd.DataFrame(
            [
                {
                    "representative_item_id": "ITEM_A",
                    "verified_item_group_id": "MED_SUPPLY",
                    "item_family_id": "DISPOSABLE_SYRINGE",
                    "item_subtype_id": "SYRINGE_USAGE_BASED",
                    "verified_specification": "3mL",
                    "verified_unit": "EA",
                }
            ]
        )
        integrated = pd.DataFrame(
            [
                {
                    "representative_item_id": "ITEM_A",
                    "item_group_id_candidate": "MED_SUPPLY",
                    "effective_item_family_id": "DISPOSABLE_SYRINGE",
                    "effective_item_subtype_id": "SYRINGE_USAGE_BASED",
                    "effective_specification": "5mL",
                    "effective_unit_code": "EA",
                }
            ]
        )

        metrics, summary, disagreements = evaluate_approval_regression(
            baseline, integrated
        )

        specification = metrics.loc[metrics["field"].eq("specification")].iloc[0]
        self.assertEqual(specification["agreement_rate"], 0.0)
        self.assertEqual(summary["all_five_fields_exact_rate"], 0.0)
        self.assertEqual(len(disagreements), 1)

    def test_reference_attention_separates_raw_name_format_difference(self):
        aliases = pd.DataFrame(
            [
                ["I1", "C1", "A", "A", "R1"],
                ["I2", "C2", "B", "B", "R1"],
                ["I3", "C3", "C", "C", "R2"],
                ["I4", "C4", "D", "D", "R3"],
            ],
            columns=[
                "institution_id",
                "local_item_code",
                "raw_item_name",
                "product_name_candidate",
                "representative_item_id",
            ],
        )
        reference = pd.DataFrame(
            [
                ["I1", "C1", "A", "X", "A", "official"],
                ["I2", "C2", "B", "Y", "B", "official"],
                ["I3", "C3", "C", "Y", "C", "official"],
                ["I4", "C4", "D changed", "Z", "D changed", "rule"],
            ],
            columns=[
                "institution_id",
                "local_item_code",
                "raw_item_name",
                "reference_standard_id",
                "reference_standard_name",
                "reference_code_type",
            ],
        )
        names = pd.DataFrame(
            {
                "representative_item_id": ["R1", "R2", "R3"],
                "representative_name": ["A/B", "C", "D"],
            }
        )

        _, attention, summary = evaluate_reference_clusters(
            aliases, reference, names, sample_size=10
        )

        self.assertEqual(summary["missing_reference_alias_rows"], 0)
        self.assertEqual(summary["reference_raw_name_mismatch_rows"], 1)
        mismatch = attention.loc[
            attention["attention_reason"].eq("reference_raw_name_mismatch")
        ].iloc[0]
        self.assertEqual(mismatch["reference_raw_item_name"], "D changed")

    def test_experimental_weights_never_replace_current_approval_gate(self):
        integrated = pd.DataFrame(
            [
                {
                    "representative_item_id": "APPROVED",
                    "family_source": "name_rule",
                    "family_resolution_status": "name_rule_only",
                    "effective_item_family_id": "DISPOSABLE_SYRINGE",
                    "effective_item_subtype_id": "SYRINGE_USAGE_BASED",
                    "effective_specification": "3mL",
                    "effective_unit_code": "EA",
                    "classification_classification_status": "approved_external_family",
                    "classification_classification_confidence": 0.97,
                    "classification_review_status": "approved",
                    "family_conflict_flag": False,
                    "usage_sum": 10,
                },
                {
                    "representative_item_id": "RULE_ONLY",
                    "family_source": "name_rule",
                    "family_resolution_status": "name_rule_only",
                    "effective_item_family_id": "MEDICAL_GAUZE",
                    "effective_item_subtype_id": "GAUZE",
                    "effective_specification": "4 x 4",
                    "effective_unit_code": "EA",
                    "classification_classification_status": "candidate_complete",
                    "classification_classification_confidence": 0.60,
                    "classification_review_status": "needs_taxonomy_review",
                    "family_conflict_flag": False,
                    "usage_sum": 20,
                },
            ]
        )

        scored = add_experimental_evidence_score(integrated)
        scenarios = evaluate_weight_scenarios(integrated, ["APPROVED"])

        self.assertEqual(scored.loc[0, "experimental_evidence_score"], 0.97)
        production = scenarios.loc[
            scenarios["scenario"].eq("current_production_approval")
        ].iloc[0]
        self.assertEqual(production["selected_representatives"], 1)
        self.assertEqual(production["unlabelled_selected_rows"], 0)


if __name__ == "__main__":
    unittest.main()
