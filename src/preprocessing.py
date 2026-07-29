import gc
import logging

import pandas as pd

from .config import (
    CURRENT_RAW_STOCK_FILE_PATTERN,
    HISTORICAL_MONTHLY_STOCK_PATH,
    HISTORICAL_RAW_STOCK_FILE_PATTERN,
    MONTHLY_STOCK_PATH,
    OUTPUT_DIR,
    PROCESSED_DATA_DIR,
    STOCK_PREPROCESSING_REPORT_PATH,
)
from .data_loader import load_stock_data
from .utils import ensure_dirs, setup_logging, write_json


LOGGER = logging.getLogger(__name__)


def _stock_summary(frame: pd.DataFrame, path, role: str) -> dict[str, object]:
    return {
        "role": role,
        "path": str(path),
        "rows": int(len(frame)),
        "start_month": pd.Timestamp(frame["year_month"].min()).strftime("%Y-%m"),
        "end_month": pd.Timestamp(frame["year_month"].max()).strftime("%Y-%m"),
        "month_count": int(frame["year_month"].nunique()),
        "institution_count": int(frame["institution_code"].nunique()),
        "department_count": int(frame["department"].nunique()),
        "item_count": int(frame["item_code"].nunique()),
        "series_count": int(frame["stock_item_key"].nunique()),
        "ledger_balance_violation_rows": int(
            frame["ledger_balance_violation_count"].sum()
        ),
        "automatic_disposal_excluded": bool(
            frame["auto_disposal_excluded_from_demand_and_ledger"].all()
        ),
    }


def run_preprocessing() -> dict[str, object]:
    setup_logging()
    ensure_dirs(PROCESSED_DATA_DIR, OUTPUT_DIR)
    monthly_stock = load_stock_data(pattern=CURRENT_RAW_STOCK_FILE_PATTERN)
    monthly_stock.to_parquet(MONTHLY_STOCK_PATH, index=False, compression="zstd")
    LOGGER.info("Saved monthly stock table: %s (%s rows)", MONTHLY_STOCK_PATH, len(monthly_stock))
    current_summary = _stock_summary(
        monthly_stock,
        MONTHLY_STOCK_PATH,
        "model_input",
    )
    del monthly_stock
    gc.collect()

    historical = load_stock_data(pattern=HISTORICAL_RAW_STOCK_FILE_PATTERN)
    historical.to_parquet(
        HISTORICAL_MONTHLY_STOCK_PATH,
        index=False,
        compression="zstd",
    )
    LOGGER.info(
        "Saved auxiliary historical monthly stock table: %s (%s rows)",
        HISTORICAL_MONTHLY_STOCK_PATH,
        len(historical),
    )
    historical_summary = _stock_summary(
        historical,
        HISTORICAL_MONTHLY_STOCK_PATH,
        "standardization_and_training_candidate",
    )
    del historical
    gc.collect()

    report = {
        "version": "stock-preprocessing-v1.2",
        "training_data_policy": (
            "current_2024_2025_plus_validation_weighted_matched_2018_2019"
        ),
        "historical_data_policy": (
            "matched_to_current_standard_item_only_with_separate_lag_segments"
        ),
        "demand_definition": "normal_outbound_only",
        "automatic_disposal_handling": (
            "excluded_from_demand_and_ledger_balance"
        ),
        "current": current_summary,
        "historical_auxiliary": historical_summary,
    }
    write_json(report, STOCK_PREPROCESSING_REPORT_PATH)
    LOGGER.info(
        "Saved stock preprocessing report: %s",
        STOCK_PREPROCESSING_REPORT_PATH,
    )
    return report


if __name__ == "__main__":
    run_preprocessing()
