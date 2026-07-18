# WeP-Stock 원자재-사용제품 매칭 설계서

작성일: 2026-07-06
대상 프로젝트: WeP-Stock AI Service
문서 목적: 뉴스/원자재 리스크를 의료기기 품목별 재고 리스크로 연결하기 위한 **원자재-사용제품 매칭 방식**, **LLM 활용 방법**, **평가 기준**, **백엔드 전달 방식** 정리

---

## 1. 왜 원자재-사용제품 매칭이 필요한가

뉴스 리스크를 의료기기 재고 리스크로 바꾸려면 다음 연결이 필요하다.

```text
뉴스 이벤트
→ 영향을 받은 국가 / 산업 / 원자재
→ 해당 원자재가 들어가는 의료기기 품목
→ 품목별 공급 리스크 점수
→ 안전재고 / 권장재고 / 발주권고
```

예를 들어 뉴스가 아래처럼 나올 수 있다.

```text
"Middle East conflict disrupts naphtha supply"
"PVC resin prices surge due to supply shortage"
"Polypropylene production halted after factory fire"
```

이 뉴스들은 "주사기", "수액세트", "카테터"를 직접 언급하지 않을 수 있다.
따라서 단순히 품목명 키워드를 찾으면 관련 리스크를 놓칠 수 있다.

그래서 다음과 같은 중간 매핑 테이블이 필요하다.

```text
의료기기 품목
→ 품목군
→ 주요 원자재
→ 상위 원자재 / 공급망 원천
→ 국가 / 지역 exposure
```

---

## 2. 결론: LLM 단독 처리보다 Hybrid 방식 추천

원자재-사용제품 매칭은 LLM만으로 처리하면 위험하다.

LLM은 다음에 강하다.

- 품목명에서 사용 가능한 원자재 후보 추론
- 한국어/영어/약어/동의어 정규화
- 뉴스 기사에서 원자재, 국가, 이벤트 유형 추출
- "PVC resin", "polyvinyl chloride", "medical-grade plastic" 같은 표현 통합

하지만 LLM은 다음에 약하다.

- 실제 특정 제품의 정확한 BOM 확인
- 의료기기 등급, 제조사별 재질 차이 반영
- 잘 모르는 품목에 대해 그럴듯하게 추정하는 문제
- 근거 없는 매핑 생성
- 매번 결과가 조금씩 달라지는 비결정성

따라서 권장 구조는 다음과 같다.

```text
규칙 기반 seed table
+ LLM 후보 생성
+ 사람 검수
+ evaluation set 기준 평가
+ versioned mapping table 운영
```

즉, LLM은 **최종 정답 생성기**가 아니라 **후보 생성기 + 보조 판별기**로 사용한다.

---

## 3. 전체 처리 흐름

```text
1. 의료기기 품목 목록 준비
   - MED_DEVICE_5
   - 품목명
   - 품목군
   - 사용량 규모
   - 중요도

2. 원자재 master 구축
   - polypropylene
   - polyethylene
   - PVC
   - silicone
   - polyurethane
   - latex
   - stainless steel
   - glass
   - naphtha 등 upstream material

3. 초기 seed 매핑 작성
   - 사람이 주요 품목군 중심으로 작성
   - 사용량 상위 20% 품목 우선

4. LLM으로 후보 원자재 생성
   - 품목명, 품목군, 설명을 입력
   - 후보 원자재, 사용 부위, 신뢰도, 근거 반환

5. Rule validation
   - 원자재 master에 없는 값 제거
   - 금지 조합 제거
   - 품목군별 허용 원자재 검사
   - confidence threshold 적용

6. Human review
   - high impact 품목은 필수 검수
   - low confidence mapping 검수
   - conflict mapping 검수

7. Versioned mapping table 저장
   - CSV 또는 DB table
   - mapping_version 관리
   - review_status 관리

8. 뉴스/원자재 리스크와 연결
   - article affected_materials 추출
   - material_id 기준으로 품목 연결
   - product risk score 계산

9. 백엔드 전달
   - AI batch 결과로 precomputed output 생성
   - FastAPI에서 mapping/risk 결과 조회
```

---

## 4. 데이터 모델 설계

### 4.1 product_master

의료기기 품목 기준 master table이다.

```csv
standard_code,item_name,item_group,description,usage_rank,criticality,is_active
A001,주사기,syringe,일회용 주사기,1,0.9,true
B001,수액세트,infusion_set,수액 투여용 세트,2,0.95,true
C001,카테터,catheter,체내 삽입용 튜브,3,0.9,true
D001,의료폐기물용기,medical_waste_container,의료폐기물 보관 용기,4,0.7,true
```

필드 설명:

| 필드 | 설명 |
|---|---|
| `standard_code` | 내부 표준 품목 코드 또는 MED_DEVICE_5 |
| `item_name` | 품목명 |
| `item_group` | 품목군 |
| `description` | 품목 설명 |
| `usage_rank` | 사용량 기준 순위 |
| `criticality` | 품목 중요도, 0~1 |
| `is_active` | 현재 운영 대상 여부 |

---

### 4.2 material_master

원자재 master table이다.

```csv
material_id,material_name_ko,material_name_en,aliases,material_type,parent_material_id,is_active
MAT_PP,폴리프로필렌,polypropylene,"PP;polypropylene resin;medical-grade PP",plastic,MAT_NAPHTHA,true
MAT_PE,폴리에틸렌,polyethylene,"PE;HDPE;LDPE;polyethylene resin",plastic,MAT_NAPHTHA,true
MAT_PVC,폴리염화비닐,PVC,"polyvinyl chloride;PVC resin",plastic,MAT_NAPHTHA,true
MAT_SILICONE,실리콘,silicone,"medical silicone;silicone rubber",polymer,,true
MAT_PU,폴리우레탄,polyurethane,"PU;urethane",polymer,,true
MAT_LATEX,라텍스,latex,"natural rubber;rubber latex",rubber,,true
MAT_NAPHTHA,나프타,naphtha,"naphta;petrochemical feedstock",upstream,,true
```

필드 설명:

| 필드 | 설명 |
|---|---|
| `material_id` | 내부 원자재 ID |
| `material_name_ko` | 한국어 원자재명 |
| `material_name_en` | 영어 원자재명 |
| `aliases` | 동의어, 약어, 기사 표현 |
| `material_type` | plastic, polymer, metal, rubber, glass, upstream 등 |
| `parent_material_id` | 상위 원자재 또는 공급망 원천 |
| `is_active` | 사용 여부 |

---

### 4.3 device_material_mapping

핵심 매핑 table이다.

```csv
mapping_id,standard_code,item_group,material_id,relation_type,usage_part,material_weight,criticality_weight,dependency_weight,evidence_type,evidence_text,llm_confidence,human_review_status,mapping_version
MAP_0001,A001,syringe,MAT_PP,direct,barrel/plunger,0.80,0.90,0.80,manual_seed,"주사기 주요 플라스틱 부품 후보",,approved,v1.0
MAP_0002,B001,infusion_set,MAT_PVC,direct,tube,0.90,0.95,0.85,llm_candidate,"수액세트 튜브 소재 후보",0.78,needs_review,v1.0
MAP_0003,C001,catheter,MAT_SILICONE,direct,tube,0.70,0.90,0.70,llm_candidate,"카테터 소재 후보",0.74,needs_review,v1.0
MAP_0004,D001,medical_waste_container,MAT_HDPE,direct,container,0.80,0.70,0.75,manual_seed,"의료폐기물 용기 플라스틱 소재 후보",,approved,v1.0
```

필드 설명:

| 필드 | 설명 |
|---|---|
| `mapping_id` | 매핑 고유 ID |
| `standard_code` | 의료기기 품목 코드 |
| `item_group` | 품목군 |
| `material_id` | 원자재 ID |
| `relation_type` | direct, upstream, packaging, substitute |
| `usage_part` | 원자재가 사용되는 부위 |
| `material_weight` | 해당 품목에서 원자재 관련성, 0~1 |
| `criticality_weight` | 해당 원자재가 없을 때 품목 생산에 미치는 영향, 0~1 |
| `dependency_weight` | 대체 가능성/의존도, 0~1 |
| `evidence_type` | manual_seed, llm_candidate, external_source, backend_confirmed |
| `evidence_text` | 매핑 근거 요약 |
| `llm_confidence` | LLM이 제시한 confidence |
| `human_review_status` | approved, rejected, needs_review |
| `mapping_version` | 매핑 버전 |

---

### 4.4 material_alias_mapping

뉴스 기사에서 다양한 표현을 같은 material_id로 정규화하기 위한 table이다.

```csv
alias,normalized_material_id,language,match_type
polypropylene,MAT_PP,en,exact
PP,MAT_PP,en,exact
polypropylene resin,MAT_PP,en,phrase
폴리프로필렌,MAT_PP,ko,exact
PVC,MAT_PVC,en,exact
polyvinyl chloride,MAT_PVC,en,exact
PVC resin,MAT_PVC,en,phrase
나프타,MAT_NAPHTHA,ko,exact
naphtha,MAT_NAPHTHA,en,exact
```

---

### 4.5 material_supply_chain_edges

상위 원자재 리스크를 하위 소재로 전파하기 위한 graph table이다.

```csv
from_material_id,to_material_id,edge_weight,relation
MAT_NAPHTHA,MAT_PP,0.70,feedstock
MAT_NAPHTHA,MAT_PE,0.70,feedstock
MAT_NAPHTHA,MAT_PVC,0.60,petrochemical_chain
MAT_CRUDE_OIL,MAT_NAPHTHA,0.80,upstream
```

예시:

```text
나프타 리스크 상승
→ PP, PE, PVC 리스크 일부 상승
→ 주사기, 수액세트, 카테터, 의료폐기물용기 리스크 상승
```

---

## 5. 원자재-제품 매칭 점수 계산

### 5.1 직접 매칭 점수

```text
direct_match_score
= material_weight
* criticality_weight
* dependency_weight
```

예시:

```text
주사기 - polypropylene
= 0.80 * 0.90 * 0.80
= 0.576
```

---

### 5.2 뉴스 원자재 리스크를 품목 리스크로 변환

뉴스에서 특정 원자재 리스크가 계산되었다고 가정한다.

```text
material_risk_score[MAT_PP] = 0.70
material_risk_score[MAT_PVC] = 0.60
```

품목별 원자재 리스크는 다음처럼 계산한다.

```text
product_material_risk
= 1 - exp(-sum(material_risk_score * direct_match_score))
```

예시:

```text
주사기 리스크
= 1 - exp(-(0.70 * 0.576))
= 약 0.331
```

이 방식을 쓰면 여러 원자재 리스크가 동시에 있을 때 단순 합산으로 1을 초과하는 문제를 줄일 수 있다.

---

### 5.3 upstream material risk 전파

뉴스가 "naphtha shortage"만 언급했을 경우 직접적으로 주사기/수액세트를 언급하지 않아도 downstream 소재에 영향을 줄 수 있다.

```text
downstream_material_risk[to_material]
+= upstream_material_risk[from_material] * edge_weight
```

예시:

```text
MAT_NAPHTHA risk = 0.80
MAT_NAPHTHA → MAT_PP edge_weight = 0.70

MAT_PP propagated risk
= 0.80 * 0.70
= 0.56
```

이후 MAT_PP와 연결된 품목의 리스크를 계산한다.

---

## 6. LLM을 사용하는 위치

LLM은 다음 세 곳에서 사용한다.

### 6.1 품목 → 원자재 후보 생성

입력:

```json
{
  "standard_code": "B001",
  "item_name": "수액세트",
  "item_group": "infusion_set",
  "description": "수액 투여에 사용하는 일회용 의료기기"
}
```

출력:

```json
{
  "standard_code": "B001",
  "candidates": [
    {
      "material_name": "PVC",
      "material_id": "MAT_PVC",
      "usage_part": "tube",
      "relation_type": "direct",
      "material_weight": 0.9,
      "criticality_weight": 0.95,
      "dependency_weight": 0.85,
      "confidence": 0.78,
      "evidence": "Infusion sets commonly include flexible plastic tubing; PVC is a likely candidate material."
    },
    {
      "material_name": "polypropylene",
      "material_id": "MAT_PP",
      "usage_part": "connector/chamber",
      "relation_type": "direct",
      "material_weight": 0.5,
      "criticality_weight": 0.7,
      "dependency_weight": 0.6,
      "confidence": 0.62,
      "evidence": "Connectors or chambers may use rigid plastic components."
    }
  ],
  "uncertainty_reason": "Exact composition can vary by manufacturer."
}
```

주의:

- LLM이 `material_master`에 없는 원자재를 생성하면 `needs_review`로 보낸다.
- 근거가 없거나 너무 일반적인 경우 confidence를 낮춘다.
- 의료기기 제조사별 재질 차이가 있으므로 `approved`로 바로 저장하지 않는다.

---

### 6.2 뉴스 기사 → 원자재/국가/이벤트 추출

입력:

```json
{
  "title": "PVC resin prices surge amid supply constraints",
  "body": "Manufacturers report delayed shipments and higher resin prices..."
}
```

출력:

```json
{
  "is_relevant": true,
  "event_type": "raw_material_price_risk",
  "affected_materials": ["MAT_PVC"],
  "affected_countries": ["China"],
  "severity": 0.72,
  "confidence": 0.81,
  "time_horizon": "1-3 months",
  "evidence": "The article explicitly mentions PVC resin price surge and supply constraints."
}
```

---

### 6.3 매핑 검증 보조

LLM에게 기존 매핑이 타당한지 검토하게 할 수 있다.

입력:

```json
{
  "item_name": "카테터",
  "item_group": "catheter",
  "mapped_material": "PVC",
  "usage_part": "tube",
  "current_weight": 0.7
}
```

출력:

```json
{
  "is_plausible": true,
  "recommended_action": "keep_but_review",
  "suggested_weight": 0.65,
  "confidence": 0.71,
  "reason": "PVC can be used in some catheter/tubing applications, but material varies by catheter type."
}
```

---

## 7. LLM prompt 설계

### 7.1 Candidate generation prompt

```text
You are helping build a supply-risk mapping table for public-health medical supplies.

Task:
Given a medical supply item, propose candidate raw materials or upstream materials that can affect its production or supply.

Rules:
1. Only use materials from the provided material_master list.
2. Do not invent exact product composition.
3. If the material can vary by manufacturer, lower the confidence.
4. Return JSON only.
5. Include usage_part, relation_type, material_weight, criticality_weight, dependency_weight, confidence, and evidence.
6. If unsure, set human_review_status to "needs_review".

Input:
- standard_code: {standard_code}
- item_name: {item_name}
- item_group: {item_group}
- description: {description}
- allowed_materials: {material_master}
```

---

### 7.2 News extraction prompt

```text
You are extracting supply-risk signals from news.

Task:
Classify whether the article affects medical supply demand or supply chain risk.

Return JSON with:
- is_relevant
- event_type
- affected_materials
- affected_countries
- severity
- confidence
- evidence
- reason_for_exclusion if not relevant

Rules:
1. Use only event types from news_event_taxonomy.
2. Use only materials from material_master.
3. Do not infer a material unless the article mentions it directly or mentions a known alias.
4. If the article is about general economy but not supply/demand/material/logistics/geopolitical/health risk, mark is_relevant=false.
```

---

## 8. LLM 결과 평가 방법

LLM 결과 평가는 두 가지로 나눈다.

```text
A. 원자재-제품 매칭 자체의 정확도 평가
B. 매칭을 사용했을 때 재고 예측/리스크 점수에 도움이 되는지 평가
```

---

### 8.1 Golden Set 구축

LLM 평가를 하려면 정답셋이 필요하다.

초기에는 다음 기준으로 100~300개 정도의 golden set을 만든다.

```text
- 사용량 상위 품목
- 재고 중요도 높은 품목
- 원자재 의존도가 명확한 품목
- 애매한 품목
- 품목명이 비표준화된 품목
```

Golden set 예시:

```csv
standard_code,item_name,item_group,true_material_id,required,importance
A001,주사기,syringe,MAT_PP,true,high
B001,수액세트,infusion_set,MAT_PVC,true,high
C001,카테터,catheter,MAT_SILICONE,false,medium
C001,카테터,catheter,MAT_PVC,true,medium
```

중요한 점:

- 하나의 품목에 정답 원자재가 여러 개일 수 있다.
- `required=true`는 해당 품목 리스크 계산에 반드시 들어가야 하는 핵심 소재를 의미한다.
- `required=false`는 가능성은 있지만 품목 전체 리스크에 과하게 반영하면 안 되는 소재를 의미한다.

---

### 8.2 매칭 정확도 지표

#### 1) Precision

LLM이 제안한 매핑 중 실제로 맞는 비율이다.

```text
precision = correct_predicted_mappings / predicted_mappings
```

해석:

- 낮으면 잘못된 원자재를 많이 붙이는 것
- 잘못된 리스크 알림이 증가할 수 있음

---

#### 2) Recall

정답 매핑 중 LLM이 찾아낸 비율이다.

```text
recall = correct_predicted_mappings / true_mappings
```

해석:

- 낮으면 중요한 원자재 리스크를 놓침
- 공급망 위험 감지가 늦어질 수 있음

---

#### 3) F1-score

precision과 recall의 균형이다.

```text
F1 = 2 * precision * recall / (precision + recall)
```

---

#### 4) Recall@K

LLM이 후보 원자재를 여러 개 제안할 경우 사용한다.

```text
Recall@3 = 정답 원자재가 상위 3개 후보 안에 포함된 비율
```

MVP에서는 Recall@3가 중요하다.
LLM은 최종 정답 생성기가 아니라 후보 생성기이므로, 정답이 후보 안에 들어오기만 해도 사람 검수로 살릴 수 있다.

---

#### 5) Hallucination Rate

근거가 없거나 `material_master`에 없는 원자재를 생성한 비율이다.

```text
hallucination_rate
= invalid_or_unsupported_mappings / total_llm_mappings
```

목표:

```text
hallucination_rate < 5%
```

---

#### 6) Review Pass Rate

사람 검수에서 승인된 비율이다.

```text
review_pass_rate
= approved_llm_mappings / reviewed_llm_mappings
```

초기 목표:

```text
review_pass_rate >= 70%
```

---

#### 7) Consistency Rate

같은 입력을 여러 번 넣었을 때 같은 후보가 나오는 비율이다.

```text
consistency_rate
= stable_outputs / repeated_tests
```

운영에서는 temperature를 0 또는 낮게 설정하고, JSON schema를 강제해야 한다.

---

### 8.3 리스크 점수 관점의 평가

매핑 정확도가 높아도 모델 성능에 도움이 안 될 수 있다.
따라서 리스크 점수 관점 평가가 필요하다.

#### 1) Ablation Test

현재 모델 구조와 연결하면 다음 비교가 가능하다.

```text
Model A: 과거 사용량 feature만 사용
Model B: 과거 사용량 + 뉴스 리스크
Model C: 과거 사용량 + 뉴스 리스크 + 원자재 리스크
Model D: 과거 사용량 + 뉴스 리스크 + 원자재 리스크 + product-material mapping 개선본
```

평가 기준:

```text
validation WAPE
test WAPE
품목군별 WAPE
공급망 이벤트 발생 월 WAPE
```

운영 판단에서는 WAPE를 우선 사용한다.

---

#### 2) Event-window Evaluation

특정 공급망 이슈가 있었던 기간을 기준으로 평가한다.

```text
이벤트 발생 월 t
→ t, t+1, t+2, t+3 월의 예측 오차 확인
```

예시:

```text
나프타 가격 급등 뉴스 발생
→ PP/PVC 관련 품목의 risk score가 상승했는가?
→ 해당 품목들의 사용량/발주량/부족 위험과 관련이 있었는가?
```

---

#### 3) Alert Quality Evaluation

백엔드나 대시보드에서 보여줄 경고 품질 평가다.

| 지표 | 의미 |
|---|---|
| `alert_precision` | 발생한 경고 중 실제로 의미 있는 경고 비율 |
| `alert_recall` | 실제 위험 이벤트 중 경고가 발생한 비율 |
| `false_alarm_rate` | 불필요한 경고 비율 |
| `missed_risk_rate` | 놓친 위험 비율 |
| `lead_time` | 실제 문제 발생 전 며칠/몇 개월 전에 감지했는지 |

---

### 8.4 사람 검수 기준

LLM 결과를 바로 `approved`하지 말고 다음 기준으로 나눈다.

```text
approved:
  - material_master에 존재
  - 품목군과 원자재 조합이 상식적으로 타당
  - confidence >= 0.80
  - high impact 품목이 아니거나 이미 seed 근거 존재

needs_review:
  - confidence 0.50~0.79
  - 품목군별로 가능한 소재가 여러 개
  - 원자재 사용 부위가 애매함
  - 품목 사용량이 많거나 criticality가 높음

rejected:
  - material_master에 없음
  - 품목과 무관한 소재
  - 근거가 너무 일반적
  - 뉴스 키워드만 보고 과도하게 연결
```

---

## 9. 백엔드 서버로 전송하는 방식

현재 AI 서비스는 요청 시점에 외부 뉴스 API, 원자재 API, LLM을 직접 호출하지 않고, 배치 산출물을 미리 생성한 뒤 API는 precomputed output을 조회하는 구조가 적합하다.

따라서 원자재-제품 매칭도 같은 방식으로 처리한다.

```text
배치 처리 시점:
- 원자재-제품 매핑 생성/갱신
- 뉴스/원자재 리스크 계산
- 품목별 supply risk 계산
- outputs/*.csv 생성

API 요청 시점:
- 이미 계산된 mapping/risk 결과 조회
- 백엔드는 AI service의 FastAPI endpoint 호출
```

---

## 10. 권장 API 설계

### 10.1 품목별 원자재 매핑 조회

```http
GET /api/v1/ai/material-mappings/{standardCode}
```

Response:

```json
{
  "standardCode": "B001",
  "itemName": "수액세트",
  "itemGroup": "infusion_set",
  "mappingVersion": "v1.0",
  "materials": [
    {
      "materialId": "MAT_PVC",
      "materialNameKo": "폴리염화비닐",
      "materialNameEn": "PVC",
      "relationType": "direct",
      "usagePart": "tube",
      "materialWeight": 0.9,
      "criticalityWeight": 0.95,
      "dependencyWeight": 0.85,
      "mappingScore": 0.727,
      "evidenceType": "llm_candidate",
      "evidenceText": "수액세트 튜브 소재 후보",
      "llmConfidence": 0.78,
      "humanReviewStatus": "needs_review"
    }
  ],
  "updatedAt": "2026-07-06T12:00:00+09:00"
}
```

---

### 10.2 전체 매핑 조회

```http
GET /api/v1/ai/material-mappings
```

Query parameters:

| 파라미터 | 설명 |
|---|---|
| `itemGroup` | 품목군 필터 |
| `materialId` | 원자재 필터 |
| `reviewStatus` | approved, needs_review, rejected |
| `version` | mapping version |

Response:

```json
{
  "mappingVersion": "v1.0",
  "count": 2,
  "items": [
    {
      "standardCode": "A001",
      "itemName": "주사기",
      "itemGroup": "syringe",
      "materialId": "MAT_PP",
      "materialNameKo": "폴리프로필렌",
      "materialNameEn": "polypropylene",
      "mappingScore": 0.576,
      "humanReviewStatus": "approved"
    },
    {
      "standardCode": "B001",
      "itemName": "수액세트",
      "itemGroup": "infusion_set",
      "materialId": "MAT_PVC",
      "materialNameKo": "폴리염화비닐",
      "materialNameEn": "PVC",
      "mappingScore": 0.727,
      "humanReviewStatus": "needs_review"
    }
  ]
}
```

---

### 10.3 품목별 공급망 리스크 조회

기존 `/api/v1/ai/supply-risk/{itemGroupId}`를 확장하거나, standardCode 기준 endpoint를 추가한다.

```http
GET /api/v1/ai/supply-risk/items/{standardCode}
```

Response:

```json
{
  "standardCode": "B001",
  "itemName": "수액세트",
  "targetMonth": "2026-08",
  "supplyRiskScore": 0.64,
  "riskLevel": "HIGH",
  "riskBreakdown": {
    "newsRisk": 0.52,
    "commodityRisk": 0.61,
    "materialMappingRisk": 0.67,
    "countryExposureRisk": 0.48
  },
  "affectedMaterials": [
    {
      "materialId": "MAT_PVC",
      "materialName": "PVC",
      "materialRiskScore": 0.72,
      "mappingScore": 0.727,
      "contribution": 0.523
    }
  ],
  "topEvidence": [
    {
      "eventType": "raw_material_price_risk",
      "title": "PVC resin prices surge amid supply constraints",
      "source": "industry_news",
      "publishedAt": "2026-07-01",
      "severity": 0.72,
      "confidence": 0.81
    }
  ],
  "mappingVersion": "v1.0",
  "generatedAt": "2026-07-06T12:00:00+09:00"
}
```

---

### 10.4 매핑 검수 상태 업데이트

백엔드 관리자 화면에서 사람이 검수한 결과를 AI 서비스에 반영하려면 내부 endpoint가 필요하다.

```http
PATCH /api/v1/ai/material-mappings/{mappingId}/review
```

Request:

```json
{
  "humanReviewStatus": "approved",
  "reviewer": "admin",
  "reviewMemo": "수액세트 튜브 소재로 PVC 매핑 승인",
  "materialWeight": 0.9,
  "criticalityWeight": 0.95,
  "dependencyWeight": 0.85
}
```

Response:

```json
{
  "mappingId": "MAP_0002",
  "humanReviewStatus": "approved",
  "mappingVersion": "v1.1",
  "updatedAt": "2026-07-06T12:10:00+09:00"
}
```

MVP에서는 PATCH endpoint를 바로 만들지 않아도 된다.
처음에는 CSV를 수정하고 배치 실행으로 반영해도 충분하다.

---

## 11. 백엔드와 AI 서비스의 책임 분리

### AI 서비스 책임

```text
- 원자재 master 관리
- 원자재 alias 관리
- 원자재-품목 매핑 생성
- LLM 후보 생성
- 뉴스/원자재 리스크 계산
- 품목별 supply risk score 계산
- risk evidence 제공
```

### 백엔드 책임

```text
- 사용자 인증/인가
- 기관/사용자 권한 관리
- 실제 재고량 관리
- 입출고 트랜잭션 관리
- 발주 승인/반려
- 관리자 검수 UI 제공
- AI API 호출 결과 저장 또는 캐싱
```

### 권장 구조

```text
초기 MVP:
AI service가 mapping CSV와 risk output을 관리
Backend는 AI endpoint를 조회해서 화면에 표시

중기:
Backend에 mapping review 화면 추가
Review 결과를 AI service에 PATCH 또는 CSV export로 반영

후기:
Backend DB가 approved mapping을 authoritative table로 보관
AI service는 backend에서 approved mapping을 받아 risk 계산
```

---

## 12. 파일 산출물 구조

추천 파일 구조:

```text
data/mapping/
  product_master.csv
  material_master.csv
  material_alias_mapping.csv
  device_material_mapping.csv
  material_supply_chain_edges.csv
  country_material_exposure.csv

outputs/
  material_mapping_candidates.csv
  material_mapping_validation_report.csv
  product_material_risk_scores.csv
  supply_risk_scores.csv
```

---

## 13. 구현 위치

현재 프로젝트 구조 기준 추천 위치:

```text
src/mapping/
  __init__.py
  material_mapper.py
  mapping_schema.py
  mapping_loader.py
  mapping_validator.py
  llm_material_mapper.py
  risk_propagation.py

src/news/
  news_llm_analyzer.py
  news_risk_scorer.py

src/commodity/
  commodity_risk_scorer.py

src/modeling/
  inventory_policy.py

src/serving/
  api.py
  schemas.py
```

역할:

| 파일 | 역할 |
|---|---|
| `material_mapper.py` | 품목-원자재 매핑 생성 |
| `mapping_schema.py` | pydantic schema 정의 |
| `mapping_loader.py` | CSV 로딩 |
| `mapping_validator.py` | rule validation |
| `llm_material_mapper.py` | LLM 후보 생성 |
| `risk_propagation.py` | upstream → downstream 리스크 전파 |
| `news_risk_scorer.py` | 뉴스에서 material risk 계산 |
| `commodity_risk_scorer.py` | 원자재 가격 기반 risk 계산 |
| `inventory_policy.py` | 최종 재고 정책 반영 |
| `api.py` | FastAPI endpoint 제공 |
| `schemas.py` | API response schema |

---

## 14. MVP 구현 순서

### Step 1. material master 작성

먼저 10~20개 원자재만 작성한다.

```text
PP
PE
PVC
silicone
polyurethane
latex
stainless steel
glass
naphtha
crude oil
```

---

### Step 2. 품목군별 seed mapping 작성

처음부터 전체 품목을 다 하지 말고, 사용량 상위 품목군부터 한다.

```text
주사기
수액세트
카테터
의료폐기물용기
마스크
장갑
거즈
검체채취도구
```

---

### Step 3. LLM 후보 생성은 needs_review로 저장

LLM 결과는 기본적으로 `approved`가 아니라 `needs_review`로 저장한다.

```text
manual_seed → approved 가능
llm_candidate → needs_review 기본값
```

---

### Step 4. risk 계산에는 approved + high-confidence만 사용

MVP에서는 아래 기준 추천:

```text
사용 가능:
- human_review_status = approved
- 또는 llm_confidence >= 0.85 이고 material_master에 존재

검수 필요:
- 0.50 <= llm_confidence < 0.85

사용 제외:
- llm_confidence < 0.50
- material_master에 없는 원자재
- rejected
```

---

### Step 5. validation report 생성

배치 실행 후 다음 리포트를 만든다.

```text
outputs/material_mapping_validation_report.csv
```

컬럼:

```csv
metric,value
total_products,100
mapped_products,82
coverage,0.82
approved_mappings,120
needs_review_mappings,45
rejected_mappings,10
avg_llm_confidence,0.74
hallucination_rate,0.03
```

---

## 15. Notion DB로 관리할 경우 컬럼

Notion에서 사람이 검수하기 쉽게 만들려면 다음 컬럼을 추천한다.

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `mapping_id` | text | 매핑 ID |
| `standard_code` | text | 품목 코드 |
| `item_name` | title | 품목명 |
| `item_group` | select | 품목군 |
| `material_name` | select | 원자재 |
| `usage_part` | text | 사용 부위 |
| `relation_type` | select | direct/upstream/packaging/substitute |
| `material_weight` | number | 관련성 |
| `criticality_weight` | number | 중요도 |
| `dependency_weight` | number | 의존도 |
| `llm_confidence` | number | LLM 신뢰도 |
| `human_review_status` | status | approved/needs_review/rejected |
| `evidence_text` | text | 근거 |
| `review_memo` | text | 검수 메모 |
| `mapping_version` | text | 버전 |

---

## 16. 위험 점수에 반영하는 방식

현재 외부 리스크 구조를 유지하면서 원자재-제품 매핑 리스크를 명시적으로 분리하는 것을 추천한다.

기존:

```text
external_risk_score = 0.4 * disease_news_risk
                    + 0.3 * supply_news_risk
                    + 0.3 * commodity_risk
```

개선안:

```text
material_supply_risk
= 1 - exp(-sum(material_risk_score * mapping_score * exposure_weight))

external_risk_score
= 0.35 * disease_demand_risk
+ 0.25 * supply_news_risk
+ 0.25 * material_supply_risk
+ 0.15 * commodity_price_risk
```

또는 단순하게 MVP에서는 다음처럼 시작한다.

```text
external_risk_score
= 0.4 * disease_news_risk
+ 0.3 * supply_news_risk
+ 0.3 * material_supply_risk
```

주의:

- weight는 고정 정답이 아니다.
- validation WAPE와 alert quality를 기준으로 보정해야 한다.
- 품목별 사용량 규모 차이가 크므로 전체 운영 평가에서는 WAPE를 우선 사용한다.

---

## 17. 테스트 케이스 예시

### Case 1. PVC 뉴스가 수액세트 리스크로 연결되는지

입력 뉴스:

```text
PVC resin prices surge amid supply shortage.
```

기대 결과:

```text
affected_materials = [MAT_PVC]
related_items include infusion_set
B001 supplyRiskScore 상승
```

---

### Case 2. 나프타 뉴스가 PP/PVC 품목으로 전파되는지

입력 뉴스:

```text
Naphtha supply disrupted due to geopolitical conflict.
```

기대 결과:

```text
affected_materials = [MAT_NAPHTHA]
propagated_materials = [MAT_PP, MAT_PE, MAT_PVC]
related_items include syringe, infusion_set, waste_container
```

---

### Case 3. 일반 경제 뉴스는 제외되는지

입력 뉴스:

```text
Stock market rises after interest rate decision.
```

기대 결과:

```text
is_relevant = false
affected_materials = []
product risk 변화 없음
```

---

### Case 4. 근거 없는 LLM 매핑 제거

입력 품목:

```text
item_name = 체온계
LLM candidate = PVC
confidence = 0.42
```

기대 결과:

```text
human_review_status = rejected 또는 needs_review
risk calculation에는 사용하지 않음
```

---

## 18. API 응답에서 설명 가능성 제공

백엔드 화면에서는 단순 risk score만 보여주면 사용자가 신뢰하기 어렵다.
다음 정보를 같이 제공해야 한다.

```text
- 어떤 원자재 때문에 위험이 올라갔는지
- 그 원자재가 어떤 품목과 연결되어 있는지
- 어떤 뉴스/가격 이벤트가 근거인지
- 매핑 신뢰도가 어느 정도인지
- 사람 검수 여부가 무엇인지
```

예시 UI 문구:

```text
수액세트의 공급 리스크가 상승했습니다.
주요 원인은 PVC 원자재 리스크 상승입니다.
PVC는 수액세트 튜브 소재 후보로 매핑되어 있으며, 현재 매핑 상태는 needs_review입니다.
최근 PVC resin 가격 상승 및 공급 차질 뉴스가 감지되었습니다.
```

---

## 19. 데이터 버전 관리

매핑은 시간이 지나면 바뀔 수 있다.
따라서 version 관리가 필요하다.

추천 버전 규칙:

```text
v1.0: manual seed + LLM candidate 초기본
v1.1: 사람 검수 반영
v1.2: 원자재 alias 확장
v2.0: 백엔드 approved mapping 연동
```

모든 risk output에는 `mapping_version`을 포함한다.

```json
{
  "standardCode": "B001",
  "supplyRiskScore": 0.64,
  "mappingVersion": "v1.1",
  "generatedAt": "2026-07-06T12:00:00+09:00"
}
```

이렇게 해야 나중에 모델 성능이나 리스크 점수가 달라졌을 때 어떤 mapping 기준으로 계산했는지 추적할 수 있다.

---

## 20. 운영상 주의점

### 20.1 LLM hallucination 방지

```text
- material_master에 없는 원자재 사용 금지
- JSON schema 강제
- confidence와 evidence 필수
- temperature 낮게 설정
- LLM 결과 기본 상태는 needs_review
```

### 20.2 과도한 리스크 전파 방지

```text
- upstream edge_weight는 1보다 작게 설정
- product risk는 1 - exp(-sum(...))으로 bounded 처리
- 동일 이벤트 중복 기사 novelty 보정
```

### 20.3 품목군 fallback 주의

품목별 매핑이 없을 때 품목군 매핑을 fallback으로 사용할 수 있다.
다만 품목군 fallback은 불확실성이 크므로 낮은 weight를 적용한다.

```text
item-level mapping weight = 1.0
group-level fallback weight = 0.5~0.7
```

### 20.4 제조사별 차이

같은 품목이라도 제조사에 따라 소재가 다를 수 있다.
초기에는 제조사별 BOM이 없으므로 `candidate material` 수준으로 다룬다.

문서나 화면에서도 다음처럼 표현한다.

```text
"확정 소재"가 아니라 "해당 품목 생산에 영향을 줄 수 있는 후보 원자재"
```

---

## 21. 최종 추천안

MVP 기준으로 가장 현실적인 구조는 다음과 같다.

```text
1. product_master.csv 작성
2. material_master.csv 작성
3. device_material_mapping.csv seed 작성
4. LLM으로 후보 원자재 추가 생성
5. mapping_validator.py로 rule validation
6. needs_review 상태로 Notion/CSV 검수
7. approved mapping만 risk 계산에 사용
8. material risk → product risk 전파
9. FastAPI로 mapping/risk 조회 제공
10. validation WAPE + manual precision/recall로 성능 평가
```

핵심 원칙:

```text
LLM은 후보 생성기
사람 검수는 품질 보증
mapping table은 버전 관리
백엔드는 precomputed 결과 조회
리스크 weight는 validation 결과로 보정
```

---

## 22. 참고 레퍼런스

- [NIST AI Risk Management Framework](https://www.nist.gov/itl/ai-risk-management-framework): AI 시스템의 측정, 모니터링, 거버넌스 관점 참고
- [NIST Generative AI Profile](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence): 생성형 AI 사용 시 위험 식별과 관리 관점 참고
- [FDA UDI System](https://www.fda.gov/medical-devices/device-advice-comprehensive-regulatory-assistance/unique-device-identification-system-udi-system): 의료기기 식별자를 제조부터 유통, 사용까지 추적하는 식별 체계 참고
- [FDA UDI Basics](https://www.fda.gov/medical-devices/unique-device-identification-system-udi-system/udi-basics): Device Identifier와 Production Identifier 구분 참고
- [ECHA SCIP Database](https://echa.europa.eu/scip-database): 제품과 포함 물질 정보를 구조화해 관리하는 데이터 모델 관점 참고
