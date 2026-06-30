import logging
from pathlib import Path

import pandas as pd

try:
    from .data_loader import aggregate_usage_chunk, discover_raw_files, load_usage_data
except ImportError:
    from data_loader import aggregate_usage_chunk, discover_raw_files, load_usage_data


LOGGER = logging.getLogger(__name__)

_aggregate_chunk = aggregate_usage_chunk


def load_and_aggregate_raw() -> pd.DataFrame:
    return load_usage_data()
