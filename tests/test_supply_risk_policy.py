import unittest

import pandas as pd

from src.module_c.supply_risk_policy import (
    calculate_level_based_safety_stock,
    derive_supply_risk_frame,
    derive_supply_risk_level,
    load_supply_risk_policy,
    validate_supply_risk_policy,
)


class SupplyRiskLevelPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = load_supply_risk_policy()

    def test_low_risk_code_is_deterministically_normal(self):
        result = derive_supply_risk_level(
            "GENERAL_LOW_RISK",
            policy=self.policy,
        )

        self.assertEqual(result["baseline_supply_risk_level"], "NORMAL")
        self.assertEqual(result["baseline_supply_risk_z"], 1.28)
        self.assertFalse(result["supply_risk_policy_needs_review"])

    def test_demand_axis_code_does_not_raise_supply_level(self):
        result = derive_supply_risk_level(
            "PANDEMIC_SURGE_SENSITIVE",
            policy=self.policy,
        )

        self.assertEqual(result["baseline_supply_risk_level"], "NORMAL")
        self.assertEqual(result["supply_risk_level_source"], "non_supply_axis_only")
        self.assertIn(
            "PANDEMIC_SURGE_SENSITIVE",
            result["ignored_event_or_demand_codes"],
        )

    def test_multiple_codes_use_highest_approved_baseline_level(self):
        result = derive_supply_risk_level(
            "GENERAL_LOW_RISK;PROPRIETARY_DEVICE_LOCKIN",
            policy=self.policy,
        )

        self.assertEqual(result["baseline_supply_risk_level"], "WARNING")

    def test_legacy_api_code_is_canonicalized(self):
        result = derive_supply_risk_level(
            "API_IMPORT_DEPENDENCY_CN_IN",
            policy=self.policy,
        )

        self.assertEqual(result["baseline_supply_risk_level"], "WARNING")
        self.assertEqual(
            result["canonical_supply_risk_meta_codes"],
            "GENERIC_API_IMPORT_DEPENDENCY",
        )

    def test_unknown_code_defaults_normal_but_requires_review(self):
        result = derive_supply_risk_level(
            "UNKNOWN_NEW_CODE",
            policy=self.policy,
        )

        self.assertEqual(result["baseline_supply_risk_level"], "NORMAL")
        self.assertEqual(result["unmapped_supply_risk_codes"], "UNKNOWN_NEW_CODE")
        self.assertTrue(result["supply_risk_policy_needs_review"])

    def test_critical_requires_all_evidence_flags_and_review_approval(self):
        base_context = {
            "is_national_essential": True,
            "is_single_import_source": True,
            "is_non_substitutable": True,
        }
        without_approval = derive_supply_risk_level(
            "ANTIBIOTIC_API_IMPORT",
            context=base_context,
            policy=self.policy,
        )
        approved = derive_supply_risk_level(
            "ANTIBIOTIC_API_IMPORT",
            context={
                **base_context,
                "critical_override_review_status": "approved",
            },
            policy=self.policy,
        )

        self.assertEqual(
            without_approval["baseline_supply_risk_level"],
            "WARNING",
        )
        self.assertEqual(approved["baseline_supply_risk_level"], "CRITICAL")
        self.assertEqual(
            approved["supply_risk_level_source"],
            "approved_critical_override",
        )

    def test_frame_rederivation_flags_stale_level(self):
        frame = pd.DataFrame(
            [
                {
                    "standard_code": "A",
                    "supply_risk_meta_code": "GENERAL_LOW_RISK",
                    "supply_risk_level": "CRITICAL",
                },
                {
                    "standard_code": "B",
                    "supply_risk_meta_code": "PROPRIETARY_DEVICE_LOCKIN",
                    "supply_risk_level": "WARNING",
                },
            ]
        )

        result = derive_supply_risk_frame(frame, policy=self.policy)

        self.assertTrue(result.iloc[0]["supply_risk_level_mismatch"])
        self.assertFalse(result.iloc[1]["supply_risk_level_mismatch"])
        self.assertEqual(
            result.groupby("supply_risk_meta_code")[
                "baseline_supply_risk_level"
            ].nunique().max(),
            1,
        )

    def test_level_based_safety_stock_uses_explicit_daily_units(self):
        result = calculate_level_based_safety_stock(
            mean_daily_usage=2.0,
            daily_demand_stddev=3.0,
            lead_time_days=20.0,
            supply_risk_level="NORMAL",
            policy=self.policy,
        )

        self.assertAlmostEqual(result["effective_lead_time_days"], 20.0)
        self.assertAlmostEqual(result["lead_time_demand"], 40.0)
        self.assertAlmostEqual(result["safety_stock"], 1.28 * 3 * (20**0.5))
        self.assertEqual(result["demand_rate_unit"], "per_day")

    def test_policy_validation_rejects_non_monotonic_z_values(self):
        invalid = {
            **self.policy,
            "levels": {
                name: values.copy()
                for name, values in self.policy["levels"].items()
            },
        }
        invalid["levels"]["CRITICAL"]["z_value"] = 1.0

        with self.assertRaisesRegex(ValueError, "monotonic"):
            validate_supply_risk_policy(invalid)


if __name__ == "__main__":
    unittest.main()
