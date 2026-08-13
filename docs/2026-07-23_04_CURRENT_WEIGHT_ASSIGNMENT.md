# 현재 가중치 할당 및 적용 현황

작성일: 2026-07-23
코드 기준 버전: `module-c-v1.1`

## 1. 먼저 알아야 할 결론

현재 위험 가중치는 학습으로 최적화된 인과계수가 아니다.

```text
calibration_status = policy_seed_requires_backtest
coefficient_basis  = initial_policy_bounds_not_empirical_causal_estimates
```

즉, 지금 값은 문헌과 운영 판단으로 제한 범위를 정한 초기 정책값이다. 각 가중치
묶음의 합은 코드에서 1인지 검증하지만, 결품 감소율이나 비용 최소화 실적으로 보정된
최종값은 아니다.

현재 시스템의 계산 순서는 다음과 같다.

```text
사용량 이력
  -> 사용량 예측
  -> 기본재고량

뉴스
  -> 질병 수요위험 / 공급 뉴스위험 / 원자재 뉴스위험

원자재 가격
  -> 시장가격위험

HS 수출입
  -> 무역위험

승인 게이트
  -> Module C 수요위험 / 공급위험
  -> 리드타임·안전재고율 조정
  -> 최종 목표재고량
```

가중치 정본은 `data/mapping/module_c_risk_weights.json`이고, 뉴스 세부 가중치
정본은 `data/mapping/news_risk_weights.yaml`이다.

## 2. 사용량 예측모델의 가중치

현재 검증에서 선택된 모델은 `stock_model_a_usage_only`이다.

| 항목 | 현재 상태 |
|---|---|
| 알고리즘 | LightGBM L1 회귀 |
| 입력 feature 수 | 40 |
| 외부 뉴스 사용 | 아니오 |
| 원자재 가격 사용 | 아니오 |
| Module C feature 사용 | 아니오 |
| 검증 WAPE | 40.1868% |

LightGBM의 입력 feature에는 사람이 정한 고정 가중치가 없다. 학습된 트리 분할이
예측에 미치는 영향은 `outputs/feature_importance_lightgbm.csv`에서 확인한다.

따라서 현재 외부 위험은 사용량 예측값 자체를 만드는 단계보다, 예측 이후 목표재고량을
조정하는 Module C 정책층에서 주로 적용된다.

## 3. 뉴스 기사 점수

기사 한 건과 품목 한 건의 점수는 다음 곱으로 계산한다.

```text
article_score
  = event_type_weight
  * severity_weight
  * confidence
  * source_weight
  * item_relevance_weight
  * mapping_weight
  * exposure_weight
  * country_weight
  * recency_weight
  * novelty_weight
```

### 3.1 사건 유형

| 사건 유형 | 가중치 | 적재 위험축 |
|---|---:|---|
| 감염병 유행 | 1.00 | 질병 수요 |
| 전쟁·무력분쟁 | 1.00 | 공급 |
| 수출제한·제재 | 1.00 | 공급 |
| 공장 가동중단 | 0.85 | 공급 |
| 항만·물류 차질 | 0.80 | 공급 |
| 원자재 부족·가격급등 | 0.70 | 원자재 |
| 정책·규제 불확실성 | 0.55 | 공급 |
| 일반 경제 불확실성 | 0.30 | 공급 |

### 3.2 출처, 심각도, 품목 관련성

| 구분 | 값 | 가중치 |
|---|---|---:|
| 출처 | 정부·국제기구 | 1.00 |
| 출처 | 전문 모니터링 기관 | 0.90 |
| 출처 | 주요 통신·언론 | 0.75 |
| 출처 | 산업 전문매체 | 0.65 |
| 출처 | 지역 언론 | 0.50 |
| 출처 | SNS·블로그 | 0.25 |
| 출처 | 미확인 | 0.50 |
| 심각도 | critical | 1.00 |
| 심각도 | high | 0.80 |
| 심각도 | medium | 0.50 |
| 심각도 | low | 0.20 |
| 관련성 | 물품 직접 일치 | 1.00 |
| 관련성 | 품목군 일치 | 0.80 |
| 관련성 | 원자재 일치 | 0.60 |
| 관련성 | 일반 의료공급 관련 | 0.35 |
| 관련성 | 일반 거시위험 | 0.15 |

### 3.3 기타 계수

```text
confidence
  = 0.70 * 분류기 신뢰도
  + 0.30 * 추출 필드 완성도

exposure_weight
  = 0.30 + 0.70 * exposure_score

recency_weight
  = exp(-ln(2) * 기사 경과일 / 사건별 반감기)

novelty_weight
  = 1 / sqrt(1 + 동일사건 중복기사 수)
```

사건별 반감기는 전쟁·수출제한 60일, 감염병·공장중단 30일, 항만·원자재 충격
21일, 정책 불확실성 45일, 일반 경제 불확실성 30일이다.

국가 가중치는 한국 1.0, 말레이시아 0.7, Global 0.6, 미확인 및 미등록 국가
0.5이다.

한 달의 기사 점수는 단순합을 그대로 사용하지 않고 다음과 같이 포화시킨다.

```text
monthly_bucket_risk = 1 - exp(-sum(article_score))
```

현재 실행 결과에서 뉴스 점수와 기사 감사 파일은 모두 0행이다. 따라서 현재 Module C에
실제로 들어간 뉴스 기여도도 0이다.

주의할 점은 뉴스 경로에서는 `mapping_weight`와 변환된 `exposure_weight`는 사용하지만
`mapping_confidence`는 직접 곱하지 않는다는 것이다. 가격·무역 경로와 신뢰도 처리
방식이 다르므로 다음 보정 전에 통일 여부를 결정해야 한다.

## 4. 원자재 시장가격 가중치

가격 신호는 세 지표를 각각 0~1로 정규화한 뒤 결합한다.

| 시장 지표 | 내부 가중치 | 위험 1.0 도달 기준 |
|---|---:|---:|
| 30일 가격 상승률 | 0.45 | +20% |
| 30일 변동성 | 0.30 | 12% |
| 90일 평균 대비 가격 | 0.25 | +15% |

```text
market_factor_risk
  = 0.45 * min(max(return_30d, 0) / 0.20, 1)
  + 0.30 * min(volatility_30d / 0.12, 1)
  + 0.25 * min(max(price_vs_90d_mean, 0) / 0.15, 1)
```

물품-원자재-시장가격 연결 경로에는 다음 감쇠계수를 모두 곱한다.

```text
market_path_weight
  = 원자재-시장 전파가중치
  * 시장 대리변수 품질
  * 물품-원자재 매핑가중치
  * 물품 원자재 노출도
  * 물품-원자재 매핑신뢰도

path_contribution = market_factor_risk * market_path_weight
```

매핑신뢰도는 `verified=1.00`, `high=0.90`, `medium=0.65`, `low=0.35`로
변환한다. 여러 시장 경로가 있으면 `1 - product(1 - path_contribution)`로 결합한다.

현재 PP 경로는 다음과 같다.

| PP 시장 경로 | 전파가중치 | 대리변수 품질 | 시차 설정 |
|---|---:|---:|---:|
| Naphtha | 0.80 | 1.00 | 21일 |
| Brent | 0.20 | 0.60 | 21일 |

현재 가격 산출물에는 Brent만 2024-01~2025-12의 24개월 데이터가 있다. Naphtha
경로는 승인되어 있지만 현재 산출물에는 관측값이 없다.

또한 `lag_days`는 매핑과 감사 파일에는 저장되지만 현재 점수 코드에서 날짜를 실제로
이동시키지는 않는다. 현재 계산은 동일 월 결합이며, 설정된 21일 시차는 아직 운영
계산에 반영되지 않는다.

## 5. 수출입 위험 가중치

HS 코드별 위험은 다음 네 신호로 계산한다.

| 수출입 신호 | 내부 가중치 | 정규화 기준 |
|---|---:|---|
| 수입 중량 전년동월 감소 | 0.35 | 30% 감소 시 위험 1.0 |
| 수입 단가 전년동월 상승 | 0.25 | 40% 상승 시 위험 1.0 |
| 수입국 집중도 | 0.25 | Top1·HHI 중 큰 값이 0.50 초과 |
| 순수입 의존도 | 0.15 | `(수입액-수출액)+ / 수입액` |

```text
hs_trade_risk
  = 0.35 * import_volume_decline_risk
  + 0.25 * import_unit_value_increase_risk
  + 0.25 * country_concentration_risk
  + 0.15 * net_import_exposure_risk
```

국가 집중도는 추적 국가의 수입액 커버리지가 80% 이상인 월에만 사용한다.

물품별 무역위험에는 다음 경로가중치를 곱한다.

```text
trade_path_weight
  = 물품-원자재 매핑가중치
  * 원자재 노출도
  * 물품-원자재 매핑신뢰도
  * 원자재-HS 매핑가중치
  * HS 대리변수 품질
```

여러 HS 경로는 시장가격과 마찬가지로
`1 - product(1 - hs_trade_risk * trade_path_weight)`로 결합한다.

현재 승인된 HS 경로는 한 개뿐이다.

| 원자재 | HS 코드 | 매핑가중치 | 대리변수 품질 |
|---|---|---:|---:|
| `POLYPROPYLENE_PP` | `3902100000` | 1.00 | 1.00 |

## 6. Module C 결합 가중치

### 6.1 수요위험

승인된 질병-품목 경로만 통과하며 별도 축소계수 없이 사용한다.

```text
demand_risk = disease_news_risk * approved_demand_gate
```

### 6.2 기본 공급위험

| 공급 신호 | 가중치 |
|---|---:|
| 공급 뉴스 | 0.45 |
| 원자재 뉴스 | 0.20 |
| 원자재 시장가격 | 0.35 |

```text
base_supply_risk
  = 0.45 * supply_news_risk
  + 0.20 * material_news_risk
  + 0.35 * market_price_risk
```

### 6.3 무역위험 overlay

무역위험은 위 세 공급 가중치의 합에 포함되지 않고 0.25 강도의 overlay로 결합한다.

```text
trade_pressure = 0.25 * trade_risk

supply_risk
  = 1 - (1 - base_supply_risk) * (1 - trade_pressure)
```

이 식은 기존 공급위험과 무역위험을 단순 합산하지 않아 중복 위험이 커지는 것을
제한한다. 표시되는 `module_c_trade_contribution`은
`supply_risk - base_supply_risk`이다.

최종 위험은 수요와 공급 중 큰 값을 사용한다.

```text
total_risk = max(demand_risk, supply_risk)
```

## 7. 최종 재고량 조정계수

기본재고량은 다음과 같다.

```text
protection_period_demand
  = predicted_usage * (review_period_days + lead_time_days) / 30

safety_stock = protection_period_demand * 0.20
base_stock   = protection_period_demand + safety_stock
```

Module C 위험을 통과한 경우 다음 정책을 적용한다.

| 조정 항목 | 계산식 | 위험 1.0일 때 최대 변화 |
|---|---|---:|
| 예상 사용량 | `predicted_usage * (1 + 0.35*demand_risk)` | +35% |
| 리드타임 배수 | `lead_time * (1 + 0.50*supply_risk)` | +50% |
| 추가 리드타임 | `14*supply_risk` | +14일 |
| 안전재고율 | `0.20 + 0.25*supply_risk` | 20%에서 45% |
| 총 위험버퍼 상한 | 원래 보호기간 수요의 75% | +75% 이내 |

수요 신호가 이미 예측모델에 포함됐다는 표시가 있으면 수요 35% 상향은 다시 적용하지
않는다.

경보 구간은 다음과 같다.

| 위험 점수 | 상태 |
|---:|---|
| 0.00 이상 0.30 미만 | normal |
| 0.30 이상 0.55 미만 | watch |
| 0.55 이상 0.75 미만 | warning |
| 0.75 이상 | critical |

## 8. PP 승인 경로의 실제 계산 예시

현재 승인된 2,297개 품목은 모두 다음 값을 사용한다.

```text
물품-PP 매핑가중치 = 0.70
PP 노출도          = 0.70
매핑신뢰도 medium  = 0.65

물품-PP 기본 경로가중치
  = 0.70 * 0.70 * 0.65
  = 0.3185
```

HS 매핑가중치와 HS 대리변수 품질이 각각 1이므로 무역 경로가중치도 0.3185이다.

```text
최대 관측 HS 무역위험 = 0.449517
품목 무역위험         = 0.449517 * 0.3185
                      = 0.143171

기본 공급위험이 0인 경우
Module C 무역 기여도  = 0.25 * 0.143171
                      = 0.035793
```

월 예측사용량 100개, 검토주기 30일, 기존 리드타임 0일인 단순 예에서는 다음과 같다.

```text
기본 목표재고 = 100 * 1.20 = 120.00개
위험 반영 목표재고          = 122.91개
증가량                       = 2.91개, 약 2.43%
```

## 9. 현재 실행 결과 스냅샷

`outputs/stock_module_c_risk_audit.csv`를 집계한 결과다.

| 신호 | 적용 가중치 | 점수 행 | 0보다 큰 기여 행 | 최대 최종 기여 |
|---|---:|---:|---:|---:|
| 질병 수요 뉴스 | 1.00 | 96,474 | 0 | 0 |
| 공급 뉴스 | 0.45 | 96,474 | 0 | 0 |
| 원자재 뉴스 | 0.20 | 96,474 | 0 | 0 |
| 시장가격 | 0.35 | 96,474 | 55,128 | 0.007441 |
| 수출입 overlay | 0.25 | 96,474 | 50,534 | 0.035793 |

| 현재 지표 | 값 |
|---|---:|
| Module C 점수 행 | 96,474 |
| 승인 PP 품목 | 2,297 |
| 최대 HS 무역위험 | 0.449517 |
| 최대 품목 무역위험 | 0.143171 |
| 최대 Module C 공급위험 | 0.035793 |
| 평균 Module C 공급위험 | 0.008766 |
| watch 이상 경보 | 0 |

현재 공급위험 최대값이 watch 기준 0.30보다 낮으므로 경보는 0건이다.

## 10. 반드시 구분해야 하는 별도 정책

### 10.1 레벨 기반 SS·ROP

`data/mapping/supply_risk_level_policy.json`에는 별도로 다음 계수가 있다.

| 기준레벨 | z 값 | 리드타임 배수 |
|---|---:|---:|
| NORMAL | 1.28 | 1.00 |
| CAUTION | 1.65 | 1.10 |
| WARNING | 2.05 | 1.25 |
| CRITICAL | 2.33 | 1.50 |

이 값은 `/inventory-policy` API의 레벨 기반 SS·ROP 계산과 품질 감사에 사용한다.
Module C의 연속형 `0~1` 공급위험 가중합과 같은 계산식이 아니다. 현재 이 정책은
`provisional_team_review_required`이며 Module C 품질 게이트의 배치 반영도
`false`다. 2026-07-24 PDF 명세 대조에서 문서값과 달랐던 CAUTION/WARNING 배수를
`supply-risk-level-v1.1`로 정정했다.

### 10.2 legacy 재고정책

Module C 열이 없는 입력에는 아래 구형 정책이 fallback으로 남아 있다.

```text
external_risk = 0.40*demand + 0.30*supply + 0.30*material

demand_buffer   = 보호기간수요 * demand_risk   * 0.20
supply_buffer   = 보호기간수요 * supply_risk   * 0.20
material_buffer = 보호기간수요 * material_risk * 0.10
전체 버퍼 상한  = 보호기간수요의 50%
```

신규 최종 산출물은 `inventory_policy_method=module_c_periodic_target_stock`인지
확인해야 하며, legacy 계수와 Module C 계수를 혼합해서 설명하면 안 된다.

## 11. 현재 산출물 주의사항

최신 위험 산출물 `outputs/stock_module_c_risk_scores.csv`는 `module-c-v1.1`이며
무역위험을 포함한다.

2026-07-25 재실행 후 `outputs/stock_predictions.csv`는 다음 상태다.

```text
행 수                    = 163,229
module_c_config_version  = module-c-v1.1
무역위험 비영 행         = 871
최대 품목 무역위험       = 0.008619
최대 공급위험            = 0.004575
Module C 정책 적용 행    = 871
```

무역 feature와 Module C v1.1 연결은 완료됐다. 다만 공급위험 품질 게이트 결과는
`PASS=0`, `batch_release_allowed=false`이고 정책 보정 상태도
`policy_seed_requires_backtest`다. 따라서 이 파일은 실험·권고 근거로 사용할 수
있지만 운영 자동발주 승인본은 아니다. 원장 최종일도 2025-12-31이어서 현재 예측
163,229행은 모두 `is_stale_data=true`다. 최신 원장 재실행 전에는 발주에 사용하지
않는다.

## 12. 어디에서 확인하는가

| 확인 목적 | 파일 | 핵심 열 |
|---|---|---|
| 정책 정본 | `data/mapping/module_c_risk_weights.json` | 모든 Module C 계수·임계값 |
| 뉴스 세부 정본 | `data/mapping/news_risk_weights.yaml` | 사건·출처·심각도·관련성 |
| 뉴스 기사별 감사 | `outputs/stock_news_article_scores.csv` | 기사별 모든 곱셈계수 |
| 가격 경로 감사 | `outputs/stock_commodity_risk_audit.csv` | `path_weight`, `risk_contribution` |
| 무역 경로 감사 | `outputs/stock_trade_risk_audit.csv` | HS 위험, 경로가중치, 기여도 |
| 최종 신호 감사 | `outputs/stock_module_c_risk_audit.csv` | `signal_weight`, `weighted_contribution` |
| 최종 위험 | `outputs/stock_module_c_risk_scores.csv` | 수요·공급·총위험 |
| 실행 상태 | `outputs/module_c_run_report.json` | 버전, 승인 수, 품질 게이트 |

## 13. 다음 보정에서 우선 확인할 부분

1. `stock_predictions.csv`를 v1.1 무역위험으로 다시 생성한다.
2. 뉴스 데이터 0행 원인을 해결한 뒤 뉴스 가중치의 실제 분포를 평가한다.
3. Naphtha 가격 시계열을 확보하고 Brent 대리경로와 비교한다.
4. `lag_days`를 실제 월 정렬에 적용하거나 설정에서 제거한다.
5. 뉴스에도 `mapping_confidence`를 적용할지 세 경로의 규칙을 통일한다.
6. PP 외 원자재-HS 경로는 근거 검토 후 한 경로씩 승인한다.
7. 결품률, 긴급발주비, 폐기비용을 목적함수로 시간순 백테스트해 초기 정책값을 보정한다.
