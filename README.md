# WeP-Stock AI Service

WeP-Stock의 **AI 학습·예측·위험 점수·재고 권고 서빙 전용** 저장소입니다.

전체 제품 백엔드 기능은 별도 서비스에서 담당하고, 이 저장소는 아래 책임만 가집니다.

```text
raw_stock 일별 재고·출고 데이터 전처리
→ 수요 예측 feature 생성
→ baseline / ML 모델 학습
→ 뉴스·원자재 위험 점수 생성
→ 예측 결과 및 재고 권고 산출
→ AI serving API 제공
```

## Out Of Scope

다음 기능은 이 저장소에서 제거했습니다. 백엔드/API 서버 repo에서 별도로 구현해야 합니다.

```text
인증 / 사용자 / 권한
파일 업로드 및 import_batch 관리
물품 표준화 검수 UI/API
기관/중앙 운영 대시보드 API
알림 상태 관리
재배치 승인 워크플로우
DB 트랜잭션/감사 로그
```

## Branch Policy

```text
main: 완성본만 병합
dev: 개발 통합 브랜치
feature/*: 기능 작업 브랜치, PR 대상은 dev
```

`main`에는 직접 push하지 않습니다.

## AI Modules

```text
src/data_loader.py              raw_stock DAT 스트리밍 로드
src/preprocessing.py            기관·부서·물품별 월간 재고/소비 집계
src/feature_engineering.py      예측 feature table 생성
src/modeling/baseline.py        baseline 예측
src/modeling/training.py        Model A/B/C 학습
src/modeling/prediction.py      예측 결과 생성
src/modeling/evaluation.py      평가 리포트 생성
src/modeling/metrics.py         MAE/RMSE/MAPE/SMAPE/WAPE 평가 지표
src/modeling/inventory_policy.py safety stock / recommended stock 정책
src/news/                       sample 뉴스 위험 점수 및 세부 weight scoring
src/commodity/                  sample 원자재 위험 점수
src/serving/                    AI serving API
src/dashboard/                  AI 결과 확인용 Streamlit MVP
```

## Input Data

유일한 학습 입력은 `raw_stock/*.DAT`입니다. `device/` 데이터와 `MED_DEVICE_5`, `SIDO` 코드는 사용하지 않습니다.

```text
일별 원본 단위: 재고마감일 x 보건기관코드_en x 부서코드 x 물품코드
월별 학습 단위: year_month x institution_code x department x item_code
수요 기준값: 정상출고량
현재고 기준값: 해당 월 마지막 마감재고량
```

물품별 뉴스·원자재 위험은 검수된 `data/mapping/stock_item_material_mapping.csv`만 사용합니다. 매핑이 없으면 임의 원자재를 배정하지 않고 위험 점수를 0으로 둡니다.

모델 학습, 예측, 평가, 재고 정책 관련 코드는 `src/modeling/` 아래에서만 관리합니다. `src/` 루트에는 데이터 파이프라인 공통 모듈과 앱 진입점만 둡니다.

## AI Serving API

FastAPI app:

```bash
uvicorn src.serving.api:app --reload
```

Swagger:

```text
http://127.0.0.1:8000/docs
```

Endpoints:

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

Legacy compatibility:

```text
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
python -m src.modeling.training
python -m src.modeling.prediction
python -m src.modeling.evaluation
```

## News Risk Weighting

뉴스 리스크는 `data/mapping/news_risk_weights.yaml`의 문헌 기반 초기 weight를 사용합니다.

기사 단위 점수:

```text
article_score
= event_type_weight
* severity
* confidence
* source_weight
* item_relevance
* exposure_weight
* recency_weight
* novelty_weight
```

월별·품목별 점수:

```text
monthly_item_news_risk = 1 - exp(-sum(article_score))
```

기존 모델 feature와의 호환성을 위해 최종 출력 컬럼은 유지합니다.

```text
disease_news_risk
supply_news_risk
material_news_risk
total_news_risk
```

## Dashboard

```bash
streamlit run src/dashboard/app.py
```

## Generated Outputs

```text
data/processed/stock_monthly.csv
data/processed/stock_model_dataset.csv
outputs/stock_feature_table.csv
outputs/stock_news_risk_scores.csv
outputs/stock_news_article_scores.csv
outputs/stock_commodity_risk_scores.csv
outputs/stock_model_validation_report.csv
outputs/stock_predictions.csv
outputs/stock_evaluation_report.csv
models/stock_model_a_usage_only.pkl
models/stock_model_b_news.pkl
models/stock_model_c_news_commodity.pkl
models/stock_manifest.json
```

## Data/Artifact Policy

아래 파일은 GitHub에 올리지 않습니다.

```text
raw_stock/
data/raw/
data/processed/
outputs/
models/
.env
```

commit 가능한 것은 source code, docs, issue/PR template, sample mapping seed입니다.
