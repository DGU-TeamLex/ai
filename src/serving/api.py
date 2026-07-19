from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd
from fastapi import APIRouter, FastAPI, HTTPException, Query

from ..config import (
    CLASSIFIED_PREDICTION_PATH,
    COMMODITY_RISK_SCORE_PATH,
    EVALUATION_REPORT_PATH,
    PREDICTION_PATH,
)
from ..modeling.inventory_policy import add_inventory_recommendations
from ..module_c.supply_risk_policy import (
    calculate_level_based_safety_stock,
    derive_supply_risk_level,
)
from .schemas import ForecastRunRequest, RecommendOrderRequest


app = FastAPI(
    title="WeP-Stock AI Service",
    version="0.2.0",
    description="WeP-Stock AI 학습, 예측, 위험 점수, 재고 권고 서빙 전용 API",
)
router = APIRouter(prefix="/api/v1/ai")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_csv(path: Path, parse_month: bool = False) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if parse_month and "year_month" in df.columns:
        df["year_month"] = pd.to_datetime(df["year_month"]).dt.strftime("%Y-%m")
    return df


def _page(items: list[dict], page: int, size: int) -> dict:
    start = max(page - 1, 0) * size
    end = start + size
    total = len(items)
    return {
        "content": items[start:end],
        "page": page,
        "size": size,
        "totalElements": total,
        "totalPages": (total + size - 1) // size if size else 1,
    }


def _prediction_rows() -> pd.DataFrame:
    df = _read_csv(PREDICTION_PATH, parse_month=True)
    if not df.empty:
        df["institution_code"] = df["institution_code"].astype(str)
        df["department"] = df["department"].astype(str)
        df["item_code"] = df["item_code"].astype(str)
        return df
    return pd.DataFrame(
        columns=[
            "year_month",
            "institution_code",
            "department",
            "item_code",
            "item_name",
            "stock_item_key",
            "predicted_usage",
            "primary_model",
        ]
    )


def _classified_prediction_rows() -> pd.DataFrame:
    if not CLASSIFIED_PREDICTION_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail=(
                "classified predictions not found. Run "
                "python -m src.modeling.classified_prediction first."
            ),
        )
    df = pd.read_csv(
        CLASSIFIED_PREDICTION_PATH,
        dtype={
            "institution_code": str,
            "department": str,
            "item_group_id": str,
            "item_family_id": str,
            "item_subtype_id": str,
            "normalized_specification": str,
            "unit_code": str,
        },
    )
    if "year_month" in df.columns:
        df["year_month"] = pd.to_datetime(df["year_month"]).dt.strftime("%Y-%m")
    return df


def _records_without_nan(df: pd.DataFrame) -> list[dict]:
    return df.astype(object).where(df.notna(), None).to_dict(orient="records")


def _institution_id(row: pd.Series) -> str:
    return str(row.get("institution_code", row.get("institutionId", "unknown")))


def _standard_code(row: pd.Series) -> str:
    return str(row.get("item_code", row.get("standardCode", "UNKNOWN")))


def _risk_level(score: float) -> str:
    if score >= 80:
        return "CRITICAL"
    if score >= 60:
        return "WARNING"
    if score >= 30:
        return "CAUTION"
    return "NORMAL"


def _not_found(message: str):
    raise HTTPException(
        status_code=404,
        detail={
            "error": {
                "code": "NOT_FOUND",
                "message": message,
                "traceId": uuid4().hex,
                "details": [],
            }
        },
    )


@app.get("/health")
@router.get("/health")
def health():
    return {"status": "ok", "service": "WeP-Stock AI Service", "version": "0.2.0"}


@router.get("/artifacts")
def artifacts():
    return {
        "predictions": {"path": str(PREDICTION_PATH), "exists": PREDICTION_PATH.exists()},
        "evaluationReport": {"path": str(EVALUATION_REPORT_PATH), "exists": EVALUATION_REPORT_PATH.exists()},
        "commodityRiskScores": {"path": str(COMMODITY_RISK_SCORE_PATH), "exists": COMMODITY_RISK_SCORE_PATH.exists()},
        "checkedAt": _now(),
    }


@router.post("/train", status_code=202)
def trigger_training(payload: ForecastRunRequest):
    return {
        "jobId": f"ai_train_{uuid4().hex[:8]}",
        "status": "QUEUED",
        "message": "AI 학습 배치 요청을 접수했습니다. 실제 job queue 연결은 백엔드/인프라 레이어에서 담당합니다.",
        "scope": payload.scope,
        "fromMonth": payload.fromMonth,
        "toMonth": payload.toMonth,
    }


@router.post("/forecasts/run", status_code=202)
def trigger_forecast_batch(payload: ForecastRunRequest):
    return {
        "jobId": f"ai_forecast_{uuid4().hex[:8]}",
        "status": "QUEUED",
        "message": "예측 배치 요청을 접수했습니다. 현재 API는 precomputed output을 서빙합니다.",
        "scope": payload.scope,
        "fromMonth": payload.fromMonth,
        "toMonth": payload.toMonth,
    }


@router.get("/forecasts")
def forecasts(
    institutionId: str | None = None,
    standardCode: str | None = None,
    department: str | None = None,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    page: int = 1,
    size: int = 50,
):
    from_ = from_ if isinstance(from_, str) else None
    to = to if isinstance(to, str) else None
    df = _prediction_rows()
    if institutionId:
        df = df[df.apply(lambda row: _institution_id(row) == institutionId, axis=1)]
    if standardCode:
        df = df[df["item_code"].astype(str) == standardCode]
    if department:
        df = df[df["department"].astype(str) == department]
    if from_:
        df = df[df["year_month"] >= from_]
    if to:
        df = df[df["year_month"] <= to]

    rows = [
        {
            "institutionId": _institution_id(row),
            "standardCode": _standard_code(row),
            "department": str(row.get("department", "")),
            "itemName": str(row.get("item_name", "")),
            "stockItemKey": str(row.get("stock_item_key", "")),
            "month": row["year_month"],
            "mean": float(row.get("predicted_usage", 0)),
            "q10": max(float(row.get("predicted_usage", 0)) * 0.5, 0),
            "q50": float(row.get("predicted_usage", 0)),
            "q90": float(row.get("predicted_usage", 0)) * 1.5,
            "confidence": 0.61,
            "championModel": str(row.get("primary_model", "baseline")),
            "externalRiskScore": float(row.get("external_risk_score", 0)),
        }
        for _, row in df.head(500).iterrows()
    ]
    return _page(rows, page, size)


@router.get("/forecasts/{institution_id}/{standard_code}")
def forecast_series(
    institution_id: str,
    standard_code: str,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
):
    rows = forecasts(institution_id, standard_code, None, from_, to, page=1, size=24)["content"]
    if not rows:
        _not_found("예측 결과가 없습니다.")
    return {
        "institutionId": institution_id,
        "standardCode": standard_code,
        "patternClass": "INTERMITTENT",
        "championModel": rows[0]["championModel"],
        "modelVersion": "ai-mvp-0.2",
        "horizon": rows,
        "dataQualityFlag": "ok",
    }


@router.get("/forecasts/eval")
def forecast_eval():
    df = _read_csv(EVALUATION_REPORT_PATH)
    if df.empty:
        return {"metrics": [], "message": "evaluation_report.csv가 없어 sample 리포트를 반환합니다."}
    return {"metrics": df.to_dict(orient="records")}


@router.get("/supply-risk")
def supply_risk(level: str | None = None, page: int = 1, size: int = 50):
    commodity = _read_csv(COMMODITY_RISK_SCORE_PATH)
    rows = []
    if not commodity.empty:
        for _, row in commodity.head(500).iterrows():
            score = float(row.get("commodity_risk", 0)) * 100
            item_level = _risk_level(score)
            if level and item_level != level:
                continue
            rows.append(
                {
                    "itemGroupId": str(row["stock_item_key"]),
                    "date": str(row["STD_YYYYMM"]),
                    "riskScore": round(score, 2),
                    "level": item_level,
                    "confidence": 0.68,
                    "source": "commodity_risk_scores.csv",
                }
            )
    return _page(rows, page, size)


@router.get("/supply-risk/{item_group_id}")
def supply_risk_detail(item_group_id: str):
    commodity = _read_csv(COMMODITY_RISK_SCORE_PATH)
    if commodity.empty or "stock_item_key" not in commodity.columns:
        _not_found("공급위험 결과가 없습니다.")
    matched = commodity[
        commodity["stock_item_key"].astype(str).eq(str(item_group_id))
    ]
    if matched.empty:
        _not_found("해당 품목의 공급위험 결과가 없습니다.")
    latest = matched.sort_values("STD_YYYYMM").iloc[-1]
    score = float(latest.get("commodity_risk", 0.0))
    return {
        "itemGroupId": item_group_id,
        "date": str(latest.get("STD_YYYYMM", "")),
        "riskScore": round(score * 100, 2),
        "level": _risk_level(score * 100),
        "confidence": float(latest.get("market_signal_confidence", 0.0)),
        "marketFactorIds": str(latest.get("market_factor_ids", "")),
        "eventCodes": str(latest.get("market_event_codes", "")),
        "source": "stock_commodity_risk_scores.csv",
    }


@router.get("/inventory-policy")
def inventory_policy(
    institutionId: str | None = None,
    standardCode: str | None = None,
    department: str | None = None,
    page: int = 1,
    size: int = 50,
):
    df = _prediction_rows()
    if institutionId:
        df = df[df.apply(lambda row: _institution_id(row) == institutionId, axis=1)]
    if standardCode:
        df = df[df["item_code"].astype(str) == standardCode]
    if department:
        df = df[df["department"].astype(str) == department]
    rows = []
    for _, row in df.head(500).iterrows():
        risk_policy = derive_supply_risk_level(
            row.get("supply_risk_meta_code", row.get("raw_material_risk_meta_code", "")),
            context=row.to_dict(),
        )
        required = ["mean_daily_usage", "daily_demand_stddev", "lead_time_days"]
        exact_inputs = all(
            column in row.index and pd.notna(row.get(column)) for column in required
        )
        calculated = None
        if exact_inputs:
            calculated = calculate_level_based_safety_stock(
                mean_daily_usage=float(row["mean_daily_usage"]),
                daily_demand_stddev=float(row["daily_demand_stddev"]),
                lead_time_days=float(row["lead_time_days"]),
                supply_risk_level=risk_policy["baseline_supply_risk_level"],
            )
        rows.append(
            {
                "institutionId": _institution_id(row),
                "standardCode": _standard_code(row),
                "department": str(row.get("department", "")),
                "SS": round(float(calculated["safety_stock"]), 2) if calculated else None,
                "ROP": round(float(calculated["reorder_point"]), 2) if calculated else None,
                "targetStock": (
                    float(row["target_stock"])
                    if "target_stock" in row.index and pd.notna(row.get("target_stock"))
                    else None
                ),
                "meanDailyUsage": (
                    float(calculated["lead_time_demand"])
                    / float(calculated["effective_lead_time_days"])
                    if calculated and float(calculated["effective_lead_time_days"]) > 0
                    else None
                ),
                "dailyDemandStddev": (
                    float(row["daily_demand_stddev"]) if calculated else None
                ),
                "leadTimeDays": (
                    float(row["lead_time_days"]) if calculated else None
                ),
                "effectiveLeadTimeDays": (
                    float(calculated["effective_lead_time_days"])
                    if calculated
                    else None
                ),
                "zUsed": float(calculated["z_value"]) if calculated else None,
                "baselineSupplyRiskLevel": risk_policy[
                    "baseline_supply_risk_level"
                ],
                "supplyRiskPolicyNeedsReview": risk_policy[
                    "supply_risk_policy_needs_review"
                ],
                "calculationStatus": (
                    "CALCULATED" if calculated else "INSUFFICIENT_DAILY_VARIANCE_OR_LEAD_TIME"
                ),
                "inventoryPolicyMethod": "level_based_daily_ss_rop",
                "assumedLeadTime": False,
                "generatedAt": _now(),
            }
        )
    return _page(rows, page, size)


@router.get("/inventory-policy/{institution_id}/{standard_code}")
def inventory_policy_detail(institution_id: str, standard_code: str):
    rows = inventory_policy(institution_id, standard_code, page=1, size=1)["content"]
    if not rows:
        _not_found("적정재고 계산 결과가 없습니다.")
    row = rows[0]
    sensitivity = []
    if row["calculationStatus"] == "CALCULATED":
        sensitivity = [
            {
                "scenario": "Lx1.2",
                "note": "상세 민감도는 명시적 일별 분산 입력으로 재계산 필요",
            },
            {
                "scenario": "Lx1.5",
                "note": "상세 민감도는 명시적 일별 분산 입력으로 재계산 필요",
            },
        ]
    return row | {
        "calculationReason": (
            "일별 평균사용량, 일별 수요표준편차, 리드타임과 결정론적 기준 공급레벨을 사용합니다."
        ),
        "sensitivity": sensitivity,
    }


@router.get("/order-recommendations")
def order_recommendations(
    institutionId: str | None = None,
    standardCode: str | None = None,
    department: str | None = None,
    page: int = 1,
    size: int = 50,
):
    policies = inventory_policy(institutionId, standardCode, department, page=1, size=500)["content"]
    rows = [
        {
            "institutionId": row["institutionId"],
            "standardCode": row["standardCode"],
            "recommendedOrder": None,
            "reason": (
                "현재고·입고예정·미납수량이 포함된 recommend-order 계약에서 계산해야 합니다."
            ),
            "generatedAt": row["generatedAt"],
        }
        for row in policies
    ]
    return _page(rows, page, size)


@router.post("/recommend-order")
def recommend_order(payload: RecommendOrderRequest):
    df = _prediction_rows()
    mask = (
        (df["year_month"] == payload.yyyymm)
        & (df["item_code"] == str(payload.item_code))
        & (df["institution_code"] == str(payload.institution_code))
    )
    if payload.department is not None:
        mask &= df["department"] == str(payload.department)
    result = df[mask].copy()
    if result.empty:
        _not_found("예측 결과가 없습니다.")
    if (
        "is_stale_data" in result.columns
        and result["is_stale_data"].astype(str).str.lower().eq("true").any()
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Prediction is stale. Load newer raw_stock data and rerun the "
                "forecast pipeline."
            ),
        )

    result["current_stock"] = payload.current_stock
    result["lead_time_days"] = payload.lead_time_days
    result["review_period_days"] = payload.review_period_days
    result["on_order_qty"] = payload.on_order_qty
    result["backorder_qty"] = payload.backorder_qty
    result = add_inventory_recommendations(
        result,
        prediction_col="predicted_usage",
        current_stock_col="current_stock",
        lead_time_days_col="lead_time_days",
        review_period_days_col="review_period_days",
        on_order_qty_col="on_order_qty",
        backorder_qty_col="backorder_qty",
    )
    row = result.iloc[0]
    mapping_count = pd.to_numeric(
        pd.Series([row.get("approved_material_mapping_count", 0)]),
        errors="coerce",
    ).fillna(0).iloc[0]
    has_approved_mapping = str(
        row.get("has_approved_material_mapping", "")
    ).strip().lower() in {"true", "t", "1", "yes", "y"}
    return {
        "predicted_usage": float(row["predicted_usage"]),
        "risk_adjusted_predicted_usage": float(
            row.get("risk_adjusted_predicted_usage", row["predicted_usage"])
        ),
        "protection_period_days": float(row["protection_period_days"]),
        "protection_period_demand": float(row["protection_period_demand"]),
        "safety_stock": float(row["safety_stock"]),
        "risk_adjusted_safety_stock": float(
            row.get("risk_adjusted_safety_stock", row["safety_stock"])
        ),
        "base_stock": float(row["base_stock"]),
        "demand_risk_buffer": float(row["demand_risk_buffer"]),
        "supply_risk_buffer": float(row["supply_risk_buffer"]),
        "material_risk_buffer": float(row["material_risk_buffer"]),
        "risk_buffer": float(row["risk_buffer"]),
        "target_stock": float(row["target_stock"]),
        "effective_lead_time_days": float(
            row.get("effective_lead_time_days", row["lead_time_days"])
        ),
        "dynamic_safety_stock_rate": float(
            row.get("dynamic_safety_stock_rate", 0.0)
        ),
        "module_c_policy_applied": bool(row.get("module_c_policy_applied", False)),
        "module_c_demand_embedded_in_forecast": bool(
            row.get("module_c_demand_embedded_in_forecast", False)
        ),
        "module_c_policy_demand_uplift_applied": bool(
            row.get("module_c_policy_demand_uplift_applied", False)
        ),
        "module_c_config_version": str(
            row.get("module_c_config_version", "legacy-risk-policy")
        ),
        "module_c_calibration_status": str(
            row.get("module_c_calibration_status", "legacy-policy")
        ),
        "inventory_policy_method": str(
            row.get("inventory_policy_method", "legacy_fixed_rate_target_stock")
        ),
        "recommended_stock": float(row["recommended_stock"]),
        "current_stock": float(row["current_stock"]),
        "on_order_qty": float(row["on_order_qty"]),
        "backorder_qty": float(row["backorder_qty"]),
        "inventory_position": float(row["inventory_position"]),
        "recommended_order": float(row["recommended_order"]),
        "risk_scores": {
            "demand": float(row["demand_risk_score"]),
            "supply": float(row["supply_risk_score"]),
            "material": float(row["material_risk_score"]),
            "external": float(row["external_risk_score"]),
        },
        "approved_material_mapping_count": int(mapping_count),
        "has_approved_material_mapping": has_approved_mapping,
        "risk_summary": "승인된 매핑 기반 수요·공급·원자재 위험 버퍼를 반영했습니다.",
    }


@app.get("/predictions")
def get_predictions(
    yyyymm: str = Query(...),
    item_code: str = Query(...),
    institution_code: str = Query(...),
    department: str | None = Query(default=None),
):
    df = _prediction_rows()
    mask = (
        (df["year_month"] == yyyymm)
        & (df["item_code"] == str(item_code))
        & (df["institution_code"] == str(institution_code))
    )
    if department is not None:
        mask &= df["department"] == str(department)
    result = df[mask]
    if result.empty:
        _not_found("예측 결과가 없습니다.")
    return _records_without_nan(result.head(50))


@app.get("/predictions/by-subtype")
@router.get("/predictions/by-subtype")
def get_predictions_by_subtype(
    yyyymm: str = Query(...),
    institution_code: str = Query(...),
    department: str | None = Query(default=None),
    item_group_id: str | None = Query(default=None),
    item_family_id: str | None = Query(default=None),
    item_subtype_id: str | None = Query(default=None),
    normalized_specification: str | None = Query(default=None),
    unit_code: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
):
    df = _classified_prediction_rows()
    mask = (df["year_month"] == yyyymm) & (
        df["institution_code"] == str(institution_code)
    )
    filters = {
        "department": department,
        "item_group_id": item_group_id,
        "item_family_id": item_family_id,
        "item_subtype_id": item_subtype_id,
        "normalized_specification": normalized_specification,
        "unit_code": unit_code,
    }
    for column, value in filters.items():
        if value is None:
            continue
        if column not in df.columns:
            raise HTTPException(
                status_code=400,
                detail=f"Classified prediction scope does not contain {column}",
            )
        mask &= df[column].astype(str) == str(value)
    result = df[mask]
    if result.empty:
        _not_found("세부유형 예측 결과가 없습니다.")
    return _records_without_nan(result.head(limit))


@app.post("/recommend-order")
def legacy_recommend_order(payload: RecommendOrderRequest):
    return recommend_order(payload)


app.include_router(router)
