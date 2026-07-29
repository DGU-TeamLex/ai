from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import pandas as pd

from ..config import (
    HSK_REFERENCE_NORMALIZED_PATH,
    HSK_REFERENCE_REPORT_PATH,
    HSK_REFERENCE_SOURCE_PATH,
)
from ..utils import ensure_dirs, write_json


SOURCE_TO_NORMALIZED = {
    "HS부호": "hs_code",
    "적용시작일자": "valid_from",
    "적용종료일자": "valid_to",
    "한글품목명": "item_name_ko",
    "영문품목명": "item_name_en",
    "HS부호내용": "hs_description",
    "한국표준무역분류명": "sitc_name",
    "수량단위최대단가": "quantity_unit_max_price",
    "중량단위최대단가": "weight_unit_max_price",
    "수량단위코드": "quantity_unit_code",
    "중량단위코드": "weight_unit_code",
    "수출성질코드": "export_nature_code",
    "수입성질코드": "import_nature_code",
    "품목규격명": "item_specification_name",
    "필수규격명": "required_specification_name",
    "참고규격명": "reference_specification_name",
    "규격설명": "specification_description",
    "규격사항내용": "specification_detail",
    "성질통합분류코드": "integrated_nature_code",
    "성질통합분류코드명": "integrated_nature_name",
}
NORMALIZED_COLUMNS = [
    *SOURCE_TO_NORMALIZED.values(),
    "hs_code_digits",
    "is_trade_leaf",
    "source_file",
    "reference_version",
]


def load_hsk_reference(path: Path = HSK_REFERENCE_SOURCE_PATH) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"HSK reference file not found: {path}")

    source = pd.read_excel(path, dtype="string", keep_default_na=False)
    source.columns = source.columns.astype(str).str.strip()
    missing = [column for column in SOURCE_TO_NORMALIZED if column not in source.columns]
    if missing:
        raise ValueError(f"HSK reference is missing columns: {missing}")

    result = source[list(SOURCE_TO_NORMALIZED)].rename(
        columns=SOURCE_TO_NORMALIZED
    )
    for column in result.columns:
        result[column] = result[column].astype("string").fillna("").str.strip()
    result["hs_code"] = result["hs_code"].str.replace(r"\.0$", "", regex=True)

    invalid = ~result["hs_code"].str.fullmatch(r"\d{2,10}")
    if invalid.any():
        examples = result.loc[invalid, "hs_code"].head(5).tolist()
        raise ValueError(f"HSK reference contains invalid HS codes: {examples}")
    duplicate = result.duplicated(["hs_code", "valid_from"], keep=False)
    if duplicate.any():
        examples = result.loc[duplicate, ["hs_code", "valid_from"]].head(5)
        raise ValueError(
            "HSK reference contains duplicate code/effective-date rows: "
            f"{examples.to_dict(orient='records')}"
        )

    result["valid_from"] = pd.to_datetime(result["valid_from"], errors="coerce")
    result["valid_to"] = pd.to_datetime(result["valid_to"], errors="coerce")
    if result[["valid_from", "valid_to"]].isna().any(axis=None):
        raise ValueError("HSK reference contains invalid effective dates")
    if result["valid_from"].gt(result["valid_to"]).any():
        raise ValueError("HSK reference contains reversed effective dates")

    result["hs_code_digits"] = result["hs_code"].str.len().astype("int16")
    result["is_trade_leaf"] = result["hs_code_digits"].eq(10)
    result["source_file"] = path.name
    result["reference_version"] = "hsk-2026-01-01"
    return result[NORMALIZED_COLUMNS].sort_values(
        ["hs_code", "valid_from"]
    ).reset_index(drop=True)


def build_hsk_reference_outputs(
    source_path: Path = HSK_REFERENCE_SOURCE_PATH,
    output_path: Path = HSK_REFERENCE_NORMALIZED_PATH,
    report_path: Path = HSK_REFERENCE_REPORT_PATH,
) -> dict[str, object]:
    reference = load_hsk_reference(source_path)
    ensure_dirs(output_path.parent, report_path.parent)
    reference.to_parquet(output_path, index=False, compression="zstd")

    pp = reference[
        reference["hs_code"].isin(["3902100000", "3902300000", "9018310000"])
    ]
    report = {
        "reference_version": "hsk-2026-01-01",
        "source_path": str(source_path),
        "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "rows": int(len(reference)),
        "trade_leaf_rows": int(reference["is_trade_leaf"].sum()),
        "hierarchy_rows": int((~reference["is_trade_leaf"]).sum()),
        "code_digit_counts": {
            str(key): int(value)
            for key, value in reference["hs_code_digits"]
            .value_counts()
            .sort_index()
            .items()
        },
        "verified_connection_codes": pp[
            ["hs_code", "item_name_ko", "item_name_en"]
        ].to_dict(orient="records"),
        "output_path": str(output_path),
    }
    write_json(report, report_path)
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Normalize the official HSK workbook")
    parser.add_argument("--source", type=Path, default=HSK_REFERENCE_SOURCE_PATH)
    parser.add_argument("--output", type=Path, default=HSK_REFERENCE_NORMALIZED_PATH)
    parser.add_argument("--report", type=Path, default=HSK_REFERENCE_REPORT_PATH)
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    print(
        build_hsk_reference_outputs(args.source, args.output, args.report)
    )
