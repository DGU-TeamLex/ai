"""검증된 SSIS 최종보고서와 재현 산출물을 한글 파일명으로 묶는다."""

from __future__ import annotations

import argparse
import hashlib
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


EXPORT_ITEMS = (
    ExportItem(
        Path("docs/2026-08-18_02_SSIS_FINAL_INTEGRATED_REPORT.md"),
        "01_의료재고_예측_통합최종보고서",
    ),
    ExportItem(
        Path("docs/2026-08-18_03_SSIS_FINAL_RESULT_TABLE.md"),
        "02_의료재고_예측_최종결과표",
    ),
    ExportItem(
        Path("docs/2026-08-11_01_METHODOLOGY_EVIDENCE.md"),
        "03_방법론_및_참고문헌_근거대장",
    ),
    ExportItem(
        Path("docs/2026-08-17_03_GITHUB_ISSUE_COMMENT_ALIGNMENT.md"),
        "04_깃허브_이슈코멘트_반영대장",
    ),
    ExportItem(
        Path("outputs/external_shock_experiment_report.csv"),
        "05_외부위험_모델_최종평가결과",
    ),
    ExportItem(
        Path("outputs/external_shock_experiment_summary.json"),
        "06_외부위험_모델_최종요약",
    ),
    ExportItem(
        Path("outputs/external_shock_experiment_predictions.parquet"),
        "07_외부위험_모델_예측값",
    ),
    ExportItem(
        Path("outputs/forecast_bias_inventory_backtest_report.csv"),
        "08_편향보정_재고정책_통합결과",
    ),
    ExportItem(
        Path("outputs/forecast_bias_inventory_backtest_forecast_grid.csv"),
        "09_L1_Tweedie_혼합비_전수결과",
    ),
    ExportItem(
        Path("outputs/forecast_bias_inventory_backtest_monthly.csv"),
        "10_30일리드타임_월별재고성과",
    ),
    ExportItem(
        Path("outputs/forecast_bias_inventory_backtest_summary.json"),
        "11_편향보정_재고정책_실험요약",
    ),
    ExportItem(
        Path("outputs/forecast_bias_inventory_backtest_policy_proposal.json"),
        "12_고정50대50_정책제안_미적용",
    ),
    ExportItem(
        Path("outputs/syringe_supply_risk_inventory_impact.csv"),
        "13_주사기_PP_공급위험_재고영향_결과",
    ),
    ExportItem(
        Path("outputs/syringe_supply_risk_inventory_summary.json"),
        "14_주사기_PP_공급위험_재고영향_요약",
    ),
    ExportItem(
        Path("docs/2026-08-19_03_RESEARCH_RESULT_SYNTHESIS.md"),
        "15_의료재고_예측_연구결과_종합",
    ),
    ExportItem(
        Path("outputs/meta_code_normalization_research_metrics.csv"),
        "16_품목정규화_메타코드_핵심지표",
    ),
    ExportItem(
        Path("outputs/meta_code_normalization_research_audit.json"),
        "17_품목정규화_메타코드_감사요약",
    ),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def export_submission(output_date: str, output_dir: Path | None = None) -> Path:
    destination = (
        output_dir
        if output_dir is not None
        else ROOT / "exports" / f"정보원_최종제출용_{output_date}"
    )
    destination.mkdir(parents=True, exist_ok=True)

    missing = [item.source.as_posix() for item in EXPORT_ITEMS if not (ROOT / item.source).is_file()]
    if missing:
        raise FileNotFoundError("필수 최종제출 원본이 없습니다: " + ", ".join(missing))

    entries: list[dict[str, object]] = []
    for item in EXPORT_ITEMS:
        source = ROOT / item.source
        exported_name = f"{item.korean_stem}_{output_date}{source.suffix}"
        exported_path = destination / exported_name
        shutil.copy2(source, exported_path)
        entries.append(
            {
                "번호": len(entries) + 1,
                "원본": item.source.as_posix(),
                "제출파일": exported_name,
                "상태": "생성됨",
                "크기_bytes": exported_path.stat().st_size,
                "sha256": sha256(exported_path),
            }
        )

    manifest = {
        "제출물_생성일": output_date,
        "설명": "연구결과 종합, 통합 최종보고서, 결과표, 근거대장과 재현 산출물을 한글 파일명으로 복사한 최종 묶음",
        "운영_적용": False,
        "정책_상태": "50:50 도전모형 제안만 생성, 기존 활성 정책 유지",
        "파일": entries,
    }
    manifest_path = destination / f"00_최종제출파일_목록_{output_date}.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    print(export_submission(args.date, args.output_dir))


if __name__ == "__main__":
    main()
