# Collaboration Rules

## Working Principles

This project is a batch-first MVP for medical device demand forecasting and inventory recommendation.

The system should keep these boundaries clear:

- Demand forecasting predicts next-month usage.
- Risk scoring converts news and commodity signals into numeric features.
- Inventory policy converts prediction and risk into recommended stock/order quantities.
- Serving reads precomputed results instead of calling external APIs in request time.

## Module Ownership

```text
src/data_loader.py              raw usage CSV loading
src/preprocessing.py            monthly usage aggregation
src/feature_engineering.py      feature table assembly
src/baseline_model.py           baseline predictors
src/train_model.py              Model A/B/C training
src/predict.py                  batch prediction output
src/inventory_policy.py         stock/order policy
src/news/                       news collection, filtering, scoring
src/commodity/                  commodity collection, features, scoring
src/serving/                    FastAPI lookup API
src/dashboard/                  Streamlit dashboard
```

## Development Flow

1. Create an issue for each feature, bug, experiment, or documentation task.
2. Work on a branch matching the issue type.
3. Keep PRs small enough to review in one pass.
4. Run the relevant command before PR.
5. Record output files and metric changes in the PR.

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

- Raw CSVs in `device/`
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

