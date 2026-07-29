# 품목 분류 v1 실행 결과

## 범위

- 입력: `raw_stock/*.DAT` 10개 파일, 16,265,602행
- 기관별 품목 별칭: 409,519개
- 기관과 무관한 대표 품목: 101,546개
- 정규화 규칙: `item-normalization-v0.4`
- 분류 규칙: `classification-v1.0`
- `device/` 폴더 데이터: 사용하지 않음

## 최종 상태

| 상태 | 대표 품목 수 | 의미 |
|---|---:|---|
| `approved_external_item` | 1 | 식약처 제품명, 품목코드, 성분, 함량까지 일치 |
| `approved_external_family` | 1,161 | 명칭의 가족·규격·단위와 공식 분류체계가 일치 |
| `candidate_complete` | 3,802 | 세부 필드는 완결됐으나 승인 근거가 부족 |
| `candidate_family` | 52,401 | 가족 후보만 있으며 세부 검토 필요 |
| `group_only` | 6,362 | DB `item_group_id`만 판정 |
| `unresolved` | 37,700 | 가족을 안전하게 판정할 근거가 없음 |
| `conflict` | 119 | 이름·코드·기관별 후보가 충돌 |

전체 대표 품목은 위 상태 중 정확히 하나를 가진다. 미승인 행을 임의 분류로 채우지 않는다.

가족 단위 승인 1,161개는 다음과 같다.

| 가족 | 승인 대표 품목 수 |
|---|---:|
| 일회용 주사기 | 450 |
| 채혈침 | 292 |
| 주사침 | 163 |
| 카테터(angio needle) | 119 |
| 수액세트 | 76 |
| 의료폐기물 전용용기 | 38 |
| Urine bag | 23 |

일반 카테터는 제품 종류가 다양하므로 자동 승인하지 않는다. `IV카테터`, `Angiocath`,
G/게이지가 명시된 혈관 카테터만 `카테터(angio needle)`로 분류한다.

## 외부 검증

사용한 1차 근거는 다음과 같다.

- 식약처 의약품 제품 허가정보:
  <https://www.data.go.kr/data/15095677/openapi.do>
- 식약처 의약품안전나라 제품 페이지: <https://nedrug.mfds.go.kr/>
- 의료기기 품목 및 품목별 등급에 관한 규정:
  <https://www.law.go.kr/LSW/admRulLsInfoP.do?admRulSeq=2100000275656>
- 폐기물관리법 시행규칙 별표 5:
  <https://www.law.go.kr/flDownload.do?bylClsCd=110201&flSeq=162812925&gubun=>
- 식약처 의료기기 표준코드별 제품정보:
  <https://www.data.go.kr/data/15073875/openapi.do>

의약품안전나라 공식 페이지 9개를 캐시했고 111개 대표 품목과 연결했다. 제품명 핵심부,
품목코드, 성분, 함량이 모두 일치한 행만 제품 단위 승인했기 때문에 최종 제품 승인은
1개다. 나머지는 후보 상태를 유지한다.

의료폐기물은 법령 기준에 따라 명칭 근거가 있을 때만 다음 세부유형을 정한다.

- `손상성`, `침통`, `needle box`, `PVC`, `플라스틱`: 합성수지류 상자형
- `PE`, `폴리에틸렌`: PE 봉투형
- `봉투`, `비닐`: 재질을 PE로 좁히지 않은 합성수지류 봉투형
- `종이`, `골판지`: 골판지류 상자형
- 일반 `용기`, `통`, `박스`: 가족만 유지하고 재질 세부유형은 미확정

## 산출물

| 경로 | 내용 |
|---|---|
| `data/processed/item_classification_candidates_v1.parquet` | 대표 품목 전체 상태 |
| `data/processed/item_local_classification_candidates_v1.parquet` | 기관별 품목키 전체 상태 |
| `data/processed/item_classification_review_queue_v1.csv` | 미승인 100,384개 검토 큐 |
| `data/sample/item_classification_review_sample_1000.csv` | 우선순위·상태별 1,000개 표본 |
| `data/sample/item_classification_noteworthy_1000.csv` | 충돌·경계·고사용량 중심 인수인계 표본 |
| `data/mapping/item_manual_standardization_decisions.csv` | 근거 게이트를 통과한 결정 원장 |
| `data/mapping/item_family_taxonomy.csv` | 승인 taxonomy 59개 |
| `data/mapping/item_forecast_classification_approved.csv` | 승인 기관별 품목키 4,948개 |
| `data/external/official/mfds_nedrug_web.parquet` | 식약처 웹 근거 캐시 9개 |
| `data/processed/item_classification_v1_report.json` | 실행 통계와 품질 게이트 |

## 재실행

```bash
python -m src.item_normalization --full
python -m src.item_enrichment build-worklist
python -m src.item_enrichment match
python -m src.material_pipeline
python -m src.item_classification fetch-official-web --delay 0.15
python -m src.item_classification build
python -m src.item_review_export
python -m src.modeling.classified_prediction
```

공공데이터포털 마스터 API까지 수집하려면 `DATA_GO_KR_SERVICE_KEY`가 필요하다. 키가 없으면
NEDrug 공개 페이지 캐시와 법령 근거만 사용하며, UDI 제품 신원을 추정으로 승인하지 않는다.

## 현재 제한

- 원재료 파이프라인의 101,546개 결과는 전부 후보이며 별도 승인 전에는 위험 점수에 쓰지 않는다.
- 현재 로컬 예측 163,229행 중 승인 분류와 일치한 행은 2,396개다.
- 세부유형 예측 사용량 커버리지는 1.74%이고 최종 출력은 2,348행이다.
- 분류 커버리지를 높이려면 UDI/의약품 마스터 API 키 수집과 100,384개 검토 큐 처리가 필요하다.
