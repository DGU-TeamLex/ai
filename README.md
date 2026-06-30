# Medical Device Inventory Forecast MVP

보건소 의료기기 월별 사용량 기반 다음 달 사용량 예측과 외부 위험 점수 기반 권장 재고량 산출을 위한 MVP입니다.

## Current Scope

현재 구조는 문서의 모듈식 단계에 맞춰 분리되어 있습니다.

```text
1. 의료기기 사용량 CSV 로드
2. 월별/품목별 feature 생성
3. baseline 평균 모델 구현
4. LightGBM 또는 RandomForest fallback 모델 구현
5. sample news 기반 뉴스 위험 점수 생성
6. sample commodity 기반 원자재 위험 점수 생성
7. Model A/B/C 성능 비교
8. predictions.csv 생성
9. FastAPI로 predictions.csv 조회
10. Streamlit 결과 화면
```

실제 뉴스 API, LLM, 원자재 API는 collector/analyzer 모듈만 교체하면 됩니다.

## Data Layout

현재 원본 데이터는 아래 구조에서 읽습니다.

```text
device/DEVICE_YYYY.../DEVICE_YYYYMM....csv
```

입력 위치는 `src/config.py`의 `RAW_DATA_DIR`, `RAW_FILE_PATTERN`에서 변경할 수 있습니다.

## Setup

```bash
conda activate teamlex
pip install -r requirements.txt
```

## Batch Run

전체 배치 파이프라인:

```bash
python -m src.main
```

단계별 실행:

```bash
python -m src.preprocessing
python -m src.news.news_risk_scorer
python -m src.commodity.commodity_risk_scorer
python -m src.feature_engineering
python -m src.train_model
python -m src.predict
python -m src.evaluate
```

## Serving

```bash
uvicorn src.serving.api:app --reload
```

Endpoints:

```text
GET /health
GET /predictions?yyyymm=2025-01&item_code=B0004&sido=41
POST /recommend-order
```

## Dashboard

```bash
streamlit run src/dashboard/app.py
```

## Outputs

```text
data/processed/usage_monthly.csv
outputs/feature_table.csv
outputs/news_risk_scores.csv
outputs/commodity_risk_scores.csv
outputs/model_validation_report.csv
outputs/predictions.csv
outputs/evaluation_report.csv
models/model_a_usage_only.pkl
models/model_b_news.pkl
models/model_c_news_commodity.pkl
models/manifest.json
```

## Model Variants

```text
Model A: 과거 사용량 feature만 사용
Model B: 과거 사용량 feature + 뉴스 위험 점수
Model C: 과거 사용량 feature + 뉴스 위험 점수 + 원자재 위험 점수
```

기본 모델 알고리즘은 LightGBM입니다. LightGBM이 설치되어 있지 않으면 RandomForest로 fallback합니다.

## Leakage Policy

다음 달 사용량을 예측하므로 현재 월의 원본 집계값은 모델 feature에서 제외합니다.

```text
total_use
total_count
patient_count
total_amount
```

모델에는 lag, rolling, 전년동월, 외부 위험 점수처럼 예측 시점에 사용할 수 있는 feature만 넣습니다.

