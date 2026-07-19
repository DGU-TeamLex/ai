# WeP-Stock AI Service

WeP-Stock의 **AI 학습·예측·위험 점수·재고 권고 서빙 전용** 저장소입니다.

전체 제품 백엔드 기능은 별도 서비스에서 담당하고, 이 저장소는 아래 책임만 가집니다.

```text
raw_stock 일별 재고·출고 데이터 전처리
→ 수요 예측 feature 생성
→ baseline / ML 모델 학습
→ 뉴스·원자재 위험 점수와 모듈 C 승인 경로 생성
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
src/item_normalization.py       품목명·품목군·세부유형·규격 후보 생성
src/item_enrichment.py          기관별 별칭을 대표 품목으로 집계하고 공식 마스터 매칭
src/item_classification.py      외부 근거 게이트 기반 승인 분류와 검토 큐 생성
src/item_integrated_pipeline.py 품목·속성·원자재·상위개념 통합 및 전체/샘플 생성
src/preprocessing.py            기관·부서·물품별 월간 재고/소비 집계
src/feature_engineering.py      예측 feature table 생성
src/material_pipeline.py       원자재·공급위험·수요트리거 후보 생성
src/modeling/baseline.py        baseline 예측
src/modeling/training.py        Model A/B/C와 모듈 C challenger 학습
src/modeling/prediction.py      예측 결과 생성
src/modeling/classified_prediction.py 승인 분류별 예측 집계
src/modeling/evaluation.py      평가 리포트 생성
src/modeling/metrics.py         MAE/RMSE/MAPE/SMAPE/WAPE 평가 지표
src/modeling/inventory_policy.py 기본재고 / 위험조정 목표재고 / 발주량 정책
src/news/                       CSV/GDELT 뉴스 수집 및 세부 weight scoring
src/commodity/                  CSV/API 가격 수집 및 원자재 위험 점수
src/module_c/                   외부 위험 결합·승인 게이트·감사·알림
src/module_c/supply_risk_policy.py 공급 메타코드 기준레벨 재도출·SS/ROP 단위 계약
src/module_c/supply_risk_anomaly_filter.py 공급위험 오류 PASS/REVIEW/BLOCK 품질 게이트
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

재고량 출력은 사용량 예측과 분리됩니다. `predicted_usage`는 다음 달 예상 사용량,
`base_stock`은 검토주기·리드타임 수요와 안전재고의 합, `target_stock`은 승인 매핑 기반
위험 버퍼까지 반영한 목표 재고량입니다. 계산식과 API 입력은
`docs/INVENTORY_QUANTITY_MODEL.md`, 외부 신호와 승인 경로는
`docs/MODULE_C_RISK_ADJUSTMENT.md`를 참고합니다.

원자재 후보 생성 규칙은 기존 `94b5663` 통합본과 canonical 저장소
`wep-stock-item-material-pipeline@74d3982`를 전체 데이터로 비교해 v2.1로 선별 통합했습니다.
생성 결과는 모두 승인 전 후보입니다. 비교·실행 결과는
`docs/ITEM_INTEGRATED_PIPELINE_V2_1_RESULT.md`를 참고합니다.

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
GET  /api/v1/ai/predictions/by-subtype
POST /api/v1/ai/recommend-order
```

Legacy compatibility:

```text
GET  /predictions
GET  /predictions/by-subtype
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
python -m src.item_integrated_pipeline --with-excel --sample-size 1000
python -m src.news.news_risk_scorer
python -m src.commodity.commodity_risk_scorer
python -m src.module_c.pipeline
python -m src.feature_engineering
python -m src.modeling.training
python -m src.modeling.prediction
python -m src.modeling.classified_prediction
python -m src.modeling.evaluation
```

품목 분류 전체 재실행:

```bash
python -m src.item_normalization --full
python -m src.item_enrichment build-worklist
python -m src.item_enrichment match
python -m src.item_classification fetch-official-web --delay 0.15
python -m src.item_integrated_pipeline --with-excel --sample-size 1000
python -m src.item_review_export
python -m src.modeling.classified_prediction
```

분류 상태, 승인 수, 외부 근거와 검토 큐는
`docs/ITEM_CLASSIFICATION_V1_RESULT.md`를 참고합니다. 현재 작업 인수인계와 우선 검토용
1,000건 설명은 `docs/ITEM_CLASSIFICATION_HANDOFF_2026-07-16.md`에 정리되어 있습니다.

## Usage Forecast Validation

사용량 예측은 표준 품목명이 아니라 `기관 + 부서 + 내부 물품코드` 시계열을 사용합니다.
품목 표준화가 미완료여도 로컬 예측은 가능하지만 기관 간 통합 및 뉴스·원자재 연결에는
승인된 표준 ID가 필요합니다.

기준선 6종과 LightGBM L1/Tweedie를 확장형 시계열 교차검증으로 비교하며, 검증 WAPE가
가장 낮은 방법을 운영 기본값으로 선택합니다. 뉴스·원자재 값이 모두 0이면 관련 모델은
학습하지 않고 보고서에 제외 사유를 기록합니다.

현재 데이터 평가 결과와 제한사항은
`docs/USAGE_FORECAST_MODEL_EVALUATION.md`를 참고합니다.

승인된 품목 분류가 들어오면 재학습 없이 로컬 예측을
`품목 + 세부 유형 품목 + 규격 + 단위`로 집계합니다. 분류와 taxonomy가 모두
`review_status=approved`여야 하며 서로 다른 단위는 자동 환산하거나 합산하지 않습니다.
입력 계약과 실행 방법은 `docs/CLASSIFIED_FORECAST_INTEGRATION.md`를 참고합니다.

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

## Module C Providers

운영 기본값은 뉴스·가격 모두 `disabled`입니다. `.env`에서 실제 공급자를 명시해야 하며
`sample`은 합성 smoke test에만 사용합니다.

```text
NEWS_PROVIDER=csv | gdelt
COMMODITY_PROVIDER=csv | alpha_vantage | fred | nasdaq_data_link
COMMODITY_ALLOW_SAMPLE_FALLBACK=false
```

직접 나프타/선물 시계열은 계약 CSV 또는 접근 권한이 있는 데이터셋을 우선 사용하고,
Brent 같은 대리변수는 `proxy_quality`와 전파 가중치를 낮춰 별도 감사합니다.

## Dashboard

```bash
streamlit run src/dashboard/app.py
```

## Generated Outputs

```text
data/processed/stock_monthly.parquet
data/processed/stock_model_dataset.parquet
data/processed/item_material_pipeline/material_pipeline_run_report.json
outputs/stock_feature_table.parquet
outputs/stock_news_risk_scores.csv
outputs/stock_news_article_scores.csv
outputs/stock_commodity_risk_scores.csv
outputs/stock_commodity_risk_audit.csv
outputs/stock_module_c_risk_scores.csv
outputs/stock_module_c_risk_audit.csv
outputs/stock_module_c_alerts.csv
outputs/module_c_material_exposure_candidates.csv
outputs/module_c_run_report.json
outputs/module_c_supply_risk_level_audit.csv
outputs/module_c_supply_risk_quality_classified.csv
outputs/module_c_supply_risk_quality_issues.csv
outputs/module_c_supply_risk_quality_passed.csv
outputs/module_c_supply_risk_quality_review.csv
outputs/module_c_supply_risk_quality_quarantine.csv
outputs/module_c_supply_risk_quality_report.json
data/sample/module_c_supply_risk_quality_sample_1000.csv
outputs/stock_forecast_data_quality.json
outputs/stock_model_cv_report.csv
outputs/stock_model_validation_report.csv
outputs/stock_backtest_predictions.csv
outputs/stock_predictions.csv
outputs/stock_predictions_by_subtype.csv
outputs/stock_predictions_by_subtype_quality.json
outputs/stock_evaluation_report.csv
outputs/stock_evaluation_by_segment.csv
models/stock_model_a_usage_only.pkl
models/stock_model_a_usage_tweedie.pkl
models/stock_manifest.json
```

`stock_model_b_news.pkl`, `stock_model_c_news_commodity.pkl`,
`stock_model_d_module_c.pkl`은 각각 필요한 검증 위험 feature가 학습기간에 실제로 존재할
때만 생성됩니다.

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
