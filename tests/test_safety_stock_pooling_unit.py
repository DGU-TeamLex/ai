import unittest

import pandas as pd

from scripts.analysis.safety_stock_pooling_unit import (
    analyze_pooling_candidates,
)


class SafetyStockPoolingUnitTest(unittest.TestCase):
    def test_month_count_uses_unique_months_not_item_month_rows(self):
        panel = pd.DataFrame(
            [
                {
                    "stock_item_key": f"INST::DEPT::{item}",
                    "representative_item_id": item,
                    "standard_item_subtype_id": "SUBTYPE",
                    "standard_item_family_id": "FAMILY",
                    "standard_item_group_id": "GROUP",
                    "year_month": month,
                    "consumption_qty": demand,
                }
                for item, values in {
                    "REP_A": [10.0, 10.0],
                    "REP_B": [1.0, 20.0],
                }.items()
                for month, demand in zip(
                    pd.date_range("2025-01-01", periods=2, freq="MS"),
                    values,
                    strict=False,
                )
            ]
        )

        report = analyze_pooling_candidates(panel)

        self.assertEqual(report["기관x subtype"]["median_months"], 2)
        self.assertEqual(report["subtype"]["median_months"], 2)
        self.assertGreater(
            report["subtype"]["median_within_group_series_cv_iqr"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
