# HSK 기반 수출입 위험 연결

- 작성일: 2026-07-23 KST
- HSK 정본: `관세청_HS부호_20260101.xlsx`
- HSK 버전: `hsk-2026-01-01`
- 모듈 C 버전: `module-c-v1.1`

## 1. 적용 결과

제공된 관세청 HSK 엑셀 12,469행을 구조화해 10자리 통계용 HSK 11,327행과
7~9자리 계층행 1,142행을 분리했다. HS부호는 숫자로 변환하지 않고 문자열로 유지해
앞자리 `0` 손실을 차단한다.

현재 운영 승인을 받은 재고-원자재 경로는 `POLYPROPYLENE_PP` 2,297개 로컬 품목이다.
공식 HSK의 정확한 명칭 일치를 근거로 다음 한 경로만 승인했다.

| 원자재 코드 | HSK | 공식 품목명 | 상태 |
|---|---|---|---|
| `POLYPROPYLENE_PP` | `3902100000` | 폴리프로필렌 | approved |

`3902300000`은 프로필렌 공중합체이므로 PP와 동일 물질로 자동 승인하지 않는다.
`9018310000`은 주사기 완제품 코드이므로 원자재 위험 경로에 섞지 않는다.

## 2. 데이터 흐름

```text
raw_stock
  -> 승인된 재고-원자재 매핑
  -> 승인된 원자재-HSK 매핑
  -> 관세청 HS별 월 수출입 총계
  -> 관세청 HS·국가별 월 수출입
  -> HS 수출입 위험
  -> 품목별 수출입 위험
  -> 모듈 C 공급위험
  -> 위험조정 목표재고
```

다음 두 조건이 모두 충족된 행만 재고에 전파한다.

1. `stock_item_material_mapping.csv`의 품목-원자재 경로가 `approved`
2. `material_hs_mapping.csv`의 원자재-HSK 경로가 `approved`

이름 유사 후보나 10자리 미만 계층 코드는 운영 점수에 사용하지 않는다.

## 3. 수출입 위험식

HS별 월 위험은 다음 정책 초안으로 계산한다.

```text
trade_risk =
    0.35 * 전년동월 대비 수입중량 감소 위험
  + 0.25 * 전년동월 대비 수입단가 상승 위험
  + 0.25 * 수입국 집중 위험
  + 0.15 * 순수입 노출 위험
```

- 수입중량이 전년동월보다 30% 이상 감소하면 해당 성분 위험은 1에 도달한다.
- 수입단가가 전년동월보다 40% 이상 상승하면 해당 성분 위험은 1에 도달한다.
- 국가별 수입액이 전체 수입액의 80% 이상 포착될 때만 집중도를 사용한다.
- 순수입 노출은 `(수입액-수출액)/수입액`의 0~1 범위 대리값이다.

관세청 통계만으로 국내 생산량을 알 수 없으므로 마지막 값은 엄밀한 `수입의존도`가
아니다. 이 시스템은 `net_import_exposure`로 명시하며 국내 생산·소비 자료가 확보되기
전에는 수입의존도라고 보고하지 않는다.

## 4. 모듈 C 반영

기존 뉴스·원자재 가격 공급위험을 유지하고 수출입 위험을 독립 충격으로 최대 25%만
반영한다.

```text
base_supply =
    0.45 * supply_news
  + 0.20 * material_news
  + 0.35 * market_price

final_supply =
    1 - (1 - base_supply) * (1 - 0.25 * trade_risk)
```

이 방식은 수출입 데이터가 없을 때 기존 결과를 바꾸지 않는다. 계수는 인과 추정값이
아니라 `policy_seed_requires_backtest` 상태이며 2024~2025 재고 이력으로 비교평가해야
한다.

## 5. 실제 API 적용 결과

2026-07-23 활용승인 후 다음 두 관세청 API의 정상 응답을 확인했다.

- <https://www.data.go.kr/data/15101609/openapi.do>
- <https://www.data.go.kr/data/15100475/openapi.do>

총계 API는 HS 필드를 `hsCode`, 국가별 API는 `hsCd`로 반환하므로 두 필드를 모두
허용하도록 파서를 수정했다. `year=총계`, `hsCode=-`인 보조행은 월 형식 검증으로
제외한다.

| 항목 | 결과 |
|---|---:|
| PP 총계 관측 | 42개월 |
| PP 국가별 관측 | 719행 |
| 추적 국가 | 20개 |
| 국가수입액 커버리지 80% 이상 | 38/42개월 |
| 수출입 점수 대상 품목 | 2,297개 |
| 품목-월 점수 | 96,474행 |
| 최대 HS 수출입 위험 | 0.4495 |
| 품목 매핑 가중 후 최대 위험 | 0.1432 |
| 모듈 C 최대 수출입 기여 | 0.0358 |

2025년 1월 표본에서 추적 국가 수입액은 전체 PP 수입액의 84.94%였다. 전체 기간에는
`data/mapping/trade_country_scope.csv`의 20개국을 사용하며, 커버리지가 80% 미만인
4개월은 국가집중도 성분을 자동으로 0 처리했다.

최초 수집:

```bash
set -a
source .env
set +a

export TRADE_PROVIDER=kcs
export TRADE_START_MONTH=2023-01
export TRADE_END_MONTH=2026-06
export TRADE_COUNTRY_CODES=
export TRADE_MAX_REQUESTS=1000

conda run -n teamlex python -m src.trade.trade_risk_scorer --provider kcs
conda run -n teamlex python -m src.module_c.pipeline
```

이후에는 저장된 공식 응답 캐시로 재계산할 수 있다.

```bash
conda run -n teamlex python -m src.trade.trade_risk_scorer --provider csv
conda run -n teamlex python -m src.module_c.pipeline
```

예상 호출 수가 `TRADE_MAX_REQUESTS`를 넘으면 수집을 시작하기 전에 중단한다. 현재
계수는 정책 초안이므로 다음 단계는 2024~2025 재고 이력에 대한 백테스트다.

## 6. 산출물

- `data/processed/trade/hsk_reference_2026.parquet`
- `data/mapping/material_hs_mapping.csv`
- `data/mapping/trade_country_scope.csv`
- `outputs/hsk_reference_2026_report.json`
- `outputs/stock_trade_risk_scores.csv`
- `outputs/stock_trade_risk_audit.csv`
- `outputs/stock_trade_risk_report.json`
- `outputs/stock_module_c_risk_scores.csv`
