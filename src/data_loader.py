import csv
import logging
from pathlib import Path
import tempfile

import pandas as pd

from .config import CSV_CHUNK_SIZE, GROUP_KEYS, RAW_STOCK_DIR, RAW_STOCK_FILE_PATTERN
from .ledger_rules import (
    LEDGER_TOLERANCE,
    nonnegative_quantity,
    physical_outbound_violation,
)


LOGGER = logging.getLogger(__name__)

RAW_STOCK_COLUMNS = [
    "부서코드",
    "물품코드",
    "물품명",
    "재고마감일",
    "이전최종재고량",
    "마감재고량",
    "구입처코드",
    "구입단가",
    "입고량",
    "불출입고량",
    "반납입고량",
    "불출출고량",
    "정상출고량",
    "반품출고량",
    "폐기출고량",
    "자동폐기출고량",
    "보정출고량",
    "보건기관코드_en",
]

RAW_STOCK_NORMALIZATION_KEY_COLUMNS = [
    "보건기관코드_en",
    "물품코드",
    "물품명",
]
RAW_STOCK_OPTIONAL_COLUMNS = [
    "구입처코드",
    "구입단가",
]
RAW_STOCK_REQUIRED_COLUMNS = [
    column for column in RAW_STOCK_COLUMNS if column not in RAW_STOCK_OPTIONAL_COLUMNS
]

NUMERIC_COLUMN_MAP = {
    "이전최종재고량": "opening_stock",
    "마감재고량": "closing_stock",
    "구입단가": "unit_price",
    "입고량": "purchase_in_qty",
    "불출입고량": "transfer_in_qty",
    "반납입고량": "return_in_qty",
    "불출출고량": "transfer_out_qty",
    "정상출고량": "consumption_qty",
    "반품출고량": "return_out_qty",
    "폐기출고량": "disposal_qty",
    "자동폐기출고량": "auto_disposal_adjustment_qty",
    "보정출고량": "correction_out_qty",
}

SUM_COLUMNS = [
    "purchase_in_qty",
    "transfer_in_qty",
    "return_in_qty",
    "transfer_out_qty",
    "consumption_qty",
    "return_out_qty",
    "disposal_qty",
    "auto_disposal_adjustment_qty",
    "correction_out_qty",
]

LEDGER_QUALITY_COLUMNS = [
    "ledger_document_rule_violation_count",
    "ledger_physical_violation_count",
    "ledger_opening_stock_missing_count",
    "ledger_balance_violation_count",
]

DEMAND_MOMENT_COLUMNS = [
    "normal_outbound_signed_sum",
    "model_demand_positive_sum",
    "normal_outbound_nonnegative_sum",
    "normal_outbound_squared_sum",
    "negative_normal_outbound_count",
    "negative_normal_outbound_amount",
]

def discover_raw_stock_files(
    raw_dir: Path = RAW_STOCK_DIR,
    pattern: str = RAW_STOCK_FILE_PATTERN,
) -> list[Path]:
    return sorted(path for path in raw_dir.glob(pattern) if path.is_file())


def normalize_item_name(value: object) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())


def validate_raw_stock_columns(
    columns: list[str] | None,
    required_columns: list[str],
    path: Path | str,
) -> list[str]:
    actual = list(columns or [])
    duplicate_columns = sorted(
        column for column in set(actual) if actual.count(column) > 1
    )
    if duplicate_columns:
        raise ValueError(
            f"Duplicate raw_stock columns in {path}: {duplicate_columns}"
        )
    missing_columns = [
        column for column in required_columns if column not in actual
    ]
    if missing_columns:
        raise ValueError(
            f"Missing required raw_stock columns in {path}: {missing_columns}"
        )
    return actual


def _read_stock_chunks(path: Path, chunk_size: int = CSV_CHUNK_SIZE):
    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file, delimiter="|", quotechar='"')
        validate_raw_stock_columns(
            reader.fieldnames,
            RAW_STOCK_REQUIRED_COLUMNS,
            path,
        )
        for row in reader:
            if None in row:
                raise ValueError(f"Malformed raw_stock record in {path} near physical line {reader.line_num}")
            rows.append(
                {
                    column: row.get(column, "")
                    for column in RAW_STOCK_COLUMNS
                }
            )
            if len(rows) >= chunk_size:
                yield pd.DataFrame.from_records(rows)
                rows = []
    if rows:
        yield pd.DataFrame.from_records(rows)


def normalize_stock_chunk(chunk: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame(index=chunk.index)
    result["closing_date"] = pd.to_datetime(chunk["재고마감일"], format="%Y%m%d", errors="coerce")
    result["year_month"] = result["closing_date"].dt.to_period("M").dt.to_timestamp()
    result["institution_code"] = chunk["보건기관코드_en"].fillna("").astype(str).str.strip()
    result["department"] = chunk["부서코드"].fillna("").astype(str).str.strip()
    result["item_code"] = chunk["물품코드"].fillna("").astype(str).str.strip()
    result["item_name"] = chunk["물품명"].map(normalize_item_name)
    result["vendor_code"] = chunk["구입처코드"].fillna("").astype(str).str.strip()

    for source, target in NUMERIC_COLUMN_MAP.items():
        result[target] = pd.to_numeric(chunk[source], errors="coerce")
    result[SUM_COLUMNS] = result[SUM_COLUMNS].fillna(0.0)

    opening_known = result["opening_stock"].notna()
    opening_available = nonnegative_quantity(result["opening_stock"])
    signed_normal_outbound = result["consumption_qty"]
    positive_normal_outbound = signed_normal_outbound.clip(lower=0.0)
    negative_normal_outbound = signed_normal_outbound.lt(0)
    result["normal_outbound_signed_sum"] = signed_normal_outbound
    result["model_demand_positive_sum"] = positive_normal_outbound
    # Backward-compatible alias. New model code must use
    # model_demand_positive_sum so the ledger and model contracts cannot be
    # confused (ai#65).
    result["normal_outbound_nonnegative_sum"] = positive_normal_outbound
    result["normal_outbound_squared_sum"] = positive_normal_outbound.pow(2)
    result["negative_normal_outbound_count"] = (
        negative_normal_outbound.astype("int8")
    )
    result["negative_normal_outbound_amount"] = (
        -signed_normal_outbound.where(negative_normal_outbound, 0.0)
    )
    purchase_available = result["purchase_in_qty"].clip(lower=0)
    document_available = opening_available + purchase_available
    result["ledger_document_rule_violation_count"] = (
        opening_known
        & result["consumption_qty"].gt(
            result["opening_stock"]
            + result["purchase_in_qty"]
            + LEDGER_TOLERANCE
        )
    ).astype("int8")
    result["ledger_physical_violation_count"] = physical_outbound_violation(
        result["consumption_qty"],
        result["opening_stock"],
        result["purchase_in_qty"],
        result["transfer_in_qty"],
        result["return_in_qty"],
    ).astype("int8")
    result["ledger_opening_stock_missing_count"] = (~opening_known).astype("int8")
    ledger_expected_closing = (
        result["opening_stock"]
        + result["purchase_in_qty"]
        + result["transfer_in_qty"]
        + result["return_in_qty"]
        - result["transfer_out_qty"]
        - result["consumption_qty"]
        - result["return_out_qty"]
        - result["disposal_qty"]
        - result["correction_out_qty"]
    )
    closing_known = result["closing_stock"].notna()
    result["ledger_balance_residual"] = (
        result["closing_stock"] - ledger_expected_closing
    ).where(opening_known & closing_known)
    result["ledger_balance_violation_count"] = (
        opening_known
        & closing_known
        & result["ledger_balance_residual"].abs().gt(LEDGER_TOLERANCE)
    ).astype("int8")

    invalid = (
        result["closing_date"].isna()
        | result["institution_code"].eq("")
        | result["department"].eq("")
        | result["item_code"].eq("")
    )
    if invalid.any():
        LOGGER.warning("Dropping %s raw_stock rows with missing keys or invalid dates", int(invalid.sum()))
        result = result.loc[~invalid].copy()
    return result


def aggregate_stock_chunk(chunk: pd.DataFrame) -> pd.DataFrame:
    normalized = normalize_stock_chunk(chunk)
    if normalized.empty:
        return pd.DataFrame()

    normalized = normalized.sort_values([*GROUP_KEYS, "closing_date"])
    normalized["closing_stock_sum"] = normalized["closing_stock"].fillna(0.0)
    normalized["stock_observation_count"] = normalized["closing_stock"].notna().astype(int)
    normalized["stockout_observation_count"] = normalized["closing_stock"].eq(0).astype(int)
    normalized["negative_stock_observation_count"] = normalized["closing_stock"].lt(0).astype(int)
    normalized["unit_price_sum"] = normalized["unit_price"].fillna(0.0)
    normalized["unit_price_count"] = normalized["unit_price"].notna().astype(int)

    aggregations = {
        "first_date": ("closing_date", "first"),
        "last_date": ("closing_date", "last"),
        "item_name": ("item_name", "last"),
        "vendor_code": ("vendor_code", "last"),
        "month_opening_stock": ("opening_stock", "first"),
        "month_end_stock": ("closing_stock", "last"),
        "minimum_stock": ("closing_stock", "min"),
        "maximum_stock": ("closing_stock", "max"),
        "closing_stock_sum": ("closing_stock_sum", "sum"),
        "stock_observation_count": ("stock_observation_count", "sum"),
        "stockout_observation_count": ("stockout_observation_count", "sum"),
        "negative_stock_observation_count": ("negative_stock_observation_count", "sum"),
        "unit_price_sum": ("unit_price_sum", "sum"),
        "unit_price_count": ("unit_price_count", "sum"),
        "ledger_balance_residual_sum": ("ledger_balance_residual", "sum"),
    }
    aggregations.update(
        {
            column: (column, "sum")
            for column in [
                *SUM_COLUMNS,
                *DEMAND_MOMENT_COLUMNS,
                *LEDGER_QUALITY_COLUMNS,
            ]
        }
    )
    return normalized.groupby(GROUP_KEYS, as_index=False).agg(**aggregations)


def _combine_stock_partials(partials: list[pd.DataFrame]) -> pd.DataFrame:
    combined = pd.concat(partials, ignore_index=True)
    combined = combined.sort_values([*GROUP_KEYS, "first_date", "last_date"])
    aggregations = {
        "first_date": ("first_date", "min"),
        "last_date": ("last_date", "max"),
        "item_name": ("item_name", "last"),
        "vendor_code": ("vendor_code", "last"),
        "month_opening_stock": ("month_opening_stock", "first"),
        "month_end_stock": ("month_end_stock", "last"),
        "minimum_stock": ("minimum_stock", "min"),
        "maximum_stock": ("maximum_stock", "max"),
        "closing_stock_sum": ("closing_stock_sum", "sum"),
        "stock_observation_count": ("stock_observation_count", "sum"),
        "stockout_observation_count": ("stockout_observation_count", "sum"),
        "negative_stock_observation_count": ("negative_stock_observation_count", "sum"),
        "unit_price_sum": ("unit_price_sum", "sum"),
        "unit_price_count": ("unit_price_count", "sum"),
        "ledger_balance_residual_sum": ("ledger_balance_residual_sum", "sum"),
    }
    aggregations.update(
        {
            column: (column, "sum")
            for column in [
                *SUM_COLUMNS,
                *DEMAND_MOMENT_COLUMNS,
                *LEDGER_QUALITY_COLUMNS,
            ]
        }
    )
    monthly = combined.groupby(GROUP_KEYS, as_index=False).agg(**aggregations)
    monthly["average_stock"] = monthly["closing_stock_sum"] / monthly["stock_observation_count"].replace(0, pd.NA)
    monthly["average_unit_price"] = monthly["unit_price_sum"] / monthly["unit_price_count"].replace(0, pd.NA)
    monthly["inbound_qty"] = monthly[["purchase_in_qty", "transfer_in_qty", "return_in_qty"]].sum(axis=1)
    monthly["other_outbound_qty"] = monthly[
        [
            "transfer_out_qty",
            "return_out_qty",
            "disposal_qty",
            "correction_out_qty",
        ]
    ].sum(axis=1)
    monthly["auto_disposal_excluded_from_demand_and_ledger"] = True
    monthly["net_stock_change"] = monthly["month_end_stock"] - monthly["month_opening_stock"]
    monthly["stockout_rate"] = monthly["stockout_observation_count"] / monthly["stock_observation_count"].replace(0, pd.NA)
    monthly["stock_item_key"] = (
        monthly["institution_code"] + "::" + monthly["department"] + "::" + monthly["item_code"]
    )
    monthly["vendor_code"] = monthly["vendor_code"].fillna("").astype(str)
    return monthly.drop(columns=["closing_stock_sum", "unit_price_sum"]).sort_values(GROUP_KEYS).reset_index(drop=True)


def load_stock_data(
    raw_dir: Path = RAW_STOCK_DIR,
    pattern: str = RAW_STOCK_FILE_PATTERN,
    chunk_size: int = CSV_CHUNK_SIZE,
) -> pd.DataFrame:
    files = discover_raw_stock_files(raw_dir, pattern)
    if not files:
        raise FileNotFoundError(f"No raw_stock DAT files found under {raw_dir} with pattern {pattern}")

    LOGGER.info("Reading %s raw_stock files from %s", len(files), raw_dir)
    monthly_outputs = []
    with tempfile.TemporaryDirectory(prefix="wep_stock_aggregation_") as temp_directory:
        partition_dir = Path(temp_directory)
        partition_paths: dict[str, Path] = {}
        valid_rows_found = False

        for path in files:
            LOGGER.info("Reading %s", path)
            for chunk in _read_stock_chunks(path, chunk_size):
                aggregated = aggregate_stock_chunk(chunk)
                if aggregated.empty:
                    continue
                valid_rows_found = True
                for year_month, partition in aggregated.groupby("year_month", sort=False):
                    month_key = pd.Timestamp(year_month).strftime("%Y%m")
                    partition_path = partition_paths.setdefault(
                        month_key,
                        partition_dir / f"stock_partial_{month_key}.csv",
                    )
                    partition.to_csv(
                        partition_path,
                        mode="a",
                        header=not partition_path.exists(),
                        index=False,
                    )

        if not valid_rows_found:
            raise ValueError("No valid raw_stock rows remained after normalization")

        for month_key, partition_path in sorted(partition_paths.items()):
            LOGGER.info("Combining raw_stock monthly partition: %s", month_key)
            partition = pd.read_csv(
                partition_path,
                parse_dates=["year_month", "first_date", "last_date"],
                dtype={
                    "institution_code": str,
                    "department": str,
                    "item_code": str,
                    "item_name": str,
                    "vendor_code": str,
                },
            )
            monthly_outputs.append(_combine_stock_partials([partition]))

    return pd.concat(monthly_outputs, ignore_index=True).sort_values(GROUP_KEYS).reset_index(drop=True)
