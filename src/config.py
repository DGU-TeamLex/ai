import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

RAW_STOCK_DIR = PROJECT_ROOT / "raw_stock"
RAW_STOCK_FILE_PATTERN = "*.DAT"
CURRENT_RAW_STOCK_FILE_PATTERN = "익스포트_*.DAT"
HISTORICAL_RAW_STOCK_FILE_PATTERN = "*2018_2019*.DAT"

PROCESSED_DATA_DIR = PROJECT_ROOT / "data" / "processed"
SAMPLE_DATA_DIR = PROJECT_ROOT / "data" / "sample"
MAPPING_DATA_DIR = PROJECT_ROOT / "data" / "mapping"
EXTERNAL_MASTER_DIR = PROJECT_ROOT / "data" / "external" / "official"
EXTERNAL_MARKET_DATA_DIR = PROJECT_ROOT / "data" / "external" / "market"
EXTERNAL_TRADE_DATA_DIR = PROJECT_ROOT / "data" / "external" / "trade"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
MODEL_DIR = PROJECT_ROOT / "models"
EXTERNAL_FEATURE_PATH = PROJECT_ROOT / "data" / "external_features.csv"

MONTHLY_STOCK_PATH = PROCESSED_DATA_DIR / "stock_monthly.parquet"
HISTORICAL_MONTHLY_STOCK_PATH = (
    PROCESSED_DATA_DIR / "stock_monthly_2018_2019_auxiliary.parquet"
)
STOCK_PREPROCESSING_REPORT_PATH = OUTPUT_DIR / "stock_preprocessing_report.json"
FEATURE_TABLE_PATH = OUTPUT_DIR / "stock_feature_table.parquet"
NEWS_RISK_SCORE_PATH = OUTPUT_DIR / "stock_news_risk_scores.csv"
NEWS_ARTICLE_SCORE_PATH = OUTPUT_DIR / "stock_news_article_scores.csv"
COMMODITY_RISK_SCORE_PATH = OUTPUT_DIR / "stock_commodity_risk_scores.csv"
COMMODITY_PRICE_CACHE_PATH = EXTERNAL_MARKET_DATA_DIR / "commodity_prices.csv"
COMMODITY_RISK_AUDIT_PATH = OUTPUT_DIR / "stock_commodity_risk_audit.csv"
HSK_REFERENCE_SOURCE_PATH = (
    EXTERNAL_TRADE_DATA_DIR / "reference" / "관세청_HS부호_20260101.xlsx"
)
HSK_REFERENCE_NORMALIZED_PATH = (
    PROCESSED_DATA_DIR / "trade" / "hsk_reference_2026.parquet"
)
HSK_REFERENCE_REPORT_PATH = OUTPUT_DIR / "hsk_reference_2026_report.json"
MATERIAL_HS_MAPPING_PATH = MAPPING_DATA_DIR / "material_hs_mapping.csv"
MATERIAL_HS_MAPPING_REPORT_PATH = OUTPUT_DIR / "material_hs_mapping_update_report.json"
MATERIAL_HS_MAPPING_SAMPLE_PATH = (
    SAMPLE_DATA_DIR / "material_hs_mapping_sample_1000.csv"
)
DRUG_INGREDIENT_SOURCE_PATH = (
    PROJECT_ROOT
    / "pdf"
    / "(한국사회보장정보원)_의료재고예측모델 개발 관련 데이터셋(약성분)_수정.DAT"
)
DRUG_INGREDIENT_DICTIONARY_PATH = (
    PROCESSED_DATA_DIR / "drug_ingredient_dictionary_v1.parquet"
)
DRUG_INGREDIENT_ENRICHMENT_PATH = (
    PROCESSED_DATA_DIR / "item_drug_ingredient_enrichment_v1.parquet"
)
DRUG_INGREDIENT_REPORT_PATH = OUTPUT_DIR / "drug_ingredient_enrichment_report.json"
DRUG_INGREDIENT_SAMPLE_PATH = (
    SAMPLE_DATA_DIR / "drug_ingredient_enrichment_sample_1000.csv"
)
TRADE_COUNTRY_SCOPE_PATH = MAPPING_DATA_DIR / "trade_country_scope.csv"
TRADE_TOTAL_CACHE_PATH = EXTERNAL_TRADE_DATA_DIR / "kcs_trade_total_monthly.csv"
TRADE_COUNTRY_CACHE_PATH = EXTERNAL_TRADE_DATA_DIR / "kcs_trade_country_monthly.csv"
TRADE_COUNTRY_CACHE_SUMMARY_PATH = (
    OUTPUT_DIR / "kcs_trade_country_cache_summary.csv"
)
TRADE_HS_FEATURE_PATH = OUTPUT_DIR / "hs_trade_risk_features.csv"
TRADE_HS_FEATURE_SAMPLE_PATH = SAMPLE_DATA_DIR / "hs_trade_risk_features_sample_1000.csv"
TRADE_RISK_SCORE_PATH = OUTPUT_DIR / "stock_trade_risk_scores.csv"
TRADE_RISK_AUDIT_PATH = OUTPUT_DIR / "stock_trade_risk_audit.csv"
TRADE_RUN_REPORT_PATH = OUTPUT_DIR / "stock_trade_risk_report.json"
TRADE_INVENTORY_IMPACT_REPORT_PATH = (
    OUTPUT_DIR / "trade_inventory_impact_report.json"
)
TRADE_INVENTORY_IMPACT_SAMPLE_PATH = (
    SAMPLE_DATA_DIR / "trade_inventory_impact_sample_1000.csv"
)
TRADE_WEIGHT_CALIBRATION_REPORT_PATH = (
    OUTPUT_DIR / "trade_weight_calibration_report.json"
)
TRADE_WEIGHT_CALIBRATION_TABLE_PATH = (
    OUTPUT_DIR / "trade_weight_calibration_observations.csv"
)
TRADE_WEIGHT_CALIBRATION_POLICY_PATH = (
    OUTPUT_DIR / "trade_weight_calibration_policy.csv"
)
TRADE_WEIGHT_CALIBRATION_SAMPLE_PATH = (
    SAMPLE_DATA_DIR / "trade_weight_calibration_sample_1000.csv"
)
MODULE_C_RISK_SCORE_PATH = OUTPUT_DIR / "stock_module_c_risk_scores.csv"
MODULE_C_RISK_AUDIT_PATH = OUTPUT_DIR / "stock_module_c_risk_audit.csv"
MODULE_C_ALERT_PATH = OUTPUT_DIR / "stock_module_c_alerts.csv"
MODULE_C_EXPOSURE_CANDIDATE_PATH = (
    OUTPUT_DIR / "module_c_material_exposure_candidates.csv"
)
MODULE_C_RUN_REPORT_PATH = OUTPUT_DIR / "module_c_run_report.json"
PREDICTION_PATH = OUTPUT_DIR / "stock_predictions.csv"
BACKTEST_PREDICTION_PATH = OUTPUT_DIR / "stock_backtest_predictions.csv"
CLASSIFIED_PREDICTION_PATH = OUTPUT_DIR / "stock_predictions_by_subtype.csv"
CLASSIFIED_PREDICTION_QUALITY_PATH = OUTPUT_DIR / "stock_predictions_by_subtype_quality.json"
EVALUATION_REPORT_PATH = OUTPUT_DIR / "stock_evaluation_report.csv"
EVALUATION_SEGMENT_REPORT_PATH = OUTPUT_DIR / "stock_evaluation_by_segment.csv"
MODEL_COMBINATION_POINT_EVALUATION_PATH = (
    OUTPUT_DIR / "stock_combination_point_evaluation.csv"
)
MODEL_COMBINATION_INVENTORY_EVALUATION_PATH = (
    OUTPUT_DIR / "stock_combination_inventory_evaluation.csv"
)
MODEL_COMBINATION_SEGMENT_EVALUATION_PATH = (
    OUTPUT_DIR / "stock_combination_segment_evaluation.csv"
)
MODEL_COMBINATION_POLICY_PATH = OUTPUT_DIR / "stock_combination_policy.json"
MODEL_COMBINATION_SAMPLE_PATH = (
    SAMPLE_DATA_DIR / "stock_combination_experiment_sample_1000.csv"
)
FORECAST_ENSEMBLE_POLICY_PATH = (
    MAPPING_DATA_DIR / "forecast_ensemble_policy.json"
)
FORECAST_ENSEMBLE_VALIDATION_PATH = (
    OUTPUT_DIR / "forecast_ensemble_validation_candidates.csv"
)
FORECAST_ENSEMBLE_REPORT_PATH = (
    OUTPUT_DIR / "forecast_ensemble_temporal_report.json"
)
FORECAST_ENSEMBLE_SEGMENT_PATH = (
    OUTPUT_DIR / "forecast_ensemble_test_by_segment.csv"
)
FORECAST_ENSEMBLE_SAMPLE_PATH = (
    SAMPLE_DATA_DIR / "forecast_ensemble_test_sample_1000.csv"
)
MODEL_VALIDATION_REPORT_PATH = OUTPUT_DIR / "stock_model_validation_report.csv"
MODEL_CV_REPORT_PATH = OUTPUT_DIR / "stock_model_cv_report.csv"
FORECAST_DATA_QUALITY_REPORT_PATH = OUTPUT_DIR / "stock_forecast_data_quality.json"
MODEL_MANIFEST_PATH = MODEL_DIR / "stock_manifest.json"
ITEM_ALIAS_CANDIDATE_PATH = PROCESSED_DATA_DIR / "item_alias_candidates_v0.3.parquet"
ITEM_ALIAS_TO_PRODUCT_PATH = PROCESSED_DATA_DIR / "item_alias_to_product_v1.parquet"
ITEM_GROUPED_VERIFIED_PATH = PROCESSED_DATA_DIR / "item_grouped_verified_v1.parquet"
ITEM_PRODUCT_WORKLIST_PATH = PROCESSED_DATA_DIR / "item_product_worklist_v1.parquet"
OFFICIAL_DEVICE_MATERIAL_CLAIMS_PATH = (
    PROCESSED_DATA_DIR / "official_device_material_claims_v1.csv"
)
ITEM_REPRESENTATIVE_ATTRIBUTES_PATH = (
    PROCESSED_DATA_DIR / "item_representative_attributes_v1.parquet"
)
ITEM_MATERIAL_PIPELINE_DIR = PROJECT_ROOT / "pipelines" / "item_material"
ITEM_MATERIAL_OUTPUT_DIR = PROCESSED_DATA_DIR / "item_material_pipeline"
ITEM_MATERIAL_EVENT_CANDIDATE_PATH = (
    ITEM_MATERIAL_OUTPUT_DIR / "item_material_event_mapping_full.csv"
)
ITEM_MATERIAL_REVIEW_QUEUE_PATH = (
    ITEM_MATERIAL_OUTPUT_DIR / "unresolved_priority_queue_full.csv"
)
ITEM_MATERIAL_GLOSSARY_PATH = ITEM_MATERIAL_OUTPUT_DIR / "meta_code_glossary_full.csv"
ITEM_MATERIAL_PIPELINE_REPORT_PATH = (
    ITEM_MATERIAL_OUTPUT_DIR / "material_pipeline_run_report.json"
)
ITEM_PARENT_CONCEPT_PATH = (
    ITEM_MATERIAL_OUTPUT_DIR / "item_parent_concept_grouping_full.csv"
)
ITEM_PARENT_CONCEPT_SUMMARY_PATH = (
    ITEM_MATERIAL_OUTPUT_DIR / "parent_concept_summary_full.csv"
)
ITEM_INTEGRATED_CLASSIFICATION_CSV_PATH = (
    PROCESSED_DATA_DIR / "item_integrated_classification_v2.csv"
)
ITEM_INTEGRATED_CLASSIFICATION_PARQUET_PATH = (
    PROCESSED_DATA_DIR / "item_integrated_classification_v2.parquet"
)
ITEM_INTEGRATED_CLASSIFICATION_REPORT_PATH = (
    PROCESSED_DATA_DIR / "item_integrated_classification_v2_report.json"
)
ITEM_INTEGRATED_SAMPLE_PATH = (
    SAMPLE_DATA_DIR / "item_integrated_classification_sample_1000.csv"
)
STANDARD_ITEM_MAPPING_PATH = (
    PROCESSED_DATA_DIR / "stock_standard_item_mapping.parquet"
)
STANDARD_ITEM_MAPPING_REPORT_PATH = (
    OUTPUT_DIR / "stock_standard_item_mapping_report.json"
)
STANDARD_ITEM_MAPPING_SAMPLE_PATH = (
    SAMPLE_DATA_DIR / "stock_standard_item_mapping_sample_1000.csv"
)
HISTORICAL_TRAINING_POLICY_PATH = (
    MAPPING_DATA_DIR / "historical_training_policy.json"
)
HISTORICAL_TRAINING_TUNING_REPORT_PATH = (
    OUTPUT_DIR / "historical_training_weight_report.json"
)
ITEM_CLASSIFICATION_EVALUATION_REPORT_PATH = (
    OUTPUT_DIR / "item_classification_evaluation.json"
)
ITEM_CLASSIFICATION_FIELD_METRICS_PATH = (
    OUTPUT_DIR / "item_classification_regression_metrics.csv"
)
ITEM_CLASSIFICATION_CLUSTER_METRICS_PATH = (
    OUTPUT_DIR / "item_classification_reference_cluster_metrics.csv"
)
ITEM_CLASSIFICATION_WEIGHT_SCENARIOS_PATH = (
    OUTPUT_DIR / "item_classification_weight_scenarios.csv"
)
ITEM_CLASSIFICATION_ATTENTION_SAMPLE_PATH = (
    SAMPLE_DATA_DIR / "item_classification_attention_sample_1000.csv"
)
MODULE_C_WEIGHT_SENSITIVITY_PATH = OUTPUT_DIR / "module_c_weight_sensitivity.csv"
MATERIAL_APPROVAL_POLICY_PATH = MAPPING_DATA_DIR / "material_approval_policy.json"
MATERIAL_APPROVAL_AUDIT_PATH = OUTPUT_DIR / "material_mapping_approval_audit.csv"
MATERIAL_APPROVAL_REPORT_PATH = OUTPUT_DIR / "material_mapping_approval_report.json"
MATERIAL_APPROVAL_SAMPLE_PATH = SAMPLE_DATA_DIR / "material_mapping_approval_sample_1000.csv"
MATERIAL_INVENTORY_IMPACT_REPORT_PATH = (
    OUTPUT_DIR / "material_mapping_inventory_impact_report.json"
)
MATERIAL_INVENTORY_IMPACT_DETAIL_PATH = (
    OUTPUT_DIR / "material_mapping_inventory_impact_detail.csv"
)
MATERIAL_INVENTORY_IMPACT_BY_SPEC_PATH = (
    OUTPUT_DIR / "material_mapping_inventory_impact_by_spec.csv"
)
MATERIAL_INVENTORY_IMPACT_SAMPLE_PATH = (
    SAMPLE_DATA_DIR / "material_mapping_inventory_impact_sample_1000.csv"
)
ITEM_FAMILY_TAXONOMY_SEED_PATH = MAPPING_DATA_DIR / "item_family_taxonomy.csv"
APPROVED_ITEM_CLASSIFICATION_SEED_PATH = (
    MAPPING_DATA_DIR / "item_forecast_classification_approved.csv"
)
STOCK_MATERIAL_MAPPING_SEED_PATH = (
    MAPPING_DATA_DIR / "stock_item_material_mapping.csv"
)
ITEM_BULK_APPROVAL_POLICY_PATH = MAPPING_DATA_DIR / "item_bulk_approval_policy.json"
ITEM_BULK_APPROVAL_MARKER_PATH = (
    PROCESSED_DATA_DIR / "item_bulk_approval_active.json"
)
ITEM_BULK_CLASSIFICATION_PATH = (
    PROCESSED_DATA_DIR / "item_forecast_classification_bulk_approved.parquet"
)
ITEM_BULK_TAXONOMY_PATH = (
    PROCESSED_DATA_DIR / "item_family_taxonomy_bulk_approved.parquet"
)
ITEM_BULK_MATERIAL_MAPPING_PATH = (
    PROCESSED_DATA_DIR / "stock_item_material_mapping_bulk_approved.parquet"
)
ITEM_BULK_APPROVAL_REPORT_PATH = OUTPUT_DIR / "item_bulk_approval_report.json"
ITEM_BULK_CLASSIFICATION_SAMPLE_PATH = (
    SAMPLE_DATA_DIR / "item_bulk_classification_approval_sample_1000.csv"
)
ITEM_BULK_MATERIAL_SAMPLE_PATH = (
    SAMPLE_DATA_DIR / "item_bulk_material_approval_sample_1000.csv"
)


def _bulk_approval_is_active() -> bool:
    if not ITEM_BULK_APPROVAL_MARKER_PATH.exists():
        return False
    try:
        marker = json.loads(
            ITEM_BULK_APPROVAL_MARKER_PATH.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return False
    return (
        marker.get("status") == "active"
        and ITEM_BULK_CLASSIFICATION_PATH.exists()
        and ITEM_BULK_TAXONOMY_PATH.exists()
        and ITEM_BULK_MATERIAL_MAPPING_PATH.exists()
    )


if _bulk_approval_is_active():
    ITEM_FAMILY_TAXONOMY_PATH = ITEM_BULK_TAXONOMY_PATH
    APPROVED_ITEM_CLASSIFICATION_PATH = ITEM_BULK_CLASSIFICATION_PATH
    STOCK_MATERIAL_MAPPING_PATH = ITEM_BULK_MATERIAL_MAPPING_PATH
else:
    ITEM_FAMILY_TAXONOMY_PATH = ITEM_FAMILY_TAXONOMY_SEED_PATH
    APPROVED_ITEM_CLASSIFICATION_PATH = APPROVED_ITEM_CLASSIFICATION_SEED_PATH
    STOCK_MATERIAL_MAPPING_PATH = STOCK_MATERIAL_MAPPING_SEED_PATH
MARKET_SERIES_REGISTRY_PATH = MAPPING_DATA_DIR / "market_series_registry.csv"
MATERIAL_MARKET_FACTOR_MAPPING_PATH = (
    MAPPING_DATA_DIR / "material_market_factor_mapping.csv"
)
MODULE_C_RISK_WEIGHT_PATH = MAPPING_DATA_DIR / "module_c_risk_weights.json"
SUPPLY_RISK_LEVEL_POLICY_PATH = (
    MAPPING_DATA_DIR / "supply_risk_level_policy.json"
)
SUPPLY_RISK_ANOMALY_RULES_PATH = (
    MAPPING_DATA_DIR / "supply_risk_anomaly_rules.json"
)
INVENTORY_STATUS_POLICY_PATH = MAPPING_DATA_DIR / "inventory_status_policy.json"
INVENTORY_STATUS_PATH = OUTPUT_DIR / "stock_inventory_status.csv"
INVENTORY_STATUS_REPORT_PATH = OUTPUT_DIR / "stock_inventory_status_report.json"
INVENTORY_STATUS_SAMPLE_PATH = SAMPLE_DATA_DIR / "stock_inventory_status_sample_1000.csv"
MODULE_C_SUPPLY_LEVEL_AUDIT_PATH = (
    OUTPUT_DIR / "module_c_supply_risk_level_audit.csv"
)
MODULE_C_SUPPLY_QUALITY_CLASSIFIED_PATH = (
    OUTPUT_DIR / "module_c_supply_risk_quality_classified.csv"
)
MODULE_C_SUPPLY_QUALITY_ISSUES_PATH = (
    OUTPUT_DIR / "module_c_supply_risk_quality_issues.csv"
)
MODULE_C_SUPPLY_QUALITY_PASSED_PATH = (
    OUTPUT_DIR / "module_c_supply_risk_quality_passed.csv"
)
MODULE_C_SUPPLY_QUALITY_REVIEW_PATH = (
    OUTPUT_DIR / "module_c_supply_risk_quality_review.csv"
)
MODULE_C_SUPPLY_QUALITY_QUARANTINE_PATH = (
    OUTPUT_DIR / "module_c_supply_risk_quality_quarantine.csv"
)
MODULE_C_SUPPLY_QUALITY_REPORT_PATH = (
    OUTPUT_DIR / "module_c_supply_risk_quality_report.json"
)
MODULE_C_SUPPLY_QUALITY_SAMPLE_PATH = (
    SAMPLE_DATA_DIR / "module_c_supply_risk_quality_sample_1000.csv"
)
CENSORED_DEMAND_METRICS_PATH = PROCESSED_DATA_DIR / "censored_demand.parquet"
DEMAND_CLASS_HANDOFF_PATH = OUTPUT_DIR / "demand_class_mu_corrected_handoff.csv"
DEMAND_CLASS_REPORT_PATH = OUTPUT_DIR / "demand_class_mu_corrected_report.json"
DEMAND_CLASS_SAMPLE_PATH = SAMPLE_DATA_DIR / "demand_class_mu_corrected_sample_1000.csv"
INSTITUTION_ID_MAPPING_PATH = MAPPING_DATA_DIR / "institution_id_mapping.csv"
COUNTRY_WEIGHT_PATH = MAPPING_DATA_DIR / "country_weight.csv"
NEWS_RISK_WEIGHT_PATH = MAPPING_DATA_DIR / "news_risk_weights.yaml"

GROUP_KEYS = ["year_month", "institution_code", "department", "item_code"]
SERIES_KEYS = ["institution_code", "department", "item_code"]
CATEGORICAL_FEATURES = [
    "institution_code",
    "department",
    "standard_item_definition_key",
    "standard_item_group_id",
    "standard_item_family_id",
    "standard_item_subtype_id",
    "standard_item_specification",
    "standard_item_unit_code",
    "standardization_match_method",
    "data_period",
]
TARGET_COLUMN = "target_usage"

HISTORICAL_TRAIN_START = "2018-01"
HISTORICAL_TRAIN_END = "2019-12"
TRAIN_START = "2024-01"
TRAIN_END = "2024-12"
VALID_START = "2025-01"
VALID_END = "2025-06"
TEST_START = "2025-07"

# 롤링 원점(rolling-origin) 검증. 각 fold 는 자기 train_end 이후만 평가하므로
# 미래 정보 누출이 없다. fold 폭은 3개월로 통일하고, 검증 총구간을 6개월(2 fold)에서
# 12개월(4 fold)로 넓혔다 — fold 수가 적으면 WAPE 차이가 특정 분기의 계절성에
# 좌우되어 튜닝 결과가 그 분기에 과적합된다(Bergmeir & Benitez 2012,
# "On the use of cross-validation for time series predictor evaluation",
# Information Sciences 191:192-213).
VALIDATION_FOLDS = [
    {
        "fold": "2024_q3",
        "train_end": "2024-06",
        "valid_start": "2024-07",
        "valid_end": "2024-09",
    },
    {
        "fold": "2024_q4",
        "train_end": "2024-09",
        "valid_start": "2024-10",
        "valid_end": "2024-12",
    },
    {
        "fold": "2025_q1",
        "train_end": "2024-12",
        "valid_start": "2025-01",
        "valid_end": "2025-03",
    },
    {
        "fold": "2025_q2",
        "train_end": "2025-03",
        "valid_start": "2025-04",
        "valid_end": "2025-06",
    },
]

MODEL_VARIANTS = {
    "stock_model_a_usage_only": {
        "use_news": False,
        "use_commodity": False,
        "use_module_c": False,
        "objective": "regression_l1",
    },
    "stock_model_a_usage_tweedie": {
        "use_news": False,
        "use_commodity": False,
        "use_module_c": False,
        "objective": "tweedie",
    },
    "stock_model_b_news": {
        "use_news": True,
        "use_commodity": False,
        "use_module_c": False,
        "objective": "tweedie",
    },
    "stock_model_c_news_commodity": {
        "use_news": True,
        "use_commodity": True,
        "use_module_c": False,
        "objective": "tweedie",
    },
    "stock_model_d_module_c": {
        "use_news": False,
        "use_commodity": False,
        "use_module_c": True,
        "objective": "tweedie",
    },
}

SAFETY_STOCK_RATE = 0.20
MAX_RISK_BUFFER_RATE = 0.50
DEMAND_RISK_BUFFER_RATE = 0.20
SUPPLY_RISK_BUFFER_RATE = 0.20
MATERIAL_RISK_BUFFER_RATE = 0.10
DEFAULT_REVIEW_PERIOD_DAYS = 30
DEFAULT_LEAD_TIME_DAYS = 15

RANDOM_STATE = 42
CSV_CHUNK_SIZE = 300_000
