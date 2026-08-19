import unittest

import numpy as np
import pandas as pd

from scripts.analysis.syringe_commodity_relationship import (
    _json_safe,
    _within_item_association,
    bias_pct,
    wape,
)


class SyringeCommodityRelationshipTest(unittest.TestCase):
    def test_metrics_use_signed_total_bias(self):
        actual = pd.Series([10.0, 20.0])
        prediction = pd.Series([8.0, 24.0])
        self.assertAlmostEqual(wape(actual, prediction), 20.0)
        self.assertAlmostEqual(bias_pct(actual, prediction), 100.0 * 2.0 / 30.0)

    def test_within_item_correlation_removes_item_level_means(self):
        frame = pd.DataFrame(
            {
                "stock_item_key": ["a", "a", "b", "b"],
                "commodity_risk": [0.0, 1.0, 10.0, 11.0],
                "actual": [2.0, 4.0, 100.0, 102.0],
                "commodity_signal_connected": [False, True, False, True],
            }
        )
        result = _within_item_association(frame)
        self.assertAlmostEqual(result["within_item_risk_actual_corr"], 1.0)
        self.assertEqual(result["items_with_both_signal_states"], 2)
        self.assertAlmostEqual(
            result["mean_item_demand_difference_signal_minus_no_signal"], 2.0
        )

    def test_json_safe_converts_non_finite_values_to_null(self):
        result = _json_safe({"nan": float("nan"), "value": np.float64(1.25)})
        self.assertIsNone(result["nan"])
        self.assertEqual(result["value"], 1.25)


if __name__ == "__main__":
    unittest.main()
