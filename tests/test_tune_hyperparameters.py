import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from src.config import HISTORICAL_TRAIN_END, VALIDATION_FOLDS
from src.modeling import tune_hyperparameters as tuning


def _feature_table() -> pd.DataFrame:
    """TEST 구간이 없는 표를 만든다.

    _load_feature_table() 이 VALID_END 까지만 읽는 실제 동작을 그대로 흉내낸다.
    과거자료(2018~19)와 현재자료(2024-01~2025-06)만 들어간다.
    """
    months = (
        list(pd.date_range("2018-01-01", "2019-12-01", freq="MS"))
        + list(pd.date_range("2024-01-01", "2025-06-01", freq="MS"))
    )
    rows = []
    for series in range(4):
        for month in months:
            rows.append(
                {
                    "year_month": month,
                    "target_usage": float(series + month.month),
                    "lag_1": float(series + 1),
                    "rolling_mean_3": float(series + 2),
                    "historical_training_eligible": True,
                    "disease_news_risk": 0.0,
                    "supply_news_risk": 0.0,
                    "material_news_risk": 0.0,
                    "total_news_risk": 0.0,
                }
            )
    return pd.DataFrame(rows)


class FoldConstructionTest(unittest.TestCase):
    def test_folds_build_without_any_test_period_rows(self):
        table = _feature_table()
        self.assertTrue(table["year_month"].max() < pd.Timestamp("2025-07-01"))

        folds = tuning._fold_frames(table, historical_weight=1.0)

        self.assertEqual(len(folds), len(VALIDATION_FOLDS))
        for fold in folds:
            self.assertFalse(fold["train"].empty)
            self.assertFalse(fold["valid"].empty)

    def test_fold_train_never_overlaps_its_validation_window(self):
        table = _feature_table()

        for fold in tuning._fold_frames(table, historical_weight=1.0):
            valid_start = pd.Timestamp(fold["spec"]["valid_start"])
            self.assertTrue(fold["train"]["year_month"].lt(valid_start).all())

    def test_historical_rows_are_dropped_when_weight_is_zero(self):
        table = _feature_table()
        cutoff = pd.Timestamp(HISTORICAL_TRAIN_END)

        with_history = tuning._fold_frames(table, historical_weight=1.0)[0]["train"]
        without_history = tuning._fold_frames(table, historical_weight=0.0)[0]["train"]

        self.assertGreater(int(with_history["year_month"].le(cutoff).sum()), 0)
        self.assertEqual(int(without_history["year_month"].le(cutoff).sum()), 0)

    def test_empty_fold_is_rejected(self):
        table = _feature_table()
        current_only = table[table["year_month"].ge(pd.Timestamp("2025-04-01"))]

        with self.assertRaises(ValueError):
            tuning._fold_frames(current_only, historical_weight=0.0)


class VariantGateTest(unittest.TestCase):
    def test_unknown_variant_is_rejected(self):
        with self.assertRaises(ValueError):
            tuning._prepare_folds("not_a_real_variant")

    def test_variant_with_all_zero_external_signal_is_rejected(self):
        table = _feature_table()

        with patch.object(tuning, "_load_feature_table", return_value=table):
            with self.assertRaises(ValueError) as ctx:
                tuning._prepare_folds("stock_model_b_news")

        self.assertIn("news", str(ctx.exception).lower())


class CombinedMetricTest(unittest.TestCase):
    def test_combined_wape_pools_folds_rather_than_averaging(self):
        prepared = [
            {"fold": "f1", "spec": {"valid_start": "2025-01", "valid_end": "2025-03"},
             "y_valid": pd.Series([100.0]), "train_rows": 1, "valid_rows": 1,
             "historical_rows": 0},
            {"fold": "f2", "spec": {"valid_start": "2025-04", "valid_end": "2025-06"},
             "y_valid": pd.Series([1.0]), "train_rows": 1, "valid_rows": 1,
             "historical_rows": 0},
        ]
        # f1 은 오차 0, f2 는 오차 1 (실측 1). fold 평균이면 50%, 통합이면 1/101 ≈ 0.99%.
        predictions = {"f1": np.array([100.0]), "f2": np.array([0.0])}

        def fake_fit(_params, fold):
            return predictions[fold["fold"]], 10

        with patch.object(tuning, "_fit_and_predict", side_effect=fake_fit):
            result = tuning._evaluate_params({}, prepared)

        self.assertAlmostEqual(result["combined"]["WAPE"], 100 * 1 / 101, places=4)
        self.assertEqual(len(result["per_fold"]), 2)
        self.assertEqual(result["selected_n_estimators"], 10)


class ReportFieldTest(unittest.TestCase):
    def test_search_ceiling_and_selected_tree_count_are_separate(self):
        self.assertEqual(tuning.SEARCH_MAX_ESTIMATORS, 2000)

        prepared = [
            {"fold": "f1", "spec": {"valid_start": "2025-01", "valid_end": "2025-03"},
             "y_valid": pd.Series([10.0, 20.0]), "train_rows": 1, "valid_rows": 2,
             "historical_rows": 0},
        ]

        with patch.object(
            tuning, "_fit_and_predict", return_value=(np.array([10.0, 20.0]), 173)
        ):
            result = tuning._evaluate_params({}, prepared)

        # 실제 반영값은 early stopping 이 고른 173 이어야 하며 탐색 상한 2000 이 아니다.
        self.assertEqual(result["selected_n_estimators"], 173)
        self.assertNotEqual(result["selected_n_estimators"], tuning.SEARCH_MAX_ESTIMATORS)


if __name__ == "__main__":
    unittest.main()
