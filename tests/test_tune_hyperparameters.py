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


class EffectiveParamRoundTripTests(unittest.TestCase):
    """저장된 파라미터로 같은 estimator 가 만들어지는지 (ai#60 리뷰 지적 1)."""

    def test_fixed_params_survive_into_best_params(self):
        """study.best_params 만 저장하면 빠지던 고정값들이 포함돼야 한다."""
        searched = {
            "learning_rate": 0.025, "num_leaves": 248, "min_child_samples": 76,
            "subsample": 0.79976, "colsample_bytree": 0.72147,
            "reg_lambda": 0.22591, "reg_alpha": 0.01544,
        }
        params = tuning.build_estimator_params(searched, "regression", 1000)
        for key in ("objective", "subsample_freq", "random_state",
                    "n_jobs", "force_col_wise", "histogram_pool_size", "verbosity"):
            self.assertIn(key, params, f"{key} 가 저장 파라미터에서 누락됐다")
        # LightGBM 기본값 0 이면 subsample 이 무효화된다.
        self.assertEqual(params["subsample_freq"], 1)
        self.assertEqual(params["n_estimators"], 1000)
        self.assertAlmostEqual(params["subsample"], 0.79976)

    def test_subsample_is_actually_active_on_estimator(self):
        """LGBMRegressor 에 넘겼을 때 subsample 이 실제로 켜져 있어야 한다."""
        from lightgbm import LGBMRegressor
        params = tuning.build_estimator_params(
            {"subsample": 0.8, "learning_rate": 0.05}, "regression", 100)
        model = LGBMRegressor(**params)
        got = model.get_params()
        self.assertEqual(got["subsample_freq"], 1)
        self.assertAlmostEqual(got["subsample"], 0.8)

    def test_json_round_trip(self):
        """JSON 으로 쓰고 다시 읽어도 동일한 파라미터가 나와야 한다."""
        import json
        searched = {"learning_rate": 0.03, "num_leaves": 100, "subsample": 0.75}
        original = tuning.build_estimator_params(searched, "tweedie", 500)
        restored = json.loads(json.dumps(original))
        self.assertEqual(original, restored)
        rebuilt = tuning.build_estimator_params(
            {k: v for k, v in restored.items() if k in tuning.SEARCHED_PARAM_NAMES},
            restored["objective"], restored["n_estimators"], restored["random_state"])
        self.assertEqual(original, rebuilt)

    def test_baseline_and_tuned_share_fixed_params(self):
        """baseline 과 tuned 가 같은 고정값을 써야 비교가 성립한다.

        `subsample_freq` 는 예외다. 운영 baseline(`production_lgbm_params`)은 이 키를
        지정하지 않아 LightGBM 기본값 0(subsample 비활성)이고, tuned 쪽은 의도적으로
        1 을 넣어 subsample 을 활성화한다. 이건 파라미터 탐색이 아니라 baseline 자체의
        동작 변경이라 두 쪽이 달라야 정상이다(ai#60 리뷰 2번).

        종전 단언은 이 키까지 같아야 한다고 봐서 KeyError 로 실패했다 — 테스트가
        틀린 것이지 코드가 틀린 것이 아니다.
        """
        base = tuning._baseline_params("regression")
        tuned = tuning.build_estimator_params({"learning_rate": 0.1}, "regression", 900)
        for key in ("histogram_pool_size", "force_col_wise",
                    "n_jobs", "random_state", "verbosity", "objective"):
            self.assertEqual(base[key], tuned[key], f"{key} 가 baseline 과 tuned 에서 다르다")
        self.assertNotIn(
            "subsample_freq", base, "운영 baseline 은 subsample_freq 를 지정하지 않아야 한다"
        )
        self.assertEqual(
            tuned["subsample_freq"], 1, "tuned 쪽은 subsample_freq=1 을 명시해야 한다"
        )


class ExternalSignalGateScopeTests(unittest.TestCase):
    """게이트가 fold 의 train 행만 보는지 (ai#60 리뷰 지적 2)."""

    def test_gate_checks_training_rows_not_whole_table(self):
        """학습 구간엔 신호가 없고 validation 에만 있으면 통과하면 안 된다."""
        calls = []

        def fake_missing(frame, options):
            calls.append(len(frame))
            return None

        folds = [
            {"name": "f1", "train": pd.DataFrame({"a": [1, 2, 3]}),
             "valid": pd.DataFrame({"a": [4, 5]})},
        ]
        with patch.object(tuning, "_missing_external_signal", side_effect=fake_missing):
            for fold in folds:
                tuning._missing_external_signal(fold["train"], {})
        # 전체 테이블(5행)이 아니라 train(3행)으로 호출돼야 한다.
        self.assertEqual(calls, [3])


if __name__ == "__main__":
    unittest.main()
