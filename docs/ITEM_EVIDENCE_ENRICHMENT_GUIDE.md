# 물품 정체 검증 및 세부 분류 가이드

## 원칙

모든 `raw_stock` 별칭은 먼저 기관과 무관한 대표 품목으로 집계한다. 대표 품목의 품목군,
세부 유형, 규격, 단위, 원자재는 공식 레코드 또는 검토자가 승인한 근거가 있을 때만
`verified_*` 필드에 기록한다. 이름 규칙으로 계산한 값은 계속 `*_candidate`로 유지한다.

`UNCLASSIFIED`를 임의의 품목군에 넣거나, 상품명만 보고 원자재를 추정하지 않는다. 근거가
없으면 `not_verified`, 동명이품이면 `ambiguous`, 이름을 단순화한 뒤에만 일치하면
`candidate_identity`로 남긴다.

## 계층

1. `representative_item_id`: 기관별 코드와 별칭을 합친 내부 대표 품목
2. `canonical_item_id`: 식약처 품목기준코드, UDI-DI, 조달청 물품식별번호 등 공식 제품 ID
3. `item_group_id`: DB의 14개 업무 품목군
4. `item_family_id`: 주사기, 수액세트, 혈당검사지 같은 기능 품목
5. `item_subtype_id`: 일회용 주사기, needle box, PE 봉투 등 세부 유형
6. `verified_specification`, `verified_unit`: 공식 규격과 단위

동일 명칭이 여러 기관에 존재해도 하나의 `representative_item_id`로 집계한다. 다만 규격,
제조사, 모델, 성분, 함량이 다르면 별도 `canonical_item_id`로 분리한다.

## 공식 소스

| 대상 | 1차 소스 | 검증 필드 |
|---|---|---|
| 의약품 | 식약처 의약품 제품 허가정보 | 품목기준코드, 제품명, 업체명, 주성분, 포장단위 |
| 의약품 보강 | 심평원 약가·주성분 마스터 | 제품코드, 일반명코드, 제형, 투여, 함량, 단위 |
| 의료기기 | 식약처 의료기기 품목허가 | 허가번호, 품목명, 등급, 업체명 |
| 의료기기 제품 | 식약처 UDI 제품정보 | UDI-DI, 모델명, 제품명, 멸균법, 사용목적 |
| 의료기기 물질 | 식약처 UDI 통합정보 | 라텍스·프탈레이트 포함 여부 등 공개된 특성 |
| 의약외품 | 식약처 의약외품 제품 허가정보 | 품목기준코드, 품목명, 업체명, 분류번호 |
| 건강기능식품 | 식약처 건강기능식품정보 | 품목제조관리번호, 제품명, 업체명 |
| 일반 물품 | 조달청 물품목록정보 | 8자리 품명, 10자리 세부품명, 물품식별번호, 개별속성 |

의료기기 원자재는 UDI 통합정보나 제조사 IFU/SDS에 공개된 값만 확정한다. 공개되지 않은
재질은 `verified_material`을 비워 두고 제조사 문서 검토 대상으로 보낸다.

## 실행 순서

```bash
python -m src.item_normalization --full
python -m src.item_enrichment build-worklist
export DATA_GO_KR_SERVICE_KEY='발급받은 일반 인증키(Decoding)'
python -m src.item_enrichment fetch --source mfds_drug_permit
python -m src.item_enrichment fetch --source mfds_device_permit
python -m src.item_enrichment fetch --source mfds_device_udi_product
python -m src.item_enrichment fetch --source mfds_device_udi_attributes
python -m src.item_enrichment fetch --source mfds_quasi_drug_permit
python -m src.item_enrichment fetch --source mfds_health_functional_food
python -m src.item_enrichment fetch --source pps_item_list
python -m src.item_enrichment match
python -m src.material_pipeline
python -m src.item_classification fetch-official-web --delay 0.15
python -m src.item_classification build
```

API는 품목별 호출이 아니라 공식 마스터를 페이지 단위로 한 번 수집한 뒤 로컬에서
매칭한다. 원문 레코드는 `source_payload_json`으로 보존한다.

API 키가 없을 때 `fetch-official-web`은 후보에 이미 연결된 식약처 의약품안전나라
제품 페이지를 수집한다. 응답 해시, 최종 URL, 조회 시각을 캐시하며, 제품명·품목코드·성분·
함량이 모두 일치해야 제품 단위 승인이 가능하다. 법령으로 확인한 가족 분류와 공식 제품
신원 검증은 서로 다른 승인 수준으로 기록한다.

## 승인 기준

- 공식 코드가 유일하게 일치: 자동 신원 확정 가능
- 정규화한 공식 제품명이 유일하게 정확 일치: 신원 후보만 생성, 사람 검토 필수
- 괄호·포장 표현을 제거한 핵심명이 유일하게 일치: 후보만 생성, 사람 검토 필수
- 동일 공식명이 둘 이상: 자동 확정 금지
- 공식 근거 없음: 미검증 유지
- 파일 형식, 필수 컬럼, 최소 행 수, 출처가 기대와 다름: 수집 전체 실패

## 산출물

- `data/processed/item_product_worklist_v1.parquet`: 모든 대표 품목
- `data/processed/item_alias_to_product_v1.parquet`: 기존 별칭과 대표 품목 연결
- `data/processed/item_grouped_verified_v1.parquet`: 검증 상태를 포함한 전체 그룹 결과
- `data/processed/item_enrichment_review_queue_v1.csv`: 검토 대상 전체
- `data/sample/item_grouping_review_sample_1000.csv`: 우선 검토 샘플
- `data/processed/item_enrichment_v1_report.json`: 실행 보고서
- `data/processed/item_classification_candidates_v1.parquet`: 대표 품목별 최종 상태
- `data/processed/item_local_classification_candidates_v1.parquet`: 기관별 품목키 상태
- `data/processed/item_classification_review_queue_v1.csv`: 미승인 검토 큐
- `data/sample/item_classification_review_sample_1000.csv`: 상태별 검토 표본
- `data/mapping/item_forecast_classification_approved.csv`: 예측 사용 승인 매핑
- `data/processed/item_classification_v1_report.json`: 분류 통계와 품질 게이트

현재 실행 수치와 의료폐기물·카테터의 세부 판정 기준은
`docs/ITEM_CLASSIFICATION_V1_RESULT.md`를 참고한다.
