# Contributing Guide

## Branch Strategy

- `main`: release branch. Do not push directly; merge only from `dev` through a release PR.
- `dev`: persistent development integration branch. Normal PRs target `dev`.
- `feat/<short-name>`: feature work created from the latest `dev`.
- `fix/<short-name>`: bug fixes created from the latest `dev`.
- `docs/<short-name>`: documentation-only changes created from the latest `dev`.
- `experiment/<short-name>`: model experiments created from the latest `dev` that may not be production-ready.

After a topic branch is merged into `dev`, delete that topic branch. Keep `dev` and
`main` permanently. Promote `dev` to `main` only for a reviewed release.

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

The normal flow is `feat/*` (or another topic branch) -> `dev`. A `dev` -> `main`
PR is reserved for a release and must not contain unrelated experimental work.

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

