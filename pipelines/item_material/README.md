# wep-stock 물품 원재료·리스크 메타코드 정규화 파이프라인

> WeP-Stock 통합본: 기존 `94b5663` 규칙과 canonical 저장소
> `wep-stock-item-material-pipeline@74d3982`를 비교한 v2.1이다. 이 디렉터리의
> 출력은 전부 **승인 전 후보**이며, `stock_item_material_mapping.csv`에 별도 승인된
> 행만 운영 뉴스·원자재 점수에 사용한다. 로컬 통합 차이는 `UPSTREAM.md`를 참고한다.

대표품목 worklist와 구조화 속성을 병합한 입력(대표품목 101,546건)에 대해
**성분/원재료 → 공급망 리스크 → 수요 트리거**를 3축 메타코드로 부여하고,
미식별 품목을 발주흔적 우선순위 큐로 반복 확장하는 규칙 기반 파이프라인.

## 문서

- **[AI_RUNBOOK.md](AI_RUNBOOK.md)** — AI 에이전트가 처음부터 끝까지 실행하는 런북(큐 확장 병렬 리서치 포함). **여러 명/여러 AI가 나눠 돌릴 때 이 문서대로.**
- **[METHOD.md](METHOD.md)** — 사람용 레퍼런스(데이터 흐름, 출력 컬럼 사전, 설계 원칙, 커버리지).

## 빠른 실행

```bash
export PIPE_DATA_DIR="$PWD/data"
./run_all.sh <입력.parquet 또는 .csv> ./output_full
```

의존성: `python3`, `pandas`. `openpyxl`은 선택적인 xlsx 생성에만 필요하다. 규칙 기반이라
같은 입력이면 결과 100% 재현(난수·API·과금 없음).

## 구성

```
scripts/     5단계 파이프라인 + 리서치 취합 스크립트
data/        brand_dict_extra.tsv(브랜드→성분 사전) · ingredient_ko_en.tsv(한→영 INN)
output_full/ 메타코드 사전(xlsx/csv) + 요약(대용량 CSV 산출물은 .gitignore, run_all.sh로 재생성)
run_all.sh   일괄 실행기
```

## 핵심 설계

- **선택 우선순위**: 검증 근거 > 공식표·명시 문맥 > 구조화 family > 이름 규칙. 충돌은 삭제하지 않고 보존.
- **근거 티어(family_basis)**: 공식표 > 상품명 직접명시 > 웹검색 > 일반지식 > 미상 — 신뢰도를 항상 구분.
- **3축 메타코드**: ①원재료 정체 · ②공급 리스크(왜 위험) · ③수요 트리거(언제 소비 급증).
- **한글→영어 정규화**: 성분명 코드 전부 영어 INN(한글 잔존 0).
- **대분류 폴백**: 성분 미식별도 원본 대분류로 개략 원재료 배정(`material_confidence`로 구분).
- **발주흔적 스코프**: `usage_sum` 결손이 많아 `occurrence_count`/`institution_count`로 예측대상 판정(`activity_scope`).
- **상위개념 분리**: parent concept은 예측 집계 후보이며 품목 identity를 덮어쓰지 않음.

## 원칙

원본 정규화 산출물의 `verified_*`/`canonical_item_id`는 절대 덮어쓰지 않음(제안 컬럼만 추가).
근거 없는 미상은 억지로 채우지 않고 티어를 남김.
