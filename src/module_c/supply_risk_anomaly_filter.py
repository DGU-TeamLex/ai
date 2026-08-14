from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import (
    SUPPLY_RISK_ANOMALY_RULES_PATH,
)
from ..utils import write_json
from .supply_risk_policy import (
    DERIVED_COLUMNS,
    calculate_level_based_safety_stock,
    derive_supply_risk_frame,
    load_supply_risk_policy,
)


ISSUE_COLUMNS = [
    "record_key",
    "row_index",
    "scope",
    "issue_code",
    "severity",
    "action",
    "description",
    "observed_value",
    "expected_value",
    "details",
    "recommended_action",
    "rules_version",
    "policy_version",
]
ACTION_RANK = {"PASS": 0, "REVIEW": 1, "BLOCK": 2}
VALID_LEVELS = {"NORMAL", "CAUTION", "WARNING", "CRITICAL"}
SAMPLE_STATUS_WEIGHTS = {"BLOCK": 0.50, "REVIEW": 0.40, "PASS": 0.10}


def validate_anomaly_rules(config: dict[str, Any]) -> dict[str, Any]:
    if not str(config.get("version", "")).strip():
        raise ValueError("Anomaly rules version is required")
    rules = config.get("rules", {})
    if not rules:
        raise ValueError("Anomaly rules are required")
    for code, rule in rules.items():
        if not code.startswith("SR"):
            raise ValueError(f"Invalid supply risk issue code: {code}")
        if rule.get("action") not in {"REVIEW", "BLOCK"}:
            raise ValueError(f"Invalid action for {code}")
        if rule.get("severity") not in {"WARNING", "ERROR", "BLOCKER"}:
            raise ValueError(f"Invalid severity for {code}")
    tolerance = config.get("numeric_tolerance", {})
    if float(tolerance.get("absolute", -1)) < 0 or float(
        tolerance.get("relative", -1)
    ) < 0:
        raise ValueError("Numeric tolerances must be non-negative")
    return config


def load_anomaly_rules(
    path: Path = SUPPLY_RISK_ANOMALY_RULES_PATH,
) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return validate_anomaly_rules(json.load(file))


def _split_codes(value: object) -> set[str]:
    if value is None or pd.isna(value):
        return set()
    return {code.strip() for code in str(value).split(";") if code.strip()}


def _as_bool(value: object) -> bool:
    return str(value).strip().lower() in {"true", "t", "1", "yes", "y"}


def _normalized_level(value: object) -> str:
    normalized = str(value).strip().upper()
    return {
        "LOW": "NORMAL",
        "MEDIUM": "WARNING",
        "HIGH": "WARNING",
        "WATCH": "CAUTION",
    }.get(normalized, normalized)


def _numeric(value: object) -> tuple[float | None, bool]:
    if value is None or (isinstance(value, str) and not value.strip()) or pd.isna(value):
        return None, False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, True
    return (number, False) if math.isfinite(number) else (None, True)


def _has_value(value: object) -> bool:
    if value is None or pd.isna(value):
        return False
    return not isinstance(value, str) or bool(value.strip())


def _first_existing(columns: list[str], frame: pd.DataFrame) -> str | None:
    return next((column for column in columns if column in frame.columns), None)


def _is_close(observed: float, expected: float, config: dict[str, Any]) -> bool:
    tolerance = config["numeric_tolerance"]
    return math.isclose(
        observed,
        expected,
        rel_tol=float(tolerance["relative"]),
        abs_tol=float(tolerance["absolute"]),
    )


def _record_keys(frame: pd.DataFrame, key_column: str | None) -> pd.Series:
    selected = key_column or _first_existing(
        [
            "inventory_id",
            "standard_code",
            "local_item_key",
            "stock_item_key",
            "representative_item_id",
        ],
        frame,
    )
    if selected:
        return frame[selected].astype("string").fillna("")
    return pd.Series([f"row-{index}" for index in frame.index], index=frame.index)


def _issue(
    issues: list[dict[str, object]],
    rules: dict[str, Any],
    policy: dict[str, Any],
    *,
    record_key: str,
    row_index: object,
    code: str,
    observed: object = "",
    expected: object = "",
    details: str = "",
    scope: str = "row",
) -> None:
    rule = rules["rules"][code]
    issues.append(
        {
            "record_key": record_key,
            "row_index": row_index,
            "scope": scope,
            "issue_code": code,
            "severity": rule["severity"],
            "action": rule["action"],
            "description": rule["description"],
            "observed_value": observed,
            "expected_value": expected,
            "details": details,
            "recommended_action": rule["recommended_action"],
            "rules_version": rules["version"],
            "policy_version": policy["version"],
        }
    )


def _base_with_current_derivation(
    frame: pd.DataFrame,
    code_column: str,
    policy: dict[str, Any],
) -> pd.DataFrame:
    base = frame.drop(
        columns=[
            column
            for column in [
                *DERIVED_COLUMNS,
                "source_supply_risk_level",
                "supply_risk_level_mismatch",
            ]
            if column in frame.columns
        ]
    ).copy()
    return derive_supply_risk_frame(
        base,
        code_column=code_column,
        existing_level_column=None,
        policy=policy,
    )


def classify_supply_risk_anomalies(
    frame: pd.DataFrame,
    *,
    key_column: str | None = None,
    code_column: str | None = None,
    operational_mode: bool = False,
    policy: dict[str, Any] | None = None,
    rules: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    policy = policy or load_supply_risk_policy()
    rules = rules or load_anomaly_rules()
    selected_code = code_column or _first_existing(
        ["supply_risk_meta_code", "raw_material_risk_meta_code"], frame
    )
    working = frame.copy()
    if selected_code is None:
        selected_code = "_quality_supply_risk_meta_code"
        working[selected_code] = ""

    observed_level_column = _first_existing(
        ["supply_risk_level", "source_supply_risk_level"], working
    )
    observed_policy_version = (
        working["supply_risk_policy_version"].copy()
        if "supply_risk_policy_version" in working.columns
        else pd.Series("", index=working.index, dtype="string")
    )
    working = _base_with_current_derivation(working, selected_code, policy)
    keys = _record_keys(working, key_column)
    working["quality_record_key"] = keys

    z_column = _first_existing(
        ["z_value", "z_used", "supply_risk_z", "baseline_supply_risk_z"],
        frame,
    )
    multiplier_column = _first_existing(
        [
            "lead_time_multiplier",
            "lt_mult",
            "supply_lead_time_multiplier",
            "baseline_lead_time_multiplier",
        ],
        frame,
    )
    ss_column = _first_existing(["ss", "SS", "safety_stock"], frame)
    rop_column = _first_existing(["rop", "ROP", "reorder_point"], frame)
    event_level_column = _first_existing(
        ["event_supply_risk_level", "module_c_event_supply_risk_level"], frame
    )
    issues: list[dict[str, object]] = []

    required_operational = {
        "code": selected_code if selected_code in frame.columns else None,
        "level": observed_level_column,
        "policy_version": (
            "supply_risk_policy_version"
            if "supply_risk_policy_version" in frame.columns
            else None
        ),
        "z": z_column,
        "lead_time_multiplier": multiplier_column,
    }
    missing_operational_labels = []
    if operational_mode:
        for label, column in required_operational.items():
            if column is None:
                missing_operational_labels.append(label)
                _issue(
                    issues,
                    rules,
                    policy,
                    record_key="__DATASET__",
                    row_index="",
                    code="SR021_REQUIRED_OPERATIONAL_FIELD_MISSING",
                    observed=label,
                    expected="required operational column",
                    scope="dataset",
                )

    aliases = policy.get("legacy_code_aliases", {})
    event_codes = set(policy.get("dynamic_event_codes", []))
    code_rules = {
        rule["supply_risk_meta_code"]: rule for rule in policy["code_rules"]
    }
    level_ranks = {
        level: int(values["rank"]) for level, values in policy["levels"].items()
    }
    numeric_input_columns = [
        "mean_daily_usage",
        "daily_demand_stddev",
        "lead_time_days",
    ]

    for index, row in working.iterrows():
        record_key = str(keys.loc[index])
        raw_codes = _split_codes(row.get(selected_code, ""))
        expected_level = row["baseline_supply_risk_level"]
        expected_z = float(row["baseline_supply_risk_z"])
        expected_multiplier = float(row["baseline_lead_time_multiplier"])

        legacy_codes = sorted(raw_codes & set(aliases))
        if legacy_codes:
            _issue(
                issues,
                rules,
                policy,
                record_key=record_key,
                row_index=index,
                code="SR018_LEGACY_CODE_ALIAS_USED",
                observed=";".join(legacy_codes),
                expected=";".join(sorted({aliases[code] for code in legacy_codes})),
            )

        non_supply_codes = sorted(
            {
                code
                for code in raw_codes
                if code in event_codes
                or (
                    code_rules.get(aliases.get(code, code), {}).get("risk_axis")
                    in {"demand", "event"}
                )
            }
        )
        if non_supply_codes:
            _issue(
                issues,
                rules,
                policy,
                record_key=record_key,
                row_index=index,
                code="SR019_NON_SUPPLY_CODE_IN_BASELINE",
                observed=";".join(non_supply_codes),
                expected="event/demand axis",
            )

        if str(row["unmapped_supply_risk_codes"]).strip():
            _issue(
                issues,
                rules,
                policy,
                record_key=record_key,
                row_index=index,
                code="SR003_UNMAPPED_META_CODE",
                observed=row["unmapped_supply_risk_codes"],
                expected="approved policy code",
            )

        observed_level = ""
        if observed_level_column:
            observed_level = _normalized_level(frame.loc[index, observed_level_column])
            if observed_level and observed_level not in VALID_LEVELS:
                _issue(
                    issues,
                    rules,
                    policy,
                    record_key=record_key,
                    row_index=index,
                    code="SR020_INVALID_LEVEL_VALUE",
                    observed=observed_level,
                    expected="NORMAL/CAUTION/WARNING/CRITICAL",
                )
            elif observed_level and observed_level != expected_level:
                _issue(
                    issues,
                    rules,
                    policy,
                    record_key=record_key,
                    row_index=index,
                    code="SR001_LEVEL_POLICY_MISMATCH",
                    observed=observed_level,
                    expected=expected_level,
                )
                if non_supply_codes and level_ranks.get(observed_level, 0) > 0:
                    _issue(
                        issues,
                        rules,
                        policy,
                        record_key=record_key,
                        row_index=index,
                        code="SR002_DEMAND_AXIS_IN_SUPPLY_LEVEL",
                        observed=f"{';'.join(non_supply_codes)} -> {observed_level}",
                        expected="NORMAL baseline or separate event level",
                    )

        row_missing_operational = list(missing_operational_labels)
        if operational_mode:
            if not raw_codes:
                row_missing_operational.append("code value")
            if not observed_level:
                row_missing_operational.append("level value")
            if not str(observed_policy_version.loc[index]).strip():
                row_missing_operational.append("policy version value")
            if z_column:
                z_value, _ = _numeric(frame.loc[index, z_column])
                if z_value is None:
                    row_missing_operational.append("z value")
            if multiplier_column:
                multiplier_value, _ = _numeric(frame.loc[index, multiplier_column])
                if multiplier_value is None:
                    row_missing_operational.append("lead-time multiplier value")
        if row_missing_operational:
            _issue(
                issues,
                rules,
                policy,
                record_key=record_key,
                row_index=index,
                code="SR021_REQUIRED_OPERATIONAL_FIELD_MISSING",
                observed=";".join(sorted(set(row_missing_operational))),
                expected="complete operational supply-risk contract",
            )

        if observed_level == "CRITICAL" and row["supply_risk_level_source"] != "approved_critical_override":
            _issue(
                issues,
                rules,
                policy,
                record_key=record_key,
                row_index=index,
                code="SR004_UNAPPROVED_CRITICAL",
                observed="CRITICAL",
                expected="approved critical override evidence",
            )

        if event_level_column and observed_level:
            event_level = _normalized_level(frame.loc[index, event_level_column])
            if event_level == observed_level and observed_level != expected_level:
                _issue(
                    issues,
                    rules,
                    policy,
                    record_key=record_key,
                    row_index=index,
                    code="SR012_EVENT_LEVEL_OVERWROTE_BASELINE",
                    observed=observed_level,
                    expected=expected_level,
                )

        version = str(observed_policy_version.loc[index]).strip()
        if (operational_mode and not version) or (version and version != policy["version"]):
            _issue(
                issues,
                rules,
                policy,
                record_key=record_key,
                row_index=index,
                code="SR005_POLICY_VERSION_STALE_OR_MISSING",
                observed=version or "missing",
                expected=policy["version"],
            )

        if z_column:
            observed_z, invalid = _numeric(frame.loc[index, z_column])
            if invalid or (observed_z is not None and observed_z < 0):
                _issue(
                    issues,
                    rules,
                    policy,
                    record_key=record_key,
                    row_index=index,
                    code="SR010_INVALID_NUMERIC_INPUT",
                    observed=f"{z_column}={frame.loc[index, z_column]}",
                    expected="finite non-negative number",
                )
            elif observed_z is not None and not _is_close(observed_z, expected_z, rules):
                _issue(
                    issues,
                    rules,
                    policy,
                    record_key=record_key,
                    row_index=index,
                    code="SR006_Z_VALUE_MISMATCH",
                    observed=observed_z,
                    expected=expected_z,
                )

        if multiplier_column:
            observed_multiplier, invalid = _numeric(frame.loc[index, multiplier_column])
            if invalid or (
                observed_multiplier is not None and observed_multiplier < 0
            ):
                _issue(
                    issues,
                    rules,
                    policy,
                    record_key=record_key,
                    row_index=index,
                    code="SR010_INVALID_NUMERIC_INPUT",
                    observed=f"{multiplier_column}={frame.loc[index, multiplier_column]}",
                    expected="finite non-negative number",
                )
            elif observed_multiplier is not None and not _is_close(
                observed_multiplier, expected_multiplier, rules
            ):
                _issue(
                    issues,
                    rules,
                    policy,
                    record_key=record_key,
                    row_index=index,
                    code="SR007_LEAD_TIME_MULTIPLIER_MISMATCH",
                    observed=observed_multiplier,
                    expected=expected_multiplier,
                )

        invalid_inputs = []
        numeric_inputs: dict[str, float] = {}
        for column in numeric_input_columns:
            if column not in frame.columns:
                continue
            value, invalid = _numeric(frame.loc[index, column])
            if invalid or (value is not None and value < 0):
                invalid_inputs.append(f"{column}={frame.loc[index, column]}")
            elif value is not None:
                numeric_inputs[column] = value
        if invalid_inputs:
            _issue(
                issues,
                rules,
                policy,
                record_key=record_key,
                row_index=index,
                code="SR010_INVALID_NUMERIC_INPUT",
                observed=";".join(invalid_inputs),
                expected="finite non-negative inputs",
            )

        has_observed_quantity = any(
            column is not None and _has_value(frame.loc[index, column])
            for column in [ss_column, rop_column]
        )
        has_recalc_inputs = all(column in numeric_inputs for column in numeric_input_columns)
        if has_observed_quantity and not has_recalc_inputs and not invalid_inputs:
            _issue(
                issues,
                rules,
                policy,
                record_key=record_key,
                row_index=index,
                code="SR011_INSUFFICIENT_RECALC_INPUT",
                observed="stored SS/ROP",
                expected="mean_daily_usage,daily_demand_stddev,lead_time_days",
            )
        if has_observed_quantity and has_recalc_inputs and not invalid_inputs:
            expected_stock = calculate_level_based_safety_stock(
                mean_daily_usage=numeric_inputs["mean_daily_usage"],
                daily_demand_stddev=numeric_inputs["daily_demand_stddev"],
                lead_time_days=numeric_inputs["lead_time_days"],
                supply_risk_level=expected_level,
                policy=policy,
            )
            for column, output_key, issue_code in [
                (ss_column, "safety_stock", "SR008_SAFETY_STOCK_RECALC_MISMATCH"),
                (rop_column, "reorder_point", "SR009_ROP_RECALC_MISMATCH"),
            ]:
                if column is None:
                    continue
                observed_quantity, invalid = _numeric(frame.loc[index, column])
                if invalid or (observed_quantity is not None and observed_quantity < 0):
                    _issue(
                        issues,
                        rules,
                        policy,
                        record_key=record_key,
                        row_index=index,
                        code="SR010_INVALID_NUMERIC_INPUT",
                        observed=f"{column}={frame.loc[index, column]}",
                        expected="finite non-negative quantity",
                    )
                elif observed_quantity is not None and not _is_close(
                    observed_quantity, float(expected_stock[output_key]), rules
                ):
                    _issue(
                        issues,
                        rules,
                        policy,
                        record_key=record_key,
                        row_index=index,
                        code=issue_code,
                        observed=observed_quantity,
                        expected=float(expected_stock[output_key]),
                    )

            rate_unit = str(frame.loc[index, "demand_rate_unit"]).strip() if "demand_rate_unit" in frame.columns else ""
            std_unit = str(frame.loc[index, "demand_stddev_unit"]).strip() if "demand_stddev_unit" in frame.columns else ""
            if rate_unit != "per_day" or std_unit != "per_sqrt_day":
                _issue(
                    issues,
                    rules,
                    policy,
                    record_key=record_key,
                    row_index=index,
                    code="SR017_UNIT_CONTRACT_MISSING_OR_INVALID",
                    observed=f"rate={rate_unit or 'missing'},std={std_unit or 'missing'}",
                    expected="rate=per_day,std=per_sqrt_day",
                )

        method = str(frame.loc[index, "inventory_policy_method"]).strip().lower() if "inventory_policy_method" in frame.columns else ""
        risk_buffer, _ = _numeric(frame.loc[index, "risk_buffer"]) if "risk_buffer" in frame.columns else (None, False)
        module_applied = _as_bool(frame.loc[index, "module_c_policy_applied"]) if "module_c_policy_applied" in frame.columns else False
        if "level_based" in method and module_applied and (risk_buffer or 0) > 0:
            _issue(
                issues,
                rules,
                policy,
                record_key=record_key,
                row_index=index,
                code="SR016_DOUBLE_INVENTORY_POLICY",
                observed=f"method={method},risk_buffer={risk_buffer}",
                expected="one approved inventory policy",
            )

    if observed_level_column:
        conflict_frame = pd.DataFrame(
            {
                "canonical_codes": working["canonical_supply_risk_meta_codes"],
                "observed_level": frame[observed_level_column].map(_normalized_level),
                "record_key": keys,
            },
            index=working.index,
        )
        conflict_frame = conflict_frame[
            conflict_frame["canonical_codes"].str.strip().ne("")
            & conflict_frame["observed_level"].isin(VALID_LEVELS)
        ]
        conflicting_codes = set(
            conflict_frame.groupby("canonical_codes")["observed_level"]
            .nunique()
            .loc[lambda values: values.gt(1)]
            .index
        )
        for index, row in conflict_frame[
            conflict_frame["canonical_codes"].isin(conflicting_codes)
        ].iterrows():
            _issue(
                issues,
                rules,
                policy,
                record_key=str(row["record_key"]),
                row_index=index,
                code="SR013_CODE_HAS_MULTIPLE_BASELINE_LEVELS",
                observed=row["observed_level"],
                expected=working.loc[index, "baseline_supply_risk_level"],
                details=f"canonical_codes={row['canonical_codes']}",
            )

        normalized_levels = frame[observed_level_column].map(_normalized_level)
        valid_levels = normalized_levels[normalized_levels.isin(VALID_LEVELS)]
        if not valid_levels.empty:
            critical_share = float(valid_levels.eq("CRITICAL").mean())
            threshold = float(
                rules["dataset_thresholds"]["critical_row_share_review"]
            )
            if critical_share > threshold:
                _issue(
                    issues,
                    rules,
                    policy,
                    record_key="__DATASET__",
                    row_index="",
                    code="SR014_EXCESSIVE_CRITICAL_SHARE",
                    observed=critical_share,
                    expected=f"<= {threshold}",
                    scope="dataset",
                )

        if ss_column:
            ss_values = pd.to_numeric(frame[ss_column], errors="coerce").clip(lower=0)
            total_ss = float(ss_values.sum())
            if total_ss > 0:
                critical_ss_share = float(
                    ss_values[normalized_levels.eq("CRITICAL")].sum() / total_ss
                )
                threshold = float(
                    rules["dataset_thresholds"][
                        "critical_safety_stock_share_review"
                    ]
                )
                if critical_ss_share > threshold:
                    _issue(
                        issues,
                        rules,
                        policy,
                        record_key="__DATASET__",
                        row_index="",
                        code="SR015_CRITICAL_SS_CONCENTRATION",
                        observed=critical_ss_share,
                        expected=f"<= {threshold}",
                        scope="dataset",
                    )

    issue_frame = pd.DataFrame(issues, columns=ISSUE_COLUMNS)
    row_issues = issue_frame[issue_frame["scope"].eq("row")] if not issue_frame.empty else issue_frame
    issue_map: dict[object, list[dict[str, object]]] = {}
    for issue in row_issues.to_dict(orient="records"):
        issue_map.setdefault(issue["row_index"], []).append(issue)

    statuses = []
    issue_codes = []
    primary_codes = []
    issue_counts = []
    for index in working.index:
        current = issue_map.get(index, [])
        current_sorted = sorted(
            current,
            key=lambda issue: (-ACTION_RANK[str(issue["action"])], str(issue["issue_code"])),
        )
        status = max(
            [str(issue["action"]) for issue in current],
            key=lambda action: ACTION_RANK[action],
            default="PASS",
        )
        statuses.append(status)
        issue_codes.append(";".join(str(issue["issue_code"]) for issue in current_sorted))
        primary_codes.append(str(current_sorted[0]["issue_code"]) if current_sorted else "")
        issue_counts.append(len(current))
    working["quality_status"] = statuses
    working["quality_issue_count"] = issue_counts
    working["quality_issue_codes"] = issue_codes
    working["quality_primary_issue_code"] = primary_codes
    working["is_operationally_eligible"] = working["quality_status"].eq("PASS")
    working["quality_rules_version"] = rules["version"]

    status_counts = Counter(working["quality_status"])
    issue_code_counts = Counter(
        issue_frame.loc[issue_frame["scope"].eq("row"), "issue_code"]
        if not issue_frame.empty
        else []
    )
    dataset_issue_codes = (
        sorted(
            set(
                issue_frame.loc[
                    issue_frame["scope"].eq("dataset"), "issue_code"
                ].tolist()
            )
        )
        if not issue_frame.empty
        else []
    )
    dataset_actions = (
        issue_frame.loc[issue_frame["scope"].eq("dataset"), "action"].tolist()
        if not issue_frame.empty
        else []
    )
    dataset_status = max(
        [str(action) for action in dataset_actions],
        key=lambda action: ACTION_RANK[action],
        default="PASS",
    )
    report = {
        "rules_version": rules["version"],
        "policy_version": policy["version"],
        "operational_mode": operational_mode,
        "input_rows": int(len(frame)),
        "pass_rows": int(status_counts.get("PASS", 0)),
        "review_rows": int(status_counts.get("REVIEW", 0)),
        "blocked_rows": int(status_counts.get("BLOCK", 0)),
        "operationally_eligible_rows": int(working["is_operationally_eligible"].sum()),
        "row_issue_counts": dict(sorted(issue_code_counts.items())),
        "dataset_issue_codes": dataset_issue_codes,
        "dataset_quality_status": dataset_status,
        "batch_release_allowed": bool(
            status_counts.get("REVIEW", 0) == 0
            and status_counts.get("BLOCK", 0) == 0
            and dataset_status == "PASS"
        ),
    }
    return working, issue_frame, report


def filter_supply_risk_records(
    frame: pd.DataFrame,
    **kwargs,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, object]]:
    classified, issues, report = classify_supply_risk_anomalies(frame, **kwargs)
    passed = classified[classified["quality_status"].eq("PASS")].copy()
    review = classified[classified["quality_status"].eq("REVIEW")].copy()
    quarantine = classified[classified["quality_status"].eq("BLOCK")].copy()
    return classified, issues, passed, review, quarantine, report


def _allocate_sample_status_quotas(
    status_counts: dict[str, int],
    target: int,
) -> dict[str, int]:
    quotas = {status: 0 for status in SAMPLE_STATUS_WEIGHTS}
    available_statuses = [
        status for status in SAMPLE_STATUS_WEIGHTS if status_counts.get(status, 0) > 0
    ]
    if target >= len(available_statuses):
        for status in available_statuses:
            quotas[status] = 1

    while sum(quotas.values()) < target:
        candidates = [
            status
            for status in available_statuses
            if quotas[status] < status_counts.get(status, 0)
        ]
        if not candidates:
            break
        selected = max(
            candidates,
            key=lambda status: (
                SAMPLE_STATUS_WEIGHTS[status] / (quotas[status] + 1),
                ACTION_RANK[status],
            ),
        )
        quotas[selected] += 1
    return quotas


def _balanced_issue_sample(frame: pd.DataFrame, quota: int) -> pd.DataFrame:
    if quota <= 0 or frame.empty:
        return frame.head(0).copy()
    groups = [
        group.reset_index(drop=True)
        for _, group in frame.groupby("_sample_issue_stratum", sort=True, dropna=False)
    ]
    selected_rows: list[pd.DataFrame] = []
    offsets = [0] * len(groups)
    while len(selected_rows) < quota:
        added = False
        for group_index, group in enumerate(groups):
            offset = offsets[group_index]
            if offset >= len(group):
                continue
            selected_rows.append(group.iloc[[offset]])
            offsets[group_index] += 1
            added = True
            if len(selected_rows) == quota:
                break
        if not added:
            break
    return pd.concat(selected_rows, ignore_index=True) if selected_rows else frame.head(0)


def select_supply_risk_quality_sample(
    classified: pd.DataFrame,
    *,
    sample_size: int = 1000,
    rules: dict[str, Any] | None = None,
) -> pd.DataFrame:
    if sample_size <= 0:
        raise ValueError("sample_size must be positive")
    required = {"quality_status", "quality_primary_issue_code"}
    missing = sorted(required - set(classified.columns))
    if missing:
        raise ValueError(f"Classified quality columns are missing: {missing}")

    rules = rules or load_anomaly_rules()
    working = classified.copy()
    target = min(sample_size, len(working))
    if working.empty:
        return working.assign(
            quality_sample_rank=pd.Series(dtype="int64"),
            sample_attention_reason=pd.Series(dtype="string"),
            quality_primary_issue_description=pd.Series(dtype="string"),
            quality_recommended_action=pd.Series(dtype="string"),
        )

    primary_codes = working["quality_primary_issue_code"].fillna("").astype(str)
    descriptions = {
        code: str(rule["description"]) for code, rule in rules["rules"].items()
    }
    recommendations = {
        code: str(rule["recommended_action"])
        for code, rule in rules["rules"].items()
    }
    working["quality_primary_issue_description"] = primary_codes.map(descriptions).fillna(
        "오류 없음"
    )
    working["quality_recommended_action"] = primary_codes.map(recommendations).fillna(
        "운영 입력으로 사용 가능"
    )
    working["sample_attention_reason"] = working["quality_status"].astype(str) + ":" + primary_codes
    working.loc[primary_codes.eq(""), "sample_attention_reason"] = "PASS:NO_ISSUE"
    working["_sample_issue_stratum"] = primary_codes.mask(primary_codes.eq(""), "NO_ISSUE")
    working["_sample_usage"] = pd.to_numeric(
        working.get("usage_sum", pd.Series(0, index=working.index)), errors="coerce"
    ).fillna(0.0)
    working["_sample_occurrences"] = pd.to_numeric(
        working.get("occurrence_count", pd.Series(0, index=working.index)),
        errors="coerce",
    ).fillna(0.0)
    working["_sample_key"] = working.get(
        "quality_record_key", pd.Series(working.index, index=working.index)
    ).astype(str)
    sample_representative = working.get(
        "representative_item_id", working["_sample_key"]
    ).fillna("").astype(str)
    working["_sample_representative"] = sample_representative.mask(
        sample_representative.str.strip().eq(""), working["_sample_key"]
    )
    working["_sample_row_id"] = range(len(working))
    working["_sample_status_priority"] = working["quality_status"].map(
        {"BLOCK": 0, "REVIEW": 1, "PASS": 2}
    ).fillna(3)
    working = working.sort_values(
        ["_sample_usage", "_sample_occurrences", "_sample_key"],
        ascending=[False, False, True],
        kind="mergesort",
    )

    unique_candidates = (
        working.sort_values(
            [
                "_sample_status_priority",
                "_sample_usage",
                "_sample_occurrences",
                "_sample_key",
            ],
            ascending=[True, False, False, True],
            kind="mergesort",
        )
        .drop_duplicates("_sample_representative")
        .copy()
    )

    def sample_by_status(source: pd.DataFrame, requested: int) -> pd.DataFrame:
        counts = {
            status: int(source["quality_status"].eq(status).sum())
            for status in SAMPLE_STATUS_WEIGHTS
        }
        quotas = _allocate_sample_status_quotas(counts, requested)
        parts = []
        for status in ["BLOCK", "REVIEW", "PASS"]:
            status_frame = source[source["quality_status"].eq(status)]
            parts.append(_balanced_issue_sample(status_frame, quotas[status]))
        return pd.concat(parts, ignore_index=True)

    unique_target = min(target, len(unique_candidates))
    sample = sample_by_status(unique_candidates, unique_target)
    remaining_target = target - len(sample)
    if remaining_target > 0:
        remaining = working[~working["_sample_row_id"].isin(sample["_sample_row_id"])]
        sample = pd.concat(
            [sample, sample_by_status(remaining, remaining_target)],
            ignore_index=True,
        )
    if len(sample) != target:
        raise RuntimeError(f"Expected {target} quality samples, selected {len(sample)}")

    sample.insert(0, "quality_sample_rank", range(1, len(sample) + 1))
    metadata = [
        "quality_sample_rank",
        "sample_attention_reason",
        "quality_primary_issue_description",
        "quality_recommended_action",
    ]
    internal = [column for column in sample.columns if column.startswith("_sample_")]
    original = [
        column
        for column in classified.columns
        if column not in metadata and column not in internal
    ]
    return sample[[*metadata, *original]]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Classify and filter supply-risk inventory data quality errors"
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--key-column")
    parser.add_argument("--code-column")
    parser.add_argument("--operational-mode", action="store_true")
    parser.add_argument("--sample-size", type=int, default=1000)
    parser.add_argument(
        "--require-release",
        action="store_true",
        help="Exit with status 2 unless every row and dataset check passes",
    )
    args = parser.parse_args()

    frame = pd.read_csv(args.input, dtype=str, keep_default_na=False)
    classified, issues, passed, review, quarantine, report = filter_supply_risk_records(
        frame,
        key_column=args.key_column,
        code_column=args.code_column,
        operational_mode=args.operational_mode,
    )
    sample = select_supply_risk_quality_sample(
        classified,
        sample_size=args.sample_size,
    )
    report["sample_requested_rows"] = args.sample_size
    report["sample_rows"] = len(sample)
    report["sample_unique_representative_items"] = int(
        sample.get("representative_item_id", sample["quality_record_key"])
        .fillna("")
        .astype(str)
        .nunique()
    )
    report["sample_selection_basis"] = (
        "representative_item_diversity_then_status_and_issue_stratification"
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    classified.to_csv(args.output_dir / "supply_risk_quality_classified.csv", index=False)
    issues.to_csv(args.output_dir / "supply_risk_quality_issues.csv", index=False)
    passed.to_csv(args.output_dir / "supply_risk_quality_passed.csv", index=False)
    review.to_csv(args.output_dir / "supply_risk_quality_review.csv", index=False)
    quarantine.to_csv(args.output_dir / "supply_risk_quality_quarantine.csv", index=False)
    sample.to_csv(
        args.output_dir / f"supply_risk_quality_sample_{args.sample_size}.csv",
        index=False,
        encoding="utf-8-sig",
    )
    write_json(report, args.output_dir / "supply_risk_quality_report.json")
    if args.require_release and not report["batch_release_allowed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
