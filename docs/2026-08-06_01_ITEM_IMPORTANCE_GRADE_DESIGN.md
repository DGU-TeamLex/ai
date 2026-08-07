# WeP-Stock 품목 중요도 등급 Weight 설정 레퍼런스 정리

작성일: 2026-08-06
대상 프로젝트: TeamLex / WeP-Stock AI Service
목적: 품목 중요도 등급 산정 로직에서 `volume_score`, `frequency_score`, `impact_score`, `criticality_flag`, `institution_scale` 등을 어떻게 설계할지에 대한 논문·레퍼런스 근거 정리

---

## 1. 핵심 결론

의료물품 재고에 바로 적용할 수 있는 **공인 고정 중요도 등급표**는 존재하지 않는다. 대신 병원·의약품 재고관리 연구는 다음 방식으로 중요도를 산정한다.

1. 사용량(금액 또는 물량) 기준의 단일 지표(ABC)만으로는 임상적 중요성을 반영하지 못한다고 본다.
2. 임상적 필수성(VED: Vital/Essential/Desirable)을 별도 축으로 분류하고, 사용량 축과 교차(매트릭스)한다.
3. 두 축 외에 리드타임, 대체 가능성, 결품 시 영향 같은 추가 기준을 다기준(Multi-Criteria) 방식으로 결합한다.
4. 표본이 적은 개체(소규모 기관·저빈도 품목)의 추정값은 집단 평균 쪽으로 수축(shrinkage)시켜 안정화한다.

따라서 WeP-Stock에서는 다음 구조가 가장 방어 가능하다.

```text
importance_score
= institution_scale_shrinkage(관내_비중, 전사_평균_비중)
* volume_score
* impact_score(대체 가능성)
* criticality_flag(의약품/소모품)
```

등급은 ABC-VED 매트릭스처럼 두 축을 교차한 3단계(A/B/C)로 단순화하고, 임계값은 목표 비중에 맞춰 실측 분포에서 역산한다.

---

## 2. 현재 프로젝트와의 연결

현재 WeP-Stock은 전 품목에 동일한 안전재고율(20%)을 적용하고 있으며, 8/5 중간점검 회의에서 보건의료정보부 측에 품목별 중요도·긴급도 기준이 존재하지 않음이 확인되었다. 회의에서 제시된 방향은 **사용량**과 **지역**이었다.

이번 정리는 자체 설계안(`policy_status: provisional_team_review_required`)의 근거를 마련하고, `data/handoff/item_importance_policy.json` 형태로 옮길 수 있는 기준안을 만드는 데 초점을 둔다.

---

## 3. 레퍼런스 요약표

| 구분 | 레퍼런스 | 핵심 방법 | WeP-Stock에 적용할 부분 |
|---|---|---|---|
| ABC 재고 분류 | Gupta, Gupta, Jain & Garg, 2007 | 연간 소비금액 기준 A/B/C 3단계 분류 (Pareto 원칙) | volume_score 기반 A/B/C 등급 구조의 원형 |
| VED 임상 중요도 분류 | Devnani, Gupta & Nigah, 2010 등 다수 병원 사례 | 임상의·약사 합의로 Vital/Essential/Desirable 분류 | criticality_flag 설계, 임상 판단 축을 별도로 둔다는 원칙 |
| ABC-VED 매트릭스 | 다수 3차병원 사례 연구 (Jharkhand 2025, Puducherry 등) | ABC(비용)와 VED(임상중요도)를 교차해 Category I/II/III로 통합 | 사용량 축과 impact/criticality 축을 곱하지 않고 매트릭스로 교차하는 설계 근거 |
| 다기준 ABC 분류 | Flores & Whybark, 1986/1987 | 사용량 외에 리드타임, 대체가능성, 통상성 등 비용 외 기준 추가 | impact_score(대체 가능 품목 수)를 별도 기준으로 추가하는 근거 |
| 가중합 다기준 분류의 한계 | 하이브리드 퍼지-확률 ABC 분류 리뷰 (PMC, 2020) | 가중선형최적화 방식이 단일 기준에서만 높은 품목을 과대평가하는 한계를 정리 | 여러 score를 가중합이 아닌 매트릭스로 결합한 이유의 근거 |
| 의약품 공급망 AHP/SAW | Jaberidoost et al., 2015 | 의약품 공급망 위험을 AHP·SAW로 평가 | 보건의료 공급망에서의 다기준 가중치 산정 선례 |
| 신용도(Credibility) 이론 | Loss Data Analytics 교재(openacttexts), SOA 교육위원회 자료 | 개별 관측치와 집단 평균을 표본크기 기반 가중치 `Z=n/(n+K)`로 결합 | institution_scale shrinkage 공식의 원형 |

---

## 4. 레퍼런스별 상세 정리

### 4.1 Gupta, Gupta, Jain & Garg (2007) — ABC and VED Analysis in Medical Stores Inventory Control

**논문**
Gupta R, Gupta KK, Jain BR, Garg RK. "ABC and VED analysis in medical stores inventory control." *Medical Journal Armed Forces India*, 2007, 63(4), 325-327.

**핵심 내용**

- 421개 의약품 품목을 대상으로 연간 소비금액 기준 ABC 분류(A 13.78%가 금액의 69.97% 차지)와 임상 중요도 기준 VED 분류를 각각 수행.
- ABC-VED 매트릭스로 교차한 결과 Category I(22.09%)이 전체 지출의 74.21%를 차지하는 것으로 확인.
- 저비용·저중요도 품목까지 동일한 강도로 관리하는 것은 비효율적이라는 결론.

**WeP-Stock 적용 포인트**

- volume_score(사용량) 단독으로 등급을 매기면 안 된다는 근거 — 사용량이 낮아도 임상적으로 필수인 품목(응급약 등)이 존재.
- A/B/C 3단계 자체가 오랫동안 검증된 구조라는 근거로 사용 가능.

---

### 4.2 병원 VED 사례 연구 다수 (Devnani et al., 2010; Jharkhand 2025; Puducherry 등)

**핵심 내용**

- VED 분류는 임상의·약사로 구성된 위원회의 합의(consensus)로 결정되며, 자동 계산이 아니라 판단 기반이라는 공통점을 가짐.
- Vital: 생명유지·응급 필수, 결품 시 즉각 위해. Essential: 표준 치료에 상시 사용. Desirable: 일시 결품을 감내 가능.
- 여러 연구에서 Vital 품목은 전체의 7~30% 수준으로 조사되며, 병원·시설 유형에 따라 편차가 큼.

**WeP-Stock 적용 포인트**

- WeP-Stock에는 임상의 위원회가 없어 이 판단을 대체할 근거가 필요 — 그래서 `criticality_flag`는 자체 판단(의약품/소모품 구분, #43 재검증 결과)으로 잠정 대체하고 `policy_status: provisional`로 명시.
- Vital 비중이 병원마다 7~30%로 편차가 크다는 사실은, WeP-Stock의 A등급 목표 비중(10~15%)을 고정값이 아니라 실측 분포로 역산해야 한다는 근거를 보강.

---

### 4.3 ABC-VED 매트릭스 (다수 3차병원 사례)

**핵심 내용**

- 두 축(비용, 임상중요도)을 곱하거나 더하지 않고, 각각 3단계로 나눈 뒤 조합(AV, AE, AD, BV...)해 Category I/II/III로 재통합.
- Category I(비용도 높고 중요도도 높은 조합군, 또는 어느 한쪽이라도 최고 등급인 조합)에 관리 자원을 집중.

**WeP-Stock 적용 포인트**

- 앞서 설계한 `volume_score × impact_score` 곱셈 구조보다, **매트릭스 교차 방식이 임상 문헌에서 더 표준적**임을 확인. A등급 조건을 "AND"로 묶은 것(§5.3)이 이 매트릭스 방식과 부합.

---

### 4.4 Flores & Whybark (1986, 1987) — Multiple Criteria ABC Analysis

**논문**
Flores, B. E., & Whybark, D. C. "Multiple Criteria ABC Analysis." *International Journal of Operations & Production Management*, 1986, 6(3), 38-46. / "Implementing multiple criteria ABC analysis." *Journal of Operations Management*, 1987, 7(1), 79-85.

**핵심 내용**

- 단일 기준(사용량) ABC의 한계를 지적하고, 리드타임·대체가능성·통상성(commonality)·진부화·결품 영향을 추가 기준으로 제안한 최초 연구 중 하나.
- 두 기준을 각각 3단계로 나눈 매트릭스로 결합하는 방식을 제시.

**WeP-Stock 적용 포인트**

- `impact_score`(결품 시 영향도, 대체 가능성 기반)를 별도 축으로 두는 설계의 원류 근거.
- 리드타임을 향후 추가 기준으로 확장할 수 있는 근거(현재는 별도 정책 §12에서 이미 다루고 있어 중복 배제).

---

### 4.5 가중합 방식 다기준 ABC 분류의 한계 (하이브리드 퍼지-확률 ABC 분류 리뷰)

**논문**
"A hybrid fuzzy-stochastic multi-criteria ABC inventory classification using possibilistic chance-constrained programming." *PMC*. https://pmc.ncbi.nlm.nih.gov/articles/PMC7384770/

**핵심 내용**

- 여러 기준을 하나의 가중합 점수로 결합하는 최적화 모델(대표적으로 가중선형최적화 방식)의 계보를 정리.
- 이 방식이 특정 기준 하나에서만 점수가 높은 품목에 전체 점수를 과도하게 높게 주는 한계가 있다는 후속 비판들을 함께 정리.

**WeP-Stock 적용 포인트**

- volume_score와 impact_score를 단순 가중합이 아니라 **AND 조건(매트릭스)**으로 결합하기로 한 이유의 반증 근거로 사용 — 가중합 방식의 알려진 약점(단일 기준 과대평가)을 피하기 위함.

---

### 4.6 신용도(Credibility) 이론 — Bühlmann / Bühlmann-Straub 모델 해설

**참고 자료**
- "Chapter 9: Experience Rating Using Credibility Theory." *Loss Data Analytics* (openacttexts, 오픈 교재). https://openacttexts.github.io/Loss-Data-Analytics/ChapCredibility.html
- Education Committee of the Society of Actuaries. *Credibility* 교육자료. https://www.soa.org/globalassets/assets/Files/Edu/2018/2018-stam-23-18.pdf

**핵심 내용**

- 개별 관측치(individual risk)와 집단 평균(collective mean)을 표본 크기 기반 가중치 `Z = n/(n+K)`로 결합하는 신용도(credibility) 프레임워크. 원 이론은 Bühlmann(1967)이 제안했으나, 원문 접근이 어려워 위 오픈 교재·교육자료를 통해 확인.
- Bühlmann-Straub 확장은 개체마다 **노출량(exposure)이 다른 경우**를 명시적으로 다룸 — 보험 계약자마다 피보험자 수가 다른 상황을 예로 듦.

**WeP-Stock 적용 포인트**

- mu값(수요율) 산출에서 이미 사용한 credibility shrinkage를 **기관 규모(institution_scale) 축**에 그대로 적용하는 근거. 특히 Bühlmann-Straub 확장이 "개체마다 노출량(규모)이 다른 상황"을 위해 만들어진 모델이라는 점이, 취급 품목 수 1~1,207까지 편차가 큰 WeP-Stock 기관 규모 문제와 정확히 일치.

---

## 5. WeP-Stock 초기 설계 제안

아래 값은 문헌에 그대로 나온 고정값이 아니라, 위 레퍼런스들의 방법론을 조합한 **초기 설정값**이다. 실제 값은 등급별 품목 수·SS 총합 분포, 8/21 최종 제출 전 팀 리뷰로 보정해야 한다.

### 5.1 volume_score (ABC 축)

```text
volume_score = institution_scale × 관내_비중 + (1 − institution_scale) × 전사_평균_비중
institution_scale = n / (n + K)     # n = 기관 전체 출고량, K = 품목 내 분산/기관 간 분산
```

- Gupta et al.(2007)의 ABC 축 원리(연간 소비량 기준 3단계)를 따르되, Bühlmann-Straub 신용도 모델(§4.6)의 불균등 노출량 보정 개념으로 소규모 기관 왜곡을 보정.

### 5.2 criticality_flag (VED 축 대체)

| 구분 | 기준 | 비고 |
|---|---|---|
| 의약품(잠정 Vital/Essential 성격) | #43 재검증된 의약품 분류 | 임상의 위원회 부재로 자체 판정, `provisional` 표기 |
| 소모품(잠정 Desirable 성격) | #43 재검증된 소모품 분류 | 동일 |
| criticality_override | 국가필수∧단일수입원∧대체불가 | 기존 정책 파일의 최상위 강제 승격 플래그, 별도 축 유지 |

- 문헌상 VED는 임상 위원회 합의가 원칙(§4.2)이므로, WeP-Stock의 criticality_flag는 VED의 **완전한 대체가 아니라 잠정 근사**임을 명시.

### 5.3 impact_score (Flores & Whybark 확장 기준)

```text
impact_score = 1 − (같은 성분/용도군 내 대체 가능 품목 수 → 0~1로 정규화)
```

- #45 3계층 동일군을 대체가능성 기준으로 재사용.

### 5.4 등급 매트릭스 (ABC-VED 매트릭스 방식)

가중합이 아니라 **매트릭스 교차**로 결합(§4.3, §4.5 반증 근거):

| volume_score | impact_score 또는 criticality | 등급 |
|---|---|---|
| 상위 | 상위(대체불가 또는 의약품) | **A** |
| 상위 | 하위 | B |
| 하위 | 상위 | B |
| 하위 | 하위 | **C** |
| (모든 조합) | criticality_override=True | **A (강제)** |

목표 비중은 실측 분포에서 역산(§4.2에서 확인된 병원 간 편차를 근거로 고정 퍼센타일 대신 유동적으로 설정).

---

## 6. 최종 등급 산출 구조

```text
importance_grade
= ABC_VED_matrix(volume_score, criticality_flag, impact_score)
  OVERRIDE BY critical_override
```

```text
SS = 보호기간수요 × safety_ratio(importance_grade)
A등급: 0.20 (현행 유지)
B등급: 0.20 (기준값)
C등급: 0.12~0.15 (하향)
```

---

## 7. 설정 파일 예시

`data/handoff/item_importance_policy.json`:

```json
{
  "policy_status": "provisional_team_review_required",
  "basis": "internal_design_no_institutional_criteria",
  "methodology_references": ["Gupta et al. 2007 (ABC)", "ABC-VED matrix (hospital pharmacy)", "Buhlmann-Straub credibility model (institution scale shrinkage)"],
  "institution_scale_shrinkage": {
    "formula": "n / (n + K)",
    "K_estimation": "item_within_variance / institution_between_variance"
  },
  "grade_matrix": {
    "A": "volume_score_top AND (criticality=medicine OR impact_score_top)",
    "B": "volume_score_top XOR criticality_or_impact_top",
    "C": "neither"
  },
  "safety_ratio": { "A": 0.20, "B": 0.20, "C": 0.13 },
  "excluded_for_v1": ["region_group"],
  "review_trigger": "기관 측 공식 중요도 기준 확보 시"
}
```

---

## 8. 구현 우선순위

1. `item_importance_policy.json` 생성
2. 품목별 `volume_score`(institution_scale shrinkage 적용) 산출
3. `criticality_flag` — #43 재검증 결과 반영
4. `impact_score` — #45 3계층 매핑 결과 반영
5. ABC-VED 매트릭스로 3등급 산출
6. 등급별 품목 수 · SS 총합 분포 검증 (8/5 과적재 사례 재발 방지)
7. #54(SS/ROP 모형 확정) 후 safety_ratio 실반영

---

## 9. 검증 방법

### 9.1 분포 기반 검증

병원 VED 사례들이 Vital 비중 7~30%로 크게 편차가 났던 것처럼(§4.2), 고정 퍼센타일이 아니라 실측 분포를 먼저 뽑아 목표 비중에 맞게 역산.

```python
grade_dist = df.groupby('item_grade').size() / len(df)
ss_share = df.groupby('item_grade')['safety_stock'].sum() / df['safety_stock'].sum()
```

### 9.2 대조군 검증 (institution_scale shrinkage)

mu 보정과 동일한 방식 — 표본이 충분한 대형 기관군을 대조군으로 삼아, shrinkage 적용 전후 관내 비중 값이 크게 달라지지 않는지 확인.

### 9.3 팀 리뷰 샘플링

VED 문헌에서 임상 위원회 합의가 원칙이었던 것처럼(§4.2), criticality_flag 산출 결과 일부를 팀 내 검토로 샘플링해 #43 재검증 결과의 타당성을 재확인.

---

## 10. 참고문헌

1. Gupta, R., Gupta, K. K., Jain, B. R., & Garg, R. K. (2007). "ABC and VED analysis in medical stores inventory control." *Medical Journal Armed Forces India*, 63(4), 325-327. https://www.sciencedirect.com/science/article/abs/pii/S0377123707800062
2. Devnani, M., Gupta, A. K., & Nigah, R. (2010). "ABC and VED analysis of the pharmacy store of a tertiary care teaching, research and referral healthcare institute of India." *Journal of Young Pharmacists*, 2(2), 201-205. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC3021698/
3. Evaluation and Optimization of Pharmaceutical Inventory Management in a Tertiary Care Teaching Hospital in Jharkhand, India, Using ABC-VED Analysis. (2025). *Cureus*. https://pmc.ncbi.nlm.nih.gov/articles/PMC12807746/
4. Assessment of drug inventory using ABC–VED matrix analysis in selected public health facilities of Puducherry, India. *Journal of Family Medicine and Primary Care*. https://pmc.ncbi.nlm.nih.gov/articles/PMC12088547/
5. Flores, B. E., & Whybark, D. C. (1986). "Multiple Criteria ABC Analysis." *International Journal of Operations & Production Management*, 6(3), 38-46. https://www.emerald.com/ijopm/article/6/3/38/144191/Multiple-Criteria-ABC-Analysis
6. Flores, B. E., & Whybark, D. C. (1987). "Implementing multiple criteria ABC analysis." *Journal of Operations Management*, 7(1), 79-85. https://onlinelibrary.wiley.com/doi/pdf/10.1016/0272-6963(87)90008-8
7. "A hybrid fuzzy-stochastic multi-criteria ABC inventory classification using possibilistic chance-constrained programming." *PMC*. https://pmc.ncbi.nlm.nih.gov/articles/PMC7384770/
8. Jaberidoost, M., et al. (2015). "Pharmaceutical supply chain risk assessment in Iran using analytic hierarchy process (AHP) and simple additive weighting (SAW) methods." *Journal of Pharmaceutical Policy and Practice*, 8, 9. https://www.tandfonline.com/doi/full/10.1186/s40545-015-0029-3
9. "Chapter 9: Experience Rating Using Credibility Theory." *Loss Data Analytics* (openacttexts 오픈 교재). https://openacttexts.github.io/Loss-Data-Analytics/ChapCredibility.html
10. Education Committee of the Society of Actuaries. *Credibility* 교육자료(Bühlmann-Straub 모델 포함). https://www.soa.org/globalassets/assets/Files/Edu/2018/2018-stam-23-18.pdf
