import csv
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from src.data_loader import (
    RAW_STOCK_COLUMNS,
    RAW_STOCK_OPTIONAL_COLUMNS,
    load_stock_data,
)
from src.features import create_features
from src.modeling.baseline import add_baseline_predictions
from src.modeling.metrics import regression_metrics
from src.modeling.training import (
    select_training_window,
    split_time_series,
    training_sample_weights,
)


def stock_row(
    date: str,
    opening: float,
    closing: float,
    consumption: float,
    item_name: str = "혈당 검사지",
    purchase_in: float = 0,
    transfer_in: float = 0,
    return_in: float = 0,
    transfer_out: float = 0,
    return_out: float = 0,
    disposal: float = 0,
    auto_disposal: float = 0,
    correction_out: float = 0,
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
        "입고량": str(purchase_in),
        "불출입고량": str(transfer_in),
        "반납입고량": str(return_in),
        "불출출고량": str(transfer_out),
        "정상출고량": str(consumption),
        "반품출고량": str(return_out),
        "폐기출고량": str(disposal),
        "자동폐기출고량": str(auto_disposal),
        "보정출고량": str(correction_out),
        "보건기관코드_en": "INST001",
    }
    return [values[column] for column in RAW_STOCK_COLUMNS]


class StockPipelineTest(unittest.TestCase):
    def test_legacy_schema_without_optional_vendor_columns_is_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.DAT"
            columns = [
                column
                for column in RAW_STOCK_COLUMNS
                if column not in RAW_STOCK_OPTIONAL_COLUMNS
            ]
            values = dict(
                zip(
                    RAW_STOCK_COLUMNS,
                    stock_row("20190101", 10, 8, 2),
                    strict=False,
                )
            )
            with path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.writer(file, delimiter="|")
                writer.writerow(columns)
                writer.writerow([values[column] for column in columns])

            loaded = load_stock_data(raw_dir=Path(directory))

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded.iloc[0]["vendor_code"], "")
        self.assertTrue(pd.isna(loaded.iloc[0]["average_unit_price"]))

    def test_current_and_historical_patterns_can_be_processed_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            current_path = Path(directory) / "익스포트_0_수정.DAT"
            historical_path = (
                Path(directory)
                / "(한국사회보장정보원)_물품재고_0_2018_2019_수정.DAT"
            )
            for path, date in [
                (current_path, "20240101"),
                (historical_path, "20190101"),
            ]:
                with path.open("w", encoding="utf-8", newline="") as file:
                    writer = csv.writer(file, delimiter="|")
                    writer.writerow(RAW_STOCK_COLUMNS)
                    writer.writerow(stock_row(date, 10, 8, 2))

            current = load_stock_data(
                raw_dir=Path(directory),
                pattern="익스포트_*.DAT",
            )
            historical = load_stock_data(
                raw_dir=Path(directory),
                pattern="*2018_2019*.DAT",
            )

        self.assertEqual(current["year_month"].tolist(), [pd.Timestamp("2024-01-01")])
        self.assertEqual(
            historical["year_month"].tolist(),
            [pd.Timestamp("2019-01-01")],
        )

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
        self.assertEqual(january["normal_outbound_signed_sum"], 5)
        self.assertEqual(january["model_demand_positive_sum"], 5)
        self.assertEqual(january["normal_outbound_nonnegative_sum"], 5)
        self.assertEqual(january["negative_normal_outbound_count"], 0)
        self.assertEqual(january["negative_normal_outbound_amount"], 0)
        self.assertEqual(january["normal_outbound_squared_sum"], 13)
        self.assertEqual(january["average_stock"], 6.5)
        self.assertEqual(january["stock_item_key"], "INST001::방문건강관리사업::USE0000067")
        self.assertEqual(january["ledger_document_rule_violation_count"], 0)
        self.assertEqual(january["ledger_physical_violation_count"], 0)
        self.assertEqual(january["ledger_balance_violation_count"], 0)
        self.assertNotIn("\n", january["item_name"])

    def test_auto_disposal_is_excluded_from_demand_and_ledger_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stock.DAT"
            with path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.writer(file, delimiter="|", quotechar='"')
                writer.writerow(RAW_STOCK_COLUMNS)
                writer.writerow(
                    stock_row(
                        "20240101",
                        opening=10,
                        closing=8,
                        consumption=2,
                        auto_disposal=-50,
                    )
                )

            monthly = load_stock_data(path.parent, path.name, chunk_size=1)

        row = monthly.iloc[0]
        self.assertEqual(row["consumption_qty"], 2)
        self.assertEqual(row["normal_outbound_nonnegative_sum"], 2)
        self.assertEqual(row["other_outbound_qty"], 0)
        self.assertEqual(row["auto_disposal_adjustment_qty"], -50)
        self.assertEqual(row["ledger_balance_violation_count"], 0)
        self.assertEqual(row["ledger_balance_residual_sum"], 0)
        self.assertTrue(row["auto_disposal_excluded_from_demand_and_ledger"])

    def test_signed_ledger_and_positive_model_demand_are_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stock.DAT"
            with path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.writer(file, delimiter="|", quotechar='"')
                writer.writerow(RAW_STOCK_COLUMNS)
                writer.writerow(
                    stock_row(
                        "20240101",
                        opening=10,
                        closing=5,
                        consumption=5,
                    )
                )
                writer.writerow(
                    stock_row(
                        "20240102",
                        opening=5,
                        closing=7,
                        consumption=-2,
                    )
                )

            monthly = load_stock_data(path.parent, path.name, chunk_size=1)

        row = monthly.iloc[0]
        self.assertEqual(row["consumption_qty"], 3)
        self.assertEqual(row["normal_outbound_signed_sum"], 3)
        self.assertEqual(row["model_demand_positive_sum"], 5)
        self.assertEqual(row["negative_normal_outbound_count"], 1)
        self.assertEqual(row["negative_normal_outbound_amount"], 2)
        self.assertEqual(row["ledger_balance_residual_sum"], 0)

    def test_negative_only_month_is_zero_model_demand_with_audit_count(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stock.DAT"
            with path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.writer(file, delimiter="|", quotechar='"')
                writer.writerow(RAW_STOCK_COLUMNS)
                writer.writerow(
                    stock_row(
                        "20240101",
                        opening=5,
                        closing=9,
                        consumption=-4,
                    )
                )

            monthly = load_stock_data(path.parent, path.name, chunk_size=1)
            featured = create_features(monthly)

        row = featured.iloc[0]
        self.assertEqual(row["normal_outbound_signed_sum"], -4)
        self.assertEqual(row["demand_qty"], 0)
        self.assertEqual(row["negative_consumption_flag"], 1)
        self.assertEqual(row["negative_normal_outbound_count"], 1)
        self.assertEqual(row["negative_normal_outbound_amount"], 4)
        self.assertEqual(row["ledger_balance_residual_sum"], 0)

    def test_ledger_quality_separates_document_and_physical_inbound_rules(self):
        import csv

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stock.DAT"
            with path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.writer(file, delimiter="|", quotechar='"', lineterminator="\n")
                writer.writerow(RAW_STOCK_COLUMNS)
                writer.writerow(
                    stock_row(
                        "20240101",
                        opening=1,
                        closing=2,
                        consumption=4,
                        transfer_in=5,
                    )
                )
                writer.writerow(
                    stock_row(
                        "20240131",
                        opening=1,
                        closing=0,
                        consumption=4,
                        purchase_in=1,
                    )
                )
                writer.writerow(
                    stock_row(
                        "20240115",
                        opening=-3,
                        closing=0,
                        consumption=0,
                    )
                )

            monthly = load_stock_data(path.parent, path.name, chunk_size=1)

        january = monthly.iloc[0]
        self.assertEqual(january["ledger_document_rule_violation_count"], 3)
        self.assertEqual(january["ledger_physical_violation_count"], 1)
        self.assertEqual(january["ledger_opening_stock_missing_count"], 0)

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

    def test_standardized_historical_gap_data_is_included_in_training(self):
        frame = pd.DataFrame(
            {
                "year_month": pd.to_datetime(
                    ["2019-12-01", "2024-12-01", "2025-01-01", "2025-07-01"]
                ),
                "historical_training_eligible": [True, True, True, True],
            }
        )

        train, valid, test = split_time_series(frame)

        self.assertEqual(
            train["year_month"].tolist(),
            [
                pd.Timestamp("2019-12-01"),
                pd.Timestamp("2024-12-01"),
            ],
        )
        self.assertEqual(valid["year_month"].tolist(), [pd.Timestamp("2025-01-01")])
        self.assertEqual(test["year_month"].tolist(), [pd.Timestamp("2025-07-01")])

    def test_unmatched_historical_item_is_excluded_from_model_training(self):
        frame = pd.DataFrame(
            {
                "year_month": pd.to_datetime(
                    ["2019-12-01", "2024-12-01", "2025-01-01", "2025-07-01"]
                ),
                "historical_training_eligible": [False, True, True, True],
            }
        )

        train, _, _ = split_time_series(frame)

        self.assertEqual(
            train["year_month"].tolist(),
            [pd.Timestamp("2024-12-01")],
        )

    def test_historical_weight_only_downweights_eligible_old_rows(self):
        frame = pd.DataFrame(
            {
                "year_month": pd.to_datetime(
                    ["2019-11-01", "2019-12-01", "2024-12-01"]
                ),
                "historical_training_eligible": [True, False, True],
            }
        )

        train = select_training_window(frame, "2024-12", 0.25)
        weights = training_sample_weights(train, 0.25)

        self.assertEqual(
            train["year_month"].tolist(),
            [
                pd.Timestamp("2019-11-01"),
                pd.Timestamp("2024-12-01"),
            ],
        )
        self.assertEqual(weights.tolist(), [0.25, 1.0])


if __name__ == "__main__":
    unittest.main()
