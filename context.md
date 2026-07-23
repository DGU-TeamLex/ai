# TeamLex / WeP-Stock AI Handoff Context

> 2026-07-11 기준 변경: 아래 과거 기록 중 `device/`, `MED_DEVICE_5`, `SIDO`를 입력으로 사용하는 내용은 폐기되었습니다. 현재 유일한 학습 입력은 `raw_stock/*.DAT`이며, 학습 단위는 `월 x 기관 x 부서 x 내부 물품코드`입니다.
>
> 2026-07-22 책임 경계 변경: DB 스키마와 일반 운영 트랜잭션은 backend가 담당하지만,
> 품질 게이트를 통과한 AI 배치 산출물의 DML 적재는 AI가 담당합니다. 현재 첫 대상은
> `demand_class`, `mu_corrected`이며 명시적 기관 매핑 없이는 적재하지 않습니다.

작성일: 2026-07-11  
목적: 새 대화에서 WeP-Stock AI Service 작업을 바로 이어가기 위한 압축 컨텍스트

## 1. 프로젝트 한 줄 요약

WeP-Stock AI Service는 보건소 의료기기 사용량 데이터를 기반으로 다음 달 사용량을 예측하고, 뉴스/원자재 리스크를 반영해 권장 재고량을 산출하는 AI 학습 및 서빙 시스템이다.

이 저장소는 전체 백엔드가 아니라 AI 영역만 담당한다.

담당 범위:

- 의료기기 사용량 데이터 전처리
- 수요 예측 feature 생성
- baseline 및 ML 모델 학습/평가
- 뉴스/원자재 기반 외부 리스크 점수 생성
- 예측 사용량, 안전재고, 권장 재고량 산출
- FastAPI 기반 AI 결과 서빙
- Streamlit 기반 로컬 결과 확인
- 품질 검증된 AI 배치 산출물의 DB DML 적재

제외 범위:

- 인증/인가/사용자/권한
- 파일 업로드/import batch 관리
- 기관/중앙 운영 대시보드 API
- 재고 입출고 트랜잭션
- 발주 승인, 재배치 승인, 알림 관리
- DB 스키마 변경, 운영 트랜잭션, 감사 로그, row-level permission

## 2. 작업 위치와 Git 주의사항

중요:

- `/home/user/teamlex`는 실제 git repo로 인식되지 않는다.
- `/home/user/teamlex/.git`은 이전/외부 프로젝트 흔적처럼 보이며 사용하지 않는다.
- GitHub 작업은 `/tmp/teamlex_ai_repo`에 `DGU-TeamLex/ai.git`을 클론해서 진행해왔다.
- 새 대화에서 작업을 시작하면 반드시 브랜치와 커밋을 먼저 확인한다.

사용자 선호:

- `main`에 직접 push하지 않는다.
- 개발 중인 내용은 `dev` 또는 feature branch에서 진행한다.
- commit/issue/PR 제목은 `feat:`, `refactor:` 같은 prefix는 유지하되 설명은 한글로 작성해도 된다.
- 작업 전에는 항상 현재 브랜치와 최신 커밋을 확인한다.

권장 시작 명령:

```bash
git clone https://github.com/DGU-TeamLex/ai.git /tmp/teamlex_ai_repo
cd /tmp/teamlex_ai_repo
git branch -a
git log --oneline --decorate --graph --all -12
git status --short --branch
```

## 3. 현재 GitHub 상태

2026-07-11에 원격 저장소를 다시 클론해 확인한 상태:

```text
origin/main: de39c5b Initial commit
origin/dev: 4ff5f0f refactor: 모델링 코드 패키지 구조 정리
origin/feature/inventory-forecast-mvp: 4ff5f0f refactor: 모델링 코드 패키지 구조 정리
origin/feature/news-risk-weight-config: 5befa47 feat: 뉴스 리스크 세부 가중치 설정 반영
```

PR 상태:

```text
PR #7: MERGED
URL: https://github.com/DGU-TeamLex/ai/pull/7
base: dev
head: feature/inventory-forecast-mvp
```

현재 주의:

- `feature/inventory-forecast-mvp`는 `dev`에 병합 완료.
- `feature/news-risk-weight-config`는 원격 push 완료 상태지만 아직 `dev`에는 병합되지 않았다.
- 다음 작업으로 `feature/news-risk-weight-config` PR 생성 및 `dev` 병합을 진행하면 된다.

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

## 4. 완료된 작업

처리 완료:

- AI MVP 파이프라인 구현
- AI-only scope 정리
- WeP-Stock 기능 명세서 기반 AI serving API 정렬
- 백엔드 책임 제거
- GitHub issue/PR template 추가
- 협업 규칙 문서화
- `feature/inventory-forecast-mvp`를 `dev`에 병합
- 모델 관련 코드 `src/modeling/` 패키지로 리팩토링
- 기존 루트 모델 wrapper/레거시 파일 삭제
- 뉴스 리스크 세부 weight 설정 파일 추가
- 뉴스 scorer에 article score 및 saturation aggregation 반영

삭제 처리된 모델/레거시 파일:

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

## 5. 현재 코드 구조

표준 실행 흐름:

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
  config.py
  main.py
  data_loader.py
  preprocessing.py
  features.py
  feature_engineering.py
  utils.py

  modeling/
    baseline.py
    metrics.py
    training.py
    prediction.py
    evaluation.py
    inventory_policy.py

  news/
    news_collector.py
    news_filter.py
    news_llm_analyzer.py
    news_risk_scorer.py

  commodity/
    commodity_collector.py
    commodity_features.py
    commodity_risk_scorer.py

  serving/
    api.py
    schemas.py

  dashboard/
    app.py
```

## 6. 데이터 처리 기준

원본 데이터 구조:

```text
device/
  DEVICE_YYYY.../
    DEVICE_YYYYMM....csv
```

현재 전처리 기준:

- `YOYANG_CLSFC_CD_ADJ == 7`인 보건소 데이터만 사용
- 월별, 시도별, 품목코드별 집계
- 최종 학습 단위는 `year_month x SIDO x MED_DEVICE_5`
- 같은 시도와 품목코드를 공유하는 행은 총 처방량 기준으로 합산

주요 설정:

```python
RAW_DATA_DIR = PROJECT_ROOT / "device"
RAW_FILE_PATTERN = "**/*.csv"
PUBLIC_HEALTH_CODE = 7
```

## 7. 모델 구성

모델 위치:

```text
src/modeling/training.py
```

모델 variant:

```text
Model A: 과거 사용량 feature만 사용
Model B: 과거 사용량 feature + 뉴스 리스크
Model C: 과거 사용량 feature + 뉴스 리스크 + 원자재 리스크
```

알고리즘:

- 기본: LightGBM
- fallback: RandomForest

데이터 split:

```text
train:      2023-12까지
validation: 2024-01부터 2024-12까지
test:       2025-01부터
```

대표 모델 선택:

- validation WAPE가 가장 낮은 모델

평가 지표:

```text
MAE
RMSE
MAPE
SMAPE
WAPE
```

운영 판단에서는 WAPE를 우선한다.

## 8. 뉴스 리스크 Weight 작업

기반 문서:

```text
/home/user/teamlex/news_risk_weight_references_teamlex.md
```

추가된 설정 파일:

```text
data/mapping/news_risk_weights.yaml
```

관련 커밋:

```text
5befa47 feat: 뉴스 리스크 세부 가중치 설정 반영
branch: feature/news-risk-weight-config
push: 완료
dev merge: 미완료
```

구현된 article score:

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

기존 모델과의 호환을 위해 최종 출력 컬럼은 유지:

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

검증 결과:

```text
conda run -n teamlex python -m compileall src
score_news_risk() sample 실행
```

sample 뉴스 기준 `score_news_risk()`는 135개 row를 생성했다.

## 9. AI Serving API

FastAPI 실행:

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

- API 요청 시점에 외부 뉴스 API, 원자재 API, LLM을 직접 호출하지 않는다.
- 배치 산출물을 미리 생성하고 API는 precomputed output을 조회한다.
- 백엔드는 인증/인가를 담당하고 AI 서비스는 내부 API처럼 호출되는 구조를 가정한다.

## 10. 실행 방법

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

검증:

```bash
conda run -n teamlex python -m compileall src
conda run -n teamlex python -c "from src.serving.api import health; print(health())"
```

## 11. 산출물과 Git 정책

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

```text
source code
docs
issue/PR templates
data/mapping/*.csv
data/mapping/*.yaml
```

## 12. 다음 작업 제안

가장 가까운 다음 작업:

1. `feature/news-risk-weight-config` PR 생성
2. `dev`와 conflict 확인
3. PR 또는 fast-forward로 `dev` 병합
4. 실제 뉴스 API collector 연동
5. LLM 또는 KoBERT/BERT 기반 뉴스 이벤트 분석으로 교체
6. 뉴스 weight를 validation WAPE와 과거 이벤트 회고분석으로 보정
7. 의료기기-원자재 매핑 테이블 고도화
8. 실제 원자재 가격 API 연동
9. API 통합 테스트 추가
10. Streamlit 대시보드 개선

## 13. 새 대화 시작 시 바로 줄 요청 예시

```text
이 context.md를 기준으로 WeP-Stock AI Service 작업을 이어가줘.
먼저 현재 GitHub 브랜치와 최신 커밋을 확인하고,
feature/news-risk-weight-config를 dev에 병합할 수 있는지 conflict를 확인한 뒤,
필요하면 PR 생성 또는 dev 병합까지 진행해줘.
main에는 직접 push하지 말고, 커밋/PR 메시지는 prefix는 유지하되 내용은 한글로 작성해줘.
```
