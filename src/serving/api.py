import pandas as pd
from fastapi import FastAPI, HTTPException, Query

from ..config import PREDICTION_PATH
from ..inventory_policy import add_inventory_recommendations
from .schemas import RecommendOrderRequest


app = FastAPI(title="Medical Device Inventory Forecast MVP")


def _load_predictions() -> pd.DataFrame:
    if not PREDICTION_PATH.exists():
        raise HTTPException(status_code=503, detail="predictions.csv not found. Run python -m src.predict first.")
    df = pd.read_csv(PREDICTION_PATH)
    df["year_month"] = pd.to_datetime(df["year_month"]).dt.strftime("%Y-%m")
    df["MED_DEVICE_5"] = df["MED_DEVICE_5"].astype(str)
    df["SIDO"] = df["SIDO"].astype(str)
    return df


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/predictions")
def get_predictions(
    yyyymm: str = Query(...),
    item_code: str = Query(...),
    sido: str | None = Query(default=None),
):
    df = _load_predictions()
    mask = (df["year_month"] == yyyymm) & (df["MED_DEVICE_5"] == str(item_code))
    if sido is not None:
        mask &= df["SIDO"] == str(sido)
    result = df[mask]
    if result.empty:
        raise HTTPException(status_code=404, detail="Prediction not found")
    return result.head(50).to_dict(orient="records")


@app.post("/recommend-order")
def recommend_order(payload: RecommendOrderRequest):
    df = _load_predictions()
    mask = (df["year_month"] == payload.yyyymm) & (df["MED_DEVICE_5"] == str(payload.item_code))
    if payload.sido is not None:
        mask &= df["SIDO"] == str(payload.sido)
    result = df[mask].copy()
    if result.empty:
        raise HTTPException(status_code=404, detail="Prediction not found")

    result["current_stock"] = payload.current_stock
    result["lead_time_days"] = payload.lead_time_days
    result = add_inventory_recommendations(
        result,
        prediction_col="predicted_usage",
        current_stock_col="current_stock",
        lead_time_days_col="lead_time_days",
    )
    row = result.iloc[0]
    return {
        "predicted_usage": float(row["predicted_usage"]),
        "recommended_stock": float(row["recommended_stock"]),
        "current_stock": float(row["current_stock"]),
        "recommended_order": float(row["recommended_order"]),
        "risk_summary": "배치 계산된 외부 위험 점수와 안전재고 정책을 반영했습니다.",
    }

