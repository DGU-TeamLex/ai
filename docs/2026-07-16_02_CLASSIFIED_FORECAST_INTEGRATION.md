# 세부유형·규격·단위별 재고 예측 연동 가이드

## 1. 목적

표준화 작업이 끝난 로컬 품목을 기존 사용량 예측에 즉시 연결하고, 다음 구조로 재고 수량을
출력한다.

| 업무 표현 | taxonomy 컬럼 | 예시 |
|---|---|---|
| 품목 | `standard_family_name` | 주사기 |
| 세부 유형 품목 | `standard_subtype_name` | 주사기(사용량 기준) |
| 세부 유형 | `normalized_specification` | 3mL |
| 단위 | `unit_code`, `unit_name` | EA, 개수 |

기존 모델은 `기관 + 부서 + 내부 물품코드`별 다음 달 사용량을 예측한다. 승인 분류가
들어오면 이 로컬 예측을 `품목 + 세부 유형 품목 + 세부 유형 + 단위`별로 합산한다.
따라서 기존 품목을 새로 분류했을 때 모델 재학습은 필요하지 않다.

## 2. 이 방식을 선택한 이유

- 기관과 부서의 실제 재고 경계를 유지한다.
- 분류가 진행되는 동안 승인된 품목부터 부분적으로 결과를 만들 수 있다.
- 미승인 명칭을 모델 학습 키로 사용하지 않는다.
- 서로 다른 단위나 규격의 재고를 잘못 합산하지 않는다.
- 분류 변경만으로 전체 모델을 다시 학습할 필요가 없다.

분류는 예측값을 새로 만드는 입력 feature가 아니라, 검증된 예측 결과의 집계 기준이다.
새로운 `raw_stock` 월이 추가되었거나 새 로컬 품목에 충분한 이력이 생긴 경우에는 전체
예측 파이프라인을 다시 실행해야 한다.

## 3. 입력 파일

### 3.1 승인 taxonomy

경로:

```text
data/mapping/item_family_taxonomy.csv
```

예측에 사용할 taxonomy 행은 `review_status=approved`여야 한다. 2026-07-16 분류 실행
기준으로 승인 taxonomy는 59행이며, 근거 게이트를 통과하지 못한 후보는 이 파일에
승인 행으로 기록하지 않는다.

taxonomy 참조키는 다음 다섯 컬럼의 조합이다.

```text
item_family_id
item_subtype_id
normalized_specification
unit_code
taxonomy_version
```

같은 참조키를 가진 승인 행은 한 개만 존재해야 한다. `item_group_id`, 표준 이름,
`is_forecastable`, 단위명은 승인 taxonomy에서 상속한다.

### 3.2 로컬 품목 승인 매핑

경로:

```text
data/mapping/item_forecast_classification_approved.csv
```

2026-07-16 분류 실행 기준으로 승인된 기관별 품목키 4,948행이 저장되어 있다. 한 행은
한 기관의 내부 물품코드와 승인 taxonomy 한 행을 연결한다.

| 컬럼 | 필수 조건 | 설명 |
|---|---|---|
| `institution_code` | 조건부 | 기관 코드 |
| `item_code` | 조건부 | 기관 내부 물품코드 |
| `local_item_key` | 필수 | `기관코드::물품코드`; 앞 두 컬럼으로 생성 가능 |
| `item_family_id` | 필수 | 승인 taxonomy family ID |
| `item_subtype_id` | 필수 | 승인 taxonomy subtype ID |
| `normalized_specification` | 필수 | 승인된 규격 |
| `unit_code` | 필수 | 승인된 수량 단위 코드 |
| `taxonomy_version` | 필수 | 참조 taxonomy 버전 |
| `review_status` | 필수 | 운영 반영은 `approved`만 허용 |
| `reviewer` | 승인 시 필수 | 검토자 ID |
| `reviewed_at` | 승인 시 필수 | 파싱 가능한 검토 시각 |
| `evidence_reference` | 승인 시 필수 | 공식 근거 또는 내부 판정 ID |
| `classification_version` | 승인 시 필수 | 로컬 분류 버전 |

`institution_id`, `local_item_code`라는 컬럼명이 들어오면 각각 `institution_code`,
`item_code`로 인식한다. 코드의 앞자리 0을 보존하기 위해 CSV 컬럼은 문자열로 관리한다.
원천 `item_code` 자체에 `::`가 포함될 수 있으므로 구분자 개수로 키를 분해하지 않는다.
기관·품목 코드 컬럼이 있으면 `institution_code + "::" + item_code`와 정확히 일치하는지
검증한다.

예시 형식:

```csv
institution_code,item_code,local_item_key,item_family_id,item_subtype_id,normalized_specification,unit_code,taxonomy_version,review_status,reviewer,reviewed_at,evidence_reference,classification_version
INST001,ITEM001,INST001::ITEM001,DISPOSABLE_SYRINGE,SYRINGE_USAGE_BASED,3mL,EA,v0.2,approved,user-01,2026-07-15T09:00:00+09:00,EVIDENCE-001,classification-v1
```

이 행은 형식 예시이며 실제 승인 데이터가 아니다.

## 4. 실행 방법

기존 로컬 예측에 새 승인 분류만 즉시 적용:

```bash
python -m src.modeling.classified_prediction
```

로컬 예측도 다시 만든 뒤 적용:

```bash
python -m src.modeling.classified_prediction --refresh-local-predictions
```

기본값은 부서 재고 경계를 유지한다. 기관 안에서 부서를 합산해야 하는 별도 업무 화면만
다음 옵션을 사용한다.

```bash
python -m src.modeling.classified_prediction --scope institution
```

전체 배치의 마지막 `src.modeling.prediction` 단계에서도 승인 분류 산출물을 자동 생성한다.

## 5. 출력

```text
outputs/stock_predictions_by_subtype.csv
outputs/stock_predictions_by_subtype_quality.json
```

CSV의 핵심 수량은 모두 해당 행의 `unit_code`, `unit_name` 단위다.

| 컬럼 | 의미 |
|---|---|
| `predicted_usage` | 다음 달 예상 사용량 |
| `current_stock` | 기준월 말 현재고 합계 |
| `protection_period_demand` | 검토주기와 리드타임 동안의 예상 사용량 합계 |
| `safety_stock` | 로컬 시계열별 안전재고 합계 |
| `base_stock` | 보호기간 수요와 안전재고를 합한 기본 재고량 |
| `demand_risk_buffer` | 수요 위험 버퍼 합계 |
| `supply_risk_buffer` | 공급 위험 버퍼 합계 |
| `material_risk_buffer` | 원자재 위험 버퍼 합계 |
| `risk_buffer` | 세 위험 버퍼 합계 |
| `target_stock` | 위험조정 목표 재고량 합계 |
| `recommended_stock` | 기존 연동 호환용 `target_stock` 별칭 |
| `inventory_position` | 현재고와 입고예정에서 미납을 뺀 수량 합계 |
| `recommended_order` | 로컬 시계열별 발주 권고량 합계 |
| `mapped_source_series_count` | 승인 원자재 매핑이 적용된 로컬 시계열 수 |
| `source_series_count` | 합산된 기관·부서·로컬 품목 시계열 수 |
| `source_local_item_count` | 합산된 로컬 품목코드 수 |

`recommended_order`는 로컬 품목별 부족량을 먼저 계산한 뒤 합산한다. 이름이 같은 품목끼리
재고를 자유롭게 대체할 수 있다고 가정하지 않는다.

## 6. 합산 및 제외 규칙

1. 분류와 taxonomy가 모두 `approved`인 행만 사용한다.
2. `item_group_id`, subtype, 규격, 단위가 모두 같은 행만 합산한다.
3. `unit_code`가 다르면 절대 합산하거나 자동 환산하지 않는다.
4. 포장 단위 환산은 검증된 환산계수가 별도로 생기기 전까지 수행하지 않는다.
5. taxonomy의 `is_forecastable=f`인 행은 예측 출력에서 제외한다.
6. 한 `local_item_key`에 승인 분류가 둘 이상이면 전체 실행을 실패시킨다.
7. 승인 매핑이 미승인 또는 존재하지 않는 taxonomy를 참조하면 전체 실행을 실패시킨다.
8. 승인 매핑이 없는 로컬 예측은 원본 예측에는 남지만 세부유형 산출물에는 포함하지 않는다.

사용자가 제시한 의료폐기물 전용용기는 현재 `WASTE`, `is_forecastable=f`이므로 기본 예측
출력에서 제외된다. 이를 예측해야 한다면 DB 정책과 taxonomy를 함께 검토하여 승인된 정책
변경으로 처리해야 하며, 코드에서 임의로 우회하지 않는다.

## 7. API 조회

```text
GET /predictions/by-subtype
```

필수 query:

```text
yyyymm=2026-07
institution_code=INST001
```

선택 query:

```text
department
item_group_id
item_family_id
item_subtype_id
normalized_specification
unit_code
limit
```

## 8. 즉시 예측의 한계

- 분류가 새로 승인된 기존 로컬 품목: 기존 예측에 즉시 결합 가능
- 신규 원본 월: 전처리, feature 생성, 예측 재실행 필요
- 과거 이력이 부족한 신규 품목: 분류가 있어도 안정적인 로컬 예측을 만들 수 없음
- 오래된 원본 데이터: 세부유형으로 합쳐도 `is_stale_data` 상태는 그대로 유지
- 미승인 품목: 로컬 예측은 존재할 수 있지만 세부유형 집계에는 포함하지 않음

품질 JSON에서 승인 건수, 매칭 건수, 예측 사용량 커버리지, 예측 제외 건수 및 최종 출력
건수를 확인한다. 현재 승인 데이터가 없으면 상태는
`awaiting_approved_classifications`로 기록된다.

2026-07-16 실행 결과는 승인 매핑 4,948행, 매칭 예측 2,396행, 예측 사용량 커버리지
1.74%, 세부유형 출력 2,348행이다. 상세 분류 기준과 검토 큐는
`docs/2026-07-16_04_ITEM_CLASSIFICATION_V1_RESULT.md`를 참고한다.
