from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pandas as pd
from fastapi import APIRouter, FastAPI, File, HTTPException, Query, UploadFile

from ..config import (
    COMMODITY_RISK_SCORE_PATH,
    EVALUATION_REPORT_PATH,
    FEATURE_TABLE_PATH,
    MONTHLY_USAGE_PATH,
    NEWS_RISK_SCORE_PATH,
    PREDICTION_PATH,
)
from ..inventory_policy import add_inventory_recommendations
from .schemas import (
    AlertResolveRequest,
    AlertSettingsRequest,
    DictionaryRequest,
    ForecastRunRequest,
    ImportReprocessRequest,
    InventoryPolicyRunRequest,
    LoginRequest,
    MappingDecisionRequest,
    MaterialDependencyUpdateRequest,
    RecommendOrderRequest,
    RefreshRequest,
    RelocationDecisionRequest,
    UserCreateRequest,
    UserUpdateRequest,
)


app = FastAPI(
    title="WeP-Stock API",
    version="0.1.0",
    description="전국 보건기관 의료물품 통합 재고 관리 웹서비스 WeP-Stock REST API 초안",
)
router = APIRouter(prefix="/api/v1")


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
        df["SIDO"] = df["SIDO"].astype(str)
        df["MED_DEVICE_5"] = df["MED_DEVICE_5"].astype(str)
        return df
    return pd.DataFrame(
        [
            {
                "year_month": "2026-07",
                "SIDO": "11",
                "MED_DEVICE_5": "KD0192",
                "actual_usage": 0.0,
                "predicted_usage": 8.3,
                "recommended_stock": 19.8,
                "external_risk_score": 0.42,
                "disease_news_risk": 0.18,
                "supply_news_risk": 0.51,
                "commodity_risk": 0.34,
                "primary_model": "SBA",
            }
        ]
    )


def _standard_code(row: pd.Series) -> str:
    return str(row.get("MED_DEVICE_5", row.get("standardCode", "UNKNOWN")))


def _institution_id(row: pd.Series) -> str:
    sido = str(row.get("SIDO", row.get("institutionId", "000")))
    return sido if sido.startswith("inst_") else f"inst_{sido}"


def _risk_level(score: float) -> str:
    if score >= 80:
        return "CRITICAL"
    if score >= 60:
        return "WARNING"
    if score >= 30:
        return "CAUTION"
    return "NORMAL"


def _api_error(status_code: int, code: str, message: str, details: list[dict] | None = None):
    raise HTTPException(
        status_code=status_code,
        detail={
            "error": {
                "code": code,
                "message": message,
                "traceId": uuid4().hex,
                "details": details or [],
            }
        },
    )


@app.get("/health")
@router.get("/health")
def health():
    return {"status": "ok", "service": "WeP-Stock", "version": "0.1.0"}


@router.post("/auth/login")
def login(payload: LoginRequest):
    role = "CENTRAL" if payload.loginId.startswith("central") else "SYS" if payload.loginId.startswith("sys") else "INST"
    return {
        "accessToken": f"dev-access-{uuid4().hex}",
        "refreshToken": f"dev-refresh-{uuid4().hex}",
        "expiresIn": 3600,
        "user": {
            "id": f"u_{payload.loginId}",
            "loginId": payload.loginId,
            "role": role,
            "institutionId": None if role != "INST" else "inst_11",
        },
    }


@router.post("/auth/refresh")
def refresh_token(payload: RefreshRequest):
    return {"accessToken": f"dev-access-{uuid4().hex}", "refreshToken": payload.refreshToken, "expiresIn": 3600}


@router.post("/auth/logout")
def logout():
    return {"status": "LOGGED_OUT"}


@router.get("/users/me")
def me():
    return {"id": "u_dev", "loginId": "dev", "role": "SYS", "institutionId": None}


@router.get("/users")
def list_users(page: int = 1, size: int = 50):
    return _page(
        [
            {"id": "u_inst_11", "loginId": "inst11", "role": "INST", "institutionId": "inst_11", "status": "ACTIVE"},
            {"id": "u_central", "loginId": "central01", "role": "CENTRAL", "institutionId": None, "status": "ACTIVE"},
            {"id": "u_sys", "loginId": "sys01", "role": "SYS", "institutionId": None, "status": "ACTIVE"},
        ],
        page,
        size,
    )


@router.post("/users", status_code=201)
def create_user(payload: UserCreateRequest):
    return {"id": f"u_{uuid4().hex[:8]}", **payload.model_dump(), "createdAt": _now()}


@router.put("/users/{user_id}")
def update_user(user_id: str, payload: UserUpdateRequest):
    return {"id": user_id, **payload.model_dump(exclude_none=True), "updatedAt": _now()}


@router.post("/imports", status_code=202)
async def create_import(file: UploadFile = File(...), sourceVendorId: str | None = None, headerTemplateId: str | None = None):
    content = await file.read()
    file_hash = f"sha256:{sha256(content).hexdigest()}"
    batch_id = f"ib_{datetime.now().strftime('%Y%m%d')}_{uuid4().hex[:6]}"
    return {
        "importBatchId": batch_id,
        "status": "VALIDATING",
        "fileHash": file_hash,
        "fileName": file.filename,
        "sourceVendorId": sourceVendorId,
        "headerTemplateId": headerTemplateId,
        "statusUrl": f"/api/v1/imports/{batch_id}",
    }


@router.get("/imports")
def list_imports(status: str | None = None, vendor: str | None = None, period: str | None = None, page: int = 1, size: int = 50):
    items = [
        {
            "importBatchId": "ib_sample_001",
            "status": status or "COMPLETED",
            "sourceVendorId": vendor or "v_03",
            "period": period or "2026-06",
            "totalRows": 14435,
            "validRows": 14435,
            "errorRows": 0,
            "mappingRate": 0.91,
        }
    ]
    return _page(items, page, size)


@router.get("/imports/{batch_id}")
def get_import(batch_id: str):
    return {
        "importBatchId": batch_id,
        "status": "COMPLETED",
        "totalRows": 14435,
        "validRows": 14435,
        "errorRows": 0,
        "warningRows": 0,
        "mappingRate": 0.91,
        "schemaVersion": "v0.1",
    }


@router.get("/imports/{batch_id}/errors")
def get_import_errors(batch_id: str, page: int = 1, size: int = 50):
    return _page([], page, size) | {"importBatchId": batch_id}


@router.get("/imports/{batch_id}/report")
def get_import_report(batch_id: str):
    return {
        "importBatchId": batch_id,
        "reportUrl": f"/api/v1/imports/{batch_id}/report.csv",
        "qualitySummary": {"errors": 0, "warnings": 0, "mappingRate": 0.91},
    }


@router.post("/imports/{batch_id}/reprocess")
def reprocess_import(batch_id: str, payload: ImportReprocessRequest):
    return {"importBatchId": batch_id, "status": "VALIDATING", "scope": payload.scope, "requestedAt": _now()}


@router.get("/standardization/queue")
def standardization_queue(status: str | None = None, institution: str | None = None, priority: str | None = None, page: int = 1, size: int = 50):
    item = {
        "rawItemId": "raw_8842",
        "rawName": "10cc 주사기",
        "normalizedName": "10mL 주사기",
        "status": status or "NEEDS_REVIEW",
        "institutionId": institution or "inst_11",
        "priority": priority or "HIGH",
        "suggestionCount": 3,
    }
    return _page([item], page, size)


@router.get("/standardization/queue/{raw_item_id}")
def standardization_detail(raw_item_id: str):
    return {
        "rawItemId": raw_item_id,
        "rawName": "10cc 주사기",
        "normalizedName": "10mL 주사기",
        "candidates": [
            {"standardItemId": "si_KD0192", "standardCode": "KD0192", "standardName": "주사기 10mL", "finalScore": 0.94},
            {"standardItemId": "si_KD0193", "standardCode": "KD0193", "standardName": "주사기 5mL", "finalScore": 0.61},
        ],
        "explanation": {"matched": ["주사기", "10mL"], "mismatched": []},
    }


@router.post("/standardization/decisions", status_code=201)
def decide_mapping(payload: MappingDecisionRequest):
    return {"decisionId": f"dec_{uuid4().hex[:8]}", "appliedRows": 1240, "masterVersion": "2026-06-01", **payload.model_dump()}


@router.post("/standardization/dictionary", status_code=201)
def add_dictionary(payload: DictionaryRequest):
    return {"dictionaryId": f"dict_{uuid4().hex[:8]}", **payload.model_dump(), "createdAt": _now()}


@router.get("/standardization/report")
def standardization_report():
    return {"normalizationRate": 0.91, "autoAcceptRate": 0.84, "needsReviewBacklog": 1, "unmappedTopN": []}


@router.get("/standard-items")
def standard_items(q: str | None = None, group: str | None = None, page: int = 1, size: int = 50):
    df = _prediction_rows()
    codes = sorted(df["MED_DEVICE_5"].astype(str).unique())[:size] if not df.empty else ["KD0192"]
    items = [
        {
            "standardItemId": f"si_{code}",
            "standardCode": code,
            "standardName": f"표준품목 {code}",
            "itemGroupId": group or "ig_general",
            "uom": "EA",
        }
        for code in codes
        if not q or q.lower() in code.lower()
    ]
    return _page(items, page, size)


@router.get("/forecasts")
def forecasts(institution: str | None = None, item: str | None = None, from_: str | None = Query(None, alias="from"), to: str | None = None, page: int = 1, size: int = 50):
    from_ = from_ if isinstance(from_, str) else None
    to = to if isinstance(to, str) else None
    df = _prediction_rows()
    if institution:
        df = df[df.apply(lambda row: _institution_id(row) == institution, axis=1)]
    if item:
        df = df[df["MED_DEVICE_5"].astype(str) == item]
    if from_:
        df = df[df["year_month"] >= from_]
    if to:
        df = df[df["year_month"] <= to]
    rows = [
        {
            "institutionId": _institution_id(row),
            "standardCode": _standard_code(row),
            "month": row["year_month"],
            "mean": float(row.get("predicted_usage", 0)),
            "q10": max(float(row.get("predicted_usage", 0)) * 0.5, 0),
            "q50": float(row.get("predicted_usage", 0)),
            "q90": float(row.get("predicted_usage", 0)) * 1.5,
            "patternClass": "INTERMITTENT",
            "championModel": str(row.get("primary_model", "baseline")),
        }
        for _, row in df.head(500).iterrows()
    ]
    return _page(rows, page, size)


@router.get("/forecasts/{institution_id}/{standard_code}")
def forecast_series(institution_id: str, standard_code: str, from_: str | None = Query(None, alias="from"), to: str | None = None):
    rows = forecasts(institution_id, standard_code, from_, to, page=1, size=24)["content"]
    if not rows:
        _api_error(404, "NOT_FOUND", "예측 결과가 없습니다.")
    return {
        "institutionId": institution_id,
        "standardCode": standard_code,
        "patternClass": rows[0]["patternClass"],
        "championModel": rows[0]["championModel"],
        "modelVersion": "mvp-0.1",
        "horizon": [
            {
                "month": row["month"],
                "mean": row["mean"],
                "q10": row["q10"],
                "q50": row["q50"],
                "q90": row["q90"],
                "confidence": 0.61,
            }
            for row in rows
        ],
        "dataQualityFlag": "ok",
    }


@router.post("/forecasts/run", status_code=202)
def run_forecasts(payload: ForecastRunRequest):
    return {"jobId": f"forecast_{uuid4().hex[:8]}", "status": "QUEUED", **payload.model_dump()}


@router.get("/forecasts/eval")
def forecast_eval():
    df = _read_csv(EVALUATION_REPORT_PATH)
    if df.empty:
        return {"metrics": [], "message": "evaluation_report.csv가 없어 sample 리포트를 반환합니다."}
    return {"metrics": df.to_dict(orient="records")}


@router.get("/supply-risk")
def supply_risk(level: str | None = None, from_: str | None = Query(None, alias="from"), to: str | None = None, page: int = 1, size: int = 50):
    from_ = from_ if isinstance(from_, str) else None
    to = to if isinstance(to, str) else None
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
                    "itemGroupId": f"ig_{row['MED_DEVICE_5']}",
                    "date": str(row["STD_YYYYMM"]),
                    "riskScore": round(score, 2),
                    "level": item_level,
                    "confidence": 0.68,
                }
            )
    if not rows:
        rows = [{"itemGroupId": "ig_plastic_consumable", "date": "2026-06-29", "riskScore": 82, "level": "CRITICAL", "confidence": 0.73}]
    return _page(rows, page, size)


@router.get("/supply-risk/{item_group_id}")
def supply_risk_detail(item_group_id: str):
    return {
        "itemGroupId": item_group_id,
        "date": "2026-06-29",
        "riskScore": 82,
        "level": "CRITICAL",
        "leadTimeEstimate": 21,
        "confidence": 0.73,
        "topContributors": [{"materialType": "naphtha", "contrib": 0.51, "lagDays": 14}],
        "evidenceNews": [{"newsId": "n_771", "title": "호르무즈 해협 긴장 고조", "url": "https://example.com", "publishedAt": "2026-06-27"}],
    }


@router.get("/supply-risk/backtest")
def supply_risk_backtest():
    return {"medianLeadDays": 14, "iqrLeadDays": [7, 21], "falsePositiveRate": None, "falseNegativeRate": None, "status": "MVP_SAMPLE"}


@router.get("/material-dependency")
def material_dependency(page: int = 1, size: int = 50):
    return _page(
        [
            {"itemGroupId": "ig_plastic_consumable", "materialType": "naphtha", "dependencyWeight": 0.8, "rationale": "플라스틱 계열 소모품"},
            {"itemGroupId": "ig_latex", "materialType": "rubber", "dependencyWeight": 0.7, "rationale": "의료용 장갑 계열"},
        ],
        page,
        size,
    )


@router.put("/material-dependency/{item_group_id}")
def update_material_dependency(item_group_id: str, payload: MaterialDependencyUpdateRequest):
    return {"itemGroupId": item_group_id, **payload.model_dump(), "updatedAt": _now()}


@router.get("/inventory-policy")
def inventory_policy(institution: str | None = None, item: str | None = None, page: int = 1, size: int = 50):
    df = _prediction_rows()
    if institution:
        df = df[df.apply(lambda row: _institution_id(row) == institution, axis=1)]
    if item:
        df = df[df["MED_DEVICE_5"].astype(str) == item]
    rows = []
    for _, row in df.head(500).iterrows():
        mu = float(row.get("predicted_usage", 0))
        sigma = max(mu * 0.5, 1)
        l_used = 1.25
        z_used = 1.65 if float(row.get("external_risk_score", 0)) < 0.5 else 2.05
        ss = z_used * sigma * (l_used**0.5)
        rop = mu * l_used + ss
        rows.append(
            {
                "institutionId": _institution_id(row),
                "standardCode": _standard_code(row),
                "SS": round(ss, 2),
                "ROP": round(rop, 2),
                "mu": round(mu, 2),
                "L_used": l_used,
                "z_used": z_used,
                "assumedLeadTime": True,
                "generatedAt": _now(),
            }
        )
    return _page(rows, page, size)


@router.get("/inventory-policy/{institution_id}/{standard_code}")
def inventory_policy_detail(institution_id: str, standard_code: str):
    rows = inventory_policy(institution_id, standard_code, page=1, size=1)["content"]
    if not rows:
        _api_error(404, "NOT_FOUND", "적정재고 계산 결과가 없습니다.")
    row = rows[0]
    return row | {
        "calculationReason": "공급위험과 리드타임 가정값을 반영했습니다.",
        "sensitivity": [
            {"scenario": "L×1.2", "SS": round(row["SS"] * 1.1, 2), "ROP": round(row["ROP"] * 1.1, 2)},
            {"scenario": "L×1.5", "SS": round(row["SS"] * 1.25, 2), "ROP": round(row["ROP"] * 1.25, 2)},
        ],
    }


@router.post("/inventory-policy/run", status_code=202)
def run_inventory_policy(payload: InventoryPolicyRunRequest):
    return {"jobId": f"policy_{uuid4().hex[:8]}", "status": "QUEUED", **payload.model_dump()}


@router.get("/order-recommendations")
def order_recommendations(institution: str | None = None, item: str | None = None, page: int = 1, size: int = 50):
    policies = inventory_policy(institution, item, page=1, size=500)["content"]
    rows = [
        {
            "institutionId": row["institutionId"],
            "standardCode": row["standardCode"],
            "recommendedOrder": max(round(row["ROP"] - row["mu"], 2), 0),
            "reason": "available < ROP 가정 기반 MVP 권고",
            "generatedAt": row["generatedAt"],
        }
        for row in policies
    ]
    return _page(rows, page, size)


@router.get("/relocations")
def relocations(page: int = 1, size: int = 50):
    return _page(
        [
            {
                "id": "reloc_001",
                "fromInstitution": "inst_41",
                "toInstitution": "inst_11",
                "standardCode": "KD0192",
                "suggestedQty": 12,
                "reason": "부족 기관과 여유 기관 매칭",
                "status": "PROPOSED",
            }
        ],
        page,
        size,
    )


@router.post("/relocations/{relocation_id}/approve")
def approve_relocation(relocation_id: str, payload: RelocationDecisionRequest):
    return {"id": relocation_id, "status": payload.status, "memo": payload.memo, "decidedAt": _now()}


@router.get("/alerts")
def alerts(type: str | None = None, severity: str | None = None, institution: str | None = None, resolved: bool | None = None, page: int = 1, size: int = 50):
    row = {
        "alertId": "alert_001",
        "alertType": type or "STOCK_BELOW_ROP",
        "severity": severity or "WARNING",
        "institutionId": institution or "inst_11",
        "standardCode": "KD0192",
        "generatedAt": _now(),
        "resolvedAt": None if not resolved else _now(),
        "title": "재고가 재주문점 이하로 하락했습니다.",
        "message": "권장 발주량을 확인하세요.",
        "evidence": {"available": 5, "ROP": 19.8},
    }
    return _page([row], page, size)


@router.get("/alerts/settings")
def alert_settings():
    return {"cooldownHours": 24, "thresholds": {"stockBelowRopRatio": 1.0, "expiryDays": [30, 60, 90]}}


@router.put("/alerts/settings")
def update_alert_settings(payload: AlertSettingsRequest):
    return payload.model_dump() | {"updatedAt": _now()}


@router.get("/alerts/{alert_id}")
def alert_detail(alert_id: str):
    return alerts(page=1, size=1)["content"][0] | {"alertId": alert_id}


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: str, payload: AlertResolveRequest):
    return {"alertId": alert_id, "status": "RESOLVED", "memo": payload.memo, "resolvedAt": _now()}


@router.get("/dashboard/institution/{institution_id}")
def dashboard_institution(institution_id: str):
    policy = inventory_policy(institution_id, None, page=1, size=10)["content"]
    return {
        "institutionId": institution_id,
        "summary": {"itemCount": len(policy), "belowRopCount": len(policy), "criticalAlertCount": 0},
        "inventoryPolicy": policy,
        "alerts": alerts(institution=institution_id, page=1, size=5)["content"],
    }


@router.get("/dashboard/central")
def dashboard_central():
    return {
        "nationalSummary": {"institutionCount": 3500, "trackedItemCount": len(_prediction_rows()["MED_DEVICE_5"].unique())},
        "riskHeatmap": supply_risk(page=1, size=10)["content"],
        "relocationSummary": relocations(page=1, size=5)["content"],
    }


@router.get("/dashboard/ops")
def dashboard_ops():
    return {
        "imports": list_imports(size=5)["content"],
        "mapping": standardization_report(),
        "batchStatus": {"forecast": "READY", "risk": "READY", "inventoryPolicy": "READY"},
    }


@router.get("/external-indicators")
def external_indicators(type: str | None = None, from_: str | None = Query(None, alias="from"), to: str | None = None, page: int = 1, size: int = 50):
    from_ = from_ if isinstance(from_, str) else None
    to = to if isinstance(to, str) else None
    rows = [
        {"indicatorId": "ind_naphtha", "indicatorType": type or "NAPHTHA_PRICE", "observedAt": "2026-06-29", "value": 650, "unit": "USD/t"},
        {"indicatorId": "ind_news", "indicatorType": "NEWS_RISK_INDEX", "observedAt": "2026-06-29", "value": 82, "unit": "score"},
    ]
    return _page(rows, page, size)


@router.post("/external-indicators/refresh", status_code=202)
def refresh_external_indicators():
    return {"jobId": f"external_{uuid4().hex[:8]}", "status": "QUEUED", "requestedAt": _now()}


@router.get("/institutions")
def institutions(q: str | None = None, page: int = 1, size: int = 50):
    rows = [
        {"institutionId": "inst_11", "institutionName": "서울 보건기관", "institutionType": "HEALTH_CENTER", "regionCode": "11", "status": "ACTIVE"},
        {"institutionId": "inst_41", "institutionName": "경기 보건기관", "institutionType": "HEALTH_CENTER", "regionCode": "41", "status": "ACTIVE"},
    ]
    if q:
        rows = [row for row in rows if q in row["institutionName"] or q in row["institutionId"]]
    return _page(rows, page, size)


@router.get("/item-groups")
def item_groups(page: int = 1, size: int = 50):
    return _page(
        [
            {"itemGroupId": "ig_plastic_consumable", "itemGroupName": "플라스틱 소모품", "criticality": "CONSUMABLE"},
            {"itemGroupId": "ig_medical_realtime", "itemGroupName": "의료용품 실시간 관리", "criticality": "MEDICAL"},
        ],
        page,
        size,
    )


@app.get("/predictions")
def legacy_predictions(yyyymm: str, item_code: str, sido: str | None = None):
    rows = forecasts(institution=f"inst_{sido}" if sido else None, item=item_code, from_=yyyymm, to=yyyymm, page=1, size=50)["content"]
    if not rows:
        _api_error(404, "NOT_FOUND", "Prediction not found")
    return rows


@app.post("/recommend-order")
def legacy_recommend_order(payload: RecommendOrderRequest):
    df = _prediction_rows()
    mask = (df["year_month"] == payload.yyyymm) & (df["MED_DEVICE_5"] == str(payload.item_code))
    if payload.sido is not None:
        mask &= df["SIDO"] == str(payload.sido)
    result = df[mask].copy()
    if result.empty:
        _api_error(404, "NOT_FOUND", "Prediction not found")
    result["current_stock"] = payload.current_stock
    result["lead_time_days"] = payload.lead_time_days
    result = add_inventory_recommendations(result, "predicted_usage", "current_stock", "lead_time_days")
    row = result.iloc[0]
    return {
        "predicted_usage": float(row["predicted_usage"]),
        "recommended_stock": float(row["recommended_stock"]),
        "current_stock": float(row["current_stock"]),
        "recommended_order": float(row["recommended_order"]),
        "risk_summary": "배치 계산된 외부 위험 점수와 안전재고 정책을 반영했습니다.",
    }


app.include_router(router)
