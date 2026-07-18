# 원자재·리스크 메타코드 파이프라인 통합

현재 운영 후보 생성 버전은 `combined-material-v2.1`이다. 전체 비교 수치와 경계 사례는
`ITEM_INTEGRATED_PIPELINE_V2_1_RESULT.md`를 참고한다.

## 입력

```text
data/processed/item_product_worklist_v1.parquet
+ data/processed/item_representative_attributes_v1.parquet
```

두 파일은 `representative_item_id`로 일대일 병합한다. 전체 입력은 `raw_stock`에서
생성하며 `device/` 데이터는 읽지 않는다.

## 흐름

```text
구조화 family/subtype/spec/unit
-> 이름 규칙 대안과 충돌 비교
-> 공급 클러스터
-> 원자재 정체 후보
-> 원자재 공급위험
-> 수요 트리거
-> parent concept 후보
-> 분류 승인 게이트
```

| 축 | 컬럼 | 용도 |
|---|---|---|
| 품목 정체 | `effective_item_family_id` | 표준 품목 후보 |
| 규격 | `effective_specification` | G, mL, 규격 등 |
| 재고 단위 | `effective_unit_code` | EA, ROLL, TABLET 등 |
| 원자재 | `raw_material_meta_code` | 의료용품→원자재→가격/API 후보 |
| 공급위험 | `raw_material_risk_meta_code` | 원자재·공급망 뉴스 후보 |
| 수요위험 | `demand_risk_meta_code` | 질병·계절·재난 뉴스 후보 |

`24G`는 규격이고 `100개`는 포장수량이다. 두 값은 각각 `needle_gauge`와
`pack_quantity`로 분리한다.

## 실행

전체 분류·원자재·샘플을 한 번에 생성한다.

```bash
conda run -n teamlex python -m src.item_integrated_pipeline \
  --with-excel \
  --sample-size 1000
```

원자재 단계만 실행할 때는 다음 명령을 사용한다.

```bash
conda run -n teamlex python -m src.material_pipeline --with-excel
```

## 핵심 산출물

```text
data/processed/item_integrated_classification_v2.csv
data/processed/item_integrated_classification_v2.parquet
data/sample/item_integrated_classification_sample_1000.csv
data/processed/item_material_pipeline/item_material_event_mapping_full.csv
data/processed/item_material_pipeline/item_parent_concept_grouping_full.csv
data/processed/item_material_pipeline/meta_code_glossary_full.csv
data/processed/item_material_pipeline/meta_code_glossary_full.xlsx
data/processed/item_integrated_classification_v2_report.json
```

## 선택 규칙

1. 검증된 구조화 family
2. 검증된 단일 유효성분
3. 공식 기준표의 정확 규칙 또는 고신뢰 문맥
4. 로컬 구조화 family·subtype·specification
5. 이름·브랜드·접미사 규칙

이름 규칙은 `name_rule_*`에 보존한다. 선택값과 다르면
`family_conflict_flag=true`이고 사람 검토가 필요하다. parent concept은 예측 집계
후보일 뿐 원래 family를 덮어쓰지 않는다.

## 승인 게이트

자동 산출물에는 `raw_material_verified`가 없고 모든 원자재 결과는
`material_review_status=needs_review`다. `identified`도 제품 BOM 검증을 뜻하지 않는다.

뉴스·원자재 scorer는 `data/mapping/stock_item_material_mapping.csv`에서 다음 조건을
모두 만족하는 행만 읽는다.

- `review_status=approved`
- 공식 레코드 또는 제조사 문서 근거 존재
- 검토자·검토시각·버전·출처 존재
- 관계 유형과 사용 부위 존재
- 유효한 `mapping_weight`와 `exposure_score`
- 중복되지 않는 `기관::부서::물품코드 + 원자재` 관계

복합 혈당측정 세트는 구성품 BOM과 수량비를 확인하기 전
`MATERIAL_UNSPECIFIED`, `composite_set_requires_bom`으로 유지한다.

## 품질 게이트

- 대표품목 행 수와 ID 보존
- parent concept 행 수와 ID 보존
- glossary 코드 참조 완전성
- `(category, meta_code)` 중복 금지
- 비성분 sentinel의 원자재 코드 승격 금지
- 모든 원자재 후보의 `needs_review` 유지
- 전체·샘플 ID 중복 금지

원격 소스와 채택·기각 내역은 `pipelines/item_material/UPSTREAM.md`에 기록한다.
