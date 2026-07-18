# 물품 원재료·리스크 메타코드 파이프라인 — 방법 문서

대표품목 worklist와 구조화 속성을 병합한 입력(대표품목 101,546건)에 대해
**성분/원재료 → 공급망 리스크 → 수요 트리거**를 코드화하고, 미식별 품목을
발주흔적 우선순위 큐로 반복 확장하는 파이프라인이다.

혼자 돌리는 게 아니라 **여러 명이 나눠서 큐를 채우는 것**을 전제로 만들었다.
아래 "5. 미식별 큐 확장 루프"가 협업의 핵심이다.

---

## 1. 폴더 구조

```
item_material_pipeline/
├─ run_all.sh                  # 5단계 일괄 실행기
├─ METHOD.md                   # (이 문서)
├─ scripts/
│  ├─ build_family_candidates.py   # 1단계: 성분/브랜드 식별 + 미식별 큐 생성
│  ├─ build_supply_clusters.py     # 2단계: 공급 클러스터 부착
│  ├─ build_material_events.py     # 3단계: 원재료·리스크·수요 메타코드 매핑
│  ├─ build_meta_code_excel.py     # 4단계: 메타코드 사전 엑셀
│  ├─ build_parent_concepts.py     # 5단계: 예측 집계용 상위개념 후보
│  └─ consolidate_research.py      # 큐 확장: 리서치 결과 → brand_dict_extra.tsv
├─ data/
│  ├─ brand_dict_extra.tsv     # 브랜드→성분 사전(현재 697개 키워드) — 확장 대상
│  └─ ingredient_ko_en.tsv     # 한글성분명→영어INN 매핑(790개)
└─ output_full/               # 전체 101,546건 산출물(아래 4번 참고)
```

의존성: `python3`, `pandas`(parquet 변환용), `openpyxl`(엑셀 생성용).

---

## 2. 빠른 실행

```bash
# 권장: worklist와 구조화 속성 병합부터 전체/샘플 출력까지 실행
conda run -n teamlex python -m src.item_integrated_pipeline --with-excel --sample-size 1000
```

`run_all.sh`을 직접 실행할 때는 입력에 구조화 속성이 이미 병합되어 있어야 한다.

개별 단계로 돌릴 때는 `PIPE_DATA_DIR` 로 사전 폴더를 지정한다:

```bash
export PIPE_DATA_DIR="$PWD/data"
python3 scripts/build_family_candidates.py  <입력.csv> out/fam.csv out/queue.csv
python3 scripts/build_supply_clusters.py    out/fam.csv out/clusters.csv out/cluster_summary.md
python3 scripts/build_material_events.py     out/clusters.csv out/mapping.csv out/glossary.csv out/reverse_index.md
python3 scripts/build_meta_code_excel.py      out/glossary.csv out/mapping.csv out/glossary.xlsx
python3 scripts/build_parent_concepts.py      out/mapping.csv out/parents.csv out/parent_summary.csv out/parent_summary.md
```

같은 입력이면 결과는 완전히 재현된다(규칙 기반, 난수·API 없음, 수 초 내 완료).

---

## 3. 데이터 흐름 (규칙 기반, 근거-티어 방식)

```
parquet ──▶ [1] family 분류 ──▶ [2] 공급 클러스터 ──▶ [3] 원재료/리스크/수요 코드 ──▶ [4] 엑셀 ──▶ [5] 상위개념
                    │
                    └─▶ 미식별 큐(unresolved_priority_queue) ──▶ (사람이 리서치) ──▶ consolidate ──▶ 사전 갱신 ──▶ 재실행
```

핵심 설계 원칙:

- **근거 티어(family_basis)**: 무엇을 근거로 분류했는지 항상 기록해 신뢰도를 구분한다.
  `official_standard_table`(공식 기준표) > `name_literal_parenthetical`/`name_literal_substring`(상품명에 성분 직접 명시) >
  `web_search_2026_07_15`(웹검색 확인) > `general_knowledge_unverified`(명명패턴/일반지식, 미검증) >
  `functional_keyword`/`non_material_category`(기능/비물질 분류) > `unresolved`(미식별).
- **성분 특정 = 원재료 특정**: 원재료는 상품명에서 직접 읽는 게 아니라 "이게 무슨 품목인가(family)"에서 유도된다.
  성분만 식별되면(브랜드사전·웹검색·괄호추출) 클러스터 등록 없이도 그 성분코드로 승격돼 재질미상에서 빠진다.
- **한글→영어 정규화**: 상품명 괄호에서 추출된 한글 성분명은 `ingredient_ko_en.tsv`로 전부 영어 INN 코드화한다.
- **대분류 폴백**: 성분을 못 잡아도 원본 대분류(MED_ORAL 등)가 있으면 개략 원재료를 배정(`material_confidence=group_coarse`).
- **발주흔적 스코프**: `usage_sum`은 결손이 많아 신뢰 못 함. `occurrence_count`/`institution_count`로 활동성을 판정한다.

---

## 4. 출력 컬럼 사전 (`item_material_event_mapping_full.csv`)

| 컬럼 | 의미 |
|---|---|
| representative_item_id / representative_name | 대표품목 ID / 이름 |
| item_group_id_candidate | 원본 AI팀 대분류(MED_ORAL, MED_TOPICAL, LAB_REAGENT, DISINFECT, KM_EXTRACT, UNCLASSIFIED 등) |
| usage_sum / occurrence_count / institution_count | 사용량 합 / 발주흔적 등장수 / 사용 기관수 |
| item_family_id_suggested | 성분/품목 코드(영어) — 예: ATORVASTATIN, SODIUM_CHLORIDE |
| standard_family_name_suggested | 위 코드의 한글 표시명 |
| **family_basis** | 분류 근거 티어(위 3번) — 신뢰도 판단용 |
| family_source / family_resolution_status | 실제 선택 출처 / 구조화·이름 규칙 합의 또는 충돌 상태 |
| name_rule_* / family_conflict_* | 채택하지 않은 이름 규칙 대안과 충돌 감사 정보 |
| supply_cluster_id / name | 굵은 운영 카테고리(해열진통, 항생제, 검사진단 등 27종 + 기타) |
| raw_material_suggested / raw_material_evidence | 승인 전 원재료 제안 + 근거(URL 등) |
| **raw_material_meta_code** | ①원재료 정체(무엇으로 만들었나) — 예: POLYPROPYLENE_PP, PHARMA_API_GENERIC |
| **raw_material_risk_meta_code** | ②공급 리스크(왜 위험한가) — 예: API_IMPORT_DEPENDENCY_CN_IN, MIDEAST_NAPHTHA_PETROCHEM_SHOCK |
| **demand_risk_meta_code** | ③수요 트리거(어떤 뉴스에 소비가 튀나) — 예: INFECTIOUS_DISEASE_OUTBREAK, SEASONAL_CLIMATE_PATTERN |
| supply_risk_events / demand_risk_events | 위 두 코드의 서술 근거 |
| **material_confidence** | `identified`(성분 정밀식별) / `group_coarse`(대분류 개략) / `unspecified`(미상) |
| **material_evidence_tier** | cluster/family/subtype/복합세트 등 원자재 후보 생성 수준 |
| **activity_scope** | `active_high`(발주 20회+ 또는 5기관+) / `active_low` / `one_off`(1회·단일기관=예측대상 제외 후보) |
| **material_review_status** | 항상 `needs_review`; 승인 전 운영 리스크 점수 사용 금지 |

**교차 필터 사용법**: 재고예측 대상은 `activity_scope != one_off` 로 좁히고, 그 안에서
`material_confidence` 로 신뢰도를 구분한다. 개략추정(group_coarse)을 정밀값인 척 섞지 않도록 설계했다.

메타코드가 3축으로 분리돼 있다: **①원재료(정체) · ②공급리스크(왜) · ③수요트리거(언제)**.
각 코드의 한글 설명·공급단계는 `meta_code_glossary_full.xlsx`(시트 3개) 참고.

---

## 5. 미식별 큐 확장 루프 ★ (협업 핵심)

파이프라인을 돌릴 때마다 `unresolved_priority_queue_full.csv`가 자동 생성된다.
**규칙/사전으로 못 잡은 품목을 발주흔적 큰 순으로 정렬**한 파일이다.

큐 컬럼: `scope`(active/one_off), `occurrence_count`, `institution_count`, `usage_sum`,
`representative_item_id`, `representative_name`, `item_group_id_candidate`.

### 확장 절차 (반복)

1. **큐 상위 확보**: `scope == active` 인 것만, `occurrence_count` 큰 순으로 상위 N개(예: 400~500개)를 뽑는다.
   `one_off`(1회·단일기관)는 예측 실익이 낮으니 건너뛴다.
2. **분담 리서치**: 뽑은 목록을 사람 수만큼 나눠, 각자 상품명 → 성분(영어 INN)을 조사한다.
   근거는 식약처 `nedrug.mfds.go.kr`, 약학정보원 `health.kr`, `munyak.co.kr`, `doctornow` 등.
   국내 제네릭은 명명 패턴으로 상당수 추론 가능(`~사르탄`=ARB, `~디핀`=암로디핀, `~스타틴`=스타틴,
   `~프라졸`=PPI, `~글립틴`=DPP-4, `세파/세프`=세팔로스포린, `메트포/글루코`=메트포르민 등).
3. **결과 파일 작성**: 각자 `research_batch_<n>.txt` 로 저장한다. **한 줄 = 한 품목**, 파이프 구분:
   ```
   매칭키워드 | FAMILY_ID | 표시명 | 신뢰도 | 근거
   ```
   - 매칭키워드: 브랜드를 특정하는 최소 부분문자열(예: `크라목신`). 다른 품목과 오매칭 안 되게.
   - FAMILY_ID: 영어 UPPER_SNAKE_CASE INN(예: `AMOXICILLIN_CLAVULANATE`). 복합제는 `성분1_성분2`.
   - 신뢰도: `SEARCHED`(웹검색 확인) / `GENERAL`(명명패턴·일반지식) / `UNRESOLVED`(특정 실패).
   - 근거: 짧은 한글 근거 + URL(검색 시).
4. **취합·사전 갱신**: 모든 `research_batch_*.txt`를 한 폴더에 모으고:
   ```bash
   export PIPE_RESEARCH_DIR=<research_batch 들이 있는 폴더>
   export PIPE_DATA_DIR=<번들의 data 폴더>
   python3 scripts/consolidate_research.py
   ```
   → `data/brand_dict_extra.tsv` 가 갱신된다(키워드 중복 시 SEARCHED 우선, 길이 내림차순 매칭).
5. **재실행**: `./run_all.sh` 다시 돌리면 추가한 브랜드가 자동으로 큐에서 빠지고 재질미상이 줄어든다.

새 달 데이터가 들어오든, 새 기관이 추가되든, 완전히 새 품목이 들어오든 **절차는 동일**하다.
"완벽한 사전을 한 번에" 만드는 게 아니라, **뭘 모르는지 항상 발주흔적 순으로 드러나게** 만들어둔 구조다.

### 규칙 vs 사전 — 어디에 넣을지

- **브랜드 하나**(예: 특정 제약사 제품) → `data/brand_dict_extra.tsv` 에 키워드 추가(위 절차).
- **성분 부분문자열**(예: 상품명에 `메트포르민`이 그대로 들어가는 다수) 또는 **기능 키워드**(예: `생리식염주사액`)
  → `scripts/build_family_candidates.py` 의 `SUBSTRING_INGREDIENTS` / `FUNCTIONAL_KEYWORDS` 리스트에 추가.
- **새 한글 성분코드**가 생기면 → `data/ingredient_ko_en.tsv` 에 `한글<TAB>영어` 추가(안 하면 코드가 한글로 남음).

---

## 6. 현재 커버리지 (전체 101,546건 기준)

- **family 식별**: 미식별 basis 44,004건(43.3%). 단, 사용량·발주흔적 가중으로 보면 상위는 대부분 식별됨.
- **활동성 스코프**: active_high 34,030 / active_low 50,574 / one_off 16,942(예측대상 제외 후보).
- **원재료(material_confidence)**: identified 57,445 · group_coarse 6,263 · unspecified(재질미상) 37,838.
  → 재질미상의 대부분은 원본 `UNCLASSIFIED` 롱테일이며 발주흔적이 낮다.
- **사전 규모**: brand_dict_extra 697 키워드, ingredient_ko_en 790 매핑.
- **메타코드**: 원재료 694 · 공급리스크 16 · 수요리스크 8.

남은 미식별은 성격이 (1) 공개 DB에도 성분 없는 소수, (2) 검사소모품·기구, (3) 비의료 잡화 위주로 바뀌었다.
브랜드사전으로 잡을 수 있는 유명 제네릭은 상당 부분 소진됐고, 라운드당 수확은 점차 감소한다.

---

## 7. 원칙(반드시 지킬 것)

- 원본 AI팀 산출물의 `verified_*` / `canonical_item_id` 필드는 **절대 덮어쓰지 않는다**. 이 파이프라인은
  전부 `*_suggested` / `*_meta_code` 등 **후보/제안 컬럼**만 추가한다.
- 근거 없는 추정은 반드시 `family_basis`/`material_confidence`로 티어를 남긴다(개략값을 정밀값인 척 금지).
- 성분 미확인은 정직하게 `unresolved` / `unspecified` 로 둔다(억지로 채우지 않는다).
