import json
from pathlib import Path
from typing import Any

from ..config import MODULE_C_RISK_WEIGHT_PATH


DEFAULT_MODULE_C_CONFIG: dict[str, Any] = {
    "version": "module-c-default-v1.2",
    "calibration_status": "policy_seed_requires_backtest",
    "coefficient_basis": "initial_policy_bounds_not_empirical_causal_estimates",
    "market_signal": {
        "return_30d": 0.45,
        "volatility_30d": 0.30,
        "price_vs_90d_mean": 0.25,
        "return_risk_threshold": 0.20,
        "volatility_risk_threshold": 0.12,
        "price_level_risk_threshold": 0.15,
    },
    "supply_signal": {
        "supply_news": 0.45,
        "material_news": 0.20,
        "market_price": 0.35,
    },
    "trade_signal": {
        "import_volume_decline": 0.20,
        "net_import_availability_decline": 0.10,
        "import_interruption": 0.10,
        "import_unit_value_increase": 0.15,
        "import_volume_volatility": 0.10,
        "import_unit_value_volatility": 0.10,
        "country_concentration": 0.10,
        "supplier_count_decline": 0.05,
        "net_import_exposure": 0.05,
        "export_volume_surge": 0.05,
        "import_volume_decline_threshold": 0.30,
        "net_import_availability_decline_threshold": 0.30,
        "import_interruption_streak_months": 2,
        "import_unit_value_increase_threshold": 0.40,
        "import_volume_volatility_threshold": 0.50,
        "import_unit_value_volatility_threshold": 0.20,
        "country_concentration_threshold": 0.50,
        "supplier_count_decline_threshold": 0.30,
        "export_volume_surge_threshold": 0.50,
        "country_coverage_min": 0.80,
        "rolling_window_months": 6,
        "rolling_min_periods": 3,
        "module_c_overlay_weight": 0.25,
    },
    "inventory_adjustment": {
        "demand_usage_uplift_max": 0.35,
        "supply_lead_time_multiplier_max": 0.50,
        "supply_extra_lead_time_days_max": 14.0,
        "safety_stock_rate_uplift_max": 0.25,
        "total_risk_buffer_rate_cap": 0.75,
    },
    "alert_thresholds": {
        "watch": 0.30,
        "warning": 0.55,
        "critical": 0.75,
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _validate_weight_section(config: dict[str, Any], section: str, keys: list[str]) -> None:
    values = [float(config[section][key]) for key in keys]
    if any(value < 0 for value in values):
        raise ValueError(f"{section} weights must be non-negative")
    if abs(sum(values) - 1.0) > 1e-9:
        raise ValueError(f"{section} weights must sum to 1.0")


def validate_module_c_config(config: dict[str, Any]) -> dict[str, Any]:
    if not str(config.get("calibration_status", "")).strip():
        raise ValueError("calibration_status is required")
    _validate_weight_section(
        config,
        "market_signal",
        ["return_30d", "volatility_30d", "price_vs_90d_mean"],
    )
    _validate_weight_section(
        config,
        "supply_signal",
        ["supply_news", "material_news", "market_price"],
    )
    _validate_weight_section(
        config,
        "trade_signal",
        [
            "import_volume_decline",
            "net_import_availability_decline",
            "import_interruption",
            "import_unit_value_increase",
            "import_volume_volatility",
            "import_unit_value_volatility",
            "country_concentration",
            "supplier_count_decline",
            "net_import_exposure",
            "export_volume_surge",
        ],
    )

    for key in [
        "return_risk_threshold",
        "volatility_risk_threshold",
        "price_level_risk_threshold",
    ]:
        if float(config["market_signal"][key]) <= 0:
            raise ValueError(f"market_signal.{key} must be greater than zero")

    for key in [
        "import_volume_decline_threshold",
        "net_import_availability_decline_threshold",
        "import_unit_value_increase_threshold",
        "import_volume_volatility_threshold",
        "import_unit_value_volatility_threshold",
        "country_concentration_threshold",
        "supplier_count_decline_threshold",
        "export_volume_surge_threshold",
        "country_coverage_min",
        "module_c_overlay_weight",
    ]:
        value = float(config["trade_signal"][key])
        if value <= 0 or value > 1:
            raise ValueError(f"trade_signal.{key} must be within (0, 1]")
    rolling_window = int(config["trade_signal"]["rolling_window_months"])
    rolling_min = int(config["trade_signal"]["rolling_min_periods"])
    interruption_streak = int(
        config["trade_signal"]["import_interruption_streak_months"]
    )
    if rolling_window < 2:
        raise ValueError("trade_signal.rolling_window_months must be at least 2")
    if rolling_min < 2 or rolling_min > rolling_window:
        raise ValueError(
            "trade_signal.rolling_min_periods must be within 2..rolling_window_months"
        )
    if interruption_streak < 1:
        raise ValueError(
            "trade_signal.import_interruption_streak_months must be at least 1"
        )

    inventory = config["inventory_adjustment"]
    for key, value in inventory.items():
        if float(value) < 0:
            raise ValueError(f"inventory_adjustment.{key} must be non-negative")
    if float(inventory["total_risk_buffer_rate_cap"]) > 1:
        raise ValueError("inventory risk buffer cap must be at most 1.0")

    thresholds = config["alert_thresholds"]
    ordered = [float(thresholds[key]) for key in ["watch", "warning", "critical"]]
    if ordered != sorted(ordered) or ordered[0] < 0 or ordered[-1] > 1:
        raise ValueError("alert thresholds must be ordered within 0..1")
    return config


def load_module_c_config(path: Path = MODULE_C_RISK_WEIGHT_PATH) -> dict[str, Any]:
    override: dict[str, Any] = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as file:
            override = json.load(file)
    return validate_module_c_config(_deep_merge(DEFAULT_MODULE_C_CONFIG, override))
