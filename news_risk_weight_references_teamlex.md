# WeP-Stock 뉴스 리스크 세부 Weight 설정 레퍼런스 정리

작성일: 2026-07-06  
대상 프로젝트: TeamLex / WeP-Stock AI Service  
목적: 뉴스 기반 외부 리스크 점수 산정 로직에서 `event_type_weight`, `source_weight`, `recency_weight`, `item_relevance`, `exposure_weight`, `novelty_weight` 등을 어떻게 설계할지에 대한 논문·레퍼런스 근거 정리

---

## 1. 핵심 결론

의료기기 재고 예측에 바로 적용할 수 있는 **공인 고정 weight 표**는 찾기 어렵다. 대신 기존 연구들은 다음과 같은 방식으로 뉴스 기반 리스크 지수를 만든다.

1. 뉴스 기사 빈도 또는 기사 비율을 지수화한다.
2. 리스크 이벤트 유형을 카테고리로 분류한다.
3. 출처 신뢰도, 분류 신뢰도, 기사 중복, 시점, 지역·품목 연관성을 반영한다.
4. 최종 weight는 전문가 판단, AHP/Fuzzy AHP, 또는 validation 성능 기반으로 보정한다.

따라서 WeP-Stock에서는 다음 구조가 가장 방어 가능하다.

```text
article_score
= event_type_weight
* severity
* confidence
* source_weight
* item_relevance
* exposure_weight
* recency_weight
* novelty_weight
```

월별·품목별 뉴스 리스크는 기사 점수 합을 그대로 쓰기보다 포화 함수로 변환하는 것이 적합하다.

```text
monthly_item_news_risk = 1 - exp(-sum(article_score))
```

이 방식은 뉴스가 많을수록 위험이 증가하지만, 동일 사건의 반복 보도로 점수가 무한히 커지는 문제를 줄인다.

---

## 2. 현재 프로젝트와의 연결

현재 WeP-Stock의 외부 리스크 점수는 아래처럼 단순 가중합 구조로 설계되어 있다.

```text
external_risk_score = 0.4 * disease_news_risk
                    + 0.3 * supply_news_risk
                    + 0.3 * commodity_risk
```

그리고 향후 작업 우선순위 중 하나가 “뉴스 리스크 세부 weight 설정 파일화”이다. 따라서 이번 정리는 `src/news/news_risk_scorer.py`와 `data/mapping/*.csv` 또는 별도 YAML/JSON 설정 파일로 옮길 수 있는 기준안을 만드는 데 초점을 둔다.

---

## 3. 레퍼런스 요약표

| 구분 | 레퍼런스 | 핵심 방법 | WeP-Stock에 적용할 부분 |
|---|---|---|---|
| 뉴스 기반 경제 불확실성 | Baker, Bloom & Davis, 2016 | 신문 기사 빈도를 이용해 Economic Policy Uncertainty Index 구축 | 기사 수를 전체 기사 수로 정규화, 카테고리별 지수, 사람 검수 기반 validation |
| 뉴스 기반 지정학 리스크 | Caldara & Iacoviello, 2022 | 지정학 리스크 기사를 8개 유형으로 분류하고 월별 기사 비율로 GPR 지수 구축 | 전쟁, 테러, 군사 위협, 전쟁 발생 등 이벤트 유형 weight 설계 |
| 감염병 뉴스 감시 | Freifeld et al., 2008, HealthMap | 웹 뉴스, ProMED, WHO 등 다양한 출처를 자동 분류·통합 | disease_news_risk, source_weight, classifier confidence, 중복 제거 |
| 공급망 뉴스 텍스트 분석 | 최동엽·서용원, 2023 | KoBERT로 공급망 리스크 기사 필터링 및 리스크 유형 분류 | 한국어 뉴스 필터링, 공급망 리스크 유형 분류 모델 근거 |
| LLM 기반 공급망 이벤트 식별 | Shahsavari et al., 2024 | 뉴스 데이터와 LLM으로 risk event의 contributing event 식별 | LLM으로 이벤트 원인·위험 유형 추출, 뉴스 → 위험 이벤트 연결 |
| 공급망 텍스트마이닝 리뷰 | Gelastopoulos & Keramydas, 2025 | 온라인 데이터 기반 SCRM 텍스트마이닝 연구 33편 리뷰 | 뉴스·소셜 데이터, sentiment/topic/classification의 사용 근거 |
| 공급망 리스크 AHP | Ganguly & Kumar, 2019 | Fuzzy AHP와 연속형 risk matrix로 공급망 리스크 우선순위화 | 초기 weight를 전문가 판단으로 정하고, 이후 데이터로 보정 |
| 의약품 공급망 AHP/SAW | Jaberidoost et al., 2015 | 의약품 공급망 위험을 AHP와 SAW로 평가 | 의료·보건 공급망에서 AHP 기반 weight 산정 근거 |
| 공급망 압력 지수 | Benigno et al., 2022, GSCPI | 운송비, PMI 등 27개 변수를 PCA로 결합 | 외부 변수 weight를 임의로 고정하지 않고 데이터 기반으로 보정하는 근거 |
| 글로벌 뉴스 이벤트 DB | GDELT Event Database | 이벤트 유형, Goldstein Scale, tone 등 구조화 뉴스 이벤트 제공 | event_type_weight 초기값 또는 외부 이벤트 코드 매핑 참고 |

---

## 4. 레퍼런스별 상세 정리

### 4.1 Baker, Bloom & Davis (2016) — Economic Policy Uncertainty Index

**논문**  
Scott R. Baker, Nicholas Bloom, Steven J. Davis. “Measuring Economic Policy Uncertainty.” *Quarterly Journal of Economics*, 2016.

**핵심 내용**

- 신문 기사에서 경제, 정책, 불확실성 관련 단어가 동시에 등장하는 기사 빈도를 집계해 EPU 지수를 만든다.
- 단순 기사 수가 아니라 월별·신문별 전체 기사 수 대비 비율로 정규화한다.
- 사람이 직접 읽은 기사와 자동 분류 결과를 비교해 지수의 타당성을 검증한다.
- 헬스케어 정책 불확실성, 국가안보 정책 불확실성처럼 세부 카테고리 지수도 만든다.

**WeP-Stock 적용 포인트**

- `news_count`를 그대로 쓰기보다 `risk_article_count / total_article_count`처럼 정규화하는 근거로 사용한다.
- “감염병 뉴스 리스크”, “공급망 뉴스 리스크”, “원자재 뉴스 리스크”처럼 카테고리별 지수를 따로 만들 수 있다.
- 자동 분류 결과를 일부 샘플링해 사람이 검수하는 방식으로 weight의 타당성을 설명할 수 있다.

---

### 4.2 Caldara & Iacoviello (2022) — Geopolitical Risk Index

**논문**  
Dario Caldara, Matteo Iacoviello. “Measuring Geopolitical Risk.” *American Economic Review*, 2022.

**핵심 내용**

- 주요 신문에서 지정학적 위협과 실제 지정학적 사건을 다룬 기사를 월별로 집계한다.
- GPR은 전체 기사 대비 지정학 리스크 관련 기사 비율로 계산된다.
- 카테고리는 War Threats, Peace Threats, Military Buildups, Nuclear Threats, Terror Threats, Beginning of War, Escalation of War, Terror Acts 등으로 나뉜다.
- 위협성 기사와 실제 발생 기사로 sub-index를 나눈다.

**WeP-Stock 적용 포인트**

- `event_type_weight`를 설계할 때 전쟁, 수출 제한, 물류 봉쇄, 군사 충돌을 높은 weight로 둘 근거가 된다.
- “위협 뉴스”와 “실제 발생 뉴스”를 구분할 수 있다.
  - 예: “전쟁 가능성”은 threat
  - 예: “항만 폐쇄 발생”은 act
- 공급망 리스크에서는 실제 발생 사건이 예측에 더 직접적이므로 threat보다 act에 높은 weight를 줄 수 있다.

---

### 4.3 Freifeld et al. (2008) — HealthMap

**논문**  
Clark C. Freifeld et al. “HealthMap: Global Infectious Disease Monitoring through Automated Classification and Visualization of Internet Media Reports.” *Journal of the American Medical Informatics Association*, 2008.

**핵심 내용**

- HealthMap은 웹 뉴스, ProMED, WHO 등 다양한 웹 기반 감염병 보고를 자동 수집·분류·시각화한다.
- 감염병 조기 탐지를 위해 자동 분류, 지리적 매핑, 출처 통합을 수행한다.
- 평가에서 전체 자동 분류 정확도는 84%였고, ProMED alerts는 91%, Google News reports는 81%로 출처별 정확도 차이가 존재했다.

**WeP-Stock 적용 포인트**

- `disease_news_risk` 설계 근거로 사용하기 좋다.
- 출처마다 신뢰도를 다르게 주는 `source_weight`의 근거가 된다.
- WHO, CDC, KDCA, MFDS, ProMED 같은 공식·전문 출처는 일반 뉴스보다 높은 weight를 주는 것이 타당하다.
- 동일 감염병 사건이 여러 기사로 반복 보도될 수 있으므로 중복 제거 또는 novelty 보정이 필요하다.

---

### 4.4 최동엽·서용원 (2023) — 미디어 텍스트 분석 기반 공급망 리스크 모니터링

**논문**  
최동엽, 서용원. “미디어 텍스트 분석 기반의 공급망 리스크 모니터링 시스템의 개발.” *한국생산관리학회지*, 2023.

**핵심 내용**

- 뉴스 기사 분석을 활용해 공급망 리스크 관련 정보를 수집하고 리스크 유형을 분류하는 시스템을 개발했다.
- KoBERT 기반 공급망 리스크 관련 기사 필터링 모델을 사용했다.
- 수집된 기사에 대해 LDA 토픽 모델링으로 리스크 유형을 식별하고 학습 데이터를 구축했다.
- KoBERT 기반 공급망 리스크 관련 기사 필터링 정확도는 92.2%로 보고되었다.

**WeP-Stock 적용 포인트**

- 한국어 뉴스 기반 공급망 리스크 필터링 모델의 근거로 사용한다.
- 초기에는 LLM 분류를 사용하더라도, 이후 KoBERT 또는 한국어 BERT 계열 모델로 대체하는 로드맵을 제시할 수 있다.
- `news_filter.py`와 `news_llm_analyzer.py`의 구조적 근거가 된다.

---

### 4.5 Shahsavari et al. (2024) — Event Identification for Supply Chain Risk Management through News Analysis using LLMs

**논문**  
M. Shahsavari et al. “Event Identification for Supply Chain Risk Management Through News Analysis by Using Large Language Models.” *The Review of Socionetwork Strategies*, 2024.

**핵심 내용**

- 공급망 리스크 관리는 risk identification, assessment, treatment, monitoring의 반복적 과정이다.
- 이 연구는 risk event가 실제로 발생하기 전에 그 원인이 되는 contributing event를 식별하는 데 집중한다.
- 뉴스 데이터를 분석하고 LLM을 활용하여 리스크 이벤트와 원인 이벤트 간 연결을 만든다.

**WeP-Stock 적용 포인트**

- 단순히 “수액세트 부족”이라는 결과 이벤트만 찾는 것이 아니라, 그 전조인 항만 파업, 원자재 수출 제한, 전쟁, 감염병 확산 같은 contributing event를 먼저 잡는 구조를 설명할 수 있다.
- LLM이 이벤트 유형, 심각도, 국가, 품목 연관성, 원자재 연관성을 추출하는 근거로 사용한다.

---

### 4.6 Gelastopoulos & Keramydas (2025) — SCRM Text Mining Systematic Review

**논문**  
Georgios Gelastopoulos, Christos Keramydas. “A systematic review of text mining analytics for supply chain risk management using online data.” *Supply Chain Analytics*, 2025.

**핵심 내용**

- 온라인 데이터 기반 공급망 리스크 관리 텍스트마이닝 연구 33편을 검토했다.
- 뉴스와 소셜미디어는 실시간 공급망 가시성을 높이는 주요 데이터 원천으로 다뤄진다.
- 주요 기법은 감성분석, 토픽모델링, 분류모델, BERT류 모델 등이다.
- 텍스트마이닝은 risk identification, prediction, mitigation에 기여할 수 있다.

**WeP-Stock 적용 포인트**

- 뉴스 데이터를 공급망 리스크 feature로 사용하는 전체 방향의 근거로 사용한다.
- 단순 키워드 매칭에서 시작해 BERT/LLM 기반 분류로 발전시키는 방향을 정당화할 수 있다.

---

### 4.7 Ganguly & Kumar (2019) — Supply Chain Risk Assessment: Fuzzy AHP Approach

**논문**  
Kunal K. Ganguly, Gopal Kumar. “Supply Chain Risk Assessment: A Fuzzy AHP Approach.” *Operations and Supply Chain Management*, 2019.

**핵심 내용**

- 공급망 리스크 요인을 계층 구조로 정리하고 Fuzzy AHP를 이용해 평가한다.
- 공급망 리스크를 Extreme, High, Medium, Low 등으로 분류한다.
- 리스크 평가는 발생 가능성과 결과의 심각도를 함께 고려한다.
- 전문가 판단이 들어가는 다기준 의사결정 문제에 AHP/Fuzzy AHP가 적합하다는 근거를 제공한다.

**WeP-Stock 적용 포인트**

- 초기 weight는 전문가 판단으로 정하고, 이후 validation WAPE 기준으로 보정한다는 논리를 만들 수 있다.
- 논문 기반으로 “고정 weight가 아니라 의사결정 기준에 따른 계층형 weight”라고 설명할 수 있다.

---

### 4.8 Jaberidoost et al. (2015) — Pharmaceutical Supply Chain Risk Assessment using AHP and SAW

**논문**  
Mona Jaberidoost et al. “Pharmaceutical supply chain risk assessment in Iran using analytic hierarchy process (AHP) and simple additive weighting (SAW) methods.” *Journal of Pharmaceutical Policy and Practice*, 2015.

**핵심 내용**

- 의약품 공급망 위험을 AHP와 SAW 방식으로 평가했다.
- 위험의 우선순위, hazard, probability 등을 고려한다.
- 보건·의약품 공급망 맥락에서 AHP 기반 리스크 평가가 활용될 수 있음을 보여준다.

**WeP-Stock 적용 포인트**

- 의료기기와 완전히 동일한 도메인은 아니지만, 보건의료 공급망 리스크 평가의 가까운 참고문헌으로 사용할 수 있다.
- 서비스 레벨, 리드타임, 품목 중요도, 대체 가능성 등을 향후 weight에 넣는 근거로 확장 가능하다.

---

### 4.9 Benigno et al. (2022) — Global Supply Chain Pressure Index

**논문**  
Gianluca Benigno, Julian di Giovanni, Jan J. J. Groen, Adam I. Noble. “The GSCPI: A New Barometer of Global Supply Chain Pressures.” Federal Reserve Bank of New York Staff Reports, 2022.

**핵심 내용**

- 글로벌 공급망 압력을 측정하기 위해 운송비, PMI 배송시간, backlog, purchased stocks 등 27개 변수를 사용한다.
- 각 변수의 weight를 사람이 임의로 정하기보다 PCA를 통해 공통 요인을 추출한다.
- PCA는 각 지표가 다른 지표들과 얼마나 함께 움직이는지에 따라 weight를 반영한다.

**WeP-Stock 적용 포인트**

- 뉴스 리스크 weight도 초기값은 전문가 기준으로 두되, 최종적으로는 validation 성능 또는 통계적 방법으로 보정해야 한다는 근거가 된다.
- 원자재 가격, 운송비, 리드타임 같은 외부 변수 추가 시 데이터 기반 weight 보정 방향을 제시할 수 있다.

---

## 5. WeP-Stock 초기 weight 제안안

아래 값은 “논문에 그대로 나온 고정값”이 아니라, 위 레퍼런스들의 방법론을 기반으로 한 **초기 설정값**이다. 실제 값은 validation WAPE, 품목별 오차, 과거 리스크 이벤트 회고분석으로 보정해야 한다.

### 5.1 event_type_weight

| 이벤트 유형 | 예시 | 권장 초기 weight | 근거 |
|---|---|---:|---|
| 감염병 확산 | 코로나, 독감 대유행, 특정 감염병 확산 | 1.00 | HealthMap, 감염병 뉴스 감시 |
| 전쟁·무력 충돌 | 중동 전쟁, 우크라이나 전쟁, 군사 충돌 | 1.00 | GPR Index |
| 수출 제한·제재 | 의료물품 수출 제한, 원자재 수출 금지, 경제 제재 | 1.00 | GPR, 공급망 리스크 연구 |
| 항만·물류 마비 | 항만 폐쇄, 해상 운송 차질, 항공화물 지연 | 0.80 | GSCPI, 공급망 압력 지수 |
| 공장 폐쇄·생산 중단 | 의료기기 공장 화재, 생산 라인 중단 | 0.85 | 공급망 리스크 식별 연구 |
| 원자재 가격 급등·부족 | 플라스틱, 나프타, 금속 가격 급등 | 0.70 | GSCPI, 원자재·운송비 변수 |
| 정책·규제 불확실성 | 의료기기 규제 변경, 통관 기준 변경 | 0.55 | EPU Index |
| 일반 경기 불확실성 | 경기 침체, 시장 불안 | 0.30 | EPU Index |

---

### 5.2 source_weight

| 출처 유형 | 예시 | 권장 초기 weight | 이유 |
|---|---|---:|---|
| 국제·국가 공식기관 | WHO, CDC, KDCA, MFDS, 정부 발표 | 1.00 | 공식 출처, 높은 신뢰도 |
| 전문 감시망·전문기관 | ProMED, 산업협회, 공공 연구기관 | 0.90 | HealthMap에서 ProMED의 높은 분류 정확도 참고 |
| 주요 언론·통신사 | Reuters, AP, BBC, NYT, 국내 주요 언론 | 0.75 | 검증된 뉴스 출처 |
| 산업 전문 매체 | 의료기기·물류·원자재 전문 매체 | 0.65 | 품목 관련성은 높지만 공신력 차이 존재 |
| 지역 매체 | 특정 국가·지역 뉴스 | 0.50 | 현장성은 있으나 검증 필요 |
| 소셜미디어·커뮤니티 | X, 블로그, 커뮤니티 | 0.25 | 빠르지만 신뢰도 낮음 |

---

### 5.3 severity

| 심각도 | 기준 | 값 |
|---|---|---:|
| Critical | 실제 생산·물류·수요에 즉시 영향 | 1.00 |
| High | 단기적으로 수급 불안 가능성 큼 | 0.80 |
| Medium | 영향 가능성은 있으나 범위 제한 | 0.50 |
| Low | 직접 영향 불명확 | 0.20 |

LLM 또는 분류 모델이 severity를 예측할 때는 아래 정보를 함께 넣는다.

- 사건 발생 여부
- 영향을 받는 국가·지역
- 영향을 받는 원자재 또는 품목군
- 생산 중단, 물류 지연, 가격 급등, 수요 급증 중 무엇인지
- 기사 내에서 “shortage”, “disruption”, “export ban”, “surge”, “delay” 같은 표현이 있는지

---

### 5.4 confidence

| 신뢰도 | 기준 | 값 |
|---|---|---:|
| Very High | 공식 출처 + 명확한 품목/지역/수치 포함 | 1.00 |
| High | 주요 언론 + 사건 명확 | 0.80 |
| Medium | 사건은 있으나 품목 영향 불명확 | 0.60 |
| Low | 루머성, 출처 불명확, 추측성 표현 많음 | 0.35 |

모델 구현에서는 LLM의 자체 판단값만 쓰기보다 다음을 조합하는 것이 좋다.

```text
confidence = 0.5 * classifier_probability
           + 0.3 * source_confidence
           + 0.2 * extraction_completeness
```

---

### 5.5 item_relevance

| 품목 연관성 | 기준 | 값 |
|---|---|---:|
| Direct item match | 기사에 의료기기 품목명 또는 표준코드 직접 언급 | 1.00 |
| Item group match | 주사기, 카테터, 수액세트 등 품목군 언급 | 0.80 |
| Material match | 나프타, 플라스틱, 금속 등 원자재 매핑으로 연결 | 0.60 |
| Healthcare supply generic | “medical supplies”, “healthcare products” 수준 | 0.35 |
| General macro risk | 전쟁·경기침체 등 일반 리스크만 언급 | 0.15 |

---

### 5.6 exposure_weight

품목별 취약성을 반영하기 위한 weight이다. 같은 전쟁 뉴스라도 국내 조달 가능한 품목과 특정 국가 수입 의존 품목의 영향은 다르다.

권장 초기식:

```text
exposure_weight = 0.3 + 0.7 * exposure_score
```

`exposure_score`는 0~1 사이 값으로 둔다.

반영 가능한 변수:

- 수입 의존도
- 특정 국가 의존도
- 특정 원자재 의존도
- 평균 리드타임
- 대체품 존재 여부
- 최소 주문 단위
- 과거 품절 이력

현재 데이터가 부족하면 기본값은 `0.60`으로 두고, 의료기기-원자재 매핑 테이블이 고도화된 뒤 품목별로 보정한다.

---

### 5.7 recency_weight

뉴스의 시간 감쇠는 half-life 방식이 적합하다.

```text
recency_weight = exp(-ln(2) * age_days / half_life_days)
```

| 이벤트 유형 | 권장 half-life | 이유 |
|---|---:|---|
| 전쟁·수출 제한·제재 | 60일 | 장기화 가능성 큼 |
| 감염병 확산 | 30일 | 유행 상황이 빠르게 변함 |
| 공장 폐쇄·생산 중단 | 30일 | 복구 시점 확인 필요 |
| 항만·물류 지연 | 21~30일 | 물류 상황은 비교적 빠르게 변동 |
| 원자재 가격 급등 | 14~30일 | 가격 변동성이 큼 |
| 정책 불확실성 | 45일 | 정책 결정까지 시간이 걸림 |
| 일반 경기 불안 | 30일 | 직접 영향이 약하므로 짧게 설정 |

---

### 5.8 novelty_weight

동일 사건이 여러 언론에서 반복 보도되면 위험을 과대평가할 수 있다. 따라서 같은 사건 클러스터 내 중복 기사에는 novelty 보정을 적용한다.

권장식:

```text
novelty_weight = 1 / sqrt(1 + duplicate_count_same_event)
```

또는 다음 방식도 가능하다.

```text
cluster_score = max(article_score in same_event_cluster)
              + 0.2 * sum(other_article_scores in same_event_cluster)
```

적용 기준:

- 같은 사건명
- 같은 국가/지역
- 같은 품목/원자재
- 유사한 날짜
- 본문 임베딩 cosine similarity가 높은 기사

---

## 6. 최종 risk score 구조 제안

### 6.1 기사 단위 점수

```text
article_score
= event_type_weight
* severity
* confidence
* source_weight
* item_relevance
* exposure_weight
* recency_weight
* novelty_weight
```

---

### 6.2 월별·품목별 뉴스 리스크

```text
monthly_item_news_risk = 1 - exp(-sum(article_score))
```

---

### 6.3 공급망 리스크와 수요 급증 리스크 분리

현재는 disease, supply, commodity를 하나의 external risk로 합치고 있지만, 향후에는 아래처럼 분리하는 것이 좋다.

```text
demand_spike_risk = disease_news_risk
                  + policy_demand_risk

supply_disruption_risk = geopolitical_risk
                       + logistics_risk
                       + factory_shutdown_risk
                       + export_restriction_risk

commodity_risk = raw_material_price_risk
               + raw_material_shortage_risk
```

재고 정책에서는 다음처럼 사용할 수 있다.

```text
external_risk_score = 0.40 * demand_spike_risk
                    + 0.40 * supply_disruption_risk
                    + 0.20 * commodity_risk
```

단, 기존 코드와의 호환성을 우선하면 현재 구조를 유지하고 내부적으로만 세부 score를 만든 뒤 최종 disease/supply/commodity로 합산하는 방식이 안전하다.

---

## 7. 설정 파일 예시

`data/mapping/news_risk_weights.yaml` 예시:

```yaml
event_type_weight:
  infectious_disease_outbreak: 1.00
  war_or_armed_conflict: 1.00
  export_restriction_or_sanction: 1.00
  port_or_logistics_disruption: 0.80
  factory_shutdown: 0.85
  raw_material_shortage_or_price_spike: 0.70
  policy_regulation_uncertainty: 0.55
  general_economic_uncertainty: 0.30

source_weight:
  official_government_or_international_org: 1.00
  expert_monitoring_network: 0.90
  major_news_agency: 0.75
  industry_media: 0.65
  local_media: 0.50
  social_media_or_blog: 0.25

severity_weight:
  critical: 1.00
  high: 0.80
  medium: 0.50
  low: 0.20

item_relevance_weight:
  direct_item_match: 1.00
  item_group_match: 0.80
  material_match: 0.60
  healthcare_supply_generic: 0.35
  general_macro_risk: 0.15

recency_half_life_days:
  war_or_export_restriction: 60
  infectious_disease_outbreak: 30
  factory_shutdown: 30
  logistics_disruption: 21
  raw_material_price_spike: 21
  policy_uncertainty: 45
  general_economic_uncertainty: 30

risk_aggregation:
  monthly_transform: "1 - exp(-sum(article_score))"
  novelty_method: "1 / sqrt(1 + duplicate_count_same_event)"
```

---

## 8. 구현 우선순위

1. `news_risk_weights.yaml` 생성
2. 뉴스 기사별 LLM 분석 결과에 아래 필드 저장
   - `event_type`
   - `severity`
   - `confidence`
   - `source_type`
   - `country`
   - `related_material`
   - `related_device_group`
   - `event_date`
   - `event_cluster_id`
3. `article_score` 계산
4. 같은 사건 클러스터 중복 보정
5. 월별·품목별 `monthly_item_news_risk` 생성
6. 기존 `outputs/news_risk_scores.csv`에 반영
7. Model A/B/C validation WAPE 비교로 weight 보정

---

## 9. 검증 방법

weight는 처음부터 정답을 맞히는 방식이 아니라, 다음 기준으로 반복 보정해야 한다.

### 9.1 모델 성능 기반 검증

현재 프로젝트 구조상 대표 모델은 validation WAPE가 가장 낮은 모델로 선택된다. 따라서 다음을 비교한다.

```text
Model A: 과거 사용량 feature만 사용
Model B: 과거 사용량 + 뉴스 리스크
Model C: 과거 사용량 + 뉴스 리스크 + 원자재 리스크
```

검증 기준:

- Model B가 Model A보다 validation WAPE를 낮추는가?
- Model C가 Model B보다 validation WAPE를 낮추는가?
- 특정 품목군에서만 성능이 좋아지는가?
- 뉴스 리스크가 실제로 사용량 급증 또는 공급 불안 시점보다 선행하는가?

### 9.2 이벤트 회고 검증

과거 주요 사건을 기준으로 리스크 점수가 실제로 상승했는지 확인한다.

예시:

- 코로나19 확산 시기
- 러시아-우크라이나 전쟁
- 중동 전쟁 및 나프타 공급 불안
- 특정 원자재 가격 급등
- 특정 의료물품 품귀 기사 발생 시점

### 9.3 사람 검수 샘플링

Baker et al.의 EPU 방식처럼 일부 기사 샘플을 사람이 직접 검토해 자동 분류 결과를 확인한다.

검수 항목:

- 이 기사가 실제 공급망/수요 리스크와 관련 있는가?
- 이벤트 유형 분류가 맞는가?
- 품목 또는 원자재 매핑이 맞는가?
- severity가 과대/과소 평가되지 않았는가?

---

## 10. Notion에 넣을 최종 문장

WeP-Stock의 뉴스 리스크 weight는 특정 논문에서 제공하는 고정 표를 그대로 사용하는 방식이 아니라, 뉴스 기반 리스크 지수 연구와 공급망 리스크 평가 연구를 조합해 설계한다. Baker, Bloom & Davis의 EPU Index와 Caldara & Iacoviello의 GPR Index는 뉴스 기사 빈도와 이벤트 카테고리를 기반으로 불확실성·지정학 리스크를 정량화한 대표 사례이다. HealthMap은 감염병 관련 웹 뉴스와 공식 출처를 자동 분류·통합해 조기 경보에 활용한 사례이며, 출처별 신뢰도 차이를 weight에 반영할 수 있는 근거를 제공한다. 또한 공급망 리스크 분야에서는 미디어 텍스트 분석, KoBERT/LLM 기반 이벤트 분류, Fuzzy AHP 기반 리스크 우선순위화 연구가 존재한다. 따라서 본 프로젝트에서는 `event_type_weight`, `source_weight`, `severity`, `confidence`, `item_relevance`, `exposure_weight`, `recency_weight`, `novelty_weight`를 곱해 기사 단위 점수를 만들고, 월별·품목별로 `1 - exp(-sum(article_score))` 형태로 집계한다. 초기 weight는 문헌 기반으로 설정하되, 최종 weight는 validation WAPE와 과거 이벤트 회고분석을 통해 보정한다.

---

## 11. 참고문헌

1. Baker, S. R., Bloom, N., & Davis, S. J. (2016). “Measuring Economic Policy Uncertainty.” *Quarterly Journal of Economics*, 131(4), 1593–1636. https://academic.oup.com/qje/article-abstract/131/4/1593/2468873
2. Caldara, D., & Iacoviello, M. (2022). “Measuring Geopolitical Risk.” *American Economic Review*, 112(4), 1194–1225. https://www.aeaweb.org/articles?id=10.1257/aer.20191823
3. Freifeld, C. C., et al. (2008). “HealthMap: Global Infectious Disease Monitoring through Automated Classification and Visualization of Internet Media Reports.” *Journal of the American Medical Informatics Association*, 15(2), 150–157. https://pubmed.ncbi.nlm.nih.gov/18096908/
4. 최동엽, 서용원. (2023). “미디어 텍스트 분석 기반의 공급망 리스크 모니터링 시스템의 개발.” *한국생산관리학회지*, 34(4), 453–471. https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART003021877
5. Shahsavari, M., et al. (2024). “Event Identification for Supply Chain Risk Management Through News Analysis by Using Large Language Models.” *The Review of Socionetwork Strategies*, 18, 255–278. https://link.springer.com/article/10.1007/s12626-024-00169-z
6. Gelastopoulos, G., & Keramydas, C. (2025). “A systematic review of text mining analytics for supply chain risk management using online data.” *Supply Chain Analytics*, 12, 100167. https://www.sciencedirect.com/science/article/pii/S2949863525000676
7. Ganguly, K. K., & Kumar, G. (2019). “Supply Chain Risk Assessment: A Fuzzy AHP Approach.” *Operations and Supply Chain Management*, 12(1), 1–13. https://journal.oscm-forum.org/journal/journal/download/20190205000116_Paper_1_Vol._12_No_._1%2C_2019_.pdf
8. Jaberidoost, M., et al. (2015). “Pharmaceutical supply chain risk assessment in Iran using analytic hierarchy process (AHP) and simple additive weighting (SAW) methods.” *Journal of Pharmaceutical Policy and Practice*, 8, 9. https://www.tandfonline.com/doi/full/10.1186/s40545-015-0029-3
9. Benigno, G., di Giovanni, J., Groen, J. J. J., & Noble, A. I. (2022). “The GSCPI: A New Barometer of Global Supply Chain Pressures.” Federal Reserve Bank of New York Staff Reports, No. 1017. https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr1017.pdf
10. GDELT Project. “GDELT Event Database Codebook V2.0.” https://data.gdeltproject.org/documentation/GDELT-Event_Codebook-V2.0.pdf
