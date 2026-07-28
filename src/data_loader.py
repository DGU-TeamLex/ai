import logging
from pathlib import Path

import pandas as pd

try:
    from .config import CSV_CHUNK_SIZE, GROUP_KEYS, PUBLIC_HEALTH_CODE, RAW_DATA_DIR, RAW_FILE_PATTERN
    from .utils import read_year_month, safe_divide
except ImportError:
    from config import CSV_CHUNK_SIZE, GROUP_KEYS, PUBLIC_HEALTH_CODE, RAW_DATA_DIR, RAW_FILE_PATTERN
    from utils import read_year_month, safe_divide


LOGGER = logging.getLogger(__name__)

RAW_COLUMNS = [
    "STD_YYYYMM",
    "MED_DEVICE_5",
    "AGE_G",
    "SEX_TYPE",
    "SIDO",
    "YOYANG_CLSFC_CD_ADJ",
    "OUT_IN_PAT",
    "PRSCRPTN_TNDN_CNT",
    "PRSCRPTN_AMT",
    "PATIENT_CNT",
    "PRSCRPTN_TOT_USE",
]

NUMERIC_COLUMNS = [
    "PRSCRPTN_TNDN_CNT",
    "PRSCRPTN_AMT",
    "PATIENT_CNT",
    "PRSCRPTN_TOT_USE",
]


def discover_raw_files(raw_dir: Path = RAW_DATA_DIR, pattern: str = RAW_FILE_PATTERN) -> list[Path]:
    files = sorted(raw_dir.glob(pattern))
    return [path for path in files if path.is_file()]


def _is_elderly(age: pd.Series) -> pd.Series:
    numeric_age = pd.to_numeric(age, errors="coerce")
    if numeric_age.notna().any():
        return numeric_age >= 60
    return age.astype(str).str.extract(r"(\d+)")[0].astype("float64") >= 60


def aggregate_usage_chunk(chunk: pd.DataFrame) -> pd.DataFrame:
    chunk = chunk.copy()
    chunk = chunk[chunk["YOYANG_CLSFC_CD_ADJ"].astype(str) == str(PUBLIC_HEALTH_CODE)]
    if chunk.empty:
        return pd.DataFrame()

    chunk["year_month"] = read_year_month(chunk["STD_YYYYMM"])
    chunk["SIDO"] = chunk["SIDO"].astype(str)
    chunk["MED_DEVICE_5"] = chunk["MED_DEVICE_5"].astype(str)
    chunk["SEX_TYPE"] = chunk["SEX_TYPE"].astype(str)
    chunk["OUT_IN_PAT"] = chunk["OUT_IN_PAT"].astype(str).str.upper()

    for col in NUMERIC_COLUMNS:
        chunk[col] = pd.to_numeric(chunk[col], errors="coerce").fillna(0.0)

    total_use = chunk["PRSCRPTN_TOT_USE"]
    chunk["elderly_use"] = total_use.where(_is_elderly(chunk["AGE_G"]), 0.0)
    chunk["sex_1_use"] = total_use.where(chunk["SEX_TYPE"] == "1", 0.0)
    chunk["sex_2_use"] = total_use.where(chunk["SEX_TYPE"] == "2", 0.0)
    chunk["in_use"] = total_use.where(chunk["OUT_IN_PAT"].str.startswith("IN"), 0.0)
    chunk["out_use"] = total_use.where(chunk["OUT_IN_PAT"].str.startswith("OUT"), 0.0)

    return (
        chunk.groupby(GROUP_KEYS, as_index=False)
        .agg(
            total_use=("PRSCRPTN_TOT_USE", "sum"),
            total_count=("PRSCRPTN_TNDN_CNT", "sum"),
            total_amount=("PRSCRPTN_AMT", "sum"),
            patient_count=("PATIENT_CNT", "sum"),
            elderly_use=("elderly_use", "sum"),
            sex_1_use=("sex_1_use", "sum"),
            sex_2_use=("sex_2_use", "sum"),
            in_use=("in_use", "sum"),
            out_use=("out_use", "sum"),
        )
    )


def load_usage_data() -> pd.DataFrame:
    files = discover_raw_files()
    if not files:
        raise FileNotFoundError(f"No CSV files found under {RAW_DATA_DIR} with pattern {RAW_FILE_PATTERN}")

    LOGGER.info("Reading %s raw CSV files from %s", len(files), RAW_DATA_DIR)
    partials = []
    for file_path in files:
        LOGGER.info("Reading %s", file_path)
        for chunk in pd.read_csv(file_path, usecols=RAW_COLUMNS, chunksize=CSV_CHUNK_SIZE):
            aggregated = aggregate_usage_chunk(chunk)
            if not aggregated.empty:
                partials.append(aggregated)

    if not partials:
        raise ValueError(
            f"No rows remained after filtering YOYANG_CLSFC_CD_ADJ == {PUBLIC_HEALTH_CODE}. "
            "Change PUBLIC_HEALTH_CODE in src/config.py if needed."
        )

    df = pd.concat(partials, ignore_index=True)
    df = (
        df.groupby(GROUP_KEYS, as_index=False)
        .agg(
            total_use=("total_use", "sum"),
            total_count=("total_count", "sum"),
            total_amount=("total_amount", "sum"),
            patient_count=("patient_count", "sum"),
            elderly_use=("elderly_use", "sum"),
            sex_1_use=("sex_1_use", "sum"),
            sex_2_use=("sex_2_use", "sum"),
            in_use=("in_use", "sum"),
            out_use=("out_use", "sum"),
        )
        .sort_values(GROUP_KEYS)
        .reset_index(drop=True)
    )

    df["elderly_use_ratio"] = safe_divide(df["elderly_use"], df["total_use"])
    df["sex_1_use_ratio"] = safe_divide(df["sex_1_use"], df["total_use"])
    df["sex_2_use_ratio"] = safe_divide(df["sex_2_use"], df["total_use"])
    df["in_use_ratio"] = safe_divide(df["in_use"], df["total_use"])
    df["out_use_ratio"] = safe_divide(df["out_use"], df["total_use"])
    return df.drop(columns=["elderly_use", "sex_1_use", "sex_2_use", "in_use", "out_use"])
