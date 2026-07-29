import argparse
import gc
import json
import logging

import pandas as pd
import pyarrow.parquet as pq

from ..config import (
    FEATURE_TABLE_PATH,
    HISTORICAL_TRAIN_END,
    HISTORICAL_TRAINING_POLICY_PATH,
    HISTORICAL_TRAINING_TUNING_REPORT_PATH,
    PROJECT_ROOT,
    TARGET_COLUMN,
    TRAIN_END,
    VALID_END,
    VALID_START,
)
from ..feature_engineering import run_feature_engineering
from ..utils import ensure_dirs, setup_logging
from .standardized_history import MAPPING_VERSION
from .training import (
    COMMODITY_COLUMNS,
    CURRENT_MONTH_COLUMNS,
    IDENTIFIER_COLUMNS,
    MODULE_C_COLUMNS,
    NEWS_COLUMNS,
    select_feature_columns,
    select_training_window,
    train_model_variant,
    training_sample_weights,
)


LOGGER = logging.getLogger(__name__)
TUNING_VERSION = "historical-training-weight-v1.0"
WEIGHT_CANDIDATES = [0.0, 0.10, 0.25, 0.50, 0.75, 1.0]


def _load_tuning_feature_table() -> pd.DataFrame:
    if not FEATURE_TABLE_PATH.exists():
        run_feature_engineering()
    schema_columns = pq.ParquetFile(
        FEATURE_TABLE_PATH
    ).schema_arrow.names
    excluded = (
        CURRENT_MONTH_COLUMNS
        | IDENTIFIER_COLUMNS
        | set(NEWS_COLUMNS)
        | set(COMMODITY_COLUMNS)
        | set(MODULE_C_COLUMNS)
    )
    columns = [
        column
        for column in schema_columns
        if column not in excluded
    ]
    if "historical_training_eligible" in schema_columns:
        columns.append("historical_training_eligible")
    return pd.read_parquet(
        FEATURE_TABLE_PATH,
        columns=columns,
        filters=[
            ("year_month", "<=", pd.Timestamp(VALID_END)),
        ],
    )


def tune_historical_training_weight(
    apply: bool = False,
) -> dict[str, object]:
    setup_logging()
    ensure_dirs(
        HISTORICAL_TRAINING_POLICY_PATH.parent,
        HISTORICAL_TRAINING_TUNING_REPORT_PATH.parent,
    )
    feature_table = _load_tuning_feature_table()
    feature_table = feature_table[
        feature_table[TARGET_COLUMN].notna()
        & feature_table[TARGET_COLUMN].ge(0)
        & feature_table["lag_1"].notna()
        & feature_table["lag_1"].ge(0)
        & feature_table["rolling_mean_3"].notna()
    ]
    validation = feature_table[
        feature_table["year_month"].between(
            pd.Timestamp(VALID_START),
            pd.Timestamp(VALID_END),
        )
    ]
    if validation.empty:
        raise ValueError("Historical-weight validation rows are empty")
    feature_columns = select_feature_columns(
        feature_table,
        use_news=False,
        use_commodity=False,
        use_module_c=False,
    )

    candidates = []
    for weight in WEIGHT_CANDIDATES:
        train = select_training_window(
            feature_table,
            TRAIN_END,
            weight,
        )
        historical_rows = int(
            train["year_month"].le(
                pd.Timestamp(HISTORICAL_TRAIN_END)
            ).sum()
        )
        bundle = train_model_variant(
            "historical_weight_candidate",
            train,
            feature_columns,
            objective="regression_l1",
            valid=validation,
            sample_weight=training_sample_weights(train, weight),
        )
        candidates.append(
            {
                "historical_weight": weight,
                "train_rows": int(len(train)),
                "historical_rows": historical_rows,
                "validation_rows": int(len(validation)),
                "algorithm": bundle["algorithm"],
                **bundle["validation_metrics"],
            }
        )
        LOGGER.info(
            "Historical weight %.2f validation WAPE=%s",
            weight,
            bundle["validation_metrics"].get("WAPE"),
        )
        del train, bundle
        gc.collect()

    selected = min(
        candidates,
        key=lambda row: (row["WAPE"], row["historical_weight"]),
    )
    current_only = next(
        row for row in candidates if row["historical_weight"] == 0.0
    )
    report = {
        "version": TUNING_VERSION,
        "status": (
            "validation_selected_applied"
            if apply
            else "validation_selected_not_applied"
        ),
        "standard_item_mapping_version": MAPPING_VERSION,
        "selection_metric": "minimum validation WAPE",
        "selection_did_not_use_test": True,
        "train_current_end": TRAIN_END,
        "validation_start": VALID_START,
        "validation_end": VALID_END,
        "candidate_weights": WEIGHT_CANDIDATES,
        "selected_historical_weight": selected["historical_weight"],
        "selected_validation_metrics": selected,
        "current_only_validation_metrics": current_only,
        "validation_wape_change": (
            selected["WAPE"] - current_only["WAPE"]
        ),
        "candidates": candidates,
        "guardrails": [
            "Only historical rows matched to a current standard item are eligible.",
            "Historical and current local series retain separate contiguous lag segments.",
            "The latest test period is not used to select the historical weight.",
            "A zero selected weight is allowed when historical data does not improve validation.",
        ],
    }
    HISTORICAL_TRAINING_TUNING_REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if apply:
        policy = {
            "version": TUNING_VERSION,
            "status": "validation_selected_applied",
            "standard_item_mapping_version": MAPPING_VERSION,
            "selected_historical_weight": selected[
                "historical_weight"
            ],
            "selection_metric": "minimum validation WAPE",
            "selection_did_not_use_test": True,
            "validation_start": VALID_START,
            "validation_end": VALID_END,
            "validation_metrics": selected,
            "current_only_validation_metrics": current_only,
            "report_path": str(
                HISTORICAL_TRAINING_TUNING_REPORT_PATH.relative_to(
                    PROJECT_ROOT
                )
            ),
        }
        HISTORICAL_TRAINING_POLICY_PATH.write_text(
            json.dumps(policy, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Select the standardized historical-data training weight",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the validation-selected weight to the training policy",
    )
    args = parser.parse_args()
    print(tune_historical_training_weight(apply=args.apply))


if __name__ == "__main__":
    main()
