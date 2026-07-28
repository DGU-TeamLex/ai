# 전체 품목·원자재 후보 일괄 승인 결과

작성일: 2026-07-28

## 1. 적용 목적

기관별 전체 품목과 생성된 원자재 후보를 검토 워크플로우의 승인 상태로 전환했다.
단, 일괄 승인은 후보 수용을 뜻하며 외부 근거로 사실이 검증됐다는 뜻은 아니다.

다음 세 상태를 분리해 보존한다.

```text
review_status=approved
  모든 후보를 검토 워크플로우에서 수용

operational_eligible=true
  분류 필드 또는 원자재 위험 입력 조건을 충족해 계산에 사용 가능

automatic_order_eligible=true
  기존 외부 근거 승인 품목만 해당
```

## 2. 승인 정책

정책 파일은 `data/mapping/item_bulk_approval_policy.json`이다.

- 기존 외부 근거 승인 4,969건은 값과 근거를 그대로 보존한다.
- 세부유형·규격·단위가 완전한 후보는 사용자 승인으로 예측 집계에 활성화한다.
- 필드가 불완전한 품목은 로컬 품목별 고유 `BULK_PENDING_*` ID를 부여한다.
- 불완전 품목끼리 같은 표준품목으로 병합하지 않는다.
- 단위가 확인되지 않은 품목은 세부유형 예측에서 제외한다.
- 원자재 후보는 전건 승인하되 운영 위험 계산은 별도 게이트를 사용한다.
- 기존 외부 근거 승인 외 품목은 자동발주 적격으로 바꾸지 않는다.

## 3. 전체 승인 결과

### 3.1 품목 분류

| 지표 | 결과 |
|---|---:|
| 기관별 로컬 품목 | 409,519 |
| 승인 품목 | 409,519 |
| 승인 커버리지 | 100% |
| 기존 외부 근거 승인 | 4,969 |
| 필드 완전 사용자 승인 | 59,348 |
| 필드 미확정 사용자 승인 | 345,202 |
| 세부유형 예측 사용 가능 | 64,315 |
| 자동발주 적격 유지 | 4,969 |
| 승인 taxonomy 행 | 346,286 |

### 3.2 원자재 후보

| 지표 | 결과 |
|---|---:|
| 로컬 품목-원자재 후보 | 623,413 |
| 승인 후보 | 623,413 |
| 부서 재고키로 확장한 승인 매핑 | 634,811 |
| 승인 재고품목 | 416,128 |
| 운영 위험 계산 적격 매핑 | 32,205 |
| 운영 위험 계산 적격 재고품목 | 20,412 |
| 미식별·혼합 원자재 매핑 | 78,139 |

운영 적격은 원자재 식별, 근거 존재, 시장가격 경로, 분류 충돌 없음,
공급정책 검토 불필요를 모두 요구한다.

## 4. 적용 전후 변화

| 항목 | 적용 전 | 적용 후 |
|---|---:|---:|
| 승인 로컬 분류 | 4,969 | 409,519 |
| 재고 상태 분류 커버리지 | 1.24% | 100% |
| 세부유형 예측 출력 | 2,359 | 35,379 |
| 세부유형 예측 사용량 커버리지 | 1.742% | 42.551% |
| 최종 긴급부족 후보 | 17 | 757 |
| Module C 공급품질 PASS | 0 | 12,996 |
| Module C 공급품질 REVIEW | 4,647 | 225,396 |
| Module C 공급품질 BLOCK | 322 | 171,127 |
| 수출입 위험 적용 재고품목 | 2,297 | 15,000 |

긴급부족 후보 증가는 정확도 향상이 아니다. 이전에는 승인 매핑이 없어 검토 대상에서
빠졌던 품목이 포함된 결과이며 실제 결품 라벨로 precision과 recall을 검증해야 한다.

## 5. 모델 재평가

| 모델 | 검증 WAPE | 판정 |
|---|---:|---|
| LightGBM L1 사용량 전용 | 40.19% | champion 유지 |
| LightGBM Tweedie Module C | 40.82% | challenger |
| LightGBM Tweedie 사용량 전용 | 40.85% | challenger |
| 최근 3개월 평균 | 44.52% | baseline |

일괄 승인과 확대된 외부 위험을 반영해도 검증 WAPE 기준 champion은 바뀌지 않았다.
Module C는 정책 초기값이며 품절·납기 지연 라벨로 보정되지 않았다.

## 6. 외부 데이터 재실행

| 산출물 | 결과 |
|---|---:|
| 원자재 가격 위험 | 489,864행 |
| 원자재 가격 감사 경로 | 772,896행 |
| 수출입 위험 | 630,000행 |
| 수출입 위험 재고품목 | 15,000개 |
| Module C 위험 점수 | 759,864행 |
| Module C 감사 경로 | 3,799,320행 |

수출입은 현재 승인된 `POLYPROPYLENE_PP -> HS 3902100000` 한 경로만 사용한다.
다른 원자재는 별도 HS 근거 승인 전까지 수출입 위험이 적용되지 않는다.

GDELT는 2024년 2월 요청부터 HTTP 429를 반복해 전체 뉴스 이력을 완성하지 못했다.
이번 재학습에서는 뉴스 feature가 비영값을 갖지 않아 뉴스 모델 두 개가 제외됐다.
뉴스 수집이 완료되기 전까지 이번 결과를 뉴스 효과 검증으로 해석하지 않는다.

## 7. 운영 게이트

`outputs/module_c_supply_risk_quality_report.json` 결과:

```text
PASS    12,996
REVIEW 225,396
BLOCK  171,127
batch_release_allowed=false
operational_mode=false
```

전건 승인 후에도 Module C 자동 릴리스는 활성화하지 않았다. `SR003` 미매핑 코드,
`SR018` 레거시 별칭, `SR019` 비공급 이벤트 혼입을 해결하고 결과 라벨로 보정하기
전에는 권고·검토용으로만 사용한다.

## 8. 파일 위치

| 파일 | 설명 |
|---|---|
| `data/processed/item_forecast_classification_bulk_approved.parquet` | 전체 품목 승인 |
| `data/processed/item_family_taxonomy_bulk_approved.parquet` | 전체 승인 taxonomy |
| `data/processed/stock_item_material_mapping_bulk_approved.parquet` | 전체 원자재 승인 |
| `data/processed/item_bulk_approval_active.json` | 활성 승인 경로 |
| `outputs/item_bulk_approval_report.json` | 승인 건수·품질 게이트 |
| `data/sample/item_bulk_classification_approval_sample_1000.csv` | 품목 승인 표본 |
| `data/sample/item_bulk_material_approval_sample_1000.csv` | 원자재 승인 표본 |
| `outputs/stock_predictions_by_subtype.csv` | 승인 분류 기반 세부유형 예측 |
| `outputs/stock_inventory_status.csv` | 전체 승인 기반 재고 상태 |

생성 데이터는 Git에 포함하지 않는다. 동일 결과는 다음 명령으로 다시 만든다.

```bash
python -m src.item_bulk_approval --apply --sample-size 1000
```

## 9. 주의사항

- 100% 승인은 100% 정확도가 아니다.
- 345,202건은 세부 taxonomy 필드가 미확정이다.
- 원자재 승인 대부분은 제품별 BOM 검증이 아니라 후보 수용이다.
- 자동발주 적격은 기존 근거 승인 4,969건으로 유지된다.
- 현재 원장은 2025년 12월까지이므로 2026년 운영 발주에 사용할 수 없다.
- CSV 감사 산출물은 최대 수백 MB이므로 운영 배치는 Parquet 전환을 검토해야 한다.
