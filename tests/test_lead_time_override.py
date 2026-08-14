"""리드타임 수동 조절이 발주량에 반영되는지 검증.

클라이언트 요구사항: "15일을 사람이 수동으로 바꾸었을 때 어떻게 예측되는지".
add_inventory_recommendations 는 lead_time_days_col 을 넘기지 않으면 전 행을
fallback 15일로 고정하므로, 호출부가 컬럼을 넘기는지까지 함께 고정한다.
"""
import inspect

import pandas as pd
import pytest

from src.modeling import prediction, recursive_inventory_simulation
from src.modeling.inventory_policy import add_inventory_recommendations


def _frame(lead_time_days=None, n=3):
    data = {
        "predicted_usage": [100.0] * n,
        "current_stock": [0.0] * n,
        "review_period_days": [30.0] * n,
    }
    if lead_time_days is not None:
        data["lead_time_days"] = lead_time_days
    return pd.DataFrame(data)


def test_manual_lead_time_changes_protection_period():
    df = _frame([15.0, 30.0, 60.0])
    out = add_inventory_recommendations(
        df,
        current_stock_col="current_stock",
        lead_time_days_col="lead_time_days",
        review_period_days_col="review_period_days",
    )
    assert list(out["lead_time_days"]) == [15.0, 30.0, 60.0]
    assert list(out["protection_period_days"]) == [45.0, 60.0, 90.0]
    # 보호기간수요는 보호기간에 비례한다 → 발주량도 단조 증가한다.
    assert out["base_stock"].is_monotonic_increasing
    assert out["recommended_order"].is_monotonic_increasing


def test_lead_time_column_absent_falls_back_to_policy_default():
    out = add_inventory_recommendations(
        _frame(),
        current_stock_col="current_stock",
        lead_time_days_col="lead_time_days",
    )
    assert out["lead_time_fallback_applied"].all()
    assert out["lead_time_days"].nunique() == 1


def test_out_of_range_lead_time_is_flagged_not_silent():
    df = _frame([0.0, 999.0], n=2)
    out = add_inventory_recommendations(
        df,
        current_stock_col="current_stock",
        lead_time_days_col="lead_time_days",
    )
    # 하한 미만 → fallback, 상한 초과 → cap. 둘 다 플래그로 드러나야 한다.
    assert bool(out["lead_time_fallback_applied"].iloc[0])
    assert bool(out["lead_time_cap_applied"].iloc[1])
    assert out["lead_time_days"].iloc[1] < 999.0


@pytest.mark.parametrize(
    "module, function_name",
    [
        (prediction, "_finalize_predictions"),
        (recursive_inventory_simulation, "_apply_policy"),
    ],
)
def test_pipeline_callers_pass_lead_time_column(module, function_name):
    """호출부가 lead_time_days_col 을 넘기지 않으면 정책은 항상 15일로 고정된다.

    이 회귀가 실제로 있었다. 정책 파일은 품목별 추정(stockout_duration_p25)을
    쓴다고 선언하는데 운영 경로는 컬럼을 넘기지 않아 전 행이 fallback 이었다.
    """
    candidates = [
        obj
        for name, obj in inspect.getmembers(module, inspect.isfunction)
        if obj.__module__ == module.__name__
    ]
    sources = [inspect.getsource(obj) for obj in candidates]
    calls = [src for src in sources if "add_inventory_recommendations(" in src]
    assert calls, f"{module.__name__} 에 정책 호출부가 없다"
    for src in calls:
        assert "lead_time_days_col" in src, (
            f"{module.__name__} 의 정책 호출부가 lead_time_days_col 을 넘기지 않는다"
        )
