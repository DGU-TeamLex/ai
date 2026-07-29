# 품목 재분류 평가와 가중치 대안

작성일: 2026-07-22

## 1. 결론

최신 규칙으로 `raw_stock` 전체 16,265,602행을 다시 정규화하고, 409,519개 로컬 품목
별칭과 101,546개 대표 품목을 재생성했다. 통합 분류와 검토 샘플 1,000건도 함께
갱신했다.

현재 확인할 수 있는 성능은 다음 세 종류다.

| 평가 | 결과 | 의미 |
|---|---:|---|
| 이전 승인 5필드 회귀 일치도 | 100.00% | 기존 승인 1,177건이 최신 실행에서도 동일한지 확인 |
| 외부 표준 ID pairwise F1 | 92.69% | 현재 대표 품목 묶음과 `regulazation` 묶음의 쌍 단위 일치도 |
| 외부 표준 ID B-cubed F1 | 93.49% | 큰 군집 쏠림을 완화한 품목별 군집 일치도 |
| 독립 인력 검수 정확도 | 측정 불가 | 현재 1,000건에 승인 정답 라벨이 없음 |

따라서 `정확도 100%`라고 발표하면 안 된다. 100%는 동일 규칙 계열의 이전 승인셋을
재현한 회귀 안정성이다. 외부 군집 F1도 사람 정답 정확도가 아니라 정규화 결과 간 비교다.

운영 분류 가중치와 Module C 가중치는 변경하지 않았다. 실적 정답 없이 가중치를 바꾸면
숫자만 달라지고 정확도 개선을 입증할 수 없기 때문이다. 대신 검토 우선순위와 재고 영향의
민감도 대안을 출력했다.

## 2. 이번에 다시 실행한 범위

```text
raw_stock 10개 DAT, 16,265,602행
  -> 로컬 별칭 409,519건
  -> 대표 품목 101,546건
  -> 제조사·성분·용량·게이지·규격·포장속성 재분해
  -> family·원자재·상위개념 후보 재생성
  -> 승인 분류·통합 분류·샘플 재생성
  -> 이전 승인 및 regulazation 기준 비교
```

정규화 품질 게이트:

- 원본 행 보존: 16,265,602 / 16,265,602
- 별칭 조인 누락: 0건
- 별칭 수: 409,519건
- 대표 품목 수: 101,546건
- 통합 대표 ID 중복: 0건
- 샘플 대표 ID 중복: 0건
- 거즈 치수 포함 별칭: 3,551건에서 규격 필드 분리 확인
- 게이지와 의약품 함량 충돌: 0건
- 사용량 기준과 포장수량 충돌: 0건

## 3. 재분류 현황

| 항목 | 건수 | 비율 |
|---|---:|---:|
| 전체 대표 품목 | 101,546 | 100.00% |
| effective family 식별 | 57,594 | 56.72% |
| 승인 대표 품목 | 1,177 | 1.16% |
| 공식 제품 단위 승인 | 1 | 0.001% |
| 공식 family 단위 승인 | 1,176 | 1.16% |
| 승인 전 검토 대상 | 100,369 | 98.84% |

family가 식별됐다는 것은 이름 규칙 또는 후보 근거가 있다는 뜻이다. 승인됐다는 뜻은 아니다.
원자재 후보 101,546건도 모두 `needs_review`이고 운영 승인 관계는 0건이다.

## 4. 승인 회귀 평가

기준은 원격 브랜치
`DGU-TeamLex/ai:feature/item-normalization-material-v2@518aa04`의 승인 1,177건이다.

| 필드 | 일치 | 전체 | 일치율 |
|---|---:|---:|---:|
| item group | 1,177 | 1,177 | 100.00% |
| item family | 1,177 | 1,177 | 100.00% |
| subtype | 1,177 | 1,177 | 100.00% |
| specification | 1,177 | 1,177 | 100.00% |
| unit | 1,177 | 1,177 | 100.00% |
| 5필드 동시 일치 | 1,177 | 1,177 | 100.00% |

첫 평가에서는 `란셋주사침28G` 1건의 승인 family가 통합 단계의 검토용 문맥 후보로
덮어써져 동시 일치도가 99.915%였다. 승인 분류가 검토 후보보다 우선하도록 수정했고,
`approved_classification_fields_preserved` 품질 게이트와 회귀 테스트를 추가한 뒤 100%로
회복했다.

## 5. 외부 표준 ID 군집 비교

`regulazation/물품재고_정규화완료.parquet`의 `표준품목ID`와 현재
`representative_item_id`를 기관·물품코드 기준으로 비교했다. 409,519건이 모두 연결됐다.

| 범위 | Pair P | Pair R | Pair F1 | B3 P | B3 R | B3 F1 |
|---|---:|---:|---:|---:|---:|---:|
| 전체 | 95.41% | 90.13% | 92.69% | 93.37% | 93.60% | 93.49% |
| 공식코드 | 99.23% | 87.24% | 92.85% | 98.41% | 86.04% | 91.81% |
| USE 규칙코드 | 86.57% | 99.94% | 92.78% | 89.82% | 99.96% | 94.62% |

해석:

- 공식코드는 precision이 높고 recall이 낮다. 서로 다른 공식 품목을 잘못 합치는 경우는
  적지만 같은 공식 품목을 이름 차이 때문에 여러 대표 품목으로 나눈 후보가 남아 있다.
- USE 규칙코드는 recall이 매우 높고 precision이 낮다. 현재 파이프라인이 방문·부서 표식을
  제거해 더 적극적으로 묶는 반면 외부 규칙은 표기별 NM ID를 나누는 경우가 많다.
- USE 기준 자체가 규칙 기반이므로 현재 묶음과 다르다고 항상 오분류는 아니다. 예를 들어
  `파스`, `방문파스`, `파스(재활)`은 운영 태그와 제품 정체성을 어떻게 분리할지 사람이
  확인해야 한다.
- 원본명 차이 2,474건은 `cm2/㎠`, `ml/㎖`, 공백, 괄호·특수문자 차이다. 기관·물품코드는
  모두 일치하므로 매핑 누락이 아니라 원문 표현 감사 항목으로 분리했다.

## 6. 분류 가중치 비교

현재 `classification_confidence`는 학습 확률이 아니라 정책값 `0.60 / 0.97 / 1.00`이다.
평가 모듈에는 운영 승인과 분리된 실험용 증거 점수를 추가했다.

```text
verified_structured_family       0.92
verified_ingredient_dictionary  0.90
official_standard_rule          0.88
local_structured_family         0.82
context_explicit_rule           0.78
name_rule                       0.55
```

일치 근거와 세부 필드 완성도에는 소폭 보너스를 주고, conflict는 감점하며 unresolved는 0으로
둔다. 이 점수는 검토 순서만 정하며 자동 승인에는 쓰지 않는다.

| 대안 | 선택 대표 품목 | 대표 커버리지 | 사용량 커버리지 | 미승인 후보 | 용도 |
|---|---:|---:|---:|---:|---|
| 현재 운영 승인 | 1,177 | 1.16% | 1.26% | 0 | 현 운영 게이트 유지 |
| 보수 검토, 0.90 | 2,189 | 2.16% | 4.01% | 1,042 | 우선 수동 검수 |
| 균형 검토, 0.82 | 2,200 | 2.17% | 4.01% | 1,053 | 보수안 대비 이득이 작음 |
| 공격 검토, 0.60 | 14,026 | 13.81% | 50.82% | 12,879 | 대규모 라벨링 큐에만 사용 |

보수·균형안은 family conflict를 제외하므로 기존 승인 중 30건도 별도 감사 대상으로 돌린다.
이는 승인을 자동 취소하라는 뜻이 아니다. 구조화 근거가 약한 이름 규칙과 충돌한 건을 다시
보자는 의미다.

권고:

1. 운영 자동승인은 현재 1,177건 게이트를 유지한다.
2. 다음 검수 배치는 보수안의 미승인 1,042건과 기존 승인 conflict 30건부터 시작한다.
3. 균형안은 보수안보다 11건만 늘고 사용량 커버리지가 사실상 같아 별도 운영안 가치가 작다.
4. 공격안은 사용량 50.82%를 덮지만 독립 precision이 없어 자동 승인에 사용하지 않는다.

## 7. Module C 가중치 민감도

현재 Module C 공급신호 가중치는 `공급뉴스 0.45 / 원자재뉴스 0.20 / 시장가격 0.35`이고,
재고 조정 상한도 실적 학습계수가 아닌 policy seed다. 승인 품목-원자재 관계와 운영 PASS가
0건이므로 실제 위험 점수는 0행이다.

월 예상 사용량 100, 검토주기 30일, 리드타임 7일, 기본재고 148의 공통 예시를 실제 재고정책
함수에 넣어 영향도만 비교했다.

| 위험점수 | 0.75배 목표재고 | 현재 목표재고 | 1.25배 목표재고 |
|---:|---:|---:|---:|
| 0.00 | 148.00 | 148.00 | 148.00 |
| 0.50 | 212.52 | 237.42 | 263.63 |
| 1.00 | 217.38 | 240.50 | 263.63 |

위험 0.5만으로 현재안은 기본재고보다 60.42% 증가한다. 실제 품절·납기지연 라벨 없이
1.25배안을 적용하는 것은 과적재 위험이 크다. 운영값은 유지하되, 최초 승인 파일럿이
필요하면 0.75배안을 자동발주가 아닌 REVIEW 권고로만 비교하는 것이 가장 보수적이다.

## 8. 진짜 정확도를 얻는 방법

다음 작업 전에는 `정확도 몇 %`를 확정할 수 없다.

1. 현재 주의 샘플과 사용량 상위 품목을 합쳐 독립 1,000건 정답셋을 만든다.
2. 검수자는 자동 예측을 보지 않고 group, family, subtype, spec, unit을 입력한다.
3. 의료용품·의약품 담당자 2명이 독립 검수하고 불일치는 제3자가 확정한다.
4. 전체 정확도 외에 필드별 precision/recall/F1, 품목군별 혼동행렬, 사용량 가중 정확도를 낸다.
5. 자동승인 임계값은 독립 정답셋 precision 98% 이상을 만족하는 지점으로 정한다.
6. 같은 기관이 학습과 평가에 동시에 들어가 생기는 낙관을 막기 위해 기관 holdout도 병행한다.

## 9. 다른 분류 대안

### 계층형 규칙 + abstain

현재 방식의 연장선이다. group, family, subtype, spec을 순차 분류하고 근거가 부족하면
`needs_review`로 남긴다. 의료 데이터에서 설명과 안전성이 가장 좋고 지금 즉시 사용할 수 있다.

### 문자 n-gram 선형 분류기

독립 라벨이 1,000~5,000건 생기면 한글·영문 혼합 품목명에 문자 n-gram TF-IDF와
Logistic Regression 또는 Linear SVM을 적용한다. group/family 후보 생성에는 강하지만
용량·게이지·포장수량은 현재 구조화 파서를 계속 사용하는 편이 안전하다.

### 공식 마스터 검색 + 재순위화

공식 의약품·의료용품 마스터를 먼저 검색하고 제조사·성분·규격 일치로 후보를 재순위화한다.
정확한 제품 ID 연결에 가장 유리하지만 공식 코드가 없는 USE 품목에는 별도 규칙이 필요하다.

### 약지도 결합

공식코드, 사전, 이름 규칙, 제조사·성분 파서를 각각 독립 라벨 함수로 두고 충돌을 학습한다.
현재처럼 고정 우선순위를 쓰는 것보다 확장성이 좋지만, 최종 보정에는 독립 정답셋이 여전히
필요하다.

## 10. 가중치 보정 절차

분류 가중치는 독립 정답셋에서 source별 실제 precision을 측정한 뒤 Platt scaling 또는
isotonic calibration으로 보정한다. 가중치를 직접 손으로 올리는 대신 `정확할 확률`로
보정하고 목표 precision에 맞춰 자동승인 임계값을 정한다.

Module C는 다음 순서로 보정한다.

```text
승인된 질병-품목·품목-원자재 관계 확보
-> 뉴스 사건일·가격 급변일·입고지연·품절 라벨 연결
-> 1~4주 lag 사건연구
-> 시간순 train/validation/test
-> 공급뉴스·원자재뉴스·가격 가중치 탐색
-> WAPE + 품절비용 + 보유비용 목적함수 비교
-> 상한·중복반영 방지 유지
```

정확도뿐 아니라 품절 감소와 과적재 증가를 동시에 봐야 한다. 테스트 구간을 보고 가중치를
다시 선택하지 않고 다음 시간 구간에서 challenger로 검증한다.

## 11. 산출물

| 파일 | 설명 |
|---|---|
| `data/processed/item_integrated_classification_v2.csv` | 최신 전체 통합 분류 101,546건 |
| `data/sample/item_integrated_classification_sample_1000.csv` | 통합 분류 검토 샘플 1,000건 |
| `outputs/item_classification_evaluation.json` | 전체 평가 요약과 해석 상태 |
| `outputs/item_classification_regression_metrics.csv` | 승인 5필드 회귀 일치도 |
| `outputs/item_classification_reference_cluster_metrics.csv` | 외부 표준 ID 군집 지표 |
| `outputs/item_classification_weight_scenarios.csv` | 분류 증거 임계값 대안 |
| `data/sample/item_classification_attention_sample_1000.csv` | 과병합·과분할·명칭차이 균형 샘플 |
| `outputs/module_c_weight_sensitivity.csv` | Module C 0.75/1.0/1.25배 민감도 |

주의 샘플은 다음 네 사유를 각각 250건 포함한다.

```text
possible_over_merge
possible_over_split
possible_over_merge_and_split
reference_raw_name_mismatch
```

## 12. 재현 명령

```bash
conda run -n teamlex python -m src.item_normalization --full --sample-size 1000
conda run -n teamlex python -m src.item_enrichment build-worklist
conda run -n teamlex python -m src.item_enrichment match --sample-size 1000
conda run -n teamlex python -m src.item_attribute_parser --sample-size 1000
conda run -n teamlex python -m src.item_integrated_pipeline --with-excel --sample-size 1000

conda run -n teamlex python -m src.item_classification_evaluation \
  --baseline-approvals /path/to/frozen/item_manual_standardization_decisions.csv \
  --baseline-source repository:branch@commit \
  --sample-size 1000
```

독립 정답셋이 완성되기 전까지 평가 JSON의
`independent_accuracy_available=false`를 유지해야 한다.
