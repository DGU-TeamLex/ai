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
    """세그먼트 첫 행이 음수 출고여도 피처 생성이 죽지 않아야 한다.

    demand_qty 는 consumption_qty < 0 인 행에서 NaN 이 된다(features.py).
    그 행이 세그먼트의 첫 행이면 누적 관측수가 0 이 되는데, 이때
    `.replace(0, pd.NA)` 를 쓰면 int 컬럼이 object 로 바뀌어
    이어지는 astype("float32") 가 NAType 에서 TypeError 를 낸다.

    실제 원장(2024~25 16건·2018~19 3건, 합 19건)의 음수 출고 대부분은
    월별 signed 집계에서 같은 달 양수 출고와 상계돼 사라지고, 실제로 월별
    feature table 에 signed 음수로 남는 건 1건(K3;34/USE0004197/2025-07)뿐이며
    그 행도 세그먼트 첫 행이 아니다(sehyeon03 리뷰, ai#61). 즉 이 시나리오가
    실제 원장에서 재현된 크래시는 아니고, 세그먼트 첫 행이 통째로 무효
    관측(음수만 있는 달)일 때를 대비한 방어 수정이다.
    """

    def test_negative_consumption_at_segment_start_does_not_crash(self):
        frame = _monthly_stock([-4.0, 10.0, 20.0, 30.0])

        result = create_features(frame)

        self.assertEqual(result["use_expanding_mean"].dtype, "float32")

    def test_expanding_mean_is_na_until_first_valid_observation(self):
        frame = _monthly_stock([-4.0, 10.0, 20.0, 30.0])

        result = create_features(frame).sort_values("year_month").reset_index(drop=True)

        # 첫 행은 유효 관측이 0건이므로 평균이 정의되지 않는다.
        self.assertTrue(pd.isna(result.loc[0, "use_expanding_mean"]))
        # 이후는 유효 관측만으로 누적 평균이 계산된다.
        self.assertAlmostEqual(float(result.loc[1, "use_expanding_mean"]), 10.0, places=4)
        self.assertAlmostEqual(float(result.loc[2, "use_expanding_mean"]), 15.0, places=4)

    def test_all_positive_series_is_unaffected(self):
        frame = _monthly_stock([10.0, 20.0, 30.0])

        result = create_features(frame).sort_values("year_month").reset_index(drop=True)

        self.assertEqual(result["use_expanding_mean"].dtype, "float32")
        self.assertAlmostEqual(float(result.loc[0, "use_expanding_mean"]), 10.0, places=4)
        self.assertAlmostEqual(float(result.loc[2, "use_expanding_mean"]), 20.0, places=4)


if __name__ == "__main__":
    unittest.main()
