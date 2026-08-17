# 한국사회보장정보원 제공 데이터 기반 의료재고 예측 실험 결과 보고서

- 작성일: 2026-08-17
- 대상: 한국사회보장정보원 및 데이터 제공·검토 담당자
- 분석 기준: `DGU-TeamLex/ai` dev commit `5a6651aef3509ec84da7a1ccad7e9041c3ddd667`
- 분석 성격: 연구·실험용. 운영 자동발주 승인 자료가 아님
- 데이터 시차: 최신 원천 2025-12. 운영시점 대비 8개월 시차는 실험 설계상 허용된 조건
- 인용 원칙: 아래 원문은 핵심 문구만 짧게 발췌했으며 한국어 해석은 연구진 번역이다.

## 1. 연구 질문과 한 문장 해법

이 연구의 질문은 단순히 “다음 달 출고량을 얼마나 정확히 맞추는가”가 아니다. 정보원이 보유한
재고 원장만으로 다음 네 질문에 연속해서 답할 수 있는지를 검증한다.

1. 반품·조정이 섞인 원장에서 모델이 학습할 실제 사용량을 어떻게 분리할 것인가?
2. 짧고 간헐적인 품목별 이력에서 어떤 예측을 선택할 것인가?
3. 예측량을 서비스 수준과 비용의 trade-off가 드러나는 발주량으로 어떻게 변환할 것인가?
4. 뉴스·원자재·무역 위험처럼 아직 검증이 약한 신호를 발주에 섞지 않고 어떻게 시험할 것인가?

이 보고서의 한 문장 해법은 다음과 같다.

> 관측 출고를 모델 수요로 정제하고, 수요 패턴에 맞게 다음 달 사용량을 예측한 뒤,
> 예측오차와 비용비를 이용해 주기검토 재고정책으로 변환하며, 검증되지 않은 외부위험은
> 실제 발주가 아닌 shadow 시나리오로 분리한다.

## 2. 문제해결 파이프라인

```text
물품재고 DAT
  │
  ├─ 원장 보존값: signed 정상출고 ────────────────┐
  └─ 모델 수요: positive 정상출고                │ 원장대사·품질 gate
                     │                           │
                     ▼                           │
              ADI·CV² 수요패턴 분류              │
                     │                           │
                     ▼                           │
          시간순 학습·검증과 예측모델 선택        │
                     │                           │
                     ▼                           │
       보호기간 수요 + 예측오차 기반 안전재고      │
                     │                           │
                     ▼                           │
             주기검토 (R,S) 기본 발주             │
                     │                           │
            ┌────────┴────────┐                  │
            ▼                 ▼                  │
        실제 권고량        외부위험 shadow         │
                         뉴스·원자재·무역          │
            └────────┬────────┘                  │
                     ▼                           │
    DORMANT·STALE·매핑·운영승인 안전 gate ◀───────┘
                     │
                     ▼
           결과·예외목록·재현 manifest
```

문헌과 구현의 관계는 다음 네 수준으로 구분한다.

- **직접 구현 근거**: 문헌의 수학적 구조를 코드에 구현했다.
- **원리 차용**: 문헌의 측정·설계 원리를 가져왔지만 동일 지수나 모형을 재현한 것은 아니다.
- **내부 실증**: 우리 데이터의 holdout·ablation·시뮬레이션으로 선택한 결과다.
- **실험 가정**: 실제 비용이나 업무자료가 없어 연구진이 설정한 계수이며 민감도 분석 대상이다.

따라서 참고문헌 수가 많다는 사실이 모델의 타당성을 자동으로 보장하지 않는다. 각 문헌이
정당화하는 범위를 넘지 않고, 최종 계수와 성능은 정보원 데이터에서 별도로 검증하는 것이 본
연구의 증거 구조다.

## 3. 데이터 수령 범위와 모델 수요 정의

### 3.1 정본 원천

- 2024-01~2025-12 물품재고 원천: 10개 DAT, 일별 16,265,602행
- 월별 집계: 3,729,983행
- 기관: 3,530개 익명 코드
- 부서: 109개
- 물품코드: 17,155개
- 기관·부서·물품 시계열: 416,128개
- 현재기간 원장대사 위반: 8행

구입단가는 101,022/16,265,602행, 약 0.62%만 관측된다. 그러므로 금액 ABC나 실제 비용
최적화의 전수 근거로 사용하지 않는다.

### 3.2 보조 과거자료와 모집단 계약

- 2018-01~2019-12 물품재고 원천: 3개 DAT, 일별 6,106,936행
- 월별 집계: 1,460,785행
- 기관·부서·물품 시계열: 179,295개
- 과거기간 원장대사 위반: 39행
- 과거 로컬 품목 175,760개 중 현재 대표품목에 엄격히 연결된 품목: 146,757개, 83.50%

2020~2023 공백은 보간하지 않는다. 2019와 2024 사이에서 lag segment를 새로 시작하며,
연결되지 않은 과거 29,003개 품목은 모델 학습에서 제외한다.

```text
U  = raw_stock 2024~2025에서 관측된 기관·부서·품목·월
E* = U에서 유도되고 사람이 승인한 매핑에 연결되는 외부자료
model panel = U LEFT JOIN E*
```

외부자료에만 존재하는 품목을 모집단에 추가하지 않는다. 미매핑 원천 품목은 수요예측에는
유지하되 외부신호 상태를 `unmapped`로 둔다.

### 3.3 원장값과 모델 label 분리

반품·정정으로 정상출고가 음수가 될 수 있으므로 원장 보존값과 모델 수요를 동일하게 취급하지
않는다.

```text
normal_outbound_signed_sum       = Σ normal_outbound
model_demand_positive_sum        = Σ max(normal_outbound, 0)
negative_normal_outbound_count   = count(normal_outbound < 0)
negative_normal_outbound_amount  = Σ |min(normal_outbound, 0)|
```

signed 합은 원장대사와 감사에 보존하고, 예측 label은 positive 합을 사용한다. 음수-only 월의
학습 수요는 0이다. 이는 문헌에서 주어진 의료재고 공식이 아니라 **도메인 데이터 계약**이며,
음수 출고를 실제 음의 환자수요로 학습시키는 오류를 막는 장치다.

### 3.4 데이터 품질과 부분 pooling 후보

- 전체 모델 패널의 수요 0행 비율: 39.65%
- 6개월 이상 관측 시계열: 276,882개
- 12개월 이상 관측 시계열: 174,132개
- 12개월 이상 비율: 기관×품목 32.71%, 기관×family 56.50%, 품목 단독 70.63%
- 월별 이름이 둘 이상인 시계열: 51개
- 물리적 재고대사 위반: 현재 8행, 과거 39행

시계열의 중앙 관측월은 6개월이다. 짧은 개별 이력의 평균·분산은 불안정하므로 Bühlmann–Straub
신뢰도 이론의 개별값과 집단값을 표본량에 따라 축소 결합하는 원리를 pooling 후보로 둔다.
다만 기존 pooling 보고서에는 행 수를 월 수처럼 센 오류가 있었다. 따라서 기관×품목,
기관×family, 품목 단독 후보를 최신 holdout에서 다시 비교하기 전에는 최종 pooling 단위를
선택하거나 Bühlmann–Straub 구현이 성과를 냈다고 주장하지 않는다.

## 4. 수요패턴 분류와 예측모델

### 4.1 ADI·CV²로 품목의 예측 난이도 표현

양수 수요가 관측된 월의 수를 `n+`, 전체 관측기간을 `T`, 양수 수요의 평균과 표준편차를
각각 `μ+`, `s+`라 하면 다음을 계산한다.

```text
ADI = T / n+
CV² = (s+ / μ+)²
```

ADI가 크면 수요 발생 간격이 길고, CV²가 크면 발생했을 때 크기 변동이 크다. 두 축을 이용해
smooth·intermittent·erratic·lumpy 패턴을 구분하고, 패턴별 후보 예측기의 성능을 비교한다.
패턴의 개념은 Syntetos, Boylan & Croston의 분류 연구에 근거하지만, 세부 임계값과 router 선택은
우리 데이터에서 검증해야 하는 **구현 설정**이다.

### 4.2 시간순 검증과 모델 선택

무작위 교차검증은 미래 관측이 과거 학습에 섞일 수 있으므로 사용하지 않는다. 과거로 학습하고
그다음 월을 검증하는 시간순 분할을 사용한다.

```text
과거 학습구간 → calibration 2025-08~09 → 진단 평가 2025-10~12
```

2025-10~12는 이전 실험에서 이미 검사했으므로 최종 untouched test가 아니라 진단 평가다.
이 구분은 Bergmeir & Benítez의 시계열 예측평가 원리와 일치하지만, 해당 논문 자체가 TeamLex
모델의 성능을 보증하는 것은 아니다.

후보 모델은 WAPE만으로 결정하지 않고 과소예측 편향과 재고성과를 함께 본다.

```text
WAPE = Σ|y - ŷ| / Σ|y|
BIAS = Σ(ŷ - y) / Σy
```

### 4.3 관측된 예측 성능

동일 TEST 605,437행의 기존 전체 실행 결과:

- baseline LightGBM: WAPE 38.12%, MAE 51.90, BIAS -15.89, BIAS% -11.67%
- tuned LightGBM: WAPE 35.68%, MAE 48.57, BIAS -12.82, BIAS% -9.42%
- WAPE 개선: 2.44%p
- 학습시간: 16.4초에서 166.1초로 약 10.13배 증가

최신 조합정책 진단 재실행 결과:

- 선택 전략: 수요패턴별 calibration router
- calibration WAPE: 36.51%
- 진단 평가 WAPE: 37.17%
- 진단 평가 BIAS: -3.82%
- unit fill rate: 95.13%
- 양수수요 행 서비스 충족률: 88.91%
- 목표재고/실제수요 비율: 1.735

튜닝과 router는 오차 및 편향 개선 가능성을 보였지만, 독립 clean test에서 다시 확인해야 한다.

## 5. 예측량을 경제적 발주량으로 변환하는 수식

### 5.1 주기검토 (R,S) 정책

재고를 매 `R`일마다 검토하고 조달 리드타임을 `L`일이라 하면 보호기간은 `H=R+L`이다.
월 수요예측 `ŷmonth`를 일수 비례로 환산한다.

```text
H   = R + L
D_H = ŷmonth × H / 30
```

과소재고비 `Cu`, 과다재고비 `Co`의 임계비율과 안전계수는 다음과 같다.

```text
p*      = Cu / (Cu + Co)
z*      = Φ⁻¹(p*)
σ_H     = σ_month × √(H / 30)
SS      = z* × σ_H
S       = D_H + SS
IP      = on_hand + on_order - backorder
Q       = max(S - IP, 0)
```

`S`는 보호기간 목표재고, `IP`는 재고포지션, `Q`는 제안 발주량이다. MIT의 주기검토 정책과
Silver·Pyke·Thomas의 order-up-to 구조가 **직접 구현 근거**다. 다만 현재 원천에 발주잔량과
backorder가 충분하지 않아 일부 실행에서는 이용 가능한 필드만 사용한다. 데이터가 없는 값을
0으로 간주한 결과는 실제 재고포지션과 다를 수 있다.

### 5.2 경제적 최적성의 범위

Newsvendor 임계비율은 부족비용이 높을수록 더 높은 수요 분위수와 안전재고를 선택하게 한다.
현재 실험의 `Co:Cu=1:9`는 실제 비용자료에서 추정한 값이 아니라 **실험 가정**이다. 따라서
“경제적으로 최적인 발주량”이라는 표현은 비용비가 주어졌을 때의 조건부 최적량이라는 뜻이다.

실제 최적화를 위해서는 다음 항목이 필요하다.

- 과다비용: 단가, 보유비, 유효기간, 폐기비, 잔존가치
- 과소비용: 긴급구매 단가차, 미충족·지연 비용, 대체품 사용비용
- 조달상태: 주문일, 약속일, 분할입고, 실제 입고일, 미입고 발주량

비용자료가 확보되면 품목·기관별 `Cu`, `Co`를 추정하고, 불확실하면 허용 구간별 민감도와
pinball loss를 함께 제시한다. 분위수 손실을 쓰는 원리는 Koenker & Bassett의 quantile
regression에 근거한다.

### 5.3 실제 계산 예시

사례 B에 `R=30일`, `L=15일`, 월 예측 `3.5175`, 현재 재고포지션 `0`을 적용했다.

```text
보호기간                  H   = 30 + 15 = 45일
보호기간 예상수요          D_H = 3.5175 × 45/30 = 5.2763
예측오차 기반 안전재고      SS  = 1.0552
기본 목표재고              S   = 5.2763 + 1.0552 = 6.3315
재고포지션                 IP  = 0
기본 제안발주량             Q   = max(6.3315 - 0, 0) = 6.3315
외부위험 shadow 목표            = 6.4011
외부위험 shadow buffer          = 0.0696
실제 발주에 외부위험 반영 여부  = false
```

이 예시는 예측 → 보호기간 수요 → 안전재고 → 목표재고 → 재고포지션 차감의 연결을 보여준다.
소수 발주가 불가능한 품목은 포장단위와 최소주문량을 받은 뒤 마지막 단계에서 올림해야 한다.

## 6. 외부위험은 왜 shadow로 분리했는가

### 6.1 기사와 사건 점수

기사의 관련성은 신뢰도 항목별 영향력을 분리한 가중기하평균으로 계산한다.

```text
article_score = exp(Σ α_k log(w_k)),  Σα_k = 1
```

어느 한 항목이 매우 낮으면 단순 산술평균보다 전체 점수가 더 낮아진다. `α_k`와 `w_k`는
문헌에 고정된 값이 아니라 사람이 라벨링한 기사 표본에서 교정해야 하는 **실험 계수**다.

뉴스는 동일 사건군의 기사 수에 따라 novelty weight를 낮추고, 월·품목·위험버킷별 기사점수
합을 데이터 기반 p90 척도로 포화변환한다.

```text
monthly_item_news_risk = 1 - exp(-Σ article_score / bucket_p90_scale)
```

원자재 가격과 무역 경로는 상관된 경로를 같은 군집으로 묶어 군집 안의 최댓값만 남기고,
서로 다른 군집 사이에서만 Noisy-OR를 사용한다.

```text
r_c = max(path_risk_j in cluster c)
combined_risk = 1 - ∏_c(1 - r_c)
```

군집화 없이 Noisy-OR를 적용하면 같은 충격이 여러 경로로 반복되어 위험이 과대평가된다.
Baker·Bloom·Davis와 Caldara·Iacoviello는 뉴스 기반 위험지수의 **원리 근거**이고, HealthMap은
복수 출처와 사람 검토의 설계 근거다. 이 문헌들이 TeamLex 점수의 확률보정을 보증하지는 않는다.

### 6.2 원자재 변동성

관측 주기에 따라 30일 변동성으로 환산한다.

```text
daily:   vol_30d = std(return, 30D) × √30
weekly:  vol_30d = std(return) × √(30/7)
monthly: vol_30d = std(return)
```

주기가 다른 변동성을 같은 배수로 연율화하지 않는다. 이 식은 독립·동분산 수익률의 제곱근
시간척도 가정을 사용한다. 자기상관과 급변구간이 강하면 성립하지 않을 수 있으므로 rolling
backtest와 이상치 검사를 병행한다.

### 6.3 상대순위와 shadow 재고

서로 단위가 다른 뉴스빈도, 원자재 변동성, 무역신호는 절대확률로 해석하지 않는다. 뉴스는
버킷별 양수분포의 p90을 척도로 쓰고, 무역 임계값도 기준분포의 quantile로 보정해 상대적 위치를
사용한다. New York Fed의 GSCPI는 여러 운송·PMI
변수에서 공통 공급망 압력을 추출한다는 **원리**를 제공하지만, TeamLex는 그 PCA 지수를
복제하지 않는다.

```text
demand_uplift       = demand_risk × demand_usage_uplift_max
shadow_prediction   = base_prediction × (1 + demand_uplift)
effective_lead_time = L × (1 + supply_risk × supply_lead_time_multiplier_max)
                      + supply_risk × extra_days_max
shadow_target       = protection_demand + risk_adjusted_safety_stock
risk_buffer         = min(shadow_target - base_target,
                          protection_demand × total_risk_buffer_rate_cap)
```

`demand_usage_uplift_max`, `supply_lead_time_multiplier_max`, `extra_days_max`,
`total_risk_buffer_rate_cap`은 모두 실험 계수다.
holdout 통과 전에는 다음과 같이 실제 목표와 발주량을 바꾸지 않는다.

```text
target_stock = base_stock
recommended_order = base_order
operational_adjustment_enabled = false
block_reason = shadow_only_empirical_holdout_not_passed
```

### 6.4 외부위험의 실제 관측 결과

- 수요전용 baseline WAPE: 38.715%
- 동시점 외부위험 추가: 38.673%, 개선 0.043%p
- 시차 외부위험 추가: 38.628%, 개선 0.087%p
- 척도변환 위험 추가: 38.662%, 개선 0.053%p
- MFDS 공급중단 1,936건 중 마스터 매칭 839건
- 직접 품목 매칭 55건, 성분 매칭 88건, 영향 품목코드 78개
- 영향 시계열 3,193개, 대상 사용량 비중 1.62%
- PP 수입가→2개월 기말재고: 최신 24개월 검증 p=0.4617
- 탐색 90개 경로: Bonferroni 보정 후 유의 0개

가격과 재고의 두 비정상 시계열을 수준값으로 바로 회귀하면 허위 상관이 생길 수 있다. 따라서
ADF 단위근, Johansen·Engle–Granger 공적분, 오차수정모형, Granger 예측인과 검정을 순서대로
적용했다. 여기서 Granger 인과는 구조적 원인 증명이 아니라 “과거 가격이 과거 재고만 사용한
모형보다 미래 재고 예측에 추가 정보를 주는가”라는 제한된 의미다.

최대 개선이 0.087%p이고 직접 노출키도 부족하므로 외부위험을 핵심 예측성과나 인과효과로
주장하지 않는다. shadow 분리는 문헌을 인용하면서도 우리 데이터의 약한 실증을 숨기지 않는
안전장치다.

## 7. 재고정책 시뮬레이션과 안전 gate

### 7.1 기존 시뮬레이션 결과

2025-09~12, 77,575개 시계열의 동일 조건 비교:

- 모델 정책: fill rate 96.12%, stockout-month rate 7.92%, 평균재고 171.40,
  회전율 4.43, WAPE 33.51%
- naive 정책: fill rate 95.31%, stockout-month rate 11.30%, 평균재고 197.39,
  회전율 3.85, WAPE 40.28%
- 모델 대비 naive: fill rate +0.81%p, stockout-month rate -3.38%p,
  평균재고 -13.17%, 회전율 +0.58

이 결과는 더 적은 평균재고로 더 높은 fill rate를 얻을 가능성을 보인다. 다만 평가구간 실제값으로
sigma를 추정했고 초기재고를 첫 목표재고로 두어 절대 성능이 낙관 편향될 수 있다.

서비스 수준 민감도:

- 1.00: fill 96.12%, stockout 7.92%, 평균재고 171.40
- 1.05: fill 96.90%, stockout 6.35%, 평균재고 186.42, 재고 +8.76%
- 1.10: fill 97.50%, stockout 5.10%, 평균재고 201.97, 재고 +17.84%
- 1.20: fill 98.33%, stockout 3.37%, 평균재고 234.25, 재고 +36.67%
- 1.30: fill 98.85%, stockout 2.30%, 평균재고 267.52, 재고 +56.08%

실제 초과·미충족 비용이 없으므로 단일 최적 multiplier는 결정하지 않는다.

### 7.2 상태분류와 억제 규칙

정책 v1.1 전체 실행 참고값:

- 전체 시계열 416,128개 중 원시 비양수 재고 후보 10,910개
- 최근 수요가 있는 stockout 후보 4,546개
- 승인 분류 통과 18개, 동등품 재고로 억제 1개, 최종 긴급부족 후보 17개
- DORMANT 필터 189,791개, stale/missing 검토 109,714개

10,910개에서 17개로 줄어든 99.84%는 정확도가 아니라 안전 gate 통과 결과다. 17개가 실제
긴급부족이라는 정답 라벨은 없다. 최신 signed/positive v1.2로 전수 재실행하기 전까지는
참고값으로만 제공한다.

```text
DORMANT 또는 NOT_OPERATED       → Q = 0
DATA_MISSING 또는 STALE         → Q = NA, 사람 검토
품목·원자재 매핑 충돌           → 외부위험 조정 차단
shadow holdout 미통과           → 기본 Q 유지
```

## 8. 참고문헌 발췌와 모델 적용 범위

이 절은 “문헌을 인용했다”는 목록이 아니라, 각 자료가 어떤 설계 결정을 지지하고 무엇까지는
지지하지 않는지를 명시한다. 원문 발췌는 저작권을 고려해 핵심 구절만 제시한다.

### 8.1 시간순 검증

**Bergmeir & Benítez (2012), _On the use of cross-validation for time series predictor evaluation_**

> “cross-validation techniques led to a more robust model selection”

- 해석: 시계열 구조를 보존한 교차검증은 더 견고한 모델 선택에 도움이 된다.
- 적용: 무작위 분할 대신 시간순 calibration·평가를 사용한다.
- 증거 수준: **직접 설계 근거**.
- 한계: 현재 2025-10~12가 이미 관찰된 진단구간이라는 문제를 없애주지는 않는다.
- 출처: [Information Sciences 논문 DOI](https://doi.org/10.1016/j.ins.2011.12.028)

### 8.2 간헐수요 분류

**Syntetos, Boylan & Croston (2005), _On the categorization of demand patterns_**

> “average inter-demand interval and the squared coefficient of variation”

- 해석: 평균 수요발생 간격과 수요크기 변동계수 제곱으로 수요 패턴을 구분할 수 있다.
- 적용: ADI·CV²를 이용한 smooth·intermittent·erratic·lumpy 분류.
- 증거 수준: **직접 구현 근거**.
- 한계: 세부 임계값과 어떤 예측기가 최선인지는 TeamLex holdout에서 다시 정해야 한다.
- 출처: [Journal of the Operational Research Society 논문 DOI](https://doi.org/10.1057/palgrave.jors.2601841)

### 8.3 주기검토 목표재고

**MIT OpenCourseWare, _Logistics Systems, Lecture 11_**

> “Order S-IP every R time periods.”

- 해석: 매 `R`기간마다 목표수준 `S`에서 재고포지션 `IP`를 뺀 만큼 주문한다.
- 적용: `Q=max(S-IP,0)`인 주기검토 (R,S) 정책.
- 증거 수준: **직접 구현 근거**.
- 한계: 정확한 `IP`에는 on-order·backorder·committed 자료가 필요하다.
- 출처: [MIT OpenCourseWare 강의자료](https://ocw.mit.edu/courses/esd-260j-logistics-systems-fall-2006/8b53c45fd26ffff706d815131e8d177e_lect11.pdf)

**Silver, Pyke & Thomas (2016), _Inventory and Production Management in Supply Chains_, 4th ed.**

- 해석: 주기검토, order-up-to 수준, 안전재고, 비용·서비스 trade-off를 하나의 재고관리 체계로
  설명한다.
- 적용: 보호기간 수요와 안전재고를 합친 `S`의 구조.
- 증거 수준: **직접 구현 근거**.
- 한계: 교재의 구조가 TeamLex의 비용 계수와 성능을 정당화하지는 않는다.
- 출처: [Routledge 도서 정보](https://www.routledge.com/Inventory-and-Production-Management-in-Supply-Chains-Fourth-Edition/Silver-Pyke-Thomas/p/book/9781315374406)

### 8.4 비용비와 분위수 발주

**Olivares, Terwiesch & Cassorla (2008), _Structural Estimation of the Newsvendor Model_**

> “a direct relationship between the overage/underage cost ratio and the probability of overestimating”

- 해석: 과다·과소 비용비는 최적 수요 분위수와 직접 연결된다.
- 적용: `p*=Cu/(Cu+Co)`, `z*=Φ⁻¹(p*)`로 안전재고의 서비스 수준을 결정한다.
- 증거 수준: **직접 수식 근거**.
- 한계: `Co:Cu=1:9` 자체는 논문에서 온 값이 아니라 실제 비용이 없는 현재의 실험 가정이다.
- 출처: [Wharton 공개 논문 PDF](https://faculty.wharton.upenn.edu/wp-content/uploads/2012/04/8-Structural-Estimation-of-Newsvendor.pdf)

**Koenker & Bassett (1978), _Regression Quantiles_**

> “A simple minimization problem yielding the ordinary sample quantiles”

- 해석: 비대칭 절대손실의 최소화로 원하는 분위수를 직접 추정할 수 있다.
- 적용: 비용비별 pinball loss 평가와 향후 조건부 분위수 예측.
- 증거 수준: **평가방법 근거**.
- 한계: 현재 점예측 LightGBM이 자동으로 분위수 예측이 되는 것은 아니다.
- 출처: [Econometrica 논문 DOI](https://doi.org/10.2307/1913643)

### 8.5 뉴스 기반 위험과 복수 출처

**Baker, Bloom & Davis (2016), _Measuring Economic Policy Uncertainty_**

> “based on newspaper coverage frequency”

- 해석: 뉴스 보도 빈도로 관측하기 어려운 정책 불확실성을 지수화할 수 있다.
- 적용: 기사빈도·기간별 상대순위를 위험 신호 후보로 사용한다.
- 증거 수준: **원리 차용**.
- 한계: TeamLex의 기사 점수가 실제 공급중단 확률이라는 뜻은 아니다.
- 출처: [저자 공개 논문 PDF](https://www.policyuncertainty.com/media/BakerBloomDavis.pdf)

**Caldara & Iacoviello (2022), _Measuring Geopolitical Risk_**

> “a news-based measure of adverse geopolitical events and associated risks”

- 해석: 불리한 지정학 사건과 관련 위험을 뉴스에서 측정할 수 있다.
- 적용: 사건유형과 기사 신호를 분리하고 시차효과를 시험한다.
- 증거 수준: **원리 차용**.
- 한계: 논문의 사전·분모·검증체계를 그대로 재현하지 않았으므로 동일 GPR 지수가 아니다.
- 출처: [미국 연방준비제도 연구 페이지](https://www.federalreserve.gov/econres/ifdp/measuring-geopolitical-risk.htm)

**Freifeld, Mandl, Reis & Brownstein (2008), _Surveillance Sans Frontières: Internet-Based Emerging
Infectious Disease Intelligence and the HealthMap Project_**

> “a variety of electronic media sources”

- 해석: 온라인 뉴스, 전문가 보고, 공식 보고 등 서로 다른 출처를 함께 쓰되 출처 특성과
  검증과정을 보존해야 한다.
- 적용: 출처유형, 중복사건 묶음, 사람 검토, 수집 실패상태를 별도 기록한다.
- 증거 수준: **수집·검수 원리 차용**.
- 한계: 질병감시 구조가 의료물품 공급중단 label을 직접 제공하지는 않는다.
- 출처: [PLOS Medicine 원문](https://journals.plos.org/plosmedicine/article?id=10.1371/journal.pmed.0050151)

### 8.6 복합 공급망 신호

**Benigno et al. (2022), _The GSCPI: A New Barometer of Global Supply Chain Pressures_**

> “a new monitoring tool to gauge global supply chain conditions”

- 해석: 운송비와 PMI 등 여러 변수의 공통 변동으로 공급망 압력을 모니터링할 수 있다.
- 적용: 단위가 다른 신호를 표준화하고, 단일 기사 대신 복수 신호의 상대순위를 본다.
- 증거 수준: **원리 차용**.
- 한계: TeamLex는 27개 변수와 PCA를 재현하지 않았으므로 GSCPI를 구현했다고 표현하지 않는다.
- 출처: [New York Fed Staff Report](https://www.newyorkfed.org/research/staff_reports/sr1017)

### 8.7 비정상 시계열과 공적분 검정

**Granger & Newbold (1974), _Spurious Regressions in Econometrics_**

> “Spurious regressions in econometrics”

- 해석: 독립적인 비정상 시계열도 수준값 회귀에서는 유의해 보이는 허위관계를 만들 수 있다.
- 적용: 가격·재고 수준값의 단순 상관을 인과근거로 사용하지 않고 정상성부터 검사한다.
- 증거 수준: **검정 순서의 직접 근거**.
- 한계: 정상성 검사를 했다는 사실만으로 인과성이 확인되는 것은 아니다.
- 출처: [Journal of Econometrics 원문 정보](https://www.sciencedirect.com/science/article/pii/0304407674900347)

**Johansen (1991), _Estimation and Hypothesis Testing of Cointegration Vectors_**

> “the maximum likelihood estimator of the cointegrating relations”

- 해석: 여러 비정상 시계열 사이의 장기 균형관계 차원과 벡터를 최대우도 방식으로 검정한다.
- 적용: 공적분이 있는 다변량 경로의 Johansen trace 검정 후보.
- 증거 수준: **통계방법 직접 근거**.
- 한계: 24개월 표본은 장기관계를 안정적으로 추정하기에 얇다.
- 출처: [Econometric Society 원문 정보](https://www.econometricsociety.org/publications/econometrica/browse/1991/11/01/estimation-and-hypothesis-testing-cointegration-vectors)

**Engle & Granger (1987), _Co-Integration and Error Correction_**

> “connects the moving average, autoregressive, and error correction representations”

- 해석: 공적분된 변수는 단기 변화와 장기 균형 복귀를 오차수정모형으로 연결할 수 있다.
- 적용: 공적분이 확인된 후보에만 ECM/VECM을 적용하는 설계.
- 증거 수준: **통계방법 직접 근거**.
- 한계: 현재 Bonferroni 보정 후 유의 경로가 0개이므로 운영 전이계수는 없다.
- 출처: [Cambridge University Press 공개 원문](https://www.cambridge.org/core/services/aop-cambridge-core/content/view/0C17E5B595B2A3FF5002A4E371C13122/9780511753978c8_p145-172_CBO.pdf/cointegration_and_errorcorrection_representation_estimation_and_testing.pdf)

### 8.8 참고했지만 현재 성과 주장에 사용하지 않는 문헌

- **Syntetos & Boylan (2005)**: Croston 계열의 간헐수요 편향 보정 근거다. 후보모형 비교에는
  포함할 수 있지만 현재 선택 router의 성능을 이 논문으로 대신 증명하지 않는다.
- **Bühlmann & Straub (1970)**: 짧은 개별 이력과 집단 평균의 부분 pooling 후보 근거다.
  최신 월수 집계 오류를 고치고 holdout을 통과하기 전에는 최종 정책 근거로 쓰지 않는다.
- **Dickey & Fuller (1979), Granger (1969)**: 각각 단위근과 예측인과 검정의 근거다. 이 검정의
  실패 결과도 보고하며, p-value를 구조적·의학적 인과로 확대 해석하지 않는다.

이 구분은 참고문헌을 빠뜨린 것이 아니라, 실제 사용한 문헌과 후보로 검토한 문헌을 다른 강도로
인용한 것이다. 심사에서는 “모든 참고문헌을 구현했다”가 아니라 “각 문헌의 역할과 한계를
추적할 수 있다”는 점을 제시한다.

## 9. 정보원에 제공할 산출물과 데이터 요청

### 9.1 제출 패키지

- 본 요약보고서: 목적, 기간, 모집단, 수식, 시간분할, 성능, 한계
- 행 단위 결과: actual/predicted usage, 패턴, 이력월, 재고포지션, 기본·shadow 목표, 발주량
- 품질·예외: signed/positive 대사, 원장대사 위반, 미매핑·충돌, stale·dormant 억제목록
- 재현 manifest: 원본명·byte·SHA-256, Git commit, 패키지 버전, schema hash, 행 수
- 문헌대응표: 수식·참고문헌·적용수준·내부 검증결과·남은 한계

행 단위 결과에는 최소한 기준월·예측월·익명 기관·부서·로컬 품목·대표품목, actual/predicted
usage, 모델명·수요패턴·이력월, current stock·inventory position·base/target stock·order,
외부위험·shadow target·shadow buffer, 억제사유·매핑범위·모델/정책/데이터 버전을 포함한다.

재현 manifest에는 실행일시, Git commit, Python과 핵심 라이브러리 버전, 입력·출력기간,
원본·중간·최종 행 수, schema hash, 성공·실패·제외 행 수와 사유를 포함한다. 확인된 실행환경은
Python 3.14.3, pandas 2.3.3, NumPy 2.5.1, scikit-learn 1.9.0, LightGBM 4.7.0이다.

환경변수는 이름과 존재 여부만 기록하고 `.env`, API key, 비밀번호는 제출하지 않는다. 원본
물품명은 인코딩과 재식별 가능성을 검토한 뒤 별도 제공한다.

### 9.2 운영 해석을 위해 추가로 필요한 데이터

현재 자료만으로 연구실험은 가능하다. 다음 자료는 운영수준의 경제적 최적성과 실제 리드타임을
검증할 때 필요하다.

- 익명 기관코드 3,530개와 운영 DB 기관 3,598개의 검증된 대응표
- 기관 로컬 `USE` 코드와 전역 대표품목의 공식 대응표
- 발주 line: order_id, order_date, ordered_qty, promised_date
- 실제 입고: receipt_date, received_qty, partial_receipt_seq
- 서비스: requested_qty, fulfilled_qty, backorder_qty
- 비용: unit_price, emergency_unit_price, holding/disposal/shortage cost
- 유효기간·폐기: expiry_date, discard_date, discard_qty, discard_reason
- 공급노출: 제조사, 원산지, 표준제품코드, 계약 공급사

### 9.3 제출·표현 금지 항목

- `.env`, API key, DB 비밀번호, 개인정보 또는 환자 단위 기록
- 단위가 다른 품목 수량의 단순 합계
- 승인되지 않은 품목–원자재·질병 관계를 확인된 사실처럼 표현한 결과
- `shadow_risk_target_stock`을 실제 발주 권고로 표시한 파일
- 2025-10~12를 한 번도 보지 않은 final untouched test라고 표현한 문구
- 외부위험 점수를 실제 공급중단 확률 또는 인과효과라고 표현한 문구

## 10. 제출 전 재실행과 판정 기준

최신 조합정책과 shadow 분리는 재실행했으나 일부 대용량 전수 결과는 이전 실행 산출물이다.
외부 제출본 확정 전에 최신 dev에서 다음을 전수 재실행한다.

1. signed/positive v1.2 월별 전처리와 재고상태 분류
2. representative-item 기준 중요도와 pooling 보고서
3. Module C shadow-only 재고권고와 신호별 ablation
4. 입력 hash·schema hash·정책버전을 포함한 run manifest
5. 개인·기관 재식별 위험과 물품명 인코딩 검수

최종 판정은 다음처럼 분리한다.

- **현재 주장 가능**: baseline 대비 예측오차 개선, naive 대비 재고 trade-off 개선 가능성,
  외부위험 shadow 분리, 데이터·매핑 gate의 작동.
- **현재 주장 불가**: 독립 clean test 성능, 자동발주의 실제 비용절감, 뉴스·원자재의 인과효과,
  17개 긴급부족 후보의 정답 정확도.
- **운영 전 필수**: 실제 주문·입고·미충족·비용자료, 승인 매핑, 새 시간구간 평가, 사람 승인.

## 11. 원본 DAT 무결성 manifest

2018~2019 파일:

- `(한국사회보장정보원)_의료재고예측모델 개발 관련 데이터셋(물품재고_0)_2018_2019_수정.DAT`
  - bytes: 217,354,590
  - SHA-256: `0d482484716cef177fb138f2810a51a84e3baf5fb41a06bf27b9c55ef415c6bf`
- `(한국사회보장정보원)_의료재고예측모델 개발 관련 데이터셋(물품재고_1)_2018_2019_수정.DAT`
  - bytes: 216,989,217
  - SHA-256: `ecb81a45f1fea4aa15354716ce99d98e4ca7275ece40633126ba0e3f97e255e0`
- `(한국사회보장정보원)_의료재고예측모델 개발 관련 데이터셋(물품재고_2)_2018_2019_수정.DAT`
  - bytes: 216,658,244
  - SHA-256: `322e21ce0562ad0f3959afeedc2a1486ad3f7876b6cae1850cb558f7d311bdb8`

2024~2025 파일:

- `익스포트_0_수정.DAT`: 192,200,389 bytes, `38569d304a9efbee26eb9cffa95382bf86e5a2cd142bebf6fa917b8cc572fde9`
- `익스포트_1_수정.DAT`: 191,987,796 bytes, `8f4e343b423943faea72c6ad002c024dd14c5129b0d1ea8aaa4dfc7dac819df7`
- `익스포트_2_수정.DAT`: 192,224,286 bytes, `55966a609402dbe64bd180f496611f248cb04b31c7081e39c0ac4b82fbfbbcb0`
- `익스포트_3_수정.DAT`: 192,267,529 bytes, `8a16688dcbcce927f48197b9c462b8448fafe72cc9c6cffcb9aa32b6a2e94c77`
- `익스포트_4_수정.DAT`: 192,217,306 bytes, `faedd3b0291284ca62188014a12ece2ddb9d1a8a394e664fc5f8d6e2081bf82d`
- `익스포트_5_수정.DAT`: 192,207,157 bytes, `68b407db8b015baf6b1cf279154a57ea9f1baaab154a9201e0f897059f80ffc2`
- `익스포트_6_수정.DAT`: 192,180,516 bytes, `df2c5d65ef5e3d1a5139eca8c642ebcc419a75452d14dd72885bb499a96cb7f2`
- `익스포트_7_수정.DAT`: 192,060,833 bytes, `19d939c01ce72a92461f9fb8057ce399f0cbe21664485dcb6ebcebb3dc8c0f8a`
- `익스포트_8_수정.DAT`: 192,069,548 bytes, `c40d166cabcbf0b82179b4b9da3293eca5a68a258cd2690a1820d3706214c6aa`
- `익스포트_9_수정.DAT`: 192,216,518 bytes, `65abc4aa74cd0c8b01203801c72009c028ae93cdbb4b62c49bee7b6033917532`

이 manifest는 현재 로컬에 보존된 물품재고 DAT 13개 기준이다. 약성분 원본은 가공 산출물과
통계는 확인되지만 동일 raw 디렉터리에 원본 파일이 남아 있지 않아, 외부 제출 전 원본 파일명·
byte·SHA-256을 별도로 복구해야 한다.

## 12. 결론

현재 모델은 연구 수준에서 baseline보다 낮은 예측오차와 naive보다 나은 재고 trade-off를
보였다. 더 중요한 점은 예측값을 바로 발주로 부르지 않고, 문헌에 근거한 (R,S) 구조·비용비·
안전재고를 거쳐 발주량으로 변환하고, 실증이 약한 외부위험은 shadow로 격리했다는 것이다.

참고문헌은 문제정의와 수식의 방향을 정당화한다. 그러나 TeamLex의 계수, 데이터 매핑, 성능,
운영효과는 문헌이 아니라 정보원 데이터의 독립 검증으로 입증해야 한다. 따라서 본 보고서는
성과와 함께 적용범위, 실패한 실험, 실험 가정, 추가 데이터 요구사항을 같은 비중으로 제공한다.
