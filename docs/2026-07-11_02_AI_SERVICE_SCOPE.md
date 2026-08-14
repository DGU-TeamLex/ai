# WeP-Stock AI Service Scope

> 2026-07-22 갱신: 일반 운영 트랜잭션과 DDL은 backend 소유지만, 품질 게이트를 통과한
> AI 배치 산출물의 DML 적재는 AI가 담당한다.

이 저장소는 WeP-Stock 전체 백엔드가 아니라 AI 학습 및 서빙 시스템만 담당합니다.

## 책임 범위

```text
1. raw_stock 일별 재고·정상출고 데이터 전처리
2. 수요 예측 feature table 생성
3. baseline / ML 모델 학습 (`src/modeling/`)
4. sample 뉴스 위험 점수 생성 및 문헌 기반 세부 weight scoring
5. sample 원자재 위험 점수 생성
6. 예측 결과 생성
7. safety stock / recommended stock 계산 (`src/modeling/inventory_policy.py`)
8. AI 결과 조회용 serving API 제공
9. 검증된 AI 배치 산출물 DML 적재 (`src/loading/`)
```

## 제외 범위

다음 기능은 백엔드 서비스에서 구현합니다.

```text
인증 / 사용자 / 권한 / RBAC
파일 업로드 / import_batch / 재처리 관리
물품 표준화 검수 워크플로우
기관/중앙/운영 대시보드 aggregation API
알림 상태 관리 및 발송
재배치 승인 워크플로우
DB 스키마 변경, 운영 트랜잭션, 감사 로그, row-level permission
```

## AI API Contract

AI 서비스는 백엔드가 호출할 수 있는 precomputed output 조회 API를 제공합니다.

```text
GET  /api/v1/ai/health
GET  /api/v1/ai/artifacts
POST /api/v1/ai/train
POST /api/v1/ai/forecasts/run
GET  /api/v1/ai/forecasts
GET  /api/v1/ai/forecasts/{institutionId}/{standardCode}
GET  /api/v1/ai/forecasts/eval
GET  /api/v1/ai/supply-risk
GET  /api/v1/ai/supply-risk/{itemGroupId}
GET  /api/v1/ai/inventory-policy
GET  /api/v1/ai/inventory-policy/{institutionId}/{standardCode}
GET  /api/v1/ai/order-recommendations
POST /api/v1/ai/recommend-order
```

## Backend Integration Assumption

- 백엔드는 인증/인가 후 AI 서비스에 내부 API로 요청합니다.
- AI 서비스는 사용자 세션이나 권한을 직접 판단하지 않습니다.
- AI 서비스는 원본 업로드 파일을 직접 받지 않고, 정제된 데이터 또는 batch input을 기준으로 학습합니다.
- API 요청 시점에 외부 뉴스 API, 원자재 API, LLM을 호출하지 않습니다.
- 학습/예측은 batch output을 만들고, serving API는 그 결과를 조회합니다.

## Data Contract

- `device/`, `MED_DEVICE_5`, `SIDO` 기반 데이터는 사용하지 않습니다.
- 학습 기준은 `raw_stock/*.DAT`입니다.
- 시계열 단위는 기관, 부서, 내부 물품코드 조합입니다.
- 정상출고량을 소비량으로, 마지막 마감재고량을 현재고로 사용합니다.
- 뉴스·원자재 매핑은 `stock_item_material_mapping.csv`의 검수된 행만 반영합니다.

## News Risk Weight Policy

뉴스 리스크 초기 weight는 `data/mapping/news_risk_weights.yaml`에서 관리합니다.

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

월별·품목별 리스크는 기사 점수 합을 그대로 쓰지 않고 아래 포화 함수로 변환합니다.

```text
monthly_item_news_risk = 1 - exp(-sum(article_score))
```

초기값은 뉴스 기반 불확실성 지수, 지정학 리스크 지수, HealthMap, 공급망 리스크 AHP/텍스트마이닝 연구를 참고한 설정값이며, 최종 weight는 validation WAPE와 과거 이벤트 회고분석으로 보정합니다.
