# 표준화 기반 과거자료 LightGBM 및 가중치 업데이트

## 1. 결론

품목 표준화 결과를 2018·2019년과 2024·2025년 재고자료에 공통 적용하고,
현재 표준품목과 매칭된 과거자료만 LightGBM 학습에 추가했다.

- 과거 학습 가중치: 검증 WAPE 기준 `1.0`
- 주 예측모델: `stock_model_a_usage_only` LightGBM L1
- 최종 전역 앙상블: L1 `0.25`, Tweedie `0.15`, Module C `0.60`
- 최근 3개월 최종 WAPE: 기존 `37.5788%` → 신규 `37.1840%`
- 최근 3개월 RMSE: 기존 `201.9180` → 신규 `200.6530`
- 최근 3개월 편향률: 기존 `-6.3977%` → 신규 `-3.4671%`

최신 테스트를 가중치 선택에 사용하지 않았다. 과거자료 가중치는 2025년 1~6월,
앙상블 가중치는 2025년 8~9월로 선택하고, 최종 확인은 2025년 10~12월에 한 번 수행했다.

## 2. 표준화 연결 방식

물리 재고 시계열은 기존대로 `기관 + 부서 + 내부 물품코드`를 유지한다. 기관이나
내부코드가 다르다는 이유로 재고를 합산하지 않는다.

표준화 계층은 다음 두 키를 분리한다.

| 키 | 역할 | 모델 직접 입력 |
|---|---|---|
| `standard_item_key` | 정규화된 상세 품목명 식별 | 아니요 |
| `standard_item_definition_key` | 품목군·family·subtype·규격·단위 의미 정의 | 예 |

`standard_item_key` 125,557개와 내부 `item_code`는 고유값이 너무 많아 범주형 모델에
직접 넣으면 메모리 비용과 과적합 위험이 커진다. 두 값은 식별, 결과 출력, 원자재 연결에
보존하고, 모델은 4,745개 의미 정의와 하위 분류 계층을 사용한다.

표준 매핑 결과:

| 항목 | 값 |
|---|---:|
| 전체 로컬 품목 | 585,279 |
| 현재 로컬 품목 | 409,519 |
| 과거 로컬 품목 | 175,760 |
| 과거 학습 허용 품목 | 146,757 |
| 과거 학습 허용률 | 83.50% |
| 과거 전용 명칭 제외 | 29,003 |
| 월별 통합 행 | 5,190,767 |

과거 strict/core 매칭만 학습을 허용한다. 단순 이름 fallback 143,736 품목-월은 특성표에
감사용으로 남기지만 학습에서는 제외한다.

## 3. 시계열 안전장치

- 2018~2019와 2024~2025는 같은 표준품목 특성을 공유할 수 있다.
- 기관·부서·내부 물품코드 시계열은 합치지 않는다.
- 2019→2024 공백에서 `series_segment_id`를 새로 생성한다.
- 과거 마지막 값이 2024년 `lag_1`이나 rolling 값으로 이어지지 않는다.
- 과거자료에는 해당 기간 외부위험을 소급 추정하지 않고 0으로 둔다.
- 최신 테스트 기간은 과거자료 및 앙상블 가중치 선택에서 제외한다.

학습 가능 라벨 3,730,978행의 분할:

| 구간 | 행 |
|---|---:|
| 2018~2019 표준매칭 학습 | 952,002 |
| 2024 현재자료 학습 | 1,383,361 |
| 2025-01~06 검증 | 704,421 |
| 2025-07 이후 테스트 origin | 605,437 |

## 4. 과거자료 가중치 선택

동일한 LightGBM L1 구조에서 과거자료 sample weight만 변경했다.

| 과거 weight | 검증 WAPE | RMSE | 편향률 |
|---:|---:|---:|---:|
| 0.00 | 40.0699% | 298.513 | -7.672% |
| 0.10 | 39.7586% | 279.792 | -6.291% |
| 0.25 | 39.7295% | 276.219 | -5.690% |
| 0.50 | 39.4402% | 273.499 | -5.834% |
| 0.75 | 39.3473% | 271.302 | -5.853% |
| 1.00 | **39.2593%** | **269.703** | -5.907% |

`1.0`은 현재자료 전용 대비 WAPE `0.8106%p`, RMSE `28.809` 개선이다.
선택값은 `data/mapping/historical_training_policy.json`에 저장한다.

## 5. 모델 및 최종 가중치

확장형 시계열 교차검증:

| 모델 | 목적함수 | 검증 WAPE | 결과 |
|---|---|---:|---|
| `stock_model_a_usage_only` | L1 | **39.1137%** | 주 모델 |
| `stock_model_a_usage_tweedie` | Tweedie | 39.6520% | 앙상블 후보 |
| `stock_model_d_module_c` | Tweedie + 외부위험 | 39.6969% | 앙상블 후보 |
| 뉴스 B/C | Tweedie | - | 2024 뉴스 특성 0으로 제외 |

전역 앙상블 가중치는 `0.05` 간격 231개 조합을 검증했다.

| 출력 | 최종 weight |
|---|---:|
| L1 | 0.25 |
| Tweedie | 0.15 |
| Module C | 0.60 |

수요패턴별로 별도 weight를 선택하는 router가 전역 weight보다 검증 WAPE가 낮아
최종 정책으로 사용된다. 기존 패턴 정책과도 별도 비교했으며 신규 정책은 검증
`36.5273% → 36.5004%`, 테스트 `37.2014% → 37.1840%`로 소폭 개선됐다.

## 6. 기존 대비 성능

동일한 최신 2025년 10~12월 테스트:

| 항목 | 기존 | 신규 | 변화 |
|---|---:|---:|---:|
| L1 WAPE | 39.0531% | 38.2461% | -0.8071%p |
| 최종 WAPE | 37.5788% | **37.1840%** | -0.3948%p |
| 최종 RMSE | 201.918 | **200.653** | -1.265 |
| 최종 편향률 | -6.398% | **-3.467%** | +2.931%p |

전체 2025년 8~12월 backtest 최종 WAPE는 `36.9198%`, RMSE는 `196.912`,
편향률은 `-5.035%`다.

WAPE는 개선됐지만 0 수요 비중이 24%이고 MAPE는 여전히 높다. 따라서 품목별
정확도 보증값으로 해석하지 말고, 수요패턴·품목군별 보고서와 함께 사용해야 한다.

## 7. 메모리 및 운영 보완

519만 행 학습에서 발생한 OOM을 다음과 같이 해결했다.

- 수치형 학습열을 `float32`로 축소
- 범주열 전체 문자열 복사를 제거하고 코드 맵을 직접 저장
- LightGBM을 column-wise로 실행하고 히스토그램 캐시를 256MB로 제한
- 검증과 최종 재적합을 분리
- L1, Tweedie, Module C 최종 재적합을 각각 독립 프로세스에서 실행
- 예측은 수요패턴용 최소 열과 테스트·미래 예측행을 분리 로딩

`python -m src.modeling.training`은 위 프로세스 분리를 자동 수행한다.

## 8. 주요 산출물

| 산출물 | 위치 |
|---|---|
| 표준품목 전체 매핑 | `data/processed/stock_standard_item_mapping.parquet` |
| 표준품목 1,000건 샘플 | `data/sample/stock_standard_item_mapping_sample_1000.csv` |
| 통합 특성표 | `outputs/stock_feature_table.parquet` |
| 과거 weight 보고서 | `outputs/historical_training_weight_report.json` |
| 과거 weight 정책 | `data/mapping/historical_training_policy.json` |
| 모델 비교 | `outputs/stock_model_validation_report.csv` |
| fold 비교 | `outputs/stock_model_cv_report.csv` |
| 앙상블 보고서 | `outputs/forecast_ensemble_temporal_report.json` |
| 앙상블 정책 | `data/mapping/forecast_ensemble_policy.json` |
| 전체 backtest | `outputs/stock_backtest_predictions.csv` |
| 최종 미래 예측 | `outputs/stock_predictions.csv` |
| 평가 보고서 | `outputs/stock_evaluation_report.csv` |

최종 예측에는 표준품목 키, 정의키, 품목군, family, subtype, 규격, 단위,
매칭방식이 포함된다.

## 9. 재실행

```bash
conda run -n teamlex python -m src.modeling.historical_weight_tuning --apply
conda run -n teamlex python -m src.modeling.training
conda run -n teamlex python -m src.modeling.prediction
conda run -n teamlex python -m src.modeling.temporal_ensemble_tuning --apply
conda run -n teamlex python -m src.modeling.prediction
```

## 10. 남은 제한

1. 뉴스 모델 B/C는 2024 학습구간 뉴스 특성이 모두 0이라 아직 학습되지 않는다.
2. 과거 2018·2019년의 뉴스·가격·수출입 위험은 소급 연결하지 않았다.
3. 과거 전용 명칭 29,003개는 현재 표준품목 근거가 생기기 전까지 학습 제외한다.
4. 앙상블 선택용 월이 2개월, 최종 테스트가 3개월뿐이므로 월이 추가될 때 재평가한다.
5. 성능 최적화 기준은 사용량 WAPE이며 품절비용·보관비용을 직접 최적화하지 않는다.

## 11. 최종 검증

- 전체 테스트: `217 passed, 1 skipped, 31 subtests passed`
- 핵심 표준화·가중치 테스트: `27 passed`
- `compileall`: 통과
- `git diff --check`: 통과
- 미래 예측: 163,229행, 시계열 키 중복 0, 음수 예측 0
- 세 모델 bundle의 과거 weight: 모두 `1.0`
- 세 모델의 의미 정의 특성 포함: 모두 확인
- 세 모델의 `item_code`, `standard_item_key` 직접 특성: 모두 제외
- 전역 및 수요패턴별 앙상블 weight 합: 모두 `1.0`
- 품질 보고서 표준화 coverage 합: 5,190,767행
