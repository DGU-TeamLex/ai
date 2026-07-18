# AI 운영 런북 — 처음부터 끝까지 (에이전트 실행용)

이 문서는 **AI 에이전트(Claude 등)가 사람 개입 없이 파이프라인을 처음부터 끝까지 실행**하기 위한 절차다.
사람 설명서는 `METHOD.md`, 여기는 "그대로 따라 실행하는 스크립트"다.

- 작업 폴더: 이 번들 폴더(`item_material_pipeline/`)를 `$BUNDLE` 로 둔다.
- 목표: 입력 parquet/csv → 원재료·리스크·수요 메타코드 매핑 완성 → 미식별 큐를
  **발주흔적 우선순위로 반복 축소** → 목표 커버리지 도달 시 종료.
- 원칙: 규칙 기반이라 난수·과금 없음. 리서치 단계만 웹검색 에이전트를 병렬로 쓴다.
  근거 없는 추정은 반드시 티어(`family_basis`/`material_confidence`)로 남기고, 못 잡으면 정직하게 미상.

---

## STEP 0 — 전제 확인

```bash
export BUNDLE="$(pwd)"                 # item_material_pipeline 폴더에서 실행
export PIPE_DATA_DIR="$BUNDLE/data"
export PIPE_RESEARCH_DIR="$BUNDLE/research"
mkdir -p "$BUNDLE/research" "$BUNDLE/output_full"
python3 -c "import pandas, openpyxl" || pip install pandas openpyxl
```

입력 파일 경로를 `$INPUT` 에 둔다(예: `../item_grouped_verified_v1.parquet`).

---

## STEP 1 — 파이프라인 4단계 실행

```bash
./run_all.sh "$INPUT" "$BUNDLE/output_full"
```

산출물은 `output_full/`. 이후 반복 시에도 이 명령 하나면 4단계가 재실행된다.

---

## STEP 2 — 현재 커버리지 측정 (매 라운드 전후로)

```bash
python3 - <<'PY'
import csv, re
from collections import Counter
HANGUL=re.compile(r'[가-힣]')
p="output_full/item_material_event_mapping_full.csv"
rows=list(csv.DictReader(open(p,encoding="utf-8-sig")))
def num(r,k):
    try:return float(r.get(k) or 0)
    except:return 0.0
total=len(rows)
act=[r for r in rows if r["activity_scope"]!="one_off"]
mu_act=[r for r in act if r["raw_material_meta_code"]=="MATERIAL_UNSPECIFIED"]
konly=sum(1 for r in rows for c in r["raw_material_meta_code"].split(";") if HANGUL.search(c))
print("총",total,"| active",len(act))
print("active 재질미상:",len(mu_act),f"({len(mu_act)/len(act)*100:.1f}%)")
print("material_confidence:",dict(Counter(r["material_confidence"] for r in rows)))
print("메타코드 한글 잔존(0이어야 정상):",konly)
PY
```

**종료 판단**: `active 재질미상` 비율이 목표(예: 30% 이하) 이하이거나, 직전 라운드 대비
개선폭이 임계(예: 1.5%p) 미만이면 STEP 6(종료)로 간다. 아니면 STEP 3~5 확장 루프.

---

## STEP 3 — 미식별 큐에서 이번 라운드 대상 추출·분할

발주흔적(occurrence_count) 큰 `active` 미식별 상위 480개를 6배치로 나눈다(에이전트 6개용).

```bash
python3 - <<'PY'
import csv
q=[r for r in csv.DictReader(open("output_full/unresolved_priority_queue_full.csv",encoding="utf-8-sig"))
   if r["scope"]=="active"]
names=[r["representative_name"] for r in q[:480]]
per=80
for i in range(6):
    open(f"research/q_batch_{i}.txt","w").write("\n".join(names[i*per:(i+1)*per]))
print("분할 완료:", min(len(names),480), "개 →", "research/q_batch_0..5.txt")
PY
```

이미 여러 라운드를 돈 뒤라 `active` 큐 상위가 검사소모품·비의료·성분미공개로만 남으면,
이 라운드는 수확이 낮으니 STEP 6으로 종료한다.

---

## STEP 4 — 병렬 리서치 에이전트 6개 dispatch

`research/q_batch_0.txt` ~ `q_batch_5.txt` 각각에 대해 **웹검색 가능한 서브에이전트**를
6개 동시 실행한다. 각 에이전트에 아래 프롬프트를 그대로 준다(`{N}` 만 0~5로 치환):

> `research/q_batch_{N}.txt` 파일을 읽어라. 한 줄에 한국 의약품/의료물품명이 하나씩 있다.
> 각 줄마다 활성성분(영어 INN) 또는 품목 카테고리를 식별하라. 모르는 것은 식약처
> nedrug.mfds.go.kr, 약학정보원 health.kr, munyak.co.kr, doctornow 를 웹검색한다.
> 국내 제네릭은 명명패턴으로 추론 가능(`~사르탄`=ARB, `~디핀`=암로디핀, `~스타틴`=스타틴,
> `~프라졸`=PPI, `~글립틴`=DPP-4, `세파/세프`=세팔로스포린, `~플록사신`=퀴놀론,
> `메트포/글루코`=메트포르민, `생리식염`=SODIUM_CHLORIDE, `포도당주사`=GLUCOSE).
> 출력은 **입력 한 줄당 정확히 한 줄**, 파이프 구분, 서두·코드펜스 없이:
> ```
> 매칭키워드 | FAMILY_ID | 표시명 | 신뢰도 | 근거
> ```
> - 매칭키워드: 브랜드를 특정하는 최소 부분문자열(다른 품목과 오매칭 안 되게).
> - FAMILY_ID: 영어 UPPER_SNAKE_CASE INN. 복합제는 `성분1_성분2`.
>   검사소모품은 BLOOD_COLLECTION_TUBE/HEMOGLOBIN_TEST_STRIP/CLINICAL_CHEMISTRY_TEST/URINE_TEST_STRIP/LANCET 등,
>   기기는 ELECTRONIC_DEVICE_COMPONENT, 한방은 HANBANG_MIXED_HERBAL_EXTRACT,
>   비의료(사은품/잡화/의류)는 PROMO_MATERIAL/APPAREL_TEXTILE/HYGIENE_TISSUE/NON_INGREDIENT_SPEC.
>   특정 불가는 UNKNOWN_INGREDIENT.
> - 신뢰도: SEARCHED(웹검색 확인) / GENERAL(명명패턴·일반지식) / UNRESOLVED(실패).
> - 근거: 짧은 한글 근거 + URL(검색 시).
> 효율적으로: 명백한 제네릭은 검색 없이 패턴 추론, 애매한 브랜드만 검색.

각 에이전트 결과(파이프 표)를 그대로 `research/research_batch_<고유번호>.txt` 로 저장한다.
**주의**: 파일명이 겹치지 않게 라운드마다 번호를 이어붙인다(예: 1라운드 0~5, 2라운드 6~11, ...).
`consolidate_research.py` 는 `research_batch_*.txt` 를 전부 병합하므로 과거 라운드 결과도 누적된다.

---

## STEP 5 — 취합 → 사전 갱신 → 재실행

```bash
python3 scripts/consolidate_research.py     # research/research_batch_*.txt → data/brand_dict_extra.tsv
```

새로 생긴 한글 성분코드가 있으면(STEP 2에서 "한글 잔존 > 0") `data/ingredient_ko_en.tsv` 에
`한글<TAB>영어INN` 을 추가한다. 그 뒤 STEP 1(재실행) → STEP 2(재측정)로 돌아간다.

> 반복은 "STEP 1 → 2 → (종료조건 미달 시) 3 → 4 → 5 → 1" 사이클이다.
> 매 라운드 active 재질미상이 얼마나 줄었는지 STEP 2로 확인하고, 개선폭이 미미해지면 종료.

---

## STEP 6 — 종료·산출물 확인

```bash
ls -la output_full/
```

최종 확인 항목:
- `item_material_event_mapping_full.csv` — 최종 통합본(27컬럼, `material_confidence`·`activity_scope` 포함).
- `meta_code_glossary_full.xlsx` — 메타코드 사전(원재료/공급리스크/수요리스크 3시트).
- `unresolved_priority_queue_full.csv` — 다음 사람/다음 달을 위한 잔여 큐.
- STEP 2의 "메타코드 한글 잔존 = 0" 재확인.

종료 시 요약 보고: 이번 세션 라운드 수, active 재질미상 시작→종료 %, 추가된 brand_dict 키워드 수,
남은 미식별의 성격(성분미공개/검사소모품/비의료 비율).

---

## 부록 A — 반복 없이 "한 번만" 돌릴 때

큐 확장이 필요 없고 현재 사전으로 매핑만 새로 뽑으면 될 때는 STEP 0 → 1 → 2 → 6 만.

## 부록 B — 새 데이터(다음 달/새 기관)가 들어왔을 때

입력 parquet만 교체하고 STEP 1부터 동일하게. 기존 `data/`(사전)는 그대로 재사용되고,
새로 미식별된 것만 큐에 뜬다. 절차는 처음과 완전히 동일하다.

## 부록 C — 절대 하지 말 것

- 원본 정규화 산출물의 `verified_*`/`canonical_item_id` 를 덮어쓰지 말 것(이 파이프라인은 제안 컬럼만 추가).
- 근거 없이 미상을 억지로 채우지 말 것(티어를 남기고 정직하게 unresolved/unspecified).
- `data/*.tsv` 를 손으로 대량 편집하지 말 것(반드시 리서치→consolidate 경로로 갱신해 재현성 유지).
