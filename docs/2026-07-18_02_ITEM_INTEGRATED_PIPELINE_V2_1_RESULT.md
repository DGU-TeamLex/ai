# 품목·원자재 통합 파이프라인 v2.1 결과

생성일: 2026-07-18  
입력: `raw_stock`에서 만든 대표품목 worklist와 구조화 속성  
금지 입력: `device/` 데이터는 사용하지 않음

## 1. 적용한 구조

```text
raw_stock 16,265,602행
-> 기관별 별칭 409,519행
-> 대표품목 worklist 101,546행
-> 구조화 속성 101,546행 일대일 병합
-> family 선택 + 이름 규칙 대안/충돌 보존
-> subtype/specification/unit 결합
-> 공급 클러스터
-> 원자재 후보 + 공급위험 + 수요트리거
-> 예측 집계용 parent concept 후보
-> 분류 승인 게이트
-> 전체 통합본 + 주목 사례 1,000건
```

family 선택 우선순위는 다음과 같다.

1. 검증된 구조화 family
2. 검증된 단일 유효성분
3. 사용자 공식 기준표의 정확 규칙 또는 고신뢰 문맥 규칙
4. 로컬 구조화 family·subtype·specification
5. 이름·브랜드·접미사 규칙

이름 규칙 결과는 선택값과 달라도 삭제하지 않고 `name_rule_*`에 남긴다. 충돌은
`family_conflict_flag`, `family_conflict_reason`으로 검토할 수 있다.

## 2. 기존·팀원 구현과 비교

검토한 원격 버전은 다음과 같다.

| 구분 | 저장소/커밋 |
|---|---|
| 기존 로컬 기반 | `wep-stock-data-normalization` `94b5663` |
| 폐기 안내가 추가된 이전 저장소 | `wep-stock-data-normalization` `938fa3e` |
| 팀원 canonical 저장소 | `wep-stock-item-material-pipeline` `74d3982` |

canonical 구현의 제네릭 접미사와 상위개념 계층은 유용했다. 반면 이름-only 결과를
전체 데이터에 직접 적용하면 산소마스크→유류, 채혈침→검사스트립/비성분 sentinel,
알코올솜→의약품 성분, 주사침 폐기통→주사침, `Lanset`→lansoprazole 같은 충돌이
재현됐다. 따라서 확대 사전을 통째로 덮어쓰지 않고 다음처럼 결합했다.

| 영역 | v2.1 선택 |
|---|---|
| 구조화 family/subtype/spec/unit | 기존 로컬 결과 우선 |
| 공식 기준표·명시 복합문맥 | 제한된 고신뢰 override |
| canonical 제네릭 접미사 | 의약품 대분류에서만 미검증 후보로 사용 |
| canonical parent concept | 별도 계층으로 사용, 품목 identity를 덮어쓰지 않음 |
| 기존 27개 공급 클러스터·3축 위험 | 유지 |
| 원격 이름 사전 결과 | `name_rule_*` 감사 대안으로 보존 |
| 원자재 승인 | 자동 승인하지 않음 |

## 3. 경계 규칙

- `Lancet`, `Lancets`, `Lanset`, 란셋·난셋·랜싯은 `BLOOD_LANCET`으로 통합한다.
- 혈당검사지·란셋·알코올솜 중 둘 이상이 함께 적힌 재고명은
  `BLOOD_GLUCOSE_TESTING_SET`으로 분리한다.
- 혈당계까지 포함한 복합명은 `BLOOD_GLUCOSE_METER_KIT`으로 분리한다.
- 복합세트 원자재는 BOM과 구성비 확인 전 `MATERIAL_UNSPECIFIED`로 둔다.
- `24G`는 바늘 굵기인 `needle_gauge`/specification이며 `pack_quantity`가 아니다.
- `100개`, `100EA` 같은 표현만 포장수량으로 분리한다.
- `주사침 100EA/통`의 `통`은 포장단위이며 폐기용기 조건이 아니다.
- `주사침폐기물통`, needle box는 의료폐기물 전용용기다.
- `멸균거즈(개별포장)`는 거즈이며 EO 멸균포장재가 아니다.
- Foley·Nelaton·흡인·산소·중심정맥 카테터는 일반 `CATHETER`로 유지한다.
- angio/안지오/엔지오 또는 카테터와 G 규격이 명시된 경우만 angio 후보로 올린다.

## 4. 전체 실행 결과

| 지표 | v2.1 |
|---|---:|
| 대표품목 입력/출력 | 101,546 / 101,546 |
| 구조화 속성 결합 | 101,546 |
| family 이름 규칙 | 93,140 |
| 고신뢰 문맥 규칙 | 3,521 |
| 공식 기준표 규칙 | 1,334 |
| 로컬 구조화 family | 3,468 |
| 검증된 family/성분 사전 | 83 |
| 보존된 family 충돌 | 134 |
| family 미식별 basis | 44,004 |
| 원자재 identified | 57,445 |
| 원자재 group_coarse | 6,263 |
| 원자재 unspecified | 37,838 |
| 복합세트 BOM 검토 | 5 |
| parent concept | 740 |
| 분류 승인 대표품목 | 1,177 |
| 운영 승인 원자재 매핑 | 0 |

기존 `94b5663` 통합본과 비교하면 대표품목 행은 그대로 보존하면서 미식별 basis가
44,237→44,004로 233건 줄었고, 원자재 `identified`가 57,349→57,445로 96건 늘었다.
대분류 개략값은 6,469→6,263으로 206건 줄었다. 기존 산출물 감사에서 발견된
비성분 sentinel 원자재 누출은 116건에서 0건이 됐다.

`identified`는 제품의 실제 BOM이 검증됐다는 뜻이 아니다. family 또는 성분에 따라
원자재 후보를 만들 수 있다는 뜻이며, 모든 행은 여전히 `needs_review`다.

## 5. 품질 게이트

최종 실행에서 다음 조건을 모두 통과했다.

- 대표품목 101,546행 보존, ID 중복 0
- parent concept 101,546행 보존, ID 불일치 0
- 샘플 1,000행, ID 중복 0
- `raw_material_verified` 컬럼 없음
- 모든 원자재 후보 `material_review_status=needs_review`
- 비성분 sentinel 원자재 코드 0
- 사용된 모든 원자재·공급위험·수요위험 코드가 glossary에 존재
- 전체 단위 테스트 85개 통과

문맥 감사 결과도 다음 조건을 만족했다.

- lancet 관련 1,052건: 채혈침 또는 명시 복합세트 1,052건
- 산소마스크 관련 89건: `MEDICAL_MASK` 89건
- 알코올솜·스왑 관련 1,063건: 알코올스왑 또는 명시 복합세트 1,063건
- 명시 주사침 24G 7건: `INJECTION_NEEDLE` 7건
- 이오메론을 EO 포장재로 분류한 행 0
- 멸균거즈를 EO 포장재로 분류한 행 0

## 6. 산출물

| 파일 | 용도 |
|---|---|
| `data/processed/item_integrated_classification_v2.csv` | 전체 101,546건, 사람이 열기 쉬운 CSV |
| `data/processed/item_integrated_classification_v2.parquet` | 전체 101,546건, 분석·조인 권장본 |
| `data/sample/item_integrated_classification_sample_1000.csv` | 충돌·규격·저근거·고활동 품목 검토 샘플 |
| `data/processed/item_material_pipeline/item_material_event_mapping_full.csv` | family·원자재·위험 3축 후보 |
| `data/processed/item_material_pipeline/item_parent_concept_grouping_full.csv` | 예측 집계용 상위개념 후보 |
| `data/processed/item_material_pipeline/meta_code_glossary_full.xlsx` | 원자재·위험 코드 사전 |
| `data/processed/item_integrated_classification_v2_report.json` | 전체 수치·비교·품질 게이트 |

전체 CSV의 `effective_*`는 통합 후보값이다. 예측 시계열 병합에는
`forecast_series_definition_key`를 검토 후보로 쓰되, 승인 전 서로 다른 실제 제품을
합쳐 학습하면 안 된다.

1,000건 샘플에는 family 충돌 134건, sentinel 차단 34건, 복합세트 5건을 전부 포함하고
나머지를 규격 파싱·저근거·고활동 층화표본으로 채웠다.

## 7. 재실행

```bash
conda run -n teamlex python -m src.item_integrated_pipeline \
  --with-excel \
  --sample-size 1000
```

원자재 후보만 다시 만들 때는 다음 명령을 사용한다.

```bash
conda run -n teamlex python -m src.material_pipeline --with-excel
```

## 8. 다음 검토 순서

1. 샘플에서 `family_conflict_structured_preferred`와 복합세트 5건을 먼저 검토한다.
2. `context_explicit_rule`의 신규 변형을 표본 감사한다.
3. `active_high`이면서 unresolved인 품목을 공식 제품 DB·제조사 문서로 조사한다.
4. 의료용품 원자재는 IFU/SDS/UDI 근거와 관계 유형·사용 부위를 기록한다.
5. 승인된 행만 `stock_item_material_mapping.csv`에 반영한다.

현재 미식별 44,004건과 원자재 미상 37,838건은 자동으로 확정하지 않았다. 외부 근거가
없는데 값을 채우는 것보다 검토 큐로 남기는 편이 재고 예측과 원자재 위험 연결에 안전하다.
