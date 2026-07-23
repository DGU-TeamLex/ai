# 모듈 C: 외부 위험 기반 재고 조정

## 1. 목적과 적용 경계

모듈 C는 사용량 예측값을 직접 대체하지 않는다. 기존 시계열 모델의 월 예상 사용량을
기준으로 뉴스, 원자재 가격, 조달 위험을 0~1 점수로 변환하고 목표 재고량을 조정한다.

```text
raw_stock 사용량 이력 -> 기본 사용량 예측 D
외부 뉴스 -> 사건/질병/원자재 코드 -> 품목별 뉴스 위험
외부 가격 -> 시장요인 위험 -> 원자재 -> 품목별 가격 위험
품목별 수요/공급 위험 + D -> 동적 리드타임/안전재고 -> target_stock
```

수요 경로와 공급 경로는 분리한다.

| 경로 | 승인 관계 | 반영 위치 |
|---|---|---|
| 질병 뉴스 -> 의료용품 | `has_approved_demand_mapping` | 예상 사용량 상향 |
| 공급 뉴스 -> 원자재 -> 의료용품 | `has_approved_material_mapping` | 조달 리드타임·안전재고 상향 |
| 시장가격 -> 원자재 -> 의료용품 | 승인된 두 단계 매핑 | 조달 리드타임·안전재고 상향 |

어느 한 관계라도 미승인이면 해당 경로의 점수는 0이다. 자동 분류 후보와 웹 검색 후보는
운영 재고량에 바로 사용하지 않는다.

## 2. 외부 데이터 공급자

### 가격 데이터

| 공급자 | 용도 | 설정 |
|---|---|---|
| `csv` | 계약한 선물/현물/평가지표와 나프타 직접 가격 | `COMMODITY_DATA_PATH` |
| `alpha_vantage` | Brent, WTI, 천연가스, 금속·농산물 공개 지표 | `ALPHA_VANTAGE_API_KEY` |
| `fred` | 검증된 FRED 시계열을 시장 대리변수로 사용 | `FRED_API_KEY` |
| `nasdaq_data_link` | 접근 권한이 있는 선물·시장 데이터셋 | `NASDAQ_DATA_LINK_API_KEY` |
| `sample` | 합성 테스트 fixture, 운영 사용 금지 | 명시적 테스트 실행만 허용 |

실제 거래소 선물 정산가 또는 가격평가기관의 나프타 시계열은 라이선스가 필요할 수 있다.
따라서 공개 원유 지표를 나프타 가격이라고 간주하지 않고 `is_proxy`, `proxy_quality`,
`transmission_weight`를 기록한다. 운영 기본값은 `disabled`이며 API 실패 시 합성 데이터로
자동 대체하지 않는다.

시장 시계열 등록 파일:

```text
data/mapping/market_series_registry.csv
```

표준 입력 스키마:

```text
date,market_factor_id,price,volume,inventory,open_interest,
provider,series_id,price_type,currency,unit,is_proxy
```

### 뉴스 데이터

| 공급자 | 용도 | 설정 |
|---|---|---|
| `gdelt` | 외부 기사 후보 수집 | `NEWS_START_DATE`, `NEWS_END_DATE` |
| `csv` | 검수·계약된 뉴스/이벤트 이력 | `NEWS_DATA_PATH` |
| `sample` | 점수 계산 smoke test, 운영 사용 금지 | 명시적 테스트 실행만 허용 |

규칙 기반 분석기는 기사에서 사건 유형, 원자재 메타코드, 외부 사건 코드를 추출한다.
예를 들어 중동/호르무즈와 나프타가 동시에 명시된 기사만
`MIDEAST_NAPHTHA_PETROCHEM_SHOCK` 후보로 태그한다. 태그 자체는 사실 판정이나 인과 검증을
대체하지 않으며 기사별 감사 파일에 출처와 함께 남긴다.

## 3. 매핑 그래프

시장요인에서 품목까지의 전파 경로는 다음 두 파일로 제한한다.

```text
data/mapping/material_market_factor_mapping.csv
data/mapping/stock_item_material_mapping.csv
```

첫 파일은 `시장요인 -> 원자재`, 두 번째 파일은 `원자재 -> 로컬 품목`과
`질병 수요코드 -> 로컬 품목` 관계다. 두 파일 모두 `review_status=approved`인 행만 읽는다.
질병 경로는 `demand_risk_meta_code`, 공급 경로는 `raw_material_meta_code`로 구분한다.
PP/PE/PVC의 나프타 직접 경로와 Brent 대리 경로도 각각 별도 가중치와 근거 URL을 갖는다.

전파 경로 가중치:

```text
path_weight
= transmission_weight
* proxy_quality
* mapping_weight
* exposure_score
* mapping_confidence_score

item_market_risk = 1 - product(1 - market_factor_risk * path_weight)
```

한 품목에 여러 시장요인이 연결되면 단순 합 대신 결합확률식으로 합쳐 1을 넘지 않게 한다.

## 4. 위험 점수

설정 파일은 `data/mapping/module_c_risk_weights.json`이다. 현재 계수는 실증 인과계수가
아닌 초기 정책 경계값이며 `calibration_status=policy_seed_requires_backtest`로 모든 감사
산출물에 기록한다. 가격 특성은 미래값을 사용하지 않는 as-of 방식으로 계산한다.

```text
market_factor_risk
= 0.45 * positive_30d_return_risk
 + 0.30 * rolling_30d_volatility_risk
 + 0.25 * price_vs_causal_90d_mean_risk

supply_risk
= 0.45 * supply_news_risk
 + 0.20 * material_news_risk
 + 0.35 * market_price_risk

total_risk = max(demand_risk, supply_risk)
```

가격 수익률, 변동성, 가격수준은 설정된 고정 임계값으로 0~1화한다. 전체 기간 최솟값과
최댓값으로 정규화하지 않으므로 과거 점수가 미래 가격에 의해 바뀌지 않는다.

## 5. 재고 조정식

월 사용량 예측을 `D`, 검토주기를 `R`, 원래 리드타임을 `L`, 수요·공급 위험을 각각
`rD`, `rS`라고 한다.

```text
base_demand = D * (R + L) / 30
base_stock  = base_demand * 1.20

D' = D * (1 + 0.35 * rD)
L' = L * (1 + 0.50 * rS) + 14 * rS
S' = 0.20 + 0.25 * rS

unconstrained_target = D' * (R + L') / 30 * (1 + S')
risk_buffer = min(
  max(unconstrained_target - base_stock, 0),
  base_demand * 0.75
)
target_stock = base_stock + risk_buffer
recommended_order = max(target_stock - inventory_position, 0)
```

### 기준 공급레벨과 동적 사건레벨

`baseline_supply_risk_level`은
`data/mapping/supply_risk_level_policy.json`에서 공급 메타코드별로 결정론적으로 다시
계산한다. 기존 DB나 CSV에 남아 있는 `supply_risk_level`은 입력 정답으로 사용하지 않는다.

```text
baseline_supply_risk_level   고정 공급구조: NORMAL/CAUTION/WARNING/CRITICAL
module_c_event_supply_risk_level 월별 뉴스·가격 사건: normal/watch/warning/critical
module_c_supply_risk         월별 연속 점수 0~1
```

`PANDEMIC_SURGE_SENSITIVE` 같은 수요 코드는 기준 공급레벨을 올리지 않는다. `CRITICAL`은
국가필수, 단일 수입원, 대체불가 세 근거와 검수 승인이 모두 있어야 한다. 미매핑 코드는
`NORMAL`로 계산하되 반드시 검토 플래그를 남긴다.

레벨 기반 SS/ROP가 필요한 경우 `mean_daily_usage`, `daily_demand_stddev`,
`lead_time_days`가 모두 명시되어야 한다. 이 입력이 없을 때 분산이나 리드타임을 임의로
만들지 않는다. 레벨 기반 SS/ROP와 위 연속형 `target_stock` 정책은 대안 관계이며 결과를
서로 합산하지 않는다.

외부 수요 feature를 사용한 학습 모델이 기본 예측으로 선택되면 `rD`의 고정 35% 상향은
다시 적용하지 않는다. 이 경우 `external_demand_signal_in_forecast=true`를 남겨 수요 신호가
한 번만 반영되게 한다. 공급 위험은 사용량이 아니라 `L'`과 `S'`에만 반영한다.

## 6. 학습 모델과 선택

`stock_model_d_module_c`는 승인된 모듈 C feature만 사용하는 Tweedie challenger다. 기존
baseline 및 사용량 전용 모델과 확장형 시계열 교차검증 WAPE로 비교한다. 학습기간에
0이 아닌 승인 신호가 없으면 모델 D를 만들지 않고 manifest에 `skipped` 사유를 기록한다.

운영 재고 권고는 어떤 사용량 모델이 선택되어도 마지막에 동일한 모듈 C 정책을 거친다.
따라서 예측 모델 성능과 재고 위험 정책을 별도로 감사할 수 있다.

## 7. 실행과 산출물

```bash
python -m src.news.news_risk_scorer
python -m src.commodity.commodity_risk_scorer
python -m src.module_c.pipeline
python -m src.feature_engineering
python -m src.modeling.training
python -m src.modeling.prediction
```

| 산출물 | 내용 |
|---|---|
| `outputs/stock_module_c_risk_scores.csv` | 월·로컬 품목별 최종 위험 점수 |
| `outputs/stock_module_c_risk_audit.csv` | 신호별 원점수·가중치·승인 여부·사건 코드 |
| `outputs/stock_module_c_alerts.csv` | watch 이상 품목과 권고 조치 |
| `outputs/module_c_material_exposure_candidates.csv` | 품목-원자재-시장요인 승인 후보 |
| `outputs/module_c_supply_risk_level_audit.csv` | 현재 메타코드의 기준 공급레벨·미매핑 감사 |
| `outputs/module_c_supply_risk_quality_classified.csv` | PASS/REVIEW/BLOCK 오류 분류 전체 |
| `outputs/module_c_supply_risk_quality_issues.csv` | 오류코드별 상세 감사 로그 |
| `outputs/module_c_supply_risk_quality_quarantine.csv` | 재고 반영 차단 행 |
| `outputs/module_c_supply_risk_quality_report.json` | 오류 건수와 배치 반영 가능 여부 |
| `data/sample/module_c_supply_risk_quality_sample_1000.csv` | 대표품목 다양성을 우선한 오류 검토 샘플 |
| `outputs/module_c_run_report.json` | 실행 건수와 승인 게이트 결과 |
| `outputs/stock_commodity_risk_audit.csv` | 가격 전파 경로별 기여도와 근거 |

## 8. 현재 데이터 적용 결과

2026-07-18 전체 분류 데이터 점검 결과:

```text
승인 품목 분류 로컬 품목: 4,969
품목-원자재 후보 관계: 11,383
시장요인 연결 후보 관계: 6,741
원자재 관계 승인: 0
운영 위험 반영 가능 관계: 0
```

현재 후보는 모두 `material_review_status=needs_review`다. 따라서 코드는 실행 가능하지만
실제 데이터의 `target_stock`에는 외부 위험을 합산하지 않는다. 먼저 사용량이 큰 후보부터
제조사 자료, 규격서, 공공 품목정보로 원자재 관계를 검토하고 승인 매핑 파일에 반영해야
한다. 승인 전 상태에서 위험을 강제로 적용하는 것은 금지한다.

또한 보유 사용량 이력은 2024~2025년이므로 2026년 4월 사건 효과를 현재 데이터만으로
백테스트할 수 없다. 2026년 사용량·조달지연·품절 이력이 들어온 뒤 사건 전후 비교와
시계열 교차검증으로 가중치를 재보정한다.
