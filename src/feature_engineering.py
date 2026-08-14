import argparse
import logging
import os

import pandas as pd

from .config import (
    COMMODITY_RISK_SCORE_PATH,
    FEATURE_TABLE_PATH,
    GROUP_KEYS,
    HISTORICAL_MONTHLY_STOCK_PATH,
    HISTORICAL_TRAIN_END,
    MODULE_C_RISK_SCORE_PATH,
    MONTHLY_STOCK_PATH,
    NEWS_RISK_SCORE_PATH,
    OUTPUT_DIR,
    PROCESSED_DATA_DIR,
    TEST_START,
    TRAIN_END,
    VALID_END,
)
from .data_loader import load_stock_data
from .features import create_features
from .modeling.data_quality import write_forecast_data_quality_report
from .modeling.standardized_history import attach_standard_item_features
from .utils import ensure_dirs, write_json


LOGGER = logging.getLogger(__name__)
RISK_JOIN_KEYS = ["year_month", "stock_item_key"]
MONTHLY_FEATURE_COLUMNS = [
    "year_month",
    "institution_code",
    "department",
    "item_code",
    "item_name",
    "stock_item_key",
    "consumption_qty",
    "inbound_qty",
    "month_end_stock",
    "stockout_rate",
    "disposal_qty",
    "auto_disposal_adjustment_qty",
]


def _load_monthly_stock() -> pd.DataFrame:
    if MONTHLY_STOCK_PATH.exists():
        current = pd.read_parquet(
            MONTHLY_STOCK_PATH,
            columns=MONTHLY_FEATURE_COLUMNS,
        )
    else:
        monthly_stock = load_stock_data()
        ensure_dirs(PROCESSED_DATA_DIR)
        monthly_stock.to_parquet(
            MONTHLY_STOCK_PATH,
            index=False,
            compression="zstd",
        )
        current = monthly_stock[MONTHLY_FEATURE_COLUMNS]
    current["data_period"] = "current"
    frames = [current]
    if HISTORICAL_MONTHLY_STOCK_PATH.exists():
        historical = pd.read_parquet(
            HISTORICAL_MONTHLY_STOCK_PATH,
            columns=MONTHLY_FEATURE_COLUMNS,
        )
        historical["data_period"] = "historical"
        frames.insert(0, historical)
    combined = pd.concat(frames, ignore_index=True)
    return attach_standard_item_features(combined).drop(
        columns="local_item_key",
    )


def _normalize_yyyymm(df: pd.DataFrame, column: str = "STD_YYYYMM") -> pd.DataFrame:
    result = df.copy()
    result["year_month"] = pd.to_datetime(result[column].astype(str), errors="coerce").dt.to_period("M").dt.to_timestamp()
    return result.drop(columns=[column])


def _merge_risk(
    feature_table: pd.DataFrame,
    path,
    value_columns: list[str],
) -> pd.DataFrame:
    if not path.exists():
        # 위험점수 산출물이 없으면 해당 외부신호는 전 행 0 이 된다.
        # 조용히 넘어가면 모듈 C 를 쓰는 모델이 "신호가 없는 상태"로 학습되고도
        # 정상처럼 보이므로, 어떤 신호가 왜 비었는지 반드시 남긴다.
        LOGGER.warning(
            "Risk score output not found: %s — %s will be filled with 0.0 for every row",
            path,
            ", ".join(value_columns),
        )
        feature_table[value_columns] = 0.0
        return feature_table

    header = pd.read_csv(path, nrows=0).columns
    available = [column for column in value_columns if column in header]
    read_columns = [
        column
        for column in ["STD_YYYYMM", "year_month", "stock_item_key", *available]
        if column in header
    ]
    risk = _normalize_yyyymm(
        pd.read_csv(path, low_memory=False, usecols=read_columns)
    )
    if risk.empty:
        LOGGER.warning(
            "Risk score output is empty: %s — %s will be filled with 0.0 for every row",
            path,
            ", ".join(value_columns),
        )
        feature_table[value_columns] = 0.0
        return feature_table
    if not set(RISK_JOIN_KEYS).issubset(risk.columns):
        LOGGER.warning("Ignoring incompatible risk output without raw_stock keys: %s", path)
        feature_table[value_columns] = 0.0
        return feature_table
    risk["stock_item_key"] = risk["stock_item_key"].astype(str)
    merged = feature_table.merge(risk[[*RISK_JOIN_KEYS, *available]], on=RISK_JOIN_KEYS, how="left")
    for column in value_columns:
        if column not in merged.columns:
            merged[column] = 0.0
    return merged


def build_feature_table() -> pd.DataFrame:
    feature_table = create_features(_load_monthly_stock())
    feature_table = feature_table.rename(
        columns={
            "target_next_month": "target_usage",
            "use_lag_1": "lag_1",
            "use_lag_2": "lag_2",
            "use_lag_3": "lag_3",
            "use_lag_6": "lag_6",
            "use_lag_12": "lag_12",
            "use_rolling_mean_3": "rolling_mean_3",
            "use_rolling_mean_6": "rolling_mean_6",
            "use_rolling_mean_12": "rolling_mean_12",
            "use_rolling_std_3": "rolling_std_3",
            "use_rolling_std_6": "rolling_std_6",
            "use_rolling_std_12": "rolling_std_12",
            "use_rolling_median_3": "rolling_median_3",
            "use_expanding_mean": "expanding_mean",
            "use_zero_rate_6": "zero_rate_6",
            "use_zero_rate_12": "zero_rate_12",
        }
    )
    feature_table["is_winter"] = feature_table["month"].isin([12, 1, 2]).astype(int)
    feature_table["is_summer"] = feature_table["month"].isin([6, 7, 8]).astype(int)
    feature_table["same_month_last_year"] = feature_table["lag_12"]
    lag_1 = feature_table["lag_1"].astype("float64")
    lag_12 = feature_table["lag_12"].astype("float64")
    feature_table["yoy_growth_rate"] = (
        ((lag_1 - lag_12) / lag_12.where(lag_12.ne(0))).fillna(0.0).astype("float32")
    )

    news_columns = ["disease_news_risk", "supply_news_risk", "material_news_risk", "total_news_risk"]
    commodity_columns = ["commodity_risk", "material_return_30d", "material_volatility_30d"]
    module_c_columns = [
        "module_c_demand_risk",
        "module_c_supply_news_risk",
        "module_c_material_news_risk",
        "module_c_market_price_risk",
        "module_c_trade_risk",
        "module_c_supply_risk",
        "module_c_total_risk",
        "module_c_signal_confidence",
    ]
    feature_table = _merge_risk(feature_table, NEWS_RISK_SCORE_PATH, news_columns)
    feature_table = _merge_risk(feature_table, COMMODITY_RISK_SCORE_PATH, commodity_columns)
    feature_table = _merge_risk(feature_table, MODULE_C_RISK_SCORE_PATH, module_c_columns)
    external_columns = [*news_columns, *commodity_columns, *module_c_columns]
    feature_table[external_columns] = feature_table[external_columns].fillna(0.0).astype("float32")
    _log_external_signal_coverage(feature_table, external_columns)
    return feature_table.sort_values(GROUP_KEYS).reset_index(drop=True)


def _log_external_signal_coverage(
    feature_table: pd.DataFrame,
    external_columns: list[str],
) -> None:
    """외부신호가 실제로 몇 행에 붙었는지 남긴다.

    산출물 파일이 있어도 승인된 원자재 매핑에 걸린 재고키에만 값이 붙으므로,
    커버리지가 1% 미만인 상태가 정상처럼 지나갈 수 있다. 학습 전에 규모를
    눈으로 확인할 수 있도록 요약한다.
    """
    total = len(feature_table)
    if not total:
        return
    empty = []
    for column in external_columns:
        nonzero = int((feature_table[column] != 0).sum())
        if nonzero == 0:
            empty.append(column)
            continue
        LOGGER.info(
            "External signal %s: %s/%s rows non-zero (%.4f%%), max=%.6f",
            column, f"{nonzero:,}", f"{total:,}", nonzero / total * 100,
            float(feature_table[column].max()),
        )
    if empty:
        LOGGER.warning(
            "External signals with no non-zero value in any row (%s): %s",
            len(empty), ", ".join(empty),
        )


class ExternalSignalContractError(RuntimeError):
    """운영 계약을 만족하지 못한 외부신호가 있다."""


def external_signal_coverage_by_split(
    feature_table: pd.DataFrame,
    external_columns: list[str],
) -> dict:
    """신호별 비영 커버리지를 **split 별로** 낸다.

    전체 행 비율만 보면 오해가 생긴다. 2018~19 구간은 외부신호가 원래 없어
    분모에 섞이면 커버리지가 실제보다 낮게 보이고, 반대로 검증 구간에만 신호가
    있어도 전체 비율은 그럴듯해 보인다. train/validation/test 를 나눠서 본다.

    학습에 실제로 쓰이는 것은 train 구간이므로 계약 판정은 train 을 본다(ai#71).
    """
    if feature_table.empty or "year_month" not in feature_table.columns:
        return {}
    month = pd.to_datetime(feature_table["year_month"]).dt.strftime("%Y-%m")
    splits = {
        "historical": month <= HISTORICAL_TRAIN_END,
        "train": (month > HISTORICAL_TRAIN_END) & (month <= TRAIN_END),
        "validation": (month > TRAIN_END) & (month <= VALID_END),
        "test": month >= TEST_START,
    }
    coverage: dict[str, dict] = {}
    for name, mask in splits.items():
        rows = int(mask.sum())
        coverage[name] = {"rows": rows, "signals": {}}
        if not rows:
            continue
        block = feature_table.loc[mask, external_columns]
        for column in external_columns:
            nonzero = int((block[column] != 0).sum())
            coverage[name]["signals"][column] = {
                "nonzero_rows": nonzero,
                "nonzero_ratio": round(nonzero / rows, 6),
                "max": float(block[column].max()),
            }
    return coverage


def enforce_external_signal_contract(
    coverage: dict,
    *,
    required_signals: list[str],
    minimum_train_coverage: float,
) -> None:
    """운영 profile 에서 신호가 준비되지 않았으면 실패시킨다.

    기본/연구 모드는 경고 + Model A fallback 을 그대로 허용한다. Model A 는
    외부신호 없이도 유효하므로 전부 실패시킬 이유가 없다. 다만 운영 Module C
    실행은 "신호가 있다" 를 전제하므로, 거기서는 0 채움이 조용히 통과하면
    안 된다(ai#71 Blocking 2).

    켜는 방법: `REQUIRE_EXTERNAL_SIGNALS=1` 또는 `--require-external-signals`.
    """
    train = coverage.get("train", {})
    rows = train.get("rows", 0)
    if not rows:
        raise ExternalSignalContractError(
            "train 구간에 행이 없다. 외부신호 계약을 판정할 수 없다."
        )
    problems = []
    for column in required_signals:
        stat = train.get("signals", {}).get(column)
        if stat is None:
            problems.append(f"  {column}: 피처테이블에 없다")
            continue
        if stat["nonzero_ratio"] < minimum_train_coverage:
            problems.append(
                f"  {column}: train 비영 {stat['nonzero_ratio']:.4%} "
                f"< 최소 {minimum_train_coverage:.4%} "
                f"({stat['nonzero_rows']:,}/{rows:,}행)"
            )
    if problems:
        raise ExternalSignalContractError(
            "운영 외부신호 계약 미충족:\n" + "\n".join(problems)
            + "\n provider 설정과 위험점수 산출물을 확인하라. "
            "연구 목적이면 REQUIRE_EXTERNAL_SIGNALS 를 끄고 Model A 로 돌려라."
        )


# 운영 계약이 요구하는 신호. Model A 는 이것 없이도 유효하므로 기본은 끈다.
REQUIRED_EXTERNAL_SIGNALS = [
    "module_c_supply_risk",
    "module_c_demand_risk",
]
DEFAULT_MINIMUM_TRAIN_COVERAGE = 0.01


def run_feature_engineering(require_external_signals: bool | None = None) -> None:
    ensure_dirs(OUTPUT_DIR, PROCESSED_DATA_DIR)
    if require_external_signals is None:
        require_external_signals = os.getenv(
            "REQUIRE_EXTERNAL_SIGNALS", ""
        ).strip().lower() in {"1", "true", "yes"}
    feature_table = build_feature_table()

    external_columns = [
        column
        for column in feature_table.columns
        if column.startswith(("module_c_", "material_"))
        or column.endswith("_news_risk")
        or column == "commodity_risk"
    ]
    coverage = external_signal_coverage_by_split(feature_table, external_columns)
    write_json(
        {
            "require_external_signals": require_external_signals,
            "minimum_train_coverage": DEFAULT_MINIMUM_TRAIN_COVERAGE,
            "required_signals": REQUIRED_EXTERNAL_SIGNALS,
            "coverage_by_split": coverage,
        },
        OUTPUT_DIR / "external_signal_coverage_report.json",
    )
    for split, block in coverage.items():
        for column in REQUIRED_EXTERNAL_SIGNALS:
            stat = block.get("signals", {}).get(column)
            if stat is None:
                continue
            LOGGER.info(
                "coverage %s/%s: %s/%s rows non-zero (%.4f%%)",
                split, column, f"{stat['nonzero_rows']:,}",
                f"{block['rows']:,}", stat["nonzero_ratio"] * 100,
            )
    if require_external_signals:
        enforce_external_signal_contract(
            coverage,
            required_signals=REQUIRED_EXTERNAL_SIGNALS,
            minimum_train_coverage=DEFAULT_MINIMUM_TRAIN_COVERAGE,
        )

    write_forecast_data_quality_report(feature_table, feature_table)
    feature_table = feature_table.dropna(subset=["lag_1", "rolling_mean_3"])
    feature_table.to_parquet(FEATURE_TABLE_PATH, index=False, compression="zstd")
    feature_table.to_parquet(
        PROCESSED_DATA_DIR / "stock_model_dataset.parquet",
        index=False,
        compression="zstd",
    )
    LOGGER.info("Saved raw_stock feature table: %s (%s rows)", FEATURE_TABLE_PATH, len(feature_table))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="피처테이블 생성")
    parser.add_argument(
        "--require-external-signals",
        action="store_true",
        help=(
            "운영 profile. 외부신호가 train 구간에서 최소 커버리지를 못 넘으면 "
            "0 으로 채우고 성공하는 대신 non-zero 로 종료한다."
        ),
    )
    arguments = parser.parse_args()
    try:
        run_feature_engineering(
            require_external_signals=arguments.require_external_signals or None
        )
    except ExternalSignalContractError as error:
        LOGGER.error("%s", error)
        raise SystemExit(2) from error
