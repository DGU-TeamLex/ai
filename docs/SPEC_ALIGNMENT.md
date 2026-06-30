# WeP-Stock 기능 명세서 반영 현황

기준 문서: `WeP-Stock 기능 명세서 & API 명세서 (초안 v0.1)`

## 목표

기존 수요예측 중심 MVP를 WeP-Stock 도메인 구조에 맞게 확장했습니다.

```text
인테이크
→ 표준화
→ 수요 예측
→ 공급위험
→ 적정재고/발주/재배치
→ 알림
→ 기관/중앙/운영 대시보드
```

## 반영된 API 범위

| 명세 영역 | 구현 상태 | 구현 파일 |
|---|---|---|
| 인증/사용자 | dev token 기반 sample 구현 | `src/serving/api.py`, `src/serving/schemas.py` |
| 파일 인테이크 | multipart 업로드 shape, batch 상태/오류/리포트/reprocess 구현 | `src/serving/api.py` |
| 물품 표준화 | 검수 queue, 후보 상세, mapping decision, dictionary, report 구현 | `src/serving/api.py` |
| 수요 예측 | forecast list/detail/run/eval 구현 | `src/serving/api.py`, `src/train_model.py`, `src/predict.py` |
| 공급위험 | risk list/detail/backtest, material dependency 구현 | `src/serving/api.py`, `src/news/`, `src/commodity/` |
| 적정재고/발주 | inventory-policy, order recommendations 구현 | `src/serving/api.py`, `src/inventory_policy.py` |
| 재배치 | relocation list/approve sample 구현 | `src/serving/api.py` |
| 알림 | alert list/detail/resolve/settings 구현 | `src/serving/api.py` |
| 대시보드 | institution/central/ops summary 구현 | `src/serving/api.py` |
| 외부지표/마스터 | external indicators, institutions, item-groups 구현 | `src/serving/api.py` |

## 현재 MVP 가정

- DB는 아직 연결하지 않고 CSV/sample response로 동작합니다.
- JWT 검증은 실제 보안 구현 전 dev token response만 제공합니다.
- 업로드 파일은 실제 적재하지 않고 `import_batch` 생성 응답 형태를 검증합니다.
- 표준화는 rule/embedding/LLM 실제 모델 대신 sample 후보를 반환합니다.
- 수요 예측은 기존 `MED_DEVICE_5`를 임시 `standardCode`로 간주합니다.
- `SIDO`를 임시 기관 식별자(`inst_{SIDO}`)로 변환합니다.
- 공급위험은 sample 뉴스/원자재 위험 점수를 기반으로 합니다.

## 다음 구현 우선순위

1. DB 모델과 migration 추가
2. `import_batch`, `stock_movement`, `inventory_snapshot` 실제 적재 구현
3. 표준품목 master/KD/UDI ETL과 mapping review workflow 구현
4. ADI/CV² 패턴 분류와 Croston/SBA/TSB 라우팅 구현
5. 공급위험 뉴스/원자재 실제 collector 연결
6. SS/ROP 계산에 lead time master, lot/expiry, inbound 반영
7. RBAC/JWT/감사 로그 구현
8. API 통합 테스트와 프론트 연동 테스트 추가

