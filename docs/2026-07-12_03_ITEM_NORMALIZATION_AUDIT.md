# 품목 정규화 v0.2 감사 결과

> 상태: 이 감사에서 확인된 규칙 오류는 후속 버전에서 수정되었다. 최신 전체 생성 및 검증 결과는 `docs/2026-07-16_05_ITEM_NORMALIZATION_V0.4_RESULT.md`를 기준으로 한다.

작성일: 2026-07-12  
검토 대상: `data/sample/raw_stock_item_normalization_sample_1000.csv`  
규칙 버전: `item-normalization-v0.2`

## 1. 결론

현재 파일은 **정규화 후보 및 규칙 검수용으로는 유효**하지만, DB의 최종 품목 마스터나 원자재 매핑 입력으로 사용하기에는 아직 부족하다.

- 원본 기관·코드·이름과 사용량 집계 정보는 검수 가능하게 보존됐다.
- 혈당검사지, 란셋, 주사기 같은 일부 명시 품목군은 정상적으로 통합된다.
- 모든 행이 `needs_review`이므로 승인 데이터로 오인할 위험은 제한되어 있다.
- 그러나 부분문자열 기반 오분류, 운영 목적과 제품 유형의 혼합, 낮은 품목군·규격 커버리지 문제가 남아 있다.

따라서 v0.2는 DB 적재 전 단계의 `candidate`로 동결하고, 아래 P0/P1 항목을 해결한 v0.3에서 정답셋 평가를 수행해야 한다.

## 2. 계량 결과

| 항목 | 결과 |
|---|---:|
| 표본 행 | 1,000 |
| 컬럼 | 34 |
| 기관·코드·원본명 중복 | 0 |
| `UNCLASSIFIED` | 320건, 32.0% |
| `item_family_id_candidate` 존재 | 99건, 9.9% |
| `item_subtype_id_candidate` 존재 | 99건, 9.9% |
| `canonical_item_id_candidate` 존재 | 0건, 0.0% |
| 표준 규격 후보 존재 | 5건, 0.5% |
| 표준 단위 후보 존재 | 71건, 7.1% |
| 원자재 후보 존재 | 1건, 0.1% |
| 운영 태그 존재 | 61건, 6.1% |
| 검수 상태 | 1,000건 모두 `needs_review` |

`UNCLASSIFIED` 320건의 사용량 합계는 약 12,875,478.5다. 미분류가 단순한 long-tail이 아니며, 사용량이 큰 의약품·영양제·의료소모품도 포함한다.

사용자가 제시한 8개 신규 품목군 중 현재 1,000건 표본에 실제 포함된 것은 주사기, 수액세트, 의료폐기물 전용용기 3개뿐이다. 현재 표본은 DB 그룹별 층화 표본이므로 신규 taxonomy 규칙별 검증 표본으로는 충분하지 않다.

## 3. 주요 발견사항

### P0. 부분문자열 오분류

단어 경계를 확인하지 않는 정규식 때문에 명백한 오분류가 발생한다.

| 원본명 | 현재 결과 | 올바른 후보 | 원인 |
|---|---|---|---|
| `트렌탈정400mg` | `RENTAL` | `MED_ORAL` | 제품명의 `렌탈` 부분문자열 탐지 |
| `트렌탈400서방정` | `RENTAL` | `MED_ORAL` | 동일 |
| `감염-행주` | `MED_INJECT` | 비의약품 또는 `UNCLASSIFIED` | `행주`의 마지막 `주`를 주사제로 탐지 |
| `[비급여용]삐콤헥사주사` | 제품명에 `용]` 잔존 | 태그 제거 후 의약품명 | `비급여`만 제거하고 `비급여용` 미처리 |

정규식은 단순 포함 여부가 아니라 토큰, 제형 접미사, 주변 문자 및 예외사전을 함께 확인해야 한다.

### P0. 제품 유형과 운영 목적 혼합

현재 `item_group_id_candidate` 하나에 제품의 본질적 유형과 운영 목적을 모두 넣는다.

```text
혈당검사지(대여)
item_family_id = BLOOD_GLUCOSE_TEST_STRIP
item_group_id = RENTAL
```

혈당검사지는 여전히 `LAB_REAGENT`이며, `대여`는 혈당측정기 대여사업과 관련된 운영 태그일 가능성이 높다. 란셋·알코올솜에도 같은 문제가 있다.

권장 분리:

```text
intrinsic_item_group_id: LAB_REAGENT
operational_class: RENTAL_PROGRAM
is_forecastable_override: null 또는 정책값
```

실제 유축기·혈압계 같은 대여 자산만 DB `RENTAL`로 확정하고, 대여사업에서 사용하는 소모품은 본래 제품 그룹을 유지한다.

### P1. 의약품 및 한방 제형 규칙 부족

다음 표현이 누락되거나 우선순위 때문에 잘못 분류된다.

- `레일라정_...`: `_` 뒤 제형 경계를 인식하지 못해 `KM_HERB`
- `플라스타`, `카타플라스마`, `습포제`: 외용제가 아닌 `KM_HERB`
- `약침`: 투여 형태를 반영하지 못하고 `KM_HERB`
- `캅셀`, `패취`: 과거 표기라 제형 규칙에서 누락
- `삐콤`, `폴산`, `페니라민`: 제품명만으로 제형을 알 수 없어 `UNCLASSIFIED`

의약품은 이름 정규식만으로 완성할 수 없다. 검증된 공식 코드와 의약품 마스터를 이용해 성분, 함량, 제형, 투여경로, 제조사를 가져와야 한다.

### P1. 영양제와 의약품 구분 오류

`오메가3`라는 성분 표현만으로 `SUPPLEMENT`를 결정해 처방 의약품인 `메가트리연질캡슐(오메가3산에틸에스테르90)`도 영양제로 분류됐다.

- `건강기능식품`, `건기식`, 명시적 `영양제`는 강한 SUPPLEMENT 근거로 사용한다.
- 성분명만 있는 경우 공식 제품 구분을 우선한다.
- 의약품 제형과 공식 코드가 확인되면 MED 그룹이 우선한다.

### P1. 표준명 커버리지 부족

`item_family_id`가 없는 901건에서는 `standard_family_name_candidate`에 정리되지 않은 `product_name_candidate`를 그대로 복사한다. 컬럼 이름은 표준명처럼 보이지만 실제로는 아직 표준화되지 않은 이름이다.

해당 행은 다음 중 하나로 표현해야 한다.

```text
standard_family_name_candidate = null
unresolved_product_name = 정리된 제품명
```

표준 마스터에 연결된 경우에만 standard/canonical 필드를 채워야 한다.

### P1. 동의어와 영문 표현 부족

표본에서 다음 항목이 `UNCLASSIFIED`로 남았다.

- `혈당 스트립`, `당뇨 스틱`
- `Uno Lancet`
- `폴리글러브`
- `알코올솜`
- `GPT / ALT`, `Cfas Lipids`, `Precicontrol Tumor`
- `플라스타`, `패취`, `메디폼`, `FIXROLLTAPE`

한국어 띄어쓰기뿐 아니라 영문 상품군, 과거 의약품 제형 표기, 검사실 약어 사전이 필요하다.

### P1. 원자재 매핑 준비 미완료

1,000건 중 원자재 후보가 있는 행은 1건이며, `canonical_item_id`는 전부 비어 있다. 현재 단계에서 이름 기반으로 원자재를 대량 추론하면 서로 다른 규격·제조사 제품에 같은 재질을 잘못 부여할 가능성이 높다.

원자재 매핑 시작 조건:

1. 승인된 `item_family_id`
2. 제품 차이가 중요한 경우 승인된 `canonical_item_id`
3. 관계 유형과 근거가 포함된 `item_material_mapping`
4. family 기본 매핑과 item override 분리

## 4. 정상 동작한 부분

- 전체 raw_stock 16,265,602행에서 409,519개 기관별 별칭을 분리했다.
- 표본은 기관·코드·원본명 기준 중복이 없다.
- 동일 혈당스틱 표현은 `BLOOD_GLUCOSE_TEST_STRIP / 혈당검사지 / LAB_REAGENT`로 연결된다.
- 모델명·괄호 내용은 삭제하지 않고 검수 정보로 보존한다.
- `3cc -> 3mL`, `22G`, `15cm x 200m` 등 세부 규격 파서는 단위 테스트를 통과했다.
- `멸균포장 거즈`와 포장재 자체를 구분하는 회귀 테스트가 있다.
- 샘플링 결과는 동일 입력에서 재현 가능하다.

## 5. 권장 작업 순서

### 1단계: v0.3 규칙 오류 수정

- `트렌탈`, `행주`, `[비급여용]` 회귀 테스트 추가
- 의약품 제형에 `캅셀`, `플라스타`, `카타플라스마`, `습포제`, `패취`, `약침` 추가
- SUPPLEMENT 규칙에서 성분명 단독 판정 제거
- 운영 태그 파서를 prefix/suffix 사전 기반으로 확장
- family가 확인된 소모품은 `대여` 문자열만으로 RENTAL 처리하지 않기

### 2단계: 분류 컬럼 분리

```text
intrinsic_item_group_id
operational_class
is_forecastable_default
is_forecastable_override
effective_is_forecastable
```

DB가 현재 단일 `item_group_id`만 허용하면, intrinsic 그룹을 저장하고 운영 목적·예측 예외는 별도 매핑 테이블로 관리하는 것이 안전하다.

### 3단계: 검수용 정답셋 구축

현재 1,000건 CSV에 다음 승인 컬럼을 추가해 사람이 정답을 입력한다.

```text
approved_item_group_id
approved_item_family_id
approved_item_subtype_id
approved_specification
approved_unit
reviewer
reviewed_at
review_note
```

우선 검수 대상:

1. 사용량 상위 `UNCLASSIFIED` 200건
2. 의약품 200건
3. DB 14개 그룹별 20건 이상
4. 신규 taxonomy 8개 family별 20건 이상
5. 현재 규칙 충돌 및 저신뢰 항목

### 4단계: 공식 마스터 연결

- 의약품: 공식 제품코드, 성분, 함량, 제형, 투여경로, 제조사
- 시약: 검사 대상, 키트/시약 유형, 호환 장비
- 의료소모품: 규격, 호환 모델, 멸균 여부, 재질 근거

현재 `공식코드`라는 라벨만으로는 코드 체계를 알 수 없으므로 `official_code_system`과 출처 버전을 함께 저장한다.

### 5단계: 표본 방식 보완

현재 그룹별 층화 표본과 별도로 다음 CSV를 만든다.

- `top_usage_unclassified.csv`
- `taxonomy_family_sample.csv`
- `rule_conflict_sample.csv`
- `drug_code_validation_sample.csv`

신규 taxonomy 8개 family가 표본에 반드시 포함되도록 family별 quota를 둔다.

### 6단계: 전체 별칭 후보 생성 및 품질 게이트

1,000건 정답셋에서 자동 분류 precision 98% 이상을 달성한 후 409,519개 전체 별칭 후보를 생성한다.

필수 보고서:

- 행 수 및 조인 누락률
- 그룹·family·canonical 매칭률
- 사용량 가중 미분류율
- 동일 이름 다중 그룹 충돌
- 핵심 속성 충돌 병합 수
- 운영 태그 제거 전후 비교
- 원자재 매핑 근거 및 승인율

### 7단계: 원자재 매핑

승인된 family부터 수동 seed를 만든다.

```text
item_family 기본 원자재 후보
-> 근거 검수
-> canonical item별 차이만 override
-> 뉴스/가격 material_id와 연결
```

의약품은 유효성분, 첨가제, 1차 포장재를 분리하고 의료소모품은 본체 부위별 재질을 분리한다. 근거 없는 재질 추론은 운영 리스크 계산에서 제외한다.

## 6. 사용자 결정이 필요한 정책

다음 세 가지는 자동 규칙보다 업무 정책 결정이 먼저다.

1. 의료폐기물 전용용기를 `WASTE / is_forecastable=false`로 계속 제외할지
2. 홍보사업에 사용된 의료용품을 `PROMO`로 볼지, 본래 의료 그룹에 두고 forecast override만 false로 할지
3. 대여사업의 혈당검사지·란셋·알코올솜을 `RENTAL`로 볼지, 본래 소모품 그룹으로 예측할지

이 결정이 확정돼야 동일 제품에 기관·사업별로 일관된 예측 정책을 적용할 수 있다.
