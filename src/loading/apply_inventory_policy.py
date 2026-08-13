"""Apply AI demand statistics to DB inventory without choosing an SS/ROP model.

Issue #54 recorded a policy conflict between two models:

* backend: continuous review, ``SS=z*sigma*sqrt(L)``, ``ROP=mu*L+SS``
* AI: periodic review plus lead time, fixed/risk-adjusted target stock

The cadence question is resolved (ai#54 request 1, 2026-07-30 confirmation from
reo-23): institutions place orders on a monthly cadence, so periodic review is
the canonical model. What is NOT yet done is applying that decision to the DB —
the SS/ROP recalculation itself is a separate, still-pending PR. Until that PR
lands, this loader updates only demand statistics and classification fields. It
deliberately does not write ``ss``, ``rop``, ``target``, ``status`` or
``order_recommendation``.

Run with ``DRY_RUN=1`` first. ``DRY_RUN=0`` commits the restricted update.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
import re

import numpy as np
import pandas as pd


HANDOFF = Path(
    os.environ.get("HANDOFF", "data/handoff/inventory_policy.csv")
)
DRY_RUN = os.environ.get("DRY_RUN", "1") == "1"
MIN_JOIN_RATIO = float(os.environ.get("MIN_JOIN_RATIO", "0.90"))

HANDOFF_COLUMNS = [
    "institution_id",
    "standard_code",
    "on_hand",
    "mu",
    "sigma",
    "mu_forecast",
    "demand_class",
    "zero_stock_reason",
    "order_suppress_reason",
]
NUMERIC_COLUMNS = ["on_hand", "mu", "sigma", "mu_forecast"]
PROTECTED_POLICY_COLUMNS = {
    "ss",
    "rop",
    "target",
    "status",
    "order_recommendation",
    "z_used",
    "lead_time_used",
    "supply_risk_level",
}

UPDATE_SQL = """
    UPDATE inventory i SET
        mu = u.mu,
        sigma = u.sigma,
        mu_forecast = u.mu_forecast,
        demand_class = coalesce(u.demand_class, i.demand_class),
        zero_stock_reason = coalesce(
            u.zero_stock_reason,
            i.zero_stock_reason
        ),
        updated_at = now()
    FROM _inv_policy u
    WHERE i.institution_id = u.institution_id
      AND i.standard_code = u.standard_code
"""


def prepare_handoff(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate the handoff before any database connection is opened."""
    missing = sorted(set(HANDOFF_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"handoff is missing required columns: {missing}")
    result = frame[HANDOFF_COLUMNS].copy()
    for column in ["institution_id", "standard_code"]:
        result[column] = result[column].astype("string").fillna("").str.strip()
    if result[["institution_id", "standard_code"]].eq("").any().any():
        raise ValueError("handoff contains blank join keys")
    if result.duplicated(["institution_id", "standard_code"]).any():
        raise ValueError("handoff contains duplicate inventory keys")
    for column in NUMERIC_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    numeric = result[NUMERIC_COLUMNS].to_numpy(dtype="float64")
    if not np.isfinite(numeric).all():
        raise ValueError("handoff contains non-finite demand statistics")
    if result[NUMERIC_COLUMNS].lt(0).any().any():
        raise ValueError("handoff demand statistics must be non-negative")
    return result


def validate_restricted_update_sql(sql: str = UPDATE_SQL) -> None:
    """Fail closed if a future edit reintroduces unresolved policy writes."""
    match = re.search(r"\bset\b(?P<body>.*?)\bfrom\b", sql, flags=re.I | re.S)
    if match is None:
        raise ValueError("inventory update SQL has no SET/FROM assignment block")
    assigned = set(
        re.findall(
            r"^\s*([a-z_][a-z0-9_]*)\s*=",
            match.group("body"),
            flags=re.I | re.M,
        )
    )
    protected = sorted(assigned & PROTECTED_POLICY_COLUMNS)
    if protected:
        raise ValueError(
            f"unresolved policy columns cannot be updated: {protected}"
        )


def main() -> None:
    import psycopg

    validate_restricted_update_sql()
    frame = prepare_handoff(pd.read_csv(HANDOFF))
    print(f"handoff {len(frame):,}행 로드 ({HANDOFF})")
    print(
        "적용 범위: mu, sigma, mu_forecast, demand_class, "
        "zero_stock_reason"
    )
    print(
        "보호 범위: SS/ROP/target/status/order_recommendation "
        "(정기검토 모형 확정됨(ai#54 요청1) — DB 재계산은 별도 PR 대기, 이 로더는 미변경)"
    )

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT count(*),
                   count(*) FILTER (WHERE mu <= 0.5),
                   count(*) FILTER (WHERE sigma <= 0.1)
            FROM inventory
            """
        )
        total, low_mu_before, low_sigma_before = cursor.fetchone()
        print(
            f"반영 전: 전체 {total:,} · mu<=0.5 {low_mu_before:,} · "
            f"sigma<=0.1 {low_sigma_before:,}"
        )

        cursor.execute(
            """
            CREATE TEMP TABLE _inv_policy (
                institution_id TEXT,
                standard_code TEXT,
                on_hand DOUBLE PRECISION,
                mu DOUBLE PRECISION,
                sigma DOUBLE PRECISION,
                mu_forecast DOUBLE PRECISION,
                demand_class TEXT,
                zero_stock_reason TEXT,
                order_suppress_reason TEXT
            ) ON COMMIT DROP
            """
        )
        buffer = io.StringIO()
        frame.to_csv(buffer, index=False, header=False)
        buffer.seek(0)
        with cursor.copy(
            "COPY _inv_policy FROM STDIN WITH (FORMAT CSV)"
        ) as copy:
            copy.write(buffer.read())

        cursor.execute(
            """
            SELECT count(*)
            FROM inventory i
            JOIN _inv_policy u USING (institution_id, standard_code)
            """
        )
        joined = cursor.fetchone()[0]
        ratio = joined / len(frame) if len(frame) else 0.0
        print(
            f"조인: {joined:,}/{len(frame):,} ({ratio * 100:.1f}%) · "
            f"DB 미포함 {len(frame) - joined:,}"
        )
        if ratio < MIN_JOIN_RATIO:
            raise SystemExit(
                f"[중단] 조인율 {ratio * 100:.1f}% < "
                f"{MIN_JOIN_RATIO * 100:.0f}%. 기관/품목 키를 먼저 검증할 것."
            )

        cursor.execute(UPDATE_SQL)
        print(f"UPDATE {cursor.rowcount:,}행")
        cursor.execute(
            """
            SELECT count(*) FILTER (WHERE mu <= 0.5),
                   count(*) FILTER (WHERE sigma <= 0.1)
            FROM inventory
            """
        )
        low_mu_after, low_sigma_after = cursor.fetchone()
        print(
            f"반영 후: mu<=0.5 {low_mu_after:,} · "
            f"sigma<=0.1 {low_sigma_after:,}"
        )

        if DRY_RUN:
            conn.rollback()
            print("*** DRY_RUN=1 - 롤백 완료 ***")
        else:
            conn.commit()
            print("수요 통계 반영 완료. SS/ROP 재계산은 ai#54 결정 후 별도 수행.")


if __name__ == "__main__":
    main()
