# 품목 정규화·분류 작업 인수인계

작성일: 2026-07-16  
기준 입력: `raw_stock/*.DAT` 10개 파일  
사용하지 않는 입력: `device/` 폴더 전체

## 1. 작업 목적

기관마다 다르게 기록된 품목명을 대표 품목으로 통합하고 다음 계층으로 분리했다.

```text
DB item_group
-> 표준 품목 family
-> 세부유형 subtype
-> 규격 specification
-> 단위 unit
-> 공식 제품 identity 또는 검토 후보
-> 승인된 분류만 재고량 예측에 연결
```

근거가 없는 항목을 강제로 확정하지 않는 것이 핵심 원칙이다. 이름 규칙이나 원자재
파이프라인이 제시한 값은 후보로 유지하고, 공식 근거 게이트를 통과한 행만
`review_status=approved`로 기록한다.

## 2. 처리 규모

| 단계 | 건수 |
|---|---:|
| raw_stock 원본 행 | 16,265,602 |
| 기관·품목코드·원본명별 별칭 | 409,519 |
| 기관과 무관한 대표 품목 | 101,546 |
| 외부 근거 승인 대표 품목 | 1,162 |
| 승인 기관별 품목키 | 4,948 |
| 승인 taxonomy | 59 |
| 미승인 검토 큐 | 100,384 |

정규화 전체 팩트의 별칭 조인 누락과 품질 게이트 오류는 모두 0건이다.

## 3. 최종 상태

| 분류 상태 | 대표 품목 수 | 처리 원칙 |
|---|---:|---|
| `approved_external_item` | 1 | 공식 제품명·품목코드·성분·함량 일치 |
| `approved_external_family` | 1,161 | 가족·규격·단위와 공식 분류체계 일치 |
| `candidate_complete` | 3,802 | 필드는 완결됐지만 승인 근거 부족 |
| `candidate_family` | 52,401 | 가족 후보만 사용 가능 |
| `group_only` | 6,362 | DB item_group만 판정 |
| `unresolved` | 37,700 | 안전하게 판정할 근거 없음 |
| `conflict` | 119 | 품목군·가족 후보 충돌 |

모든 대표 품목은 위 상태 중 하나를 가진다. `candidate_complete`도 자동 승인 데이터가
아니므로 운영 예측이나 원자재 위험 점수에 바로 사용하면 안 된다.

## 4. 적용한 주요 기준

### 주사기·수액세트

- 주사기는 바늘 게이지보다 사용량 기준 용량을 우선한다.
- `1mL/23G`는 `주사기(사용량 기준) / 1mL / EA`다.
- 수액세트는 부착 바늘의 G나 수액 용량으로 쪼개지 않고 `수액세트 / EA`로 통합한다.
- `1,000mL`, `2리터`, `3밀리리터` 같은 표기를 표준 용량으로 바꾼다.

### 카테터

- `IV카테터`, `Angiocath`, G/게이지가 명시된 혈관 카테터는
  `카테터(angio needle)`로 분류한다.
- 나비바늘은 별도 subtype으로 유지한다.
- Foley, 흡인, 중심정맥 등 일반 카테터는 제품 종류가 다양하므로 자동 승인하지 않는다.
- `24(G)`, `24게이지`도 `24G`로 정규화한다.

### 의료폐기물 용기

- `주사침통`, `주사침 폐기통`은 주사침이 아니라 `WASTE` 용기다.
- `손상성`, `침통`, `needle box`, `PVC`, `플라스틱`: 합성수지류 상자형 후보
- `PE`, `폴리에틸렌`: PE 봉투형 후보
- `봉투`, `비닐`: PE로 좁히지 않은 합성수지류 봉투형 후보
- `종이`, `골판지`: 골판지류 상자형 후보
- 일반 `용기`, `통`, `박스`: 재질을 추정하지 않고 subtype을 비워 둔다.
- DB 정책에 따라 `WASTE / is_forecastable=f`를 유지한다.

### 기관별 코드

- 기관이 달라도 이름·가족·규격이 같으면 같은 대표 품목으로 묶을 수 있다.
- 기관별 원본 코드는 `item_alias_to_product_v1.parquet`에 보존한다.
- 원천 품목코드 자체에 `::`가 포함될 수 있으므로 구분자 개수로 코드를 분해하지 않는다.
- 한 `local_item_key`가 서로 다른 taxonomy에 연결되면 승인하지 않는다.

## 5. 외부 검증 근거

- 식약처 의약품 제품 허가정보:
  <https://www.data.go.kr/data/15095677/openapi.do>
- 식약처 의약품안전나라: <https://nedrug.mfds.go.kr/>
- 의료기기 품목 및 품목별 등급에 관한 규정:
  <https://www.law.go.kr/LSW/admRulLsInfoP.do?admRulSeq=2100000275656>
- 폐기물관리법 시행규칙 별표 5:
  <https://www.law.go.kr/flDownload.do?bylClsCd=110201&flSeq=162812925&gubun=>
- 식약처 의료기기 표준코드별 제품정보:
  <https://www.data.go.kr/data/15073875/openapi.do>

NEDrug 공식 페이지 9개를 응답 해시와 조회 시각과 함께 캐시했다. 연결된 대표 품목은
111개지만 제품명 핵심부, 품목코드, 성분, 함량을 모두 만족한 제품 단위 승인은 1개다.
나머지는 공식 페이지가 있다는 이유만으로 승인하지 않았다.

## 6. 원자재 매핑 상태

원자재 파이프라인은 대표 품목 101,546개 전체에 후보를 생성한다. 그러나 모든 행의
`material_review_status`는 `needs_review`이며 운영 원자재 매핑으로 승인되지 않았다.

특히 다음 근거는 확정값으로 사용하면 안 된다.

- `general_knowledge_unverified`
- `unresolved`
- `group_coarse`
- 제품별 IFU, SDS, UDI 근거가 없는 family 기본 재질

뉴스·선물가격 위험 점수에는 별도로 승인된
`data/mapping/stock_item_material_mapping.csv`만 사용할 수 있다.

## 7. 주목 데이터 1,000건

검토 파일:

```text
data/sample/item_classification_noteworthy_1000.csv
```

대표 품목 1,000개가 중복 없이 들어 있으며 UTF-8 BOM CSV로 저장했다. 단순 무작위 표본이
아니라 업무 위험과 사용량을 기준으로 선정했다.

| `attention_category` | 건수 | 주목 이유 |
|---|---:|---|
| `catheter_boundary` | 180 | angio와 일반 카테터 경계 |
| `medical_waste_boundary` | 152 | 용기 형태·재질 경계 |
| `candidate_complete_unapproved` | 120 | 완결 후보지만 근거 부족 |
| `classification_conflict` | 119 | 품목군·가족 충돌 전부 포함 |
| `official_drug_evidence_review` | 111 | NEDrug 연결 후보 전부 포함 |
| `unresolved_high_usage` | 70 | 고사용량 미분류 |
| `material_evidence_review` | 60 | 원자재 근거 미검증 |
| `approved_family_audit` | 50 | 자동승인 규칙 감사 표본 |
| `candidate_family_high_usage` | 50 | 고사용량 가족 후보 |
| `priority_fill` | 48 | 상태·사용량 우선 추가 항목 |
| `group_only_high_usage` | 40 | 고사용량 item_group 단독 판정 |

CSV의 첫 네 컬럼을 먼저 사용한다.

```text
attention_rank
attention_category
attention_reason_ko
recommended_review_action_ko
```

그 뒤에는 원본명, 현재 상태, 후보·선택 taxonomy, 식약처 근거, 원자재 후보, 사용량,
기관 수, 원본명 예시가 포함된다.

권장 검토 순서:

1. `classification_conflict` 119건을 먼저 해결한다.
2. `official_drug_evidence_review`에서 제품명·성분·함량을 공식 페이지와 대조한다.
3. 폐기물·카테터 경계 항목으로 정규화 규칙의 precision을 감사한다.
4. `candidate_complete_unapproved`에 공식 근거를 추가한다.
5. 고사용량 미분류를 공식 코드 마스터에 연결한다.
6. 원자재는 품목 승인 후 별도의 UDI·IFU·SDS 검토를 진행한다.

표본 재생성 명령:

```bash
python -m src.item_review_export
```

## 8. 재고량 모델 연결 결과

- 승인 분류만 기존 로컬 예측에 결합한다.
- 출력 기준은 `품목 family + subtype + specification + unit`이다.
- `base_stock`은 보호기간 수요와 안전재고를 합한 기본재고량이다.
- 최종 세부유형 예측은 2,348행이다.
- 승인 분류와 매칭된 로컬 예측은 2,396행이다.
- 현재 예측 사용량 커버리지는 1.74%다.
- 단위가 다른 행은 자동 환산하거나 합산하지 않는다.

커버리지가 낮은 것은 오류가 아니라 엄격한 승인 게이트의 결과다. 검토 큐 승인이 늘어나면
모델 재학습 없이 기존 로컬 예측에 새 분류를 즉시 적용할 수 있다.

## 9. 주요 산출물

| 경로 | 내용 |
|---|---|
| `data/processed/item_alias_candidates_v0.3.parquet` | 기관별 별칭 정규화 후보 전체 |
| `data/processed/stock_with_item_normalization_v0.3.parquet` | 1,626만 재고 행 정규화 결과 |
| `data/processed/item_product_worklist_v1.parquet` | 대표 품목 101,546개 |
| `data/processed/item_classification_candidates_v1.parquet` | 대표 품목 분류 상태 전체 |
| `data/processed/item_classification_review_queue_v1.csv` | 미승인 검토 큐 100,384개 |
| `data/sample/item_classification_noteworthy_1000.csv` | 이번 인수인계용 주목 표본 |
| `data/mapping/item_manual_standardization_decisions.csv` | 승인 결정 원장 1,162개 |
| `data/mapping/item_family_taxonomy.csv` | 승인 taxonomy 59개 |
| `data/mapping/item_forecast_classification_approved.csv` | 승인 기관별 품목키 4,948개 |
| `outputs/stock_predictions_by_subtype.csv` | 세부유형별 기본·목표재고량 |

## 10. 전체 재실행

```bash
python -m src.item_normalization --full
python -m src.item_enrichment build-worklist
python -m src.item_enrichment match
python -m src.material_pipeline
python -m src.item_classification fetch-official-web --delay 0.15
python -m src.item_classification build
python -m src.item_review_export
python -m src.modeling.classified_prediction
python -m unittest discover -s tests
```

공공데이터포털 마스터 API 전체 수집에는 `DATA_GO_KR_SERVICE_KEY`가 필요하다. 키가 없으면
제품 신원을 추정으로 승인하지 말고 현재 검토 상태를 유지해야 한다.

최종 검증에서는 전체 테스트 69개가 통과했다.
