# WeP-Stock AI Service

WeP-Stock의 **AI 학습·예측·위험 점수·재고 권고 서빙 전용** 저장소입니다.

현재 전체 구조와 진행 현황은
`docs/2026-07-20_01_SYSTEM_STRUCTURE_AND_PROGRESS.md`를 참고합니다.

처음 참여하는 팀원이 전체 배경, 용어, 데이터, 모델, Module C, GitHub 상태와 다음 작업을
한 번에 이해하려면 `docs/2026-07-22_06_FULL_PROJECT_HANDOFF_GUIDE.md`부터 확인합니다.

최신 전체 재분류 결과, 외부 군집 비교, 분류·Module C 가중치 대안은
`docs/2026-07-22_07_ITEM_RECLASSIFICATION_ACCURACY_AND_WEIGHT_ALTERNATIVES.md`를 참고합니다.

현재 실제 적용 중인 뉴스·시장가격·수출입·Module C·재고정책 가중치와 실행 결과는
`docs/2026-07-23_04_CURRENT_WEIGHT_ASSIGNMENT.md`에서 확인합니다.

현재 사용량 예측 정확도, 세부유형 커버리지, Module C 비교와 운영 가능 범위는
`docs/2026-07-23_05_CURRENT_FORECAST_MODEL_EVALUATION.md`에서 확인합니다.

20개국 관세청 캐시, 무역위험 세부 가중치 보정, 홀드아웃 성능과 최종 재고 영향은
`docs/2026-07-29_03_20_COUNTRY_TRADE_WEIGHT_RECALIBRATION.md`에서 확인합니다.

최신 데이터를 검증·테스트로 분리한 예측 혼합 가중치, v1.4 무역 가중치와 현재
재고 출력은 `docs/2026-07-29_04_TEMPORAL_TRAIN_VALIDATION_TEST_WEIGHT_TUNING.md`에서
확인합니다.

PDF 3종과 현재 코드의 차이, 이번 보완 내용, 실제 재실행 결과와 남은 제약은
`docs/2026-07-24_01_PDF_REQUIREMENT_GAP_AND_IMPLEMENTATION.md`에서 확인합니다.

전체 품목·원자재 후보의 사용자 일괄 승인 정책, 운영 게이트, 재실행 결과는
`docs/2026-07-28_01_ALL_ITEM_BULK_APPROVAL_RESULT.md`에서 확인합니다.

2026-07-23 중간점검 발표 자료는 다음 순서로 확인합니다.

1. `docs/2026-07-22_01_MIDTERM_SIMPLE_INVENTORY_MODEL.md`
2. `docs/2026-07-22_02_MIDTERM_WEIGHT_SELECTION_MODEL.md`
3. `docs/2026-07-22_03_MIDTERM_FINAL_INVENTORY_SYSTEM.md`
4. `docs/2026-07-22_04_MIDTERM_DECISION_PROCESS.md`

전체 제품 백엔드 기능은 별도 서비스에서 담당하고, 이 저장소는 아래 책임만 가집니다.

```text
raw_stock 일별 재고·출고 데이터 전처리
→ 수요 예측 feature 생성
→ baseline / ML 모델 학습
→ 뉴스·원자재 위험 점수와 모듈 C 승인 경로 생성
→ 예측 결과 및 재고 권고 산출
→ 품질 게이트를 통과한 AI 배치 결과 DML 적재
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
운영 입출고·발주 트랜잭션/감사 로그
DB 스키마 변경(DDL)
```

DB 책임은 `스키마(DDL)=backend`, `검증된 AI 배치 산출물(DML)=AI`로 나눈다. 현재
`demand_class`와 `mu_corrected` 적재는 `src/loading/`이 담당하며, 명시적인 기관코드
매핑과 품질 리포트가 없으면 실행을 중단한다.

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
src/item_classification_evaluation.py 승인 회귀·외부 군집·가중치 민감도 평가
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
src/modeling/inventory_status.py 재고 0 원인·승인 대체품 재고·긴급부족 분류
src/news/                       CSV/GDELT 뉴스 수집 및 세부 weight scoring
src/commodity/                  CSV/API 가격 수집 및 원자재 위험 점수
src/module_c/                   외부 위험 결합·승인 게이트·감사·알림
src/module_c/supply_risk_policy.py 공급 메타코드 기준레벨 재도출·SS/ROP 단위 계약
src/module_c/supply_risk_anomaly_filter.py 공급위험 오류 PASS/REVIEW/BLOCK 품질 게이트
src/loading/                    검증된 AI 배치 산출물의 보호된 DB 적재
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

`자동폐기출고량`은 수요와 재고 수지에서 제외하고 별도 정보열로만 보존합니다.
2018~2019 데이터는 현재 표준품목과 strict/core 규칙으로 매칭된 행만 학습 후보가 됩니다.
매칭되지 않은 과거 전용 명칭은 제외하며, 2019→2024 공백은 별도 시계열 구간으로 끊어
lag가 이어지지 않게 합니다. 과거자료 학습 가중치는 2025년 검증 WAPE로 선택하며 현재
정책값은 `data/mapping/historical_training_policy.json`의 `1.0`입니다.

일평균 수요와 표준편차는 실제 0을 보존하며 `mu_is_floored`,
`sigma_is_floored`로 정책 하한 적용 여부를 별도 기록합니다. 현재 하한은 모두 0입니다.
리드타임은 품목 P25를 사용하고 미매칭·0일은 15일로 대체하며, 원천값은 120일에서
상한 처리한 뒤 공급위험 배수를 적용합니다.

물품별 뉴스·원자재 위험은 검수된 `data/mapping/stock_item_material_mapping.csv`만 사용합니다. 매핑이 없으면 임의 원자재를 배정하지 않고 위험 점수를 0으로 둡니다.

재고량 출력은 사용량 예측과 분리됩니다. `predicted_usage`는 다음 달 예상 사용량,
`base_stock`은 검토주기·리드타임 수요와 안전재고의 합, `target_stock`은 승인 매핑 기반
위험 버퍼까지 반영한 목표 재고량입니다. 계산식과 API 입력은
`docs/2026-07-18_03_INVENTORY_QUANTITY_MODEL.md`, 외부 신호와 승인 경로는
`docs/2026-07-18_05_MODULE_C_RISK_ADJUSTMENT.md`를 참고합니다.

원자재 후보 생성 규칙은 기존 `94b5663` 통합본과 canonical 저장소
`wep-stock-item-material-pipeline@74d3982`를 전체 데이터로 비교해 v2.1로 선별 통합했습니다.
생성 결과는 모두 승인 전 후보입니다. 비교·실행 결과는
`docs/2026-07-18_02_ITEM_INTEGRATED_PIPELINE_V2_1_RESULT.md`를 참고합니다.

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
python -m src.trade.hsk_reference
python -m src.trade.trade_risk_scorer
python -m src.module_c.pipeline
python -m src.feature_engineering
python -m src.modeling.historical_weight_tuning --apply
python -m src.modeling.training
python -m src.modeling.prediction
python -m src.modeling.temporal_ensemble_tuning --apply
# 적용된 혼합 가중치로 현재 예측 재생성
python -m src.modeling.prediction
python -m src.trade.trade_inventory_impact
python -m src.modeling.classified_prediction
python -m src.modeling.evaluation

# 8~9월 보정, 10~12월 홀드아웃으로 예측×재고정책 조합 비교
python -m src.modeling.combination_experiment
```

원자재 매핑 승인과 최종 재고 영향 실험은
[`docs/2026-07-23_01_MATERIAL_MAPPING_APPROVAL_FINAL_INVENTORY_REPORT.md`](docs/2026-07-23_01_MATERIAL_MAPPING_APPROVAL_FINAL_INVENTORY_REPORT.md)의
승인 게이트와 재실행 순서를 따른다. `material_approval --apply`는 실험 정책과 감사표를
검토한 뒤에만 명시적으로 실행한다.

수요 절단편향 계산과 DB 적재 사전검사:

```bash
python -m src.loading.compute_demand_class_mu_corrected

# 전처리 후 재고 0 원인·긴급부족 분류
python -m src.modeling.inventory_status

# 기본은 dry-run이며, 명시적 기관 매핑과 DATABASE_URL이 필요하다.
python -m src.loading.reflect_demand_class_mu_corrected
```

`--apply`는 품질 리포트가 통과하고 `data/mapping/institution_id_mapping.csv`의
익명기관-DB기관 대응이 검증된 뒤에만 사용한다. 정렬 순서로 두 기관 목록을 `zip()`하는
방식은 기본 차단한다.

품목 분류 전체 재실행:

```bash
python -m src.item_normalization --full
python -m src.item_enrichment build-worklist
python -m src.item_enrichment match
python -m src.item_classification fetch-official-web --delay 0.15
python -m src.item_integrated_pipeline --with-excel --sample-size 1000
python -m src.item_classification_evaluation --baseline-approvals /path/to/frozen/approvals.csv
python -m src.item_review_export
python -m src.modeling.classified_prediction
```

전체 후보를 사용자 승인 상태로 전환하고 활성화:

```bash
python -m src.item_bulk_approval --apply --sample-size 1000
```

이 명령의 `approved`는 후보 수용을 뜻하며 외부 사실 검증 완료를 뜻하지 않습니다.
필드가 불완전한 분류는 예측에서 제외되고, 원자재 후보는 근거·시장가격 경로·공급정책
게이트를 통과한 행만 위험 계산에 사용됩니다. 기존 근거 승인 외 항목은 자동발주에
사용하지 않습니다.

분류 상태, 승인 수, 외부 근거와 검토 큐는
`docs/2026-07-16_04_ITEM_CLASSIFICATION_V1_RESULT.md`를 참고합니다. 현재 작업 인수인계와 우선 검토용
1,000건 설명은 `docs/2026-07-16_03_ITEM_CLASSIFICATION_HANDOFF.md`에 정리되어 있습니다.

## Usage Forecast Validation

사용량 예측의 물리 시계열은 `기관 + 부서 + 내부 물품코드`를 유지합니다. 여기에 검증된
표준품목 정의, 품목군, family, subtype, 규격, 단위를 모델 특성으로 추가해 과거자료와
현재자료가 의미 계층을 공유하게 합니다. 상세 표준키나 내부 품목코드 자체는 고유값이
너무 많아 모델 범주 특성에서 제외하고, 원본 식별·출력·원자재 연결에는 그대로 보존합니다.

기준선 6종과 LightGBM L1/Tweedie를 확장형 시계열 교차검증으로 비교하며, 검증 WAPE가
가장 낮은 방법을 운영 기본값으로 선택합니다. 뉴스·원자재 값이 모두 0이면 관련 모델은
학습하지 않고 보고서에 제외 사유를 기록합니다.

현재 데이터 평가 결과와 제한사항은
`docs/2026-07-15_01_USAGE_FORECAST_MODEL_EVALUATION.md`를 참고합니다.

승인된 품목 분류가 들어오면 재학습 없이 로컬 예측을
`품목 + 세부 유형 품목 + 규격 + 단위`로 집계합니다. 분류와 taxonomy가 모두
`review_status=approved`여야 하며 서로 다른 단위는 자동 환산하거나 합산하지 않습니다.
입력 계약과 실행 방법은 `docs/2026-07-16_02_CLASSIFIED_FORECAST_INTEGRATION.md`를 참고합니다.

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

운영 기본값은 뉴스·가격·수출입 모두 `disabled`입니다. `.env`에서 실제 공급자를 명시해야 하며
`sample`은 합성 smoke test에만 사용합니다.

```text
NEWS_PROVIDER=csv | gdelt | gdelt_ngram
COMMODITY_PROVIDER=csv | alpha_vantage | fred | nasdaq_data_link
COMMODITY_ALLOW_SAMPLE_FALLBACK=false
TRADE_PROVIDER=csv | kcs
TRADE_COUNTRY_CODES=
```

직접 나프타/선물 시계열은 계약 CSV 또는 접근 권한이 있는 데이터셋을 우선 사용하고,
Brent 같은 대리변수는 `proxy_quality`와 전파 가중치를 낮춰 별도 감사합니다.
`TRADE_PROVIDER=kcs`는 공공데이터포털에서 관세청 품목별 및 품목별 국가별 수출입
서비스 활용승인이 끝난 뒤에만 사용합니다. 국가코드를 비워두면 승인된
`data/mapping/trade_country_scope.csv`를 사용합니다.

20개국 캐시가 완결된 뒤 무역 계수를 재보정할 때만 다음 명령을 실행합니다.
미완료 국가-HSK가 있으면 적용 전에 중단됩니다.

```bash
python -m src.trade.trade_weight_calibration --apply
```

## Dashboard

```bash
streamlit run src/dashboard/app.py
```

## Generated Outputs

```text
data/processed/stock_monthly.parquet
data/processed/stock_model_dataset.parquet
data/processed/item_material_pipeline/material_pipeline_run_report.json
data/processed/censored_demand.parquet
outputs/stock_feature_table.parquet
outputs/stock_news_risk_scores.csv
outputs/stock_news_article_scores.csv
outputs/stock_commodity_risk_scores.csv
outputs/stock_commodity_risk_audit.csv
outputs/hs_trade_risk_features.csv
outputs/stock_trade_risk_scores.csv
outputs/stock_trade_risk_audit.csv
outputs/stock_trade_risk_report.json
outputs/trade_inventory_impact_report.json
outputs/kcs_trade_country_cache_summary.csv
outputs/trade_weight_calibration_report.json
outputs/trade_weight_calibration_policy.csv
outputs/trade_weight_calibration_observations.csv
outputs/forecast_ensemble_temporal_report.json
outputs/forecast_ensemble_validation_candidates.csv
outputs/forecast_ensemble_test_by_segment.csv
data/sample/hs_trade_risk_features_sample_1000.csv
data/sample/trade_inventory_impact_sample_1000.csv
data/sample/trade_weight_calibration_sample_1000.csv
data/sample/forecast_ensemble_test_sample_1000.csv
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
outputs/demand_class_mu_corrected_handoff.csv
outputs/demand_class_mu_corrected_report.json
data/sample/demand_class_mu_corrected_sample_1000.csv
models/stock_model_a_usage_only.pkl
models/stock_model_a_usage_tweedie.pkl
models/stock_manifest.json
```

현재 GitHub 이슈·PR과 로컬 구현의 대조 결과는
`docs/2026-07-22_05_GITHUB_ISSUE_PR_ALIGNMENT.md`를 참고한다.

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
