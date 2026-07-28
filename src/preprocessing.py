import logging

from .config import MONTHLY_USAGE_PATH, OUTPUT_DIR, PROCESSED_DATA_DIR
from .data_loader import load_usage_data
from .utils import ensure_dirs, setup_logging


LOGGER = logging.getLogger(__name__)


def run_preprocessing() -> None:
    setup_logging()
    ensure_dirs(PROCESSED_DATA_DIR, OUTPUT_DIR)
    monthly_usage = load_usage_data()
    monthly_usage.to_csv(MONTHLY_USAGE_PATH, index=False)
    LOGGER.info("Saved monthly usage table: %s (%s rows)", MONTHLY_USAGE_PATH, len(monthly_usage))


if __name__ == "__main__":
    run_preprocessing()

