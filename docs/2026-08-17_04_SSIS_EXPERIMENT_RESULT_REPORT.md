# 한국사회보장정보원 제공 데이터 기반 의료재고 예측 실험 결과 보고서

- 작성일: 2026-08-17
- 대상: 한국사회보장정보원 및 데이터 제공·검토 담당자
- 저장소 기준: `DGU-TeamLex/ai` dev, commit `1a5c3f4e9d53439f28b44c6e3543536bf8e1ddcf`
- 분석 성격: 연구·실험용. 운영 자동발주 승인 자료가 아님
- 데이터 시차: 최신 원천 2025-12. 운영시점 대비 8개월 시차는 실험 설계상 허용된 조건

## 1. 제출 목적

본 보고서는 정보원 제공 재고 원천으로 무엇을 학습·검증했는지, 실제로 어떤 결과가 나왔는지,
어떤 수치를 운영 근거로 사용할 수 없는지, 재현과 후속 검토를 위해 정보원에 무엇을 함께
제공해야 하는지를 설명한다.

가장 중요한 결론은 다음과 같다.

- 현재 데이터만으로 수요예측, 재고상태 분류, 상대적 재고정책 비교 실험은 가능하다.
- 튜닝 모델의 기존 전체 실행 TEST WAPE는 35.68%로 baseline 38.12%보다 2.44%p 낮다.
- 별도 재고 시뮬레이션에서 모델 정책은 naive 대비 fill rate를 0.81%p 높이면서 평균재고를
  13.17% 낮췄다. 다만 이 평가는 이미 검사한 기간과 평가구간 sigma를 사용해 최종 clean
  test가 아니다.
- 뉴스·원자재·무역 신호는 수요예측 개선폭과 재고 선행성 근거가 작거나 재현되지 않았다.
  최신 정책에서는 실제 발주를 변경하지 않고 shadow 결과만 산출한다.
- 실제 주문일·입고일·미충족 수요·비용 자료가 없으므로 현재 발주식은 경제적 전역 최적해가
  아니라 상대순위·시나리오 정책이다.

## 2. 데이터 수령 및 사용 범위

### 2.1 정본 원천

- 2024-01~2025-12 물품재고 원천: 10개 DAT, 일별 16,265,602행
- 월별 집계: 3,729,983행
- 기관: 3,530개 익명 코드
- 부서: 109개
- 물품코드: 17,155개
- 기관·부서·물품 시계열: 416,128개
- 현재기간 원장대사 위반: 8행

원천에 있는 정상출고, 입고, 기초·기말재고, 이동, 반품, 폐기, 자동폐기, 조정 필드를 이용한다.
구입단가는 101,022/16,265,602행, 약 0.62%만 관측되어 금액 ABC나 실제 비용 최적화의 전수
근거로 사용하지 않는다.

### 2.2 보조 과거자료

- 2018-01~2019-12 물품재고 원천: 3개 DAT, 일별 6,106,936행
- 월별 집계: 1,460,785행
- 기관·부서·물품 시계열: 179,295개
- 과거기간 원장대사 위반: 39행
- 과거 로컬 품목 175,760개 중 엄격한 현재 대표품목 연결로 학습 가능한 품목: 146,757개,
  83.50%

2020~2023 공백을 보간하지 않는다. 2019와 2024 사이에는 lag segment를 새로 시작하며,
현재 대표품목으로 엄격하게 연결되지 않은 과거 29,003개 품목은 모델 학습에서 제외한다.

### 2.3 모집단 계약

```text
U  = raw_stock 2024~2025에서 관측된 기관·부서·품목·월
E* = U에서 유도되고 사람이 승인한 매핑에 연결되는 외부자료
model panel = U LEFT JOIN E*
```

공개 외부자료에만 존재하는 품목을 모집단에 추가하지 않는다. 매핑되지 않은 원천 품목은
수요예측에는 유지하되 외부신호 상태를 `unmapped`로 둔다.

## 3. 데이터 품질 결과

- 전체 모델 패널의 수요 0행 비율: 39.65%
- 6개월 이상 관측 시계열: 276,882개
- 12개월 이상 관측 시계열: 174,132개
- 기관×품목 pooling 후보의 12개월 이상 비율: 32.71%
- 기관×family 후보의 12개월 이상 비율: 56.50%
- 품목 단독 후보의 12개월 이상 비율: 70.63%
- 월별 이름이 둘 이상인 시계열: 51개
- 물리적 재고대사 위반: 현재 8행, 과거 39행

시계열 중앙 관측월은 6개월이다. 따라서 모든 품목에 개별 표준편차를 적용하는 것보다,
대표품목·family 수준의 부분 pooling 후보를 holdout에서 비교하는 것이 타당하다. 다만 기존
pooling 보고서는 행 수를 월 수처럼 센 오류가 있었으므로 최신 코드로 전수 재실행하기 전에는
최종 pooling 단위를 확정하지 않는다.

정상출고는 다음처럼 분리한다.

```text
normal_outbound_signed_sum       = 원장 보존·대사값
model_demand_positive_sum        = sum(max(normal_outbound, 0))
negative_normal_outbound_count   = 음수 정상출고 건수
negative_normal_outbound_amount  = 음수 정상출고 절댓값 합
```

음수-only 월의 학습 수요는 0이며 signed 합을 수요 label로 사용하지 않는다.

## 4. 수요예측 성능

### 4.1 기존 전체 실행의 튜닝 결과

동일 TEST 605,437행에서 다음 결과가 관측됐다.

- baseline LightGBM: WAPE 38.12%, MAE 51.90, BIAS -15.89, BIAS% -11.67%
- tuned LightGBM: WAPE 35.68%, MAE 48.57, BIAS -12.82, BIAS% -9.42%
- WAPE 개선: 2.44%p
- 학습시간: 16.4초에서 166.1초로 약 10.13배 증가

튜닝 모델은 오차를 줄였지만 여전히 과소예측 편향이 있다. 단일 seed 42 결과이며 학습비용과
재고성과는 WAPE 하나에 포함되지 않는다.

### 4.2 최신 조합정책 재실행 결과

최신 dev 코드로 기존 백테스트 예측을 다시 읽어 조합정책을 재실행했다.

- calibration: 2025-08~09
- 진단 평가: 2025-10~12
- 선택 전략: 수요패턴별 calibration router
- calibration WAPE: 36.51%
- 진단 평가 WAPE: 37.17%
- 진단 평가 BIAS: -3.82%
- unit fill rate: 95.13%
- 양수수요 행 서비스 충족률: 88.91%
- 목표재고/실제수요 비율: 1.735

2025-10~12는 이전 실험에서 이미 검사한 기간이다. 따라서 이 결과는 진단용이며 최종 미사용
test 성능으로 제출하지 않는다.

## 5. 재고정책 시뮬레이션

기존 2025-09~12, 77,575개 시계열의 동일 조건 시뮬레이션 결과는 다음과 같다.

- 모델 정책: fill rate 96.12%, stockout-month rate 7.92%, 평균재고 171.40, 회전율 4.43,
  WAPE 33.51%
- naive 정책: fill rate 95.31%, stockout-month rate 11.30%, 평균재고 197.39, 회전율 3.85,
  WAPE 40.28%
- 모델 대비 naive 차이: fill rate +0.81%p, stockout-month rate -3.38%p,
  평균재고 -13.17%, 회전율 +0.58

이 결과는 모델 정책이 naive보다 적은 평균재고로 높은 fill rate를 보일 가능성을 시사한다.
하지만 평가구간 실제값으로 sigma를 추정했고 초기재고를 첫 목표재고로 두었으므로 절대 성능은
낙관 편향될 수 있다.

### 5.1 서비스 수준 민감도

예측량 multiplier를 높이면 다음 trade-off가 나타났다.

- 1.00: fill 96.12%, stockout 7.92%, 평균재고 171.40
- 1.05: fill 96.90%, stockout 6.35%, 평균재고 186.42, 재고 +8.76%
- 1.10: fill 97.50%, stockout 5.10%, 평균재고 201.97, 재고 +17.84%
- 1.20: fill 98.33%, stockout 3.37%, 평균재고 234.25, 재고 +36.67%
- 1.30: fill 98.85%, stockout 2.30%, 평균재고 267.52, 재고 +56.08%

따라서 서비스 수준을 높일수록 재고비용이 급격히 증가한다. 실제 초과재고비와 미충족 비용이
없으므로 단일 최적 multiplier를 결정하지 않는다.

## 6. 최신 발주 출력 형식

최신 정책은 운영 목표와 외부위험 shadow 목표를 분리한다. 실제 익명 예측값 두 건을 최신 dev
함수에 다시 적용하면 다음처럼 나온다.

```text
사례 A
predicted_usage                   0.8214
current_stock                     0.0000
base_stock / target_stock         1.4786 / 1.4786
recommended_order                 1.4786
shadow_risk_target_stock          1.4948
shadow_risk_buffer                0.0162
operational_adjustment_enabled    false
block_reason                      shadow_only_empirical_holdout_not_passed

사례 B
predicted_usage                   3.5175
current_stock                     0.0000
base_stock / target_stock         6.3315 / 6.3315
recommended_order                 6.3315
shadow_risk_target_stock          6.4011
shadow_risk_buffer                0.0696
operational_adjustment_enabled    false
block_reason                      shadow_only_empirical_holdout_not_passed
```

외부 위험은 분석상 목표재고를 높일 수 있지만 실제 발주량에는 반영되지 않는다. DORMANT,
NOT_OPERATED는 발주 0으로 억제하고, DATA_MISSING 또는 STALE 상태는 숫자 권고 대신 검토 대상으로
보낸다.

## 7. 재고상태 분류 결과

이 절의 숫자는 정책 v1.1 전체 실행 산출물이며 최신 signed/positive v1.2 계약으로 전수 재실행
전에는 참고값으로만 제공한다.

- 전체 시계열: 416,128개
- 원시 비양수 재고 후보: 10,910개
- 최근 수요가 있는 실제 stockout 후보: 4,546개
- 승인 분류까지 통과한 후보: 18개
- 동등품 재고로 억제: 1개
- 최종 긴급부족 후보: 17개
- DORMANT 필터: 189,791개
- stale/missing 관측 검토: 109,714개

10,910개에서 17개로 줄어든 99.84%는 정확도가 아니라 안전 gate 통과 결과다. 17개가 실제
긴급부족이라는 정답 라벨은 아직 없다.

## 8. 외부위험 신호 결과

### 8.1 예측 기여도

- 수요전용 baseline WAPE: 38.715%
- 동시점 외부위험 추가: 38.673%, 개선 0.043%p
- 시차 외부위험 추가: 38.628%, 개선 0.087%p
- 척도변환 위험 추가: 38.662%, 개선 0.053%p

개선폭이 매우 작고 잔차 상관도 절댓값 최대 약 0.03 수준이다. 외부위험을 수요예측 개선의
핵심 근거로 주장하지 않는다.

### 8.2 공급중단·무역 근거

- MFDS 공급중단 1,936건 중 마스터 매칭 839건
- 직접 품목 매칭 55건, 성분 매칭 88건
- 영향 품목코드 78개, 영향 시계열 3,193개
- 대상 사용량 비중: 1.62%
- 사건연구에서 통계적으로 유의한 사후효과 없음
- PP 수입가→2개월 기말재고: 최신 24개월 검증 p=0.4617, 재현 실패
- 탐색 90개 경로: Bonferroni 보정 후 유의 0개

정보원 제공 재고자료와 외부 공급사건 사이의 직접 노출키가 부족하다. 제조사·원산지·제품 BOM
확인이 없으므로 원자재·무역 위험은 설명용 shadow 신호다.

### 8.3 승인·매핑 gate

- 로컬 품목 분류 후보: 409,519개
- workflow상 bulk 승인: 409,519개
- 기존 증거로 자동발주 가능한 분류: 4,969개
- 원자재 공식 사실확인 승인: 0개
- 원자재 위험의 운영 eligible: 0개

bulk 승인은 검토 workflow에 넣었다는 뜻이지 외부 사실이 검증됐다는 뜻이 아니다.

## 9. 정보원에 제공해야 할 산출물

### 9.1 필수 요약보고서

- 연구 목적, 대상기간, 모집단, 예측 grain
- 원본·월별 행 수와 기관·부서·품목·시계열 수
- train/validation/test 시간분할
- baseline과 선택모델 WAPE, MAE, RMSE, BIAS
- fill rate, stockout-month rate, 평균재고, 회전율
- 패턴별 결과와 짧은 관측기간 품목의 한계
- 외부위험의 shadow-only 상태와 인과성 비확인
- 데이터 시차 8개월이 실험상 의도됐다는 설명

### 9.2 재현 manifest

- 원본 파일명, byte 크기, SHA-256
- 실행일시, Git commit, Python 및 핵심 라이브러리 버전
- 입력기간, 출력기간, 모델·정책·매핑 버전
- 환경변수의 이름과 존재 여부만 기록하고 API key 값은 제외
- 입력·중간·최종 산출물의 row count와 schema hash
- 성공·실패·제외 행 수와 제외 사유

본 실행 기준 환경은 Python 3.14.3, pandas 2.3.3, NumPy 2.5.1,
scikit-learn 1.9.0, LightGBM 4.7.0이다. GitHub CI는 Python 3.12에서도 309개 테스트와
31개 subtest를 통과했다.

### 9.3 행 단위 예측 결과

정보원 전달용 예측 파일에는 최소한 다음 필드가 필요하다.

- 식별: 기준월, 예측월, 익명 기관코드, 부서코드, 로컬 품목코드, representative_item_id
- 예측: actual_usage, predicted_usage, 모델명, demand_pattern, history_months
- 재고: current_stock, inventory_position, base_stock, target_stock, recommended_order
- shadow: external_risk_score, shadow_risk_target_stock, shadow_risk_buffer
- 안전 gate: demand_class, zero_stock_reason, inventory_action, suppression_reason
- 감사: mapping evidence scope, mapping version, model version, policy version, data age

원본 물품명은 인코딩과 재식별 가능성을 검토한 뒤 별도 제공한다. 정보원 내부 식별키와 AI의
익명 기관코드가 정확히 연결되는지 확인되지 않으면 기관별 권고를 운영 DB에 적재하지 않는다.

### 9.4 품질·예외 파일

- signed 출고와 positive 모델 수요 대사표
- 원장대사 위반 8행과 과거 39행
- 매핑 충돌·미매핑·proxy 매핑 목록
- DORMANT, NOT_OPERATED, DATA_MISSING, STALE 억제 목록
- 사람 검토가 필요한 VED·질병·원자재·제품 exact 매핑 목록
- 모델 실패·수집 실패·외부 API retry/permanent missing 상태

### 9.5 제공하면 안 되는 내용

- `.env` 파일과 API key·DB 비밀번호
- 개인정보 또는 환자 단위 기록
- 단위가 다른 품목 수량의 단순 합계
- 승인되지 않은 매핑을 사실처럼 표현한 원자재·질병 관계
- shadow 목표를 실제 발주 권고로 표시한 파일
- 2025-10~12를 final untouched test라고 표현한 문구

## 10. 정보원에 확인·요청할 사항

현재 실험을 수행하기 위해 새 데이터가 반드시 필요한 것은 아니다. 다만 다음 자료가 있으면
운영 해석과 경제적 최적화를 검증할 수 있다.

- 익명 기관코드 3,530개와 운영 DB 기관 3,598개의 검증된 대응표
- 기관 로컬 `USE` 코드와 전역 대표품목의 공식 대응표
- 발주 line: order_id, order_date, ordered_qty, promised_date
- 실제 입고: receipt_date, received_qty, partial_receipt_seq
- 서비스: requested_qty, fulfilled_qty, backorder_qty
- 비용: unit_price, emergency_unit_price, holding/disposal/shortage cost
- 유효기간·폐기: expiry_date, discard_date, discard_qty, discard_reason
- 공급노출: 제조사, 원산지, 표준제품코드, 계약 공급사

## 11. 제출 전 재실행해야 하는 항목

현재 보고서는 최신 조합정책과 shadow 분리는 다시 실행했지만 일부 대용량 전수 산출물은 PR #94
이전 결과를 근거로 한다. 정보원 외부 제출본을 확정하기 전에 다음을 최신 dev에서 전수 재실행한다.

1. signed/positive v1.2 월별 전처리와 재고상태 분류
2. representative-item 기준 중요도와 pooling 보고서
3. Module C shadow-only 재고권고와 신호별 ablation
4. 입력 hash·schema hash·정책버전을 포함한 run manifest
5. 개인·기관 재식별 위험과 물품명 인코딩 검수

## 12. 원본 DAT 무결성 manifest

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

## 13. 최종 판정

현재 모델은 연구 수준에서 baseline보다 낮은 예측오차와 naive보다 나은 재고 trade-off를
보인다. 그러나 독립 clean test, 실제 입고·비용, 승인된 제품 노출키가 없으므로 자동발주 운영
모델로 승인할 수는 없다. 정보원에는 성과와 함께 이러한 제한, shadow-only 정책, 제외·보류
목록, 원본 hash를 같은 제출물에 포함해야 한다.
