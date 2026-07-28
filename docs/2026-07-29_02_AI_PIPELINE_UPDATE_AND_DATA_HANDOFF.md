# AI 파이프라인 누적 업데이트 및 데이터 인계

## 1. 문서 목적

이 문서는 `raw_stock` 기반 품목 정규화부터 약성분·원자재·HSK 연결, 외부 뉴스와
가격·수출입 위험, Module C 재고 조정, 최종 예측까지 현재 구현 상태를 한 번에
인계하기 위한 문서다.

현재 작업 브랜치:

```text
experiment/material-mapping-inventory-pilot
```

핵심 원칙:

- `device/` 데이터는 사용하지 않는다.
- 기본 재고·사용량 입력은 `raw_stock/*.DAT`다.
- 분류 후보 승인, 외부 사실 검증, 시장 신호 사용, 자동 발주 가능 여부를 분리한다.
- 품목명 유사성만으로 약성분이나 HSK를 만들어내지 않는다.
- 원천·생성 대용량 데이터는 Git에 넣지 않고 코드·정책·매핑·문서만 버전 관리한다.

## 2. 현재 전체 흐름

```text
raw_stock DAT
  -> 스키마 검사 및 월별 재고 집계
  -> 기관 + 물품코드 + 물품명 정규화 키
  -> 품목명/규격/단위/제조사/성분 파싱
  -> 표준 품목·세부유형·item_group 분류
  -> 약성분 공공 데이터 매칭
  -> 품목-원자재 후보 및 승인 상태
  -> 원자재-HSK 승인 경로
  -> 뉴스·시장가격·관세청 수출입 위험
  -> Module C 수요/공급 위험
  -> 사용량 예측
  -> 위험 조정 안전재고·목표재고·권고발주
```

## 3. 구현된 변경

### 3.1 raw_stock 스키마와 정규화

- 정규화 키를 `보건기관코드_en + 물품코드 + 물품명`으로 명시했다.
- 2018·2019년처럼 컬럼 수가 다른 DAT도 핵심 키와 필수 컬럼이 있으면 처리한다.
- `구입처코드`, `구입단가`는 선택 컬럼으로 처리한다.
- 파일별 추가 컬럼은 보존하고 누락된 선택 컬럼은 빈 값으로 채운다.
- 중복 헤더, 필수 컬럼 누락, 깨진 논리행, 정규화 키 공백은 즉시 오류로 차단한다.
- 인용부호 안의 개행을 지원하는 CSV 파서를 사용한다.

현재 모델용 `stock_monthly.parquet`은 2024-01~2025-12, 3,729,983 품목-월이다.
레거시 연도 처리 기능과 현재 모델 산출 범위는 별개이며, 2018·2019년을 모델 학습에
포함하려면 전처리부터 다시 생성해야 한다.

### 3.2 품목 표준화와 분류

- 기관이 다르더라도 동일 개념을 공유할 수 있게 로컬 키와 대표 품목 개념을 분리했다.
- 품목명에서 용량, 규격, 개수, 제조사, 성분, 단위를 분리하고 원문을 함께 보존한다.
- `item_group_id`, family, subtype, specification, unit을 독립 필드로 관리한다.
- 동일 물품명이더라도 규격이나 단위가 다르면 같은 세부유형 수량으로 합치지 않는다.
- 승인 taxonomy를 세부유형 예측 출력의 정본으로 사용한다.

일괄 승인 결과:

| 항목 | 현재 값 |
|---|---:|
| 로컬 품목 후보 | 409,519 |
| 분류 승인 | 409,519 |
| 예측 운영 가능 | 65,715 |
| 자동발주 가능 | 4,969 |
| 원자재 후보 | 541,332 |
| 승인 재고-원자재 경로 | 552,255 |
| 원자재 identity 승인 경로 | 461,366 |
| 시장가격 사용 가능 경로 | 134,923 |
| 뉴스 사용 가능 경로 | 461,366 |
| 운영 가능 원자재 경로 | 69,763 |

여기서 일괄 승인은 워크플로 후보 수락이다. 외부 사실 검증이나 자동발주 승인을
뜻하지 않으며 해당 게이트는 별도 컬럼으로 유지한다.

### 3.3 약성분 데이터

한국사회보장정보원 약성분 DAT를 스트리밍 방식으로 처리한다.

매칭 우선순위:

1. 기관 + 내부 물품코드 정확 일치
2. 유일한 약품코드
3. 유일한 정규화 약품명

현재 결과:

| 항목 | 값 |
|---|---:|
| 원천 약성분 행 | 3,576,320 |
| 개행 포함 약품명 | 1,771 |
| 로컬 alias | 409,519 |
| 매칭 로컬 품목 | 165,240 |
| 기관·코드 정확 매칭 | 162,597 |
| 유일 정규화명 매칭 | 1,904 |
| 유일 약품코드 매칭 | 739 |
| 대표 품목 성분행 | 10,347 |
| 승인 성분행 | 10,333 |
| 충돌 차단 | 14 |
| 성분 사전 | 142,675 |

성분 identity는 정부 데이터 근거로 승인할 수 있지만 시장가격이나 HSK 사용은 별도
승인 경로가 있어야 한다.

### 3.4 원자재와 HSK

- 관세청 2026 HSK 정본의 10자리 통계용 코드를 검증한다.
- 기존 의료소모품 원자재 경로와 약성분 정확 명칭 경로를 함께 생성한다.
- HSK 정본 명칭이 다르거나 유효기간이 잘못된 승인 경로는 차단한다.
- 유사명만 존재하는 성분에는 HSK를 추정하지 않는다.

현재 결과:

| 항목 | 값 |
|---|---:|
| 승인 원자재-HSK 경로 | 32 |
| 승인 원자재 코드 | 28 |
| 승인 HSK | 32 |
| 기존 원자재 큐레이션 | 15 |
| 약성분 정확 HSK | 17 |
| HSK 미매칭 승인 약성분 | 913 |

### 3.5 외부 뉴스와 시장가격

- GDELT DOC API의 429·5xx·비JSON 임시 응답에 지수 백오프와 재시도를 적용했다.
- 월별 분할 캐시와 통합 쿼리 모드를 지원한다.
- DOC API 제한 시 GDELT Web NGrams 기반 실시간 후보 수집 경로를 추가했다.
- 뉴스 신호는 승인된 질병·품목 또는 원자재 경로만 재고 품목에 전달한다.
- 시장가격은 관측월, lag, mapping weight, exposure, proxy quality를 분리해 감사한다.
- 시장 신뢰도 계산에서 경로 신뢰도가 이중으로 곱해지던 문제를 수정했다.

API 키와 실행 옵션은 `.env`에 저장하며 `.env`는 Git에 포함하지 않는다.

### 3.6 수출입 위험

관세청 캐시:

| 항목 | 값 |
|---|---:|
| 기간 | 2023-01~2026-06 |
| HSK 총계 | 1,340행 |
| HSK·국가별 | 4,962행 |
| 현재 수집 국가 | CN, DE, IN, JP, US |
| 국가 커버리지 80% 통과 | 582 / 1,340 HS-월 |

위험 변수는 기존 4개에서 다음 10개로 확장했다.

1. 수입량 전년동월 감소
2. 순수입 가용량 감소
3. 수입 0 연속월
4. 수입단가 전년동월 상승
5. 6개월 수입량 변동계수
6. 6개월 단가 로그변화 변동성
7. 공급국 Top1·HHI 집중도
8. 공급국 수 전년동월 감소
9. 순수입 노출
10. 수출량 전년동월 급증

상세 공식과 가중치는
`docs/2026-07-29_01_TRADE_VARIABLE_EXPANSION_AND_INVENTORY_IMPACT.md`에 있다.

현재 산출:

| 항목 | 값 |
|---|---:|
| HSK 특징 | 1,340행 |
| 실제 재고 관측 품목-월 점수 | 787,723행 |
| 무역점수 품목 | 98,695 |
| 최신 품목-HS 감사 경로 | 52,764행 |
| 최대 HSK 위험 | 0.8500 |
| 최대 품목 위험 | 0.6904 |

품목이 존재하지 않는 월의 직교곱은 제거했고, 실제 `stock_monthly` 품목-월 키에
존재하는 경우만 Module C로 전달한다.

### 3.7 Module C와 최종 재고

현재 설정은 `module-c-v1.2`다. 수출입은 기존 공급위험에 최대 25% overlay로
결합한다.

| 항목 | 값 |
|---|---:|
| Module C 점수 | 1,202,451행 |
| 무역 신호 적용 | 787,723행 |
| 최대 무역 공급기여 | 0.1726 |
| Module C 경보 | 0행 |
| 최종 로컬 예측 | 163,229행 |
| 세부유형 집계 출력 | 35,819행 |

동일한 예측과 정책에서 무역 신호만 0으로 둔 반사실 비교:

| 항목 | 값 |
|---|---:|
| 무역 노출 최신 예측 | 34,326행 |
| 목표재고 증가 | 32,977행 |
| 권고발주 증가 | 3,271행 |
| 목표재고 증가 95백분위 | 8.5746 |
| 목표재고 최대 증가 | 226.6528 |

품목 단위가 서로 다르므로 수량 합계는 품목 간 총물량으로 해석하지 않는다.

### 3.8 예측 방법 비교

현재 최신 백테스트 605,437행:

| 방식 | WAPE | BIAS % |
|---|---:|---:|
| Module C 모델 후보 | 37.5057 | -4.3993 |
| 사용량 Tweedie | 37.5970 | -4.5104 |
| 현재 `predicted_usage` | 38.8735 | -15.6570 |
| 3개월 이동평균 | 40.0211 | -6.0372 |

검증 구간에서 선택된 운영 모델과 최신 백테스트 후보의 순위는 다를 수 있다.
모델 선택은 테스트 구간 성능을 보고 바꾸지 않으며 별도 보정 구간에서 결정한다.

조합 실험은 2025-08~09를 보정, 2025-10~12를 홀드아웃으로 사용했다.

| 항목 | 결과 |
|---|---:|
| 패턴 라우터 홀드아웃 WAPE | 37.5843 |
| 90% 서비스수준 positive-row 달성률 | 89.6441% |
| 90% 서비스수준 unit fill rate | 92.8263% |
| 90% 서비스수준 target/actual | 1.5760 |

조합 정책은 `holdout_evaluated_not_operational` 상태이며 자동 운영 전 별도 승인이
필요하다.

## 4. 주요 데이터 위치

### Git에 포함되는 정책·사전

| 파일 | 설명 |
|---|---|
| `data/mapping/item_family_taxonomy.csv` | 품목 family/subtype 정본 |
| `data/mapping/item_forecast_classification_approved.csv` | 기존 엄격 승인 분류 |
| `data/mapping/item_manual_standardization_decisions.csv` | 수동 표준화 결정 |
| `data/mapping/item_bulk_approval_policy.json` | 일괄 승인 정책 |
| `data/mapping/material_hs_mapping.csv` | 승인 원자재-HSK 경로 |
| `data/mapping/module_c_risk_weights.json` | Module C v1.2 가중치 |

### 로컬 생성 전체 데이터

| 파일 | 설명 |
|---|---|
| `data/processed/stock_monthly.parquet` | 월별 재고·사용량 |
| `data/processed/item_integrated_classification_v2.parquet` | 전체 통합 품목 분류 |
| `data/processed/drug_ingredient_dictionary_v1.parquet` | 약성분 사전 |
| `data/processed/item_drug_ingredient_enrichment_v1.parquet` | 품목-약성분 연결 |
| `data/processed/stock_item_material_mapping_bulk_approved.parquet` | 활성 품목-원자재 경로 |
| `outputs/hs_trade_risk_features.csv` | HSK 월별 10개 위험 |
| `outputs/stock_trade_risk_scores.csv` | 품목-월 무역위험 |
| `outputs/stock_module_c_risk_scores.csv` | 통합 외부위험 |
| `outputs/stock_predictions.csv` | 최종 예측·목표재고·권고발주 |
| `outputs/stock_predictions_by_subtype.csv` | 세부유형·단위 집계 |

### 1,000행 검토 표본

| 파일 | 설명 |
|---|---|
| `data/sample/drug_ingredient_enrichment_sample_1000.csv` | 약성분 매칭 |
| `data/sample/item_bulk_classification_approval_sample_1000.csv` | 분류 승인 |
| `data/sample/item_bulk_material_approval_sample_1000.csv` | 원자재 승인 |
| `data/sample/material_hs_mapping_sample_1000.csv` | 원자재-HSK |
| `data/sample/hs_trade_risk_features_sample_1000.csv` | 수출입 특징 |
| `data/sample/trade_inventory_impact_sample_1000.csv` | 재고 영향 상위 |
| `data/sample/module_c_supply_risk_quality_sample_1000.csv` | 공급위험 품질 |

`data/processed/`, `data/sample/`, `outputs/`, `models/`, `raw_stock/`, `pdf/`는
대용량·원천·생성 데이터이므로 Git에서 제외한다.

## 5. 재현 명령

```bash
conda run -n teamlex python -m src.item_normalization --full
conda run -n teamlex python -m src.drug_ingredient
conda run -n teamlex python -m src.item_integrated_pipeline --with-excel --sample-size 1000
conda run -n teamlex python -m src.item_bulk_approval --apply
conda run -n teamlex python -m src.trade.material_hs_mapping
conda run -n teamlex python -m src.news.news_risk_scorer
conda run -n teamlex python -m src.commodity.commodity_risk_scorer
conda run -n teamlex python -m src.trade.trade_risk_scorer --provider csv
conda run -n teamlex python -m src.module_c.pipeline
conda run -n teamlex python -m src.feature_engineering
conda run -n teamlex python -m src.modeling.prediction
conda run -n teamlex python -m src.trade.trade_inventory_impact
```

## 6. 검증

```text
python -m unittest discover -s tests
195 tests passed
1 test skipped
```

추가 데이터 무결성:

- HSK 특징 중복 키: 0
- 무역 품목-월 중복 키: 0
- Module C 품목-월 중복 키: 0
- Module C 전체 설정 버전: `module-c-v1.2`
- 최종 예측 전체 설정 버전: `module-c-v1.2`

## 7. 남은 주의사항

1. 수출입 국가 캐시는 현재 5개국이다. 설정된 20개국 증분 수집이 필요하다.
2. HSK 미매칭 약성분 913개는 공식 근거 전까지 무역위험에 사용하지 않는다.
3. 현재 무역 가중치는 정책 초기값이며 실제 품절·구매단가·리드타임으로 보정해야 한다.
4. 공급위험 품질검사 결과 `review`와 `blocked` 항목은 자동 발주에 사용하지 않는다.
5. 표준 단위 변환이 없으므로 서로 다른 `unit_code` 수량을 합산하지 않는다.
6. 원천 DAT와 API 키는 Git에 커밋하지 않는다.
