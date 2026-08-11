"""리드타임 민감도 분석 — 담당자가 L 을 바꾸면 발주량이 얼마나 변하는가.

클라이언트 요구: "리드타임을 수동으로 조절했을 때 예상 주문량이 어떻게 되는지"

현재 운영 상태는 전 품목 `DEFAULT_LEAD_TIME_DAYS = 15` fallback 이다
(운영 가이드 2265행: stock_predictions.csv 163,229행 전부 15일 사용).
여기서는 L 을 여러 값으로 바꿔가며 목표재고·발주권고 총량이 어떻게 변하는지 낸다.

목표재고 = μ × (R + L) + 안전재고,  R = 30일 고정(#54 정기검토 확정)
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd

# 레포 루트를 sys.path 에 넣는다. 임시 폴더에서 옮겨온 스크립트라
# 절대경로가 박혀 있었다(다른 사람 PC 에서는 실행되지 않는다).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from src.config import (  # noqa: E402
    DEFAULT_LEAD_TIME_DAYS,
    DEFAULT_REVIEW_PERIOD_DAYS,
    PREDICTION_PATH,
    SAFETY_STOCK_RATE,
)
from src.modeling.inventory_policy import add_inventory_recommendations  # noqa: E402

LEAD_TIMES = [7, 15, 30, 45, 60, 90, 120]
OUT = str(pathlib.Path(__file__).resolve().parents[2] / "outputs" / "lead_time_sensitivity.json")


def main():
    if not PREDICTION_PATH.exists():
        raise SystemExit(f"예측 산출물이 필요합니다: {PREDICTION_PATH}")

    predictions = pd.read_csv(PREDICTION_PATH, low_memory=False)
    print(f"예측 행: {len(predictions):,}", flush=True)
    print(f"컬럼 일부: {list(predictions.columns)[:10]}", flush=True)

    # 가장 최근 예측 시점만 사용한다(발주 판단은 현재 시점 기준).
    month_col = next(
        (c for c in ("forecast_month", "year_month", "STD_YYYYMM") if c in predictions.columns),
        None,
    )
    if month_col:
        latest = predictions[month_col].max()
        predictions = predictions[predictions[month_col].eq(latest)].copy()
        print(f"기준 시점 {latest}: {len(predictions):,}행", flush=True)

    pred_col = next(
        (c for c in ("predicted_usage", "prediction", "forecast_usage") if c in predictions.columns),
        None,
    )
    if pred_col is None:
        raise SystemExit(f"예측 컬럼을 찾지 못했습니다: {list(predictions.columns)[:20]}")

    stock_col = next(
        (c for c in ("month_end_stock", "current_stock", "closing_stock") if c in predictions.columns),
        None,
    )
    print(f"예측 컬럼={pred_col} 현재고 컬럼={stock_col}", flush=True)

    rows = []
    for lead_time in LEAD_TIMES:
        frame = predictions.copy()
        frame["manual_lead_time_days"] = float(lead_time)
        frame["manual_review_period_days"] = float(DEFAULT_REVIEW_PERIOD_DAYS)
        result = add_inventory_recommendations(
            frame,
            prediction_col=pred_col,
            current_stock_col=stock_col,
            lead_time_days_col="manual_lead_time_days",
            review_period_days_col="manual_review_period_days",
        )
        target_col = next(
            (c for c in ("target_stock", "unconstrained_target_stock", "base_stock") if c in result.columns),
            None,
        )
        order_col = next(
            (c for c in ("recommended_order_qty", "recommended_order", "order_qty") if c in result.columns),
            None,
        )
        entry = {
            "lead_time_days": lead_time,
            "protection_period_days": DEFAULT_REVIEW_PERIOD_DAYS + lead_time,
            "rows": int(len(result)),
            "protection_period_demand_sum": float(result["protection_period_demand"].sum()),
            "safety_stock_sum": float(result["safety_stock"].sum()),
            "base_stock_sum": float(result["base_stock"].sum()),
        }
        if target_col:
            entry["target_stock_sum"] = float(result[target_col].sum())
            entry["target_col"] = target_col
        if order_col:
            order = pd.to_numeric(result[order_col], errors="coerce").fillna(0).clip(lower=0)
            entry["order_qty_sum"] = float(order.sum())
            entry["items_needing_order"] = int((order > 0).sum())
            entry["order_col"] = order_col
        rows.append(entry)
        print(
            f"L={lead_time:>3}일  보호기간={entry['protection_period_days']:>3}일  "
            f"base_stock합={entry['base_stock_sum']:>18,.0f}"
            + (f"  발주권고합={entry.get('order_qty_sum', 0):>16,.0f}" if order_col else "")
            + (f"  발주대상={entry.get('items_needing_order', 0):>8,}" if order_col else ""),
            flush=True,
        )

    base = next(r for r in rows if r["lead_time_days"] == DEFAULT_LEAD_TIME_DAYS)
    print()
    print(f"=== 현재 운영값 L={DEFAULT_LEAD_TIME_DAYS}일 대비 배수 ===")
    print(f"{'L(일)':>7}{'보호기간':>9}{'base_stock':>12}{'발주권고':>12}{'발주대상':>12}")
    for r in rows:
        bs = r["base_stock_sum"] / base["base_stock_sum"] if base["base_stock_sum"] else 0
        oq = (
            r.get("order_qty_sum", 0) / base["order_qty_sum"]
            if base.get("order_qty_sum")
            else 0
        )
        it = (
            r.get("items_needing_order", 0) / base["items_needing_order"]
            if base.get("items_needing_order")
            else 0
        )
        mark = "  ← 현재" if r["lead_time_days"] == DEFAULT_LEAD_TIME_DAYS else ""
        print(f"{r['lead_time_days']:>7}{r['protection_period_days']:>9}{bs:>11.2f}x{oq:>11.2f}x{it:>11.2f}x{mark}")

    report = {
        "review_period_days": DEFAULT_REVIEW_PERIOD_DAYS,
        "safety_stock_rate": SAFETY_STOCK_RATE,
        "current_default_lead_time_days": DEFAULT_LEAD_TIME_DAYS,
        "formula": "target = mu * (R + L) + safety_stock",
        "rows": rows,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\nsaved:", OUT)


if __name__ == "__main__":
    main()
