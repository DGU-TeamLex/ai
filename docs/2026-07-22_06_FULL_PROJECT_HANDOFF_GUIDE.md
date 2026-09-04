# WeP-Stock AI 전체 프로젝트 이해 및 인수인계 가이드

작성일: 2026-07-22  
대상: 기획자, 데이터 검수자, AI 개발자, backend 개발자, 발표 담당자  
저장소: `DGU-TeamLex/ai`  
현재 작업 위치: `/home/user/teamlex`

## 읽는 방법

- 5분 요약: §2, §3, §21, §22, §24
- 물품 검수 담당: §7, §8, §14, §23
- 예측 모델 담당: §6, §9, §10, §11
- Module C 담당: §12, §13
- backend·DB 담당: §5, §15, §16
- 실행·인수인계 담당: §17 ~ §23

## 1. 이 문서의 목적

이 문서는 지금까지 진행한 WeP-Stock AI 작업을 처음 보는 사람도 전체 흐름과 현재 상태를
이해할 수 있도록 하나로 합친 인수인계 문서다. 다음 질문에 답하는 것을 목표로 한다.

1. 어떤 데이터를 사용하고 무엇을 예측하는가?
2. 물품명은 왜 정규화하며, 현재 어디까지 됐는가?
3. 뉴스와 원자재 가격은 재고량에 어떻게 반영되는가?
4. 기본재고, 안전재고, 목표재고와 발주량은 어떻게 다른가?
5. 현재 실제로 사용할 수 있는 결과와 사용할 수 없는 결과는 무엇인가?
6. GitHub 이슈와 PR은 어디까지 해결됐는가?
7. 다음 담당자는 무엇부터 진행해야 하는가?

## 2. 가장 짧은 요약

WeP-Stock AI는 `raw_stock/*.DAT`의 실제 재고·출고 이력으로 다음 달 사용량을 예측하고,
현재고와 안전재고 정책을 적용해 기본재고와 발주 권고량을 계산하는 시스템이다.

뉴스와 원자재 가격은 예측값에 무조건 더하지 않는다. 검증된 `질병-품목` 또는
`품목-원자재` 관계가 있을 때만 Module C가 수요위험과 공급위험을 각각 계산해 목표재고를
조정한다.

현재 핵심 상태는 다음과 같다.

| 영역 | 현재 상태 | 운영 판단 |
|---|---|---|
| raw_stock 전체 전처리 | 완료 | 재현 가능 |
| 로컬 사용량 예측 | 완료 | 품질 통제 조건부 사용 |
| 물품 정규화 후보 | 전체 생성 완료 | 대부분 사람 검수 필요 |
| 세부유형 예측 | 생성 완료 | 최신 분류로 재생성 필요 |
| 수요 절단편향 보정 | 계산 완료 | 모델에는 아직 미통합 |
| 품목-원자재 관계 | 후보 생성 완료 | 운영 승인 관계 0건 |
| 뉴스·가격 adapter | 구현 완료 | 운영 provider 미설정 |
| Module C 위험 전파 | 코드 완료 | 승인 관계가 없어 운영 점수 0건 |
| DB 배치 적재 | 보호 코드 구현 | 기관 ID 매핑이 없어 실행 금지 |

## 3. 절대 바뀌면 안 되는 원칙

### 3-1. 입력 데이터

- 유일한 재고·수요 원천은 `raw_stock/*.DAT`다.
- `device/`, `MED_DEVICE_5`, `SIDO` 기반 데이터와 코드는 사용하지 않는다.
- `regulazation/`은 외부 작업 결과를 비교·감사하는 참고 자료이지 운영 정본이 아니다.

### 3-2. 후보와 승인의 구분

- 이름 규칙으로 만든 family, 원자재, 질병 관계는 `candidate`다.
- 공식 근거나 사람 검수가 끝난 관계만 `approved`로 사용한다.
- 확인할 수 없는 품목을 억지로 분류하지 않고 `UNCLASSIFIED` 또는 `not_verified`로 남긴다.
- 쇼핑몰이나 블로그 한 곳만으로 제품·성분·원자재를 확정하지 않는다.

### 3-3. 규격과 재고 수량의 구분

- `24G`는 바늘 두께 규격이지 재고 개수가 아니다.
- `3mL`는 주사기 용량이고, 실제 재고 단위는 `EA`다.
- `100개입`은 포장수이고, `3mL`, `23G`, `24mm`와 각각 다른 속성이다.
- 서로 다른 규격이나 단위를 승인된 환산표 없이 합산하지 않는다.

### 3-4. 운영 안전

- 최신 데이터가 아니면 자동 발주에 사용하지 않는다.
- 품질 게이트가 `REVIEW` 또는 `BLOCK`인 행은 운영 DB에 반영하지 않는다.
- 기관코드를 정렬 순서만으로 `zip()`해 DB 기관과 연결하지 않는다.
- 실제 DB 반영은 dry-run, 키 매칭률, 샘플 검토를 거친 뒤에만 수행한다.

## 4. 핵심 용어

| 용어 | 쉬운 설명 |
|---|---|
| 원본 물품명 | 기관이 실제로 입력한 이름. 예: `[방문]혈당검사스틱` |
| 로컬 품목키 | 기관 안에서 재고를 식별하는 키 |
| 대표품목 | 여러 기관의 같은 물품명 후보를 묶은 검수 단위 |
| `item_group_id` | 의약품, 소모품, 폐기물 같은 대분류 |
| family | 주사기, 거즈, 혈당검사지 같은 물품 종류 |
| subtype | 주사기 사용량 기준, EO 멸균롤 같은 세부유형 |
| specification | `3mL`, `22G`, `15cm x 200m` 같은 규격 |
| unit | 실제 재고를 세는 단위. `EA`, `ROLL`, `TABLET` 등 |
| `mu` | 일정 기간의 평균 사용량 |
| `sigma` | 사용량 변동성 |
| SS | 안전재고. 예상보다 많이 쓰거나 조달이 늦어질 때의 여유분 |
| ROP | 재주문점. 재고가 이 수준 아래로 내려가면 발주를 검토하는 기준 |
| Module C | 뉴스·원자재 가격·공급사건 위험을 재고정책에 반영하는 모듈 |
| CENSORED | 재고가 없어서 실제 수요가 출고 기록에 충분히 나타나지 않은 상태 |
| DORMANT | 재고가 있었지만 실제 출고가 없었던 휴면 후보 |

## 5. 저장소 책임 경계

### AI 저장소 담당

- raw_stock 전처리와 데이터 품질 검사
- 물품명 정규화 후보와 검토 큐 생성
- 공식 근거 기반 분류 후보 생성
- 사용량 모델 학습·평가·예측
- 뉴스·원자재 위험 점수 계산
- Module C 위험 전파와 품질 게이트
- SS/ROP, 기본재고, 목표재고, 발주 권고 수치 계산
- AI 결과 조회 API
- 품질 게이트를 통과한 AI 배치 산출물의 DB DML 적재

### Backend 담당

- DB 테이블·컬럼·인덱스와 마이그레이션 같은 DDL
- 인증, 사용자, 권한
- 파일 업로드와 import batch 관리
- 검수·승인 UI와 승인 이력
- 운영 입출고·발주 트랜잭션
- 알림 발송·확인과 재배치 승인
- 중앙·기관 대시보드와 일반 조회 API

책임 경계는 `스키마(DDL)=backend`, `검증된 AI 배치 결과(DML)=AI`다. backend가 AI의
SS/ROP나 발주 계산식을 다시 구현하면 정책이 두 벌이 되므로 피한다.

## 6. 원천 데이터와 분석 단위

### 6-1. raw_stock 규모

| 항목 | 값 |
|---|---:|
| DAT 파일 | 10개 |
| 논리 레코드 | 16,265,602 |
| 월별 집계 행 | 3,729,983 |
| 로컬 시계열 | 416,128 |
| 기간 | 2024-01 ~ 2025-12 |
| 다음 예측월 | 2026-01 |

현재 날짜 기준 원천이 오래돼 `data_age_months=7`이다. 현재 예측은 시스템 구조와 모델
성능을 설명하는 중간 결과이며 실제 발주에 바로 사용하면 안 된다.

### 6-2. 로컬 예측 grain

```text
year_month + institution_code + department + item_code
```

- 목표값: 월 `정상출고량`
- 현재고: 해당 월 마지막 `마감재고량`
- 같은 기관이라도 부서가 다르면 사용 패턴이 다를 수 있어 별도 시계열로 유지한다.
- 품목 표준화가 끝나지 않아도 이 로컬 키의 예측은 가능하다.
- 기관 간 통합, 원자재 연결, 질병 연결에는 승인된 표준 품목 관계가 필요하다.

### 6-3. 주요 데이터 품질

| 지표 | 결과 |
|---|---:|
| 0 사용량 월 비율 | 32.58% |
| 음수 사용량 행 | 1 |
| 6개월 이상 이력 시계열 | 219,634 |
| 12개월 이상 이력 시계열 | 134,054 |

음수 사용량은 임의로 0으로 바꾸지 않고 학습 라벨에서 제외한다. 데이터가 없는 월도 자동
0 수요로 채우지 않고 관측 공백으로 처리한다.

## 7. 물품 정규화와 표준화

### 7-1. 왜 필요한가

다음 세 이름은 현장에서는 같은 종류의 물품일 수 있다.

```text
방)혈당스틱
혈당스틱
[방문]혈당검사스틱
```

정규화는 `방문`, `비급여`, `대여` 같은 운영 태그를 제품명에서 분리하고, 제품 동의어를
family로 연결한다. 위 예시는 다음과 같이 표현한다.

```text
운영 태그: 방문
대표 제품명 후보: 혈당검사스틱
family: BLOOD_GLUCOSE_TEST_STRIP
item_group_id: LAB_REAGENT
```

기관이 다르다는 이유만으로 서로 다른 표준품목이 되는 것은 아니다. 기관별 로컬 품목키는
유지하지만, 엄격히 정규화한 이름과 규격이 같으면 같은 대표품목 후보가 될 수 있다.

### 7-2. item group 14종

| ID | 이름 | 예측 대상 |
|---|---|---|
| `MED_ORAL` | 내복약 | 예 |
| `MED_INJECT` | 주사제 | 예 |
| `MED_TOPICAL` | 외용·패치 | 예 |
| `LAB_REAGENT` | 검사시약 | 예 |
| `DISINFECT` | 소독·멸균 | 예 |
| `MED_SUPPLY` | 치료재료·의료소모품 | 예 |
| `KM_EXTRACT` | 한방엑스제 | 예 |
| `KM_HERB` | 한방약초 | 예 |
| `SUPPLEMENT` | 영양제 | 예 |
| `PROMO` | 판촉·홍보물 | 아니오 |
| `FUEL` | 유류 | 예 |
| `WASTE` | 폐기물 | 아니오 |
| `RENTAL` | 대여물품 | 아니오 |
| `UNCLASSIFIED` | 미분류 | 검토 필요 |

`is_forecastable`은 정책값이다. 예를 들어 폐기물 사용량을 별도 운영 목적으로 예측할지
결정이 바뀐다면 taxonomy 승인 절차를 통해 변경해야 한다.

### 7-3. 이름을 분해하는 속성

```text
원본: 주사기(3cc/23g*24mm/100개입)

기본명: 주사기
용량: 3mL
바늘 게이지: 23G
바늘 길이: 24mm
포장수: 100
포장단위: EA
재고단위: EA
```

의약품은 제품명, 제조사, 성분, 함량, 제형, 포장수를 분리한다. 의료소모품은 family,
재질 후보, 치수, 용량, 게이지, 길이, 포장수를 분리한다.

거즈는 `거즈(4*4)`, `2*2거즈`, `거즈 10x10cm`처럼 위치와 구분자가 달라도 규격을
보존하도록 보강했다. 단위가 없는 `4*4`에 임의로 cm를 붙이지 않고 `4 x 4`로 유지한다.

### 7-4. 정규화 처리 단계

```text
raw_stock 물품명
-> 운영 태그 제거
-> 단위·동의어 정규화
-> 대표품목 후보 생성
-> 제조사/성분/용량/게이지/포장수 분해
-> group/family/subtype/spec/unit 후보
-> 공식 근거 또는 사람 검수
-> 승인 분류
```

### 7-5. 현재 결과

| 지표 | 결과 |
|---|---:|
| 기관별 로컬 물품 별칭 | 409,519 |
| 대표품목 | 101,546 |
| 승인 대표품목 | 1,177 |
| 승인 로컬 품목키 | 4,969 |
| 검토 대기 대표품목 | 100,369 |
| `UNCLASSIFIED` 대표품목 | 67,375 |
| 통합 검토 샘플 | 1,000 |

전체 통합 결과 101,546행은 모두 원자재 관점에서 `needs_review`다. `identified`는 family나
성분 후보를 찾았다는 뜻이지 제품 수준 원자재가 공식 검증됐다는 뜻이 아니다.

2026-07-22 추가한 거즈 규격 규칙과 회귀 테스트는 통과했지만, 101,546행 전체 통합 파일은
2026-07-18 생성본이다. 새 규칙을 데이터에 반영하려면 정규화·통합 파이프라인 전체를 다시
실행해야 한다.

## 8. 외부 근거와 오프라인 사전

외부 검색 결과를 매번 다시 찾지 않도록 검증된 정보는 증거 사전에 저장한다.

주요 필드는 다음과 같다.

```text
match_key
canonical_id
canonical_value
source_name
source_record_id
source_url
source_tier
verification_status
confidence
retrieved_at
evidence_note
dictionary_version
```

코드는 `verified_official`처럼 승인된 사전 행만 자동 분류에 사용한다. 후보나 2차 출처는
검토 화면에 제시할 수 있지만 자동 확정에는 쓰지 않는다.

우선 출처:

- 의약품: 식품의약품안전처 의약품 허가정보, 의약품안전나라, HIRA
- 의료기기: 식약처 UDI·품목분류·IFU
- 일반 물품: 조달청 물품목록 등 공공 정본
- 폐기물: 관련 법령과 공식 별표

## 9. 단순 사용량 예측 모델

### 9-1. 모델이 예측하는 값

모델은 재고량을 직접 예측하지 않는다. 다음 달 예상 사용량을 예측한다.

```text
과거 사용량과 시계열 feature
-> predicted_usage
-> 재고정책 계산
-> base_stock / target_stock / recommended_order
```

### 9-2. 데이터 분할

| 구간 | 기간 또는 행수 |
|---|---:|
| 학습 종료 | 2024-12 |
| 검증 | 2025-01 ~ 2025-06, 704,421행 |
| 테스트 | 2025-07 이후, 605,437행 |

미래 정보가 과거 학습에 섞이지 않도록 시간 순서로 분리한다.

### 9-3. 모델 비교

| 방법 | 검증 WAPE | 테스트 WAPE | 현재 판단 |
|---|---:|---:|---|
| LightGBM L1 | 40.19% | 38.87% | 현재 champion |
| LightGBM Tweedie | 40.77% | 37.60% | challenger |
| 3개월 평균 | 44.52% | 40.02% | baseline |
| 전월값 | 48.07% | 44.75% | baseline |
| 전년 동월 | 54.92% | 52.67% | baseline |

검증 WAPE가 가장 낮은 LightGBM L1을 선택했다. 테스트 구간만 보면 Tweedie가 더 좋지만,
테스트 결과를 보고 모델을 다시 고르면 테스트 누수가 된다. 다음 데이터 배치에서 Tweedie를
challenger로 재검증해야 한다.

WAPE 약 40%는 모든 품목에 동일하게 신뢰할 수 있는 완성 모델이라는 뜻이 아니다. 간헐수요,
짧은 이력, 만성 결품 구간을 분리해 평가해야 한다.

## 10. 재고가 없어서 기록되지 않은 수요

### 10-1. 문제

재고가 0이면 실제로 필요해도 출고할 수 없다. 따라서 단순히 `출고량 / 전체기간`으로
평균수요를 계산하면 만성 결품 품목의 수요를 과소평가한다.

월 거래가 없었다는 사실과 재고가 0이었다는 사실도 다르다. PR #26 초기 구현은
`1 - 거래 관측 월 비율`을 재고0 비율로 사용해 CENSORED를 57.3%로 과다 분류했다.

### 10-2. 현재 계산 방법

raw_stock의 일별 마감재고가 다음 거래일까지 유지된다고 본다.

```text
zero_ratio = zero_stock_days / total_days
mu_naive = demand_total / total_days
available_rate = demand_total / held_stock_days
```

여기서 `mu_naive`와 `mu_corrected`는 일 단위 노출률이다. 현재 예측 모델의
`predicted_usage`는 월 단위이므로, 두 값을 바로 교체하거나 합산하면 안 된다. 모델 통합 시
일·월 단위 계약과 부서 노출 집계 방식을 먼저 확정해야 한다.

분류:

```text
DORMANT  = demand_total == 0 and zero_ratio < 0.5
CENSORED = zero_ratio >= 0.5
ACTIVE   = 그 외
```

`mu_corrected`는 재고 보유기간과 품목별 사전분포를 사용한 Buhlmann 축소추정으로 계산한다.
분모가 작은 품목의 폭주를 막기 위해 사전분포 상한과 양수 `mu_naive` 대비 최대 10배 상한을
함께 적용한다.

다음은 자동 적재에서 제외한다.

- 재고 관측 커버리지가 부족한 행
- CENSORED인데 실제 관측 수요가 0인 행
- CENSORED인데 재고 보유일이 30일 미만인 행

### 10-3. 전체 결과

| 지표 | 결과 |
|---|---:|
| 기관·부서·품목 로컬 시계열 | 416,128 |
| 기관·품목 handoff | 409,519 |
| ACTIVE | 183,437 |
| DORMANT | 135,730 |
| CENSORED | 90,352 |
| CENSORED 비율 | 22.06% |
| 검토 필요·적재 제외 | 58,516 |
| 적재 후보 | 351,003 |
| 10배 hard cap 적용 | 11,904 |
| 중복 handoff 키 | 0 |
| 비정상 `mu_corrected` | 0 |

품질 상태는 `PASS_WITH_REVIEW`다. 즉 351,003개 적재 후보 부분집합은 품질조건을
통과했지만, 58,516개 검토행을 포함한 전체 배치가 자동 승인된 것은 아니다.

### 10-4. 아직 남은 일

현재 LightGBM은 실제 출고량을 예측한다. `mu_corrected`는 별도 handoff로 계산됐지만 모델
학습 라벨, sigma, SS/ROP, 백테스트에는 아직 통합되지 않았다. 따라서 현재 예측을 잠재수요
예측이라고 설명하면 안 된다.

## 11. 기본재고와 발주량 계산

### 11-1. 현재 단순 정책

```text
protection_period_days
  = review_period_days + lead_time_days

protection_period_demand
  = predicted_usage * protection_period_days / 30

safety_stock
  = protection_period_demand * safety_stock_rate

base_stock
  = protection_period_demand + safety_stock

inventory_position
  = current_stock + on_order_qty - backorder_qty

recommended_order
  = max(base_stock - inventory_position, 0)
```

현재 기본값은 검토주기 30일, 리드타임 0일, 안전재고율 20%다. 리드타임 0은 실제 조달이
즉시 된다는 뜻이 아니라 아직 실제 리드타임 데이터 계약이 없다는 뜻이다.

### 11-2. 일별 분산 기반 SS/ROP

명시적인 일평균, 일별 표준편차, 리드타임이 있을 때는 다음 공식을 사용할 수 있다.

```text
SS  = z * daily_demand_stddev * sqrt(effective_lead_time_days)
ROP = mean_daily_usage * effective_lead_time_days + SS
```

`level_based_daily_ss_rop`와 `module_c_periodic_target_stock`은 서로 다른 정책이다. 같은
발주량에 두 안전재고를 동시에 더하면 이중 과적재가 되므로 한 정책만 선택해야 한다.

## 12. 뉴스·질병·원자재 가중치 구조

외부 위험은 두 개의 인과 경로로 나눈다.

```text
[수요 경로]
뉴스 키워드 -> 질병/사건 -> 사용 의료용품·의약품 -> 수요위험

[공급 경로]
뉴스 또는 선물·원자재 가격 -> 원자재 -> 의료용품 -> 공급위험
```

두 경로를 하나의 `related_material` 필드에 섞지 않는다.

- 수요위험은 예상 사용량 증가 방향으로 반영한다.
- 공급위험은 리드타임과 안전재고 증가 방향으로 반영한다.
- 승인되지 않은 관계는 위험 점수 0으로 둔다.

### 12-1. 뉴스 점수

```text
article_score
= event_type_weight
* severity
* confidence
* source_weight
* item_relevance
* exposure_weight
* recency_weight
* novelty_weight
```

현재 뉴스는 CSV와 GDELT collector를 지원한다. 가중치는
`data/mapping/news_risk_weights.yaml`에서 사람이 수정할 수 있다. GitHub #22가 요구하는
CSV override 형식은 아직 추가되지 않았다.

### 12-2. 가격 provider

지원 adapter:

```text
csv
alpha_vantage
fred
nasdaq_data_link
```

운영 기본값은 `disabled`다. 합성 sample은 smoke test 전용이며 운영 provider 실패 시
자동 대체하지 않는다. 나프타 직접 가격이 없을 때 Brent 같은 proxy를 쓸 수 있지만,
`is_proxy`, `proxy_quality`, 낮은 전파 가중치로 직접 가격과 구분해야 한다.

## 13. Module C 위험 조정

### 13-1. 흐름

```text
뉴스·가격 이벤트
-> 원자재 또는 질병 코드
-> 승인된 품목 관계
-> 수요위험 / 공급위험
-> 위험조정 사용량·리드타임·안전재고
-> target_stock
-> recommended_order
-> 경보 후보
```

### 13-2. 현재 상태

| 지표 | 결과 |
|---|---:|
| 승인 로컬 분류 입력 | 4,969 |
| 품목-원자재 후보 관계 | 11,383 |
| 시장요인 연결 후보 | 6,741 |
| 원자재 관계 운영 승인 | 0 |
| 운영 위험 점수 | 0 |
| 경보 | 0 |

위험 점수가 0인 것은 코드가 실패한 것이 아니다. 미검수 관계를 재고량에 반영하지 않도록
승인 게이트가 정상 작동한 결과다.

### 13-3. 공급위험 품질 게이트

| 상태 | 행수 | 의미 |
|---|---:|---|
| PASS | 0 | 운영 반영 가능 |
| REVIEW | 4,647 | 사람 검토 전 반영 금지 |
| BLOCK | 322 | 격리·계산 제외 |

주요 오류:

- `SR019`: 사건 코드가 기준 공급코드 필드에 혼입, 4,647행
- `SR018`: 구 API 공급위험 alias 사용, 1,213행
- `SR003`: 정책에 없는 국내과점 코드, 322행

현재 `batch_release_allowed=false`이므로 Module C 결과로 안전재고를 자동 조정하면 안 된다.
가중치는 인과 추정 결과가 아니라 `policy_seed_requires_backtest` 상태의 초기 정책값이다.

## 14. 세부유형 단위 출력

승인 분류가 존재하면 로컬 예측을 다음 단위로 집계한다.

```text
기관 + 부서 + item_group + family + subtype + specification + unit
```

예:

| 품목 | 세부유형 | 규격 | 단위 |
|---|---|---|---|
| 주사기 | 주사기 사용량 기준 | 3mL | EA |
| 주사기 | 주사기 사용량 기준 | 5mL | EA |
| 의료폐기물 용기 | 합성수지형 needle box | 2L | EA |
| EO 멸균포장재 | EO 멸균롤 | 15cm x 200m | ROLL |
| 카테터 | angio needle | 22G 성인용 | EA |

현재 세부유형 출력은 2,348행이지만 승인 분류 4,948행을 사용한 이전 생성본이다. 현재 승인
분류는 4,969행이므로 `classified_prediction`을 다시 실행해야 한다. 전체 로컬 예측 대비
승인 분류 매칭 커버리지 역시 약 1.74%로 낮아 운영 범위가 제한적이다.

## 15. DB 배치 적재

### 15-1. 현재 첫 적재 대상

```text
inventory.demand_class
inventory.mu_corrected
```

GitHub PR #26 코멘트 기준으로 backend DDL은 준비됐지만, 로컬에서는 운영 DB에 연결해
독립 검증하거나 실제 값을 반영하지 않았다.

### 15-2. 적재 안전장치

- 품질 리포트가 릴리스 가능한 부분집합인지 확인
- `status` 컬럼을 변경하지 않음
- `mu_corrected`의 NaN, 무한대, 음수 차단
- 기관·품목 중복 키 차단
- `COPY -> TEMP TABLE -> UPDATE FROM` 사용
- 임시테이블 `ON COMMIT DROP`
- DB 키 매칭률 99% 미만이면 rollback
- 기본 dry-run, `--apply`를 명시해야 commit

### 15-3. 현재 차단 사유

`data/mapping/institution_id_mapping.csv`에 검증된 익명기관-DB기관 대응이 없다. 현재
preflight는 3,530개 익명 기관코드가 매핑되지 않았다고 보고 DB 연결 전에 중단한다.

따라서 현재 상태에서 `--apply`를 실행하면 안 된다.

## 16. API와 화면

주요 FastAPI endpoint:

```text
GET  /health
GET  /api/v1/ai/forecasts
GET  /api/v1/ai/predictions/by-subtype
GET  /api/v1/ai/inventory-policy
GET  /api/v1/ai/supply-risk
POST /api/v1/ai/recommend-order

GET  /predictions
POST /recommend-order
```

API 요청 시 외부 뉴스나 가격 API를 호출하지 않는다. 배치가 미리 생성한 파일을 읽는
batch-first 구조다. `/health`, `/predictions`, `/recommend-order`는 임시 uvicorn 서버와 CSV
fixture를 사용한 실제 HTTP 통합 테스트를 추가했다.

Streamlit 화면은 AI 산출물을 확인하는 MVP이며 운영 승인, 권한, 알림 처리를 담당하지 않는다.

## 17. 주요 파일 위치

### 17-1. 정규화·분류

| 파일 | 설명 |
|---|---|
| `data/processed/item_integrated_classification_v2.csv` | 대표품목 전체 101,546행 |
| `data/processed/item_integrated_classification_v2.parquet` | 동일 전체 결과 Parquet |
| `data/sample/item_integrated_classification_sample_1000.csv` | 통합 검토 샘플 1,000행 |
| `data/processed/item_integrated_classification_v2_report.json` | 통합 품질 리포트 |
| `data/mapping/item_forecast_classification_approved.csv` | 승인 로컬 분류 |
| `data/mapping/item_family_taxonomy.csv` | 품목 taxonomy |
| `data/mapping/item_attribute_evidence_dictionary_v1.csv` | 검증 증거 사전 |

### 17-2. 예측·재고

| 파일 | 설명 |
|---|---|
| `outputs/stock_predictions.csv` | 로컬 품목 예측·재고정책 163,229행 |
| `outputs/stock_predictions_by_subtype.csv` | 세부유형 집계 2,348행 |
| `outputs/stock_model_validation_report.csv` | 모델 검증 성능 |
| `outputs/stock_evaluation_report.csv` | 테스트 평가 |
| `models/stock_manifest.json` | 모델 선택·제외 사유 |

### 17-3. 절단편향·DB handoff

| 파일 | 설명 |
|---|---|
| `data/processed/censored_demand.parquet` | 로컬 416,128개 재고0 지표 |
| `outputs/demand_class_mu_corrected_handoff.csv` | 기관×품목 409,519행 |
| `outputs/demand_class_mu_corrected_report.json` | 품질·릴리스 판정 |
| `data/sample/demand_class_mu_corrected_sample_1000.csv` | 검토 샘플 1,000행 |
| `data/mapping/institution_id_mapping.csv` | 기관 매핑 입력 템플릿 |

### 17-4. Module C

| 파일 | 설명 |
|---|---|
| `data/mapping/stock_item_material_mapping.csv` | 승인 품목-원자재 관계 |
| `data/mapping/news_risk_weights.yaml` | 뉴스 weight 정책 |
| `data/mapping/market_series_registry.csv` | 시장 시계열 registry |
| `outputs/module_c_run_report.json` | Module C 실행·차단 사유 |
| `outputs/module_c_supply_risk_quality_classified.csv` | 전체 품질 분류 |
| `data/sample/module_c_supply_risk_quality_sample_1000.csv` | 공급위험 검토 샘플 |

## 18. 실행 순서

### 18-1. 환경과 테스트

```bash
conda activate teamlex
pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

### 18-2. 물품 전체 재생성

```bash
conda run -n teamlex python -m src.item_normalization --full
conda run -n teamlex python -m src.item_enrichment build-worklist
conda run -n teamlex python -m src.item_enrichment match
conda run -n teamlex python -m src.item_integrated_pipeline \
  --with-excel \
  --sample-size 1000
```

공식 웹 근거 수집은 네트워크·출처 정책을 확인한 뒤 별도 실행한다.

### 18-3. 예측 배치

```bash
conda run -n teamlex python -m src.preprocessing
conda run -n teamlex python -m src.feature_engineering
conda run -n teamlex python -m src.modeling.training
conda run -n teamlex python -m src.modeling.prediction
conda run -n teamlex python -m src.modeling.classified_prediction
conda run -n teamlex python -m src.modeling.evaluation
```

### 18-4. Module C

```bash
conda run -n teamlex python -m src.news.news_risk_scorer
conda run -n teamlex python -m src.commodity.commodity_risk_scorer
conda run -n teamlex python -m src.module_c.pipeline
```

### 18-5. 수요 절단편향

```bash
conda run -n teamlex python -m src.loading.compute_demand_class_mu_corrected
```

기존 일별 지표를 재사용해 handoff만 다시 만들 때:

```bash
conda run -n teamlex python -m src.loading.compute_demand_class_mu_corrected \
  --reuse-metrics \
  --sample-size 1000
```

### 18-6. DB 사전검사

```bash
# 기본 dry-run이다. 현재는 기관 매핑이 없어 정상적으로 중단된다.
conda run -n teamlex python -m src.loading.reflect_demand_class_mu_corrected
```

`--apply`는 기관 매핑과 운영 dry-run을 팀이 승인하기 전에는 사용하지 않는다.

## 19. 테스트 결과

2026-07-22 기준:

```text
전체 테스트 137개
샌드박스: 136 passed, TCP 통합 테스트 1 skipped
샌드박스 밖 실제 HTTP 통합 테스트: passed
Python compileall: passed
```

추가된 주요 회귀 테스트:

- 재고0 지속일 계산
- CENSORED/DORMANT/ACTIVE 분류
- 보정 수요의 유한·비음수·10배 상한
- 위험한 정렬 `zip()` 기관 매핑 차단
- `inventory.status` 미수정
- 거즈 10종 규격 위치·구분자 처리
- 주사기 3mL/5mL 대표품목 분리
- `/health`, `/predictions`, `/recommend-order` 실제 HTTP 호출

## 20. GitHub 이슈와 PR

2026-07-22 확인 기준 열린 이슈 19건, 열린 PR 1건이다.

### 코드 해결 판단

- #5: API 실제 HTTP 통합 테스트
- #8: device 의존성 제거와 raw_stock 전환, 단 `dev` 병합 필요
- #14: 규격 토큰 분리와 병합 방지 회귀 테스트

### 일부 해결

- #24: 수요 절단편향 계산 완료, 모델·sigma·SS/ROP 통합 필요
- #25: 첫 DB DML loader 구현, 기관 매핑과 운영 dry-run 필요
- #9: 실제 예측 구현, Croston/SBA/TSB와 재고 시뮬레이션 필요
- #11: 원자재 후보 생성, 300~500개 우선 검수와 승인 관계 필요
- #12: 위험축 분리, 질병 사전·승인 매핑·선행성 검증 필요
- #19/#20/#23: 위험전파·정책 공식 구현, 실제 승인 데이터·리드타임·통합 검증 필요

### 외부 데이터 조건

- #21의 2026-04 사건연구는 raw_stock이 2025-12에 끝나 현재 소비·품절 선행성을 검증할 수 없다.

PR #26의 차단 의견은 로컬 코드에서 수정했지만 원격 PR 브랜치는 아직 갱신하지 않았다.
최신 상세 판정은 `docs/2026-07-22_05_GITHUB_ISSUE_PR_ALIGNMENT.md`에 있다.

또한 `/home/user/teamlex`는 현재 Git 저장소로 인식되지 않는다. 이 문서와 최신 수정 코드는
아직 원격 브랜치에 커밋·push된 상태가 아니므로, Git 작업 전 `/tmp`의 AI 저장소 clone과
변경 범위를 대조해야 한다.

## 21. 현재 완료된 것과 완료되지 않은 것

### 완료된 것

- raw_stock 10개 전체 로딩과 월별 전처리
- device 입력 제거
- 대표품목 101,546개 정규화·분류 후보 생성
- 속성 분해와 외부 근거 사전 구조
- 전체·샘플 정규화 산출물
- LightGBM과 baseline 비교, 실제 예측 파일 생성
- 기본재고·목표재고·발주 권고 계산
- 세부유형·규격·단위 출력 구조
- 뉴스·가격 adapter와 Module C 위험 엔진
- 공급위험 품질 게이트
- 일별 재고0 기반 수요분류와 `mu_corrected`
- 보호된 DB batch loader
- GitHub 이슈·PR 대조 문서

### 완료되지 않은 것

- 101,546개 대표품목의 사람 검수
- 승인 품목-원자재 관계
- 승인 질병-품목 관계
- 실제 운영 뉴스·원자재 가격 provider
- 실제 기관 ID 매핑
- 실제 조달 리드타임과 입고예정·미납 데이터
- 수요 절단편향을 반영한 모델 재학습과 백테스트
- Croston/SBA/TSB 등 간헐수요 전용 모델
- 결품률·fill rate·평균재고 기반 재고 시뮬레이션
- 2026년 최신 raw_stock
- Module C 사건연구와 정책 계수 calibration
- 최신 코드의 원격 브랜치 반영·PR 갱신

## 22. 다음 작업 우선순위

### P0. 운영 오반영 방지

1. 현재 품질 게이트와 승인 게이트를 제거하지 않는다.
2. 명시적 기관 ID 매핑이 오기 전 DB 적재를 금지한다.
3. Module C `PASS=0` 상태에서 위험조정 재고를 운영값으로 사용하지 않는다.
4. 최신 raw_stock이 오기 전 현재 예측을 자동 발주에 사용하지 않는다.

### P1. 팀 검수

1. 사용량과 결품 영향이 큰 300~500개 대표품목을 우선 선정한다.
2. family/subtype/spec/unit를 검수한다.
3. 의약품은 공식 성분·제조사, 의료기기는 UDI·IFU를 연결한다.
4. 품목-원자재와 질병-품목 관계를 서로 다른 승인 CSV로 관리한다.
5. 승인 후 `classified_prediction`과 Module C를 같은 배치 버전으로 다시 실행한다.

### P2. 예측·재고정책 개선

1. `mu_corrected`를 모델 A/B와 SS/ROP 실험에 연결한다.
2. 결품 구간 제외 또는 가중 백테스트를 설계한다.
3. LightGBM, Tweedie, Croston, SBA, TSB를 비교한다.
4. WAPE뿐 아니라 fill rate, 결품일수, 평균재고, 과잉재고를 비교한다.
5. 실제 리드타임과 일별 수요분산 계약을 확정한다.

### P3. 외부 위험 운영화

1. 운영 뉴스 provider와 수집주기를 확정한다.
2. 직접 나프타·PP·PE·PVC 또는 계약 가격 시계열을 연결한다.
3. 승인 관계만 사용해 Module C를 재실행한다.
4. 과거 사건과 실제 품절·사용량의 1~4주 선행성을 검증한다.
5. 정책 seed를 실증 계수로 교체한다.

### P4. Git 협업

1. `/home/user/teamlex` 변경을 최신 AI feature 브랜치에 반영한다.
2. PR #26을 최신 raw_stock·Module C 코드 기준으로 rebase하거나 대체 PR을 만든다.
3. #5, #14는 CI 통과 후 종료한다.
4. #24, #25는 남은 완료 조건을 이슈 체크리스트에 반영한다.
5. `main`에 직접 push하지 않고 `feat/* -> dev -> main` 순서를 지킨다.

## 23. 역할별 바로 해야 할 일

| 담당 | 가장 먼저 할 일 |
|---|---|
| 데이터·의료 검수자 | 우선 300~500개 품목의 family/spec/unit/성분/원자재 검수 |
| AI 개발자 | `mu_corrected` 통합 실험, 간헐수요 모델, 재고 시뮬레이션 |
| Module C 담당 | 승인 매핑과 운영 뉴스·가격 연결, 사건연구 |
| Backend 담당 | DDL·기관 매핑 제공, 적재 dry-run 공동 검증 |
| 운영 담당 | 실제 리드타임, 입고예정, 미납, 발주단위 계약 제공 |
| 발표 담당 | 후보와 승인, 예측 사용량과 재고량, 기본과 위험조정을 구분해 설명 |

## 24. 발표할 때 사용할 핵심 문장

> 과거 출고 이력으로 다음 달 사용량을 예측한 뒤 기본재고를 계산하고, 검증된 질병·원자재
> 관계가 있을 때만 외부 위험을 반영해 목표재고를 조정합니다. 현재 예측과 품질 게이트는
> 구현됐지만 품목·원자재 승인, 최신 데이터, 실제 리드타임이 부족해 운영 자동발주는 아직
> 차단된 상태입니다.

## 25. 관련 문서

| 문서 | 목적 |
|---|---|
| `docs/2026-07-20_01_SYSTEM_STRUCTURE_AND_PROGRESS.md` | 전체 구조와 수치 중심 현황 |
| `docs/2026-07-22_01_MIDTERM_SIMPLE_INVENTORY_MODEL.md` | 단순 예측·기본재고 발표자료 |
| `docs/2026-07-22_02_MIDTERM_WEIGHT_SELECTION_MODEL.md` | 뉴스·원자재 weight 발표자료 |
| `docs/2026-07-22_03_MIDTERM_FINAL_INVENTORY_SYSTEM.md` | 최종 재고량 결합 발표자료 |
| `docs/2026-07-22_04_MIDTERM_DECISION_PROCESS.md` | 기준 선정 과정과 근거 |
| `docs/2026-07-22_05_GITHUB_ISSUE_PR_ALIGNMENT.md` | 최신 GitHub 이슈·PR 대조 |
| `docs/2026-07-13_02_ITEM_STANDARDIZATION_TEAM_GUIDE.md` | 팀 수동 표준화 절차 |
| `docs/2026-07-18_05_MODULE_C_RISK_ADJUSTMENT.md` | Module C 상세 설계 |
| `docs/2026-07-18_06_SUPPLY_RISK_QUALITY_GATE.md` | 공급위험 품질 게이트 운영법 |

## 26. 최종 주의사항

이 시스템에서 가장 중요한 것은 분류 행수를 많이 채우는 것이 아니라 잘못된 확정을 운영
재고에 넣지 않는 것이다.

현재 `UNCLASSIFIED`, `needs_review`, 위험 점수 0, DB 적재 중단은 실패가 아니라 근거가
부족할 때 시스템이 정직하게 멈춘 결과다. 다음 작업도 이 원칙을 유지해야 한다.
