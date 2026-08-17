import unittest

import pandas as pd

from src.modeling.external_risk_features import add_external_risk_shock_features


class ExternalRiskShockFeatureTest(unittest.TestCase):
    def _frame(self):
        months = pd.to_datetime(["2025-01-01", "2025-02-01", "2025-03-01"] * 2)
        return pd.DataFrame(
            {
                "year_month": months,
                "series_segment_id": [1, 1, 1, 2, 2, 2],
                "stock_item_key": ["A"] * 3 + ["B"] * 3,
                "disease_news_risk": 0.0,
                "supply_news_risk": 0.0,
                "material_news_risk": 0.0,
                "total_news_risk": [0.0, 0.4, 0.8, 0.0, 0.2, 0.0],
                "commodity_risk": [0.0, 0.3, 0.7, 0.0, 0.1, 0.0],
                "material_return_30d": [0.0, 0.2, -0.3, 0.0, 0.1, 0.0],
                "material_volatility_30d": [0.1, 0.4, 0.9, 0.1, 0.2, 0.1],
            }
        )

    def test_lags_use_only_prior_rows(self):
        result = add_external_risk_shock_features(self._frame())
        series_a = result[result["series_segment_id"].eq(1)].sort_values("year_month")
        self.assertAlmostEqual(float(series_a["total_news_risk_lag_1"].iloc[2]), 0.4)
        self.assertAlmostEqual(float(series_a["material_volatility_30d_lag_2"].iloc[2]), 0.1)
        self.assertTrue(series_a["total_news_risk_lag_1"].iloc[:2].eq(0.0).all())
        self.assertTrue(series_a["material_volatility_30d_lag_2"].iloc[:2].eq(0.0).all())

    def test_zero_rows_do_not_receive_middle_rank(self):
        result = add_external_risk_shock_features(self._frame())
        march_b = result[
            result["series_segment_id"].eq(2) & result["year_month"].eq(pd.Timestamp("2025-03-01"))
        ].iloc[0]
        self.assertEqual(float(march_b["news_risk_relative_rank"]), 0.0)
        self.assertEqual(float(march_b["material_price_up_shock"]), 0.0)

    def test_noisy_or_is_bounded_and_reinforces_signals(self):
        result = add_external_risk_shock_features(self._frame())
        self.assertTrue(result["external_risk_shock_score"].between(0.0, 1.0).all())
        feb_a = result[
            result["series_segment_id"].eq(1) & result["year_month"].eq(pd.Timestamp("2025-02-01"))
        ].iloc[0]
        self.assertGreaterEqual(
            float(feb_a["external_risk_shock_score"]),
            float(feb_a["news_risk_shock"]),
        )


if __name__ == "__main__":
    unittest.main()
