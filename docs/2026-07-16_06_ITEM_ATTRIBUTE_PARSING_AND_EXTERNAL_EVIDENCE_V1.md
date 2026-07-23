# 물품명 속성 분해 및 외부 근거 사전 운영 가이드 v1.1

## 1. 목적

`raw_stock` 물품명을 재고 예측과 원자재 매칭에 사용할 수 있도록 다음 속성으로 분해한다.

- 단순 품목명 또는 제품명
- 제조사 후보
- 성분명과 표준 성분 ID
- 제형
- 용량과 용량 역할
- 의약품 함량과 농도
- 제품 중량
- 바늘 게이지와 바늘 길이
- 치수
- 포장 수량과 포장 단위
- 재고 단위
- 미확정 토큰

파서 구현은 `src/item_attribute_parser.py`에 있다. `device/` 데이터는 읽지 않는다.

## 2. 24G 판정 원칙

`G`는 이름만 보고 그램이나 개수로 정하지 않는다.

- 주사기, 주사침, 채혈침, 카테터, 니들 등 바늘 문맥의 `24G`는 `needle_gauge=24G`다.
- `3cc/23G*24mm/100개입`은 각각 용량 `3mL`, 게이지 `23G`, 길이 `24mm`, 포장 수량 `100 EA`다.
- `23Gx24mm`, `23G X 24mm`, `23G*24mm`의 `x`, `X`, `*`는 동일한 규격 구분자로 처리한다.
- 의약품 문맥의 `세프트리악손주1G`는 `active_strengths=1g`다.
- 의료용 거즈의 `8g`는 `net_weight=8g`이며 게이지나 의약품 함량이 아니다.
- `G`와 `개수`는 서로 다른 컬럼이며 게이지 값을 재고 수량 계산에 사용하지 않는다.

FDA 주사기 지침은 주사기 크기, 바늘 게이지, 바늘 길이, 수량을 서로 다른 표시 속성으로 구분한다. 이 의미 규칙은 검증 사전에 저장한다.

## 3. 두 종류의 키

`forecast_series_key`는 원래 기관별 재고 시계열 식별자인 `local_item_key`를 유지한다.

`normalized_inventory_key`는 품목군, 세부유형, 용량, 게이지, 길이, 치수, 포장 수량, 포장 단위, 재고 단위를 포함한다. 규격이 다른 품목을 검토 없이 합치지 않기 위한 키다.

`material_match_key`는 품목군, 세부유형, 성분, 용량, 게이지, 길이, 치수를 포함한다. 의약품은 검증 성분을, 의료용품은 품목군과 물리 규격을 원자재 매칭 입력으로 사용한다.

의약품은 `MED_ORAL`, `MED_INJECT`, `MED_TOPICAL`처럼 유효한 `item_group_id`가 있으면 의료소모품용 세부 `item_family_id`가 비었다는 이유만으로 오류 처리하지 않는다. 의약품 원자재 매칭의 핵심 통과 조건은 세부 품목군 유무가 아니라 성분 근거다.

## 4. 성분과 제조사 처리

성분은 근거 수준을 구분한다.

- `verified_dictionary`: 식약처 의약품안전나라 또는 공식 제조사 페이지에서 검증한 성분
- `name_literal_parenthetical`: 원문 괄호에 있고 기존 성분 사전과 일치한 성분 후보
- `name_literal_substring`: 원문 이름에 직접 포함된 성분 후보
- `name_literal_unmapped`: 원문에 성분으로 보이는 문자열이 직접 있지만 표준 ID는 아직 검증되지 않은 경우

`name_literal_unmapped`는 `UNMAPPED::` ID를 부여해 원문을 잃지 않되 원자재 자동 확정에는 사용하지 않는다. 외부 검증 후에만 정식 성분 ID로 승격한다.

`material_match_readiness`는 다음처럼 사용한다.

- `verified_ingredient_ready`: 검증 사전의 제품-성분 근거가 있어 자동 원자재 매칭 입력으로 사용 가능
- `ingredient_candidate_review`: 이름에서 성분 후보를 찾았지만 외부 근거 검토 전이므로 자동 적용 금지
- `family_only`: 의료용품 계열과 규격만 확인된 상태
- `needs_external_match`: 품목 계열 또는 성분을 더 찾아야 하는 상태

제조사는 `제약`, `약품`, `파마`, 법인 표기 등 명시적 회사 근거가 있는 괄호를 우선 추출한다. 위치만으로 제조사라고 단정하지 않는다. 제조사와 성분 중 역할이 불명확한 괄호는 `unresolved_tokens`와 외부 후보 큐로 보낸다.

## 5. 외부 근거 등급

자동 적용 가능한 사전 파일은 `data/mapping/item_attribute_evidence_dictionary_v1.csv`다.

자동 적용 조건은 코드에서 다음과 같이 제한한다.

- `verification_status`: `verified_official` 또는 `verified_multi_source`
- `source_tier`: `official_regulator`, `official_manufacturer`, `official_public_agency`

식약처, FDA 등 규제기관과 공식 제조사 근거를 우선한다. 건강보험심사평가원 같은 공공기관 근거는 해당 페이지가 직접 제공하는 필드에만 사용하며, 성분처럼 페이지에서 직접 확인되지 않는 필드는 독립 전문자료와 일치할 때 `verified_multi_source`로 기록한다. 블로그, 쇼핑몰, 일반 약품 설명 사이트, 검색 결과 요약만으로는 자동 적용 사전에 넣지 않는다.

공식 페이지를 사전에 추가할 때는 원문 URL, 원본 레코드 ID, 조회 시각, 근거 설명, 가능하면 응답 해시를 함께 기록한다.

## 6. 외부 매칭 후보 큐

`data/processed/item_external_match_candidates_v1.csv`에는 완전히 분류하지 못한 대표품목을 저장한다.

- `verified_dictionary_ready`: 공식 페이지를 로컬 캐시로 검증해 사전 승격 가능한 후보
- `web_candidate_needs_review`: URL은 있으나 아직 공식성 또는 품목 동일성을 확정하지 못한 후보
- `search_required`: 아직 신뢰할 수 있는 외부 근거를 찾지 못한 후보

큐에는 검색어, 기존 제안 품목군, 제조사·성분·규격 후보, URL, 도메인, 근거 등급, 사용량과 등장 횟수를 보존한다. 우선순위는 사용량과 등장 횟수가 큰 품목부터 검토한다.

검색 결과는 다음 순서로 검증한다.

1. 식약처 의약품 또는 의료기기 허가 정보
2. 식약처 UDI 제품 정보
3. 공식 제조사 제품 페이지
4. 다른 규제기관 또는 공공조달 규격
5. 전문 2차 자료
6. 일반 판매 페이지와 검색 결과

5, 6단계 근거만 있는 경우는 후보 상태를 유지한다.

이번 외부 검증에서 사전에 추가한 대표 사례는 다음과 같다.

| 제품 | 검증 필드 | 근거 등급 |
|---|---|---|
| 페니라민정 | 제품, 유한양행, 클로르페니라민말레산염 2mg | 유한양행 공식 제품정보 |
| 삼남아세트아미노펜정500mg | 제품, 삼남제약, 아세트아미노펜 500mg | 삼남제약 공식 제품정보 |
| 신일티아민염산염정10mg | 제품코드, 성분, 제조사, 10mg | 심평원 + 약학정보원 다중 근거 |
| 한국넬슨이부프로펜정200mg | 구명칭, 제품, 제조사, 이부프로펜 200mg | 한국넬슨제약 + 대한의사협회 DB 다중 근거 |

근거 URL은 사전의 `source_url`과 `evidence_note`에 보존된다. 검색으로 찾았더라도 동일 제품 여부가 불명확하면 사전이 아니라 외부 후보 큐에 남긴다.

## 7. 산출물

- `data/processed/item_alias_attributes_v1.parquet`: 기관별 전체 별칭 속성
- `data/processed/item_representative_attributes_v1.parquet`: 대표품목 속성
- `data/processed/item_external_match_candidates_v1.csv`: 외부 매칭 및 검색 큐
- `data/mapping/item_attribute_evidence_dictionary_v1.csv`: 오프라인 자동 적용 검증 사전
- `data/sample/item_attribute_review_sample_1000.csv`: 게이지, 미확정 토큰, 외부 검토 품목 중심 표본
- `data/processed/item_attribute_parser_v1_report.json`: 전수 처리 통계와 품질 게이트

`parsed_tokens_json`은 각 토큰의 원문, 표준값, 시작·끝 위치, 신뢰도, 판정 근거, 역할을 보존한다. 사람이 오분류를 재현하고 수정할 때 이 컬럼을 먼저 확인한다.

## 8. 실행

```bash
conda run -n teamlex python -m src.item_normalization --full
conda run -n teamlex python -m src.item_enrichment build-worklist
conda run -n teamlex python -m src.material_pipeline --input data/processed/item_product_worklist_v1.parquet
conda run -n teamlex python -m src.item_classification build
conda run -n teamlex python -m src.item_attribute_parser
```

공식 API 키가 제공되면 식약처 의약품, 의료기기, UDI 원장을 먼저 수집한 뒤 속성 파이프라인을 다시 실행한다. API 키 없이 검색으로 찾은 결과는 전체 품목 검증을 대체하지 않는다.

## 9. 품질 게이트

- 원본 별칭 수와 속성 출력 행 수가 같아야 한다.
- 대표품목 ID는 중복되지 않아야 한다.
- 같은 원문 위치가 `needle_gauge`와 `active_strength`로 동시에 분류되면 실패한다.
- `mg/1정`의 `1정`처럼 함량 기준인 토큰과 실제 `pack_count`가 같은 위치를 차지하면 실패한다.
- 비공식 후보 행은 검증 사전 로더가 읽지 않아야 한다.
- `pack_quantity`와 `needle_gauge`는 별도 컬럼이어야 한다.
- 원문 명시 성분의 표준 ID가 불명확하면 `UNMAPPED::` 상태를 유지해야 한다.
- 외부 근거 없이 제품 성분이나 제조사를 추론해 확정하지 않는다.

## 10. 2026-07-16 전수 처리 결과

- 원본 재고: 16,265,602행, 정규화 조인 누락 0건
- 별칭 속성: 409,519행
- 대표품목 속성: 101,546행, 대표 ID 중복 0건
- 검증 사전: 45행 (`verified_official` 36, `verified_multi_source` 9)
- 게이지 보유 대표품목: 1,008행
- 농도 보유 대표품목: 4,361행
- `verified_ingredient_ready`: 81행
- `ingredient_candidate_review`: 8,059행
- 외부 검토 큐: 97,589행
- 외부 URL 후보: 1,455행, 사전 승격 준비 후보: 69행, 검색 필요: 96,065행
- 검토 샘플: 1,000행, 대표 ID 중복 0건
- 게이지-의약품 함량 위치 충돌: 0건
- 함량 기준단위-포장수량 위치 충돌: 0건

`search_required`는 외부 매칭이 끝났다는 뜻이 아니라 검색어와 우선순위만 준비된 상태다. 따라서 현재 결과를 전 품목 완전 검증으로 간주하면 안 된다. 원자재 자동매칭은 81개의 `verified_ingredient_ready`부터 적용하고, 나머지는 후보 검토를 거쳐 사전을 늘리는 방식으로 운영한다.
