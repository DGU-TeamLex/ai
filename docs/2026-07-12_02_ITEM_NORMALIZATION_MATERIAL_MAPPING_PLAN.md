# 품목 전체 정규화 및 원자재 매핑 계획

작성일: 2026-07-12  
대상: `raw_stock/*.DAT`, `regulazation/물품재고_정규화완료.parquet`, DB `item_groups`

## 1. 목표

정규화의 목표는 이름이 비슷한 모든 행을 하나로 합치는 것이 아니다. 다음 세 가지 식별 수준을 분리해 재고 예측과 리스크 매핑에 필요한 공통 기준을 만드는 것이다.

| 식별자 | 의미 | 주요 사용처 |
|---|---|---|
| `local_item_key` | 한 기관이 사용하는 내부 물품코드 | 기관·부서별 재고 및 수요 예측 |
| `canonical_item_id` | 제조사, 모델, 함량, 규격까지 구분한 표준 제품 | 정확한 제품별 원자재 및 공급망 매핑 |
| `item_family_id` | 같은 기능·성분·용도의 품목군 | 질병 매핑, 공통 원자재 매핑, 통계 집계 |

예측 시계열은 `local_item_key`를 유지한다. 서로 다른 기관의 재고량을 이름이 같다는 이유로 합치지 않는다. 질병과 원자재 리스크는 `item_family_id`에 기본 매핑하고, 규격이나 제조사에 따라 차이가 확인되면 `canonical_item_id`에서 덮어쓴다.

## 2. 핵심 원칙

1. 원본 물품명과 원본 수치를 절대 덮어쓰지 않는다.
2. 운영 태그와 제품 속성을 분리한다.
3. 함량, 제형, 제조사, 호환 모델, 규격이 충돌하면 자동 병합하지 않는다.
4. 공식 코드도 코드 체계가 확인된 경우에만 확정 키로 사용한다.
5. 규칙, 사전, 모델, 결과에 모두 버전을 남긴다.
6. LLM과 유사도는 후보를 만들고, 최종 승인은 결정적 규칙 또는 사람 검수로 한다.
7. 품목 분류 신뢰도, 표준 제품 매칭 신뢰도, 원자재 매핑 신뢰도를 서로 분리한다.

## 3. 처리 대상 축소

원본은 16,265,602행이지만 정규화는 전체 fact를 매번 처리할 필요가 없다. 먼저 다음 키로 별칭 후보를 추출한다.

```text
institution_id
local_item_code
raw_item_name
first_seen_date
last_seen_date
usage_sum
```

현재 정규화본 기준으로 기관·원본 코드·표준 ID 연결은 약 40만 개 수준이다. 이 별칭 차원에서 정규화와 검수를 수행한 뒤 결과를 원본 재고 fact에 조인한다.

## 4. 정규화 단계

### 4.1 1단계: 표면 정리

의미를 바꾸지 않는 변환만 수행한다.

- Unicode 및 공백 정규화
- 줄바꿈, 중복 공백 제거
- 영문 대소문자 표준화용 비교 키 생성
- 단위 표기 통합: `ml`, `mL`, `㎖` 등
- 수량 표기 통합: `50T`, `50정`, `50 tablets` 등
- 원본 괄호와 구두점은 보존하고 파싱용 토큰만 별도로 생성

출력 예:

```text
raw_item_name: "    혈당 스틱(케어센스N) 50매"
cleaned_name: "혈당 스틱(케어센스N) 50매"
comparison_name: "혈당스틱 케어센스n 50매"
```

### 4.2 2단계: 운영 태그 분리

제품 정체성과 무관한 사업명, 장소, 과금 및 배포 목적을 별도 컬럼으로 이동한다.

운영 태그 예:

```text
방문, 방문건강관리, 재활, 내당, 본소, 지소
무료, 유료, 비급여, 택배용
홍보, 판촉, 기념품, 지원사업
목동, 신월 등 기관 내부 장소 표현
1., 2-1., 가. 같은 목록 번호
```

운영 태그 제거는 승인된 사전에 있는 표현만 수행한다. 괄호 안 내용이 제조사, 성분, 모델, 규격일 수 있으므로 모든 괄호를 일괄 삭제하지 않는다.

### 4.3 3단계: 동의어 및 품목군 표준화

표현이 달라도 기능적으로 같은 품목군을 `item_family_id`로 묶는다.

```text
혈당스틱, 혈당검사스틱, 혈당검사지, 혈당측정검사지
-> item_family_id: BLOOD_GLUCOSE_TEST_STRIP
-> standard_family_name: 혈당검사지
-> item_group_id: LAB_REAGENT
```

동의어 사전은 다음 형태로 버전 관리한다.

```csv
alias,standard_family_name,item_family_id,item_group_id,rule_version
혈당스틱,혈당검사지,BLOOD_GLUCOSE_TEST_STRIP,LAB_REAGENT,v1
혈당검사스틱,혈당검사지,BLOOD_GLUCOSE_TEST_STRIP,LAB_REAGENT,v1
```

### 4.4 4단계: 제품 속성 추출

공통 속성:

```text
brand_name
manufacturer
model_name
specification
pack_quantity
unit
country_of_origin
```

의약품 전용 속성:

```text
active_ingredient
ingredient_amount
strength
dosage_form
route
release_type
official_product_code
official_code_system
```

의료소모품·시약 전용 속성:

```text
device_or_assay_type
compatible_model
size
volume
gauge
sterility
single_use
assay_target
```

### 4.5 5단계: DB item_group 분류

DB `item_groups`를 기준으로 분류하되 다음 우선순위를 사용한다.

```text
수동 override
-> PROMO/RENTAL/FUEL/WASTE 운영 목적
-> 검증된 공식 코드
-> LAB_REAGENT/DISINFECT/MED_SUPPLY 명시 패턴
-> MED_ORAL/MED_INJECT/MED_TOPICAL 제형
-> KM_EXTRACT/KM_HERB/SUPPLEMENT
-> UNCLASSIFIED
```

`주사기`, `주사침`, `주사바늘`은 `MED_SUPPLY`이며 `MED_INJECT`가 아니다. `홍보물품-파스`는 제품이 파스여도 운영 목적 기준으로 `PROMO`다.

### 4.6 6단계: 표준 제품 후보 매칭

후보 생성 우선순위:

| 우선순위 | 매칭 방식 | 자동 승인 기준 예시 |
|---|---|---|
| 1 | 검증된 공식 코드 완전 일치 | 코드 체계와 유효기간이 확인된 경우 |
| 2 | 기존 승인 local alias | 유효기간 내 동일 기관·내부 코드 |
| 3 | 이름+제형+함량+규격+제조사 완전 일치 | 핵심 속성 충돌이 없는 경우 |
| 4 | 표준명+제품 속성 부분 일치 | 검수 후보 |
| 5 | 유사도 또는 LLM 후보 | 검수 후보 |

핵심 속성이 빠진 일반명은 품목군까지는 자동 승인할 수 있지만 표준 제품 병합은 보류한다.

## 5. 유형별 정규화 예시

### 5.1 혈당검사지

```text
원본명: [방문]혈당검사스틱
운영 태그: 방문
표준 품목군명: 혈당검사지
item_family_id: BLOOD_GLUCOSE_TEST_STRIP
item_group_id: LAB_REAGENT
canonical_item_id: 미확정(제조사/호환 모델 없음)
```

```text
원본명: 혈당스틱(아큐첵 퍼포마) 50매
표준 품목군명: 혈당검사지
brand_model: 아큐첵 퍼포마
pack_quantity: 50
unit: 매
canonical_item_id: 아큐첵 퍼포마용 혈당검사지 50매 제품
```

두 항목은 같은 품목군이지만, 호환 모델을 확인하기 전에는 같은 표준 제품 ID로 합치지 않는다.

### 5.2 의약품

```text
원본명: 아마릴정2mg(한독약품)-1정
item_group_id: MED_ORAL
brand_name: 아마릴정
active_ingredient: 글리메피리드
strength: 2mg
dosage_form: 정제
route: 경구
manufacturer: 한독
dispensing_unit: 정
```

의약품의 `canonical_item_id`는 가능하면 검증된 공식 제품 코드를 사용한다. 질병 매핑은 성분·제형 기반 품목군에, 원자재 매핑은 유효성분과 근거가 있는 첨가제·포장재에 연결한다.

### 5.3 의료소모품

```text
원본명: 일회용 주사기 10mL 23G
item_group_id: MED_SUPPLY
item_family_id: DISPOSABLE_SYRINGE
volume: 10mL
gauge: 23G
single_use: true
```

주사기 공통 원자재는 품목군 기본값으로 후보를 만들고, 특정 제품의 재질 정보가 확인되면 제품 수준에서 덮어쓴다.

## 6. ID와 테이블 구조

### 6.1 item_alias

```text
institution_id
local_item_code
raw_item_name
cleaned_name
operational_tags
local_item_key
canonical_item_id
item_family_id
item_group_id
classification_confidence
match_confidence
review_status
effective_from
effective_to
normalization_version
```

### 6.2 item_master

```text
canonical_item_id
canonical_name
item_family_id
item_group_id
brand_name
manufacturer
model_name
active_ingredient
strength
dosage_form
specification
pack_quantity
unit
official_product_code
official_code_system
review_status
master_version
```

### 6.3 item_family_master

```text
item_family_id
standard_family_name
item_group_id
description
is_disease_mapping_target
is_material_mapping_target
review_status
family_version
```

### 6.4 normalization_review_queue

```text
local_item_key
raw_item_name
candidate_canonical_item_id
candidate_item_family_id
conflict_fields
confidence
usage_rank
review_reason
review_status
```

## 7. 원자재 매핑

### 7.1 원자재 범위 분리

원자재를 하나의 평면 목록으로 관리하지 않고 관계 유형을 구분한다.

```text
active_ingredient: 의약품 유효성분
excipient: 의약품 첨가제
direct_component: 의료소모품 본체 재질
primary_packaging: 바이알, 앰플, 블리스터 등 1차 포장재
secondary_packaging: 외부 포장재
upstream_material: 나프타 등 상위 원료
compatible_consumable: 장비와 전용 소모품 관계
```

### 7.2 매핑 계층

```text
item_family 기본 매핑
-> canonical item 제품별 override
-> 제조사 근거가 있으면 제품 매핑 우선
```

예시:

```text
혈당검사지
-> assay chemical: 제조사 자료가 있을 때만 제품별 연결
-> plastic substrate / electrode material: 근거가 있을 때 연결
-> compatible meter model: 제품별 연결

일회용 주사기
-> polypropylene: barrel/plunger 후보
-> stainless steel: needle 후보
-> synthetic rubber: gasket 후보

아마릴정 2mg
-> glimepiride: active_ingredient
-> aluminum/PVC blister: 실제 포장 근거가 있을 때만 연결
```

### 7.3 item_material_mapping

```text
mapping_id
mapping_level
item_family_id
canonical_item_id
material_id
relation_type
usage_part
dependency_weight
substitutability_weight
evidence_type
evidence_reference
mapping_confidence
review_status
mapping_version
```

`mapping_level`은 `family_default` 또는 `item_override`다. LLM이 추론한 재질은 `candidate` 상태로 저장하고 근거가 확인되기 전에는 운영 점수에 사용하지 않는다.

## 8. 리스크 연결

두 경로는 독립적으로 계산한다.

```text
뉴스 -> 질병 -> item_family -> local item
뉴스/가격 -> 원자재 -> item family 또는 canonical item -> local item
```

동일 뉴스가 질병과 원자재 양쪽에 잡혀도 점수를 즉시 합산하지 않는다. 기사 ID와 경로를 보존하고 최종 리스크 결합 단계에서 중복 영향을 제한한다.

## 9. 품질 기준

| 항목 | 통과 기준 |
|---|---|
| 원본 보존 | 원본명과 원본 수치 변경 0건 |
| 결정성 | 동일 입력·버전에서 동일 결과 |
| 운영 태그 제거 | 승인 사전에 있는 태그만 제거 |
| 품목군 충돌 | 하나의 별칭에 복수 승인 품목군 0건 |
| 제품 병합 충돌 | 함량·제형·모델·규격 충돌 자동 병합 0건 |
| 공식 코드 | 코드 체계와 출처가 없는 자동 승인 0건 |
| 자동 매칭 | 정답셋 precision 98% 이상 |
| 원자재 매핑 | 근거·관계 유형·버전 없는 운영 매핑 0건 |
| 운영 대상 | `is_forecastable = true`만 모델 입력 |

정확도는 전체 이름 개수뿐 아니라 사용량 가중 기준으로 측정한다. 사용량 상위 품목을 먼저 검수해 운영 데이터의 80~90%를 우선 커버한다.

## 10. 구현 순서

1. 원본에서 기관별 품목 별칭 테이블을 추출한다.
2. 운영 태그, 동의어, 단위, 제형 규칙을 YAML/CSV로 작성한다.
3. 규칙 기반 이름 파서와 item_group 분류기를 구현한다.
4. 혈당검사지, 의약품, 주사기 등 대표 정답셋을 만든다.
5. 공식 코드와 제품 속성을 이용해 canonical 후보를 생성한다.
6. 충돌과 저신뢰 항목을 review queue로 출력한다.
7. 사용량 상위 후보를 사람이 승인한다.
8. item family 원자재 seed mapping을 구축한다.
9. 제품별 근거가 있는 material override를 추가한다.
10. 승인된 alias와 매핑만 재고 fact 및 리스크 계산에 조인한다.

첫 구현 범위는 전체를 한 번에 자동 확정하는 것이 아니라, 규칙 기반 고신뢰 결과와 검수 대상을 안정적으로 분리하는 데 둔다.
