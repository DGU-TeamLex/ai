import unittest

import pandas as pd

from src.modeling.inventory_status import (
    build_inventory_status,
    load_inventory_status_policy,
    select_inventory_status_sample,
)


def monthly_row(
    item_code: str,
    month: str,
    on_hand: float,
    demand: float,
    *,
    physical_violations: int = 0,
    document_violations: int = 0,
) -> dict:
    return {
        "year_month": pd.Timestamp(month),
        "institution_code": "INST1",
        "department": "D1",
        "item_code": item_code,
        "item_name": item_code,
        "month_end_stock": on_hand,
        "consumption_qty": demand,
        "first_date": pd.Timestamp(month),
        "last_date": pd.Timestamp(month) + pd.offsets.MonthEnd(0),
        "model_demand_positive_sum": max(demand, 0),
        "normal_outbound_nonnegative_sum": max(demand, 0),
        "normal_outbound_squared_sum": max(demand, 0) ** 2,
        "ledger_document_rule_violation_count": document_violations,
        "ledger_physical_violation_count": physical_violations,
        "ledger_opening_stock_missing_count": 0,
        "ledger_balance_violation_count": 0,
    }


def approved_mapping(
    item_code: str,
    family: str,
    subtype: str,
    specification: str,
    *,
    forecastable: bool = True,
) -> dict:
    return {
        "local_item_key": f"INST1::{item_code}",
        "item_group_id": "MED_SUPPLY" if forecastable else "PROMO",
        "item_family_id": family,
        "standard_family_name": family,
        "item_subtype_id": subtype,
        "standard_subtype_name": subtype,
        "normalized_specification": specification,
        "unit_code": "EA",
        "unit_name": "개수",
        "is_forecastable": forecastable,
        "review_status": "approved",
        "taxonomy_version": "v1.0",
        "classification_version": "classification-v1.0",
    }


class InventoryStatusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = load_inventory_status_policy()

    def test_zero_reason_and_exact_substitution_filters_are_deterministic(self):
        monthly = pd.DataFrame(
            [
                monthly_row("NOT_USED", "2025-12-01", 0, 0),
                monthly_row(
                    "MISSING",
                    "2025-12-01",
                    0,
                    2,
                    physical_violations=1,
                    document_violations=1,
                ),
                monthly_row("STRIP_ZERO", "2025-12-01", 0, 3),
                monthly_row("STRIP_STOCK", "2025-12-01", 5, 1),
                monthly_row("NEEDLE_24_ZERO", "2025-12-01", 0, 4),
                monthly_row("NEEDLE_22_STOCK", "2025-12-01", 3, 1),
                monthly_row("STALE", "2025-11-01", 0, 1),
            ]
        )
        mapping = pd.DataFrame(
            [
                approved_mapping("STRIP_ZERO", "GLUCOSE_STRIP", "STRIP", "STANDARD"),
                approved_mapping("STRIP_STOCK", "GLUCOSE_STRIP", "STRIP", "STANDARD"),
                approved_mapping("NEEDLE_24_ZERO", "NEEDLE", "NEEDLE", "24G"),
                approved_mapping("NEEDLE_22_STOCK", "NEEDLE", "NEEDLE", "22G"),
            ]
        )

        result, report = build_inventory_status(
            monthly,
            mapping,
            policy=self.policy,
        )
        indexed = result.set_index("item_code")

        self.assertEqual(indexed.loc["NOT_USED", "zero_stock_reason"], "NOT_OPERATED")
        self.assertEqual(indexed.loc["NOT_USED", "demand_class"], "DORMANT")
        self.assertEqual(
            indexed.loc["NOT_USED", "inventory_action"],
            "FILTERED_DORMANT",
        )
        self.assertEqual(indexed.loc["MISSING", "zero_stock_reason"], "DATA_MISSING")
        self.assertTrue(indexed.loc["MISSING", "data_quality_alert"])
        self.assertEqual(
            indexed.loc["STALE", "zero_stock_reason"],
            "STALE_OR_MISSING_OBSERVATION",
        )

        covered = indexed.loc["STRIP_ZERO"]
        self.assertTrue(covered["alert_suppressed_by_exact_group_stock"])
        self.assertEqual(covered["exact_group_total_stock"], 5)
        self.assertFalse(covered["urgent_shortage"])
        self.assertEqual(
            covered["inventory_action"],
            "COVERED_BY_APPROVED_EQUIVALENT_STOCK",
        )

        different_gauge = indexed.loc["NEEDLE_24_ZERO"]
        self.assertEqual(different_gauge["exact_group_total_stock"], 0)
        self.assertTrue(different_gauge["broad_family_has_stock_display_only"])
        self.assertFalse(
            different_gauge["alert_suppressed_by_exact_group_stock"]
        )
        self.assertTrue(different_gauge["urgent_shortage"])
        self.assertEqual(different_gauge["inventory_action"], "URGENT_SHORTAGE")

        self.assertEqual(report["urgent_shortage_count"], 1)
        self.assertEqual(report["dormant_demand_count"], 1)
        self.assertEqual(report["exact_group_stock_suppression_count"], 1)
        self.assertFalse(report["pdf_reference_counts_reused_as_measured_results"])
        self.assertEqual(
            indexed.loc["STRIP_ZERO", "observation_period_days"],
            31,
        )
        self.assertAlmostEqual(
            indexed.loc["STRIP_ZERO", "raw_mean_daily_usage"],
            3 / 31,
        )
        self.assertAlmostEqual(
            indexed.loc["STRIP_ZERO", "mean_daily_usage"],
            3 / 31,
        )
        self.assertFalse(indexed.loc["STRIP_ZERO", "mu_is_floored"])
        self.assertFalse(indexed.loc["STRIP_ZERO", "sigma_is_floored"])
        self.assertEqual(report["demand_floor_application_counts"]["mean_daily_usage"], 0)
        self.assertEqual(
            report["demand_floor_application_counts"]["daily_demand_stddev"],
            0,
        )

    def test_sample_is_bounded_and_keeps_multiple_actions(self):
        rows = []
        for index in range(20):
            rows.append(
                {
                    "inventory_action": "URGENT_SHORTAGE" if index % 2 else "OK",
                    "recent_normal_outbound": index,
                    "all_time_normal_outbound": index,
                    "institution_code": "INST1",
                    "department": "D1",
                    "item_code": f"I{index:02d}",
                }
            )
        sample = select_inventory_status_sample(pd.DataFrame(rows), sample_size=7)

        self.assertEqual(len(sample), 7)
        self.assertEqual(set(sample["inventory_action"]), {"OK", "URGENT_SHORTAGE"})


if __name__ == "__main__":
    unittest.main()
