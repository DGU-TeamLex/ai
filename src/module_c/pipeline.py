import logging

import pandas as pd

from ..config import (
    COMMODITY_RISK_SCORE_PATH,
    MODULE_C_ALERT_PATH,
    MODULE_C_EXPOSURE_CANDIDATE_PATH,
    MODULE_C_RISK_AUDIT_PATH,
    MODULE_C_RISK_SCORE_PATH,
    MODULE_C_RUN_REPORT_PATH,
    MODULE_C_SUPPLY_LEVEL_AUDIT_PATH,
    MODULE_C_SUPPLY_QUALITY_CLASSIFIED_PATH,
    MODULE_C_SUPPLY_QUALITY_ISSUES_PATH,
    MODULE_C_SUPPLY_QUALITY_PASSED_PATH,
    MODULE_C_SUPPLY_QUALITY_QUARANTINE_PATH,
    MODULE_C_SUPPLY_QUALITY_REPORT_PATH,
    MODULE_C_SUPPLY_QUALITY_REVIEW_PATH,
    MODULE_C_SUPPLY_QUALITY_SAMPLE_PATH,
    NEWS_RISK_SCORE_PATH,
    OUTPUT_DIR,
)
from ..utils import ensure_dirs, setup_logging, write_json
from .config import load_module_c_config
from .exposure_candidates import build_module_c_exposure_candidates
from .risk_engine import build_module_c_risk_outputs
from .supply_risk_anomaly_filter import (
    filter_supply_risk_records,
    select_supply_risk_quality_sample,
)


LOGGER = logging.getLogger(__name__)


def _read_optional_csv(path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def run_module_c_pipeline() -> None:
    setup_logging()
    ensure_dirs(OUTPUT_DIR, MODULE_C_SUPPLY_QUALITY_SAMPLE_PATH.parent)
    config = load_module_c_config()
    scores, audit, alerts = build_module_c_risk_outputs(
        _read_optional_csv(NEWS_RISK_SCORE_PATH),
        _read_optional_csv(COMMODITY_RISK_SCORE_PATH),
        config,
    )
    scores.to_csv(MODULE_C_RISK_SCORE_PATH, index=False)
    audit.to_csv(MODULE_C_RISK_AUDIT_PATH, index=False)
    alerts.to_csv(MODULE_C_ALERT_PATH, index=False)
    candidates, exposure_report = build_module_c_exposure_candidates()
    candidates.to_csv(MODULE_C_EXPOSURE_CANDIDATE_PATH, index=False)
    supply_audit_columns = [
        "local_item_key",
        "institution_code",
        "item_code",
        "representative_item_id",
        "representative_name",
        "item_family_id",
        "item_subtype_id",
        "normalized_specification",
        "unit_code",
        "raw_material_meta_code",
        "raw_material_risk_meta_code",
        "demand_risk_meta_code",
        "raw_material_suggested",
        "material_confidence",
        "material_evidence_tier",
        "material_evidence_reference",
        "material_review_status",
        "market_factor_ids",
        "baseline_supply_risk_level",
        "baseline_supply_risk_z",
        "baseline_lead_time_multiplier",
        "supply_risk_level_source",
        "canonical_supply_risk_meta_codes",
        "ignored_event_or_demand_codes",
        "unmapped_supply_risk_codes",
        "supply_risk_policy_needs_review",
        "supply_risk_policy_version",
        "supply_risk_policy_status",
        "review_priority",
        "usage_sum",
        "occurrence_count",
    ]
    supply_audit = (
        candidates[supply_audit_columns]
        .drop_duplicates("local_item_key")
        .sort_values(
            ["supply_risk_policy_needs_review", "local_item_key"],
            ascending=[False, True],
        )
    )
    supply_audit.to_csv(MODULE_C_SUPPLY_LEVEL_AUDIT_PATH, index=False)
    (
        supply_classified,
        supply_issues,
        supply_passed,
        supply_review,
        supply_quarantine,
        supply_quality_report,
    ) = filter_supply_risk_records(
        supply_audit,
        key_column="local_item_key",
        code_column="raw_material_risk_meta_code",
        operational_mode=False,
    )
    supply_sample = select_supply_risk_quality_sample(
        supply_classified,
        sample_size=1000,
    )
    supply_quality_report["sample_requested_rows"] = 1000
    supply_quality_report["sample_rows"] = len(supply_sample)
    supply_quality_report["sample_unique_representative_items"] = int(
        supply_sample["representative_item_id"].nunique()
    )
    supply_quality_report["sample_selection_basis"] = (
        "representative_item_diversity_then_status_and_issue_stratification"
    )
    supply_classified.to_csv(
        MODULE_C_SUPPLY_QUALITY_CLASSIFIED_PATH,
        index=False,
    )
    supply_issues.to_csv(MODULE_C_SUPPLY_QUALITY_ISSUES_PATH, index=False)
    supply_passed.to_csv(MODULE_C_SUPPLY_QUALITY_PASSED_PATH, index=False)
    supply_review.to_csv(MODULE_C_SUPPLY_QUALITY_REVIEW_PATH, index=False)
    supply_quarantine.to_csv(
        MODULE_C_SUPPLY_QUALITY_QUARANTINE_PATH,
        index=False,
    )
    supply_sample.to_csv(
        MODULE_C_SUPPLY_QUALITY_SAMPLE_PATH,
        index=False,
        encoding="utf-8-sig",
    )
    write_json(supply_quality_report, MODULE_C_SUPPLY_QUALITY_REPORT_PATH)
    report = {
        "module": "C",
        "config_version": config["version"],
        "calibration_status": config["calibration_status"],
        "coefficient_basis": config["coefficient_basis"],
        "risk_score_count": int(len(scores)),
        "risk_adjustment_enabled_count": int(
            scores.get("module_c_adjustment_enabled", pd.Series(dtype=bool)).sum()
        ),
        "alert_count": int(len(alerts)),
        "exposure": exposure_report,
        "supply_risk_quality": supply_quality_report,
    }
    write_json(report, MODULE_C_RUN_REPORT_PATH)
    LOGGER.info("Saved Module C risk scores: %s (%s rows)", MODULE_C_RISK_SCORE_PATH, len(scores))
    LOGGER.info("Saved Module C audit: %s (%s rows)", MODULE_C_RISK_AUDIT_PATH, len(audit))
    LOGGER.info("Saved Module C alerts: %s (%s rows)", MODULE_C_ALERT_PATH, len(alerts))
    LOGGER.info(
        "Saved Module C exposure candidates: %s (%s rows)",
        MODULE_C_EXPOSURE_CANDIDATE_PATH,
        len(candidates),
    )
    LOGGER.info(
        "Saved Module C supply risk policy audit: %s (%s rows)",
        MODULE_C_SUPPLY_LEVEL_AUDIT_PATH,
        len(supply_audit),
    )
    LOGGER.info(
        "Saved Module C supply risk quality outputs: pass=%s review=%s blocked=%s",
        len(supply_passed),
        len(supply_review),
        len(supply_quarantine),
    )
    LOGGER.info(
        "Saved Module C supply risk review sample: %s (%s rows)",
        MODULE_C_SUPPLY_QUALITY_SAMPLE_PATH,
        len(supply_sample),
    )


if __name__ == "__main__":
    run_module_c_pipeline()
