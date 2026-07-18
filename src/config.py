from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

RAW_STOCK_DIR = PROJECT_ROOT / "raw_stock"
RAW_STOCK_FILE_PATTERN = "*.DAT"

PROCESSED_DATA_DIR = PROJECT_ROOT / "data" / "processed"
SAMPLE_DATA_DIR = PROJECT_ROOT / "data" / "sample"
MAPPING_DATA_DIR = PROJECT_ROOT / "data" / "mapping"
EXTERNAL_MASTER_DIR = PROJECT_ROOT / "data" / "external" / "official"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
MODEL_DIR = PROJECT_ROOT / "models"
EXTERNAL_FEATURE_PATH = PROJECT_ROOT / "data" / "external_features.csv"

MONTHLY_STOCK_PATH = PROCESSED_DATA_DIR / "stock_monthly.parquet"
FEATURE_TABLE_PATH = OUTPUT_DIR / "stock_feature_table.parquet"
NEWS_RISK_SCORE_PATH = OUTPUT_DIR / "stock_news_risk_scores.csv"
NEWS_ARTICLE_SCORE_PATH = OUTPUT_DIR / "stock_news_article_scores.csv"
COMMODITY_RISK_SCORE_PATH = OUTPUT_DIR / "stock_commodity_risk_scores.csv"
PREDICTION_PATH = OUTPUT_DIR / "stock_predictions.csv"
BACKTEST_PREDICTION_PATH = OUTPUT_DIR / "stock_backtest_predictions.csv"
CLASSIFIED_PREDICTION_PATH = OUTPUT_DIR / "stock_predictions_by_subtype.csv"
CLASSIFIED_PREDICTION_QUALITY_PATH = OUTPUT_DIR / "stock_predictions_by_subtype_quality.json"
EVALUATION_REPORT_PATH = OUTPUT_DIR / "stock_evaluation_report.csv"
EVALUATION_SEGMENT_REPORT_PATH = OUTPUT_DIR / "stock_evaluation_by_segment.csv"
MODEL_VALIDATION_REPORT_PATH = OUTPUT_DIR / "stock_model_validation_report.csv"
MODEL_CV_REPORT_PATH = OUTPUT_DIR / "stock_model_cv_report.csv"
FORECAST_DATA_QUALITY_REPORT_PATH = OUTPUT_DIR / "stock_forecast_data_quality.json"
MODEL_MANIFEST_PATH = MODEL_DIR / "stock_manifest.json"
ITEM_ALIAS_CANDIDATE_PATH = PROCESSED_DATA_DIR / "item_alias_candidates_v0.3.parquet"
ITEM_GROUPED_VERIFIED_PATH = PROCESSED_DATA_DIR / "item_grouped_verified_v1.parquet"
ITEM_PRODUCT_WORKLIST_PATH = PROCESSED_DATA_DIR / "item_product_worklist_v1.parquet"
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
ITEM_FAMILY_TAXONOMY_PATH = MAPPING_DATA_DIR / "item_family_taxonomy.csv"
APPROVED_ITEM_CLASSIFICATION_PATH = MAPPING_DATA_DIR / "item_forecast_classification_approved.csv"

STOCK_MATERIAL_MAPPING_PATH = MAPPING_DATA_DIR / "stock_item_material_mapping.csv"
COUNTRY_WEIGHT_PATH = MAPPING_DATA_DIR / "country_weight.csv"
NEWS_RISK_WEIGHT_PATH = MAPPING_DATA_DIR / "news_risk_weights.yaml"

GROUP_KEYS = ["year_month", "institution_code", "department", "item_code"]
SERIES_KEYS = ["institution_code", "department", "item_code"]
CATEGORICAL_FEATURES = ["institution_code", "department", "item_code"]
TARGET_COLUMN = "target_usage"

TRAIN_END = "2024-12"
VALID_START = "2025-01"
VALID_END = "2025-06"
TEST_START = "2025-07"

VALIDATION_FOLDS = [
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
        "objective": "regression_l1",
    },
    "stock_model_a_usage_tweedie": {
        "use_news": False,
        "use_commodity": False,
        "objective": "tweedie",
    },
    "stock_model_b_news": {
        "use_news": True,
        "use_commodity": False,
        "objective": "tweedie",
    },
    "stock_model_c_news_commodity": {
        "use_news": True,
        "use_commodity": True,
        "objective": "tweedie",
    },
}

SAFETY_STOCK_RATE = 0.20
MAX_RISK_BUFFER_RATE = 0.50
DEMAND_RISK_BUFFER_RATE = 0.20
SUPPLY_RISK_BUFFER_RATE = 0.20
MATERIAL_RISK_BUFFER_RATE = 0.10
DEFAULT_REVIEW_PERIOD_DAYS = 30
DEFAULT_LEAD_TIME_DAYS = 0

RANDOM_STATE = 42
CSV_CHUNK_SIZE = 300_000
