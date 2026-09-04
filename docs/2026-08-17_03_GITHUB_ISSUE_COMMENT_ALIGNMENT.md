# GitHub 이슈 최신 코멘트 반영 감사

- 감사일: 2026-08-17
- 저장소: `DGU-TeamLex/ai`
- 대상 브랜치: `feat/report-storyline-and-data-examples` → `dev`
- 보호 대상: 사용자 요청에 따라 `main`에는 직접 push·merge하지 않음
- 판단 기준: 이슈 본문보다 가장 최근 코멘트를 우선하되, 코멘트 이후의 코드·테스트·산출물도 함께 확인함

## 1. 상태를 읽는 법

| 상태 | 뜻 |
|---|---|
| 반영 완료 | 최신 요구가 코드·테스트 또는 재현 산출물에서 확인됨 |
| 부분 반영 | 핵심 로직은 있으나 운영·DB·독립평가·승인 같은 완료조건이 남음 |
| 미채택 | 실험 결과나 위험 때문에 의도적으로 모델·운영정책에 넣지 않음 |
| 외부 확인 대기 | 정보원·임상담당자·backend·DB 또는 새 데이터가 있어야 끝낼 수 있음 |

`부분 반영`과 `외부 확인 대기`를 실패로 해석하면 안 된다. 최신 코멘트가 요구한 안전조건에
따라 연구결과가 자동발주로 승격되지 않게 막아 둔 상태도 정상적인 반영이다.

## 2. 이번 감사에서 직접 확인한 사항

- #65: `normal_outbound_signed_sum`, `model_demand_positive_sum`, 음수 건수·절댓값이 분리돼 있다.
  실제 추출 결과 현재기간 음수 포함 월 15개, 음수만 기록된 월 1개, 혼재 월 14개였다.
- #22: 최신 코멘트 이후 합성뉴스가 소수만 섞여도 strict 모드에서 차단되고, 공급 전역사건은
  명시적 재고품목 모집단으로 전파되며, GDELT 슬라이스는 성공·재시도·영구누락 상태와 계약
  hash를 가진다. 관련 `unittest` 35개를 포함한 선별검사 44개가 통과했다.
- #62·#56: 승인된 `representative_item_id`를 중심으로 전파·사용량 등급을 계산하고, 승인되지
  않은 로컬품목은 합치지 않는 회귀검사를 확인했다.
- #58: 2025-10~12는 이미 선택·진단에 사용된 `재사용 평가구간`으로 기록돼 있으며 깨끗한 최종
  시험기간이라고 부르지 않는다.
- #20·#39·#54·#72: 외부위험과 리드타임 변경값은 실제 권고와 분리된 병렬 시험값으로 다루고,
  현재 보고서에서는 운영 효과나 인과효과로 주장하지 않는다.
- #38·#52: 최신 재고상태 산출물은 `DORMANT`를 자동발주에서 걸러내고 평균·표준편차 바닥값
  적용 건수를 모두 0으로 기록한다. 다만 backend·DB의 동일 정책 여부는 별도 확인이 필요하다.

## 3. 최신 코멘트 결정표

| 이슈 | 최신 코멘트 | 반영 판단 | 근거와 남은 조건 |
|---|---|---|---|
| [#72 PP 선행관계](https://github.com/DGU-TeamLex/ai/issues/72) | 2026-08-16 | 미채택 | 24개월 `p=0.4617`, 90검정 보정 후 0건. PP 2개월 효과를 가중치·리드타임 근거로 사용하지 않음 |
| [#65 부호 보존 원장](https://github.com/DGU-TeamLex/ai/issues/65) | 2026-08-16 | 부분 반영 | 컬럼·사례·회귀계약 반영. 2018~2019까지 같은 grain으로 만드는 기간별 정합성 표는 추가 필요 |
| [#63 사건 시기](https://github.com/DGU-TeamLex/ai/issues/63) | 2026-08-10 | 반영 완료 | 2026 사건의 인과검증을 금지하고 FDA 사례도 설명적 스트레스 시험으로만 허용 |
| [#62 원자재 매핑 축](https://github.com/DGU-TeamLex/ai/issues/62) | 2026-08-16 | 부분 반영 | 대표품목→로컬→재고키, 충돌 격리 반영. 사용량 가중 커버리지·오탐 검토는 계속 필요 |
| [#58 평가기간](https://github.com/DGU-TeamLex/ai/issues/58) | 2026-08-16 | 부분 반영 | 재사용 평가구간 표기와 공동지표 반영. 동일 실제 초기재고와 새 미사용 기간 필요 |
| [#57 기관 prefix](https://github.com/DGU-TeamLex/ai/issues/57) | 2026-08-16 | 미채택 | 개선 0.0598%p, fold 불일치·해석 위험으로 사용하지 않음 |
| [#56 품목 중요도](https://github.com/DGU-TeamLex/ai/issues/56) | 2026-08-16 | 부분 반영 | 대표품목 기준 `사용량 등급`으로 수정. VED 임상 중요도는 담당자 승인 대기 |
| [#54 단일 재고정책](https://github.com/DGU-TeamLex/ai/issues/54) | 2026-08-16 | 외부 확인 대기 | 보고서 수식은 정기검토 `(R,S)`. AI/backend fixture parity, DB 정책버전·migration·rollback 필요 |
| [#52 수요 바닥값](https://github.com/DGU-TeamLex/ai/issues/52) | 2026-08-16 | 부분 반영 | 최신 AI 산출물은 바닥값 0건, DORMANT 차단. DB와 하류 결과의 같은 계약 여부 확인 필요 |
| [#44 안전재고 pooling](https://github.com/DGU-TeamLex/ai/issues/44) | 2026-08-16 | 부분 반영 | 고유 월수·개별 계열 CV 계산 수정. 새 holdout의 품절·충족률·평균재고로 최종 선택 필요 |
| [#43 약성분 분류](https://github.com/DGU-TeamLex/ai/issues/43) | 2026-08-16 | 부분 반영 | 대표품목 축 분석은 반영. 제품명·성분·제형·함량·제조사 exact 검증과 source hash 필요 |
| [#41 브랜치 거버넌스](https://github.com/DGU-TeamLex/ai/issues/41) | 2026-08-16 | 부분 반영 | 본 작업은 feat→dev만 사용. branch protection·필수 review는 저장소 관리자 설정 |
| [#39 리드타임](https://github.com/DGU-TeamLex/ai/issues/39) | 2026-08-16 | 부분 반영 | 계약 납기와 실제 입고일을 구분. 30일은 민감도 기준일 뿐 자동승격값이 아님 |
| [#38 0재고 사유](https://github.com/DGU-TeamLex/ai/issues/38) | 2026-08-10 | 부분 반영 | AI의 reason→action 차단 반영. 공용 가용재고 builder와 DB/frontend 계약 필요 |
| [#34 과거자료 적재](https://github.com/DGU-TeamLex/ai/issues/34) | 2026-08-10 | 반영 완료 | 과거 전용 품목을 조용히 누락하지 않고 미연결 건수·사용량·사례를 보고 |
| [#28 예측 handoff](https://github.com/DGU-TeamLex/ai/issues/28) | 2026-08-10 | 반영 완료 | 행별 백테스트와 세그먼트 산출물 존재. 소비자의 DB join율·checksum 회신은 별도 범위 |
| [#23 SS/ROP·발주](https://github.com/DGU-TeamLex/ai/issues/23) | 2026-08-10 | 부분 반영 | 연속 ROP 단독식 대신 정기검토 목표재고·재고포지션을 보고서에 사용. backend parity 필요 |
| [#22 뉴스 수집](https://github.com/DGU-TeamLex/ai/issues/22) | 2026-08-16 | 반영 완료 | 최신 코멘트 이후 세 경계조건 코드·테스트 반영, 2026-08-17 CSV 재생도 뉴스점수 110,256행 생성 |
| [#21 2026 사건연구](https://github.com/DGU-TeamLex/ai/issues/21) | 2026-08-10 | 외부 확인 대기 | 2026 원장이 오기 전 인과효과를 평가하지 않음 |
| [#20 위험–재고 연결](https://github.com/DGU-TeamLex/ai/issues/20) | 2026-08-16 | 부분 반영 | 신호는 계산하되 자동발주 변경 차단. 신호별 제거실험과 새 미사용 기간 필요 |
| [#19 위험 전파사슬](https://github.com/DGU-TeamLex/ai/issues/19) | 2026-08-10 | 부분 반영 | end-to-end 골격과 산출물 존재. `PASS=0, REVIEW=4,647, BLOCKED=322`라 운영 완료 아님 |
| [#16 미분류 잔류](https://github.com/DGU-TeamLex/ai/issues/16) | 2026-08-10 | 부분 반영 | 미분류 강제승격 차단. 검토 SLA와 수요량 가중 coverage 필요 |
| [#15 품목 정규화](https://github.com/DGU-TeamLex/ai/issues/15) | 2026-08-10 | 반영 완료 | 대표품목·후보/승인 분리·과거 적용 경로 존재 |
| [#14 규격 토큰](https://github.com/DGU-TeamLex/ai/issues/14) | 2026-08-10 | 반영 완료 | 3cc/5cc와 거즈 규격 분리 회귀사례 유지 |
| [#12 질병–품목 관계](https://github.com/DGU-TeamLex/ai/issues/12) | 2026-08-10 | 외부 확인 대기 | 가장 큰 미완료 데이터 축. 임상 근거·reviewer가 있는 승인 관계표 필요 |
| [#11 품목–원자재](https://github.com/DGU-TeamLex/ai/issues/11) | 2026-08-10 | 부분 반영 | 의약품 성분 경로와 소모품 proxy 존재. family generic과 exact BOM의 근거 확대 필요 |
| [#10 대용량 재현성](https://github.com/DGU-TeamLex/ai/issues/10) | 2026-08-10 | 부분 반영 | 전처리는 동작. 입력·출력 hash, commit, 자원사용량을 한 run manifest로 자동 고정해야 함 |
| [#9 실제 예측모형](https://github.com/DGU-TeamLex/ai/issues/9) | 2026-08-10 | 반영 완료 | LightGBM·시간순 평가·간헐수요·handoff가 존재. 운영 평가는 #58 조건을 따름 |
| [#8 raw_stock 전환](https://github.com/DGU-TeamLex/ai/issues/8) | 2026-08-10 | 반영 완료 | raw_stock이 정본 입력이며 금지된 옛 경로 의존을 제거함 |
| [#5 통합테스트 CI](https://github.com/DGU-TeamLex/ai/issues/5) | 2026-08-10 | 부분 반영 | dev용 오프라인 CI 존재. required check·review 규칙은 관리자 설정 필요 |
| [#4 원자재 가격](https://github.com/DGU-TeamLex/ai/issues/4) | 2026-08-10 | 부분 반영 | CSV/API adapter와 점수 생성은 동작. 직접 series·라이선스·staleness 관리는 계속 필요 |
| [#2 뉴스 API](https://github.com/DGU-TeamLex/ai/issues/2) | 2026-08-10 | 반영 완료 | provider·cache 경로와 합성 fallback 차단. 과거 backfill coverage는 운영 데이터 과제 |

## 4. 심사·제출 전에 반드시 남겨야 하는 미완료

1. **새 독립 평가기간**: 2025-10~12를 더 이상 최종 미사용 시험기간이라고 부르지 않는다.
2. **정책 정본 일치**: AI와 backend가 같은 입력에서 같은 목표재고·발주량을 내는 fixture와 DB
   migration/rollback 보고가 필요하다.
3. **사람 승인 데이터**: 질병–품목, VED, exact BOM은 임상·조달 담당자의 근거와 승인자가 필요하다.
4. **실제 리드타임·비용**: 계약 납기 대신 주문일–실입고일, 보유·폐기·부족·긴급구매 비용이 필요하다.
5. **실행 manifest**: 입력 byte/SHA-256, commit, 설정, 기간, 행 수, 출력 hash와 실행시간을 한 파일에
   고정해야 한다.

## 5. Git 반영 원칙

문서 수정은 `feat/report-storyline-and-data-examples`에서 수행하고 PR 대상은 `dev`로 제한한다.
feat 브랜치는 검토·머지 전까지 유지하고, `dev` 병합 뒤 삭제한다. `main` 동기화는 이 작업의
범위가 아니며 사용자 별도 승인 없이 수행하지 않는다.
