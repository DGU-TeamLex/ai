"""결과보고서의 데이터 처리 사례를 실제 Parquet에서 재현한다.

이 스크립트는 원본·중간 산출물을 수정하지 않는다. 기관·로컬 품목 식별자는
SHA-256 앞 8자리로 가명화하고, 보고서에서 설명할 최소 열만 JSON으로 출력한다.

사용법:
    python scripts/analysis/report_data_examples.py
    python scripts/analysis/report_data_examples.py --sample-limit 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
CURRENT_MONTHLY = ROOT / "data" / "processed" / "stock_monthly.parquet"
STANDARD_MAPPING = (
    ROOT / "data" / "processed" / "stock_standard_item_mapping.parquet"
)
MAPPING_REPORT = ROOT / "outputs" / "stock_standard_item_mapping_report.json"
EXTERNAL_COVERAGE = ROOT / "outputs" / "external_signal_coverage_report.json"
FORECAST_QUALITY = ROOT / "outputs" / "stock_forecast_data_quality.json"


def pseudonym(value: object) -> str:
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:8]
    return f"H-{digest}"


def records(frame: pd.DataFrame) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in frame.to_dict(orient="records"):
        clean: dict[str, object] = {}
        for key, value in row.items():
            if isinstance(value, pd.Timestamp):
                clean[key] = value.strftime("%Y-%m")
            elif hasattr(value, "item"):
                clean[key] = value.item()
            else:
                clean[key] = value
        result.append(clean)
    return result


def demand_sign_examples(limit: int) -> dict[str, object]:
    columns = [
        "year_month",
        "institution_code",
        "department",
        "item_code",
        "item_name",
        "normal_outbound_signed_sum",
        "model_demand_positive_sum",
        "negative_normal_outbound_count",
        "negative_normal_outbound_amount",
    ]
    frame = pd.read_parquet(
        CURRENT_MONTHLY,
        columns=columns,
        filters=[("negative_normal_outbound_count", ">", 0)],
    )
    frame["institution_sample_id"] = frame["institution_code"].map(pseudonym)
    frame["item_sample_id"] = (
        frame["institution_code"].astype(str)
        + "|"
        + frame["item_code"].astype(str)
    ).map(pseudonym)
    output_columns = [
        "year_month",
        "institution_sample_id",
        "item_sample_id",
        "normal_outbound_signed_sum",
        "model_demand_positive_sum",
        "negative_normal_outbound_count",
        "negative_normal_outbound_amount",
    ]
    negative_only = frame[
        frame["model_demand_positive_sum"].eq(0)
        & frame["normal_outbound_signed_sum"].lt(0)
    ].sort_values(["year_month", "institution_code", "item_code"])
    mixed = frame[
        frame["model_demand_positive_sum"].gt(0)
        & frame["normal_outbound_signed_sum"].ne(
            frame["model_demand_positive_sum"]
        )
    ].sort_values(["year_month", "institution_code", "item_code"])
    return {
        "source": str(CURRENT_MONTHLY.relative_to(ROOT)),
        "filter": "negative_normal_outbound_count > 0",
        "negative_recorded_months": int(len(frame)),
        "negative_only_months": int(len(negative_only)),
        "mixed_positive_negative_months": int(len(mixed)),
        "negative_only_examples": records(negative_only[output_columns].head(limit)),
        "mixed_examples": records(mixed[output_columns].head(limit)),
    }


def mapping_examples(limit: int) -> dict[str, object]:
    columns = [
        "data_period",
        "local_item_key",
        "raw_item_name",
        "product_name_candidate",
        "standard_item_key",
        "standardization_match_method",
        "standardization_confidence",
        "historical_training_eligible",
    ]
    frame = pd.read_parquet(
        STANDARD_MAPPING,
        columns=columns,
        filters=[
            ("data_period", "==", "historical"),
            ("historical_training_eligible", "==", False),
        ],
    ).sort_values(["raw_item_name", "local_item_key"])
    frame["local_item_sample_id"] = frame["local_item_key"].map(pseudonym)
    output_columns = [
        "local_item_sample_id",
        "raw_item_name",
        "product_name_candidate",
        "standard_item_key",
        "standardization_match_method",
        "standardization_confidence",
        "historical_training_eligible",
    ]
    report = json.loads(MAPPING_REPORT.read_text(encoding="utf-8"))
    quality = json.loads(FORECAST_QUALITY.read_text(encoding="utf-8"))
    fallback = next(
        row
        for row in quality["standardization_coverage"]
        if row["data_period"] == "historical"
        and row["normalization_status"] == "historical_name_fallback"
        and row["historical_training_eligible"] is False
    )
    return {
        "source": str(STANDARD_MAPPING.relative_to(ROOT)),
        "filter": (
            "data_period == historical and "
            "historical_training_eligible == false"
        ),
        "excluded_historical_local_items": int(len(frame)),
        "excluded_historical_monthly_rows": int(fallback["rows"]),
        "excluded_historical_usage_sum": float(fallback["usage_sum"]),
        "mapping_report_version": report["version"],
        "examples": records(frame[output_columns].head(limit)),
    }


def external_signal_summary() -> dict[str, object]:
    report = json.loads(EXTERNAL_COVERAGE.read_text(encoding="utf-8"))
    train = report["coverage_by_split"]["train"]["signals"]
    unavailable = [
        name for name, values in train.items() if values["nonzero_rows"] == 0
    ]
    return {
        "source": str(EXTERNAL_COVERAGE.relative_to(ROOT)),
        "train_rows": report["coverage_by_split"]["train"]["rows"],
        "zero_coverage_signal_count": len(unavailable),
        "zero_coverage_signals": unavailable,
        "interpretation": (
            "해당 재실행에서는 값이 있는 외부신호가 없어 외부위험 모형을 "
            "비교·선택하면 안 된다. 수요전용 모형만 유효하다."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-limit", type=int, default=3)
    args = parser.parse_args()
    if args.sample_limit < 1:
        raise ValueError("sample-limit must be positive")
    payload = {
        "privacy": (
            "기관·로컬 품목 키는 SHA-256 앞 8자리로 가명화했다. "
            "해시는 원본 식별자를 공개하지 않는 사례 추적용 표지다."
        ),
        "demand_sign_examples": demand_sign_examples(args.sample_limit),
        "historical_mapping_exclusions": mapping_examples(args.sample_limit),
        "external_signal_coverage": external_signal_summary(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
