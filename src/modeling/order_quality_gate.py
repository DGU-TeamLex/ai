"""발주 권고량에 공통 데이터 품질 게이트를 적용한다."""

from __future__ import annotations

import numpy as np
import pandas as pd


REVIEW_REQUIRED_ZERO_REASONS = {
    "DATA_MISSING",
    "STALE_OR_MISSING_OBSERVATION",
}


def apply_order_quality_gates(
    frame: pd.DataFrame,
    *,
    order_col: str,
    raw_order_col: str,
    suppressed_col: str,
    reason_col: str,
) -> pd.DataFrame:
    """동일한 휴면·미운영·검토필요 규칙을 발주 열에 적용한다.

    정상 행은 계산값을 유지하고, 휴면/미운영 행은 0으로 억제한다.
    재고 데이터가 없거나 오래된 행은 사람이 검토하도록 ``NaN``으로 둔다.
    """

    result = frame.copy()
    if order_col not in result.columns:
        raise ValueError(f"발주량 열이 없습니다: {order_col}")

    result[raw_order_col] = pd.to_numeric(result[order_col], errors="coerce")
    demand_class = (
        result.get("demand_class", pd.Series("", index=result.index, dtype="string"))
        .astype("string")
        .fillna("")
        .str.upper()
    )
    zero_reason = (
        result.get(
            "zero_stock_reason",
            pd.Series("", index=result.index, dtype="string"),
        )
        .astype("string")
        .fillna("")
        .str.upper()
    )

    dormant = demand_class.eq("DORMANT")
    not_operated = zero_reason.eq("NOT_OPERATED")
    review_required = zero_reason.isin(REVIEW_REQUIRED_ZERO_REASONS)
    suppression_reason = pd.Series("", index=result.index, dtype="string")
    suppression_reason = suppression_reason.mask(dormant, "DORMANT")
    suppression_reason = suppression_reason.mask(not_operated, "NOT_OPERATED")
    suppression_reason = suppression_reason.mask(review_required, zero_reason)

    result.loc[dormant | not_operated, order_col] = 0.0
    result.loc[review_required, order_col] = np.nan
    result[suppressed_col] = dormant | not_operated | review_required
    result[reason_col] = suppression_reason
    return result
