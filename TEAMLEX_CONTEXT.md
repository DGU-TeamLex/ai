# TEAMLEX Context

> 2026-07-11 기준 변경: `device/` 데이터 의존성은 제거되었습니다. 현재 파이프라인은 `raw_stock/*.DAT`만 사용하며 `MED_DEVICE_5`와 `SIDO`는 더 이상 모델 키가 아닙니다.

작성일: 2026-07-06  
프로젝트: WeP-Stock AI Service  
GitHub: https://github.com/DGU-TeamLex/ai

## 1. 현재 상태 요약

WeP-Stock AI 저장소는 전체 백엔드가 아니라 **AI 학습, 예측, 위험 점수 생성, 재고 권고 서빙**만 담당하도록 정리했다.

현재 완료 처리된 큰 작업:

| 구분 | 상태 | 내용 |
| --- | --- | --- |
| AI MVP 파이프라인 | 처리 완료 | 전처리, feature 생성, 학습, 예측, 평가, 재고 권고 배치 흐름 구현 |
| AI-only scope 정리 | 처리 완료 | 인증, 사용자, 업로드, 알림, 재배치 등 백엔드 책임 제거 |
| FastAPI AI serving | 처리 완료 | `/api/v1/ai/*` 중심의 AI 결과 조회 API 구성 |
| 모델 코드 리팩토링 | 처리 완료 | 모델 관련 코드를 `src/modeling/` 패키지로 이동 |
| 루트 모델 wrapper 삭제 | 처리 완료 | `src/train_model.py`, `src/predict.py` 등 중복 wrapper 삭제 |
| README/협업 문서 정리 | 처리 완료 | 모듈 구조, 실행법, 브랜치 정책, 책임 범위 문서화 |
| GitHub 이슈/PR 템플릿 | 처리 완료 | 이슈 템플릿, PR 템플릿, 협업 규칙 추가 |
| dev 브랜치 병합 | 처리 완료 | `feature/inventory-forecast-mvp`를 `dev`에 fast-forward 병합 |
| 뉴스 리스크 세부 weight | 처리 완료 | `news_risk_weights.yaml` 추가 및 scorer 반영. 단, 아직 `dev` 미병합 |

현재 남은 주요 작업:

| 구분 | 상태 | 내용 |
| --- | --- | --- |
| 실제 뉴스 API 연동 | 진행 예정 | GDELT, NewsAPI, Event Registry 등 collector 교체 |
| LLM/분류모델 기반 뉴스 분석 | 진행 예정 | 현재 키워드 기반 분석기를 LLM 또는 KoBERT/BERT 계열로 고도화 |
| 뉴스 weight validation 보정 | 진행 예정 | validation WAPE, 과거 이벤트 회고분석 기반 weight 보정 |
| 의료기기-원자재 매핑 고도화 | 진행 예정 | 임시 mapping을 실제 품목/원자재/공급망 기준으로 보정 |
| 실제 원자재 가격 API 연동 | 진행 예정 | 원유, 라텍스, 금속, 운송비 등 API 또는 내부 데이터 연동 |
| API 통합 테스트 | 진행 예정 | `/health`, `/forecasts`, `/recommend-order` fixture 테스트 추가 |
| Streamlit 대시보드 개선 | 진행 예정 | 운영자가 보기 쉬운 필터, 위험 근거, 평가 지표 화면 개선 |
| 실제 재고/리드타임 연동 | 진행 예정 | 현재 재고량, 발주 이력, 리드타임 기반 재고 정책 고도화 |

## 2. 작업 위치와 Git 상태

주의:

- `/home/user/teamlex`는 현재 작업 파일이 있는 로컬 폴더지만, 실제 git repo로 인식되지 않는다.
- GitHub 커밋/푸시는 `/tmp/teamlex_ai_repo`에 다시 클론한 실제 repo에서 진행했다.
- 이 `TEAMLEX_CONTEXT.md`는 현재 `/home/user/teamlex` 로컬 문서다.

브랜치 정책:

```text
main: 완성본만 병합
dev: 개발 통합 브랜치
feat/*: 기능 작업 브랜치, PR 대상은 dev
```

현재 확인한 실제 repo 상태:

```text
repo path: /tmp/teamlex_ai_repo
branch: feature/news-risk-weight-config
HEAD: 5befa47 feat: 뉴스 리스크 세부 가중치 설정 반영
tracking: origin/feature/news-risk-weight-config
```

중요 브랜치 상태:

```text
origin/dev: 4ff5f0f refactor: 모델링 코드 패키지 구조 정리
origin/feature/news-risk-weight-config: 5befa47 feat: 뉴스 리스크 세부 가중치 설정 반영
origin/feature/inventory-forecast-mvp: 4ff5f0f refactor: 모델링 코드 패키지 구조 정리
origin/main: de39c5b Initial commit
```

처리 완료된 PR:

```text
PR #7: MERGED
URL: https://github.com/DGU-TeamLex/ai/pull/7
base: dev
head: feature/inventory-forecast-mvp
```

최근 주요 커밋:

```text
5befa47 feat: 뉴스 리스크 세부 가중치 설정 반영
4ff5f0f refactor: 모델링 코드 패키지 구조 정리
6cb55ca refactor: AI 학습 및 서빙 책임만 남기도록 정리
adb5c4f feat: WeP-Stock 기능 명세서 기반 API 정렬
a20d720 docs: dev 브랜치 협업 규칙 반영
c8f6ed0 feat: 재고 예측 MVP 파이프라인 추가
de39c5b Initial commit
```

생성된 주요 GitHub 이슈:

```text
#2 feat: 실제 뉴스 API 수집 모듈 연동
#3 feat: 의료기기-원자재 매핑 테이블 고도화
#4 feat: 원자재 가격 API 연동
#5 test: API 조회 및 권장 발주량 통합 테스트 추가
#6 feat: Streamlit 대시보드 사용성 개선
```

## 3. 현재 코드 구조

표준 실행 경로:

```text
src.main
-> preprocessing
-> news/commodity risk scoring
-> feature_engineering
-> modeling.training
-> modeling.prediction
```

핵심 구조:

```text
src/
  config.py                    경로, 학습 기간, 모델 옵션, 재고 정책 설정
  main.py                      전체 배치 파이프라인 진입점
  data_loader.py               원본 CSV 탐색, chunk 단위 로딩, 월별 사용량 집계
  preprocessing.py             usage_monthly.csv 생성
  features.py                  lag/rolling/계절성/외부 feature 생성 함수
  feature_engineering.py       모델 학습용 feature_table 생성
  utils.py                     공통 유틸리티

  modeling/
    baseline.py                baseline 예측값 생성
    metrics.py                 MAE, RMSE, MAPE, SMAPE, WAPE 계산
    training.py                Model A/B/C 학습
    prediction.py              test 예측, 대표 모델 선택, 권장 재고량 생성
    evaluation.py              평가 리포트 생성
    inventory_policy.py        안전재고, 리스크 버퍼, 권장 재고/발주량 계산

  news/
    news_collector.py          뉴스 수집 모듈
    news_filter.py             의료기기/공급망 관련 뉴스 필터링
    news_llm_analyzer.py       이벤트 유형, 국가, 심각도, 신뢰도 분석
    news_risk_scorer.py        품목별 뉴스 리스크 점수 생성

  commodity/
    commodity_collector.py     원자재 가격 수집 모듈
    commodity_features.py      가격 feature 생성
    commodity_risk_scorer.py   품목별 원자재 리스크 점수 생성

  serving/
    api.py                     FastAPI AI serving API
    schemas.py                 API 요청 스키마

  dashboard/
    app.py                     Streamlit 결과 확인 화면
```

처리 완료된 리팩토링:

- 모델 학습, 예측, 평가, 재고 정책 코드를 `src/modeling/` 아래로 이동했다.
- 기존 루트 모델 wrapper/레거시 파일은 삭제했다.

삭제 처리된 파일:

```text
src/baseline_model.py
src/metrics.py
src/inventory_policy.py
src/train_model.py
src/predict.py
src/evaluate.py
src/train_eval.py
src/preprocess.py
```

## 4. 데이터 처리 기준

원본 데이터 구조:

```text
device/
  DEVICE_YYYY.../
    DEVICE_YYYYMM....csv
```

입력 설정:

```python
RAW_DATA_DIR = PROJECT_ROOT / "device"
RAW_FILE_PATTERN = "**/*.csv"
PUBLIC_HEALTH_CODE = 7
```

전처리 기준:

- `YOYANG_CLSFC_CD_ADJ == 7`인 보건소 데이터만 사용한다.
- 월별, 시도별, 품목코드별로 집계한다.
- 최종 학습 단위는 `year_month x SIDO x MED_DEVICE_5`이다.
- 같은 시도와 같은 품목코드를 공유하는 행은 총 처방량 기준으로 합산한다.

주요 집계 컬럼:

```text
total_use
total_count
total_amount
patient_count
elderly_use_ratio
sex_1_use_ratio
sex_2_use_ratio
in_use_ratio
out_use_ratio
```

## 5. Feature Engineering

처리 완료된 feature:

- 월, 분기, 계절성
- 최근 1, 2, 3, 6, 12개월 사용량 lag
- 3, 6, 12개월 rolling mean/std
- 전년동월 사용량
- 전년동월 대비 증가율
- 뉴스 리스크 점수
- 원자재 리스크 점수

데이터 누수 방지:

다음 달 사용량을 예측하므로 예측 대상 월의 실제 사용량 집계값은 모델 feature에서 제외한다.

제외 대상:

```text
total_use
total_count
patient_count
total_amount
```

## 6. 모델 구성

학습 파일:

```text
src/modeling/training.py
```

처리 완료된 모델 variant:

```text
Model A: 과거 사용량 feature만 사용
Model B: 과거 사용량 feature + 뉴스 리스크
Model C: 과거 사용량 feature + 뉴스 리스크 + 원자재 리스크
```

기본 알고리즘:

- LightGBM
- LightGBM 미설치 시 RandomForest fallback

데이터 split:

```text
train:      2023-12까지
validation: 2024-01부터 2024-12까지
test:       2025-01부터
```

대표 모델 선택 기준:

- validation WAPE가 가장 낮은 모델을 대표 모델로 선택한다.

baseline 의미:

- ML 모델과 비교하기 위한 단순 기준 예측이다.
- rolling mean, 전년동월, 단순 평균 등을 사용한다.
- ML 모델이 baseline보다 항상 좋아야 하는 것은 아니지만, 차이를 평가하고 설명해야 한다.

## 7. 평가 기준

평가 결과 위치:

```text
outputs/model_validation_report.csv
outputs/evaluation_report.csv
```

처리 완료된 평가 지표:

| 지표 | 의미 |
| --- | --- |
| MAE | 실제값과 예측값의 절대 오차 평균 |
| RMSE | 큰 오차에 더 민감한 제곱근 평균 제곱 오차 |
| MAPE | 실제값 대비 절대 오차 비율 |
| SMAPE | 실제값과 예측값 규모를 함께 고려한 비율 오차 |
| WAPE | 전체 사용량 대비 총 절대 오차 비율 |

운영 판단에서는 WAPE를 우선 기준으로 본다. 품목별 사용량 규모 차이가 크기 때문에 전체 물량 기준의 오차 비율이 재고 판단에 더 적합하다.

## 8. 재고 정책

재고 정책 파일:

```text
src/modeling/inventory_policy.py
```

현재 처리 완료된 계산식:

```text
external_risk_score = 0.4 * disease_news_risk
                    + 0.3 * supply_news_risk
                    + 0.3 * commodity_risk

safety_stock = predicted_usage * 0.20
risk_buffer = predicted_usage * external_risk_score * 0.50

recommended_stock = predicted_usage + safety_stock + risk_buffer
```

설정 위치:

```text
SAFETY_STOCK_RATE = 0.20
MAX_RISK_BUFFER_RATE = 0.50
```

진행 예정:

- 실제 리드타임 반영
- 서비스 레벨 반영
- 품목 중요도 반영
- 대체 가능성 반영
- 최소 주문 단위 반영

## 9. 뉴스 리스크 Weight

처리 완료된 내용:

- `news_risk_weight_references_teamlex.md` 내용을 기반으로 뉴스 리스크 세부 weight 설계를 반영했다.
- `data/mapping/news_risk_weights.yaml`을 추가했다.
- `src/config.py`에 `NEWS_RISK_WEIGHT_PATH`를 추가했다.
- `src/news/news_llm_analyzer.py`에서 이벤트 타입을 세분화했다.
- `src/news/news_risk_scorer.py`에서 설정 파일 기반 article score 계산을 구현했다.
- 기존 모델 feature 호환을 위해 최종 출력 컬럼은 유지했다.

추가된 설정 파일:

```text
data/mapping/news_risk_weights.yaml
```

기사 단위 점수:

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

월별/품목별 집계:

```text
monthly_item_news_risk = 1 - exp(-sum(article_score))
```

출력 컬럼:

```text
disease_news_risk
supply_news_risk
material_news_risk
total_news_risk
```

세분화된 이벤트 타입:

```text
infectious_disease_outbreak
war_or_armed_conflict
export_restriction_or_sanction
port_or_logistics_disruption
factory_shutdown
raw_material_shortage_or_price_spike
policy_regulation_uncertainty
general_economic_uncertainty
```

뉴스 weight 작업 상태:

```text
branch: feature/news-risk-weight-config
commit: 5befa47 feat: 뉴스 리스크 세부 가중치 설정 반영
push: 완료
dev merge: 미완료
```

검증 결과:

```text
conda run -n teamlex python -m compileall src
score_news_risk() sample 실행
```

sample 뉴스 기준 `score_news_risk()`는 135개 row를 생성했다.

남은 뉴스 리스크 작업:

- 실제 뉴스 API 연동
- LLM 또는 KoBERT/BERT 기반 이벤트 분석으로 교체
- source_type, event_cluster_id, exposure_score를 실제 수집/매핑 데이터에 포함
- validation WAPE와 과거 이벤트 회고분석으로 weight 보정

## 10. AI Serving API

처리 완료된 FastAPI 실행:

```bash
uvicorn src.serving.api:app --reload
```

주요 API:

```text
GET  /health
GET  /api/v1/ai/health
GET  /api/v1/ai/artifacts

POST /api/v1/ai/train
POST /api/v1/ai/forecasts/run
GET  /api/v1/ai/forecasts
GET  /api/v1/ai/forecasts/{institutionId}/{standardCode}
GET  /api/v1/ai/forecasts/eval

GET  /api/v1/ai/supply-risk
GET  /api/v1/ai/supply-risk/{itemGroupId}

GET  /api/v1/ai/inventory-policy
GET  /api/v1/ai/inventory-policy/{institutionId}/{standardCode}
GET  /api/v1/ai/order-recommendations
POST /api/v1/ai/recommend-order
```

Legacy compatibility:

```text
GET  /predictions
POST /recommend-order
```

Serving 정책:

- 요청 시점에 외부 뉴스 API, 원자재 API, LLM을 직접 호출하지 않는다.
- 배치 산출물을 미리 생성하고 API는 precomputed output을 조회한다.
- 백엔드는 인증/인가를 담당하고 AI 서비스는 내부 API처럼 호출되는 구조를 가정한다.

## 11. 실행 방법

환경:

```bash
conda activate teamlex
pip install -r requirements.txt
```

전체 배치:

```bash
python -m src.main
```

단계별 실행:

```bash
python -m src.preprocessing
python -m src.news.news_risk_scorer
python -m src.commodity.commodity_risk_scorer
python -m src.feature_engineering
python -m src.modeling.training
python -m src.modeling.prediction
python -m src.modeling.evaluation
```

API 서버:

```bash
uvicorn src.serving.api:app --reload
```

Streamlit:

```bash
streamlit run src/dashboard/app.py
```

검증 명령:

```bash
conda run -n teamlex python -m compileall src
conda run -n teamlex python -c "from src.serving.api import health; print(health())"
```

## 12. 산출물과 커밋 정책

주요 산출물:

```text
data/processed/usage_monthly.csv
data/processed/model_dataset.csv
outputs/news_risk_scores.csv
outputs/commodity_risk_scores.csv
outputs/feature_table.csv
outputs/model_validation_report.csv
outputs/predictions.csv
outputs/evaluation_report.csv
models/model_a_usage_only.pkl
models/model_b_news.pkl
models/model_c_news_commodity.pkl
models/manifest.json
```

GitHub에 올리지 않는 항목:

```text
device/
data/raw/
data/processed/
outputs/
models/
.env
```

커밋 가능한 항목:

- source code
- docs
- issue/PR templates
- sample mapping seed
- `data/mapping/*.csv`
- `data/mapping/*.yaml`

## 13. 앞으로의 작업

진행 예정 목록:

1. `feature/news-risk-weight-config`를 PR로 올리고 `dev`에 병합
2. 실제 뉴스 API 연동
3. 뉴스 이벤트 분석을 LLM 또는 분류 모델로 교체
4. 뉴스 weight validation 보정
5. 의료기기-원자재 매핑 테이블 고도화
6. 실제 원자재 가격 API 연동
7. 공급망 리스크와 수요 급증 리스크를 재고 정책에서 분리 반영
8. API 통합 테스트 추가
9. Streamlit 대시보드 사용성 개선
10. 실제 재고량, 리드타임, 발주 이력 데이터 연동
11. 모델 성능 비교 자동 리포트 고도화

이미 처리 완료된 항목:

```text
AI MVP 파이프라인 구현
AI-only scope 정리
모델 코드 src/modeling 리팩토링
루트 모델 wrapper 삭제
dev 브랜치 병합
뉴스 리스크 세부 weight 설정 파일화
뉴스 리스크 scorer에 article_score 및 saturation aggregation 반영
```

구현 위치 가이드:

| 작업 | 위치 |
| --- | --- |
| 모델 종류 추가 | `src/modeling/training.py` |
| 예측 결과 포맷 변경 | `src/modeling/prediction.py` |
| 평가 지표 추가 | `src/modeling/metrics.py`, `src/modeling/evaluation.py` |
| 재고 정책 변경 | `src/modeling/inventory_policy.py` |
| 뉴스 API 연동 | `src/news/news_collector.py` |
| 뉴스 LLM 분석 | `src/news/news_llm_analyzer.py` |
| 뉴스 weight 고도화 | `src/news/news_risk_scorer.py`, `data/mapping/news_risk_weights.yaml` |
| 원자재 API 연동 | `src/commodity/commodity_collector.py` |
| 품목-원자재 매핑 | `data/mapping/device_material_mapping.csv` |
| AI serving API 변경 | `src/serving/api.py`, `src/serving/schemas.py` |

## 14. 마지막 검증 결과

모델 리팩토링 후 검증:

```bash
conda run -n teamlex python -m compileall src
```

통과했다.

뉴스 weight 반영 후 검증:

```bash
conda run -n teamlex python -m compileall src
conda run -n teamlex python -c "from src.news.news_risk_scorer import score_news_risk; df=score_news_risk(); print(len(df))"
conda run -n teamlex python -c "from src.serving.api import health; print(health())"
```

통과했다. `score_news_risk()`는 sample 뉴스 기준 135개 row를 생성했다.
