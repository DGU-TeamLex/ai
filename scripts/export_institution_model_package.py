"""Export the model evidence package requested by the data-providing institution.

The exporter does not modify source artifacts. Large tables are streamed into
UTF-8-SIG CSV.GZ files so the package can be transferred without placing
institution data or trained models in Git.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import importlib.metadata
import json
import shutil
import sys
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

import joblib
import pandas as pd
import pyarrow as pa
import pyarrow.csv as pa_csv
import pyarrow.parquet as pq


UTF8_BOM = b"\xef\xbb\xbf"


@dataclass(frozen=True)
class SourcePaths:
    standard_mapping: Path
    standard_report: Path
    feature_table: Path
    latest_predictions: Path
    backtest_predictions: Path
    main_model: Path
    comparison_model: Path
    model_manifest: Path
    hyperparameters: Path
    evaluation: Path
    validation: Path
    bias_experiment: Path
    normalization_audit: Path


def source_paths(root: Path) -> SourcePaths:
    snapshot = root / "outputs" / "experiment_snapshots" / "demand_only_20260817"
    return SourcePaths(
        standard_mapping=root / "data" / "processed" / "stock_standard_item_mapping.parquet",
        standard_report=root / "outputs" / "stock_standard_item_mapping_report.json",
        feature_table=root / "outputs" / "stock_feature_table.parquet",
        latest_predictions=root / "outputs" / "stock_predictions.csv",
        backtest_predictions=root / "outputs" / "stock_backtest_predictions.csv",
        main_model=snapshot / "models" / "stock_model_a_usage_only.pkl",
        comparison_model=snapshot / "models" / "stock_model_a_usage_tweedie.pkl",
        model_manifest=snapshot / "models" / "stock_manifest.json",
        hyperparameters=root / "outputs" / "tuned_hyperparameters_stock_model_a_usage_only.json",
        evaluation=root / "outputs" / "stock_evaluation_report.csv",
        validation=root / "outputs" / "stock_model_validation_report.csv",
        bias_experiment=root / "outputs" / "forecast_bias_inventory_backtest_report.csv",
        normalization_audit=root / "outputs" / "meta_code_normalization_research_audit.json",
    )


def require_sources(paths: SourcePaths) -> None:
    missing = [str(value) for value in vars(paths).values() if not value.exists()]
    if missing:
        raise FileNotFoundError("Required source artifacts are missing:\n" + "\n".join(missing))


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_csv(path: Path, rows: Iterable[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as sink:
        writer = csv.DictWriter(sink, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parquet_to_csv_gz(source: Path, destination: Path, batch_size: int = 25_000) -> tuple[int, int]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    parquet = pq.ParquetFile(source)
    rows = 0
    first = True
    with gzip.open(destination, "wb", compresslevel=4) as compressed:
        compressed.write(UTF8_BOM)
        for batch in parquet.iter_batches(batch_size=batch_size):
            buffer = pa.BufferOutputStream()
            pa_csv.write_csv(
                batch,
                buffer,
                write_options=pa_csv.WriteOptions(include_header=first),
            )
            compressed.write(buffer.getvalue().to_pybytes())
            rows += batch.num_rows
            first = False
    return rows, len(parquet.schema_arrow.names)


def csv_to_csv_gz(source: Path, destination: Path, chunk_size: int = 8 * 1024 * 1024) -> tuple[int, int]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with source.open("rb") as raw, gzip.open(destination, "wb", compresslevel=4) as compressed:
        first = raw.read(3)
        if first == UTF8_BOM:
            compressed.write(first)
        else:
            compressed.write(UTF8_BOM)
            compressed.write(first)
            rows += first.count(b"\n")
        for chunk in iter(lambda: raw.read(chunk_size), b""):
            compressed.write(chunk)
            rows += chunk.count(b"\n")
    columns = len(pd.read_csv(source, nrows=0).columns)
    return max(rows - 1, 0), columns


def preview_parquet(source: Path, destination: Path, rows: int) -> None:
    parquet = pq.ParquetFile(source)
    batches = parquet.iter_batches(batch_size=rows)
    frame = next(batches).to_pandas()
    frame.head(rows).to_csv(destination, index=False, encoding="utf-8-sig")


def preview_csv(source: Path, destination: Path, rows: int) -> None:
    pd.read_csv(source, nrows=rows).to_csv(destination, index=False, encoding="utf-8-sig")


def model_record(role: str, model_path: Path, evaluation: pd.DataFrame) -> dict:
    artifact = joblib.load(model_path)
    model = artifact["model"]
    key = f"{artifact['name']}_pred"
    metric_row = evaluation.loc[evaluation["model"].eq(key)]
    metrics = metric_row.iloc[0].to_dict() if not metric_row.empty else {}
    return {
        "역할": role,
        "모델명": artifact["name"],
        "알고리즘": artifact.get("algorithm", type(model).__name__),
        "목적함수": artifact.get("objective", ""),
        "특성수": len(artifact.get("feature_cols", [])),
        "평가행수": metrics.get("N", ""),
        "WAPE_pct": metrics.get("WAPE", ""),
        "BIAS_pct": metrics.get("BIAS_PCT", ""),
        "MAE": metrics.get("MAE", ""),
        "RMSE": metrics.get("RMSE", ""),
        "모델파일": model_path.name,
        "설명": (
            "최종 WAPE 기준 주모델"
            if role == "주모델"
            else "과소예측 편향을 비교하기 위한 Tweedie 도전모델"
        ),
    }


def describe_column(dataset: str, column: str) -> str:
    exact = {
        "data_period": "자료 구간: historical 또는 current",
        "local_item_key": "기관 내 품목 연결키",
        "raw_item_name": "원자료의 품목명",
        "standard_item_key": "표준화된 품목 식별키",
        "standard_item_definition_key": "품목군·세부유형·규격·단위를 결합한 정의키",
        "standardization_match_method": "표준 품목을 연결한 방법",
        "standardization_confidence": "표준화 연결 신뢰도(0~1)",
        "historical_training_eligible": "과거자료 학습 포함 가능 여부",
        "year_month": "관측 월",
        "forecast_origin_month": "예측을 생성한 기준 월",
        "institution_code": "익명화 기관 코드",
        "department": "부서명",
        "item_code": "기관 내 품목 코드",
        "stock_item_key": "기관·부서·품목을 결합한 재고 시계열 키",
        "demand_qty": "모델 학습에 사용한 월 수요량",
        "target_usage": "다음 달 실제 사용량인 학습 목표값",
        "actual_usage": "평가 월 실제 사용량",
        "predicted_usage": "최종 선택 규칙에 따른 예측 사용량",
        "primary_model": "해당 행에 적용한 예측모델",
        "safety_stock": "수요 불확실성을 반영한 안전재고",
        "target_stock": "검토주기와 리드타임을 반영한 목표재고",
        "recommended_order": "품질조건 적용 후 권고 발주량",
        "prediction_type": "backtest 또는 future 예측 구분",
    }
    if column in exact:
        return exact[column]
    if column.startswith("lag_"):
        return "이전 월 수요량 시차 특성"
    if column.startswith("rolling_"):
        return "최근 월 이동통계 특성"
    if column.endswith("_risk") or "risk_score" in column:
        return "외부 또는 공급 위험 점수(연구용 포함)"
    if column.endswith("_pred"):
        return "해당 기준모형 또는 개발모형의 예측값"
    if "lead_time" in column:
        return "리드타임 또는 리드타임 정책 관련 변수"
    if "stock" in column:
        return "재고 상태 또는 재고정책 계산 변수"
    return f"{dataset}에 포함된 분석 변수"


def schema_rows(paths: SourcePaths) -> list[dict]:
    datasets: list[tuple[str, list[tuple[str, str]]]] = []
    for label, path in [
        ("품목표준화", paths.standard_mapping),
        ("모델입력", paths.feature_table),
    ]:
        schema = pq.ParquetFile(path).schema_arrow
        datasets.append((label, [(field.name, str(field.type)) for field in schema]))
    for label, path in [
        ("평가구간_모델출력", paths.backtest_predictions),
        ("최신월_모델출력", paths.latest_predictions),
    ]:
        frame = pd.read_csv(path, nrows=100)
        datasets.append((label, [(name, str(dtype)) for name, dtype in frame.dtypes.items()]))
    return [
        {
            "데이터셋": dataset,
            "열이름": column,
            "자료형": dtype,
            "설명": describe_column(dataset, column),
        }
        for dataset, columns in datasets
        for column, dtype in columns
    ]


def copy_source_code(source_root: Path, destination: Path) -> None:
    selected = [
        "src/config.py",
        "src/data_loader.py",
        "src/item_normalization.py",
        "src/item_integrated_pipeline.py",
        "src/feature_engineering.py",
        "src/modeling/training.py",
        "src/modeling/prediction.py",
        "src/modeling/evaluation.py",
        "src/modeling/metrics.py",
        "src/modeling/standardized_history.py",
        "src/modeling/inventory_policy.py",
        "src/modeling/order_quality_gate.py",
        "src/modeling/combination_experiment.py",
        "scripts/analysis/forecast_bias_inventory_backtest.py",
        "scripts/analysis/meta_code_normalization_research_audit.py",
        "scripts/analysis/material_mapping_standard_axis.py",
        "scripts/analysis/syringe_supply_risk_inventory_impact.py",
        "pipelines/item_material/scripts/build_meta_code_excel.py",
        "requirements.txt",
        ".env.example",
    ]
    for relative in selected:
        source = source_root / relative
        if source.exists():
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    exporter_target = destination / "scripts" / Path(__file__).name
    exporter_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), exporter_target)


def runtime_rows() -> list[dict]:
    packages = ["python", "pandas", "numpy", "pyarrow", "scikit-learn", "lightgbm", "joblib"]
    rows = []
    for package in packages:
        version = sys.version.split()[0] if package == "python" else importlib.metadata.version(package)
        rows.append({"구성요소": package, "버전": version})
    return rows


def write_readme(destination: Path, stats: dict[str, tuple[int, int]]) -> None:
    text = f"""# 보건의료정보부 요청자료

생성일: {date.today().isoformat()}

## 1. 품목 표준화 결과

- `01_품목표준화/품목표준화_전체.csv.gz`: 전체 {stats['standard'][0]:,}행
- `01_품목표준화/품목표준화_미리보기.csv`: Excel 확인용 일부 행
- 표준화 결과는 원래 품목명과 표준 품목키, 품목군, 세부유형, 규격, 단위, 연결방법과 신뢰도를 포함합니다.

## 2. 개발 모델

- 주모델: LightGBM regression_l1
- 비교모델: LightGBM Tweedie
- `.pkl` 파일에는 학습모형, 전처리 정보, 특성 목록과 검증지표가 함께 저장되어 있습니다.
- `.pkl` 파일은 Python 코드 실행 권한을 가질 수 있으므로 출처와 SHA-256을 확인한 뒤 신뢰된 환경에서만 불러오십시오.

## 3. 모델 입력·출력

- 모델입력 전체: {stats['input'][0]:,}행, {stats['input'][1]:,}열
- 평가구간 모델출력 전체: {stats['backtest'][0]:,}행, {stats['backtest'][1]:,}열
- 최신월 모델출력 전체: {stats['latest'][0]:,}행, {stats['latest'][1]:,}열
- 전체 자료는 UTF-8-SIG CSV를 gzip으로 압축한 `.csv.gz`입니다. Python, R, 데이터베이스 도구에서 바로 읽을 수 있습니다.
- Excel은 1,048,576행 제한이 있으므로 전체 자료 대신 각 `미리보기.csv`를 먼저 확인하십시오.

## 해석 범위

- 기관 코드는 익명화 코드입니다.
- 뉴스·원자재·Module C 변수는 연구 검토 열을 포함합니다. 외부위험이 수요예측에 직접 반영됐다는 뜻은 아닙니다.
- 30일 등 리드타임 값은 실제 입고일로 학습한 품목별 예측값이 아니라 계약 기준 또는 시나리오 값일 수 있습니다.
- 최신월 결과는 자동 발주 지시가 아니라 사람 검토를 위한 권고값입니다.

## 무결성 확인

`06_명세/SHA256SUMS.csv`의 해시를 사용해 전달 전후 파일이 동일한지 확인할 수 있습니다.
"""
    destination.write_text(text, encoding="utf-8")


def build_zip(package_dir: Path) -> Path:
    zip_path = package_dir.with_suffix(".zip")
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=str(Path(package_dir.name) / path.relative_to(package_dir)))
    return zip_path


def export_package(source_root: Path, output_dir: Path, preview_rows: int) -> tuple[Path, Path]:
    paths = source_paths(source_root)
    require_sources(paths)
    output_dir.mkdir(parents=True, exist_ok=False)

    standard_dir = output_dir / "01_품목표준화"
    model_dir = output_dir / "02_개발모델"
    io_dir = output_dir / "03_모델입출력"
    validation_dir = output_dir / "04_검증자료"
    code_dir = output_dir / "05_소스코드"
    spec_dir = output_dir / "06_명세"
    for directory in [standard_dir, model_dir, io_dir, validation_dir, code_dir, spec_dir]:
        directory.mkdir(parents=True)

    stats: dict[str, tuple[int, int]] = {}
    stats["standard"] = parquet_to_csv_gz(paths.standard_mapping, standard_dir / "품목표준화_전체.csv.gz")
    preview_parquet(paths.standard_mapping, standard_dir / "품목표준화_미리보기.csv", preview_rows)
    shutil.copy2(paths.standard_report, standard_dir / "품목표준화_검증요약.json")
    shutil.copy2(paths.normalization_audit, standard_dir / "정규화_메타코드_연구검증.json")
    print(f"[1/6] 품목 표준화 결과 생성: {stats['standard'][0]:,}행", flush=True)

    shutil.copy2(paths.main_model, model_dir / "주모델_LightGBM_L1.pkl")
    shutil.copy2(paths.comparison_model, model_dir / "비교모델_LightGBM_Tweedie.pkl")
    shutil.copy2(paths.model_manifest, model_dir / "모델매니페스트.json")
    shutil.copy2(paths.hyperparameters, model_dir / "주모델_학습설정.json")
    evaluation = pd.read_csv(paths.evaluation)
    write_csv(
        model_dir / "모델목록_성능.csv",
        [
            model_record("주모델", paths.main_model, evaluation),
            model_record("비교모델", paths.comparison_model, evaluation),
        ],
        ["역할", "모델명", "알고리즘", "목적함수", "특성수", "평가행수", "WAPE_pct", "BIAS_pct", "MAE", "RMSE", "모델파일", "설명"],
    )
    write_csv(model_dir / "실행환경.csv", runtime_rows(), ["구성요소", "버전"])
    print("[2/6] 주모델·비교모델 및 실행환경 복사", flush=True)

    stats["input"] = parquet_to_csv_gz(paths.feature_table, io_dir / "모델입력_전체.csv.gz")
    preview_parquet(paths.feature_table, io_dir / "모델입력_미리보기.csv", preview_rows)
    print(f"[3/6] 모델 입력 생성: {stats['input'][0]:,}행", flush=True)
    stats["backtest"] = csv_to_csv_gz(paths.backtest_predictions, io_dir / "평가구간_모델출력_전체.csv.gz")
    preview_csv(paths.backtest_predictions, io_dir / "평가구간_모델출력_미리보기.csv", preview_rows)
    print(f"[4/6] 평가구간 출력 생성: {stats['backtest'][0]:,}행", flush=True)
    stats["latest"] = csv_to_csv_gz(paths.latest_predictions, io_dir / "최신월_모델출력_전체.csv.gz")
    preview_csv(paths.latest_predictions, io_dir / "최신월_모델출력_미리보기.csv", preview_rows)
    print(f"[5/6] 최신월 출력 생성: {stats['latest'][0]:,}행", flush=True)

    for source, name in [
        (paths.evaluation, "최종평가_모델별지표.csv"),
        (paths.validation, "검증구간_모델별지표.csv"),
        (paths.bias_experiment, "편향완화_혼합실험.csv"),
    ]:
        pd.read_csv(source).to_csv(validation_dir / name, index=False, encoding="utf-8-sig")

    copy_source_code(source_root, code_dir)
    write_csv(spec_dir / "데이터사전.csv", schema_rows(paths), ["데이터셋", "열이름", "자료형", "설명"])
    write_readme(output_dir / "README_제출자료설명.md", stats)

    package_report = {
        "version": "institution-model-package-v1.0",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_git_commit": _git_commit(source_root),
        "row_counts": {key: value[0] for key, value in stats.items()},
        "column_counts": {key: value[1] for key, value in stats.items()},
        "package_sha256_excludes": ["06_명세/SHA256SUMS.csv", "zip container"],
    }
    (spec_dir / "생성보고서.json").write_text(json.dumps(package_report, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest_rows = []
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.name in {"파일목록.csv", "SHA256SUMS.csv"}:
            continue
        relative = path.relative_to(output_dir).as_posix()
        manifest_rows.append(
            {
                "파일": relative,
                "크기_bytes": path.stat().st_size,
                "SHA256": sha256_file(path),
                "설명": "전체자료" if "전체" in path.name else "제출근거 또는 실행자료",
            }
        )
    write_csv(spec_dir / "파일목록.csv", manifest_rows, ["파일", "크기_bytes", "SHA256", "설명"])

    checksum_rows = []
    for path in sorted(output_dir.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.csv":
            checksum_rows.append({"SHA256": sha256_file(path), "파일": path.relative_to(output_dir).as_posix()})
    write_csv(spec_dir / "SHA256SUMS.csv", checksum_rows, ["SHA256", "파일"])

    zip_path = build_zip(output_dir)
    print(f"[6/6] 파일목록·SHA-256·ZIP 생성: {zip_path.name}", flush=True)
    return output_dir, zip_path


def _git_commit(root: Path) -> str | None:
    import subprocess

    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--preview-rows", type=int, default=1000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_root = args.source_root.resolve()
    output_dir = (
        args.output_dir.resolve()
        if args.output_dir
        else source_root / "exports" / f"정보원_모델제출자료_{args.date}"
    )
    package_dir, zip_path = export_package(source_root, output_dir, args.preview_rows)
    print(json.dumps({"package_dir": str(package_dir), "zip_path": str(zip_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
