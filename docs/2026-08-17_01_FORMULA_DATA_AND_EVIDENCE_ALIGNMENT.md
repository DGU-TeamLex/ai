# 수식·데이터·참고문헌 정합성 및 실험 정책

작성 2026-08-17. 이 문서는 참고문헌을 많이 나열하는 것이 아니라, 각 문헌이 실제 코드의
어느 결정을 지지하는지와 어디부터가 TeamLex의 실험적 가정인지를 구분한다.

## 1. 근거 등급

- **직접 구현**: 문헌의 핵심 정의·분할·통계식을 코드가 그대로 따른다.
- **원리 차용**: 문헌이 변수 또는 구조 선택을 지지하지만 현재 점수식은 TeamLex의 변형이다.
- **내부 실증**: 문헌이 아니라 시간순 validation/holdout 결과가 선택 근거다.
- **실험 prior**: 아직 결과 기반 보정이 끝나지 않은 초기 계수다.

참고문헌이 변수 선택을 지지한다고 해서 현재 숫자까지 증명하는 것은 아니다. 심사와 발표에서는
이 네 등급을 섞지 않는다.

## 2. 사용량 예측 모델

### 시간 분할과 검증

롤링 원점 검증은 Bergmeir & Benítez(2012)의 시계열 평가 원칙을 **직접 구현**한다.
미래 월을 학습에 섞지 않고 각 fold의 train 종료 뒤를 평가한다. 앙상블은 validation에서
가중치를 선택하고 test는 마지막 평가에만 사용한다. 이 부분은 문헌 원칙과 코드가 정합적이다.

### 수요 패턴

smooth/intermittent/erratic/lumpy 구분은 Syntetos & Boylan(2005), Syntetos, Boylan &
Croston(2005)의 ADI/CV² 분류를 **직접 구현 또는 직접 파생**한 것이다. 패턴별 모델 가중치는
논문 고정값이 아니라 validation WAPE로 선택한 **내부 실증** 결과다.

### ML 모델과 앙상블

LightGBM·XGBoost·Tweedie 계열은 해당 알고리즘의 표준 구현을 사용하지만, 현재 하이퍼파라미터와
혼합비는 참고문헌이 직접 보증하지 않는다. `forecast_ensemble_policy.json`의 혼합비는 시간순
validation 선택 및 3개월 재사용 평가구간에 근거한다. 2025-10~12는 이전 실험에서 이미
검사했으므로 최종 untouched test가 아니다. WAPE 개선은 내부 참고 근거지만 장기간 일반화가
확정된 것은 아니다.

현재 자료에서 뉴스 피처는 사용량 예측 WAPE를 개선하지 못했다. 따라서 뉴스·원자재는
“수요예측을 반드시 개선하는 변수”로 주장하지 않고 별도 위험·재고정책 신호로 평가한다.

## 3. 뉴스 위험

Baker, Bloom & Davis(2016)는 전체 기사 대비 위험기사 비율과 사람 검수를, Caldara &
Iacoviello(2022)는 사건 유형별 뉴스 지수화를, Freifeld et al.(2008)은 출처 결합·분류·중복
제거를 지지한다. 이 문헌들은 event type, source, recency, novelty, human review라는 변수
선택의 근거이며 현재 계수의 직접 출처는 아니다.

기사점수는 다음 가중 기하평균을 쓴다.

```text
article_score = exp(sum(alpha_i * log(weight_i)))
sum(alpha_i) = 1
```

`alpha_i`는 `news_risk_weights.yaml`에 있으며 **실험 prior**다. 단순 곱셈이 항 수에 따라
0으로 붕괴하는 문제를 피하면서 항별 영향력을 분리한다. 향후 검수 사건과 다음 달 수요·품절·
리드타임 결과로 ablation 및 재보정해야 한다.

월별 점수의 p90 scale은 상대 순위 지수다. 확률로 해석하지 않는다. 경보 임계값의 절대적
의미를 주장하려면 고정 train scale 또는 사건 결과 calibration이 추가로 필요하다.

## 4. 원자재·무역 위험

Benigno et al.(2022)의 GSCPI는 여러 공급망 지표를 데이터 기반으로 결합해야 한다는
**원리 차용** 근거다. 현재 구현은 PCA 재현이 아니라 가격 임계값·매핑경로·무역 구성요소를
결합한 TeamLex 위험지수다.

Granger & Newbold(1974), Dickey–Fuller(1979), Johansen(1991), Engle–Granger(1987),
Granger(1969)는 원자재 가격전이 분석의 단위근·공적분·VECM·인과성 검증을 지지한다.
그러나 현재 탐색 90건은 Bonferroni 보정 후 유의한 경로가 없고, PP 2개월 경로도 24개월
검증에서 p=0.4617로 재현되지 않았다. 현재 외부→재고 인과성을 운영 근거로 주장하지 않는다.

가격 변동성은 관측 빈도별로 계산한다.

```text
daily:   std(daily return, 30D) * sqrt(30)
weekly:  std(weekly return) * sqrt(30/7)
monthly: std(monthly return), 추가 확대 없음
```

상관된 유가 계열은 같은 `OIL_FEEDSTOCK` 군집으로 묶어 군집 내부 최댓값을 쓰고, 독립적인
군집 사이에서만 noisy-OR를 사용한다. 이는 동일 충격의 반복계산을 줄이는 실험 설계다.

## 5. 안전재고와 경제적 발주식

Silver, Pyke & Thomas(2016)의 정기검토 `(R,S)` 정책에서 보호기간은 `R+L`이다. 이 구조는
**직접 구현**한다. 기존 `보호기간 수요의 20%`는 불확실성 근거가 약해 fallback으로 내렸다.

현재 실험식은 고전적 newsvendor critical fractile과 예측오차를 결합한다.

```text
Cu = 품절 1단위 비용
Co = 초과재고 1단위 비용
p* = Cu / (Cu + Co)
z* = Phi^-1(p*)
sigma_RL = monthly_error_std * sqrt((R + L) / 30)
SS = z* * sigma_RL
S = forecast_demand(R + L) + SS
Q = max(S - (on_hand + on_order - backorder), 0)
```

기준 비용비 `Co:Cu = 1:9`는 서비스 수준 90%를 만드는 **실험 prior**이며 문헌 고정값이 아니다.
폐기·보관·긴급구매·결품 비용이 확보되면 실제 원가로 교체한다. 공급위험이 리드타임,
불확실성, 품절비용을 높인다는 경로는 현재 실험 가정이다. 실제 목표재고와 발주량은 base를
유지하고, 위험 조정값은 별도 `shadow_*` 열에만 계산한다. 독립 holdout을 통과하기 전 운영
조정을 활성화하지 않는다.

Koenker & Bassett(1978)의 pinball loss는 비대칭 손실과 서비스 수준 평가의 직접 근거다.
`combination_experiment.py`에서 calibration 구간으로 buffer를 선택하고 별도 평가구간에서
확인한다. 이 평가구간은 이미 검사되었으므로 final clean holdout으로 표현하지 않는다.

## 6. 데이터 수집 변경

- 실험 모집단은 `raw_stock` 2024~2025의 기관·부서·품목·월로 고정하고 외부 전용 품목을
  union하지 않는다.
- 외부 데이터는 승인된 `representative_item_id → local item → stock_item_key` fanout을
  통과한 원천 품목에만 left join한다. 충돌은 quarantine하고 미매핑은 0위험으로 바꾸지 않는다.
- GDELT archive는 25% 표본을 유지하되 고정 정각 대신 quarter-hour를 순환한다.
- URL의 `utm_*`, `fbclid`, `gclid` 등을 제거한 canonical URL로 중복 제거한다.
- 같은 사건의 fallback 군집은 일 단위가 아니라 사건·국가·물질·키워드·주 단위로 묶는다.
- 뉴스 archive finalize 시 SHA-256, 표본전략, 월별·카테고리별 건수 provenance를 쓴다.
- 합성 뉴스는 비율과 무관하게 기본 실패하고, GKG 체크포인트는 성공·재시도·영구누락과
  수집 계약 hash를 기록한다.
- 일별·주별·월별 시장가격의 변동성 단위를 분리한다.

본문 없는 제목 기반 뉴스 분류, 25% 표본, 국가 탐지 규칙은 여전히 한계다. 향후 사람 검수
표본으로 precision/recall을 제시해야 한다.

## 7. 심사 시 주장 가능한 범위

주장 가능:

- 시간순 validation과 별도 평가구간을 사용했다.
- 간헐수요 분류와 정기검토 보호기간은 문헌의 핵심 정의와 일치한다.
- 뉴스·공급망 문헌에서 변수와 구조를 선택하고 의료재고 맥락에 맞게 변형했다.
- 문헌 값, 내부 실증값, 실험 prior를 분리했다.
- 안전재고를 예측오차와 상대 비용에 연결했다.

아직 주장하면 안 됨:

- 뉴스점수가 실제 공급차질 확률이다.
- 참고문헌이 현재 뉴스 alpha 또는 비용비 1:9를 직접 증명한다.
- 모든 원자재 경로에 인과성이 확인됐다.
- 외부신호가 사용량 예측을 유의하게 개선했다.
- 현재 발주식이 실제 비용의 전역 최적해다.
- 2025-10~12가 한 번도 보지 않은 final test다.

## 8. 주요 참고문헌과 로컬 근거

- Bergmeir, C. & Benítez, J. M. (2012), *Information Sciences* 191:192–213.
- Syntetos, A. A. & Boylan, J. E. (2005), *International Journal of Forecasting* 21(2):303–314.
- Syntetos, Boylan & Croston (2005), *Journal of the Operational Research Society* 56(5):495–503.
- Baker, Bloom & Davis (2016), *Quarterly Journal of Economics* 131(4):1593–1636.
- Caldara & Iacoviello (2022), *American Economic Review* 112(4):1194–1225.
- Freifeld et al. (2008), *PLoS Medicine* 5(7):e151.
- Benigno et al. (2022), Global Supply Chain Pressure Index methodology.
- Silver, Pyke & Thomas (2016), *Inventory and Production Management in Supply Chains*, 4th ed.
- Koenker & Bassett (1978), *Econometrica* 46(1):33–50.
- Bühlmann & Straub (1970), *Bulletin of the Swiss Association of Actuaries* 70:111–133.
- 상세 근거와 기존 실측치는 `docs/2026-08-11_01_METHODOLOGY_EVIDENCE.md` 및
  `docs/2026-07-06_01_news_risk_weight_references_teamlex.md`를 따른다.
