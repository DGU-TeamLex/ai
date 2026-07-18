from pathlib import Path
import tempfile
import unittest

import pandas as pd

from src.data_loader import RAW_STOCK_COLUMNS, load_stock_data
from src.features import create_features
from src.modeling.baseline import add_baseline_predictions
from src.modeling.metrics import regression_metrics


def stock_row(
    date: str,
    opening: float,
    closing: float,
    consumption: float,
    item_name: str = "혈당 검사지",
) -> list[str]:
    values = {
        "부서코드": "방문건강관리사업",
        "물품코드": "USE0000067",
        "물품명": item_name,
        "재고마감일": date,
        "이전최종재고량": str(opening),
        "마감재고량": str(closing),
        "구입처코드": "",
        "구입단가": "",
        "입고량": "0",
        "불출입고량": "0",
        "반납입고량": "0",
        "불출출고량": "0",
        "정상출고량": str(consumption),
        "반품출고량": "0",
        "폐기출고량": "0",
        "자동폐기출고량": "0",
        "보정출고량": "0",
        "보건기관코드_en": "INST001",
    }
    return [values[column] for column in RAW_STOCK_COLUMNS]


class StockPipelineTest(unittest.TestCase):
    def test_quote_aware_loader_builds_monthly_stock(self):
        import csv

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stock.DAT"
            with path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.writer(file, delimiter="|", quotechar='"', lineterminator="\r\n")
                writer.writerow(RAW_STOCK_COLUMNS)
                writer.writerow(stock_row("20240101", 10, 8, 2, "혈당\n검사지"))
                writer.writerow(stock_row("20240131", 8, 5, 3))
                writer.writerow(stock_row("20240229", 5, 4, 1))

            monthly = load_stock_data(path.parent, path.name, chunk_size=1)

        self.assertEqual(len(monthly), 2)
        january = monthly[monthly["year_month"] == pd.Timestamp("2024-01-01")].iloc[0]
        self.assertEqual(january["month_opening_stock"], 10)
        self.assertEqual(january["month_end_stock"], 5)
        self.assertEqual(january["consumption_qty"], 5)
        self.assertEqual(january["average_stock"], 6.5)
        self.assertEqual(january["stock_item_key"], "INST001::방문건강관리사업::USE0000067")
        self.assertNotIn("\n", january["item_name"])

    def test_features_predict_next_contiguous_month(self):
        monthly = pd.DataFrame(
            [
                {
                    "year_month": pd.Timestamp("2024-01-01"),
                    "institution_code": "INST001",
                    "department": "내과",
                    "item_code": "ITEM1",
                    "stock_item_key": "INST001::내과::ITEM1",
                    "consumption_qty": 5.0,
                    "inbound_qty": 2.0,
                    "month_end_stock": 10.0,
                    "stockout_rate": 0.0,
                    "disposal_qty": 0.0,
                    "auto_disposal_adjustment_qty": 0.0,
                },
                {
                    "year_month": pd.Timestamp("2024-02-01"),
                    "institution_code": "INST001",
                    "department": "내과",
                    "item_code": "ITEM1",
                    "stock_item_key": "INST001::내과::ITEM1",
                    "consumption_qty": 7.0,
                    "inbound_qty": 3.0,
                    "month_end_stock": 6.0,
                    "stockout_rate": 0.0,
                    "disposal_qty": 0.0,
                    "auto_disposal_adjustment_qty": 0.0,
                },
            ]
        )

        features = create_features(monthly)

        january = features.iloc[0]
        self.assertEqual(january["use_lag_1"], 5.0)
        self.assertEqual(january["target_next_month"], 7.0)
        self.assertEqual(january["use_rolling_mean_3"], 5.0)

    def test_features_reset_history_after_a_missing_month(self):
        monthly = pd.DataFrame(
            [
                {
                    "year_month": pd.Timestamp("2024-01-01"),
                    "institution_code": "INST001",
                    "department": "내과",
                    "item_code": "ITEM1",
                    "stock_item_key": "INST001::내과::ITEM1",
                    "consumption_qty": 100.0,
                    "inbound_qty": 0.0,
                    "month_end_stock": 10.0,
                    "stockout_rate": 0.0,
                    "disposal_qty": 0.0,
                    "auto_disposal_adjustment_qty": 0.0,
                },
                {
                    "year_month": pd.Timestamp("2024-03-01"),
                    "institution_code": "INST001",
                    "department": "내과",
                    "item_code": "ITEM1",
                    "stock_item_key": "INST001::내과::ITEM1",
                    "consumption_qty": 4.0,
                    "inbound_qty": 0.0,
                    "month_end_stock": 6.0,
                    "stockout_rate": 0.0,
                    "disposal_qty": 0.0,
                    "auto_disposal_adjustment_qty": 0.0,
                },
                {
                    "year_month": pd.Timestamp("2024-04-01"),
                    "institution_code": "INST001",
                    "department": "내과",
                    "item_code": "ITEM1",
                    "stock_item_key": "INST001::내과::ITEM1",
                    "consumption_qty": 6.0,
                    "inbound_qty": 0.0,
                    "month_end_stock": 5.0,
                    "stockout_rate": 0.0,
                    "disposal_qty": 0.0,
                    "auto_disposal_adjustment_qty": 0.0,
                },
            ]
        )

        features = create_features(monthly)
        january, march, _ = [row for _, row in features.iterrows()]

        self.assertTrue(pd.isna(january["target_next_month"]))
        self.assertEqual(march["history_months"], 1)
        self.assertTrue(pd.isna(march["use_lag_2"]))
        self.assertEqual(march["use_rolling_mean_3"], 4.0)
        self.assertEqual(march["target_next_month"], 6.0)

    def test_calendar_features_describe_the_forecast_month(self):
        monthly = pd.DataFrame(
            [
                {
                    "year_month": pd.Timestamp("2025-12-01"),
                    "institution_code": "INST001",
                    "department": "내과",
                    "item_code": "ITEM1",
                    "stock_item_key": "INST001::내과::ITEM1",
                    "consumption_qty": 2.0,
                    "inbound_qty": 0.0,
                    "month_end_stock": 3.0,
                    "stockout_rate": 0.0,
                    "disposal_qty": 0.0,
                    "auto_disposal_adjustment_qty": 0.0,
                }
            ]
        )

        row = create_features(monthly).iloc[0]

        self.assertEqual(row["forecast_month"], pd.Timestamp("2026-01-01"))
        self.assertEqual(row["year"], 2026)
        self.assertEqual(row["month"], 1)

    def test_baselines_only_use_precomputed_as_of_features(self):
        frame = pd.DataFrame(
            {
                "lag_1": [2.0, 100.0],
                "rolling_mean_3": [2.0, 51.0],
                "rolling_median_3": [2.0, 51.0],
                "rolling_mean_6": [2.0, 51.0],
                "same_month_last_year": [pd.NA, pd.NA],
                "expanding_mean": [2.0, 51.0],
            }
        )

        predictions = add_baseline_predictions(frame)

        self.assertEqual(predictions.iloc[0]["baseline_expanding_mean_pred"], 2.0)
        self.assertEqual(predictions.iloc[0]["baseline_same_month_last_year_pred"], 2.0)

    def test_metrics_ignore_unknown_future_actuals(self):
        metrics = regression_metrics([1.0, float("nan"), 3.0], [1.5, 10.0, 2.5])

        self.assertEqual(metrics["N"], 2)
        self.assertAlmostEqual(metrics["MAE"], 0.5)


if __name__ == "__main__":
    unittest.main()
