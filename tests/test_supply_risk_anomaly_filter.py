from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from src.module_c.supply_risk_anomaly_filter import (
    filter_supply_risk_records,
    load_anomaly_rules,
    main,
    select_supply_risk_quality_sample,
)
from src.module_c.supply_risk_policy import (
    calculate_level_based_safety_stock,
    load_supply_risk_policy,
)


class SupplyRiskAnomalyFilterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = load_supply_risk_policy()
        cls.rules = load_anomaly_rules()

    def valid_row(self, standard_code: str = "VALID") -> dict:
        stock = calculate_level_based_safety_stock(
            mean_daily_usage=2.0,
            daily_demand_stddev=3.0,
            lead_time_days=20.0,
            supply_risk_level="WARNING",
            policy=self.policy,
        )
        return {
            "standard_code": standard_code,
            "supply_risk_meta_code": "PROPRIETARY_DEVICE_LOCKIN",
            "supply_risk_level": "WARNING",
            "supply_risk_policy_version": self.policy["version"],
            "z_value": 2.05,
            "lead_time_multiplier": 1.25,
            "mean_daily_usage": 2.0,
            "daily_demand_stddev": 3.0,
            "lead_time_days": 20.0,
            "ss": stock["safety_stock"],
            "rop": stock["reorder_point"],
            "demand_rate_unit": "per_day",
            "demand_stddev_unit": "per_sqrt_day",
            "inventory_policy_method": "level_based_daily_ss_rop",
            "module_c_policy_applied": False,
            "risk_buffer": 0.0,
        }

    def test_valid_operational_row_passes(self):
        classified, issues, passed, review, quarantine, report = (
            filter_supply_risk_records(
                pd.DataFrame([self.valid_row()]),
                operational_mode=True,
                policy=self.policy,
                rules=self.rules,
            )
        )

        self.assertEqual(classified.iloc[0]["quality_status"], "PASS")
        self.assertTrue(issues.empty)
        self.assertEqual(len(passed), 1)
        self.assertTrue(review.empty)
        self.assertTrue(quarantine.empty)
        self.assertEqual(report["operationally_eligible_rows"], 1)
        self.assertTrue(report["batch_release_allowed"])

    def test_stale_critical_row_is_quarantined_with_related_errors(self):
        row = self.valid_row("STALE")
        row.update(
            {
                "supply_risk_meta_code": "GENERAL_LOW_RISK",
                "supply_risk_level": "CRITICAL",
                "z_value": 2.33,
                "lead_time_multiplier": 1.5,
            }
        )

        classified, issues, _, _, quarantine, _ = filter_supply_risk_records(
            pd.DataFrame([row]),
            operational_mode=True,
            policy=self.policy,
            rules=self.rules,
        )
        codes = set(issues["issue_code"])

        self.assertEqual(classified.iloc[0]["quality_status"], "BLOCK")
        self.assertEqual(len(quarantine), 1)
        self.assertIn("SR001_LEVEL_POLICY_MISMATCH", codes)
        self.assertIn("SR004_UNAPPROVED_CRITICAL", codes)
        self.assertIn("SR006_Z_VALUE_MISMATCH", codes)
        self.assertIn("SR007_LEAD_TIME_MULTIPLIER_MISMATCH", codes)
        self.assertIn("SR008_SAFETY_STOCK_RECALC_MISMATCH", codes)
        self.assertIn("SR009_ROP_RECALC_MISMATCH", codes)

    def test_demand_axis_contamination_is_blocked(self):
        row = self.valid_row("DEMAND_AXIS")
        row.update(
            {
                "supply_risk_meta_code": "PANDEMIC_SURGE_SENSITIVE",
                "supply_risk_level": "WARNING",
            }
        )

        _, issues, _, _, quarantine, _ = filter_supply_risk_records(
            pd.DataFrame([row]),
            policy=self.policy,
            rules=self.rules,
        )
        codes = set(issues["issue_code"])

        self.assertEqual(len(quarantine), 1)
        self.assertIn("SR002_DEMAND_AXIS_IN_SUPPLY_LEVEL", codes)
        self.assertIn("SR019_NON_SUPPLY_CODE_IN_BASELINE", codes)

    def test_unknown_code_is_blocked_and_legacy_alias_requires_review(self):
        unknown = self.valid_row("UNKNOWN")
        unknown.update(
            {
                "supply_risk_meta_code": "NEW_UNKNOWN_CODE",
                "supply_risk_level": "NORMAL",
                "z_value": 1.28,
                "lead_time_multiplier": 1.0,
            }
        )
        legacy = self.valid_row("LEGACY")
        legacy.update(
            {
                "supply_risk_meta_code": "API_IMPORT_DEPENDENCY_CN_IN",
                "supply_risk_level": "WARNING",
            }
        )

        classified, issues, _, review, quarantine, _ = filter_supply_risk_records(
            pd.DataFrame([unknown, legacy]),
            policy=self.policy,
            rules=self.rules,
        )

        self.assertEqual(
            classified.set_index("standard_code").loc["UNKNOWN", "quality_status"],
            "BLOCK",
        )
        self.assertEqual(
            classified.set_index("standard_code").loc["LEGACY", "quality_status"],
            "REVIEW",
        )
        self.assertEqual(len(quarantine), 1)
        self.assertEqual(len(review), 1)
        self.assertIn("SR003_UNMAPPED_META_CODE", set(issues["issue_code"]))
        self.assertIn("SR018_LEGACY_CODE_ALIAS_USED", set(issues["issue_code"]))

    def test_same_code_with_multiple_stored_levels_is_blocked(self):
        normal = self.valid_row("NORMAL")
        normal.update(
            {
                "supply_risk_meta_code": "GENERAL_LOW_RISK",
                "supply_risk_level": "NORMAL",
                "z_value": 1.28,
                "lead_time_multiplier": 1.0,
            }
        )
        critical = {**normal, "standard_code": "CRITICAL", "supply_risk_level": "CRITICAL"}

        classified, issues, _, _, _, report = filter_supply_risk_records(
            pd.DataFrame([normal, critical]),
            policy=self.policy,
            rules=self.rules,
        )

        conflict_rows = issues[
            issues["issue_code"].eq("SR013_CODE_HAS_MULTIPLE_BASELINE_LEVELS")
        ]
        self.assertEqual(len(conflict_rows), 2)
        self.assertTrue(classified["quality_status"].eq("BLOCK").all())
        self.assertIn("SR014_EXCESSIVE_CRITICAL_SHARE", report["dataset_issue_codes"])

    def test_double_inventory_policy_is_blocked(self):
        row = self.valid_row("DOUBLE")
        row["module_c_policy_applied"] = True
        row["risk_buffer"] = 20.0

        _, issues, _, _, quarantine, _ = filter_supply_risk_records(
            pd.DataFrame([row]),
            policy=self.policy,
            rules=self.rules,
        )

        self.assertEqual(len(quarantine), 1)
        self.assertIn("SR016_DOUBLE_INVENTORY_POLICY", set(issues["issue_code"]))

    def test_operational_mode_blocks_missing_required_contract(self):
        frame = pd.DataFrame(
            [
                {
                    "standard_code": "MISSING",
                    "supply_risk_meta_code": "GENERAL_LOW_RISK",
                }
            ]
        )

        _, issues, _, _, quarantine, report = filter_supply_risk_records(
            frame,
            operational_mode=True,
            policy=self.policy,
            rules=self.rules,
        )

        self.assertEqual(len(quarantine), 1)
        self.assertIn(
            "SR021_REQUIRED_OPERATIONAL_FIELD_MISSING",
            set(issues["issue_code"]),
        )
        self.assertIn(
            "SR021_REQUIRED_OPERATIONAL_FIELD_MISSING",
            report["dataset_issue_codes"],
        )
        self.assertFalse(report["batch_release_allowed"])

    def test_cli_require_release_exits_nonzero_after_writing_quarantine(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.csv"
            output_dir = root / "quality"
            pd.DataFrame(
                [
                    {
                        "standard_code": "BAD",
                        "supply_risk_meta_code": "UNKNOWN_CODE",
                    }
                ]
            ).to_csv(input_path, index=False)
            argv = [
                "supply-risk-filter",
                "--input",
                str(input_path),
                "--output-dir",
                str(output_dir),
                "--require-release",
            ]

            with patch("sys.argv", argv), self.assertRaises(SystemExit) as error:
                main()

            self.assertEqual(error.exception.code, 2)
            self.assertTrue((output_dir / "supply_risk_quality_quarantine.csv").exists())
            self.assertTrue((output_dir / "supply_risk_quality_report.json").exists())
            self.assertTrue((output_dir / "supply_risk_quality_sample_1000.csv").exists())

    def test_review_sample_is_deterministic_balanced_and_readable(self):
        rows = []
        for index in range(2):
            rows.append(
                {
                    "quality_record_key": f"BLOCK-{index}",
                    "quality_status": "BLOCK",
                    "quality_primary_issue_code": "SR003_UNMAPPED_META_CODE",
                    "quality_issue_codes": "SR003_UNMAPPED_META_CODE",
                    "usage_sum": 100 - index,
                }
            )
        for index in range(8):
            issue_code = (
                "SR018_LEGACY_CODE_ALIAS_USED"
                if index % 2 == 0
                else "SR019_NON_SUPPLY_CODE_IN_BASELINE"
            )
            rows.append(
                {
                    "quality_record_key": f"REVIEW-{index}",
                    "quality_status": "REVIEW",
                    "quality_primary_issue_code": issue_code,
                    "quality_issue_codes": issue_code,
                    "usage_sum": 50 - index,
                }
            )
        for index in range(2):
            rows.append(
                {
                    "quality_record_key": f"PASS-{index}",
                    "quality_status": "PASS",
                    "quality_primary_issue_code": "",
                    "quality_issue_codes": "",
                    "usage_sum": index,
                }
            )
        classified = pd.DataFrame(rows)

        first = select_supply_risk_quality_sample(
            classified,
            sample_size=8,
            rules=self.rules,
        )
        second = select_supply_risk_quality_sample(
            classified,
            sample_size=8,
            rules=self.rules,
        )

        pd.testing.assert_frame_equal(first, second)
        self.assertEqual(len(first), 8)
        self.assertTrue(first["quality_record_key"].is_unique)
        self.assertEqual(
            set(classified.loc[classified["quality_status"].eq("BLOCK"), "quality_record_key"]),
            set(first.loc[first["quality_status"].eq("BLOCK"), "quality_record_key"]),
        )
        self.assertEqual(set(first["quality_status"]), {"BLOCK", "REVIEW", "PASS"})
        self.assertIn("quality_primary_issue_description", first.columns)
        self.assertIn("quality_recommended_action", first.columns)
        self.assertEqual(first["quality_sample_rank"].tolist(), list(range(1, 9)))

    def test_review_sample_prefers_representative_item_diversity(self):
        rows = [
            {
                "quality_record_key": f"BLOCK-{index}",
                "representative_item_id": "ITEM-BLOCK",
                "quality_status": "BLOCK",
                "quality_primary_issue_code": "SR003_UNMAPPED_META_CODE",
            }
            for index in range(5)
        ]
        rows.extend(
            {
                "quality_record_key": f"REVIEW-{index}",
                "representative_item_id": f"ITEM-REVIEW-{index}",
                "quality_status": "REVIEW",
                "quality_primary_issue_code": "SR019_NON_SUPPLY_CODE_IN_BASELINE",
            }
            for index in range(5)
        )

        sample = select_supply_risk_quality_sample(
            pd.DataFrame(rows),
            sample_size=4,
            rules=self.rules,
        )

        self.assertEqual(sample["representative_item_id"].nunique(), 4)
        self.assertIn("ITEM-BLOCK", set(sample["representative_item_id"]))


if __name__ == "__main__":
    unittest.main()
