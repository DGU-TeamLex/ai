# WeP-Stock AI 전체 구조 및 진행 현황

작성일: 2026-07-20
최종 갱신: 2026-07-22
대상 저장소: `DGU-TeamLex/ai`
작업 브랜치: `feature/module-c-risk-adjustment`
마지막 원격 커밋: `ba2dba2`
기준 입력: `raw_stock/*.DAT`

## 1. 현재 상태 요약

WeP-Stock AI 저장소는 다음 네 영역을 담당한다.

1. `raw_stock` 수불 데이터를 기관·부서·내부 물품코드 시계열로 변환한다.
2. 물품명을 대표품목, 품목군, 세부유형, 규격, 단위, 원자재 후보로 구조화한다.
3. 다음 달 사용량을 예측하고 기본재고, 목표재고, 발주 권고량을 계산한다.
4. 뉴스·원자재 가격·공급위험 신호를 검수된 매핑을 통해 재고정책에 반영한다.

현재 기능별 상태는 다음과 같다.

| 영역 | 코드 상태 | 데이터 상태 | 운영 판단 |
|---|---|---|---|
| raw_stock 로딩·월별 전처리 | 완료 | 전체 10개 DAT 처리 완료 | 사용 가능 |
| 로컬 사용량 예측 | 완료 | 416,128개 시계열 평가 완료 | 품질 통제 조건부 사용 가능 |
| 재고0 기반 수요분류·mu 보정 | 완료 | 기관×품목 409,519행 생성 | 58,516행 검토 제외, 기관 매핑 전 적재 금지 |
| 품목 정규화·속성 분해 | 완료 | 대표품목 101,546개 후보 생성 | 검수 승인 확대 필요 |
| 세부유형·규격·단위 집계 | 완료 | 2,348개 출력 생성 | 최신 승인본으로 재실행 필요 |
| 원자재·수요트리거 후보 | 완료 | 101,546개 모두 후보 상태 | 운영 승인 관계 0건 |
| 뉴스·가격 수집 adapter | 완료 | 운영 provider 기본 비활성 | 실제 데이터 연결 필요 |
| Module C 위험 전파 | 완료 | 승인 관계가 없어 위험 점수 0행 | 운영 반영 차단 |
| 공급위험 품질 게이트 | 완료 | PASS 0, REVIEW 4,647, BLOCK 322 | 배치 승인 불가 |
| FastAPI·대시보드 | MVP 완료 | 배치 산출물 조회 방식 | backend 계약 검증 필요 |

핵심 해석은 다음과 같다.

- 로컬 사용량 예측은 품목 표준화가 완전히 끝나지 않아도 실행할 수 있다.
- 기관 간 품목 통합, 원자재 위험 연결, 질병 뉴스 연결은 승인된 표준 품목 관계가 필요하다.
- 현재 Module C 후보를 재고량에 반영하면 안 된다. 품질 게이트가 이를 의도적으로 막고 있다.
- `device/` 폴더는 물리적으로 남아 있으나 어떤 학습·분류·예측 코드에서도 사용하지 않는다.

## 2. 저장소 책임 경계

### AI 저장소가 담당하는 것

- DAT 로딩과 월별 수요·재고 집계
- 품목 정규화 후보와 검토 큐 생성
- 외부 근거 기반 품목 분류 후보 생성
- 사용량 모델 학습·검증·예측
- 뉴스·가격 위험 점수 계산
- Module C 공급위험 전파와 품질 검사
- 기본재고·목표재고·발주 권고 수치 계산
- 품질 게이트를 통과한 AI 배치 산출물 DML 적재
- 배치 산출물 조회용 FastAPI

### 별도 backend가 담당해야 하는 것

- 인증, 사용자, 권한
- 파일 업로드와 import batch 관리
- 검수 승인 UI와 승인 이력
- DB 스키마(DDL)와 마이그레이션
- 운영 입출고·발주 트랜잭션
- 알림 발송과 확인 상태
- 재배치 승인 워크플로우
- 기관별 운영 대시보드 API

AI는 수치와 판정 근거를 계산하고, 검증된 대용량 배치 결과를 직접 적재한다. backend는
스키마, 권한, 단건 운영 트랜잭션, 표출과 승인을 담당한다. backend가 AI의 SS/ROP 또는
발주량 계산식을 별도로 복제하면 정책이 이중화되므로 피해야 한다.

## 3. 디렉터리 구조

```text
teamlex/
├── raw_stock/                 유일한 학습 원천 DAT 10개
├── device/                    폐기된 입력, 사용 금지
├── regulazation/              외부 정규화 결과 감사용, 운영 입력 아님
├── data/
│   ├── mapping/               Git 관리 정책·사전·승인 매핑
│   ├── processed/             재생성 가능한 전체 중간 산출물
│   ├── sample/                검토용 1,000건 샘플
│   └── external/              공식 마스터·뉴스·가격 캐시
├── pipelines/item_material/   원자재·품목군 규칙과 통합 스크립트
├── src/
│   ├── modeling/              모델·평가·재고정책
│   ├── news/                  뉴스 수집·분석·점수화
│   ├── commodity/             가격 수집·특성·점수화
│   ├── module_c/              위험 결합·정책·품질 게이트
│   ├── loading/               품질검증 결과의 보호된 DB 배치 DML
│   ├── serving/               FastAPI
│   └── dashboard/             Streamlit 검토 화면
├── outputs/                   예측·감사·품질 결과
├── models/                    학습 모델과 manifest
├── tests/                     회귀·품질·서빙 테스트
└── docs/                      날짜순 설계·결과·운영 문서
```

### Git 관리 기준

| 경로 | Git 관리 | 이유 |
|---|---|---|
| `src/`, `tests/`, `docs/` | 관리 | 코드·검증·설명 |
| `data/mapping/` | 관리 | 사람이 수정·승인하는 정책 seed |
| `raw_stock/` | 제외 | 원천 데이터 |
| `data/processed/`, `data/sample/` | 제외 | 재생성 산출물 |
| `outputs/`, `models/` | 제외 | 실행 결과와 모델 바이너리 |
| `.env` | 제외 | API 키와 비밀정보 |

## 4. 전체 데이터 흐름

시스템은 하나의 직선 파이프라인보다 다섯 개의 흐름이 마지막에 결합되는 구조다.

```text
[품목 마스터 흐름]
raw_stock 물품명
  -> 별칭 정규화
  -> 대표품목 101,546개
  -> 제조사/성분/용량/게이지/포장수 분해
  -> family/subtype/spec/unit 후보
  -> 원자재/공급위험/수요트리거 후보
  -> 검수 승인 매핑

[사용량 예측 흐름]
raw_stock 수불
  -> 월별 기관·부서·내부코드 집계
  -> lag/rolling/계절/간헐수요 feature
  -> baseline 및 LightGBM 비교
  -> 로컬 품목 다음 달 사용량 예측

[수요 절단편향 보정 흐름]
raw_stock 일별 마감재고
  -> 거래일 사이 재고 지속일 계산
  -> 기관·부서·품목별 재고0/보유 일수
  -> 기관·품목별 ACTIVE/DORMANT/CENSORED
  -> Buhlmann 축소추정 + 10배 상한
  -> 검토 제외 후 demand_class/mu_corrected 배치 DML

[외부 위험 흐름]
뉴스 CSV/GDELT + 가격 CSV/API
  -> 기사·시장요인 위험 점수
  -> 승인된 질병/원자재/품목 관계
  -> Module C 수요위험·공급위험
  -> 품질 게이트

[재고정책·서빙 흐름]
사용량 예측 + 승인 위험 + 현재고/입고예정/미납
  -> base_stock
  -> target_stock
  -> recommended_order
  -> 세부유형별 집계
  -> FastAPI/대시보드
```

## 5. 배치 실행 구조

`python -m src.main`은 다음 순서만 자동 실행한다.

```text
preprocessing
-> news risk scoring
-> commodity risk scoring
-> module_c pipeline
-> feature engineering
-> model training
-> prediction
```

다음 작업은 비용이 크거나 사람 승인 경계가 있으므로 별도 실행한다.

```text
item normalization / enrichment / classification
item integrated pipeline
classified_prediction
evaluation
demand-class calculation / guarded DB loading
```

따라서 품목 승인 CSV가 변경된 뒤 `src.main`만 실행하면 세부유형 예측 산출물이 자동으로
갱신되지 않는다. `python -m src.modeling.classified_prediction`을 별도로 실행해야 한다.

## 6. 품목 정규화·분류 구조

### 처리 단계

| 단계 | 모듈 | 주요 결과 |
|---|---|---|
| 별칭 정규화 | `src/item_normalization.py` | 그룹·기본명·규격 후보 |
| 대표품목 집계 | `src/item_enrichment.py` | 기관과 무관한 대표품목 |
| 속성 분해 | `src/item_attribute_parser.py` | 제조사·성분·용량·게이지·포장수 |
| 근거 분류 | `src/item_classification.py` | family/subtype/spec/unit 후보·승인 |
| 원자재 후보 | `src/material_pipeline.py` | 원자재·공급위험·수요트리거 코드 |
| 최종 통합 | `src/item_integrated_pipeline.py` | 전체 CSV/Parquet와 1,000건 샘플 |

### 식별 수준

| 식별자 | 의미 |
|---|---|
| `local_item_key` | 기관의 실제 재고 품목 키 |
| `representative_item_id` | 기관별 이름 차이를 통합한 대표품목 |
| `item_group_id` | DB 대분류 14종 |
| `item_family_id` | 물품 또는 성분 family |
| `item_subtype_id` | 재고 예측용 세부유형 |
| `normalized_specification` | 3mL, 22G, 400mg 등 규격 |
| `unit_code` | EA, TABLET, ROLL 등 재고 수량 단위 |
| `parent_concept_id` | 예측 집계를 위한 상위 개념 후보 |

규격과 수량은 분리한다. 예를 들어 `24G`는 바늘 두께이고 재고 수량은 `EA`다. 서로 다른
규격이나 단위는 승인된 환산표 없이 합산하지 않는다.

### 현재 수치

| 지표 | 결과 |
|---|---:|
| raw_stock 논리 레코드 | 16,265,602 |
| 기관별 품목 별칭 | 409,519 |
| 대표품목 | 101,546 |
| 통합 분류 전체 행 | 101,546 |
| 통합 검토 샘플 | 1,000 |
| 승인 대표품목 | 1,177 |
| 승인 로컬 품목키 | 4,969 |
| 검토 대기 대표품목 | 100,369 |
| `UNCLASSIFIED` 대표품목 | 67,375 |
| 원자재 운영 승인 관계 | 0 |

`identified` 또는 `candidate_complete`는 제품 수준 검증 완료를 뜻하지 않는다. 현재 통합
원자재 결과 101,546개는 모두 `material_review_status=needs_review`다.

## 7. 사용량 예측 구조

### 학습 단위

```text
year_month + institution_code + department + item_code
```

- 목표값: `정상출고량`
- 현재고: 월 마지막 마감재고량
- 예측 범위: 다음 달 사용량
- 표준화 필요성: 로컬 시계열 예측에는 필수 아님
- 표준화 필요성: 기관 간 통합과 외부 위험 연결에는 필수

### 현재 데이터 품질

| 지표 | 결과 |
|---|---:|
| 월별 행 | 3,729,983 |
| 로컬 시계열 | 416,128 |
| 기간 | 2024-01 ~ 2025-12 |
| 학습 라벨 행 | 2,693,219 |
| 검증 행 | 704,421 |
| 테스트 행 | 605,437 |
| 6개월 이상 시계열 | 219,634 |
| 12개월 이상 시계열 | 134,054 |
| 0 수요 월 비율 | 32.58% |
| 음수 수요 행 | 1 |

음수 수요 1행은 임의 보정하지 않고 학습 라벨에서 제외한다. 관측되지 않은 월도 자동으로
0 수요라고 단정하지 않고 관측 공백으로 유지한다.

### 모델 평가

| 모델 | 검증 WAPE | 선택 여부 |
|---|---:|---|
| LightGBM L1 | 40.19% | 선택 |
| LightGBM Tweedie | 40.77% | 미선택 |
| 3개월 이동평균 | 44.52% | 미선택 |
| 전월값 | 48.07% | 미선택 |
| 전년 동월 | 54.92% | 미선택 |

현재 선택 모델은 `stock_model_a_usage_only`다. 뉴스·원자재·Module C 모델은 학습기간에
0이 아닌 승인 위험 신호가 없어 생성하지 않는다.

### 수요 절단편향 진단과 보정

월별 관측 공백을 재고 0으로 해석하지 않는다. `raw_stock`의 일별 마감재고가 다음 거래일까지
유지된다고 보고 `재고0 지속일 / 전체 지속일`을 계산한다.

| 지표 | 결과 |
|---|---:|
| 기관·부서·품목 로컬 시계열 | 416,128 |
| 기관·품목 handoff | 409,519 |
| ACTIVE | 183,437 |
| DORMANT | 135,730 |
| CENSORED | 90,352 |
| CENSORED 비율 | 22.06% |
| 자동 적재 제외·검토 | 58,516 |
| 적재 후보 | 351,003 |

PR #26의 초기 방식처럼 `1 - 거래가 관측된 월 비율`을 재고0 비율로 쓰면 CENSORED가
57.3%로 과다 분류된다. 현재 구현은 GitHub 검증 범위 20~25% 안에 들며, 양수
`mu_naive`의 보정은 최대 10배로 제한한다. 단, 이 보정값은 아직 예측 모델의 학습 라벨과
SS/ROP에 통합되지 않았으므로 현재 LightGBM 출력은 관측 출고량 기준선이다.

### 예측 산출물 시점 차이

| 산출물 | 기준 시점 | 행수 |
|---|---|---:|
| 승인 로컬 분류 CSV | 2026-07-18 | 4,969 |
| 로컬 예측 | 2026-07-15 | 163,229 |
| 세부유형 예측 | 2026-07-16 | 2,348 |
| 세부유형 예측이 사용한 승인 분류 | 이전 버전 | 4,948 |

승인 분류가 21개 증가했지만 세부유형 예측은 재실행되지 않았다. 다음 배치에서 로컬 예측,
세부유형 집계, 품질 리포트를 같은 실행 버전으로 다시 생성해야 한다.

## 8. 뉴스·원자재·Module C 구조

### 외부 데이터 provider

| 데이터 | 지원 방식 | 운영 기본값 |
|---|---|---|
| 뉴스 | `csv`, `gdelt`, `sample` | `disabled` |
| 가격 | `csv`, `alpha_vantage`, `fred`, `nasdaq_data_link`, `sample` | `disabled` |

`sample`은 smoke test 전용이다. 운영 provider 실패 시 합성 데이터로 자동 대체하지 않는다.
나프타 직접 가격이 없을 때 Brent를 사용할 수 있지만 `is_proxy`, `proxy_quality`, 전파
가중치를 통해 직접 가격과 구분한다.

### 두 개의 위험 경로

```text
뉴스 -> 질병/사건 -> 의료용품 수요 증가
뉴스/시장가격 -> 원자재 -> 의료용품 공급위험 증가
```

두 경로는 인과와 재고 처방이 다르므로 별도 feature로 유지한다.

- 수요위험: 예상 사용량 방향에 반영
- 공급위험: 리드타임과 안전재고 방향에 반영
- 동적 사건 코드: 기준 공급레벨을 덮어쓰지 않음
- 미승인 관계: 위험 점수 0

### Module C 현재 결과

| 지표 | 결과 |
|---|---:|
| 승인 분류 입력 | 4,969 |
| 품목-원자재 후보 관계 | 11,383 |
| 시장요인 연결 후보 | 6,741 |
| 원자재 관계 승인 | 0 |
| 운영 위험 반영 가능 관계 | 0 |
| 위험 점수 행 | 0 |
| 경보 행 | 0 |

Module C 계수는 실증 인과계수가 아니라 `policy_seed_requires_backtest` 상태의 초기
정책값이다. 실제 품절·리드타임·사건 구간으로 보정하기 전에는 정책 seed로 표시해야 한다.

## 9. 공급위험 품질 게이트

품질 게이트는 공급위험 메타코드, 기준레벨, z 값, 리드타임 배수, SS/ROP 재계산값,
단위 계약, 중복 재고정책을 검사한다.

```text
PASS   운영 입력 가능
REVIEW 자동 반영 금지, 검토 큐 이동
BLOCK  재고 계산 제외, 격리
```

### 현재 검사 결과

| 상태 | 행수 |
|---|---:|
| PASS | 0 |
| REVIEW | 4,647 |
| BLOCK | 322 |

| 오류 | 발생 행수 | 의미 |
|---|---:|---|
| `SR019_NON_SUPPLY_CODE_IN_BASELINE` | 4,647 | 사건 코드가 기준 공급코드 필드에 혼입 |
| `SR018_LEGACY_CODE_ALIAS_USED` | 1,213 | 구 API 수입의존 alias 사용 |
| `SR003_UNMAPPED_META_CODE` | 322 | 정책에 없는 국내과점 코드 |

`batch_release_allowed=false`이므로 AI 배치 적재와 자동 안전재고 조정을 중단해야 한다.
검토 샘플은 대표품목 중복 없이 1,000개가 생성돼 있다.

## 10. 재고정책 구조

### 기본 정책

```text
protection_period_demand = predicted_usage * (review_days + lead_time_days) / 30
safety_stock = protection_period_demand * safety_stock_rate
base_stock = protection_period_demand + safety_stock
```

### Module C 정책

- 수요위험은 위험조정 사용량에 반영한다.
- 공급위험은 유효 리드타임과 안전재고율에 반영한다.
- `risk_buffer`에는 상한을 둔다.
- 현재고, 입고예정, 미납을 반영한 `inventory_position`에서 발주량을 계산한다.

### 레벨 기반 SS/ROP 정책

```text
SS  = z(level) * daily_demand_stddev * sqrt(effective_lead_time_days)
ROP = mean_daily_usage * effective_lead_time_days + SS
```

이 방식은 명시적인 일 단위 평균, 일 단위 표준편차, 리드타임이 있을 때만 계산한다. 레벨
기반 SS/ROP와 연속형 Module C 목표재고를 동시에 합산하면 이중 안전재고가 되므로 금지한다.

## 11. API와 화면 구조

### FastAPI

주요 기능:

- 모델 학습 요청
- 예측 조회
- 공급위험 조회
- 재고정책 조회
- 세부유형·규격·단위별 예측 조회
- 발주 권고량 계산

API 요청 시 외부 뉴스나 가격 API를 호출하지 않는다. 배치가 미리 만든 CSV를 읽는
batch-first 구조다.

### Streamlit

`src/dashboard/app.py`는 AI 산출물과 재고정책을 확인하는 MVP다. 운영 승인, 사용자 권한,
알림 처리 기능은 포함하지 않는다.

## 12. 주요 데이터와 산출물 위치

### 입력·정책

| 경로 | 내용 |
|---|---|
| `raw_stock/*.DAT` | 유일한 학습 원천 |
| `data/mapping/item_family_taxonomy.csv` | 품목군·세부유형 taxonomy |
| `data/mapping/item_forecast_classification_approved.csv` | 승인 로컬 품목 분류 |
| `data/mapping/stock_item_material_mapping.csv` | 운영 승인 품목-원자재 관계 |
| `data/mapping/market_series_registry.csv` | 시장 시계열 등록부 |
| `data/mapping/material_market_factor_mapping.csv` | 원자재-시장요인 전파 관계 |
| `data/mapping/supply_risk_level_policy.json` | 공급위험 기준레벨 정책 |
| `data/mapping/supply_risk_anomaly_rules.json` | 품질 오류 규칙 21종 |

### 품목·예측

| 경로 | 내용 |
|---|---|
| `data/processed/item_integrated_classification_v2.parquet` | 대표품목 전체 통합본 |
| `data/sample/item_integrated_classification_sample_1000.csv` | 통합 검토 샘플 |
| `data/processed/stock_monthly.parquet` | 월별 로컬 시계열 |
| `outputs/stock_feature_table.parquet` | 학습·예측 feature |
| `outputs/stock_predictions.csv` | 로컬 품목 예측·재고정책 |
| `outputs/stock_predictions_by_subtype.csv` | 세부유형·규격·단위 집계 |
| `models/stock_manifest.json` | 모델별 평가와 선택 결과 |

### Module C·품질

| 경로 | 내용 |
|---|---|
| `outputs/module_c_material_exposure_candidates.csv` | 품목-원자재-시장요인 후보 |
| `outputs/module_c_supply_risk_quality_classified.csv` | 전체 PASS/REVIEW/BLOCK 분류 |
| `outputs/module_c_supply_risk_quality_issues.csv` | 오류별 상세 감사 로그 |
| `outputs/module_c_supply_risk_quality_review.csv` | 검토 대상 |
| `outputs/module_c_supply_risk_quality_quarantine.csv` | 운영 차단 대상 |
| `data/sample/module_c_supply_risk_quality_sample_1000.csv` | 대표품목 검토 샘플 |
| `outputs/module_c_supply_risk_quality_report.json` | 배치 승인 여부 |

## 13. 반드시 유지할 운영 원칙

1. `device/` 데이터는 어떤 경우에도 입력으로 사용하지 않는다.
2. 후보 분류를 승인 분류로 간주하지 않는다.
3. 동일 이름이어도 규격·단위가 다르면 자동 병합하지 않는다.
4. `24G` 같은 게이지를 수량으로 해석하지 않는다.
5. 공급위험, 수요트리거, 동적 사건 코드를 같은 필드에 섞지 않는다.
6. 승인되지 않은 원자재 관계는 위험 점수 0으로 둔다.
7. 운영 외부 데이터 장애 시 sample로 자동 대체하지 않는다.
8. 레벨 기반 SS/ROP와 Module C 연속형 버퍼를 동시에 합산하지 않는다.
9. `REVIEW`와 `BLOCK`은 자동 발주·재고 변경에 사용하지 않는다.
10. 규칙·정책·매핑 변경 시 버전과 근거를 함께 저장한다.

## 14. 현재 차단 요인

### 1순위: 메타코드 축 혼합

4,647개 로컬 품목의 기준 공급코드 필드에 동적 나프타 사건 코드가 포함돼 있다.
`supply_risk_meta_code`, `event_supply_risk_code`, `demand_trigger_meta_code`를 분리해야 한다.

### 2순위: 국내과점 코드 미매핑

322개 로컬 품목, 76개 대표품목이 `DOMESTIC_OLIGOPOLY_CONCENTRATION` 때문에 차단됐다.
수액제 시장 근거를 수액세트 제조시장에 일괄 전이하지 말고 품목별로 검증해야 한다.

### 3순위: 원자재 운영 승인 0건

후보는 11,383개지만 `data/mapping/stock_item_material_mapping.csv`에는 승인 행이 없다.
우선품목부터 reviewer, 검토일, 근거, 가중치, 노출도를 승인해야 한다.

### 4순위: 실제 외부 신호 부재

뉴스와 가격 adapter는 구현됐지만 운영 provider는 비활성이고 학습기간 위험 feature는 전부
0이다. 이 때문에 뉴스·원자재·Module C challenger 모델이 학습되지 않는다.

### 5순위: 승인 범위와 산출물 버전 불일치

최신 승인 로컬 품목은 4,969개지만 세부유형 예측은 4,948개 승인본으로 생성됐다. 동일
배치 ID 또는 생성시각을 사용해 품목 승인, 예측, Module C 산출물을 함께 재생성해야 한다.

### 6순위: 실증 calibration 미완료

현재 위험 가중치, z 값, 리드타임 상향, 버퍼 상한은 초기 정책값이다. 실제 공급지연,
품절, 소비 증가, 발주 이력으로 서비스수준과 과잉재고를 함께 검증해야 한다.

## 15. 권장 진행 순서

1. 날짜순 문서명 변경과 이 현황 문서를 커밋한다.
2. 공급·사건·수요 메타코드 컬럼을 분리하고 legacy alias를 이관한다.
3. 국내과점 76개 대표품목을 전수 검토한다.
4. 사용량 상위 품목부터 품목-원자재 관계를 승인한다.
5. 최신 승인 분류 기준으로 `classified_prediction`을 재실행한다.
6. 뉴스·가격 운영 provider와 비밀키를 설정한다.
7. Module C 품질 게이트를 AI loader/CI 적재 직전에 연결한다.
8. 실제 리드타임과 일별 수요분산 입력 계약을 확정한다.
9. 사건연구와 재고 시뮬레이션으로 정책 계수를 보정한다.
10. 승인 범위를 101,546개 대표품목 전체로 단계적으로 확대한다.

## 16. 실행 명령

### 기본 배치

```bash
conda run -n teamlex python -m src.main
```

### 품목 전체 통합

```bash
conda run -n teamlex python -m src.item_integrated_pipeline \
  --with-excel \
  --sample-size 1000
```

### 세부유형 예측 재생성

```bash
conda run -n teamlex python -m src.modeling.classified_prediction
```

### Module C와 품질 분류

```bash
conda run -n teamlex python -m src.module_c.pipeline
```

### 운영 적재 전 엄격 검사

```bash
conda run -n teamlex python -m src.module_c.supply_risk_anomaly_filter \
  --input inventory_export.csv \
  --output-dir outputs/inventory_supply_quality \
  --key-column inventory_id \
  --code-column supply_risk_meta_code \
  --operational-mode \
  --require-release
```

### 수요분류 계산과 보호된 DML

```bash
conda run -n teamlex python -m src.loading.compute_demand_class_mu_corrected

# 기본 dry-run. 명시적 기관 매핑이 없으면 DB 접속 전 중단한다.
conda run -n teamlex python -m src.loading.reflect_demand_class_mu_corrected
```

`--apply`는 품질 리포트, backend DDL, 명시적 기관 ID 매핑, 99% 이상 DB 키 매칭을 모두
통과한 뒤에만 허용한다. 이 적재는 `demand_class`, `mu_corrected`, `updated_at`만 수정하며
`inventory.status`를 변경하지 않는다.

### 테스트

```bash
conda run -n teamlex python -m unittest discover -s tests
```

2026-07-22 기준 샌드박스 전체 테스트는 137개 중 136개 통과, TCP 제한 대상 1개 skip이다.
skip된 uvicorn HTTP 통합 테스트는 샌드박스 밖에서 별도 실행해 통과했고, Python compile
검사도 통과했다.

## 17. 관련 문서

| 문서 | 내용 |
|---|---|
| `docs/2026-07-13_02_ITEM_STANDARDIZATION_TEAM_GUIDE.md` | 팀 수동 검수·표준화 절차 |
| `docs/2026-07-15_01_USAGE_FORECAST_MODEL_EVALUATION.md` | 사용량 모델 평가 |
| `docs/2026-07-16_02_CLASSIFIED_FORECAST_INTEGRATION.md` | 세부유형 예측 입력 계약 |
| `docs/2026-07-18_02_ITEM_INTEGRATED_PIPELINE_V2_1_RESULT.md` | 통합 품목·원자재 결과 |
| `docs/2026-07-18_03_INVENTORY_QUANTITY_MODEL.md` | 재고량 계산식 |
| `docs/2026-07-18_04_공급위험레벨_안전재고_과적재_보고서.md` | 공급레벨 과적재 진단 |
| `docs/2026-07-18_05_MODULE_C_RISK_ADJUSTMENT.md` | Module C 설계와 외부 provider |
| `docs/2026-07-18_06_SUPPLY_RISK_QUALITY_GATE.md` | 품질 게이트 운영법 |
| `docs/2026-07-18_07_MODULE_C_QUALITY_RESULT_AND_GITHUB_ISSUES.md` | 이슈 대조와 품질 분류 결과 |
| `docs/2026-07-22_05_GITHUB_ISSUE_PR_ALIGNMENT.md` | 최신 열린 이슈·PR 대조와 수정 결과 |

## 18. 완료 기준

현재 시스템의 최종 완료는 코드가 실행되는 것만으로 판단하지 않는다. 다음 조건을 모두
충족해야 한다.

- 승인 품목-원자재 관계가 존재한다.
- 공급·수요·사건 메타코드 축이 분리돼 있다.
- 운영 뉴스·가격 데이터가 감사 가능한 출처로 수집된다.
- 공급위험 품질 게이트에 `PASS` 행이 존재한다.
- AI 적재 모듈은 `PASS` 및 `load_eligible` 행만 적재하고 실패 시 기존 재고값을 유지한다.
- 실제 리드타임·수요분산으로 SS/ROP가 재현된다.
- 사건연구와 재고 시뮬레이션에서 결품 감소와 과잉재고 억제가 함께 확인된다.
- 같은 배치 버전의 분류, 예측, 위험, 재고 산출물이 일관되게 연결된다.
