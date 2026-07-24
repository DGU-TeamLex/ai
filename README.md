# WeP-Stock AI Service

WeP-Stock 의 **AI 학습 · 예측 · 위험 점수 · 재고 권고 서빙 전용** 저장소입니다.

> **브랜치 안내** — `main` 은 완성본만 병합하는 브랜치라 아직 비어 있습니다.
> 아래 내용은 개발 통합 브랜치 **`dev`** 에 있는 실제 구현 기준입니다.
> ```
> main       완성본만 병합 (직접 push 금지)
> dev        개발 통합 브랜치
> feature/*  기능 작업 브랜치, PR 대상은 dev
> ```

## 소개

전국 보건기관 의료물품 재고관리 서비스 WeP-Stock 의 AI 파이프라인입니다. 제품 백엔드 기능(인증·업로드·대시보드 API 등)은 [DGU-TeamLex/backend](https://github.com/DGU-TeamLex/backend) 가 담당하고, 이 저장소는 다음 흐름만 책임집니다.

```text
의료물품 사용량 데이터 전처리
→ 수요 예측 feature 생성
→ baseline / ML 모델 학습
→ 뉴스·원자재 위험 점수 생성
→ 예측 결과 및 재고 권고 산출
→ AI serving API 제공
```

### Out Of Scope

아래는 이 저장소에서 구현하지 않습니다 (backend 담당).

```text
인증 / 사용자 / 권한 / RBAC
파일 업로드 / import_batch / 재처리 관리
물품 표준화 검수 워크플로우
기관/중앙/운영 대시보드 aggregation API
알림 상태 관리 및 발송
재배치 승인 워크플로우
DB 트랜잭션, 감사 로그, row-level permission
```

## ✨ 주요 기능

### 수요 예측 모델 (모듈 B)

- 시계열 분할 학습 — 학습 `~2023-12` / 검증 `2024-01~2024-12` / 테스트 `2025-01~`
- 외부 신호 기여도를 분리 측정하기 위한 **3개 모델 변형**을 동일 파이프라인으로 학습·비교

  | 모델 | 사용 feature |
  |---|---|
  | `model_a_usage_only` | 사용량만 |
  | `model_b_news` | 사용량 + 뉴스 위험 |
  | `model_c_news_commodity` | 사용량 + 뉴스 + 원자재 위험 |

- 집계 단위는 `year_month × SIDO × MED_DEVICE_5`, 평가 지표는 MAE / RMSE / MAPE / SMAPE / WAPE (`src/modeling/metrics.py`, 그룹별 집계 지원)

### 뉴스 · 원자재 공급위험 (모듈 C)

- `src/news/` — 수집(`news_collector`) → 키워드 필터(`news_filter`) → LLM 분석(`news_llm_analyzer`) → 위험 점수(`news_risk_scorer`)
- `src/commodity/` — 원자재 가격 수집 → feature 생성 → 위험 점수(`commodity_risk_scorer`)
- 품목-원자재 매핑과 국가 가중치는 `data/mapping/` 의 seed CSV 로 관리

### 재고 정책 (모듈 D)

`src/modeling/inventory_policy.py` 가 예측 수요에 외부 위험 점수를 반영해 safety stock / recommended stock 을 산출합니다. 기본 안전재고율 20%, 위험 버퍼 상한 50% (`src/config.py`).

### 운영 산출물 적재 (`src/loading/`)

실데이터 운영 중 발견된 문제를 해결해 backend DB 로 반영하는 스크립트 모음입니다.

| 모듈 | 역할 |
|---|---|
| `compute_demand_class_mu_corrected.py` / `reflect_...` | 수요 성격 분류(`DORMANT`/`CENSORED`/`ACTIVE`) 및 결품기간 절단편향을 보정한 `mu_corrected` 산출·적재 |
| `compute_mu_forecast.py` / `apply_mu_forecast.py` | 직전 3개월 일수요율(roll3) 예측기 산출·적재 |
| `backtest_holdout.py` | 미검증 3개월(2025-10~12) 홀드아웃으로 수요율 예측기를 실측 대비 평가 |
| `apply_is_medical.py` | 비의료품(판촉·홍보·문구 등)을 예측·알림 대상에서 제외 — 메타코드 오염도에 따른 3단계 판정 |
| `apply_family_stock.py` | 동일 품목이 업체·규격 표기 차이로 물품코드가 분산돼 발생하는 **긴급부족 오탐**을 품목군 단위 재고 집계로 제거 |
| `apply_supply_risk_policy.py` | 확정된 공급위험 레벨 정책(`data/handoff/supply_risk_level_policy.json`) 반영 |

> 백테스트 결과 roll3(WAPE 42.6%)가 정적 `mu_corrected`(49.9%)보다 실측에 근접해, 소진 예측 수요율로 roll3 를 채택했습니다.

### AI Serving API

`src/serving/api.py` — FastAPI. `APIRouter(prefix="/api/v1/ai")` 로 아래 엔드포인트를 제공합니다.

```text
GET  /health
GET  /api/v1/ai/health
GET  /api/v1/ai/artifacts

POST /api/v1/ai/train
POST /api/v1/ai/forecasts/run
GET  /api/v1/ai/forecasts
GET  /api/v1/ai/forecasts/{institutionId}/{standardCode}
GET  /api/v1/ai/forecasts/eval

GET  /api/v1/ai/supply-risk
GET  /api/v1/ai/supply-risk/{itemGroupId}

GET  /api/v1/ai/inventory-policy
GET  /api/v1/ai/inventory-policy/{institutionId}/{standardCode}
GET  /api/v1/ai/order-recommendations
POST /api/v1/ai/recommend-order
```

레거시 호환용으로 `GET /predictions`, `POST /recommend-order` 도 유지합니다.

### 결과 확인 대시보드

`src/dashboard/app.py` — Streamlit MVP. 학습·예측 산출물을 눈으로 확인하는 용도입니다.

## 🛠 기술 스택

| 영역 | 사용 기술 |
|---|---|
| **Language** | Python |
| **Data** | pandas, numpy |
| **ML** | scikit-learn, LightGBM, XGBoost |
| **Serving** | FastAPI, uvicorn, Pydantic v2 |
| **Dashboard** | Streamlit |
| **HTTP** | httpx |

## 🏗 아키텍처

```
device/ · data/raw/            원본 사용량 데이터 (git 미추적)
        │
        ▼  src/data_loader.py → src/preprocessing.py
data/processed/usage_monthly.csv       월별 사용량 집계
        │
        ├── src/news/news_risk_scorer.py       → outputs/news_risk_scores.csv
        ├── src/commodity/commodity_risk_scorer.py → outputs/commodity_risk_scores.csv
        ▼
src/feature_engineering.py     → outputs/feature_table.csv
        ▼
src/modeling/training.py       → models/model_{a,b,c}*.pkl · models/manifest.json
        ▼
src/modeling/prediction.py     → outputs/predictions.csv
src/modeling/evaluation.py     → outputs/evaluation_report.csv
        ▼
src/serving/api.py (FastAPI)  ·  src/dashboard/app.py (Streamlit)
        ▼
src/loading/*                  → backend Neon Postgres 반영
```

모델 학습·예측·평가·재고 정책 코드는 `src/modeling/` 아래에서만 관리하고, `src/` 루트에는 데이터 파이프라인 공통 모듈과 앱 진입점만 둡니다.

## 🚀 시작하기

### 1. 설치

```bash
conda activate teamlex
pip install -r requirements.txt
```

### 2. 환경변수 (`.env.example`)

외부 뉴스·원자재 데이터 수집에 필요한 API 키입니다. `.env` 는 커밋하지 않습니다.

| 변수 | 설명 |
|---|---|
| `NEWS_API_KEY` | 뉴스 수집 API 키 |
| `GDELT_API_KEY` | GDELT API 키 |
| `EVENT_REGISTRY_API_KEY` | Event Registry API 키 |
| `ALPHA_VANTAGE_API_KEY` | 원자재 가격(Alpha Vantage) API 키 |

경로·하이퍼파라미터는 환경변수가 아니라 `src/config.py` 에서 관리합니다.

### 3. 배치 실행

```bash
python -m src.main          # 전체 배치
```

단계별 실행:

```bash
python -m src.preprocessing
python -m src.news.news_risk_scorer
python -m src.commodity.commodity_risk_scorer
python -m src.feature_engineering
python -m src.modeling.training
python -m src.modeling.prediction
python -m src.modeling.evaluation
```

### 4. Serving API · 대시보드

```bash
uvicorn src.serving.api:app --reload     # Swagger: http://127.0.0.1:8000/docs
streamlit run src/dashboard/app.py
```

### 5. 산출물

```text
data/processed/usage_monthly.csv
outputs/feature_table.csv
outputs/news_risk_scores.csv
outputs/commodity_risk_scores.csv
outputs/model_validation_report.csv
outputs/predictions.csv
outputs/evaluation_report.csv
models/model_a_usage_only.pkl
models/model_b_news.pkl
models/model_c_news_commodity.pkl
models/manifest.json
```

## 📁 구조

```
.
├── src/
│   ├── main.py                     # 전체 배치 파이프라인 진입점
│   ├── config.py                   # 경로 · 분할 기간 · 모델 변형 · 하이퍼파라미터
│   ├── data_loader.py              # 사용량 CSV 로드
│   ├── preprocessing.py            # 월별 사용량 집계
│   ├── feature_engineering.py      # 예측 feature table 생성
│   ├── features.py · utils.py
│   ├── modeling/                   # baseline · training · prediction · evaluation
│   │   ├── metrics.py              # MAE/RMSE/MAPE/SMAPE/WAPE
│   │   └── inventory_policy.py     # safety stock · recommended stock
│   ├── news/                       # 뉴스 수집·필터·LLM 분석·위험 점수
│   ├── commodity/                  # 원자재 수집·feature·위험 점수
│   ├── loading/                    # 운영 산출물 계산·backend DB 반영
│   ├── serving/                    # FastAPI 서빙 (api.py, schemas.py)
│   └── dashboard/                  # Streamlit MVP
├── data/
│   ├── mapping/                    # 품목-원자재 매핑 · 국가 가중치 seed CSV
│   └── handoff/                    # backend 로 넘기는 산출물 · 정책 JSON
├── docs/                           # AI_SERVICE_SCOPE · COLLABORATION · GITHUB_ISSUES
├── .github/                        # 이슈/PR 템플릿
├── .env.example
├── requirements.txt
└── CONTRIBUTING.md
```

### Data / Artifact 정책

아래 경로는 GitHub 에 올리지 않습니다. commit 가능한 것은 source code, docs, issue/PR 템플릿, sample mapping seed 입니다.

```text
device/
data/raw/
data/processed/
outputs/
models/
.env
```

## 👤 기여도 & 개발 환경

| 항목 | 내용 |
|---|---|
| **기여 비율** | **100%** (단독 개발) |
| **커밋** | 1 / 1 (본인 / 전체 사람 커밋) |
| **참여 인원** | 1명 |

<sub>기여 비율은 커밋 author 이메일 기준 집계이며 봇·자동화 커밋은 제외했습니다.</sub>
