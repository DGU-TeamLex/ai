# TeamLex 보유 대용량 데이터 한정 실험 감사

- 확인일: 2026-08-17
- 범위: 새 내부 원장을 전제로 하지 않고, 이미 받은 `raw_stock/*.DAT`를 실험 모집단으로 고정한다.
- 브랜치 원칙: `feat/*`에서 작업하고 `dev`로만 병합한다. `main`에는 직접 반영하지 않는다.

## 1. 결론

현재 실험의 정본은 2024-01-01~2025-12-31 재고 원천 16,265,602행이다. 이 자료는
월별 3,729,983행, 기관·부서·품목 시계열 416,128개를 만든다. 2018~2019 원천
6,106,936행은 엄격한 표준품목 매칭이 성립하는 경우에만 보조 학습자료로 쓰며,
2020~2023 공백을 보간하거나 과거 행에 현재 위험신호를 소급 부착하지 않는다.

따라서 지금 새 품목을 공개 데이터에서 끌어와 모집단을 늘리면 안 된다. 관세청·MFDS·FDA·WHO·
GDELT·원자재 자료는 `raw_stock`에서 유도된 품목 중 승인된 매핑이 있는 행에만 보조 신호로
붙인다. 매핑되지 않은 원천 품목도 수요예측에는 남기되 외부 신호 상태를 `unmapped` 또는
`not_applicable`로 기록한다. 외부 증거가 0이라는 뜻으로 바꾸지 않는다.

원천에는 정상출고, 입고, 기초·기말재고, 이동, 반품, 폐기, 자동폐기, 조정이 있어 수요예측,
재고소진 프록시, 폐기, 입고량 실험은 가능하다. 반면 발주일·약정일·실제 입고일·부분입고·
유효기간·긴급구매·미충족 수요가 없고 구입단가도 101,022/16,265,602행, 약 0.62%에만 있다.
그러므로 현재 발주식은 절대적 경제 최적해가 아니라 상대순위·시나리오·shadow 정책으로만
해석한다.

## 2. 모집단 계약

```text
U  = distinct(institution, department, item, month) from raw_stock_2024_2025
E* = external_data inner join approved_mapping(U)
training/evaluation = U left join E*
```

- 외부 데이터 전용 품목을 `U`에 union하지 않는다.
- `USE` 코드는 기관 로컬 코드이므로 기관 없이 전역 품목키로 사용하지 않는다.
- 표준 연결축은 `representative_item_id → local item → stock_item_key`다.
- 제품 exact, subtype/family proxy, 원자재 proxy의 증거 수준을 구분한다.
- 충돌 매핑은 하나를 임의 선택하지 않고 quarantine한다.
- 커버리지는 행 비율과 사용량 가중 비율을 함께 보고한다.

## 3. 원천 데이터로 지금 검증 가능한 것

- 월별 양수 정상출고 수요와 signed 원장 감사
- 품목·기관·부서별 수요 패턴, 상대 중요도와 롤링 예측
- 기말재고 0 기반 stockout proxy와 재고 커버리지
- 입고량, 반품, 이동, 폐기·자동폐기의 월별 변화
- 관측 단가가 있는 소수 행의 민감도 분석
- 동일한 실제 초기재고 조건을 둔 정책별 shadow 시뮬레이션

정상출고는 아래 두 순간을 분리한다.

```text
normal_outbound_signed_sum       = 원장 보존·대사값
model_demand_positive_sum        = sum(max(normal_outbound, 0))
negative_normal_outbound_count   = 음수 정상출고 건수
negative_normal_outbound_amount  = 음수 정상출고 절댓값 합
```

음수만 있는 월의 모델 수요는 0이다. signed 합은 재고대사에서만 사용한다.

## 4. 원천 데이터만으로 확정할 수 없는 것

- 실제 발주-입고 리드타임과 부분입고 지연
- 품절 때문에 관측되지 않은 잠재수요와 backorder
- 실제 보관비, 주문비, 폐기비, 긴급구매 프리미엄, 미충족 비용
- 유효기간 단위 폐기 위험
- 제조사·원산지·BOM 수준의 외부 사건 노출
- 2026년 사건의 사후 효과

나라장터 최대 납품기한은 약정 benchmark일 뿐 실제 입고일이 아니다. 30일 baseline에 대한
민감도 분석에는 쓸 수 있지만 자동으로 60일·94일 정책을 승격하지 않는다.

## 5. 외부 데이터 사용 범위

### 관세청 국가·HS 월별 무역

승인된 `raw_stock` 파생 HS·국가 노출만 수집한다. 현재 매핑 범위 32 HS × 20개국 × 96개월은
최대 5,376 요청이며, 640쌍 중 관측 72쌍·96개월 완전 6쌍이다. 이는 모집단 확장 작업이 아니라
승인 매핑의 커버리지 보강이다. 요청별 `success`, 빈 응답, 재시도, 최종 실패 상태를 남긴다.

### 공식 사건과 뉴스

MFDS 1,936건 분석은 대상 사용량 접점이 약 1.62%이고 사건연구가 유의하지 않았다. FDA·WHO는
한국 재고의 정답이 아니라 보조 사건 라벨이다. GDELT는 원문을 무한히 늘리지 말고 현재 재고
품목에 연결되는 사건을 표본 검수한다. 공급 전역 사건은 명시적인 재고 품목 universe에만
broadcast하며 sentinel-only 결과는 성공으로 보지 않는다.

합성 뉴스는 비율과 무관하게 탐지되면 기본적으로 실패한다. GKG 수집 체크포인트는
`success`, `retryable_failure`, `permanent_missing`을 구분하고 수집기간·표본·필터 계약 hash가
바뀌면 예전 체크포인트를 재사용하지 않는다.

### 원자재와 무역 위험

외부 위험은 현재 운영 발주량을 바꾸지 않는다. 24개월 PP 검증에서 관세청 수입가→2개월
기말재고 관계는 p=0.4617로 재현되지 않았다. 3개월 분할의 p=0.007은 표본이 너무 짧고,
탐색 90건은 Bonferroni 보정 후 유의한 경로가 없다. 외부 위험 조정값은 shadow 열로만
산출하고, 사전지정한 12개월 이상의 미사용 평가기간과 block bootstrap을 통과한 뒤 승격한다.

## 6. 현재 데이터 안에서의 실행 우선순위

1. `raw_stock` 모집단과 승인 매핑 계약을 고정한다.
2. signed 출고와 양수 모델 수요를 분리하고 원장 대사 보고서를 재현 가능하게 만든다.
3. `representative_item_id` 축에서 상대 사용량 등급을 계산하고 VED는 사람 검토로 분리한다.
4. 재고정책은 동일한 실제 초기재고·주문상태에서 WAPE, BIAS, fill rate, stockout, 평균재고를
   함께 비교한다. 2025-10~12는 이미 본 구간이므로 최종 clean test라고 부르지 않는다.
5. 외부 뉴스·원자재·무역 신호는 신호별 ablation과 shadow 결과만 기록한다.
6. 관세청 수집은 승인된 raw-stock 매핑 커버리지의 결손만 보완한다.
7. 질병·VED·제품 exact 매핑은 사람 승인 전 자동 발주에 사용하지 않는다.

## 7. 추가 데이터가 생겼을 때만 가능한 승격

새 데이터 수집은 현재 실험의 선행조건이 아니다. 나중에 발주일, 실제 입고일, 부분입고,
backorder, 긴급구매 단가, 유효기간·폐기비가 확보되면 비용 계수를 실측해 경제적 최적화를
재추정할 수 있다. 그 전에는 비용비 1:9와 리드타임 30일을 실험 prior로 명시하고 민감도
범위만 제시한다.

## 8. 공식 보조 출처

- 관세청 국가·HS별 무역: https://www.data.go.kr/data/15100475/openapi.do
- 나라장터 납품요구: https://www.data.go.kr/data/15129471/openapi.do
- MFDS 공급중단: https://www.data.go.kr/data/15057899/openapi.do
- openFDA 의약품 부족: https://open.fda.gov/apis/drug/drugshortages/how-to-use-the-endpoint/
- FDA 의료기기 부족: https://www.fda.gov/medical-devices/medical-device-supply-chain-and-shortages/medical-device-shortages-list
- WHO Disease Outbreak News: https://www.who.int/emergencies/disease-outbreak-news
- World Bank Pink Sheet: https://thedocs.worldbank.org/en/doc/18675f1d1639c7a34d463f59263ba0a2-0050012025/worldbank-commodities-price-data-the-pink-sheet

이 출처들은 `raw_stock` 모집단을 늘리는 자료가 아니라 승인된 품목의 외부 설명변수와 사건
라벨을 보강하는 자료다.
