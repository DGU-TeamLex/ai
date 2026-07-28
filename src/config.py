from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Real data is stored by year under device/, e.g.
# device/DEVICE_2025 (...)/DEVICE_202501 (...).csv
RAW_DATA_DIR = PROJECT_ROOT / "device"
RAW_FILE_PATTERN = "**/*.csv"

PROCESSED_DATA_DIR = PROJECT_ROOT / "data" / "processed"
SAMPLE_DATA_DIR = PROJECT_ROOT / "data" / "sample"
MAPPING_DATA_DIR = PROJECT_ROOT / "data" / "mapping"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
MODEL_DIR = PROJECT_ROOT / "models"
EXTERNAL_FEATURE_PATH = PROJECT_ROOT / "data" / "external_features.csv"

MONTHLY_USAGE_PATH = PROCESSED_DATA_DIR / "usage_monthly.csv"
FEATURE_TABLE_PATH = OUTPUT_DIR / "feature_table.csv"
NEWS_RISK_SCORE_PATH = OUTPUT_DIR / "news_risk_scores.csv"
COMMODITY_RISK_SCORE_PATH = OUTPUT_DIR / "commodity_risk_scores.csv"
PREDICTION_PATH = OUTPUT_DIR / "predictions.csv"
EVALUATION_REPORT_PATH = OUTPUT_DIR / "evaluation_report.csv"

DEVICE_MATERIAL_MAPPING_PATH = MAPPING_DATA_DIR / "device_material_mapping.csv"
COUNTRY_WEIGHT_PATH = MAPPING_DATA_DIR / "country_weight.csv"

# Change this value if the public health institution code is corrected later.
PUBLIC_HEALTH_CODE = 7

GROUP_KEYS = ["year_month", "SIDO", "MED_DEVICE_5"]
CATEGORICAL_FEATURES = ["SIDO", "MED_DEVICE_5"]
TARGET_COLUMN = "target_usage"

TRAIN_END = "2023-12"
VALID_START = "2024-01"
VALID_END = "2024-12"
TEST_START = "2025-01"

MODEL_VARIANTS = {
    "model_a_usage_only": {
        "use_news": False,
        "use_commodity": False,
    },
    "model_b_news": {
        "use_news": True,
        "use_commodity": False,
    },
    "model_c_news_commodity": {
        "use_news": True,
        "use_commodity": True,
    },
}

SAFETY_STOCK_RATE = 0.20
MAX_RISK_BUFFER_RATE = 0.50

RANDOM_STATE = 42
CSV_CHUNK_SIZE = 300_000
