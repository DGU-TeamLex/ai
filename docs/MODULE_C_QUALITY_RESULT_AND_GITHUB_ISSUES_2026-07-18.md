# Module C 공급위험 품질 분류 결과와 GitHub 이슈 점검

작성일: 2026-07-18
대상 저장소: [DGU-TeamLex/ai](https://github.com/DGU-TeamLex/ai)
작업 브랜치: `feature/module-c-risk-adjustment`
검토 범위: 현재 작업 트리의 Module C, 공급위험 정책, 품질 게이트, 생성 데이터

## 1. 결론

공급위험 오류 분류 코드는 구현됐고 전체 테스트 입력에 재현 가능하게 적용된다. 다만 현재
Module C 대상 4,969개 로컬 품목 중 운영 반영 가능한 `PASS`는 0개다.

```text
PASS:       0
REVIEW: 4,647
BLOCK:    322
batch_release_allowed: false
```

이는 사용량 예측 자체를 중단해야 한다는 뜻이 아니다. 기본 사용량 예측은 계속 사용할 수
있지만, 현재 공급위험 메타코드로 안전재고, ROP, 발주량을 자동 상향하면 안 된다는 뜻이다.

현재 가장 먼저 해결할 문제는 다음 두 가지다.

1. 기준 공급코드 필드에서 동적 사건 코드 `MIDEAST_NAPHTHA_PETROCHEM_SHOCK`를 분리한다.
2. 정책에 없는 `DOMESTIC_OLIGOPOLY_CONCENTRATION`을 일괄 alias로 바꾸지 말고 품목별 근거로
   재검토한다.

## 2. 데이터 범위

이번 품질 분류의 모집단은 전체 대표품목 101,546개가 아니다. 현재 승인된 예측 분류를
Module C 후보 파이프라인에 연결할 수 있는 로컬 품목 범위다.

| 단계 | 행수 | 식별 단위 | 의미 |
|---|---:|---|---|
| 통합 대표품목 | 101,546 | `representative_item_id` | 전체 품목 분류 후보 |
| Module C 노출 후보 | 11,383 | 로컬 품목과 복수 원자재 관계 | 검토 전 후보 관계 |
| 공급위험 품질 분류 | 4,969 | `local_item_key` | 현재 Module C 검사 대상 |
| 품질 분류 내 대표품목 | 1,177 | `representative_item_id` | 검사 대상의 대표품목 수 |

따라서 101,546개 전체 품목의 공급위험 분류가 끝났다고 해석하면 안 된다. 전체 범위로
확장하려면 품목 분류 및 원자재 관계가 먼저 검수 승인돼야 한다.

## 3. 실행 방법

전체 Module C 후보와 품질 분류를 함께 재생성한다.

```bash
conda run -n teamlex python -m src.module_c.pipeline
```

DB 적재 직전 데이터는 운영 모드로 검사한다.

```bash
conda run -n teamlex python -m src.module_c.supply_risk_anomaly_filter \
  --input inventory_export.csv \
  --output-dir outputs/inventory_supply_quality \
  --key-column inventory_id \
  --code-column supply_risk_meta_code \
  --operational-mode \
  --sample-size 1000 \
  --require-release
```

`--require-release`는 `REVIEW`, `BLOCK`, 데이터셋 이슈 중 하나라도 있으면 모든 감사 파일을
저장한 뒤 종료코드 2를 반환한다.

## 4. 전체 분류 결과

### 상태별 결과

| 상태 | 행수 | 운영 처리 |
|---|---:|---|
| `PASS` | 0 | 현재 적재 가능 행 없음 |
| `REVIEW` | 4,647 | 자동 재고 조정 금지, 검토 큐 이동 |
| `BLOCK` | 322 | 재고 계산에서 제외하고 격리 |

### 오류별 결과

오류는 한 행에 둘 이상 존재할 수 있으므로 오류 합계는 입력 행수보다 크다.

| 오류 | 발생 행수 | 판단 |
|---|---:|---|
| `SR019_NON_SUPPLY_CODE_IN_BASELINE` | 4,647 | 사건 코드가 기준 공급코드에 혼입 |
| `SR018_LEGACY_CODE_ALIAS_USED` | 1,213 | 구 API 수입의존 alias 사용, SR019와 중복 |
| `SR003_UNMAPPED_META_CODE` | 322 | 현재 정책에 없는 국내과점 코드 |

`dataset_quality_status=PASS`는 CRITICAL 집중도 같은 데이터셋 집계 오류가 추가로 없다는
뜻이다. 행 단위 오류가 존재하므로 `batch_release_allowed=false`이며 자동 적재는 차단된다.

### 전체 산출물

| 파일 | 행수 | 용도 |
|---|---:|---|
| `outputs/module_c_supply_risk_quality_classified.csv` | 4,969 | 물품명과 분류 결과를 포함한 전체 데이터 |
| `outputs/module_c_supply_risk_quality_issues.csv` | 6,182 | 오류 한 건당 한 행인 감사 로그 |
| `outputs/module_c_supply_risk_quality_passed.csv` | 0 | 자동 반영 가능 데이터 |
| `outputs/module_c_supply_risk_quality_review.csv` | 4,647 | 사람 검토 대상 |
| `outputs/module_c_supply_risk_quality_quarantine.csv` | 322 | 운영 반영 차단 대상 |
| `outputs/module_c_supply_risk_quality_report.json` | 1 | 실행 요약과 배치 승인 결과 |

## 5. 검토 샘플 1,000건

샘플은 단순 상위 1,000행이 아니다. 같은 품목이 기관별로 반복되는 편향을 줄이기 위해
다음 순서로 결정론적으로 선택한다.

1. `representative_item_id` 다양성을 우선한다.
2. `BLOCK`, `REVIEW`, `PASS` 상태를 층화한다.
3. 같은 상태 안에서 주요 오류코드를 층화한다.
4. 사용량, 발생건수, 로컬 품목키 순으로 같은 입력에서 항상 같은 행을 선택한다.

생성 파일:

```text
data/sample/module_c_supply_risk_quality_sample_1000.csv
```

| 지표 | 결과 |
|---|---:|
| 샘플 행 | 1,000 |
| 고유 로컬 품목키 | 1,000 |
| 고유 대표품목 | 1,000 |
| 고유 표시 물품명 | 1,000 |
| `BLOCK` 대표품목 | 76 |
| `REVIEW` 대표품목 | 924 |

전체 `BLOCK`은 322개 로컬 품목이지만 대표품목으로는 76개다. 샘플에는 76개 대표품목을
한 번씩 넣었고, 322개 전체 행은 quarantine 파일에 보존했다.

### 샘플 표현 예시

| 순번 | 물품명 | family | 세부유형 | 규격 | 단위 | 원자재 | 오류 | 상태 |
|---:|---|---|---|---|---|---|---|---|
| 1 | 수액세트 | `INFUSION_SET` | `INFUSION_SET` | 수액세트 | `EA` | `POLYVINYL_CHLORIDE_PVC` | `SR003` | `BLOCK` |
| 2 | 수액셋트 | `INFUSION_SET` | `INFUSION_SET` | 수액세트 | `EA` | `POLYVINYL_CHLORIDE_PVC` | `SR003` | `BLOCK` |
| 3 | 수액세트(무침) | `INFUSION_SET` | `INFUSION_SET` | 수액세트 | `EA` | `POLYVINYL_CHLORIDE_PVC` | `SR003` | `BLOCK` |
| 77 | 아루펜정 400mg(삼남제약)-1정 | `IBUPROFEN` | `DRUG_TABLET` | 400mg | `TABLET` | `POLYPROPYLENE_PP` | `SR018;SR019` | `REVIEW` |
| 78 | 주사기3cc | `DISPOSABLE_SYRINGE` | `SYRINGE_USAGE_BASED` | 3mL | `EA` | `POLYPROPYLENE_PP` | `SR019` | `REVIEW` |
| 79 | 3cc주사기 | `DISPOSABLE_SYRINGE` | `SYRINGE_USAGE_BASED` | 3mL | `EA` | `POLYPROPYLENE_PP` | `SR019` | `REVIEW` |
| 80 | 안전란셋(28G) | `BLOOD_LANCET` | `BLOOD_LANCET` | 28G | `EA` | `POLYPROPYLENE_PP` | `SR019` | `REVIEW` |
| 81 | (AI)오토첵 란셋(30G) | `BLOOD_LANCET` | `BLOOD_LANCET` | 30G | `EA` | `POLYPROPYLENE_PP` | `SR019` | `REVIEW` |

`28G`, `30G`는 개수가 아니라 바늘 게이지 규격으로 유지된다. 샘플 CSV에는 위 컬럼 외에도
원자재 근거, 정책 재도출값, 전체 오류코드, 권장 조치가 포함된다.

## 6. GitHub 열린 이슈 대조

2026-07-18 기준 열린 이슈는 17건이다. 아래 상태는 GitHub 체크박스가 아니라 현재 로컬
코드, 테스트, 데이터 산출물을 함께 대조한 기술 판단이다.

| 이슈 | 현재 판단 | 근거와 남은 일 |
|---|---|---|
| [#2 실제 뉴스 API](https://github.com/DGU-TeamLex/ai/issues/2) | 부분 구현 | GDELT/CSV 수집과 캐시 구현. 운영 기간, 스케줄, 장애 정책 확정 필요 |
| [#4 원자재 가격 API](https://github.com/DGU-TeamLex/ai/issues/4) | 부분 구현 | Alpha Vantage/FRED/Nasdaq/CSV adapter 구현. 실제 운영 가격 입력은 없음 |
| [#5 API 통합 테스트](https://github.com/DGU-TeamLex/ai/issues/5) | 부분 구현 | 함수 단위 예측·발주 테스트 존재. `/health` 포함 실제 HTTP TestClient 검증 필요 |
| [#8 device 의존성 제거](https://github.com/DGU-TeamLex/ai/issues/8) | 코드 완료 | `src/`, `tests/` 의존성 제거. 이슈의 남은 조건은 dev 병합 |
| [#9 실제 수요예측](https://github.com/DGU-TeamLex/ai/issues/9) | 핵심 구현 | 실제 예측·서빙과 모델 비교 구현. 재고 목적함수 기반 최종 champion 검증 필요 |
| [#10 raw_stock 전체 전처리](https://github.com/DGU-TeamLex/ai/issues/10) | 대부분 구현 | 16,265,602건 처리 및 3,729,983 월별 행 생성. 자원 사용·재현성 보고 보강 필요 |
| [#11 품목-원자재 매핑](https://github.com/DGU-TeamLex/ai/issues/11) | 후보 생성 완료 | 101,546개 후보 생성, 운영 승인 관계는 0건 |
| [#12 질병-품목 수요 매핑](https://github.com/DGU-TeamLex/ai/issues/12) | 후보 단계 | 수요코드 후보는 있으나 검수 승인과 1~4주 선행성 검증 없음 |
| [#14 규격 토큰 분리](https://github.com/DGU-TeamLex/ai/issues/14) | 대부분 구현 | 게이지·용량·포장수 분리와 충돌 게이트 구현. 명시된 거즈 회귀 fixture 보강 필요 |
| [#15 정규화 파이프라인 소유](https://github.com/DGU-TeamLex/ai/issues/15) | 핵심 구현 | 101,546개 전체 및 1,000개 샘플 재생성 가능. 팀 검수·병합 필요 |
| [#16 기준표 밖 분류](https://github.com/DGU-TeamLex/ai/issues/16) | 부분 구현 | 미확정 잔류 정책 구현. 공식 근거 사전과 외부 검증 커버리지 부족 |
| [#18 나프타 가격축](https://github.com/DGU-TeamLex/ai/issues/18) | 코드 완료, 운영 차단 | PP/PE/PVC 경로와 proxy 품질 구현. 승인 품목 관계와 나프타 운영 시계열 필요 |
| [#19 공급위험 전파 사슬](https://github.com/DGU-TeamLex/ai/issues/19) | 코드 완료, 운영 차단 | 위험 엔진·감사·경보 구현. 현재 승인 관계 0건으로 점수 0행 |
| [#20 z·LT 동적 상향](https://github.com/DGU-TeamLex/ai/issues/20) | 코드 완료, 보정 필요 | 동적 재고정책과 중복 적용 방지 구현. 실증 calibration 필요 |
| [#21 사건연구 백테스트](https://github.com/DGU-TeamLex/ai/issues/21) | 미완료 | 실제 사건창, 가격, 품절·소비 데이터로 1~4주 선행성 검증 필요 |
| [#22 뉴스 수집과 가중치 override](https://github.com/DGU-TeamLex/ai/issues/22) | 부분 구현 | GDELT와 YAML 가중치 override 존재. 이슈의 CSV 계약 및 운영 검수 절차 보강 필요 |
| [#23 SS/ROP·발주권고](https://github.com/DGU-TeamLex/ai/issues/23) | 핵심 구현 | 일 단위 SS/ROP와 API 구현. 실제 리드타임, backend 이관, 민감도 분석 필요 |

### 닫힌 이슈 확인사항

| 이슈 | 확인사항 |
|---|---|
| [#3 device 원자재 매핑](https://github.com/DGU-TeamLex/ai/issues/3) | raw_stock 기반 #11로 대체됐으므로 폐기 상태가 맞음 |
| [#13 rolling backtest](https://github.com/DGU-TeamLex/ai/issues/13) | 모델 비교 산출물은 존재. 재고 시뮬레이션과 외부 feature ablation 결과는 계속 추적 필요 |
| [#17 결측일 0 복원](https://github.com/DGU-TeamLex/ai/issues/17) | 일 단위 결측과 월 단위 관측 공백의 의미를 분리해 재확인해야 함 |

## 7. 기존 이슈에 추가할 완료 조건

새 이슈를 중복 생성하기 전에 아래 항목은 기존 이슈 본문이나 댓글에 추가하는 것이 좋다.

### #11 품목-원자재 매핑

- `review_status=approved`인 관계를 최소 우선품목 범위에서 생성한다.
- `reviewer`, `reviewed_at`, 근거 URL, 제품 수준 확인 여부를 필수화한다.
- 복합 제품은 단일 원자재 확정 대신 BOM 필요 상태를 유지한다.
- 승인 전에는 Module C 점수가 0인지 회귀 테스트한다.

### #18, #19 공급위험 경로

- `supply_risk_meta_code`, `event_supply_risk_code`, `demand_trigger_meta_code`를 물리적으로
  다른 컬럼으로 저장한다.
- 운영 provider가 `disabled`이거나 실패했을 때 sample 데이터로 자동 대체하지 않는다.
- 위험 점수와 함께 provider, series, proxy 품질, lag, mapping version을 저장한다.
- 품질 게이트 `PASS` 행만 안전재고 계산에 입력한다.

### #20, #23 재고정책

- `mean_daily_usage`, `daily_demand_stddev`, `lead_time_days`와 단위 계약을 필수화한다.
- 레벨 기반 SS/ROP와 연속형 Module C 버퍼 중 적용 정책 하나를 명시한다.
- 동일 정책 버전으로 재계산한 SS/ROP가 허용오차 1% 이내인지 검증한다.
- stale 입력과 품질 게이트 실패 시 기존 재고량을 덮어쓰지 않는다.

## 8. 새 GitHub 이슈 후보

아래 세 항목은 기존 이슈의 단순 세부 작업보다 데이터 계약과 배포 경계가 독립적이므로
새 이슈로 분리하는 것이 좋다.

### 신규 A: 공급위험·사건·수요 메타코드 축 분리와 legacy migration

완료 조건:

- 4,969개 현재 로컬 품목에서 사건 코드를 기준 공급코드 필드에서 제거한다.
- `API_IMPORT_DEPENDENCY_CN_IN`을 현재 정본 코드로 변환하고 변환 이력을 남긴다.
- 변환 전후 코드와 정책 버전을 감사 CSV로 저장한다.
- `SR018`, `SR019`가 0건인지 재실행으로 검증한다.

### 신규 B: 공급위험 품질 게이트 CI·backend 적재 연동

완료 조건:

- DB export에 운영 필수 필드와 단위 계약을 포함한다.
- `--operational-mode --require-release` 종료코드 2에서 적재를 중단한다.
- `BLOCK`은 quarantine 테이블, `REVIEW`는 수동 검토 큐에 저장한다.
- `PASS`만 `policy_version` 단위 트랜잭션으로 적재한다.
- 실패 시 기존 운영 재고값이 유지되는 통합 테스트를 추가한다.

### 신규 C: 국내과점 공급위험 코드의 품목별 근거 재설계

완료 조건:

- `DOMESTIC_OLIGOPOLY_CONCENTRATION` 322개 로컬 품목, 76개 대표품목을 전수 검토한다.
- 수액제 시장 근거를 수액세트 제조시장에 일괄 전이하지 않는다.
- 제조사 집중도, 대체 가능성, 수입원, 조달 리드타임 근거를 분리한다.
- 근거가 부족한 품목은 `NORMAL + needs_review`로 유지한다.
- 승인된 신규 코드 또는 기존 정본 코드로 재분류한 뒤 `SR003`이 0건인지 확인한다.

사건연구와 calibration은 기존 #21의 완료 조건을 확장하는 편이 중복을 줄인다.

## 9. 권장 진행 순서

1. 신규 A로 공급, 사건, 수요 코드 축을 분리한다.
2. 신규 C로 미매핑 국내과점 76개 대표품목을 검증한다.
3. #11에서 우선품목의 품목-원자재 관계를 승인한다.
4. #2와 #4의 운영 뉴스·가격 데이터를 연결한다.
5. 신규 B로 backend 적재 전 품질 게이트를 강제한다.
6. #20과 #23의 재고정책을 단일 방식으로 선택한다.
7. #21 사건연구로 가중치와 임계값을 보정한다.

운영 적용 기준은 단순히 코드가 실행되는지가 아니다. 승인된 관계, 실제 외부 데이터,
동일 정책 버전, 명시적 일 단위 입력, 품질 게이트 `PASS`가 동시에 충족돼야 한다.
