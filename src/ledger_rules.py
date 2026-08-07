from __future__ import annotations

import pandas as pd


LEDGER_TOLERANCE = 1e-9


def nonnegative_quantity(values: pd.Series) -> pd.Series:
    """Return the non-negative contribution of a ledger quantity."""
    return pd.to_numeric(values, errors="coerce").fillna(0.0).clip(lower=0.0)


def physical_available_stock(
    opening_stock: pd.Series,
    purchase_in: pd.Series,
    transfer_in: pd.Series,
    return_in: pd.Series,
) -> pd.Series:
    """Stock physically available for normal outbound on a ledger row."""
    return (
        nonnegative_quantity(opening_stock)
        + nonnegative_quantity(purchase_in)
        + nonnegative_quantity(transfer_in)
        + nonnegative_quantity(return_in)
    )


def physical_outbound_violation(
    normal_outbound: pd.Series,
    opening_stock: pd.Series,
    purchase_in: pd.Series,
    transfer_in: pd.Series,
    return_in: pd.Series,
    *,
    tolerance: float = LEDGER_TOLERANCE,
) -> pd.Series:
    """Flag physically impossible outbound while preserving missing-opening state."""
    opening = pd.to_numeric(opening_stock, errors="coerce")
    available = physical_available_stock(
        opening,
        purchase_in,
        transfer_in,
        return_in,
    )
    return (
        opening.notna()
        & nonnegative_quantity(normal_outbound).gt(available + tolerance)
    )
