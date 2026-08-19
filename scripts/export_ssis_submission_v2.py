"""Export the SSIS package plus the final bias/inventory validation addendum."""
from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from export_ssis_submission import ROOT, export_submission


@dataclass(frozen=True)
class ExtraItem:
    source: Path
    korean_stem: str


EXTRA_ITEMS = (
    ExtraItem(
        Path("docs/2026-08-18_01_FINAL_BIAS_INVENTORY_VALIDATION_ADDENDUM.md"),
        "07_편향보정_30일리드타임_최종검증보완보고서",
    ),
    ExtraItem(
        Path("outputs/forecast_bias_inventory_backtest_report.csv"),
        "08_편향보정_재고정책_통합결과",
    ),
    ExtraItem(
        Path("outputs/forecast_bias_inventory_backtest_forecast_grid.csv"),
        "09_L1_Tweedie_혼합비_전수결과",
    ),
    ExtraItem(
        Path("outputs/forecast_bias_inventory_backtest_monthly.csv"),
        "10_30일리드타임_월별재고성과",
    ),
    ExtraItem(
        Path("outputs/forecast_bias_inventory_backtest_summary.json"),
        "11_편향보정_재고정책_요약",
    ),
    ExtraItem(
        Path("outputs/forecast_bias_inventory_backtest_policy_proposal.json"),
        "12_고정50대50_정책제안_미적용",
    ),
)


def export_submission_v2(output_date: str, output_dir: Path | None = None) -> Path:
    destination = export_submission(output_date, output_dir)
    manifest_path = destination / f"00_제출파일_목록_{output_date}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for item in EXTRA_ITEMS:
        source = ROOT / item.source
        if not source.is_file():
            raise FileNotFoundError(f"보완 제출물 원본이 없습니다: {item.source}")
        exported_name = f"{item.korean_stem}_{output_date}{source.suffix}"
        shutil.copy2(source, destination / exported_name)
        manifest["파일"].append(
            {
                "원본": item.source.as_posix(),
                "제출파일": exported_name,
                "필수": True,
                "상태": "생성됨",
            }
        )

    manifest["설명"] = (
        "기존 결과보고서와 외부위험 결과에 편향보정·30일 리드타임 최종 검증을 추가한 묶음"
    )
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
    print(export_submission_v2(args.date, args.output_dir))


if __name__ == "__main__":
    main()
