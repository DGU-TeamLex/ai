import logging

from .config import MONTHLY_STOCK_PATH, OUTPUT_DIR, PROCESSED_DATA_DIR
from .data_loader import load_stock_data
from .utils import ensure_dirs, setup_logging


LOGGER = logging.getLogger(__name__)


def run_preprocessing() -> None:
    setup_logging()
    ensure_dirs(PROCESSED_DATA_DIR, OUTPUT_DIR)
    monthly_stock = load_stock_data()
    monthly_stock.to_csv(MONTHLY_STOCK_PATH, index=False)
    LOGGER.info("Saved monthly stock table: %s (%s rows)", MONTHLY_STOCK_PATH, len(monthly_stock))


if __name__ == "__main__":
    run_preprocessing()
