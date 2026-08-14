# 공급위험·안전재고 오류 분류 품질 게이트

## 1. 목적

`src/module_c/supply_risk_anomaly_filter.py`는 공급위험 메타코드, 기준레벨, 정책계수,
안전재고와 ROP의 정합성을 검사하고 각 행을 다음 상태로 분류한다.

```text
PASS   운영 입력으로 사용 가능
REVIEW 자동 수정하지 않고 검토 큐로 이동
BLOCK  재고·발주 계산에서 제외하고 격리
```

입력 행은 삭제하지 않는다. 원본과 파생값, 오류코드, 관측값, 기대값, 수정 조치를 모두
분류·이슈 파일에 남긴다. 배치 자동 반영은 모든 행이 PASS이고 데이터셋 오류도 없을 때만
`batch_release_allowed=true`가 된다.

## 2. 검사 순서

```text
입력 계약 검사
-> 현재 정책으로 baseline 공급레벨 재도출
-> 수요/사건/공급 축 분리
-> 저장 레벨·CRITICAL 근거 검증
-> z·리드타임 배수 검증
-> 일 단위 SS/ROP 재계산 비교
-> 정책 중복 적용 검사
-> 코드별 복수 레벨·CRITICAL 집중도 데이터셋 검사
-> PASS / REVIEW / BLOCK 분리
```

## 3. 탐지 오류

| 오류코드 | 기본 처리 | 탐지 내용 |
|---|---|---|
| `SR001` | BLOCK | 저장 레벨과 현재 코드 정책 불일치 |
| `SR002` | BLOCK | 수요축 코드가 공급레벨을 상향 |
| `SR003` | BLOCK | 정책에 없는 메타코드 |
| `SR004` | BLOCK | 승인 근거 없는 CRITICAL |
| `SR005` | REVIEW | 정책 버전 누락 또는 노후화 |
| `SR006` | BLOCK | 레벨과 z 값 불일치 |
| `SR007` | BLOCK | 레벨과 리드타임 배수 불일치 |
| `SR008` | BLOCK | SS 재계산값 불일치 |
| `SR009` | BLOCK | ROP 재계산값 불일치 |
| `SR010` | BLOCK | 음수·무한대·비수치 입력 |
| `SR011` | REVIEW | SS/ROP 재검증 입력 부족 |
| `SR012` | BLOCK | 사건레벨이 기준레벨을 덮어씀 |
| `SR013` | BLOCK | 같은 코드가 여러 기준레벨로 저장됨 |
| `SR014` | REVIEW | CRITICAL 행 비중 과다 |
| `SR015` | REVIEW | CRITICAL 안전재고 집중도 과다 |
| `SR016` | BLOCK | 두 재고정책의 안전재고 중복 적용 |
| `SR017` | REVIEW | 일 단위 계산 계약 누락·오류 |
| `SR018` | REVIEW | 구 코드 alias 사용 |
| `SR019` | REVIEW | 사건·수요 코드가 기준 공급코드에 포함됨 |
| `SR020` | BLOCK | 허용되지 않은 레벨 문자열 |
| `SR021` | BLOCK | 운영 필수 컬럼 또는 값 누락 |

전체 코드명, 심각도와 수정 조치는
`data/mapping/supply_risk_anomaly_rules.json`에서 관리한다. 데이터셋 집중도 임계값은
초기 검토값이며 실제 재적재 결과로 보정한다.

## 4. 입력 모드

### 후보 모드

정규화·분류 후보를 검사한다. 레벨이나 SS/ROP가 아직 없어도 코드 축, 구 네이밍,
미매핑 여부를 분류한다.

```bash
python -m src.module_c.supply_risk_anomaly_filter \
  --input candidate.csv \
  --output-dir outputs/supply_quality \
  --key-column local_item_key \
  --code-column raw_material_risk_meta_code
```

### 운영 모드

DB 재적재 직전 또는 적재 결과를 검사한다. 다음 계약을 필수로 요구한다.

```text
supply_risk_meta_code
supply_risk_level
supply_risk_policy_version
z_value 또는 z_used
lead_time_multiplier 또는 lt_mult
```

SS/ROP가 있으면 다음 입력과 단위도 필요하다.

```text
mean_daily_usage
daily_demand_stddev
lead_time_days
demand_rate_unit=per_day
demand_stddev_unit=per_sqrt_day
```

```bash
python -m src.module_c.supply_risk_anomaly_filter \
  --input inventory_export.csv \
  --output-dir outputs/inventory_supply_quality \
  --key-column inventory_id \
  --code-column supply_risk_meta_code \
  --operational-mode \
  --require-release
```

`--require-release`는 REVIEW, BLOCK 또는 데이터셋 이슈가 하나라도 있으면 산출물을 모두
저장한 뒤 종료코드 2를 반환한다. backend 배치와 CI는 이 종료코드에서 DB 반영을 중단한다.

## 5. 산출물

| 파일 | 용도 |
|---|---|
| `supply_risk_quality_classified.csv` | 원본과 파생 레벨, 최종 상태, 오류코드 |
| `supply_risk_quality_issues.csv` | 오류 1건당 한 행인 상세 감사 로그 |
| `supply_risk_quality_passed.csv` | 자동 통과 행 |
| `supply_risk_quality_review.csv` | 검토 대상 행 |
| `supply_risk_quality_quarantine.csv` | 운영 반영 차단 행 |
| `supply_risk_quality_sample_1000.csv` | 대표품목 다양성·상태·오류 유형을 반영한 검토 샘플 |
| `supply_risk_quality_report.json` | 상태·오류별 건수와 배치 반영 가능 여부 |

`python -m src.module_c.pipeline` 실행 시 같은 파일명이 `module_c_` 접두어로
`outputs/`에 자동 생성된다.
검토 샘플은 `data/sample/module_c_supply_risk_quality_sample_1000.csv`에 저장된다.

## 6. 현재 데이터 결과

승인 분류 4,969개 로컬 품목을 후보 모드로 검사한 결과:

```text
PASS:       0
REVIEW: 4,647
BLOCK:    322
```

| 오류 | 행수 | 원인 |
|---|---:|---|
| `SR019_NON_SUPPLY_CODE_IN_BASELINE` | 4,647 | 나프타 사건 코드가 기준 공급코드 필드에 존재 |
| `SR018_LEGACY_CODE_ALIAS_USED` | 1,213 | 구 API 수입의존 코드 사용, SR019와 중복 가능 |
| `SR003_UNMAPPED_META_CODE` | 322 | 구 국내과점 코드가 수액제·수액세트를 혼합 |

대표품목 다양성을 우선한 검토 샘플은 1,000행이며 고유 대표품목도 1,000개다. 샘플은
`BLOCK` 대표품목 76개, `REVIEW` 대표품목 924개를 포함한다. 전체 로컬 행은 샘플이 아니라
classified, review, quarantine 파일에서 확인한다.

따라서 현재 구 메타코드 필드를 그대로 backend 기준레벨 입력으로 쓰면 안 된다. 정본
`supply_risk_meta_code`와 `demand_trigger_meta_code`를 다시 생성한 뒤 품질 게이트를 재실행해
PASS 행만 적재해야 한다.

## 7. Backend 적용 원칙

1. 적재 전 운영 모드 품질 게이트를 실행한다.
2. `BLOCK` 행은 기존 inventory를 덮어쓰지 않고 격리 테이블에 저장한다.
3. `REVIEW` 행은 자동 발주·안전재고 변경을 금지한다.
4. `PASS` 행만 같은 `policy_version` 단위로 트랜잭션 적재한다.
5. `batch_release_allowed=false`면 전체 자동 배포를 중단한다.
6. 동적 뉴스·가격 사건은 `event_supply_risk_level`에 저장하고 baseline을 덮어쓰지 않는다.
7. 오류 수정 후 같은 입력으로 재실행해 이슈가 제거됐는지 확인한다.

규칙 파일 변경은 코드 리뷰와 정책 버전 증가 없이 직접 배포하지 않는다.
