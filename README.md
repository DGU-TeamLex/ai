# WeP-Stock AI/API MVP

전국 보건기관 의료물품 통합 재고 관리 웹서비스 **WeP-Stock**의 AI/백엔드 MVP입니다.

본 저장소는 기능 명세서 v0.1 기준으로 다음 파이프라인과 REST API 초안을 제공합니다.

```text
파일 인테이크
→ 물품 표준화
→ 수요 예측
→ 공급위험 조기경보
→ 적정재고/발주권고/재배치
→ 알림/대시보드
```

현재 구현은 실제 DB/잡큐/외부 API 연결 전 단계의 **CSV + sample fallback 기반 MVP**입니다. 프론트엔드와 백엔드 개발 착수를 위해 API path, request/response shape, 모듈 경계를 명세서에 맞춰 고정하는 것을 우선했습니다.

## Branch Policy

```text
main: 완성본만 병합
dev: 개발 통합 브랜치
feature/*: 기능 작업 브랜치, PR 대상은 dev
```

`main`에는 직접 push하지 않습니다.

## Implemented Scope

### Batch/AI

```text
src/data_loader.py              의료기기 사용량 CSV 로드
src/preprocessing.py            월별 사용량 집계
src/feature_engineering.py      예측 feature table 생성
src/train_model.py              Model A/B/C 학습
src/predict.py                  예측 및 권장 재고 산출
src/inventory_policy.py         safety stock / recommended stock 정책
src/news/                       sample 뉴스 위험 점수
src/commodity/                  sample 원자재 위험 점수
```

### WeP-Stock API

`src/serving/api.py`는 명세서의 `/api/v1` path를 MVP 수준으로 구현합니다.

```text
인증/사용자
POST /api/v1/auth/login
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
GET  /api/v1/users/me
GET  /api/v1/users
POST /api/v1/users
PUT  /api/v1/users/{id}

파일 인테이크
POST /api/v1/imports
GET  /api/v1/imports
GET  /api/v1/imports/{batchId}
GET  /api/v1/imports/{batchId}/errors
GET  /api/v1/imports/{batchId}/report
POST /api/v1/imports/{batchId}/reprocess

물품 표준화
GET  /api/v1/standardization/queue
GET  /api/v1/standardization/queue/{rawItemId}
POST /api/v1/standardization/decisions
POST /api/v1/standardization/dictionary
GET  /api/v1/standardization/report
GET  /api/v1/standard-items

수요 예측
GET  /api/v1/forecasts
GET  /api/v1/forecasts/{institutionId}/{standardCode}
POST /api/v1/forecasts/run
GET  /api/v1/forecasts/eval

공급위험
GET  /api/v1/supply-risk
GET  /api/v1/supply-risk/{itemGroupId}
GET  /api/v1/supply-risk/backtest
GET  /api/v1/material-dependency
PUT  /api/v1/material-dependency/{itemGroupId}

적정재고/발주/재배치
GET  /api/v1/inventory-policy
GET  /api/v1/inventory-policy/{institutionId}/{standardCode}
POST /api/v1/inventory-policy/run
GET  /api/v1/order-recommendations
GET  /api/v1/relocations
POST /api/v1/relocations/{id}/approve

알림/대시보드/외부지표/마스터
GET  /api/v1/alerts
GET  /api/v1/alerts/{id}
POST /api/v1/alerts/{id}/resolve
GET  /api/v1/alerts/settings
PUT  /api/v1/alerts/settings
GET  /api/v1/dashboard/institution/{institutionId}
GET  /api/v1/dashboard/central
GET  /api/v1/dashboard/ops
GET  /api/v1/external-indicators
POST /api/v1/external-indicators/refresh
GET  /api/v1/institutions
GET  /api/v1/item-groups
```

기존 MVP 호환용으로 아래 legacy endpoint도 유지합니다.

```text
GET  /health
GET  /predictions
POST /recommend-order
```

## Setup

```bash
conda activate teamlex
pip install -r requirements.txt
```

## Batch Run

전체 배치:

```bash
python -m src.main
```

단계별 실행:

```bash
python -m src.preprocessing
python -m src.news.news_risk_scorer
python -m src.commodity.commodity_risk_scorer
python -m src.feature_engineering
python -m src.train_model
python -m src.predict
python -m src.evaluate
```

## API Run

```bash
uvicorn src.serving.api:app --reload
```

Swagger 문서:

```text
http://127.0.0.1:8000/docs
```

## Dashboard

```bash
streamlit run src/dashboard/app.py
```

## Data/Artifact Policy

아래 파일은 GitHub에 올리지 않습니다.

```text
device/
data/raw/
data/processed/
outputs/
models/
.env
```

commit 가능한 것은 source code, docs, issue/PR template, sample mapping seed입니다.

