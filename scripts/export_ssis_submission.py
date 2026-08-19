"""한국사회보장정보원 제출용 보고서 묶음을 한글 파일명으로 내보낸다.

저장소 내부 산출물 이름은 코드와 테스트의 호환성을 위해 유지하고, 사람이 전달받는
제출 폴더에서만 한글 파일명을 사용한다. 외부위험 실험 결과는 아직 생성되지 않았으면
건너뛰고 목록 파일에 미생성 상태로 남긴다.
"""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ExportItem:
    source: Path
    korean_stem: str
    required: bool = True


EXPORT_ITEMS = (
    ExportItem(
        Path("docs/2026-08-17_04_SSIS_EXPERIMENT_RESULT_REPORT.md"),
        "01_의료재고_예측_실험_결과보고서",
    ),
    ExportItem(
        Path("docs/2026-08-17_06_SSIS_RESULT_TABLE_TEMPLATE.md"),
        "02_의료재고_예측_실험_결과표",
    ),
    ExportItem(
        Path("docs/2026-08-11_01_METHODOLOGY_EVIDENCE.md"),
        "03_의료재고_예측_방법론_및_참고문헌_근거대장",
    ),
    ExportItem(
        Path("outputs/external_shock_experiment_report.csv"),
        "04_외부위험_모델_평가결과",
        required=False,
    ),
    ExportItem(
        Path("outputs/external_shock_experiment_summary.json"),
        "05_외부위험_모델_요약",
        required=False,
    ),
    ExportItem(
        Path("outputs/external_shock_experiment_predictions.parquet"),
        "06_외부위험_모델_예측값",
        required=False,
    ),
)


def export_submission(output_date: str, output_dir: Path | None = None) -> Path:
    destination = output_dir or ROOT / "exports" / f"정보원_제출용_{output_date}"
    destination.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, object] = {
        "제출물_생성일": output_date,
        "설명": "저장소 내부 원본을 변경하지 않고 제출용 한글 파일명으로 복사한 묶음",
        "파일": [],
    }

    missing_required: list[str] = []
    file_entries: list[dict[str, object]] = []
    for item in EXPORT_ITEMS:
        source = ROOT / item.source
        exported_name = f"{item.korean_stem}_{output_date}{source.suffix}"
        entry: dict[str, object] = {
            "원본": item.source.as_posix(),
            "제출파일": exported_name,
            "필수": item.required,
            "상태": "생성됨" if source.is_file() else "원본_미생성",
        }
        if source.is_file():
            shutil.copy2(source, destination / exported_name)
        elif item.required:
            missing_required.append(item.source.as_posix())
        file_entries.append(entry)

    manifest["파일"] = file_entries
    manifest_path = destination / f"00_제출파일_목록_{output_date}.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if missing_required:
        missing = ", ".join(missing_required)
        raise FileNotFoundError(f"필수 보고서 원본이 없습니다: {missing}")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(
        description="정보원 제출용 보고서와 결과 파일을 한글 파일명으로 복사합니다."
    )
    parser.add_argument("--date", default=date.today().isoformat(), help="파일명 기준일")
    parser.add_argument("--output-dir", type=Path, help="제출 폴더 경로")
    args = parser.parse_args()

    destination = export_submission(args.date, args.output_dir)
    print(destination)


if __name__ == "__main__":
    main()
