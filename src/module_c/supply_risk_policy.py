from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import SUPPLY_RISK_LEVEL_POLICY_PATH


DERIVED_COLUMNS = [
    "baseline_supply_risk_level",
    "baseline_supply_risk_z",
    "baseline_lead_time_multiplier",
    "supply_risk_level_source",
    "canonical_supply_risk_meta_codes",
    "ignored_event_or_demand_codes",
    "unmapped_supply_risk_codes",
    "supply_risk_policy_needs_review",
    "supply_risk_policy_version",
    "supply_risk_policy_status",
]


def _split_codes(value: object) -> list[str]:
    if value is None or pd.isna(value):
        return []
    return sorted({code.strip() for code in str(value).split(";") if code.strip()})


def _as_bool(value: object) -> bool:
    return str(value).strip().lower() in {"true", "t", "1", "yes", "y"}


def validate_supply_risk_policy(policy: dict[str, Any]) -> dict[str, Any]:
    levels = policy.get("levels", {})
    required_levels = {"NORMAL", "CAUTION", "WARNING", "CRITICAL"}
    if set(levels) != required_levels:
        raise ValueError("Supply risk policy must define NORMAL/CAUTION/WARNING/CRITICAL")

    ordered = sorted(levels.items(), key=lambda item: int(item[1]["rank"]))
    if [name for name, _ in ordered] != ["NORMAL", "CAUTION", "WARNING", "CRITICAL"]:
        raise ValueError("Supply risk level ranks must be strictly ordered")
    z_values = [float(values["z_value"]) for _, values in ordered]
    multipliers = [float(values["lead_time_multiplier"]) for _, values in ordered]
    if z_values != sorted(z_values) or multipliers != sorted(multipliers):
        raise ValueError("z values and lead-time multipliers must be monotonic")

    rules = policy.get("code_rules", [])
    codes = [str(rule["supply_risk_meta_code"]).strip() for rule in rules]
    if len(codes) != len(set(codes)):
        raise ValueError("Supply risk policy contains duplicate code rules")
    for rule in rules:
        if rule.get("risk_axis") not in {"supply", "demand", "event", "none"}:
            raise ValueError("Supply risk code has an invalid risk_axis")
        if rule.get("baseline_level") not in levels:
            raise ValueError("Supply risk code references an unknown baseline level")

    critical = policy.get("critical_override", {})
    if critical.get("result_level") != "CRITICAL":
        raise ValueError("Critical override must resolve to CRITICAL")
    if not critical.get("required_true_fields"):
        raise ValueError("Critical override requires explicit evidence fields")
    if not str(policy.get("version", "")).strip():
        raise ValueError("Supply risk policy version is required")

    floors = policy.get("demand_parameter_floors", {})
    for field in ["mean_daily_usage", "daily_demand_stddev"]:
        value = float(floors.get(field, -1))
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"Supply risk policy has an invalid {field} floor")

    lead_time = policy.get("lead_time_estimation", {})
    minimum_lead_time = float(lead_time.get("minimum_days", 0))
    if not math.isfinite(minimum_lead_time) or minimum_lead_time <= 0:
        raise ValueError("Supply risk policy minimum lead time must be positive")

    inventory_status = policy.get("inventory_status", {})
    watch_multiplier = float(
        inventory_status.get("watch_reorder_point_multiplier", 0)
    )
    target_cover_days = float(inventory_status.get("target_cover_days", -1))
    if not math.isfinite(watch_multiplier) or watch_multiplier < 1:
        raise ValueError("Inventory WATCH multiplier must be at least 1")
    if not math.isfinite(target_cover_days) or target_cover_days < 0:
        raise ValueError("Inventory target cover days must be non-negative")
    return policy


def load_supply_risk_policy(
    path: Path = SUPPLY_RISK_LEVEL_POLICY_PATH,
) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return validate_supply_risk_policy(json.load(file))


def _critical_override_approved(
    context: dict[str, object],
    policy: dict[str, Any],
) -> bool:
    critical = policy["critical_override"]
    required_flags = all(
        _as_bool(context.get(field, False))
        for field in critical["required_true_fields"]
    )
    review_status = str(
        context.get(critical["required_review_status_field"], "")
    ).strip().lower()
    return required_flags and review_status == str(
        critical["required_review_status"]
    ).strip().lower()


def derive_supply_risk_level(
    supply_risk_meta_codes: object,
    context: dict[str, object] | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, object]:
    policy = policy or load_supply_risk_policy()
    context = context or {}
    levels = policy["levels"]
    rules = {
        rule["supply_risk_meta_code"]: rule for rule in policy["code_rules"]
    }
    aliases = policy.get("legacy_code_aliases", {})
    event_codes = set(policy.get("dynamic_event_codes", []))

    canonical_codes: set[str] = set()
    ignored_codes: set[str] = set()
    unmapped_codes: set[str] = set()
    candidate_levels = [policy["unknown_code_policy"]["baseline_level"]]
    has_supply_rule = False

    for raw_code in _split_codes(supply_risk_meta_codes):
        code = aliases.get(raw_code, raw_code)
        if raw_code in event_codes or code in event_codes:
            ignored_codes.add(raw_code)
            continue
        rule = rules.get(code)
        if rule is None:
            unmapped_codes.add(raw_code)
            continue
        canonical_codes.add(code)
        if rule["risk_axis"] == "supply":
            has_supply_rule = True
            candidate_levels.append(rule["baseline_level"])
        else:
            ignored_codes.add(raw_code)

    baseline_level = max(
        candidate_levels,
        key=lambda level: int(levels[level]["rank"]),
    )
    source = "meta_code"
    if not _split_codes(supply_risk_meta_codes):
        source = "default_missing_code"
    elif unmapped_codes and len(unmapped_codes) == len(_split_codes(supply_risk_meta_codes)):
        source = "default_unmapped_code"
    elif ignored_codes and not has_supply_rule:
        source = "non_supply_axis_only"

    if _critical_override_approved(context, policy):
        baseline_level = policy["critical_override"]["result_level"]
        source = "approved_critical_override"

    values = levels[baseline_level]
    return {
        "baseline_supply_risk_level": baseline_level,
        "baseline_supply_risk_z": float(values["z_value"]),
        "baseline_lead_time_multiplier": float(values["lead_time_multiplier"]),
        "supply_risk_level_source": source,
        "canonical_supply_risk_meta_codes": ";".join(sorted(canonical_codes)),
        "ignored_event_or_demand_codes": ";".join(sorted(ignored_codes)),
        "unmapped_supply_risk_codes": ";".join(sorted(unmapped_codes)),
        "supply_risk_policy_needs_review": bool(unmapped_codes)
        or not bool(_split_codes(supply_risk_meta_codes)),
        "supply_risk_policy_version": policy["version"],
        "supply_risk_policy_status": policy["policy_status"],
    }


def _normalized_existing_level(value: object) -> str:
    normalized = str(value).strip().upper()
    return {
        "LOW": "NORMAL",
        "MEDIUM": "WARNING",
        "HIGH": "WARNING",
    }.get(normalized, normalized)


def derive_supply_risk_frame(
    frame: pd.DataFrame,
    code_column: str = "supply_risk_meta_code",
    existing_level_column: str | None = "supply_risk_level",
    policy: dict[str, Any] | None = None,
) -> pd.DataFrame:
    if code_column not in frame.columns:
        raise ValueError(f"Supply risk code column not found: {code_column}")
    policy = policy or load_supply_risk_policy()
    records = []
    for _, row in frame.iterrows():
        records.append(
            derive_supply_risk_level(
                row.get(code_column),
                context=row.to_dict(),
                policy=policy,
            )
        )
    derived = pd.DataFrame(records, index=frame.index, columns=DERIVED_COLUMNS)
    result = pd.concat([frame.copy(), derived], axis=1)
    if existing_level_column and existing_level_column in result.columns:
        existing = result[existing_level_column].map(_normalized_existing_level)
        result["source_supply_risk_level"] = result[existing_level_column]
        result["supply_risk_level_mismatch"] = (
            existing.ne("") & existing.ne(result["baseline_supply_risk_level"])
        )
    else:
        result["source_supply_risk_level"] = ""
        result["supply_risk_level_mismatch"] = False
    return result


def calculate_level_based_safety_stock(
    mean_daily_usage: float,
    daily_demand_stddev: float,
    lead_time_days: float,
    supply_risk_level: str,
    policy: dict[str, Any] | None = None,
    on_hand: float | None = None,
) -> dict[str, object]:
    policy = policy or load_supply_risk_policy()
    level = str(supply_risk_level).strip().upper()
    if level not in policy["levels"]:
        raise ValueError(f"Unknown supply risk level: {supply_risk_level}")
    values = [mean_daily_usage, daily_demand_stddev, lead_time_days]
    if any(not math.isfinite(float(value)) or float(value) < 0 for value in values):
        raise ValueError("Demand and lead-time inputs must be finite and non-negative")
    if on_hand is not None and not math.isfinite(float(on_hand)):
        raise ValueError("on_hand must be finite when provided")

    level_policy = policy["levels"][level]
    floors = policy["demand_parameter_floors"]
    effective_mean_daily_usage = max(
        float(mean_daily_usage),
        float(floors["mean_daily_usage"]),
    )
    effective_daily_demand_stddev = max(
        float(daily_demand_stddev),
        float(floors["daily_demand_stddev"]),
    )
    minimum_lead_time = float(policy["lead_time_estimation"]["minimum_days"])
    base_lead_time = max(float(lead_time_days), minimum_lead_time)
    effective_lead_time = base_lead_time * float(
        level_policy["lead_time_multiplier"]
    )
    safety_stock = float(level_policy["z_value"]) * float(
        effective_daily_demand_stddev
    ) * math.sqrt(effective_lead_time)
    lead_time_demand = effective_mean_daily_usage * effective_lead_time
    reorder_point = lead_time_demand + safety_stock
    target_cover_days = float(policy["inventory_status"]["target_cover_days"])
    target_stock = (
        effective_mean_daily_usage * (effective_lead_time + target_cover_days)
        + safety_stock
    )

    inventory_status = None
    order_recommendation = None
    normalized_on_hand = None
    if on_hand is not None:
        normalized_on_hand = max(float(on_hand), 0.0)
        watch_multiplier = float(
            policy["inventory_status"]["watch_reorder_point_multiplier"]
        )
        if normalized_on_hand <= 0:
            inventory_status = "CRITICAL"
        elif normalized_on_hand < reorder_point:
            inventory_status = "BELOW_ROP"
        elif normalized_on_hand < reorder_point * watch_multiplier:
            inventory_status = "WATCH"
        else:
            inventory_status = "OK"
        order_recommendation = max(target_stock - normalized_on_hand, 0.0)

    return {
        "supply_risk_level": level,
        "z_value": float(level_policy["z_value"]),
        "lead_time_multiplier": float(level_policy["lead_time_multiplier"]),
        "raw_mean_daily_usage": float(mean_daily_usage),
        "effective_mean_daily_usage": effective_mean_daily_usage,
        "raw_daily_demand_stddev": float(daily_demand_stddev),
        "effective_daily_demand_stddev": effective_daily_demand_stddev,
        "raw_lead_time_days": float(lead_time_days),
        "base_lead_time_days": base_lead_time,
        "lead_time_floor_applied": base_lead_time > float(lead_time_days),
        "lead_time_estimation_method": policy["lead_time_estimation"]["method"],
        "lead_time_estimator_status": policy["lead_time_estimation"][
            "estimator_status"
        ],
        "effective_lead_time_days": effective_lead_time,
        "lead_time_demand": lead_time_demand,
        "safety_stock": safety_stock,
        "reorder_point": reorder_point,
        "target_stock": target_stock,
        "on_hand": normalized_on_hand,
        "order_recommendation": order_recommendation,
        "inventory_status": inventory_status,
        "watch_reorder_point_multiplier": float(
            policy["inventory_status"]["watch_reorder_point_multiplier"]
        ),
        "demand_rate_unit": "per_day",
        "demand_stddev_unit": "per_sqrt_day",
        "policy_version": policy["version"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deterministically rederive baseline supply risk levels"
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--code-column", default="supply_risk_meta_code")
    parser.add_argument("--existing-level-column", default="supply_risk_level")
    args = parser.parse_args()

    frame = pd.read_csv(args.input, dtype=str, keep_default_na=False)
    result = derive_supply_risk_frame(
        frame,
        code_column=args.code_column,
        existing_level_column=args.existing_level_column,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output, index=False)


if __name__ == "__main__":
    main()
