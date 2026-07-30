import unittest

import pandas as pd

from src.modeling.backtest_handoff import (
    ROW_COLUMNS,
    SEGMENT_COLUMNS,
    build_backtest_handoff,
    build_segment_handoff,
)


class BacktestHandoffTest(unittest.TestCase):
    def test_row_handoff_has_stable_unique_grain(self):
        row = {column: "" for column in ROW_COLUMNS}
        row.update(
            {
                "forecast_origin_month": "2025-09-01",
                "year_month": "2025-10-01",
                "institution_code": "A",
                "department": "D",
                "item_code": "I",
                "actual_usage": 3,
                "predicted_usage": 2.5,
            }
        )

        result = build_backtest_handoff(pd.DataFrame([row]))

        self.assertEqual(len(result), 1)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_backtest_handoff(pd.DataFrame([row, row]))

    def test_segment_handoff_rejects_duplicate_metric_rows(self):
        row = {column: 0 for column in SEGMENT_COLUMNS}
        row.update(
            {
                "segment_type": "demand_pattern",
                "segment_value": "smooth",
                "model": "predicted_usage",
            }
        )

        result = build_segment_handoff(pd.DataFrame([row]))

        self.assertEqual(len(result), 1)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_segment_handoff(pd.DataFrame([row, row]))


if __name__ == "__main__":
    unittest.main()
