# 재고량 정책 모델

## 1. 출력의 의미

사용량 예측 모델과 재고 정책 모델은 역할을 분리한다.

| 출력 | 의미 |
|---|---|
| `predicted_usage` | 다음 한 달의 예상 사용량 |
| `base_stock` | 검토주기와 조달리드타임 수요에 안전재고를 더한 기본 재고량 |
| `risk_buffer` | 승인 매핑 기반 수요·공급·원자재 위험 추가분 |
| `target_stock` | `base_stock + risk_buffer`인 최종 목표 재고량 |
| `recommended_stock` | 기존 연동 호환용 `target_stock` 별칭 |
| `recommended_order` | 재고위치를 고려한 발주 권고량 |

따라서 `predicted_usage` 자체는 기본 재고량이 아니다. 모델이 수요를 예측하고,
`src/modeling/inventory_policy.py`가 그 예측을 재고량으로 변환한다.

## 2. 기본 계산식

월 예상 사용량을 `D`, 검토주기를 `R`, 조달리드타임을 `L`이라고 한다.
모든 수량은 해당 로컬 품목 또는 세부유형 행의 `unit_code` 단위다.

```text
protection_period_days   = R + L
protection_period_demand = D * (R + L) / 30
safety_stock             = protection_period_demand * 0.20
base_stock               = protection_period_demand + safety_stock
```

아래 식은 모듈 C feature가 없는 기존 산출물과의 호환 정책이다. 위험 점수는 0부터 1까지로
제한한다.

```text
demand_risk_score   = disease_news_risk
supply_risk_score   = supply_news_risk
material_risk_score = max(material_news_risk, commodity_risk)

demand_risk_buffer   = protection_period_demand * demand_risk_score * 0.20
supply_risk_buffer   = protection_period_demand * supply_risk_score * 0.20
material_risk_buffer = protection_period_demand * material_risk_score * 0.10
```

세 버퍼의 합은 `protection_period_demand`의 50%를 넘지 않는다.

```text
risk_buffer       = capped sum of three risk buffers
target_stock      = base_stock + risk_buffer
inventory_position = current_stock + on_order_qty - backorder_qty
recommended_order = max(target_stock - inventory_position, 0)
```

리드타임 수요는 `target_stock`에 한 번만 들어간다. 발주량 계산 단계에서 다시 더하지 않는다.

## 3. 모듈 C 동적 계산식

모듈 C가 생성한 승인 위험 점수가 있으면 고정 버퍼 대신 수요, 리드타임, 안전재고율을
각각 조정한다. 수요 위험을 `rD`, 공급 위험을 `rS`라 한다.

```text
risk_adjusted_predicted_usage = D * (1 + 0.35 * rD)
effective_lead_time_days      = L * (1 + 0.50 * rS) + 14 * rS
dynamic_safety_stock_rate     = 0.20 + 0.25 * rS

unconstrained_target
= risk_adjusted_predicted_usage
* (R + effective_lead_time_days) / 30
* (1 + dynamic_safety_stock_rate)

risk_buffer = min(
  max(unconstrained_target - base_stock, 0),
  protection_period_demand * 0.75
)
target_stock = base_stock + risk_buffer
```

수요 뉴스 또는 모듈 C feature를 이미 사용한 학습 모델이 선택된 경우
`external_demand_signal_in_forecast=true`로 표시하고 35% 고정 수요 상향은 다시 적용하지
않는다. 공급 위험은 사용량을 늘리는 대신 리드타임과 안전재고율에만 반영한다. 상세한
신호 결합식과 감사 산출물은 `docs/MODULE_C_RISK_ADJUSTMENT.md`에 정리되어 있다.

## 4. 매핑 적용 경계

다음 파일에서 `review_status=approved`인 행만 운영 매핑으로 사용한다.

```text
data/mapping/stock_item_material_mapping.csv
```

`needs_review`, `candidate` 행과
`data/processed/item_material_pipeline/item_material_event_mapping_full.csv`의 자동 생성 후보는
재고량 계산에 사용하지 않는다. 승인 매핑이 없는 품목은 뉴스·원자재 위험 점수가 0이며
`target_stock == base_stock`이다.

예측 CSV에는 감사용으로 다음 값이 함께 저장된다.

| 컬럼 | 의미 |
|---|---|
| `has_approved_material_mapping` | 승인 원자재 매핑 존재 여부 |
| `approved_material_mapping_count` | 승인된 품목-원자재 관계 수 |
| `approved_related_materials` | 승인 원자재 목록 |
| `approved_raw_material_meta_codes` | 승인 원자재 메타코드 |
| `approved_raw_material_risk_meta_codes` | 승인 원자재 위험 메타코드 |
| `approved_demand_risk_meta_codes` | 승인 수요 위험 메타코드 |
| `approved_material_mapping_versions` | 적용한 매핑 버전 |

2026-07-18 현재 운영 승인 행은 0건이다. 따라서 현재 선택된 사용량 전용 모델의
`risk_buffer`는 0이고, 승인 전 후보가 재고량에 영향을 주지 않는다.

## 5. 배치와 API

배치는 검토주기 30일, 리드타임 0일, 입고예정 0, 미납수량 0을 기본값으로 사용한다.
실제 발주 시점에는 API에 품목별 조달 정보를 전달해 다시 계산한다.

```json
{
  "yyyymm": "2026-07",
  "item_code": "ITEM1",
  "institution_code": "INST001",
  "department": "진료실",
  "current_stock": 40,
  "lead_time_days": 15,
  "review_period_days": 30,
  "on_order_qty": 20,
  "backorder_qty": 10
}
```

`POST /recommend-order` 응답에는 기본 재고량, 위험조정 사용량, 동적 리드타임·안전재고율,
목표 재고량, 재고위치, 발주 권고량이 각각 포함된다. 수요 신호의 중복 방지 여부도
`module_c_demand_embedded_in_forecast`로 확인할 수 있다.

## 6. 운영 전 보정 항목

현재 안전재고율 20%와 위험 버퍼 상한은 명시적 초기 정책값이다. 승인 매핑과 실제
리드타임 이력이 축적되면 다음 순서로 재평가한다.

1. 품목군별 예측오차 분포로 서비스 수준별 안전재고를 계산한다.
2. 공급지연 이력으로 `lead_time_days`와 공급 위험 계수를 보정한다.
3. 위험 발생 전후의 실제 사용량으로 세 위험 버퍼 계수를 백테스트한다.
4. 단위 환산계수가 승인되기 전에는 서로 다른 `unit_code` 수량을 합산하지 않는다.

## 7. 공급위험 레벨 기반 SS/ROP 계약

backend의 레벨 기반 정책을 사용할 때는 `data/mapping/supply_risk_level_policy.json`을
정본으로 사용한다. 저장된 과거 레벨을 재사용하지 않고 현재 공급 메타코드에서
`baseline_supply_risk_level`을 다시 계산한다.

```text
SS  = z(level) * daily_demand_stddev * sqrt(effective_lead_time_days)
ROP = mean_daily_usage * effective_lead_time_days + SS
effective_lead_time_days = lead_time_days * lead_time_multiplier(level)
```

월 사용량이나 임의 생성 표준편차를 이 식에 넣지 않는다. 세 일 단위 입력이 없으면 계산
불가 상태를 반환한다. 이 정책과 모듈 C 연속형 `target_stock` 정책을 동시에 적용해 두
안전재고를 합산하는 것도 금지한다. 공급레벨 오정합 분석과 정책 결정은
`docs/report_공급위험레벨_안전재고_과적재.md` §9를 참고한다. 적재 전 오류 분류와 격리
절차는 `docs/SUPPLY_RISK_QUALITY_GATE.md`를 따른다.
