"""품목 정규화와 메타코드 체계의 연구 성과·한계를 감사한다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STANDARD_MAPPING = (
    ROOT / "data" / "processed" / "stock_standard_item_mapping.parquet"
)
DEFAULT_STANDARD_REPORT = ROOT / "outputs" / "stock_standard_item_mapping_report.json"
DEFAULT_INTEGRATED = (
    ROOT / "data" / "processed" / "item_integrated_classification_v2.parquet"
)
DEFAULT_APPROVED_MAPPING = ROOT / "data" / "mapping" / "stock_item_material_mapping.csv"
DEFAULT_APPROVAL_REPORT = ROOT / "outputs" / "material_mapping_approval_report.json"
DEFAULT_HISTORICAL_EFFECT = ROOT / "outputs" / "historical_training_weight_report.json"
DEFAULT_SUPPLY_ANALYSIS = ROOT / "outputs" / "syringe_supply_risk_inventory_summary.json"
DEFAULT_METRICS = ROOT / "outputs" / "meta_code_normalization_research_metrics.csv"
DEFAULT_SUMMARY = ROOT / "outputs" / "meta_code_normalization_research_audit.json"

STANDARD_COLUMNS = [
    "data_period",
    "local_item_key",
    "standard_item_key",
    "standard_item_definition_key",
    "standard_item_group_id",
    "standard_item_family_id",
    "standard_item_subtype_id",
    "standard_item_specification",
    "standard_item_unit_code",
    "standardization_match_method",
    "standardization_confidence",
    "historical_training_eligible",
]
INTEGRATED_COLUMNS = [
    "representative_item_id",
    "effective_item_family_id",
    "effective_item_subtype_id",
    "effective_specification",
    "effective_unit_code",
    "classification_classification_status",
    "classification_review_status",
    "classification_verification_status",
    "raw_material_meta_code",
    "raw_material_risk_meta_code",
    "demand_risk_meta_code",
    "material_review_status",
]
APPROVED_COLUMNS = [
    "stock_item_key",
    "raw_material_meta_code",
    "raw_material_risk_meta_code",
    "demand_risk_meta_code",
    "relation_type",
    "review_status",
]
RAW_MATERIAL_PLACEHOLDERS = {
    "",
    "MATERIAL_UNSPECIFIED",
    "MIXED_MATERIAL_NOT_SINGLE",
}
RISK_PLACEHOLDERS = {"", "UNCLASSIFIED_MATERIAL_RISK", "NOT_APPLICABLE"}
DEMAND_PLACEHOLDERS = {"", "NOT_APPLICABLE", "LOW_PRIORITY_NO_TRIGGER"}


def _text(series: pd.Series) -> pd.Series:
    return series.astype("string").fillna("").str.strip()


def _split_codes(value: object) -> set[str]:
    return {
        code.strip()
        for code in str(value or "").split(";")
        if code.strip()
    }


def _code_stats(series: pd.Series, placeholders: set[str]) -> dict[str, int | float]:
    token_sets = series.map(_split_codes)
    actionable = token_sets.map(lambda values: bool(values - placeholders))
    tokens = sorted(set().union(*token_sets.tolist()) - {""})
    actionable_tokens = sorted(set(tokens) - placeholders)
    return {
        "rows": int(len(series)),
        "actionable_rows": int(actionable.sum()),
        "actionable_pct": float(actionable.mean() * 100.0) if len(series) else 0.0,
        "unique_tokens": int(len(tokens)),
        "unique_actionable_tokens": int(len(actionable_tokens)),
    }


def audit_standard_mapping(mapping: pd.DataFrame, report: dict) -> dict:
    missing = sorted(set(STANDARD_COLUMNS).difference(mapping.columns))
    if missing:
        raise ValueError("표준품목 매핑 필수 열 누락: " + ", ".join(missing))

    duplicate_local = int(
        mapping.duplicated(["data_period", "local_item_key"], keep=False).sum()
    )
    missing_standard_key = int(_text(mapping["standard_item_key"]).eq("").sum())
    missing_definition_key = int(
        _text(mapping["standard_item_definition_key"]).eq("").sum()
    )
    if duplicate_local or missing_standard_key or missing_definition_key:
        raise ValueError(
            "표준품목 키 품질 게이트 실패: "
            f"duplicate={duplicate_local}, standard_missing={missing_standard_key}, "
            f"definition_missing={missing_definition_key}"
        )

    historical = mapping["data_period"].eq("historical")
    eligible = mapping["historical_training_eligible"].fillna(False).astype(bool)
    forbidden_fallback = int(
        (
            historical
            & mapping["standardization_match_method"].eq("historical_name_fallback")
            & eligible
        ).sum()
    )
    if forbidden_fallback:
        raise ValueError("이름 임시연결이 학습적격으로 유입됐습니다")

    historical_rows = int(historical.sum())
    historical_eligible = int((historical & eligible).sum())
    historical_excluded = int((historical & ~eligible).sum())
    result = {
        "mapping_rows": int(len(mapping)),
        "current_local_items": int(mapping["data_period"].eq("current").sum()),
        "historical_local_items": historical_rows,
        "historical_training_eligible_items": historical_eligible,
        "historical_training_eligible_pct": (
            historical_eligible / historical_rows * 100.0 if historical_rows else 0.0
        ),
        "historical_unmatched_items_excluded": historical_excluded,
        "standard_item_count": int(mapping["standard_item_key"].nunique()),
        "semantic_definition_eligible_rows": int(eligible.sum()),
        "duplicate_period_local_keys": duplicate_local,
        "missing_standard_item_keys": missing_standard_key,
        "missing_definition_keys": missing_definition_key,
        "mean_mapping_confidence": float(
            pd.to_numeric(mapping["standardization_confidence"], errors="coerce").mean()
        ),
        "match_method_counts": {
            str(key): int(value)
            for key, value in mapping["standardization_match_method"]
            .value_counts()
            .sort_index()
            .items()
        },
        "quality_gate_passed": True,
    }
    expected = {
        "mapping_rows": report.get("mapping_rows"),
        "current_local_items": report.get("current_local_items"),
        "historical_local_items": report.get("historical_local_items"),
        "historical_training_eligible_items": report.get(
            "historical_training_eligible_items"
        ),
        "standard_item_count": report.get("standard_item_count"),
    }
    mismatches = {
        key: {"computed": result[key], "reported": value}
        for key, value in expected.items()
        if value is not None and int(result[key]) != int(value)
    }
    if mismatches:
        raise ValueError(f"표준품목 보고서 수치 불일치: {mismatches}")
    result["source_report_consistent"] = True
    return result


def audit_meta_codes(
    integrated: pd.DataFrame,
    approved_mapping: pd.DataFrame,
    approval_report: dict,
) -> dict:
    missing_integrated = sorted(set(INTEGRATED_COLUMNS).difference(integrated.columns))
    missing_approved = sorted(set(APPROVED_COLUMNS).difference(approved_mapping.columns))
    if missing_integrated:
        raise ValueError("통합분류 필수 열 누락: " + ", ".join(missing_integrated))
    if missing_approved:
        raise ValueError("승인 매핑 필수 열 누락: " + ", ".join(missing_approved))

    family = _text(integrated["effective_item_family_id"])
    specific_family = family.ne("") & family.ne("UNSPECIFIED_ITEM")
    subtype = _text(integrated["effective_item_subtype_id"])
    specification = _text(integrated["effective_specification"])
    unit = _text(integrated["effective_unit_code"])
    review = _text(integrated["classification_review_status"])
    verification = _text(integrated["classification_verification_status"])

    approved_review = _text(approved_mapping["review_status"]).str.lower().eq("approved")
    if not approved_review.all():
        raise ValueError("승인 매핑 파일에 미승인 행이 포함돼 있습니다")
    duplicate_approved = int(
        approved_mapping.duplicated("stock_item_key", keep=False).sum()
    )
    if duplicate_approved:
        raise ValueError("승인 재고품목 매핑 키가 중복됐습니다")

    result = {
        "representative_item_rows": int(len(integrated)),
        "specific_family_rows": int(specific_family.sum()),
        "specific_family_pct": float(specific_family.mean() * 100.0),
        "subtype_rows": int(subtype.ne("").sum()),
        "specification_rows": int(specification.ne("").sum()),
        "unit_rows": int(unit.ne("").sum()),
        "verified_family_rows": int(verification.eq("verified_family").sum()),
        "approved_classification_rows": int(review.eq("approved").sum()),
        "classification_review_required_rows": int(review.ne("approved").sum()),
        "raw_material_meta_codes": _code_stats(
            integrated["raw_material_meta_code"], RAW_MATERIAL_PLACEHOLDERS
        ),
        "raw_material_risk_meta_codes": _code_stats(
            integrated["raw_material_risk_meta_code"], RISK_PLACEHOLDERS
        ),
        "demand_risk_meta_codes": _code_stats(
            integrated["demand_risk_meta_code"], DEMAND_PLACEHOLDERS
        ),
        "integrated_material_review_status_counts": {
            str(key): int(value)
            for key, value in integrated["material_review_status"]
            .value_counts(dropna=False)
            .sort_index()
            .items()
        },
        "approved_candidate_rows": int(approval_report["approved_candidate_rows"]),
        "approved_candidate_local_items": int(
            approval_report["approved_local_item_count"]
        ),
        "approved_stock_item_mapping_rows": int(len(approved_mapping)),
        "approved_stock_item_count": int(approved_mapping["stock_item_key"].nunique()),
        "approved_raw_material_code_counts": {
            str(key): int(value)
            for key, value in approved_mapping["raw_material_meta_code"]
            .value_counts()
            .sort_index()
            .items()
        },
        "approved_relation_type_counts": {
            str(key): int(value)
            for key, value in approved_mapping["relation_type"]
            .value_counts()
            .sort_index()
            .items()
        },
        "approved_mapping_duplicate_stock_keys": duplicate_approved,
        "approved_mapping_applied": bool(approval_report.get("applied", False)),
        "quality_gate_passed": True,
    }
    reported_rows = int(approval_report["approved_stock_item_mapping_rows"])
    if reported_rows != result["approved_stock_item_mapping_rows"]:
        raise ValueError(
            "승인 매핑 보고서와 실제 파일 행수 불일치: "
            f"{reported_rows} != {result['approved_stock_item_mapping_rows']}"
        )
    return result


def audit_historical_effect(report: dict) -> dict:
    """현재자료 전용 대비 엄격 연결 과거자료 포함 효과를 재검증한다."""
    current = report["current_only_validation_metrics"]
    selected = report["selected_validation_metrics"]
    computed_change = float(selected["WAPE"] - current["WAPE"])
    reported_change = float(report["validation_wape_change"])
    if abs(computed_change - reported_change) > 1e-9:
        raise ValueError(
            "과거자료 효과 보고서 WAPE 변화량 불일치: "
            f"{computed_change} != {reported_change}"
        )
    if not bool(report.get("selection_did_not_use_test", False)):
        raise ValueError("과거자료 가중치 선택에 시험구간이 사용됐습니다")
    return {
        "selection_metric": str(report["selection_metric"]),
        "selection_did_not_use_test": True,
        "validation_period": f'{report["validation_start"]}~{report["validation_end"]}',
        "selected_historical_weight": float(report["selected_historical_weight"]),
        "current_only_validation_wape_pct": float(current["WAPE"]),
        "selected_with_history_validation_wape_pct": float(selected["WAPE"]),
        "validation_wape_change_pct_point": reported_change,
        "historical_training_rows": int(selected["historical_rows"]),
        "quality_gate_passed": True,
    }


def audit_raw_material_linkage(
    approval_report: dict,
    supply_report: dict,
    meta_audit: dict,
) -> dict:
    """메타코드 승인 범위가 실제 PP 공급위험 분석 범위와 일치하는지 검사한다."""
    scope = supply_report["scope"]
    mapping = supply_report["mapping_audit"]
    policy = supply_report["policy"]
    expected_filter = {
        "raw_material_meta_code": "POLYPROPYLENE_PP",
        "relation_type": "direct_component",
        "review_status": "approved",
    }
    checks = {
        "approved_mapping_count_matches": (
            int(mapping["approved_pp_direct_rows"])
            == int(meta_audit["approved_stock_item_mapping_rows"])
        ),
        "analysis_filter_matches": mapping["filter"] == expected_filter,
        "family_matches": scope["family"] == "DISPOSABLE_SYRINGE",
        "material_matches": scope["material"] == "POLYPROPYLENE_PP",
        "relation_matches": scope["relation_type"] == "direct_component",
        "demand_path_disabled": not bool(policy["external_demand_signal_in_forecast"]),
        "operational_adjustment_disabled": not bool(
            policy["operational_adjustment_enabled"]
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ValueError("원자재 메타코드 연결 게이트 실패: " + ", ".join(failed))

    candidate_rows = int(approval_report["input_candidate_rows"])
    approved_rows = int(approval_report["approved_candidate_rows"])
    return {
        "candidate_rows": candidate_rows,
        "approved_candidate_rows": approved_rows,
        "candidate_approval_pct": approved_rows / candidate_rows * 100.0,
        "approved_stock_item_mapping_rows": int(
            meta_audit["approved_stock_item_mapping_rows"]
        ),
        "analysis_stock_items": int(scope["stock_items"]),
        "analysis_item_month_rows": int(scope["rows"]),
        "analysis_months": int(scope["months"]),
        "analysis_filter": expected_filter,
        "linkage_chain": [
            "normalized item",
            "DISPOSABLE_SYRINGE family and SYRINGE_USAGE_BASED subtype",
            "POLYPROPYLENE_PP candidate",
            "evidence and conflict quality gates",
            "approved stock_item_key",
            "PP market risk",
            "lead-time and safety-stock sensitivity",
        ],
        "policy_effect": {
            "mean_effective_lead_time_increase_days": float(
                supply_report["lead_time"]["mean_increase_days"]
            ),
            "safety_stock_delta_pct": float(
                supply_report["safety_stock"]["delta_pct"]
            ),
            "gated_order_delta_pct": float(
                supply_report["recommended_order_gated"]["delta_pct"]
            ),
        },
        "demand_path_enabled": False,
        "operational_adjustment_enabled": False,
        "gate_checks": checks,
        "quality_gate_passed": True,
    }


def build_metrics(summary: dict) -> pd.DataFrame:
    normalization = summary["normalization"]
    meta = summary["meta_codes"]
    linkage = summary["raw_material_analysis_linkage"]
    rows = [
        ("normalization", "mapping_rows", normalization["mapping_rows"], "rows", "verified"),
        ("normalization", "standard_item_count", normalization["standard_item_count"], "items", "verified"),
        (
            "normalization",
            "historical_training_eligible_pct",
            normalization["historical_training_eligible_pct"],
            "percent",
            "verified",
        ),
        (
            "normalization",
            "historical_unmatched_items_excluded",
            normalization["historical_unmatched_items_excluded"],
            "items",
            "guardrail",
        ),
        (
            "normalization",
            "duplicate_period_local_keys",
            normalization["duplicate_period_local_keys"],
            "rows",
            "quality_gate",
        ),
        (
            "normalization",
            "historical_validation_wape_change_pct_point",
            summary["historical_model_effect"]["validation_wape_change_pct_point"],
            "percentage_point",
            "validation_selected",
        ),
        (
            "meta_code_taxonomy",
            "representative_item_rows",
            meta["representative_item_rows"],
            "rows",
            "candidate_taxonomy",
        ),
        (
            "meta_code_taxonomy",
            "specific_family_pct",
            meta["specific_family_pct"],
            "percent",
            "candidate_taxonomy",
        ),
        (
            "meta_code_taxonomy",
            "classification_review_required_rows",
            meta["classification_review_required_rows"],
            "rows",
            "limitation",
        ),
        (
            "approved_material_mapping",
            "approved_candidate_rows",
            meta["approved_candidate_rows"],
            "rows",
            "experimental_approved",
        ),
        (
            "approved_material_mapping",
            "approved_stock_item_mapping_rows",
            meta["approved_stock_item_mapping_rows"],
            "rows",
            "experimental_approved",
        ),
        (
            "raw_material_analysis_linkage",
            "candidate_approval_pct",
            linkage["candidate_approval_pct"],
            "percent",
            "verified_funnel",
        ),
        (
            "raw_material_analysis_linkage",
            "analysis_stock_items",
            linkage["analysis_stock_items"],
            "items",
            "verified_scope",
        ),
        (
            "raw_material_analysis_linkage",
            "analysis_item_month_rows",
            linkage["analysis_item_month_rows"],
            "rows",
            "verified_scope",
        ),
    ]
    return pd.DataFrame(
        rows, columns=["layer", "metric", "value", "unit", "claim_status"]
    )


def run(
    standard_mapping_path: Path = DEFAULT_STANDARD_MAPPING,
    standard_report_path: Path = DEFAULT_STANDARD_REPORT,
    integrated_path: Path = DEFAULT_INTEGRATED,
    approved_mapping_path: Path = DEFAULT_APPROVED_MAPPING,
    approval_report_path: Path = DEFAULT_APPROVAL_REPORT,
    historical_effect_path: Path = DEFAULT_HISTORICAL_EFFECT,
    supply_analysis_path: Path = DEFAULT_SUPPLY_ANALYSIS,
    metrics_path: Path = DEFAULT_METRICS,
    summary_path: Path = DEFAULT_SUMMARY,
) -> dict:
    standard_mapping = pd.read_parquet(
        standard_mapping_path, columns=STANDARD_COLUMNS
    )
    integrated = pd.read_parquet(integrated_path, columns=INTEGRATED_COLUMNS)
    approved_mapping = pd.read_csv(
        approved_mapping_path, usecols=APPROVED_COLUMNS
    )
    standard_report = json.loads(standard_report_path.read_text(encoding="utf-8-sig"))
    approval_report = json.loads(approval_report_path.read_text(encoding="utf-8-sig"))
    historical_effect_report = json.loads(
        historical_effect_path.read_text(encoding="utf-8-sig")
    )
    supply_analysis_report = json.loads(
        supply_analysis_path.read_text(encoding="utf-8-sig")
    )
    meta_audit = audit_meta_codes(integrated, approved_mapping, approval_report)

    summary = {
        "version": "meta-code-normalization-research-audit-v1.1",
        "status": "complete",
        "normalization": audit_standard_mapping(standard_mapping, standard_report),
        "meta_codes": meta_audit,
        "historical_model_effect": audit_historical_effect(historical_effect_report),
        "raw_material_analysis_linkage": audit_raw_material_linkage(
            approval_report, supply_analysis_report, meta_audit
        ),
        "research_interpretation": {
            "contribution": (
                "정규화와 계층형 메타코드는 과거품목·현재품목·외부위험·재고정책을 "
                "연결하는 공통 분석축을 제공했다."
            ),
            "claim_boundary": (
                "통합 메타코드는 후보 분류를 포함하며 인과관계를 증명하지 않는다. "
                "원자재 효과 분석에는 별도 승인 매핑만 사용한다."
            ),
            "normalization_distinction": (
                "품목명·규격·단위 표준화와 외부위험 점수의 0~1 수치 정규화는 "
                "서로 다른 절차다."
            ),
            "raw_material_enablement": (
                "메타코드는 승인된 주사기-PP 직접부품 경로에만 원자재 위험을 전달하고 "
                "동일 재고키로 리드타임·안전재고·발주 민감도를 추적하게 했다."
            ),
        },
        "sources": {
            "standard_mapping": standard_mapping_path.relative_to(ROOT).as_posix(),
            "standard_report": standard_report_path.relative_to(ROOT).as_posix(),
            "integrated_taxonomy": integrated_path.relative_to(ROOT).as_posix(),
            "approved_material_mapping": approved_mapping_path.relative_to(ROOT).as_posix(),
            "approval_report": approval_report_path.relative_to(ROOT).as_posix(),
            "historical_effect": historical_effect_path.relative_to(ROOT).as_posix(),
            "supply_analysis": supply_analysis_path.relative_to(ROOT).as_posix(),
        },
    }
    metrics = build_metrics(summary)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(metrics_path, index=False, encoding="utf-8-sig")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--standard-mapping", type=Path, default=DEFAULT_STANDARD_MAPPING)
    parser.add_argument("--standard-report", type=Path, default=DEFAULT_STANDARD_REPORT)
    parser.add_argument("--integrated", type=Path, default=DEFAULT_INTEGRATED)
    parser.add_argument("--approved-mapping", type=Path, default=DEFAULT_APPROVED_MAPPING)
    parser.add_argument("--approval-report", type=Path, default=DEFAULT_APPROVAL_REPORT)
    parser.add_argument("--historical-effect", type=Path, default=DEFAULT_HISTORICAL_EFFECT)
    parser.add_argument("--supply-analysis", type=Path, default=DEFAULT_SUPPLY_ANALYSIS)
    parser.add_argument("--metrics", type=Path, default=DEFAULT_METRICS)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()
    summary = run(
        args.standard_mapping,
        args.standard_report,
        args.integrated,
        args.approved_mapping,
        args.approval_report,
        args.historical_effect,
        args.supply_analysis,
        args.metrics,
        args.summary,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
