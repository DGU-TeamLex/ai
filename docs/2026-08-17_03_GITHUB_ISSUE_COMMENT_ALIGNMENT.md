# GitHub 이슈 최신 코멘트 반영 감사

- 감사일: 2026-08-17
- 저장소: `DGU-TeamLex/ai`
- 범위: 열려 있는 이슈 31개의 본문이 아니라 각 이슈의 최신 코멘트를 결정 기준으로 사용했다.
- 원칙: 검증되지 않은 가정은 코드로 강행하지 않고 shadow, quarantine, 사람 승인 또는 새 평가기간 게이트로 남긴다.

## 1. 이번 변경에서 코드로 반영한 항목

- #65: signed 정상출고 원장값과 양수-only 모델 수요를 별도 컬럼으로 분리했다. 음수-only 월의 모델 수요는 0이고 음수 건수·절댓값을 감사 필드로 보존한다.
- #22: 합성 뉴스는 비율과 무관하게 기본 실패한다. 공급 전역 사건은 명시적 재고 universe에만 전파한다. GKG 체크포인트에 성공·재시도·영구누락 상태와 계약 hash를 둔다.
- #20, #72: 뉴스·무역·원자재 위험의 운영 재고조정을 기본 차단하고 shadow 목표재고만 계산한다. PP lag 효과를 운영 가중치나 리드타임 증거로 사용하지 않는다.
- #44: pooling 월 수를 행 개수가 아닌 고유 월 수로 고쳤고, 개별 시계열 CV 후 그룹 내 CV 분포를 평가한다. 최종 선택은 holdout 서비스·fill·평균재고로 미룬다.
- #56: 기관 로컬 코드를 전역 통합하지 않고 `representative_item_id` 기준 사용량 등급을 계산한다. 가격 결측 때문에 ABC value class가 아니라 volume class로 명명하고 VED는 별도 검토한다.
- #62: 원자재 매핑을 대표품목→로컬품목→재고키 fanout으로 변경하고 충돌을 격리한다. 원자재 연결은 exact BOM이 아니라 proxy임을 기록한다.
- #58: 이미 검사한 2025-10~12를 final untouched test라고 부르지 않고 재사용 평가구간으로 표시한다.
- #5: `dev` 대상 오프라인 pytest CI workflow를 추가했다. required check 설정은 저장소 관리자 작업으로 남는다.

## 2. 전체 최신 코멘트 결정표

| 이슈 | 최신 코멘트에서 채택한 결정 | 현재 상태 |
|---|---|---|
| #72 PP lag | 24개월 검증 p=0.4617, 3개월 p=0.007은 불안정, 90검정 보정 후 0건. 운영 근거 금지 | 코드·문서 반영 |
| #65 signed demand | signed 대사값과 positive model demand, 음수 건수·양을 분리 | 코드·테스트 반영 |
| #63 FDA timing | 2023-11-30 사건과 2024-03-19 갱신을 분리, 월자료로 1~4주 효과 주장 금지 | 문서 게이트 |
| #62 mapping axis | `representative_item_id` 축, evidence scope, conflict quarantine, 가중 커버리지 | 코드 반영 |
| #58 evaluation | 동일 초기상태, 사전 고정 정책, WAPE·BIAS·fill·stockout·평균재고 동시 보고 | 일부 반영, 새 clean test 필요 |
| #57 institution prefix | 개선 0.0598%p로 작고 개인정보 위험. 지역 prefix 채택 금지 | 미채택 결정 |
| #56 importance | representative 축, 미해결 USE는 기관별 유지, volume class와 VED 분리 | 코드 반영 |
| #54 policy parity | 단일 versioned 정책, DB/source version, migration·rollback, 누락 규칙 명시 | backend/DB 확인 필요 |
| #52 mu floor | DORMANT 자동발주 차단, raw/stabilized 추정 분리, holdout 검증 | 기존 차단 유지, 추가 보고 필요 |
| #44 pooling | 고유 월 수, 개별 시계열 CV, 그룹 CV IQR, holdout 정책평가 | 코드 반영 |
| #43 ingredient | USE 단독 dedupe 금지, 대표품목/약품코드, 제형·함량·제조사와 hash 보고 | 최신 dev/PR 정렬 후 확인 |
| #41 governance | 승인자·버전·근거·rollback 없는 매핑을 운영에 사용 금지 | 승인 게이트 유지 |
| #39 lead time | 나라장터는 실제 입고가 아닌 약정 benchmark, 30일 민감도, 의료품 shadow | 문서·shadow 원칙 반영 |
| #38 zero stock | 0재고 suppression과 reason code를 유지 | 기존 구현 확인 |
| #34 historical ingestion | unresolved 후보 행을 별도 보고하고 조용히 누락하지 않음 | 기존 수정 확인 |
| #28 handoff | handoff 계약과 산출물 설명 유지 | 완료 상태 확인 |
| #23 SS/ROP | 연속 ROP가 아니라 versioned 정기검토 `(R,S)`, backend는 AI 정책만 소비 | 수식 반영, backend parity 필요 |
| #22 news | sentinel 0행 금지, 합성자료 fail, URL 검증, 체크포인트 상태·hash | 코드·테스트 반영 |
| #21 2026 event | 현재 원천 종료 뒤 사건은 causal 평가 금지, descriptive stress only | 평가 게이트 |
| #20 risk coupling | 다중검정·독립 holdout 전 자동발주 연결 금지, signal ablation | shadow 차단 반영 |
| #19 propagation | 승인된 매핑과 증거 수준을 따라 전파, 미매핑을 0위험으로 오해 금지 | 모집단 계약 반영 |
| #16 unclassified | 미분류를 강제 분류하지 않고 별도 상태·검토 대상으로 보존 | 기존 원칙 유지 |
| #15 normalization | 정규화 규칙과 원문·버전·충돌 보고를 분리 | 기존 원칙 유지 |
| #14 spec tokens | 환경·계약 토큰을 명시하고 알 수 없는 값은 fail closed | 기존 원칙 유지 |
| #12 disease mapping | 질병-수요 관계는 원자재 관계와 분리하고 사람 승인 필요 | 사람 라벨 대기 |
| #11 material mapping | 원자재 proxy와 exact product BOM을 구분 | 코드·문서 반영 |
| #10 reproducibility | 실행 manifest, 입력 hash, 자원·환경·정책 버전 기록 | 추가 구현 필요 |
| #9 model | 시간순 평가, 간헐수요, 정책 지표를 함께 비교 | 기존 구현·새 clean test 필요 |
| #8 raw pipeline | 대용량 원천을 정본으로 하고 schema·누락·대사 보고 | 현재 모집단 계약 반영 |
| #5 CI | dev PR/push에서 재현 가능한 offline test를 수행 | workflow 반영, 보호규칙 필요 |
| #4 commodity | 수집기 존재와 운영 품질·검증을 구분 | shadow 유지 |
| #2 news adapter | provider가 없거나 불완전하면 fail closed, 합성 fallback 금지 | 코드 반영 |

## 3. 아직 자동으로 완료할 수 없는 항목

다음은 코드를 더 작성한다고 정답이 생기지 않는다.

- #12와 #56의 질병·VED 임상 중요도는 담당자 라벨과 승인자가 필요하다.
- #43의 성분 제품 exact 연결은 최신 `dev`의 PR 산출물과 source hash를 먼저 맞춰야 한다.
- #54와 #23의 DB/backend 정책 일치는 실제 배포 DB schema와 consumer 확인이 필요하다.
- #58, #20, #72의 운영 승격은 사전에 보지 않은 12개월 이상 평가기간과 block bootstrap 결과가 필요하다.
- #10의 완전한 재현 manifest는 실행 entry point와 보존할 대용량 산출물 범위를 합의해야 한다.

이 항목들은 미반영이 아니라 최신 코멘트의 안전조건을 반영해 명시적 미승격 상태로 둔 것이다.

## 4. Git 반영 순서

현재 `feat/formula-data-evidence-alignment`는 `origin/dev`와 이력이 갈라져 있다. 이 변경을 먼저
검증한 뒤 최신 `dev` 위로 충돌 없이 정렬하고, feat PR을 통해 `dev`에만 병합한다. `main`에는
직접 push 또는 merge하지 않는다.
