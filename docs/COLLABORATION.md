# Collaboration Rules

## Working Principles

This project is a batch-first AI service for raw stock consumption forecasting and inventory recommendation.

The system should keep these boundaries clear:

- Demand forecasting predicts next-month usage.
- Risk scoring converts news and commodity signals into numeric features.
- Inventory policy converts prediction and risk into recommended stock/order quantities.
- AI serving reads precomputed results instead of calling external APIs in request time.
- Product backend concerns such as auth, user management, upload workflow, alerts, and relocation approval live outside this repository.

## Module Ownership

```text
src/data_loader.py              quote-aware raw_stock DAT loading
src/item_normalization.py       item name, group, subtype, and specification candidates
src/item_enrichment.py          representative item aggregation and official master matching
src/item_classification.py      evidence-gated approved classifications and review queue
src/item_integrated_pipeline.py classification, material, and parent-concept integration
src/preprocessing.py            monthly stock and consumption aggregation
src/feature_engineering.py      feature table assembly
src/material_pipeline.py        material, supply-risk, and demand-trigger candidates
src/modeling/baseline.py        baseline predictors
src/modeling/training.py        Model A/B/C training
src/modeling/prediction.py      batch prediction output
src/modeling/classified_prediction.py approved subtype-level prediction aggregation
src/modeling/evaluation.py      prediction evaluation report
src/modeling/metrics.py         regression metrics
src/modeling/inventory_policy.py stock/order policy
src/news/                       news collection, filtering, scoring
src/commodity/                  commodity collection, features, scoring
src/serving/                    AI serving API
src/dashboard/                  AI result inspection dashboard
```

Model logic should live under `src/modeling/`. Do not add new model training, prediction, evaluation, or inventory policy code directly under the `src/` root.

## Development Flow

1. 기능, 버그, 실험, 문서 작업은 먼저 issue로 등록합니다.
2. 작업 유형에 맞는 브랜치를 생성합니다.
3. 작업 브랜치는 `dev`를 대상으로 PR을 생성합니다.
4. `dev`에서 기능 검증과 통합 테스트를 진행합니다.
5. 완성 버전이 되었을 때만 `dev`에서 `main`으로 PR을 생성합니다.

## Branch Rules

- `main`: 최종 완성본만 유지합니다.
- `dev`: MVP 개발과 기능 통합 기준 브랜치입니다.
- `feature/*`: 기능 구현 브랜치입니다. PR 대상은 `dev`입니다.
- `fix/*`: 버그 수정 브랜치입니다. PR 대상은 `dev`입니다.
- `docs/*`: 문서 수정 브랜치입니다. PR 대상은 `dev`입니다.
- `experiment/*`: 모델 실험 브랜치입니다. 검증 전에는 `main`에 병합하지 않습니다.

`main`에는 직접 push하지 않습니다.

## Required Validation Commands

For full batch changes:

```bash
conda activate teamlex
python -m src.main
```

For syntax/import checks:

```bash
conda activate teamlex
python -m compileall src
```

For API lookup logic:

```bash
conda activate teamlex
python -c "from src.serving.api import health; print(health())"
```

## Artifact Rules

Commit source code, documentation, templates, and mapping configuration.

Do not commit:

- Raw DAT files in `raw_stock/`
- Processed datasets
- Model pickle files
- Output reports/predictions
- API keys or `.env` files

## Model Experiment Rules

When changing model logic, document:

- Feature changes
- Split period
- Baseline comparison
- MAE, RMSE, MAPE, SMAPE, WAPE
- Whether the change improves or degrades baseline

ML models do not need to beat baselines in every PR, but the reason should be visible in the PR description.
