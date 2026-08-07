"""Build the zero-stock reason handoff from the normalized stock ledger.

DATA_MISSING uses the physical availability rule shared with ``src.data_loader``:

    normal_outbound >
        max(opening, 0) + max(purchase_in, 0)
        + max(transfer_in, 0) + max(return_in, 0)

The narrower document rule that omits transfer/return inbound remains an audit
metric in the preprocessing pipeline, but it must not drive automatic status.
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from src.ledger_rules import nonnegative_quantity, physical_outbound_violation


LEDGER_PATH = Path(
    os.path.expanduser(
        os.environ.get(
            "LEDGER_PATH",
            "~/Downloads/물품재고_정규화완료.parquet",
        )
    )
)
INST_IDS = Path(
    os.environ.get("INST_IDS", "data/mapping/institution_ids_sorted.csv")
)
INST_CODE_MAP = os.environ.get("INST_CODE_MAP", "")
OUT_PATH = Path(
    os.environ.get("OUT", "data/handoff/zero_stock_reason.csv")
)
RECENT_MONTHS = int(os.environ.get("RECENT_MONTHS", "3"))
ZERO_STOCK_POLICY_VERSION = "inventory-status-v1.1-physical"

LEDGER_COLUMNS = [
    "보건기관코드_en",
    "물품코드",
    "재고마감일",
    "정상출고량",
    "마감재고량",
    "이전최종재고량",
    "입고량",
    "불출입고량",
    "반납입고량",
]
KEY_COLUMNS = ["보건기관코드_en", "물품코드"]


def build_zero_stock_reasons(
    ledger: pd.DataFrame,
    *,
    recent_months: int = RECENT_MONTHS,
) -> pd.DataFrame:
    """Classify zero-stock institution/item pairs without DB key mapping."""
    missing = sorted(set(LEDGER_COLUMNS) - set(ledger.columns))
    if missing:
        raise ValueError(f"ledger is missing required columns: {missing}")
    if recent_months <= 0:
        raise ValueError("recent_months must be positive")

    frame = ledger.copy()
    frame["재고마감일"] = pd.to_datetime(
        frame["재고마감일"],
        errors="coerce",
    )
    if frame["재고마감일"].isna().any():
        raise ValueError("ledger contains invalid 재고마감일 values")

    frame["normal_outbound_nonnegative"] = nonnegative_quantity(
        frame["정상출고량"]
    )
    frame["physical_violation"] = physical_outbound_violation(
        frame["정상출고량"],
        frame["이전최종재고량"],
        frame["입고량"],
        frame["불출입고량"],
        frame["반납입고량"],
    )
    frame = frame.sort_values(
        [*KEY_COLUMNS, "재고마감일"],
        kind="mergesort",
    )

    grouped = (
        frame.groupby(KEY_COLUMNS, observed=True, sort=False)
        .agg(
            ship_sum=("normal_outbound_nonnegative", "sum"),
            violations=("physical_violation", "sum"),
            last_stock=("마감재고량", "last"),
        )
        .reset_index()
    )

    latest_period = frame["재고마감일"].max().to_period("M")
    recent_periods = {
        str(latest_period - offset) for offset in range(recent_months)
    }
    frame["year_month"] = frame["재고마감일"].dt.to_period("M").astype(str)
    recent = (
        frame.loc[frame["year_month"].isin(recent_periods)]
        .groupby(KEY_COLUMNS, observed=True, sort=False)[
            "normal_outbound_nonnegative"
        ]
        .sum()
        .rename("recent_demand")
        .reset_index()
    )
    grouped = grouped.merge(
        recent,
        on=KEY_COLUMNS,
        how="left",
        validate="one_to_one",
    )
    grouped["recent_demand"] = grouped["recent_demand"].fillna(0.0)

    zero = grouped[
        pd.to_numeric(grouped["last_stock"], errors="coerce")
        .fillna(0.0)
        .le(0.0)
    ].copy()
    zero["zero_stock_reason"] = "TRUE_STOCKOUT"
    zero.loc[zero["ship_sum"].le(0), "zero_stock_reason"] = "NOT_OPERATED"
    zero.loc[
        zero["ship_sum"].gt(0) & zero["violations"].gt(0),
        "zero_stock_reason",
    ] = "DATA_MISSING"
    return zero


def load_institution_mapping(
    anonymous_codes: pd.Series,
    *,
    explicit_mapping_path: str = INST_CODE_MAP,
    institution_ids_path: Path = INST_IDS,
) -> dict[str, str]:
    """Load an explicit mapping, or fail closed unless sorted sets align."""
    if explicit_mapping_path:
        mapping_frame = pd.read_csv(explicit_mapping_path)
        aliases = {
            "anon_institution_code": "institution_code",
            "보건기관코드_en": "institution_code",
        }
        mapping_frame = mapping_frame.rename(columns=aliases)
        required = {"institution_code", "institution_id"}
        missing = sorted(required - set(mapping_frame.columns))
        if missing:
            raise ValueError(
                f"institution mapping is missing columns: {missing}"
            )
        mapping_frame = mapping_frame.dropna(subset=list(required))
        if mapping_frame["institution_code"].duplicated().any():
            raise ValueError("institution mapping contains duplicate source codes")
        return dict(
            zip(
                mapping_frame["institution_code"].astype(str),
                mapping_frame["institution_id"].astype(str),
            )
        )

    anonymous = sorted(anonymous_codes.dropna().astype(str).unique())
    real = sorted(
        pd.read_csv(institution_ids_path)["institution_id"]
        .dropna()
        .astype(str)
        .unique()
    )
    if len(anonymous) != len(real):
        raise ValueError(
            "sorted-zip institution mapping is unsafe: "
            f"anonymous={len(anonymous):,}, institution_id={len(real):,}. "
            "Provide a verified two-column INST_CODE_MAP."
        )
    return dict(zip(anonymous, real))


def main() -> None:
    if not LEDGER_PATH.exists():
        raise SystemExit(f"[중단] 원장 파일이 없다: {LEDGER_PATH}")
    ledger = pd.read_parquet(LEDGER_PATH, columns=LEDGER_COLUMNS)
    zero = build_zero_stock_reasons(ledger)

    mapping = load_institution_mapping(ledger["보건기관코드_en"])
    zero["institution_id"] = zero["보건기관코드_en"].astype(str).map(mapping)
    if zero["institution_id"].isna().any():
        count = int(zero["institution_id"].isna().sum())
        raise SystemExit(f"[중단] 기관 매핑 실패 {count:,}행")

    output = zero.rename(columns={"물품코드": "standard_code"})[
        [
            "institution_id",
            "standard_code",
            "zero_stock_reason",
            "recent_demand",
        ]
    ].rename(columns={"recent_demand": "recent3m"})
    output["policy_version"] = ZERO_STOCK_POLICY_VERSION
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    print(f"저장: {OUT_PATH} ({len(output):,}행)")
    for reason, count in output["zero_stock_reason"].value_counts().items():
        recent = int(
            (
                output["zero_stock_reason"].eq(reason)
                & output["recent3m"].gt(0)
            ).sum()
        )
        print(
            f"  {reason:<15} {count:>8,} "
            f"({count / max(1, len(output)) * 100:4.1f}%) "
            f"| 최근 {RECENT_MONTHS}개월 수요>0 {recent:,}"
        )


if __name__ == "__main__":
    main()
