# GitHub 열린 이슈·PR 대조 및 수정 결과

작성일: 2026-07-22
대상: `DGU-TeamLex/ai`
확인 기준: GitHub 열린 이슈 19건, 열린 PR 1건

## 1. 결론

현재 로컬 구현으로 즉시 코드 수준에서 해결 가능한 항목은 다음과 같다.

- #5: `/health`, `/predictions`, `/recommend-order` 실제 HTTP 통합 테스트
- #14: 거즈 규격 위치·구분자 정규화, 주사기 용량별 병합 방지 회귀 테스트
- PR #26의 차단 의견: 재고0 정의, DORMANT 근거, 기관 매핑, 경로, `status` 덮어쓰기

#24와 #25는 중요한 계산·적재 기반을 만들었지만 아직 닫으면 안 된다. #24는 보정값을
예측 모델과 SS/ROP 평가에 통합해야 하고, #25는 검증된 익명기관-DB기관 매핑이 없어 실제
DB 적재가 차단돼 있다.

## 2. PR #26 검토 결과

PR: [#26 demand_class/mu_corrected 적재 스크립트](https://github.com/DGU-TeamLex/ai/pull/26)

### 기존 차단 문제

| 문제 | 기존 동작 | 영향 |
|---|---|---|
| 재고0 정의 | `1 - observed_month_ratio` | 거래가 없는 달을 결품으로 오인 |
| CENSORED 비율 | 57.3% | 실측 22.4% 대비 과다 분류 |
| DORMANT | 거래월 존재 여부로 판정 | 재고 보유 근거가 아님 |
| 기관 매핑 | 정렬한 두 목록을 `zip()` | 3,530 대 3,598 불일치가 조용히 잘림 |
| 입력 경로 | 다른 저장소 상대경로 | 현재 AI 저장소에서 재현 불가 |
| 상태 반영 | `status='DORMANT'` 덮어쓰기 | backend/FE 4상태 계약 파손 |

### 로컬 수정

- `raw_stock/*.DAT`에서 일별 마감재고 지속일을 직접 계산한다.
- 최종 관측 재고는 전체 데이터 기간말까지 지속된 것으로 처리한다.
- 기관·부서·품목에서 계산한 일수를 기관·품목 grain으로 합산한다.
- `zero_ratio = zero_stock_days / total_days`를 사용한다.
- Buhlmann 축소추정, 사전분포 상한, 양수 `mu_naive` 대비 10배 상한을 적용한다.
- CENSORED인데 관측 수요가 0이거나 보유일이 30일 미만이면 검토 대상으로 제외한다.
- 명시적인 `anon_institution_code,institution_id` 매핑을 요구한다.
- 기존 정렬 `zip()`은 기본 차단하고, 길이 불일치는 예외로 중단한다.
- 품질 리포트, 컬럼 존재, 유한·비음수 값, 중복 키, 99% DB 매칭률을 검사한다.
- DB 반영은 기본 dry-run이고 `--apply`를 명시해야 한다.
- `demand_class`, `mu_corrected`, `updated_at`만 수정하고 `status`는 건드리지 않는다.

PR #26의 원격 브랜치는 오래된 `dev`와 다른 저장소 산출물에 기반한다. 현재 수정본을
반영하려면 최신 기능 브랜치 기준으로 rebase 또는 새 PR 구성이 필요하다. 이번 작업에서는
GitHub 브랜치, PR, 이슈 상태를 변경하지 않았다.

## 3. 전체 데이터 검증 결과

| 지표 | 결과 |
|---|---:|
| raw_stock 논리 행 | 16,265,602 |
| 로컬 기관·부서·품목 | 416,128 |
| 기관·품목 handoff | 409,519 |
| ACTIVE | 183,437 |
| DORMANT | 135,730 |
| CENSORED | 90,352 |
| CENSORED 비율 | 22.06% |
| 검토 필요·적재 제외 | 58,516 |
| 적재 후보 | 351,003 |
| 10배 hard cap 적용 | 11,904 |
| 음수 출고 제외 원본 행 | 16 |
| 중복 handoff 키 | 0 |
| 비정상 `mu_corrected` | 0 |

CENSORED 비율은 PR 검토자가 제시한 검증 범위 20~25% 안에 있다. 품질을 통과한 것은
`load_eligible` 부분집합이며, 58,516개 검토 행을 포함한 전체 배치가 자동 승인된 것은
아니다.

## 4. 이슈별 판정

판정 기준:

- `코드 해결`: 완료 조건을 로컬 코드와 테스트가 충족한다. 병합 후 종료 가능하다.
- `일부 해결`: 핵심 코드가 있으나 데이터 승인, 통합, 백테스트 같은 완료 조건이 남았다.
- `외부 조건`: 현재 원천 기간이나 타 저장소 결정 없이는 완료할 수 없다.

| 이슈 | 판정 | 현재 확인 내용 | 남은 조건 |
|---|---|---|---|
| #5 API 통합 테스트 | 코드 해결 | 임시 uvicorn 서버와 CSV fixture로 세 엔드포인트 검증 | 원격 브랜치 반영·CI 확인 |
| #8 device 제거 | 코드 해결 | 입력·모델·서빙이 raw_stock 기준 | `dev` 병합 후 종료 |
| #14 규격 분리 | 코드 해결 | 거즈 10종, 주사기 3mL/5mL 분리 회귀 고정 | 전체 정규화 산출물 재생성 |
| #15 정규화 파이프라인 | 일부 해결 | 대표품목 생성, 단위·동의어 정규화 구현 | data repo 입력 계약 최종 확인 |
| #16 근거 분류 | 일부 해결 | 공식 근거 게이트와 UNCLASSIFIED 잔류 구현 | 사전 재사용 범위·근거율 검증 |
| #24 절단편향 | 일부 해결 | 일별 재고0 분류와 보호된 `mu_corrected` 생성 | 모델 mu/sigma·백테스트·SS/ROP 통합 |
| #25 AI DB 적재 | 일부 해결 | 첫 DML loader와 품질 게이트 구현 | 명시적 기관 매핑, PR 갱신, 운영 dry-run |
| #23 SS/ROP | 일부 해결 | 일별 평균·분산·LT 기반 공식과 세부유형 출력 구현 | 실제 LT 계약과 민감도 보고서 |
| #20 C-D 결합 | 일부 해결 | 수요/공급 경로와 LT·안전재고 반영 분리 | z 동적 조정과 SS/ROP 단일 정책 통합 |
| #19 위험 전파 | 일부 해결 | 이벤트→원자재→품목→경보 코드 구현 | 승인 품목-원자재 관계가 현재 0건 |
| #18 나프타 축 | 일부 해결 | 나프타→PP/PE/PVC 후보 경로 존재 | 승인 seed와 실제 가격 시계열 |
| #22 뉴스 override | 일부 해결 | GDELT와 사람이 수정하는 YAML weight 존재 | 요구 형식인 CSV override와 본문 미저장 검증 |
| #9 실제 수요예측 | 일부 해결 | 실제 raw_stock LightGBM·baseline·API 출력 구현 | Croston/SBA/TSB와 재고 시뮬레이션 |
| #2 실제 뉴스 API | 일부 해결 | GDELT 배치 collector 구현 | 운영 provider 검증, fallback 정책 합의 |
| #4 원자재 API | 일부 해결 | CSV·Alpha Vantage·FRED·Nasdaq adapter 구현 | 운영 키/계약 데이터와 실제 배치 검증 |
| #11 품목-원자재 | 일부 해결 | 공식 수집기·후보·감사 필드 구현 | 우선 300~500개 검수, 승인 관계 생성 |
| #12 질병-품목 | 일부 해결 | 수요위험과 공급위험 feature는 분리 | 질병 사전·승인 매핑·1~4주 선행 검증 |
| #10 전체 전처리 | 일부 해결 | 10개 DAT 전체 전처리와 산출물 생성 | 성능·중복·수불식·명칭충돌·멱등성 통합 리포트 |
| #21 2026-04 사건연구 | 외부 조건 | raw_stock이 2025-12에 종료 | 2026-04 이후 소비·재고 관측 데이터 |

## 5. 파일 위치

| 파일 | 설명 |
|---|---|
| `src/loading/compute_demand_class_mu_corrected.py` | 일별 재고 지속시간·분류·mu 보정 |
| `src/loading/reflect_demand_class_mu_corrected.py` | 보호된 COPY→임시테이블→UPDATE DML |
| `data/processed/censored_demand.parquet` | 로컬 416,128개 재고0 지표 |
| `outputs/demand_class_mu_corrected_handoff.csv` | 기관×품목 409,519개 handoff |
| `outputs/demand_class_mu_corrected_report.json` | 품질·릴리스 판정 |
| `data/sample/demand_class_mu_corrected_sample_1000.csv` | 검토용 1,000행 |
| `data/mapping/institution_id_mapping.csv` | 명시적 기관 매핑 입력 템플릿 |

## 6. 운영 전 순서

1. 현재 수정본을 최신 `dev` 계열 브랜치에 반영한다.
2. 전체 테스트와 CI를 통과시킨다.
3. 익명기관과 DB 기관의 명시적·검증된 매핑을 채운다.
4. PR #26을 교체하거나 갱신하고 차단 리뷰를 재요청한다.
5. `reflect_demand_class_mu_corrected`를 `--apply` 없이 운영 DB dry-run한다.
6. 99% 이상 키 매칭과 행별 샘플을 팀원이 확인한 뒤에만 `--apply`를 검토한다.
7. #24의 `mu_corrected`를 예측·재고정책에 통합하는 별도 실험을 진행한다.

기관 매핑이 준비되지 않은 현재 상태에서는 실제 DB 적재를 실행하지 않는다.

## 7. 검증

```text
python -m unittest discover -s tests -v
137 tests run: 136 passed, 1 sandbox-only skip

HTTP integration test outside the sandbox
1 passed

python -m compileall -q src tests
passed
```

skip 1건은 로컬 샌드박스가 TCP 소켓 생성을 차단해 uvicorn HTTP 통합 테스트를 실행하지
못한 경우다. 같은 테스트를 샌드박스 밖에서 재실행해 실제 localhost HTTP 요청 3건이 모두
통과함을 확인했다.
