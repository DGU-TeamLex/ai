# 파이프라인 방법론 근거 대장

작성 2026-08-11. 각 설계 결정이 **어떤 문헌 근거**와 **어떤 실측 결과**에 기대고 있는지를
한 곳에 모은다. 지금까지 이 근거들이 임시 스크립트와 대화에만 있어 재현이 불가능했다.

읽는 법
- 문헌은 저자·연도·학술지·권:페이지·해당 절까지 특정한다. 원문 인용은 싣지 않는다
  (저작권). 주장을 우리 맥락으로 서술하고 출처를 달아 검증자가 원문을 찾아갈 수 있게 한다.
- 수치는 전부 `outputs/` 산출물에서 가져왔다. 출처 파일을 각 절에 적었다.
- **사전지정(confirmatory)** 과 **탐색(exploratory)** 을 구분한다. 이 구분 없이 p값을
  보고하면 다중검정으로 부풀려진 결과를 확정 사실처럼 말하게 된다.

---

## 1. 검증 설계 — 롤링 원점 교차검증

**결정** `config.VALIDATION_FOLDS` 를 2 fold(6개월)에서 4 fold(12개월)로 확대. fold 폭
3개월, rolling-origin 구조 유지. 각 fold 는 자기 `train_end` 이후만 평가한다.

**근거** Bergmeir, C. & Benítez, J. M. (2012), "On the use of cross-validation for time
series predictor evaluation", *Information Sciences* 191:192–213. 시계열에서 표준 k-fold
는 미래 정보 누출로 성능을 낙관 편향시키며, 평가 구간이 짧으면 성능 차이가 그 구간의
계절성에 좌우된다. 저자들은 블록/롤링 형태의 분할을 권한다.

**우리 맥락** fold 가 2개면 WAPE 차이가 2025 상반기 특성에 좌우되어, 튜닝이 그 반기에
과적합된다. 12개월로 넓히면 계절 한 주기를 덮는다.

| fold | train_end | valid |
|---|---|---|
| 2024_q3 | 2024-06 | 2024-07~09 |
| 2024_q4 | 2024-09 | 2024-10~12 |
| 2025_q1 | 2024-12 | 2025-01~03 |
| 2025_q2 | 2025-03 | 2025-04~06 |

**구현** `src/config.py` `VALIDATION_FOLDS`. 학습·검증 구간 중첩은 `_prepare_folds()`
에서 명시적으로 검사해 누수 시 예외를 던진다.

---

## 2. 튜닝 baseline 계약

**결정** 운영 파라미터를 `training.production_lgbm_params()` 한 곳에만 정의하고,
`_build_estimator()` 와 튜너 baseline 이 그것을 공유한다.

**왜 필요했나** 튜너의 `_baseline_params()` 가 파라미터를 자체 나열하면서
`subsample_freq=1` 을 넣고 있었다. 운영은 이 값을 지정하지 않아 LightGBM 기본값 0 이고,
따라서 `subsample=0.9` 가 **실제로는 비활성**이다. 두 조건이 달라 "baseline 대비 개선폭"
에 파라미터 효과와 동작 변경이 섞였다. 이 때문에 초기 보고치(-2.04%p)를 철회했다.

**원칙** 비교 대상이 되는 baseline 은 반드시 운영 코드의 단일 정의에서 가져온다.
탐색 쪽이 도입하는 동작 변경(`subsample_freq=1`)은 파라미터 탐색과 **분리해서** 보고한다.

---

## 3. 외부 신호(뉴스·원자재)의 예측 기여

**결과** 기여가 없다. 오히려 악화시킨다. (`outputs/variant_ab_comparison.json`)

| 변형 | 피처 수 | 통합 WAPE | A 대비 | BIAS |
|---|---|---|---|---|
| A (사용량만) | 47 | **39.052** | — | −8.74 |
| B (+뉴스) | 51 | 39.394 | **+0.342** | +3.53 |
| C (+뉴스+원자재) | — | 산출 불가 | — | — |

C 는 원자재 위험 피처에 non-zero 관측이 하나도 없어 실행되지 않았다. LightGBM 분할
기여도에서도 뉴스 피처는 **9,600 분할 중 0회** 선택됐다.

**해석 주의** 이것은 "뉴스가 수요와 무관하다" 는 결론이 아니다. 현 시점 뉴스 피처의
커버리지·품질 문제와 구분되지 않는다. 매핑 승인률이 0.39% → 23.45% 로 오른 뒤에도
그 출처가 `user_bulk_candidate_approval_not_fact_verification` 이라 사실 검증을 거치지
않았다. 신호 자체의 무효성과 파이프라인 결함을 분리하지 못한 상태다.

---

## 4. 원자재 → 국내 가격 전이

**설계** 단순 상관이 아니라 단위근 → 공적분 → 오차수정 → 인과성 순서를 밟는다.
비정상 시계열 간 상관은 허구적 회귀(spurious regression)를 낳는다
— Granger, C. W. J. & Newbold, P. (1974), "Spurious regressions in econometrics",
*Journal of Econometrics* 2(2):111–120.

**절차와 근거**

1. **ADF 단위근 검정** — Dickey, D. A. & Fuller, W. A. (1979), *JASA* 74(366):427–431.
2. **Johansen 공적분 검정 (trace)** — Johansen, S. (1991), "Estimation and hypothesis
   testing of cointegration vectors in Gaussian vector autoregressive models",
   *Econometrica* 59(6):1551–1580. 임계값 15.494 (95%, r=0).
3. **VECM** — 공적분이 있으면 차분 VAR 이 아니라 오차수정항을 포함해야 한다
   (Engle, R. F. & Granger, C. W. J. 1987, *Econometrica* 55(2):251–276).
4. **Granger 인과성** — Granger (1969), *Econometrica* 37(3):424–438.
5. 시차 선택은 AIC.

**결과** (`outputs/material_vecm_transmission.json`, n_obs=24)

| 경로 | 수요비중 | Johansen trace | 공적분 | VECM α(국내) | Granger 최소 p |
|---|---|---|---|---|---|
| ALUMINUM → ALUMINUM | 13.37% | **25.059** | ✓ | 0.476 (p=.044) | **0.0033** (lag 1) |
| BRENT → PP | 14.35% | **20.901** | ✓ | 0.318 (p=.000) | **0.0123** (lag 3) |
| COTTON → COTTON_FIBER | 0.28% | 27.228 | ✓ | 5.759 (p=.024) | 0.0832 (lag 3) |
| BRENT → PVC | 0.03% | 18.448 | ✓ | 0.713 (p=.000) | 0.2422 |
| BRENT → PE | 0.18% | 15.140 | ✗ | — | 0.2868 |
| BRENT → PARAFFIN | 1.42% | 9.023 | ✗ | — | 0.4761 |
| CORN → GLUCOSE | 0.10% | 12.137 | ✗ | — | 0.6263 |
| SUGAR → REFINED_SUGAR | 0.01% | 6.616 | ✗ | — | 0.5904 |

**나프타 편향 정정** 초기 분석은 나프타(PP 경로)만 봤다. 대칭적으로 다시 돌린 결과
**알루미늄이 수요비중 13.37% 로 PP(14.35%) 와 대등**하고, 가중치 1.0 직접 연결에
무료 데이터까지 있어 조건이 더 낫다. `PETROCHEMICAL_NAPHTHA` 경로는 전부 `no_data` 다.

**표본 한계** n_obs=24 는 Johansen 검정에 얇다. 확정이 아니라 후속 확인 대상이다.

---

## 5. 다중검정 보정

**결정** 탐색적으로 돌린 가격→재고 검정 90건에는 Bonferroni 보정을 적용한다.
PP 경로도 최신 24개월 검증에서 재현되지 않았으므로 운영 근거로 별도 우대하지 않는다.

**근거** Bonferroni 보정의 표준 서술은 Dunn, O. J. (1961), "Multiple comparisons among
means", *JASA* 56(293):52–64. 사전지정 가설과 탐색적 가설을 같은 보정 풀에 넣으면 안
된다는 원칙은 ICH E9 (1998) *Statistical Principles for Clinical Trials* §2.2.2 참조.

**적용**
- 탐색 90건: Bonferroni 보정 후 유의한 경로 0건
- PP 관세청 수입가 → 기말재고, lag 2개월: 24개월 검증 p=0.4617로 재현 실패
- 과거 3개월 분할 p=0.007은 평가기간이 너무 짧아 일반화 근거가 되지 않음

**해석** PP lag를 운영 위험가중치나 리드타임 연장의 증거로 쓰지 않는다. 외부 위험 조정은
shadow 결과로만 보존하고, 사전지정한 경로·목표·lag·보정법과 12개월 이상의 미사용 평가기간,
block bootstrap을 갖춘 뒤 재검증한다.

---

## 6. 재고 정책 — 정기발주 (R,S)

> 2026-08-17 변경: 아래 고정 20% 식은 당시 baseline과 민감도 기록으로 보존한다.
> 현재 실험 정책은 비용 임계비율과 예측오차 기반 안전재고를 사용한다. 정본은
> `docs/2026-08-17_01_FORMULA_DATA_AND_EVIDENCE_ALIGNMENT.md`와
> `data/mapping/inventory_optimization_policy.json`이다.

**모형** `목표재고 = μ × (R + L) + SS`, R=30일, SS = 보호기간수요 × 0.20.

**근거** 정기발주 정책과 보호기간(review period + lead time) 개념은 재고이론 표준
교과서를 따른다 — Silver, E. A., Pyke, D. F. & Thomas, D. J. (2016),
*Inventory and Production Management in Supply Chains*, 4th ed., Ch. 7 (periodic review).
보충 시점 사이 전 구간의 수요 위험을 덮어야 하므로 L 이 아니라 R+L 이 기준이 된다.

**R=30일 근거** 발주가 월 단위 정기 발주라는 업무 사실. `data/mapping/` 정책 파일과
이슈 #54 에 기록.

**민감도** (`outputs/lead_time_sensitivity.json`, 163,229행)

| L | 보호기간 | 발주량 합 | 15일 대비 | 발주 필요 품목 |
|---|---|---|---|---|
| 7일 | 37일 | 902,228 | 0.63× | 13,824 |
| **15일** | 45일 | 1,441,712 | 1.00× | 16,597 |
| 30일 | 60일 | 2,926,294 | **2.03×** | 22,094 |
| 60일 | 90일 | 8,022,869 | **5.56×** | 33,808 |
| 120일 | 150일 | 27,481,507 | **19.06×** | 57,546 |

발주량이 L 에 비선형으로 증가한다. 재고가 목표 미달인 품목이 함께 늘기 때문이다.
**L 을 잘못 잡으면 영향이 매우 크다.**

---

## 7. 리드타임 추정 — 정의와 식별

**정의 (클라이언트 확인)** L = **발주 시점부터 입고까지의 총 대기시간**.

**원장으로는 L 을 식별할 수 없다** 원장 컬럼 18개 중 날짜는 `재고마감일` 하나뿐이고
발주일 필드가 없다. 원장으로 잴 수 있는 것은

```
M = 입고일 − 직전 거래일   (조건: 입고량>0 AND 이전최종재고량==0)
```

이고 발주 시점이 그 구간 안 어딘가이므로 **M ≥ L**, 즉 상한이다.
현 정책의 `stockout_duration_p25` 는 M 의 p25 를 L 의 대용으로 쓰는 heuristic 이다.

**원장 실측** (`outputs/lead_time_optimal.json`)

| 기간 | 표본 | p10 | p25 | median | p75 | p90 |
|---|---|---|---|---|---|---|
| 2024-25 | 93,101 | 3 | 11 | 35 | 96 | 197 |
| 2018-19 | 37,679 | 2 | 8 | 28 | 78 | 176 |

**품목별 추정은 쓰면 안 된다** 두 기간 공통 1,831품목의 품목별 median 상관은 **0.079**.
기간 간 out-of-sample 로 추정량을 비교하면 (`outputs/lead_time_estimator_selection.json`,
공통 2,763품목):

| 추정량 | MAE(일) | 과소추정률 | pinball@0.9 |
|---|---|---|---|
| 현행 15일 고정 | 28.60 | 87.1% | 25.25 |
| 전품목 공통 28일 | **21.61** | 63.4% | 16.55 |
| 품목별 raw median | 34.30 | 57.9% | 17.03 |
| 신뢰도 축소 (k=0.808) | 29.47 | 55.1% | **15.25** |

**품목별 값이 전품목 공통값보다 나쁘다.** 표본이 얇아 과적합이다. 축소 추정량의 근거는
Bühlmann, H. & Straub, E. (1970), "Glaubwürdigkeit für Schadensätze",
*Bulletin of the Swiss Association of Actuaries* 70:111–133 — 개체 추정치를 집단 평균
쪽으로 n/(n+k) 만큼 당긴다.

비대칭 손실(품절 > 과재고) 평가는 pinball loss 를 쓴다 —
Koenker, R. & Bassett, G. (1978), "Regression quantiles", *Econometrica* 46(1):33–50.

**⚠️ 철회된 권고** 위 표에서 pinball 최소인 q75=55일을 한때 권고했으나 **철회했다**.
그 계산은 M(품절→입고)을 맞추도록 최적화한 것이고, 클라이언트 정의인 L(발주→입고)이
아니다. 다른 양을 최적화한 결과였다. "현행 15일이 87% 과소추정" 도 M 기준이며 L 기준
으로는 성립하지 않는다.

**L 의 직접 식별 — 조달청 납품요구** 발주일과 납기가 모두 있는 유일한 공개 자료다.

```
L_계약 = maxDlvrTmlmtDate(납품기한) − dlvrReqRcptDate(납품요구접수일)
```

출처: 조달청 나라장터 종합쇼핑몰 품목정보 서비스,
`apis.data.go.kr/1230000/at/ShoppingMallPrdctInfoService/getDlvrReqInfoList`
(공공데이터포털 dataset 15129471 / 파일데이터 15053481).

**조인 제약** 원장 `보건기관코드_en` 은 비식별화되어 있어(`P;485`, `R4<4<` 등 ASCII
치환 흔적) 수요기관코드와 매칭 불가. 발주 건별 짝짓기는 포기하고, 평문인 `dminsttNm`
으로 **보건소·보건지소·보건진료소·보건의료원** 모집단만 좁혀 세부품명 단위로 집계한다.

**중간 결과** (2024-01~06, 10,255건 중 유효 10,109건)

```
p25 = 30    median = 30    p75 = 30    p90 = 60      (현행 fallback 15일)
```

의료 소모품은 분산 없이 30일 고정이다 — 의료용살충제(n=3,289), 백신(n=3,214),
저출력심장충격기(n=250), 방역용소독기(n=149), 손소독기(n=98) 모두 p25=median=p90=30.
긴 쪽(네트워크스위치 329일, 승용차 210일, 히트펌프 60일)은 전부 비의료 자산이다.

**한계** L_계약은 **계약상 납기**지 실제 도착일이 아니다. 기관 조인 불가로 납기 초과분을
검증할 수 없다. 정책 반영 시 지연 마진을 별도로 얹어야 한다. 또한 현재 6개월치이며
전 구간(18개월) 수집은 일 할당량 1,000회 제한으로 진행 중이다.

---

## 8. 수요 패턴 분류

**근거** Syntetos, A. A. & Boylan, J. E. (2005), "The accuracy of intermittent demand
estimates", *International Journal of Forecasting* 21(2):303–314, 및
Syntetos, Boylan & Croston (2005), *JORS* 56(5):495–503 의 ADI/CV² 분류 —
smooth / intermittent / erratic / lumpy.

**우리 용도** 세그먼트별로 개선폭을 따로 본다. 전체 WAPE 하나로는 어느 수요 유형에서
좋아졌는지 알 수 없다. `outputs/tuned_segment_comparison.json` 참조.

---

## 9. 재현에 필요한 것

각 수치는 아래 산출물에서 나온다. 스크립트가 `scripts/` 로 이관되기 전 결과는
파일만 있고 코드가 임시 폴더에 있었다 — 이관 대상이다.

| 절 | 산출물 |
|---|---|
| 3 | `outputs/variant_ab_comparison.json` |
| 4 | `outputs/material_vecm_transmission.json`, `outputs/material_lag_all.json` |
| 6 | `outputs/lead_time_sensitivity.json` |
| 7 | `outputs/lead_time_optimal.json`, `outputs/lead_time_estimator_selection.json`, `outputs/procurement_lead_time_by_item.csv` |
| 8 | `outputs/tuned_segment_comparison.json` |

수집기는 `src/procurement/lead_time_collector.py`. 진행 상태는
`data/processed/procurement_collection_progress.json` 이 관리하며 중단 지점부터 이어받는다.
