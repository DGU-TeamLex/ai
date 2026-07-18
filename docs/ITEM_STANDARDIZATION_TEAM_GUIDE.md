# TeamLex 물품 표준화·정규화 팀 작업 가이드

작성일: 2026-07-13
대상 시스템: WeP-Stock
기준 데이터: `raw_stock/*.DAT`
현재 정규화 버전: `item-normalization-v0.4`, `item-enrichment-v1.0`,
`classification-v1.0`

## 1. 문서 목적

이 문서는 다른 팀원이 현재까지의 작업을 이어받아 물품을 직접 조사하고, 표준 품목명,
품목군, 세부 유형, 규격, 단위 및 원자재를 일관된 기준으로 검토할 수 있도록 만든 실무
가이드다.

이 작업의 목표는 이름이 비슷한 행을 무조건 하나로 합치는 것이 아니다. 다음을 구분하면서
재고 예측과 뉴스·원자재 리스크 연결에 사용할 수 있는 검증된 품목 마스터를 만드는 것이
목표다.

- 기관별 재고 시계열은 원래 기관과 내부 코드를 유지한다.
- 같은 제품의 표기 차이는 대표 품목으로 묶는다.
- 제조사, 모델, 성분, 함량 또는 규격이 다르면 서로 다른 제품으로 구분한다.
- 공식 근거가 없는 값은 후보로만 남기고 검증값으로 확정하지 않는다.

## 2. 반드시 지킬 원칙

1. `device/` 폴더의 데이터는 어떤 단계에서도 사용하지 않는다.
2. 원본 기준은 오직 `raw_stock/`이며 원본명과 원본 수치를 수정하지 않는다.
3. `data/processed/`와 `data/sample/`은 재생성되는 산출물이므로 직접 수정하지 않는다.
4. 규칙으로 계산한 `*_candidate`는 정답이 아니다.
5. 공식 근거 또는 승인된 검토 결정이 있을 때만 `verified_*` 값을 채운다.
6. 이름만 같다고 다른 기관의 재고 행을 하나의 시계열로 합치지 않는다.
7. 제조사, 모델, 성분, 함량, 멸균 여부 또는 규격이 충돌하면 자동 병합하지 않는다.
8. 상품명만 보고 원자재를 추정하지 않는다.
9. 확인할 수 없는 값은 빈칸과 `not_verified`로 남긴다. `UNCLASSIFIED`도 정상적인 결과다.
10. 사용한 출처, 출처 레코드 ID, URL, 조회일과 검토자를 반드시 남긴다.
11. `regulazation/물품재고_정규화완료.parquet`은 참고 후보일 뿐, 재고 수치나 최종 품목
    ID의 기준으로 사용하지 않는다.

## 3. 현재 진행 상태

| 항목 | 현재 결과 |
|---|---:|
| 원본 재고 행 | 16,265,602 |
| 기관·코드·원본명별 별칭 | 409,519 |
| 기관과 무관한 대표 품목 | 101,546 |
| 후보 분류 일관 | 7,515 |
| 후보 분류 충돌 | 119 |
| 세부 분류 미완성 | 93,912 |
| 공식 근거로 검증 완료 | 0 |
| 검토 샘플 | 1,000 |

공공데이터 인증키가 아직 설정되지 않았으므로 현재 대표 품목은 전부
`verification_status=not_verified`다. 이는 실패가 아니라 근거 없는 확정을 막기 위한
의도적인 상태다.

## 4. 식별 수준

| 식별자 | 의미 | 병합 기준 | 사용처 |
|---|---|---|---|
| `local_item_key` | 기관별 내부 물품 | 기관 ID와 내부 코드 유지 | 수요예측, 재고 시계열 |
| `representative_item_id` | 표기 차이를 모은 내부 대표 품목 | 비교용 이름과 규격이 같음 | 검토 작업 단위 |
| `canonical_item_id` | 제조사·모델·함량까지 특정된 공식 제품 | 공식 코드 또는 완전한 제품 속성 | 제품별 공급망·원자재 |
| `item_family_id` | 기능적으로 같은 품목군 | 기능·용도·성분 기준 | 질병·뉴스·공통 원자재 매핑 |
| `item_subtype_id` | 품목군 안의 세부 유형 | 재질·형태·용도 기준 | 세부 분류·조달 규격 |

### 4.1 기관 ID가 다른 경우

동일 명칭이 여러 기관에 있어도 품목군과 표준 제품 후보는 같을 수 있다. 하지만 기관별
재고량은 합치지 않는다.

```text
기관 A :: USE001 :: 혈당스틱
기관 B :: USE009 :: [방문]혈당검사스틱

item_family_id       = BLOOD_GLUCOSE_TEST_STRIP
standard_family_name = 혈당검사지
local_item_key       = 서로 다름
```

### 4.2 이름이 같아도 분리해야 하는 경우

다음 중 하나라도 다르면 동일 `canonical_item_id`로 확정하지 않는다.

- 의약품 제조사, 유효성분, 함량, 제형 또는 투여경로
- 의료기기 제조사, 모델명, 호환 장비 또는 멸균 여부
- 주사기 용량, 카테터 게이지, 포장재 치수
- 포장수량이 재고 관리 단위를 바꾸는 경우

## 5. DB 품목군 기준

| `item_group_id` | 이름 | 기본 예측 대상 | 판단 기준 |
|---|---|---:|---|
| `MED_ORAL` | 내복약 | t | 정제, 캡슐, 시럽 등 경구 의약품 |
| `MED_INJECT` | 주사제 | t | 바이알·앰플·프리필드 등 주사용 의약품 |
| `MED_TOPICAL` | 외용·패치 | t | 연고, 크림, 점안제, 패치, 파스 |
| `LAB_REAGENT` | 검사시약 | t | 진단시약, 검사지, 테스트 스트립, 배지 |
| `DISINFECT` | 소독·멸균 | t | 소독제, 멸균제, EO 멸균 포장재 |
| `MED_SUPPLY` | 치료재료·의료소모품 | t | 주사기, 주사침, 거즈, 카테터, 수액세트 |
| `KM_EXTRACT` | 한방엑스제 | t | 허가된 한방 엑스·과립 제제 |
| `KM_HERB` | 한방약초 | t | 한약재, 약초, 절편 |
| `SUPPLEMENT` | 영양제 | t | 건강기능식품으로 확인된 제품 |
| `PROMO` | 판촉·홍보물 | f | 홍보·판촉 목적으로 관리되는 물품 |
| `FUEL` | 유류 | t | 휘발유, 경유, 등유 등 |
| `WASTE` | 폐기물 | f | 의료폐기물 용기·봉투, 폐기용 물품 |
| `RENTAL` | 대여물품 | f | 혈압계·유축기 등 실제 대여 장비 |
| `UNCLASSIFIED` | 미분류 | NULL | 근거 부족 또는 기존 그룹에 해당하지 않음 |

### 5.1 자주 틀리는 경계

- `주사기`, `주사침`은 `MED_SUPPLY`이며 `MED_INJECT`가 아니다.
- 주사기에 들어가는 약물은 `MED_INJECT`다.
- 혈당검사 스틱은 `LAB_REAGENT`, 혈당측정기 대여 장비는 `RENTAL`이다.
- 대여 장비용 란셋·검사지·커프는 대여품이 아니라 본래 제품군을 유지한다.
- `홍보물품-한방파스`는 제품 자체의 속성과 별도로 운영 그룹은 `PROMO`다.
- 의료폐기물 전용용기는 현재 기준에서 `WASTE`다.
- 처방 의약품 오메가3 캡슐은 이름에 영양 성분이 있어도 `SUPPLEMENT`가 아니다.
- 임산부 배지의 `배지`와 미생물 배양배지를 혼동하지 않는다.
- 핸드크림·화장품과 의약품 외용 크림을 허가 근거 없이 같은 그룹으로 묶지 않는다.

## 6. 주요 파일

### 6.1 검토 입력

| 파일 | 용도 |
|---|---|
| `data/processed/item_enrichment_review_queue_v1.csv` | 대표 품목 전체 검토 목록 |
| `data/sample/item_grouping_review_sample_1000.csv` | 검토 방식 확인용 1,000건 샘플 |
| `data/processed/item_product_worklist_v1.parquet` | 대표 품목 전체와 집계 통계 |
| `data/processed/item_alias_to_product_v1.parquet` | 원본 별칭과 대표 품목 연결 |
| `data/processed/item_alias_candidates_v0.3.parquet` | 기관·코드별 상세 후보와 운영 태그 |

### 6.2 기준 및 설정

| 파일 | 용도 | 직접 수정 여부 |
|---|---|---:|
| `data/mapping/item_family_taxonomy.csv` | 승인 전 품목군·세부 유형 seed | 검토 후 가능 |
| `data/mapping/item_external_source_registry.csv` | 공식 데이터 소스 목록 | API 변경 시 가능 |
| `src/item_normalization.py` | 이름 정리·후보 분류 규칙 | 테스트와 함께 수정 |
| `src/item_enrichment.py` | 대표품목 집계·외부수집·근거 매칭 | 테스트와 함께 수정 |
| `tests/test_item_normalization.py` | 기존 오분류 회귀 테스트 | 규칙 변경 시 필수 |
| `tests/test_item_enrichment.py` | 근거 수집·매칭 테스트 | 매칭 변경 시 필수 |

`data/processed/`, `data/sample/`, `data/external/`은 Git 추적 대상이 아니다. 팀이 합의한
규칙, 매핑 seed, 테스트와 문서만 커밋한다.

### 6.3 이전 정규화 파일의 사용 범위

`regulazation/물품재고_정규화완료.parquet`은 원본과 같은 16,265,602행을 보존하고 있어
별칭과 이름 후보를 비교하는 참고 자료로는 사용할 수 있다. 다만 다음 이유로 운영 입력이나
정답셋으로 직접 사용하지 않는다.

- 수량과 단가가 정수형으로 변환되어 원본의 소수 정밀도가 손실됐다.
- `NM_*` ID의 생성 규칙·버전·검수 상태가 없어 재현 가능한 공식 ID가 아니다.
- 같은 표준명에 복수 ID가 있고, 하나의 ID에 다른 원본명이 묶인 사례가 있다.
- 품목군, 규격, 단위, 공식 근거와 원자재 관계가 승인 상태로 분리되어 있지 않다.

이 파일의 이름 후보를 재사용하려면 `legacy_normalization_id`와 legacy alias 후보로만
가져오고, 수치 fact는 반드시 `raw_stock/*.DAT`에서 다시 만든다. 자세한 감사 결과는
`docs/RAW_STOCK_NORMALIZATION_GUIDE.md`를 기준으로 한다.

### 6.4 실행 환경 준비

정규화·검증 명령은 프로젝트의 `teamlex` Conda 환경에서 실행한다. 기본 Python에
`pandas` 또는 `pyarrow`가 없으면 테스트 수집부터 실패한다.

```bash
conda activate teamlex
python -m pip install -r requirements.txt
```

이미 의존성이 설치된 환경에서는 `pip install`을 반복할 필요가 없다. 활성 환경은
`python -c "import pandas, pyarrow"`로 빠르게 확인할 수 있다.

## 7. 검토 목록의 핵심 컬럼

### 7.1 원본 및 후보

| 컬럼 | 의미 |
|---|---|
| `representative_item_id` | 검토 단위 ID. 결정 파일의 기본키로 사용 |
| `representative_name` | 대표로 선택된 원본 기반 이름 |
| `raw_name_examples` | 같은 대표 품목에 묶인 원본명 예시 |
| `local_codes` | 연결된 기관 내부 코드 전체 |
| `institution_count` | 이 이름을 사용한 기관 수 |
| `occurrence_count` | 원본 재고 행 출현 횟수 |
| `usage_sum` | 사용량 합계. 검토 우선순위에 활용 |
| `item_group_id_candidate` | 현재 규칙의 품목군 후보 |
| `item_group_candidates` | 동일 대표품목에서 충돌한 후보 목록 |
| `standard_family_name_candidate` | 현재 규칙의 표준 품목군명 후보 |
| `standard_subtype_name_candidate` | 현재 규칙의 세부 유형 후보 |
| `normalized_specification_candidate` | 규칙으로 파싱한 규격 후보 |
| `standard_unit_candidate` | 규칙으로 계산한 단위 후보 |
| `candidate_status` | 후보 일관·충돌·미완성 상태 |

### 7.2 외부 근거 및 검증

| 컬럼 | 의미 |
|---|---|
| `canonical_item_id_candidate` | 외부 매칭으로 찾은 공식 제품 후보 |
| `canonical_item_id` | 공식 코드로 확정된 제품 ID |
| `matched_source_item_name` | 외부 마스터에서 찾은 이름 |
| `verified_item_name` | 승인된 표준 제품명 |
| `verified_item_group_id` | 승인된 DB 품목군 |
| `verified_family_name` | 승인된 기능 품목군명 |
| `verified_subtype_name` | 승인된 세부 유형명 |
| `verified_specification` | 승인된 규격 |
| `verified_unit` | 승인된 재고 단위 |
| `verified_material` | 근거가 확인된 원자재·재질 |
| `evidence_source` | 근거 데이터 소스 ID |
| `evidence_record_id` | 해당 소스의 공식 레코드 ID |
| `evidence_url` | 근거를 다시 확인할 수 있는 URL |
| `retrieved_at` | 근거 조회 시각 |
| `match_method` | 코드 일치·이름 일치 등 매칭 방법 |
| `match_score` | 매칭 후보 점수. 승인 여부와 동일하지 않음 |
| `verification_status` | 신원 검증 상태 |
| `review_status` | 다음 검토 단계 |
| `review_reason` | 검토가 필요한 이유 |

### 7.3 상태 해석

| 컬럼 | 값 | 의미 |
|---|---|---|
| `candidate_status` | `candidate_consistent` | 기존 규칙 후보끼리 충돌 없음 |
| `candidate_status` | `candidate_conflict` | 같은 대표품목에서 그룹·유형 충돌 |
| `candidate_status` | `candidate_incomplete` | 세부 품목군 또는 유형 미확정 |
| `verification_status` | `not_verified` | 공식 근거 미확인 |
| `verification_status` | `candidate_identity` | 이름 기반 외부 후보. 사람 검토 필수 |
| `verification_status` | `ambiguous` | 공식 후보가 둘 이상이거나 충돌 |
| `verification_status` | `verified_identity` | 유일한 공식 코드로 제품 신원 확인 |
| `review_status` | `needs_external_evidence` | 외부 근거 수집 필요 |
| `review_status` | `identity_review_required` | 제조사·모델·규격 비교 필요 |
| `review_status` | `taxonomy_review_required` | 신원 확인 후 로컬 분류 승인 필요 |

## 8. 팀원의 수동 검토 절차

### 8.1 작업 배치 선정

처음에는 다음 순서로 검토한다.

1. `candidate_status=candidate_conflict`
2. `usage_sum` 또는 `occurrence_count`가 큰 품목
3. `UNCLASSIFIED` 중 의약품·의료용품으로 보이는 품목
4. 기존 family 후보가 있으나 규격·단위가 비어 있는 품목
5. 나머지 저사용량 품목

팀원별 배치는 `representative_item_id`로 나눈다. 같은 ID를 여러 사람이 동시에 수정하지
않는다.

### 8.2 원본 문맥 확인

대표 이름만 보지 말고 다음을 함께 확인한다.

- `raw_name_examples`
- `local_codes`
- `institution_count`
- `item_group_candidates`
- `data/processed/item_alias_candidates_v0.3.parquet`의 운영 태그와 원본 코드

예를 들어 `파스`라는 이름만으로 제품을 특정할 수 없다. 제조사와 규격이 없다면
`MED_TOPICAL` family 후보까지만 승인하고 `canonical_item_id`는 비워 둔다.

### 8.3 공식 근거 검색

품목 종류에 따라 다음 순서로 찾는다.

| 대상 | 1차 근거 | 보강 근거 |
|---|---|---|
| 의약품 | 식약처 의약품 제품 허가정보 | 심평원 약가·주성분 정보 |
| 의료기기 | 식약처 품목허가·UDI 제품정보 | 제조사 IFU, 제품 라벨 |
| 의약외품 | 식약처 의약외품 제품 허가정보 | 제조사 공식 문서 |
| 건강기능식품 | 식약처 건강기능식품정보 | 제품 표시사항 |
| 일반 물품 | 조달청 물품목록정보 | 제조사 규격서 |
| 원자재 | UDI 통합정보, IFU, SDS | 제조사 기술문서 |

공식 소스 URL은 `data/mapping/item_external_source_registry.csv`에서 확인한다.

검색 결과 페이지의 짧은 설명, 쇼핑몰 상품명, 블로그, 광고 문구만으로 승인하지 않는다.
보조 검색 결과는 공식 레코드를 찾기 위한 후보로만 사용한다.

### 8.4 신원 결정

아래 순서로 판단한다.

1. 공식 코드가 로컬 코드와 유일하게 일치하면 `verified_identity` 후보가 된다.
2. 공식 이름만 일치하면 제조사·모델·함량·규격을 추가로 비교한다.
3. 모든 핵심 속성이 일치할 때만 공식 `canonical_item_id`를 승인한다.
4. 공식 이름이 여러 제품에 쓰이면 `ambiguous`로 둔다.
5. 일반명뿐이면 family만 정하고 제품 ID는 비운다.
6. 근거가 없으면 `not_verified`를 유지한다.

### 8.5 품목군·세부 유형 결정

제품 신원과 로컬 DB 분류는 별도 결정이다. 공식 제품을 찾았어도 DB의 14개 그룹 중 어떤
그룹에 들어갈지는 제형·용도·품목분류를 근거로 검토한다.

```text
공식 제품 ID
-> 공식 품목명·제형·용도 확인
-> item_group_id 선택
-> item_family_id / standard_family_name 선택
-> item_subtype_id / standard_subtype_name 선택
-> 규격과 단위 기록
```

새 family나 subtype을 만들 때는 기존 ID를 재사용할 수 없는지 먼저 확인한다. 새 ID는
영문 대문자 `SNAKE_CASE`로 만들고, 동일 개념에 두 ID를 만들지 않는다.

### 8.6 규격과 단위 표준화

규격 값과 재고 단위를 분리한다.

| 원본 | 표준 규격 | 단위 코드 | 주의점 |
|---|---|---|---|
| `주사기 3cc` | `3mL` | `EA` | `cc`를 `mL`로 통일 |
| `주사기 5cc` | `5mL` | `EA` | 3mL와 합치지 않음 |
| `15cm*200M` | `15cm x 200m` | `ROLL` | 폭과 길이 모두 보존 |
| `500cc 이하` | `<=500mL` | `EA` | 경계 조건 보존 |
| `5L 초과` | `>5L` | `EA` | `5L 이하`와 분리 |
| `22G 성인용` | `22G (성인용)` | `EA` | 게이지와 용도 보존 |
| `혈당검사지 50매` | 제품 규격 `50매` | 별도 검토 | 호환 모델과 포장수량 보존 |

다음 값은 임의로 삭제하지 않는다.

- 소수점: `0.5mg`와 `5mg`는 다르다.
- 포장수량: `100개`와 `200개`는 다르다.
- 비교 조건: `이하`, `초과`, `미만`, `이상`
- 치수 순서와 단위
- 성인용·소아용, 멸균·비멸균, 일회용 여부

### 8.7 원자재 결정

원자재는 다음 관계를 구분해서 기록한다.

| 관계 | 예시 |
|---|---|
| `active_ingredient` | 의약품 유효성분 |
| `excipient` | 의약품 첨가제 |
| `direct_component` | 의료소모품 본체 재질 |
| `primary_packaging` | 바이알, 앰플, 블리스터 |
| `secondary_packaging` | 외부 상자·포장재 |
| `upstream_material` | 상위 원료·석유화학 원료 |
| `compatible_consumable` | 장비와 전용 소모품 관계 |

제품명에 `PE`, `라텍스`, `스테인리스`가 들어 있어도 그것이 어느 부품의 재질인지 확인해야
한다. 제조사 IFU·SDS 또는 공식 데이터에 없는 재질은 `verified_material`에 넣지 않는다.

## 9. 수동 결정 기록 형식

생성된 review CSV나 Parquet를 직접 덮어쓰지 않는다. 팀의 수동 결정은 별도 매핑 CSV로
관리한다. 권장 파일명은 다음과 같다.

```text
data/mapping/item_manual_standardization_decisions.csv
```

권장 컬럼:

```csv
decision_id,representative_item_id,decision_action,canonical_item_id,verified_item_name,verified_item_group_id,item_family_id,verified_family_name,item_subtype_id,verified_subtype_name,verified_specification,verified_unit,verified_material,evidence_source,evidence_record_id,evidence_url,evidence_field,retrieved_at,verification_status,review_status,reviewer,reviewed_at,review_note,decision_version
```

이 수동 결정 CSV는 팀의 판정 이력을 보존하는 승인 원장이다. 예측 연동에는 원장의 결정을
로컬 품목 단위로 펼친 `data/mapping/item_forecast_classification_approved.csv`를 사용한다.
`data/processed/` 산출물을 직접 고치지 않는다. 승인 taxonomy와 로컬 매핑을 예측에 반영하는
명령, 필수 컬럼 및 검증 규칙은 `docs/CLASSIFIED_FORECAST_INTEGRATION.md`를 따른다.

### 9.1 필수 입력

| 조건 | 필수 컬럼 |
|---|---|
| family만 승인 | 대표 ID, 그룹, family ID·이름, 근거, 검토자, 버전 |
| 제품 신원 승인 | 위 항목 + 공식 `canonical_item_id`, 제품명, 공식 레코드 ID |
| subtype 승인 | family 항목 + subtype ID·이름 |
| 규격 승인 | 규격 원문을 확인할 수 있는 `evidence_field` |
| 원자재 승인 | 원자재 관계와 제품 부품을 설명하는 `review_note` |
| 미해결 | 대표 ID, `UNRESOLVED`, 사유, 검토자 |

`decision_action` 권장값:

- `APPROVE_FAMILY`: 제품은 미확정이고 기능 품목군만 승인
- `APPROVE_ITEM`: 공식 제품 신원까지 승인
- `SPLIT`: 현재 대표품목을 둘 이상의 제품으로 분리해야 함
- `MERGE`: 다른 대표품목과 병합 후보
- `REJECT_CANDIDATE`: 현재 자동 후보가 틀림
- `UNRESOLVED`: 근거 부족으로 보류

### 9.2 결정 예시

#### 혈당검사 스틱

```text
representative_name       = [방문]혈당검사스틱
decision_action           = APPROVE_FAMILY
verified_item_group_id    = LAB_REAGENT
item_family_id            = BLOOD_GLUCOSE_TEST_STRIP
verified_family_name      = 혈당검사지
canonical_item_id         = 빈칸
review_note               = 제조사·호환 모델 없어 제품 신원 미확정
```

#### 의약품

```text
representative_name       = 아마릴정2mg(한독약품)-1정
decision_action           = APPROVE_ITEM
verified_item_group_id    = MED_ORAL
verified_specification    = 2mg
canonical_item_id         = 식약처 품목기준코드 기반 ID
evidence_source           = mfds_drug_permit
evidence_record_id        = 실제 조회한 품목기준코드
```

유효성분은 공식 레코드에서 확인한 값만 기록한다. 이 문서의 예시 문구를 근거로 실제
레코드 ID를 만들어서는 안 된다.

#### 주사기

```text
representative_name       = 주사기 3cc
decision_action           = APPROVE_FAMILY
verified_item_group_id    = MED_SUPPLY
item_family_id            = DISPOSABLE_SYRINGE
verified_family_name      = 주사기
item_subtype_id           = SYRINGE_USAGE_BASED
verified_subtype_name     = 주사기(사용량 기준)
verified_specification    = 3mL
verified_unit             = EA
```

`주사기 5cc`, `주사기 10cc`는 같은 family에 속하지만 규격은 별도로 보존한다.

## 10. 공식 API 수집

### 10.1 사전 준비

공공데이터포털에서 `data/mapping/item_external_source_registry.csv`에 있는 API를 각각 활용
신청한다. 인증키는 코드, 문서, 채팅 또는 Git에 넣지 않는다.

현재 수집 코드는 `.env`를 자동으로 읽지 않는다. 실행할 셸에서 다음처럼 환경변수를
설정해야 한다.

```bash
export DATA_GO_KR_SERVICE_KEY='공공데이터포털 Decoding 인증키'
```

### 10.2 첫 페이지 시험

전체 수집 전에 각 소스의 첫 페이지만 확인한다.

```bash
python -m src.item_enrichment fetch \
  --source mfds_drug_permit \
  --max-pages 1 \
  --output /tmp/mfds_drug_permit_test.parquet
```

다음을 확인한 후 전체 수집한다.

- 파일이 실제 Parquet인지
- 행 수가 0이 아닌지
- 공식 ID와 제품명 필드가 들어 있는지
- API 오류 응답이 데이터 행으로 저장되지 않았는지
- 조회일과 원문 JSON이 기록됐는지

### 10.3 전체 수집 순서

```bash
python -m src.item_enrichment fetch --source mfds_drug_permit
python -m src.item_enrichment fetch --source mfds_device_permit
python -m src.item_enrichment fetch --source mfds_device_udi_product
python -m src.item_enrichment fetch --source mfds_device_udi_attributes
python -m src.item_enrichment fetch --source mfds_quasi_drug_permit
python -m src.item_enrichment fetch --source mfds_health_functional_food
python -m src.item_enrichment fetch --source pps_item_list
python -m src.item_enrichment match
```

공식 파일 데이터라고 표시되어 있어도 파일 형식, 필수 컬럼, 최소 행 수를 검사한다. 실제
작업 중 심평원 의약품표준코드 CSV 링크가 전혀 다른 JPEG를 반환한 사례가 있었고, 해당
파일은 즉시 폐기했다. 포털 설명과 확장자만 믿지 않는다.

## 11. 규칙 또는 taxonomy 수정 방법

### 11.1 기존 family에 동의어 추가

1. 원본 예시와 반례를 수집한다.
2. `src/item_normalization.py`의 기존 `FAMILY_RULES`로 처리 가능한지 확인한다.
3. 정규식이 다른 단어의 부분문자열과 충돌하지 않는지 확인한다.
4. 정상 예시와 오분류 반례를 테스트에 함께 추가한다.
5. 샘플을 생성해 결과를 눈으로 확인한다.
6. 전체 결과를 재생성한다.

정규식만 수정하고 반례 테스트를 생략하지 않는다. `트렌탈/렌탈`, `행주/주사`,
`배지/배양배지` 같은 부분문자열 충돌이 이미 발생한 적이 있다.

### 11.2 새로운 family 또는 subtype 추가

`data/mapping/item_family_taxonomy.csv`에 다음 값을 정의한다.

```text
source_item_name
source_subtype_name
source_specification
item_family_id
standard_family_name
item_subtype_id
standard_subtype_name
item_group_id
is_forecastable
normalized_specification
unit_code
unit_name
material_candidate
material_mapping_status
review_status
taxonomy_version
```

추가 기준:

- `item_family_id`, `item_subtype_id`는 영문 대문자 `SNAKE_CASE`
- 표준명은 하나의 맞춤법과 띄어쓰기로 통일
- 규격과 단위를 서로 다른 컬럼에 저장
- 원자재 근거가 없으면 `material_candidate`를 비움
- 신규 행은 검토 완료 전 `review_status=candidate`
- 변경할 때 `taxonomy_version` 증가

### 11.3 생성 파일 재생성

```bash
python -m src.item_normalization --full
python -m src.item_enrichment build-worklist
python -m src.item_enrichment match
```

전체 정규화는 1,600만 행 이상을 다시 조인하므로 실행 중인 프로세스를 종료하지 않는다.
완료 후 보고서와 행 수를 반드시 비교한다.

## 12. 품질검사

### 12.1 자동 테스트

```bash
python -m unittest
```

현재 기준은 33개 테스트 통과다. 테스트 수보다 중요한 것은 기존 테스트가 모두 통과하고,
새 규칙의 정상 예시와 반례가 추가되는 것이다.

### 12.2 필수 데이터 검사

| 검사 | 통과 기준 |
|---|---:|
| 별칭 연결 행 | 409,519 |
| 대표 품목 ID 중복 | 0 |
| 빈 대표 이름 | 0 |
| 대표품목과 grouped 결과 행 차이 | 0 |
| 근거 없이 채워진 `canonical_item_id` | 0 |
| 동일 공식 코드의 복수 자동 승인 | 0 |
| 규격 충돌 자동 병합 | 0 |
| 검토 샘플 행 | 1,000 |
| 검토 샘플 품목군 누락 | 0 |

원본 데이터가 변경되면 고정 행 수 자체는 달라질 수 있다. 이 경우 이전 보고서와 차이를
설명하고, `별칭 입력 행 = 별칭 연결 행`, `대표 ID 유일성`, `조인 누락 0` 조건으로
검증한다.

### 12.3 사람이 확인할 표본

최소한 다음을 포함한다.

- 사용량 상위 품목
- `candidate_conflict` 전부 또는 충분한 표본
- 각 DB 품목군 최소 1건
- 같은 family의 다른 규격
- 이름이 비슷한 동명이품
- 운영 태그가 포함된 품목
- 의약품, 의료기기, 시약, 일반 물품 각각의 공식 코드 매칭

## 13. 팀 협업 규칙

1. 작업 배치는 `representative_item_id` 목록으로 나눈다.
2. 한 배치의 담당자와 2차 검토자를 기록한다.
3. 사용량 상위 품목, 제품 병합, 원자재 매핑은 2명이 확인한다.
4. 생성 산출물 대신 mapping CSV, 규칙 코드, 테스트를 커밋한다.
5. PR에는 변경 이유, 근거 URL, 영향받은 품목 수, 추가 테스트를 적는다.
6. 출처가 변경되거나 폐기된 경우 기존 결정을 즉시 삭제하지 말고 유효기간과 대체 출처를
   기록한다.
7. 판단이 어려우면 임의로 다수결 값이나 가장 가까운 이름을 선택하지 않고
   `UNRESOLVED`로 남긴다.

## 14. 완료 기준

한 품목의 표준화가 완료됐다고 말하려면 다음 조건을 만족해야 한다.

### family 수준 완료

- 대표 품목과 연결된 원본 별칭을 확인했다.
- DB `item_group_id`가 결정됐다.
- `item_family_id`와 표준 family명이 결정됐다.
- 결정 근거와 검토자가 기록됐다.
- 규격 충돌이 없는지 확인했다.

### canonical 제품 수준 완료

- family 수준 조건을 모두 만족한다.
- 공식 제품 코드와 코드 체계가 확인됐다.
- 제품명, 제조사, 모델 또는 성분·함량이 일치한다.
- 공식 레코드 ID와 URL, 조회일이 기록됐다.
- 동일 공식명 후보가 여럿이면 충돌을 해소했다.

### 원자재 수준 완료

- canonical 제품 또는 승인된 family가 확인됐다.
- 원자재와 제품의 관계 유형이 기록됐다.
- 사용 부품 또는 포장 위치가 기록됐다.
- IFU·SDS·공식 데이터 등 직접 근거가 있다.
- 근거 없는 LLM·이름 추론이 포함되지 않았다.

## 15. 관련 문서

- `docs/RAW_STOCK_NORMALIZATION_GUIDE.md`: 이전 `regulazation` 파일 감사와 raw_stock 재정규화 원칙
- `docs/ITEM_EVIDENCE_ENRICHMENT_GUIDE.md`: 외부 근거 수집과 자동 매칭 개요
- `docs/ITEM_NORMALIZATION_MATERIAL_MAPPING_PLAN.md`: 전체 설계와 원자재 매핑 구조
- `docs/ITEM_NORMALIZATION_AUDIT.md`: 기존 정규화 데이터 감사 결과
- `docs/ITEM_NORMALIZATION_V0.4_RESULT.md`: 최신 전체 정규화 결과
- `docs/ITEM_CLASSIFICATION_V1_RESULT.md`: 외부 근거 기반 분류·승인 결과

이 문서와 실제 코드·보고서가 다르면 코드를 먼저 확인하고 문서를 함께 수정한다. 근거 없이
모든 항목을 채우는 것보다 미해결 항목과 이유를 정확히 남기는 것이 이 시스템의 품질
기준이다.
