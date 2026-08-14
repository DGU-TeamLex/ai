# GitHub 이슈 점검 및 보완 결과

> 후속 변경: 이 문서의 2018·2019년 “보조 분석 전용” 정책은 표준품목 매칭 검증 후
> 변경됐다. 현재 정책과 성능은
> `2026-07-29_06_STANDARDIZED_HISTORY_LIGHTGBM_UPDATE.md`를 기준으로 한다.

## 1. 점검 범위

- 대상 저장소: `DGU-TeamLex/ai`
- 점검일: 2026-07-29
- 확인 범위: 공개 상태인 AI 이슈 28건
- 구현 및 전수 검증 대상: #9, #10, #34, #38, #39, #40, #42, #52
- 기존 구현 확인 대상: #4, #5, #8, #18

이 문서는 이슈가 닫혔다는 선언이 아니다. AI 저장소에서 확인된 구현 범위와
backend, frontend, 운영 데이터에 남은 작업을 분리해 기록한다.

## 2. 이번 코드 보완

### 수요 및 수지식

- 순수요를 `정상출고량`으로 통일했다.
- `자동폐기출고량`은 수요 모멘트와 재고 수지식에서 제외했다.
- 월별 수지 잔차 합계와 위반 건수를 산출물에 남긴다.
- 음수 정상출고는 수요 모멘트에서 제외하고 품질 지표로 관리한다.

### 현재 및 과거 데이터 분리

- `익스포트_*.DAT`는 2024~2025 모델 입력으로 처리한다.
- `*2018_2019*.DAT`는 별도 보조 Parquet로 처리한다.
- 2020~2023 공백 때문에 2018~2019 데이터는 모델 학습에 합치지 않는다.
- 저장 직후 대형 DataFrame을 해제해 연속 처리 시 메모리 사용을 줄였다.

### 재고 판정 및 발주

- `mu`, `sigma`의 임의 양수 바닥값을 제거했다.
- 바닥값 적용 여부 플래그를 판정, 예측, API 출력에 포함했다.
- 무수요 시계열을 `DORMANT`로 분류해 부족 판정과 자동발주에서 제외했다.
- `NOT_OPERATED`는 권고량 0, `DATA_MISSING` 및 stale 데이터는 권고량
  `null`로 처리한다.
- 계산 전 원시 권고량과 발주 억제 여부 및 사유를 감사용으로 보존한다.

### 리드타임

- 품목별 P25 우선, 최소 1일, fallback 15일, 상한 120일 정책을 반영했다.
- 공급위험 배수보다 먼저 fallback과 cap을 적용한다.
- 원시값, 적용값, fallback/cap 플래그와 정책 버전을 출력한다.

## 3. 전수 재처리 결과

| 구분 | 현재 모델 입력 | 과거 보조 데이터 |
|---|---:|---:|
| 기간 | 2024-01~2025-12 | 2018-01~2019-12 |
| 월별 행 | 3,729,983 | 1,460,785 |
| 기관 | 3,530 | 1,553 |
| 품목 | 17,155 | 11,795 |
| 시계열 | 416,128 | 179,295 |
| 수지식 위반 원시 행 | 8 | 39 |
| 자동폐기 제외 | 100% | 100% |

- 전체 전처리 시간: 약 14분 33초
- 관측한 현재 데이터 월별 임시 파일 최대 크기: 약 2.3GB
- 전처리 보고서: `outputs/stock_preprocessing_report.json`
- 예측 데이터 품질: `outputs/stock_forecast_data_quality.json`

이슈 #42의 사전 실측값인 현재 0건, 과거 68건과 이번 로더 결과인 현재 8건,
과거 39건이 다르다. 47개 예외의 원본 행, 허용오차 및 파서 제외 기준을 확인할
때까지 100% 일치로 간주하지 않는다.

## 4. 재고 판정 결과

- 전체 시계열: 416,128
- DORMANT: 189,791
- `mu_is_floored`: 0
- `sigma_is_floored`: 0
- 원시 비양수 재고 후보: 10,910
- 최종 긴급 부족: 770
- 후보 감소율: 92.94%

최신 예측 163,229행에서는 47,186행의 자동발주가 억제됐다.

| 억제 사유 | 행 | 처리 |
|---|---:|---|
| DORMANT | 41,742 | 권고량 0 |
| NOT_OPERATED | 4,864 | 권고량 0 |
| DATA_MISSING | 580 | 권고량 null |

- 원시 권고량 합계: 1,360,355.48
- 운영 권고량 합계: 1,303,876.62
- 리드타임 fallback: 163,229행
- 리드타임 cap: 0행

전 행 fallback은 품목별 실측 리드타임 입력이 아직 예측 데이터에 연결되지
않았음을 뜻한다. 정책 구현과 추정기 연결을 같은 완료 상태로 보면 안 된다.

## 5. 모델 재평가

- 라벨 행: 2,693,219
- 학습: 1,383,361
- 검증: 704,421
- 테스트: 605,437

검증 WAPE:

| 방법 | WAPE |
|---|---:|
| 사용량 L1 | 40.19% |
| 사용량 Tweedie | 40.77% |
| Module C 포함 | 41.04% |

검증 구간으로 선택한 패턴별 앙상블을 테스트 구간에 고정 적용한 결과:

| 지표 | 기존 | 패턴별 앙상블 | 변화 |
|---|---:|---:|---:|
| WAPE | 39.05% | 37.58% | -1.47%p |
| RMSE | 298.17 | 201.92 | -96.25 |
| 편향률 | -14.98% | -6.40% | +8.59%p |

MAPE, SMAPE, RMSLE는 악화했다. 따라서 패턴별 앙상블은 총수요 오차와 큰
오차에는 유리하지만 0수요 및 저수요 품목까지 일관되게 개선한 모델은 아니다.
또한 학습 구간의 뉴스 전용 특성이 모두 0이어서 뉴스 모델 2종은 자동 제외됐다.

## 6. 이슈 상태 판단

| 이슈 | 판단 | 남은 핵심 작업 |
|---|---|---|
| #9 | 부분 완료 | backend MOCK 교체, 간헐수요 모델과 재고비용 프론티어 |
| #10 | 부분 완료 | 최대 RSS 자동 계측, 예외 및 명칭 충돌 보고서, 결정성 |
| #34 | 부분 완료 | 과거 보조 지표 산출, backend 시계열 적재 |
| #38 | AI 출력 완료 | frontend 표시 및 backend null 계약 |
| #39 | 부분 완료 | 품목별 P25 추정기 연결과 영향 시뮬레이션 |
| #40 | AI 코드 완료 | backend 적재 정의 교차 확인 |
| #42 | 부분 완료 | 수지식 예외 47건 원인 확인 |
| #52 | 부분 완료 | backend 스키마와 운영 DB 재산정, shrinkage 비교 |

우선순위가 높은 미구현 이슈:

- #43: 약 성분 기반 의료성 및 중요도 재분류
- #44: 안전재고 pooling 계층 재설계
- #45: 약 성분 3단계 계층 구조
- #21: 외부 충격 event study
- #41: main/dev 브랜치 운영 규칙

## 7. GitHub 코멘트

- [#4 외부 시세 수집기](https://github.com/DGU-TeamLex/ai/issues/4#issuecomment-5112812601)
- [#5 HTTP 통합 테스트](https://github.com/DGU-TeamLex/ai/issues/5#issuecomment-5112816288)
- [#8 device 의존성 제거](https://github.com/DGU-TeamLex/ai/issues/8#issuecomment-5112807744)
- [#9 실제 수요예측 평가](https://github.com/DGU-TeamLex/ai/issues/9#issuecomment-5113251366)
- [#10 전체 전처리](https://github.com/DGU-TeamLex/ai/issues/10#issuecomment-5113229171)
- [#18 나프타 가격축](https://github.com/DGU-TeamLex/ai/issues/18#issuecomment-5112942645)
- [#34 2018~2019 보조 경로](https://github.com/DGU-TeamLex/ai/issues/34#issuecomment-5113232193)
- [#38 미운영 및 누락 발주 억제](https://github.com/DGU-TeamLex/ai/issues/38#issuecomment-5113234859)
- [#39 리드타임 정책](https://github.com/DGU-TeamLex/ai/issues/39#issuecomment-5113238102)
- [#40 순수요 정의](https://github.com/DGU-TeamLex/ai/issues/40#issuecomment-5113240746)
- [#42 재고 수지식](https://github.com/DGU-TeamLex/ai/issues/42#issuecomment-5113244128)
- [#52 수요 바닥값 및 DORMANT](https://github.com/DGU-TeamLex/ai/issues/52#issuecomment-5113247284)

## 8. 검증

- 전체 단위 및 통합 테스트: 213개 통과
- skip: 1개
- skip 사유: 실행환경 정책이 로컬 TCP 통합 테스트를 차단
- 최종 현재 예측: `outputs/stock_predictions.csv`
- 최종 백테스트: `outputs/stock_backtest_predictions.csv`
- 앙상블 평가: `outputs/forecast_ensemble_temporal_report.json`
- 재고 판정: `outputs/stock_inventory_status.csv`
