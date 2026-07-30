# 2026년 1~8월 재귀 사용량·재고 시뮬레이션

- 작성일: 2026-07-30
- 기준 원천월: 2025-12
- 예측 기간: 2026-01~2026-08
- 대상: `stock_item_key` 163,229개
- 산출 행: 1,305,832개
- 상태: 가상 시나리오이며 실제 재고 관측값이 아님

## 1. 목적

2026년 1~8월 실제 재고·사용량 원천이 없는 상태에서 현재 사용량 모델과 재고정책을
그대로 연결해 2026년 8월 말 재고 시나리오를 만든다. 한 번 계산한 1월 값을 8개월
복제하지 않고, 각 월의 가상 출고량을 다음 달 lag·rolling feature에 다시 넣는 재귀
예측이다.

## 2. 사용한 현재 시스템

사용량은 현재 적용된 `forecast-ensemble-temporal-v1.0`의 패턴별 라우터로 예측한다.
전역 혼합 기준은 LightGBM L1 0.25, LightGBM Tweedie 0.15, Module C Tweedie 0.60이며,
실제 행에는 `smooth`, `intermittent`, `erratic`, `lumpy` 등 수요패턴별 가중치가
우선 적용된다.

재고량은 `add_inventory_recommendations()`의 현재 정책을 사용한다.

```text
target_stock
  = base_stock + capped_module_c_risk_buffer

recommended_order
  = max(target_stock - inventory_position, 0)
```

Module C 수요위험이 이미 사용량 모델에 포함된 경우 정책층에서 같은 수요위험을 다시
곱하지 않는 기존 중복 방지도 유지한다.

## 3. 가상 사용량 생성

품목키·예측월·seed `42`를 해시한 재현 가능한 대칭 삼각분포 계수를 사용한다.

```text
virtual_usage_factor ~ Triangular(0.90, 1.00, 1.10)

virtual_requested_usage_qty
  = predicted_usage * virtual_usage_factor
```

따라서 모든 가상 수요는 해당 월 모델 예측값의 `-10%~+10%` 안에 있다. 이 범위는
통계적 신뢰구간이 아니라 사용자가 지정한 가상 변동 시나리오다.

## 4. 월별 재고 흐름

발주가 억제되지 않은 `recommended_order`가 같은 달 안에 전량 입고된다고 가정했다.

```text
available_stock_qty
  = opening_stock + simulated_inbound_qty

simulated_consumption_qty
  = min(virtual_requested_usage_qty, available_stock_qty)

unmet_demand_qty
  = virtual_requested_usage_qty - simulated_consumption_qty

predicted_month_end_stock
  = opening_stock
  + simulated_inbound_qty
  - simulated_consumption_qty
```

월말 재고는 0 미만으로 내려가지 않는다. 실제 출고 가능한
`simulated_consumption_qty`를 다음 달 수요 이력에 넣고 lag 1·2·3·6·12,
rolling mean/std 3·6·12, rolling median, expanding mean, zero rate와 계절 feature를
다시 계산한다.

## 5. 미래 데이터 가정

| 입력 | 적용 |
|---|---|
| Module C 위험 | 2025-12 origin의 마지막 관측 신호 유지 |
| 재고상태·발주억제 | 2025-12 origin의 마지막 판정 유지 |
| 폐기·자동폐기 | 0 |
| 미납·백오더 | 0 |
| 발주 입고 | 권고량이 같은 월 안에 전량 도착 |

미래 뉴스·가격·수출입·리드타임 실측이 들어오면 이 고정 가정을 해당 월 값으로 교체해야
한다.

## 6. 월별 결과

`원시 월말재고`는 모든 원천값을 보존한 합계다. `집계 적격 월말재고`는 기존 평가에서
검토 대상으로 지정한 2025-12 재고 100,000 이상 21개 품목만 집계에서 제외한 값이다.
개별 품목 행은 삭제하거나 수정하지 않았다.

| 월 | 예측사용량 | 가상수요 | 원시 월말재고 | 집계 적격 월말재고 | 품절 품목률 |
|---|---:|---:|---:|---:|---:|
| 2026-01 | 18,606,661 | 18,608,336 | 345,970,178 | 125,256,771 | 3.32% |
| 2026-02 | 17,202,903 | 17,195,091 | 330,679,470 | 110,117,324 | 4.18% |
| 2026-03 | 16,333,410 | 16,339,823 | 317,025,811 | 96,623,256 | 5.33% |
| 2026-04 | 15,229,751 | 15,233,443 | 305,188,963 | 84,912,182 | 6.37% |
| 2026-05 | 14,141,384 | 14,141,760 | 295,205,402 | 75,033,708 | 7.19% |
| 2026-06 | 12,380,604 | 12,369,238 | 287,027,356 | 66,954,198 | 7.61% |
| 2026-07 | 11,838,112 | 11,834,050 | 280,085,015 | 60,113,858 | 9.17% |
| 2026-08 | 10,976,055 | 10,969,653 | 274,209,471 | 54,295,186 | 9.97% |

## 7. 2026년 8월 해석

| 항목 | 값 |
|---|---:|
| 품목 수 | 163,229 |
| 예측사용량 | 10,976,054.68 |
| 가상 요청 사용량 | 10,969,652.93 |
| 실제 출고 가능량 | 10,951,406.31 |
| 미충족 수요 | 18,246.61 |
| 시뮬레이션 입고량 | 5,075,861.67 |
| 원시 월말재고 | 274,209,470.74 |
| 집계 적격 월말재고 | 54,295,186.07 |
| 품절 품목 | 16,269, 9.97% |
| 수량 기준 충족률 | 99.83% |

가상 사용량 계수의 실제 최솟값·평균·최댓값은 각각
`0.900141`, `0.999906`, `1.099916`이다.

품절 16,269건은 전부 기존 정책에서 발주가 억제된 행에서 발생했다.

| 발주억제 사유 | 전체 | 8월 품절 |
|---|---:|---:|
| `DORMANT` | 41,742 | 10,867 |
| `NOT_OPERATED` | 4,864 | 4,822 |
| `DATA_MISSING` | 580 | 580 |

이는 억제 정책을 해제해야 한다는 의미가 아니다. 2026년 실제 재고상태가 없어서
2025-12 판정을 8월까지 유지한 결과이므로 해당 16,269건을 우선 재확인해야 한다.

## 8. 재고 이상치

원천 재고 100,000 이상 21개 품목이 8월 원시 월말재고의 약 80.20%인
219,914,284.67을 차지한다. 특히 약 1억 단위 재고 두 건은 누적값·단위·sentinel
가능성이 있다.

- `source_stock_outlier_flag=true`: 품목 결과는 보존하되 합계 검토 대상
- `stock_aggregate_eligible=false`: 집계 적격 합계에서만 제외
- 운영 DB의 실제 단위와 수량을 확인하기 전에는 원시 총합을 재고 규모로 사용하지 않음

## 9. 검증 결과

| 검사 | 결과 |
|---|---:|
| 원천 이력과 lag 불일치 | 0 |
| expanding mean 불일치 | 0 |
| 기존 1월 예측사용량 불일치 | 0 |
| 기존 1월 목표재고 불일치 | 0 |
| 기존 1월 권고발주 불일치 | 0 |
| ±10% 범위 초과 | 0 |
| 음수 입고·출고·미충족·월말재고 | 0 |
| 월별 장부식 최대 오차 | 0 |

## 10. 산출물

| 파일 | 설명 |
|---|---|
| `outputs/inventory_simulation_2026_01_2026_08/*.parquet` | 1~8월 전체 상세, 월별 163,229행 |
| `outputs/inventory_forecast_2026_08.csv` | 8월 전체 품목 CSV |
| `outputs/inventory_forecast_2026_08_by_item_group.csv` | 8월 품목군별 원시·집계 적격 결과 |
| `outputs/inventory_simulation_2026_01_2026_08_summary.csv` | 월별 합계와 품절률 |
| `outputs/inventory_simulation_2026_01_2026_08_report.json` | 산식·검증·가정·경로 |
| `data/sample/inventory_simulation_2026_01_2026_08_sample_1000.csv` | 확인용 1,000행 |

핵심 열은 다음과 같다.

| 열 | 의미 |
|---|---|
| `predicted_usage` | 현재 혼합 모델의 해당 월 사용량 예측 |
| `virtual_usage_factor` | 0.90~1.10 가상 변동계수 |
| `virtual_requested_usage_qty` | 재고 제약 전 가상 수요 |
| `simulated_consumption_qty` | 재고에서 실제 출고 가능한 가상 사용량 |
| `simulated_inbound_qty` | 같은 달 도착을 가정한 권고발주량 |
| `unmet_demand_qty` | 가상 수요 중 공급하지 못한 양 |
| `target_stock` | 현재 정책의 위험조정 목표재고 |
| `predicted_month_end_stock` | 월말 예측 재고 |
| `source_stock_outlier_flag` | 원천 재고 100,000 이상 검토 대상 |
| `stock_aggregate_eligible` | 이상치 제외 집계 포함 여부 |

## 11. 재실행

```bash
conda run -n teamlex python -m src.modeling.recursive_inventory_simulation \
  --start-month 2026-01 \
  --end-month 2026-08 \
  --usage-deviation 0.10 \
  --random-seed 42 \
  --sample-size 1000
```

같은 입력·모델·seed에서는 같은 가상 사용량이 생성된다.

## 12. 사용 제한

이 결과는 실제 2026년 재고가 아니라 현재 정책을 전제로 한 합성 시나리오다.
`recommended_order`의 효과, 실제 입고시점, 서비스 수준과 폐기비용은 검증되지 않았다.
따라서 자동 발주 입력으로 사용하지 말고, 2026년 월별 재고·입고·출고·폐기 실측을
확보한 뒤 순서대로 대체하고 백테스트해야 한다.
