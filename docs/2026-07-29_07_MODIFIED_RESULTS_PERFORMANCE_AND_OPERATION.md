# 수정 결과, 성능 향상 및 운영 가이드

## 1. 문서 목적

이 문서는 현재 AI 저장소에 반영된 누적 수정 결과와 성능 변화, 실제 실행 순서를
한 번에 설명한다. 구현 세부 근거는 다음 문서를 함께 사용한다.

- `2026-07-29_03_20_COUNTRY_TRADE_WEIGHT_RECALIBRATION.md`
- `2026-07-29_04_TEMPORAL_TRAIN_VALIDATION_TEST_WEIGHT_TUNING.md`
- `2026-07-29_05_GITHUB_ISSUE_REMEDIATION.md`
- `2026-07-29_06_STANDARDIZED_HISTORY_LIGHTGBM_UPDATE.md`

## 2. 핵심 수정 결과

### 2.1 재고 원천 및 수요 정의

- 모델 원천을 `raw_stock/*.DAT`로 고정했다.
- 현재자료 `익스포트_*.DAT`와 2018·2019년 자료를 별도 전처리한다.
- 수요를 `정상출고량`으로 정의했다.
- 자동폐기, 반품, 이동출고를 수요에 더하지 않는다.
- 음수 정상출고는 임의로 0으로 바꾸지 않고 학습 라벨에서 제외한다.
- `device/` 데이터는 사용하지 않는다.

### 2.2 표준품목과 과거자료 연결

- 물리 재고 시계열은 `기관 + 부서 + 내부 물품코드`를 유지한다.
- 상세 표준키와 의미 정의키를 분리했다.
- 모델은 품목군, family, subtype, 규격, 단위 의미계층을 사용한다.
- 현재 표준품목과 strict/core 규칙으로 매칭된 과거 품목만 학습을 허용한다.
- 2019→2024 공백에서 lag 구간을 끊어 과거 마지막 값이 2024년 lag가 되지 않는다.

| 항목 | 결과 |
|---|---:|
| 전체 로컬 품목 매핑 | 585,279 |
| 현재 로컬 품목 | 409,519 |
| 과거 로컬 품목 | 175,760 |
| 과거 학습 허용 품목 | 146,757 |
| 과거 전용 명칭 제외 | 29,003 |
| 과거 학습행 | 952,002 |
| 통합 월별 특성행 | 5,190,767 |

### 2.3 수출입 및 Module C

- 관세청 국가별 수출입 자료를 승인 20개국, HSK 32개 조합으로 확장했다.
- 국가-HSK 체크포인트 `640/640`, 총계 HSK `32/32`를 수집했다.
- 수입량 감소, 수입 중단, 단가, 변동성, 공급국 집중도 등 10개 무역 변수를
  시간분할로 재보정했다.
- 무역, 뉴스, 시장가격, 질병수요 위험을 Module C에서 결합한다.
- 승인·품질 게이트를 통과하지 않은 원자재 연결은 위험 가산에 사용하지 않는다.

### 2.4 재고 판정 보호장치

- 실제 0 수요를 양수 바닥값으로 바꾸지 않는다.
- `DORMANT`, `NOT_OPERATED`, `DATA_MISSING`을 분리한다.
- `DORMANT`와 `NOT_OPERATED`는 자동발주를 0으로 억제한다.
- `DATA_MISSING`과 오래된 데이터는 권고량을 `null`로 처리한다.
- 리드타임 원시값, fallback, cap, 공급위험 배수 적용값을 감사열로 남긴다.

### 2.5 학습 및 예측 안정화

- LightGBM 수치형 특성을 `float32`로 축소했다.
- 고유값이 많은 `item_code`, `standard_item_key`는 직접 모델 특성에서 제외했다.
- 범주형 전체 문자열 복사를 없애고 코드 맵으로 변환한다.
- LightGBM column-wise 실행과 256MB 히스토그램 캐시 제한을 적용했다.
- 검증과 L1, Tweedie, Module C 최종 재적합을 별도 프로세스로 실행한다.
- 최종 예측 CSV에 표준품목 키, 품목군, 세부유형, 규격, 단위를 포함한다.

## 3. 성능 향상

### 3.1 과거자료 학습 효과

2025년 1~6월 검증에서 과거자료 weight만 바꿔 비교했다.

| 방식 | WAPE | RMSE | 편향률 |
|---|---:|---:|---:|
| 현재자료 전용, weight 0 | 40.0699% | 298.513 | -7.672% |
| 표준매칭 과거자료, weight 1 | **39.2593%** | **269.703** | -5.907% |
| 변화 | **-0.8106%p** | **-28.809** | +1.765%p |

이 결과를 근거로 `historical_training_policy.json`의 과거 weight를 `1.0`으로
적용했다. 최신 테스트는 이 선택에 사용하지 않았다.

### 3.2 모델 교차검증

| 모델 | 검증 WAPE | 역할 |
|---|---:|---|
| LightGBM L1 | **39.1137%** | 단일 주 모델 |
| LightGBM Tweedie | 39.6520% | 앙상블 후보 |
| Module C Tweedie | 39.6969% | 외부위험 앙상블 후보 |

뉴스 B/C 모델은 2024년 학습구간 뉴스 특성이 모두 0이므로 품질 가드가 학습을
차단했다. 0인 외부 신호를 사용한 모델을 성능 개선으로 보고하지 않는다.

### 3.3 최종 앙상블

2025년 8~9월에서 5% 간격 231개 전역 조합과 수요패턴별 조합을 비교했다.

| 모델 출력 | 전역 fallback weight |
|---|---:|
| L1 | 0.25 |
| Tweedie | 0.15 |
| Module C | 0.60 |

수요패턴별 라우터가 검증 WAPE `36.5004%`로 가장 낮아 최종 정책으로 적용됐다.

동일한 최신 2025년 10~12월 테스트:

| 지표 | 기존 최종 시스템 | 수정 후 | 변화 |
|---|---:|---:|---:|
| WAPE | 37.5788% | **37.1840%** | -0.3948%p |
| RMSE | 201.918 | **200.653** | -1.265 |
| 편향률 | -6.398% | **-3.467%** | 과소예측 2.931%p 완화 |

2025년 8~12월 전체 backtest WAPE는 `36.9198%`, RMSE는 `196.912`다.

WAPE와 편향은 개선됐지만 MAPE, SMAPE, RMSLE가 항상 같이 좋아지는 것은 아니다.
0 수요와 저수요가 많은 데이터이므로 “모든 품목에서 정확도가 상승했다”로 해석하면
안 된다.

### 3.4 무역위험 성능

최신 3개월 무역 테스트에서 기존 v1.3과 v1.4를 비교했다.

| 지표 | v1.3 | v1.4 |
|---|---:|---:|
| 품절 대리목표 가중 MAE | 0.025726 | **0.025492** |
| Spearman | 0.189130 | **0.195333** |

MAE 개선은 0.91%로 작다. 이는 HSK 단위 공급위험 순위화 성능이며 인과효과나
품목별 실제 부족확률을 뜻하지 않는다.

## 4. 실행 전 준비

```bash
cd /path/to/ai
conda activate teamlex
pip install -r requirements.txt
```

외부 API를 갱신할 때는 `.env`에 발급받은 키를 등록한다. 키 값은 저장소에
커밋하지 않는다. 기존 캐시만 사용할 때는 외부 API 재수집이 필요하지 않다.

다음 파일이 있어야 현재 정책을 재현할 수 있다.

- `data/mapping/historical_training_policy.json`
- `data/mapping/forecast_ensemble_policy.json`
- `data/mapping/module_c_risk_weights.json`
- 승인된 품목·원자재·HSK 매핑 파일

## 5. 실행 방법

### 5.1 저장된 정책으로 정기 배치

원천 재고와 외부 신호를 다시 처리하고 현재 저장된 weight를 적용한다.

```bash
python -m src.main
```

`src.main`은 전처리, 재고상태, 뉴스, 시장가격, 수출입, Module C, 특성 생성,
모델 학습, 예측을 순서대로 실행한다. 과거 weight와 앙상블 정책은 저장된 정책을
사용하며 매 실행마다 자동 재선정하지 않는다.

### 5.2 표준화와 가중치까지 전체 재보정

새 연도 자료가 추가됐거나 표준품목 매핑이 크게 바뀐 경우 사용한다.

```bash
python -m src.preprocessing
python -m src.modeling.inventory_status

python -m src.item_integrated_pipeline --with-excel --sample-size 1000
python -m src.news.news_risk_scorer
python -m src.commodity.commodity_risk_scorer
python -m src.trade.hsk_reference
python -m src.trade.trade_risk_scorer
python -m src.module_c.pipeline

python -m src.feature_engineering
python -m src.modeling.historical_weight_tuning --apply
python -m src.modeling.training

# 원시 모델별 backtest와 현재 예측 생성
python -m src.modeling.prediction

# 검증구간으로 앙상블 weight 선택, 최신 테스트는 평가에만 사용
python -m src.modeling.temporal_ensemble_tuning --apply

# 적용된 정책으로 최종 예측 재생성
python -m src.modeling.prediction
```

`python -m src.modeling.training`은 검증과 세 모델 재적합을 독립 프로세스로
자동 분리한다. 이전 OOM을 피하기 위해 한 프로세스에서 세 모델을 연속으로 직접
학습하지 않는다.

### 5.3 무역 weight 재보정

20개국 캐시, HSK 범위 또는 관측기간이 충분히 바뀐 경우에만 실행한다.

```bash
python -m src.trade.trade_weight_calibration --apply
python -m src.trade.trade_risk_scorer --provider csv
python -m src.module_c.pipeline
python -m src.feature_engineering
python -m src.modeling.training
python -m src.modeling.prediction
```

무역 weight 보정은 일일 배치가 아니다. 테스트 성능을 확인한 뒤 같은 테스트에
맞춰 반복 수정하지 않는다.

### 5.4 API 실행

```bash
uvicorn src.serving.api:app --reload
```

확인 주소:

```text
http://127.0.0.1:8000/docs
```

주요 결과 확인:

```text
GET /api/v1/ai/artifacts
GET /api/v1/ai/forecasts
GET /api/v1/ai/forecasts/eval
GET /api/v1/ai/inventory-policy
GET /api/v1/ai/order-recommendations
GET /api/v1/ai/predictions/by-subtype
```

## 6. 실행 후 확인할 산출물

| 산출물 | 위치 |
|---|---|
| 표준품목 전체 매핑 | `data/processed/stock_standard_item_mapping.parquet` |
| 표준품목 1,000건 샘플 | `data/sample/stock_standard_item_mapping_sample_1000.csv` |
| 데이터 품질 | `outputs/stock_forecast_data_quality.json` |
| 과거 weight 비교 | `outputs/historical_training_weight_report.json` |
| 모델 비교 | `outputs/stock_model_validation_report.csv` |
| 앙상블 비교 | `outputs/forecast_ensemble_temporal_report.json` |
| 최종 미래 예측 | `outputs/stock_predictions.csv` |
| 전체 backtest | `outputs/stock_backtest_predictions.csv` |
| 모델 평가 | `outputs/stock_evaluation_report.csv` |
| 재고 판정 | `outputs/stock_inventory_status.csv` |
| 무역 영향 | `outputs/trade_inventory_impact_report.json` |

정상 완료 기준:

- 최종 예측행의 `predicted_usage`가 음수가 아니다.
- `stock_item_key`가 미래 예측에서 중복되지 않는다.
- 전역 및 수요패턴별 weight 합이 1이다.
- 모델 bundle의 과거 weight와 정책 파일이 일치한다.
- 품질 보고서의 표준화 coverage 합이 전체 특성행과 일치한다.

## 7. 검증 명령

```bash
python -m pytest -q
python -m compileall -q src tests
git diff --check
```

이번 적용 결과:

- `217 passed`
- `1 skipped`
- `31 subtests passed`
- 미래 예측 163,229행
- 미래 예측 시계열 중복 0건
- 음수 예측 0건
- 전역·패턴별 weight 합 1.0

## 8. 현재 제한사항

1. raw_stock 최신 월은 2025-12다. 2026년 운영 예측에 사용하려면 신규 월 자료를
   추가하고 전체 배치를 다시 실행해야 한다.
2. 2018·2019년 뉴스, 가격, 수출입 위험은 근거 없이 소급 생성하지 않았다.
3. 뉴스 B/C 모델은 2024 학습구간에 실제 비영 신호가 생기기 전까지 제외된다.
4. 과거 전용 명칭 29,003개는 현재 표준품목 근거가 생길 때까지 학습에서 제외된다.
5. 품목별 실제 조달 리드타임이 없으면 15일 fallback이 적용된다.
6. 혼합단위 합계는 품목별 단위가 달라 실제 총 물량으로 해석할 수 없다.
7. DB 반영은 기관 매핑과 backend 계약 검증 후 별도 승인 절차로 수행한다.
