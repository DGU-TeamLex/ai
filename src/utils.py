import json
import logging
from pathlib import Path

import pandas as pd


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def ensure_dirs(*paths: Path) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def write_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_year_month(series: pd.Series) -> pd.Series:
    values = series.astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    return pd.to_datetime(values, format="%Y%m").dt.to_period("M").dt.to_timestamp()


def safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    denominator = denominator.replace(0, pd.NA)
    return (numerator / denominator).astype("float64").fillna(0.0)

