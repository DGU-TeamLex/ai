# Contributing Guide

## Branch Strategy

- `main`: stable branch. Merge only through PR.
- `feature/<short-name>`: feature work.
- `fix/<short-name>`: bug fixes.
- `docs/<short-name>`: documentation-only changes.
- `experiment/<short-name>`: model experiments that may not be production-ready.

## Commit Convention

Use concise conventional commits:

```text
feat: add commodity risk scoring module
fix: prevent leakage in rolling features
docs: update batch run instructions
refactor: split training pipeline into modules
test: add feature table validation checks
chore: update dependencies
```

## Pull Request Rules

Each PR should include:

- Purpose and scope
- Changed modules
- Validation command and result
- Data/model artifact impact
- Known limitations

Do not commit raw medical data, generated outputs, trained model files, API keys, or local environment files.

## Data Policy

The following paths are intentionally ignored:

```text
device/
data/raw/
data/processed/
outputs/
models/
```

Mapping seed files under `data/mapping/*.csv` may be committed because they are editable project configuration, not raw usage data.

## Review Checklist

- No data leakage in demand features
- No service-time external API/LLM calls
- Batch outputs have stable CSV schemas
- `python -m src.main` works in the `teamlex` conda environment
- API and dashboard read precomputed `outputs/predictions.csv`

