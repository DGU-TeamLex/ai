# 중간점검 3. 최종 재고량 출력 시스템

작성일: 2026-07-22
발표일: 2026-07-23

## 1. 발표용 한 문장

단순 사용량 기반 기본재고를 기준선으로 두고, 승인된 수요위험은 사용량에, 승인된
공급위험은 리드타임과 안전재고에 반영한 뒤 상한을 적용해 최종 목표재고와 발주량을 낸다.

## 2. 전체 결합 흐름

```text
raw_stock 월별 사용량
  -> 사용량 예측 모델
  -> predicted_usage
  -> 단순 기본재고 base_stock

뉴스/가격 + 승인 매핑
  -> demand_risk / supply_risk
  -> Module C 위험조정

base_stock + risk_buffer
  -> target_stock
  -> inventory_position 차감
  -> recommended_order
```

외부 API는 요청 시점에 호출하지 않는다. 뉴스와 가격은 배치에서 수집·점수화하고, 최종
API는 배치 산출물을 읽는다. 외부 장애가 발주 요청까지 전파되지 않도록 하기 위한 구조다.

## 3. 최종 계산식

기본재고는 단순 모델과 동일하다.

```text
base_stock
  = predicted_usage * (review_days + lead_time_days) / 30 * 1.20
```

수요위험은 아직 사용량 예측에 포함되지 않은 경우에만 적용한다.

```text
risk_adjusted_usage
  = predicted_usage * (1 + demand_risk * 0.35)
```

공급위험은 리드타임과 안전재고율을 조정한다.

```text
effective_lead_time_days
  = lead_time_days * (1 + supply_risk * 0.50)
  + supply_risk * 14

dynamic_safety_stock_rate
  = 0.20 + supply_risk * 0.25
```

위험조정 후 제한 전 목표재고를 계산한다.

```text
risk_adjusted_period_demand
  = risk_adjusted_usage
  * (review_days + effective_lead_time_days) / 30

unconstrained_target_stock
  = risk_adjusted_period_demand
  * (1 + dynamic_safety_stock_rate)
```

정책 초기값이 과도한 재고를 만들지 않도록 위험버퍼에 상한을 둔다.

```text
raw_risk_buffer
  = max(unconstrained_target_stock - base_stock, 0)

risk_buffer_cap
  = protection_period_demand * 0.75

risk_buffer
  = min(raw_risk_buffer, risk_buffer_cap)

target_stock
  = base_stock + risk_buffer

recommended_order
  = max(target_stock - inventory_position, 0)
```

## 4. 중복 반영 방지

향후 뉴스 수요신호를 포함한 예측모델이 선택되면
`external_demand_signal_in_forecast=true`가 된다. 이 경우 Module C 정책층에서는 같은
수요위험을 다시 사용량에 곱하지 않는다.

또한 레벨 기반 SS/ROP 방식과 현재 연속형 Module C 목표재고를 동시에 더하지 않는다.
둘 다 안전재고를 상향하기 때문에 함께 적용하면 과적재가 된다.

## 5. 발표용 최종 출력

출력 파일:

`outputs/midterm_2026-07-23/03_final_inventory_output.csv`

12개 로컬 품목, 7개 품목군에 네 가지 상태를 적용해 총 48행을 만들었다.

| 상태 | 평균 목표재고 증가율 | 해석 |
|---|---:|---|
| 현재 승인관계 없음 | 0.00% | 기본재고 유지 |
| 승인 수요 급증 시나리오 | 36.76% | 예상 사용량 상향 |
| 승인 공급차질 시나리오 | 53.58% | 리드타임·안전재고 상향 |
| 승인 복합충격 시나리오 | 62.50% | 버퍼 상한 범위 안에서 동시 반영 |

증가율은 선택한 품목 모두 같은 정책계수를 사용하기 때문에 거의 동일하게 나타난다.
실제 운영에서는 품목별 매핑 가중치, 노출도, 리드타임이 들어가 서로 달라져야 한다.

## 6. 한 품목의 단계별 예시

`케어센스N혈당측정스트립`의 발표용 예시는 다음과 같다.

| 상태 | 수요위험 | 공급위험 | 기본재고 | 최종 목표재고 | 발주 권고 |
|---|---:|---:|---:|---:|---:|
| 현재 승인관계 없음 | 0.000 | 0.000 | 104.593 | 104.592 | 104.592 |
| 승인 수요 급증 | 0.800 | 0.100 | 104.593 | 143.045 | 143.045 |
| 승인 공급차질 | 0.100 | 0.655 | 104.593 | 160.629 | 160.629 |
| 승인 복합충격 | 0.700 | 0.880 | 104.593 | 169.962 | 169.962 |

현재고가 0인 예시이므로 목표재고와 발주 권고가 같다. 기본재고의 0.001 차이는 발표용
CSV의 소수 셋째 자리 반올림 뒤 재계산한 결과이며 `target_stock_delta`는 허용오차 안에서
0으로 처리했다.

## 7. 출력에서 볼 핵심 컬럼

| 컬럼 | 의미 |
|---|---|
| `predicted_usage` | 단순 모델의 다음 달 예상 사용량 |
| `simple_base_stock` | 위험 반영 전 기본재고 |
| `module_c_demand_risk` | 승인 게이트를 지난 수요위험 |
| `module_c_supply_risk` | 승인 게이트를 지난 공급위험 |
| `risk_adjusted_predicted_usage` | 수요위험 반영 사용량 |
| `effective_lead_time_days` | 공급위험 반영 리드타임 |
| `dynamic_safety_stock_rate` | 공급위험 반영 안전재고율 |
| `risk_buffer` | 상한 적용 후 추가 재고 |
| `target_stock` | 최종 목표재고 |
| `inventory_position` | 현재고 + 입고예정 - 미납 |
| `recommended_order` | 최종 발주 권고량 |
| `release_status` | 운영 반영 가능 여부 |

## 8. 현재 운영 결과와 시나리오의 구분

현재 실제 상태는 다음과 같다.

- 승인된 품목-원자재 관계 0건
- Module C 위험 점수 0행
- 품질 게이트 PASS 0행
- 최신 예측 입력 7개월 경과

따라서 현재 상태에서는 위험버퍼가 0이고 최종 목표재고는 단순 기본재고와 같다. 나머지
세 상태는 승인 신호가 입력될 때 동일 코드가 어떻게 계산되는지 보여주는 합성 시나리오다.

CSV의 모든 행은 `operational_use_allowed=false`다.

- 현재 상태: `BLOCKED_STALE_INPUT_AND_MAPPING_GATE`
- 합성 상태: `DEMO_ONLY_SYNTHETIC_SIGNAL`

## 9. 운영 연결 전 필요한 입력

1. 품목별 승인 단위와 포장 환산
2. 실제 조달 리드타임
3. 현재고, 입고예정량, 미납량
4. 승인된 품목-원자재 관계
5. 승인된 질병·사건-품목 관계
6. 실제 뉴스와 가격 시계열
7. PASS 품질상태와 동일 배치 ID

이 조건이 충족되기 전에는 최종 수치를 자동 발주로 보내지 않고 설명·검토용으로만 쓴다.

## 10. 재생성 명령

```bash
conda run -n teamlex python -m src.presentation.midterm_package
```
