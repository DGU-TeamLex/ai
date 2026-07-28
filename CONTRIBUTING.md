# Contributing Guide

## Branch Strategy

- `main`: 완성 버전만 반영하는 안정 브랜치입니다. MVP가 충분히 검증되기 전까지 직접 병합하지 않습니다.
- `dev`: 개발 통합 브랜치입니다. AI 학습, 실험, AI 서빙 API 개선은 우선 `dev`로 PR을 올립니다.
- `feature/<short-name>`: 기능 작업 브랜치입니다. 기본 PR 대상은 `dev`입니다.
- `fix/<short-name>`: 버그 수정 브랜치입니다. 기본 PR 대상은 `dev`입니다.
- `docs/<short-name>`: 문서 수정 브랜치입니다. 기본 PR 대상은 `dev`입니다.
- `experiment/<short-name>`: 모델 실험 브랜치입니다. 결과가 검증되기 전까지 `main`에 병합하지 않습니다.

## Commit Convention

커밋 메시지는 prefix는 영어 convention을 유지하고, 내용은 한글로 작성합니다.

```text
feat: 원자재 위험 점수 모듈 추가
fix: rolling feature 데이터 누수 방지
docs: 배치 실행 방법 업데이트
refactor: 학습 파이프라인 모듈 분리
test: feature table 검증 테스트 추가
chore: 의존성 업데이트
```

## Pull Request Rules

Each PR should include:

- Purpose and scope
- Changed modules
- Validation command and result
- Data/model artifact impact
- Known limitations

Do not commit raw medical data, generated outputs, trained model files, API keys, or local environment files.

## Merge Policy

- 작업 브랜치 PR은 기본적으로 `dev`를 대상으로 생성합니다.
- `dev`에서 기능 검증, 배치 실행, AI API/결과 대시보드 확인을 진행합니다.
- 배포 또는 제출 가능한 완성 버전이 되었을 때만 `dev`에서 `main`으로 PR을 생성합니다.
- `main`에 직접 push하지 않습니다.

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
- AI API and result dashboard read precomputed `outputs/predictions.csv`
