from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

RAW_STOCK_DIR = PROJECT_ROOT / "raw_stock"
RAW_STOCK_FILE_PATTERN = "*.DAT"

PROCESSED_DATA_DIR = PROJECT_ROOT / "data" / "processed"
SAMPLE_DATA_DIR = PROJECT_ROOT / "data" / "sample"
MAPPING_DATA_DIR = PROJECT_ROOT / "data" / "mapping"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
MODEL_DIR = PROJECT_ROOT / "models"
EXTERNAL_FEATURE_PATH = PROJECT_ROOT / "data" / "external_features.csv"

MONTHLY_STOCK_PATH = PROCESSED_DATA_DIR / "stock_monthly.csv"
FEATURE_TABLE_PATH = OUTPUT_DIR / "stock_feature_table.csv"
NEWS_RISK_SCORE_PATH = OUTPUT_DIR / "stock_news_risk_scores.csv"
NEWS_ARTICLE_SCORE_PATH = OUTPUT_DIR / "stock_news_article_scores.csv"
COMMODITY_RISK_SCORE_PATH = OUTPUT_DIR / "stock_commodity_risk_scores.csv"
PREDICTION_PATH = OUTPUT_DIR / "stock_predictions.csv"
EVALUATION_REPORT_PATH = OUTPUT_DIR / "stock_evaluation_report.csv"
MODEL_VALIDATION_REPORT_PATH = OUTPUT_DIR / "stock_model_validation_report.csv"
MODEL_MANIFEST_PATH = MODEL_DIR / "stock_manifest.json"

STOCK_MATERIAL_MAPPING_PATH = MAPPING_DATA_DIR / "stock_item_material_mapping.csv"
COUNTRY_WEIGHT_PATH = MAPPING_DATA_DIR / "country_weight.csv"
NEWS_RISK_WEIGHT_PATH = MAPPING_DATA_DIR / "news_risk_weights.yaml"

GROUP_KEYS = ["year_month", "institution_code", "department", "item_code"]
SERIES_KEYS = ["institution_code", "department", "item_code"]
CATEGORICAL_FEATURES = ["institution_code", "department", "item_code"]
TARGET_COLUMN = "target_usage"

TRAIN_END = "2025-03"
VALID_START = "2025-04"
VALID_END = "2025-06"
TEST_START = "2025-07"

MODEL_VARIANTS = {
    "stock_model_a_usage_only": {
        "use_news": False,
        "use_commodity": False,
    },
    "stock_model_b_news": {
        "use_news": True,
        "use_commodity": False,
    },
    "stock_model_c_news_commodity": {
        "use_news": True,
        "use_commodity": True,
    },
}

SAFETY_STOCK_RATE = 0.20
MAX_RISK_BUFFER_RATE = 0.50

RANDOM_STATE = 42
CSV_CHUNK_SIZE = 300_000
