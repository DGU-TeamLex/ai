from pathlib import Path
import tempfile
import unittest

import pandas as pd

from src.data_loader import RAW_STOCK_COLUMNS, load_stock_data
from src.features import create_features


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


if __name__ == "__main__":
    unittest.main()
