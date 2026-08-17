import unittest

import pandas as pd

from src.features import create_features


def _monthly_stock(consumption: list[float]) -> pd.DataFrame:
    """단일 시계열(기관·부서·물품 1개)의 월별 재고 표를 만든다."""
    months = pd.date_range("2024-01-01", periods=len(consumption), freq="MS")
    return pd.DataFrame(
        {
            "year_month": months,
            "institution_code": "INST_A",
            "department": "DEPT_A",
            "item_code": "ITEM_A",
            "item_name": "테스트품목",
            "stock_item_key": "INST_A::DEPT_A::ITEM_A",
            "consumption_qty": consumption,
            "inbound_qty": [0.0] * len(consumption),
            "month_end_stock": [10.0] * len(consumption),
            "stockout_rate": [0.0] * len(consumption),
            "disposal_qty": [0.0] * len(consumption),
            "auto_disposal_adjustment_qty": [0.0] * len(consumption),
        }
    )


class ExpandingMeanDtypeTest(unittest.TestCase):
    """세그먼트 첫 달의 signed 출고가 음수여도 모델 수요 0으로 처리한다.

    signed 합은 원장 대사에 보존하고 모델 수요는 양수 출고만 합산한다.
    따라서 음수-only 월도 결측이 아니라 유효한 수요 0 관측이다(ai#65).
    """

    def test_negative_consumption_at_segment_start_does_not_crash(self):
        frame = _monthly_stock([-4.0, 10.0, 20.0, 30.0])

        result = create_features(frame)

        self.assertEqual(result["use_expanding_mean"].dtype, "float32")

    def test_negative_only_month_is_valid_zero_demand_observation(self):
        frame = _monthly_stock([-4.0, 10.0, 20.0, 30.0])

        result = create_features(frame).sort_values("year_month").reset_index(drop=True)

        self.assertEqual(float(result.loc[0, "demand_qty"]), 0.0)
        self.assertEqual(int(result.loc[0, "negative_consumption_flag"]), 1)
        self.assertAlmostEqual(float(result.loc[0, "use_expanding_mean"]), 0.0, places=4)
        self.assertAlmostEqual(float(result.loc[1, "use_expanding_mean"]), 5.0, places=4)
        self.assertAlmostEqual(float(result.loc[2, "use_expanding_mean"]), 10.0, places=4)

    def test_all_positive_series_is_unaffected(self):
        frame = _monthly_stock([10.0, 20.0, 30.0])

        result = create_features(frame).sort_values("year_month").reset_index(drop=True)

        self.assertEqual(result["use_expanding_mean"].dtype, "float32")
        self.assertAlmostEqual(float(result.loc[0, "use_expanding_mean"]), 10.0, places=4)
        self.assertAlmostEqual(float(result.loc[2, "use_expanding_mean"]), 20.0, places=4)


if __name__ == "__main__":
    unittest.main()
