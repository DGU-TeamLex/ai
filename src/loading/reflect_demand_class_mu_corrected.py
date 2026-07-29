from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import (
    DEMAND_CLASS_HANDOFF_PATH,
    DEMAND_CLASS_REPORT_PATH,
    INSTITUTION_ID_MAPPING_PATH,
)


HANDOFF_COLUMNS = [
    "anon_institution_code",
    "standard_code",
    "demand_class",
    "mu_corrected",
    "review_required",
    "load_eligible",
]
EXPLICIT_MAPPING_COLUMNS = ["anon_institution_code", "institution_id"]
LOADABLE_DEMAND_CLASSES = {"ACTIVE", "CENSORED", "DORMANT"}


def _as_bool(series: pd.Series) -> pd.Series:
    return (
        series.astype("string")
        .fillna("")
        .str.strip()
        .str.lower()
        .isin({"true", "t", "1", "yes", "y"})
    )


def load_release_report(report_path: Path) -> dict:
    if not report_path.exists():
        raise FileNotFoundError(f"Demand-class quality report not found: {report_path}")
    with report_path.open("r", encoding="utf-8") as file:
        report = json.load(file)
    if not bool(report.get("batch_release_allowed", False)):
        raise ValueError(
            "Demand-class batch is not releasable: "
            f"quality_status={report.get('quality_status', 'unknown')}"
        )
    if bool(report.get("status_update_included", True)):
        raise ValueError("Demand-class batch must not include an inventory status update")
    return report


def load_institution_mapping(
    mapping_path: Path,
    anon_codes: list[str],
    allow_legacy_sorted_zip: bool = False,
) -> pd.DataFrame:
    if not mapping_path.exists():
        raise FileNotFoundError(f"Institution mapping not found: {mapping_path}")
    mapping = pd.read_csv(mapping_path, dtype=str, keep_default_na=False)
    anon_codes = sorted({str(value).strip() for value in anon_codes if str(value).strip()})
    if not anon_codes:
        raise ValueError("Handoff contains no anonymous institution codes")

    if set(EXPLICIT_MAPPING_COLUMNS).issubset(mapping.columns):
        result = mapping[EXPLICIT_MAPPING_COLUMNS].copy()
        for column in EXPLICIT_MAPPING_COLUMNS:
            result[column] = result[column].astype(str).str.strip()
            if result[column].eq("").any():
                raise ValueError(f"Institution mapping contains an empty {column}")
        if result["anon_institution_code"].duplicated().any():
            raise ValueError("Institution mapping contains duplicate anonymous codes")
        if result["institution_id"].duplicated().any():
            raise ValueError("Institution mapping contains duplicate institution IDs")
        missing = sorted(set(anon_codes) - set(result["anon_institution_code"]))
        if missing:
            raise ValueError(
                f"Institution mapping is missing {len(missing)} anonymous codes"
            )
        return result[result["anon_institution_code"].isin(anon_codes)].copy()

    if "institution_id" not in mapping.columns:
        raise ValueError(
            "Institution mapping requires anon_institution_code and institution_id columns"
        )
    if not allow_legacy_sorted_zip:
        raise ValueError(
            "A one-column institution list would require unsafe sorted-zip mapping. "
            "Provide an explicit anon_institution_code,institution_id mapping or pass "
            "--allow-legacy-sorted-zip for a controlled dry run only."
        )

    institution_ids = sorted(
        {
            str(value).strip()
            for value in mapping["institution_id"]
            if str(value).strip()
        }
    )
    if len(anon_codes) != len(institution_ids):
        raise ValueError(
            "Institution count mismatch blocks sorted-zip mapping: "
            f"anonymous={len(anon_codes)}, institution_ids={len(institution_ids)}"
        )
    return pd.DataFrame(
        {
            "anon_institution_code": anon_codes,
            "institution_id": institution_ids,
        }
    )


def prepare_update_frame(
    handoff: pd.DataFrame,
    institution_mapping: pd.DataFrame,
) -> pd.DataFrame:
    missing = sorted(set(HANDOFF_COLUMNS) - set(handoff.columns))
    if missing:
        raise ValueError(f"Demand-class handoff is missing columns: {missing}")
    if handoff[["anon_institution_code", "standard_code"]].duplicated().any():
        raise ValueError("Demand-class handoff contains duplicate institution-item keys")

    result = handoff.copy()
    result["anon_institution_code"] = (
        result["anon_institution_code"].astype(str).str.strip()
    )
    result["standard_code"] = result["standard_code"].astype(str).str.strip()
    result["demand_class"] = result["demand_class"].astype(str).str.strip().str.upper()
    result["mu_corrected"] = pd.to_numeric(
        result["mu_corrected"], errors="coerce"
    )
    result["review_required"] = _as_bool(result["review_required"])
    result["load_eligible"] = _as_bool(result["load_eligible"])
    result = result[
        result["load_eligible"]
        & ~result["review_required"]
        & result["demand_class"].isin(LOADABLE_DEMAND_CLASSES)
    ].copy()
    if result.empty:
        raise ValueError("No verified demand-class rows are eligible for loading")
    if (
        result["mu_corrected"].isna().any()
        or not np.isfinite(result["mu_corrected"]).all()
        or result["mu_corrected"].lt(0).any()
    ):
        raise ValueError("Loadable mu_corrected values must be finite and non-negative")

    mapping = institution_mapping[EXPLICIT_MAPPING_COLUMNS].copy()
    result = result.merge(
        mapping,
        on="anon_institution_code",
        how="left",
        validate="many_to_one",
    )
    if result["institution_id"].isna().any():
        raise ValueError("Some anonymous institution codes have no explicit mapping")
    output = result[
        ["institution_id", "standard_code", "demand_class", "mu_corrected"]
    ].copy()
    if output[["institution_id", "standard_code"]].duplicated().any():
        raise ValueError("Mapped update frame contains duplicate inventory keys")
    return output.reset_index(drop=True)


def _load_psycopg():
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "psycopg is required for DB loading. Install requirements.txt first."
        ) from exc
    return psycopg


def reflect_to_database(
    update_frame: pd.DataFrame,
    database_url: str,
    apply_changes: bool = False,
    minimum_match_rate: float = 0.99,
) -> dict[str, object]:
    if not 0 < minimum_match_rate <= 1:
        raise ValueError("minimum_match_rate must be within (0, 1]")
    if not str(database_url).strip():
        raise ValueError("DATABASE_URL is required")
    psycopg = _load_psycopg()

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'inventory'
                  AND column_name IN ('demand_class', 'mu_corrected')
                """
            )
            existing_columns = {row[0] for row in cursor.fetchall()}
            missing_columns = {"demand_class", "mu_corrected"} - existing_columns
            if missing_columns:
                raise RuntimeError(
                    f"inventory table is missing columns: {sorted(missing_columns)}"
                )

            cursor.execute("DROP TABLE IF EXISTS _demand_class_update")
            cursor.execute(
                """
                CREATE TEMP TABLE _demand_class_update (
                    institution_id TEXT NOT NULL,
                    standard_code TEXT NOT NULL,
                    demand_class TEXT NOT NULL,
                    mu_corrected DOUBLE PRECISION NOT NULL
                ) ON COMMIT DROP
                """
            )
            buffer = io.StringIO()
            update_frame.to_csv(buffer, index=False, header=False)
            buffer.seek(0)
            with cursor.copy(
                "COPY _demand_class_update FROM STDIN WITH (FORMAT CSV)"
            ) as copy:
                copy.write(buffer.read())

            cursor.execute(
                """
                SELECT count(*)
                FROM inventory i
                JOIN _demand_class_update u
                  ON i.institution_id = u.institution_id
                 AND i.standard_code = u.standard_code
                """
            )
            matched_rows = int(cursor.fetchone()[0])
            match_rate = matched_rows / len(update_frame)
            if match_rate < minimum_match_rate:
                connection.rollback()
                raise RuntimeError(
                    "Inventory match rate is below the release threshold: "
                    f"{match_rate:.2%} < {minimum_match_rate:.2%}"
                )

            updated_rows = 0
            if apply_changes:
                cursor.execute(
                    """
                    UPDATE inventory i
                    SET demand_class = u.demand_class,
                        mu_corrected = u.mu_corrected,
                        updated_at = now()
                    FROM _demand_class_update u
                    WHERE i.institution_id = u.institution_id
                      AND i.standard_code = u.standard_code
                    """
                )
                updated_rows = int(cursor.rowcount)
                connection.commit()
            else:
                connection.rollback()

    return {
        "input_rows": int(len(update_frame)),
        "matched_rows": matched_rows,
        "match_rate": match_rate,
        "updated_rows": updated_rows,
        "apply_changes": apply_changes,
        "status_column_updated": False,
    }


def run_reflection(
    handoff_path: Path = DEMAND_CLASS_HANDOFF_PATH,
    report_path: Path = DEMAND_CLASS_REPORT_PATH,
    mapping_path: Path = INSTITUTION_ID_MAPPING_PATH,
    database_url: str | None = None,
    apply_changes: bool = False,
    allow_legacy_sorted_zip: bool = False,
    minimum_match_rate: float = 0.99,
) -> dict[str, object]:
    load_release_report(report_path)
    if not handoff_path.exists():
        raise FileNotFoundError(f"Demand-class handoff not found: {handoff_path}")
    handoff = pd.read_csv(handoff_path, dtype={"anon_institution_code": str})
    mapping = load_institution_mapping(
        mapping_path,
        handoff["anon_institution_code"].tolist(),
        allow_legacy_sorted_zip=allow_legacy_sorted_zip,
    )
    update_frame = prepare_update_frame(handoff, mapping)
    return reflect_to_database(
        update_frame,
        database_url=database_url or os.environ.get("DATABASE_URL", ""),
        apply_changes=apply_changes,
        minimum_match_rate=minimum_match_rate,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Guarded batch load for demand_class and mu_corrected"
    )
    parser.add_argument("--handoff-path", type=Path, default=DEMAND_CLASS_HANDOFF_PATH)
    parser.add_argument("--report-path", type=Path, default=DEMAND_CLASS_REPORT_PATH)
    parser.add_argument("--mapping-path", type=Path, default=INSTITUTION_ID_MAPPING_PATH)
    parser.add_argument("--database-url")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-legacy-sorted-zip", action="store_true")
    parser.add_argument("--minimum-match-rate", type=float, default=0.99)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_reflection(
        handoff_path=args.handoff_path,
        report_path=args.report_path,
        mapping_path=args.mapping_path,
        database_url=args.database_url,
        apply_changes=args.apply,
        allow_legacy_sorted_zip=args.allow_legacy_sorted_zip,
        minimum_match_rate=args.minimum_match_rate,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
