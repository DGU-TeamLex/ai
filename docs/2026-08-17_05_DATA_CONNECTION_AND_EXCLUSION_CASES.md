# 데이터 연결·제외 실제 사례 부록

- 기준일: 2026-08-17
- 목적: 어떤 데이터가 왜 사용·제외·보류됐는지 실제 산출물 사례로 설명
- 개인정보 처리: 기관·로컬 품목 식별자는 SHA-256 앞 8자리로 가명화
- 원칙: `제외`는 원본 삭제가 아니라 특정 학습·위험전파·자동권고 단계에서 사용하지 않는다는 뜻

## 1. 한눈에 보는 처리 사유

| 상태 | 실제 사례 | 처리 | 이유 |
|---|---|---|---|
| 음수만 기록된 월 | 2025-07, 원장합 -89 | 모델수요 0, 감사값 보존 | 반품·정정을 사람의 음의 사용량으로 학습하지 않음 |
| 양수·음수 혼재 월 | 원장합 18, 양수합 19 | 모델수요 19, 음수 1 별도 보존 | 실제 양수 출고와 정정값을 분리 |
| 과거 이름 임시연결 | `#6878 314 012 bur` | 현재품목 학습에서 제외 | 이름만으로 같은 규격·제품이라 확정 불가 |
| 뉴스의 원자재는 탐지했으나 품목 미매핑 | 알루미늄 공장 화재 기사 | 기사 감사표에는 유지, 품목별 전파 제외 | 잘못된 품목에 위험을 붙이지 않음 |
| 외부위험 품질검사 미통과 | PASS 0, REVIEW 4,647, BLOCKED 322 | 실제 발주 반영 금지 | 매핑·근거·정책 품질이 운영 기준 미달 |
| 과거 수요전용 스냅샷의 외부열 0 | 15개 열 모두 비영 관측 0 | 외부모형 비교 제외 | `위험 0`이 아니라 그 실행의 입력 미연결 |

## 2. 음수 거래는 어떻게 처리했는가

원장은 부호를 보존하고, 학습값은 양수 정상출고만 합산한다.

```text
부호 보존 원장출고 = Σ normal_outbound
양수 모델수요      = Σ max(normal_outbound, 0)
음수 거래량        = Σ |min(normal_outbound, 0)|
```

| 유형 | 월 | 기관 표본ID | 품목 표본ID | 원장합 | 모델수요 | 음수 건수·양 | 쉬운 설명 |
|---|---|---|---|---:|---:|---:|---|
| 음수만 기록 | 2025-07 | H-cfb951b0 | H-188aa8e2 | -89 | 0 | 1건·89 | 사용량 -89가 아니라 반품·정정 89로 기록 |
| 양수·음수 혼재 | 2024-05 | H-8263f413 | H-8a2081f4 | 18 | 19 | 1건·1 | 양수 사용 19를 학습하고 조정 1을 감사 |
| 양수·음수 상계 | 2024-06 | H-b4e0dfa4 | H-f7c34b9a | 0 | 4 | 1건·4 | 원장합이 0이어도 실제 양수 출고 4는 유지 |

현재기간에서 음수가 들어간 월은 15개였다. 그중 음수만 기록된 월 1개, 양수·음수 혼재 월
14개다. 이 수치는 `scripts/analysis/report_data_examples.py`로 재추출했다.[^negative-source]

## 3. 과거품목 연결에서 제외한 사례

2018~2019 로컬품목 175,760개 중 146,757개는 현재 대표품목과 엄격히 연결됐다. 나머지
29,003개는 이름 기반 임시 표준키만 있어 현재품목의 학습이력으로 사용하지 않았다. 영향을 받은
월별 행은 143,736개, 양수 모델수요 합계는 12,402,768.2116이다.[^mapping-source]

| 과거품목 표본ID | 원문 품목명 | 연결방법 | 신뢰도 | 처리 | 제외 이유 |
|---|---|---|---:|---|---|
| H-4c26d731 | `#6878 314 012 bur` | 과거 이름 임시연결 | 0.50 | 학습 제외 | 현재 대표품목·규격과 엄격 일치하지 않음 |
| H-b218d7e4 | `#858 314 010 bur` | 과거 이름 임시연결 | 0.50 | 학습 제외 | 비슷한 이름만으로 같은 제품이라 확정 불가 |
| H-f9987eed | `#8878 314 012 bur` | 과거 이름 임시연결 | 0.50 | 학습 제외 | 규격 차이를 무시해 다른 품목을 합칠 위험 |

원본과 임시 키는 삭제하지 않았다. 공식 대응표가 들어오면 다시 연결할 수 있다.

## 4. 외부위험이 빠졌던 이유와 현재 재실행 사례

외부위험에는 네 단계가 있다.

```text
외부 원천 존재 → 위험점수 생성 → 품목·월 연결 → 품질검사 통과 → 모델 비교
```

이전 수요전용 스냅샷은 특성생성 시점에 점수파일이 연결되지 않아 뉴스 4개, 원자재 3개,
Module C 8개, 총 15개 열의 비영 관측이 모두 0이었다. 따라서 외부모형을 비교에서 제외했다.
이는 외부위험이 존재하지 않았다는 의미가 아니다.[^coverage-source]

2026-08-17 재실행에서는 큰 용량의 기존 로컬 데이터 범위 안에서 다음 중간 산출물이 생성됐다.

| 단계 | 생성 결과 | 해석 |
|---|---:|---|
| 뉴스 위험점수 | 110,256행 | 48개월 공통 공급위험과 품목별 위험을 생성 |
| 원자재 위험점수 | 316,986행 | 가격수익률·30일 변동성 기반 점수 생성 |
| 무역 위험점수 | 20,345행, 품목 2,297개 | 승인된 46개 원자재–HS 경로에서 생성 |
| Module C 위험점수 | 316,986행 | 뉴스·가격·무역 결합점수 생성 |
| Module C 품질판정 | PASS 0, REVIEW 4,647, BLOCKED 322 | 점수가 있어도 실제 권고에는 반영할 수 없음 |

이 표는 모델 성능 결과가 아니라 **입력 연결 단계가 살아났다는 중간 증거**다. 특성생성·학습·평가가
끝나기 전에는 WAPE 개선을 기입하지 않는다.[^external-run]

### 4.1 뉴스는 잡혔지만 품목에 연결하지 않은 실제 기사

다음 기사들은 `material=aluminum`으로 분류됐지만 승인된 재고품목 매핑을 찾지 못해 품목별
위험 전파에서 제외됐다.

- `Another fire breaks out at aluminum plant that supplies Ford`
- `Aluminum Plant Fire Disrupts Ford Supply Chain Again`
- `Europe trawls for alternative aluminum supply in face of Mozal shutdown`

기사를 삭제하거나 위험 0으로 바꾼 것이 아니다. 기사 점수 감사자료에는 남기고, 알루미늄과
관련 없는 의료품목에 임의로 전파하지 않은 것이다.[^external-log]

## 5. 제출용 제외사유 코드 제안

| 코드 | 한글 사유 | 자동 처리 | 재포함 조건 |
|---|---|---|---|
| `NEGATIVE_ONLY_MONTH` | 음수만 기록된 월 | 모델수요 0, 원장 감사 유지 | 원천기관이 거래 의미를 정정할 때 |
| `HISTORICAL_NAME_FALLBACK` | 과거 이름 임시연결 | 현재품목 학습 제외 | 공식 품목 대응표 승인 |
| `MATERIAL_ITEM_UNMAPPED` | 원자재–품목 미매핑 | 품목별 위험 전파 제외 | 승인 매핑·근거 추가 |
| `MAPPING_CONFLICT` | 하나의 품목에 복수 관계 충돌 | 격리·사람 검토 | 충돌 해소·승인자 기록 |
| `EXTERNAL_INPUT_UNAVAILABLE` | 실행 시 외부 입력 미연결 | 외부모형 비교 제외 | 파일 기간·행수·비영률 검사 통과 |
| `QUALITY_REVIEW` | 품질검사 검토 필요 | 병렬 시험만 허용 | 검토자가 근거 승인 |
| `QUALITY_BLOCKED` | 품질검사 차단 | 계산·권고 사용 금지 | 차단 사유 수정 후 재검사 |
| `REUSED_EVALUATION_SLICE` | 이미 본 평가기간 | 진단결과로만 보고 | 새 미사용 기간 확보 |

## 6. 각주

[^negative-source]: `data/processed/stock_monthly.parquet`에서
    `negative_normal_outbound_count > 0`을 필터링한 결과. 재현 명령은
    `.venv\\Scripts\\python.exe scripts/analysis/report_data_examples.py --sample-limit 5`다.
[^mapping-source]: `data/processed/stock_standard_item_mapping.parquet`,
    `outputs/stock_standard_item_mapping_report.json`, `outputs/stock_forecast_data_quality.json`.
[^coverage-source]: `outputs/experiment_snapshots/demand_only_20260817/external_signal_coverage_report.json`.
    수요전용 실행의 train 1,829,647행에서 15개 외부신호의 비영 관측이 모두 0이었다.
[^external-run]: `outputs/background_external_risk_experiment_active_20260817.stdout.log`의
    `news-risk-scoring`, `commodity-risk-scoring`, `trade-risk-scoring`, `module-c-risk-scoring` 완료 기록.
[^external-log]: `outputs/background_external_risk_experiment_active_20260817.stderr.log`의
    `No stock item mapping found for material=aluminum` 경고. 경고는 단계 실패가 아니라 매핑 없는
    기사별 제외 기록이다.
