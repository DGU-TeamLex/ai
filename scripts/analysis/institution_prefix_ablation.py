"""기관코드 계층 파생변수 ablation — 예측 증분이 실제로 있는가 (ai#57).

## 무엇을 검증하나

가명 기관코드 `보건기관코드_en` 안에 계층 구조가 남아 있다는 것이 원장 전수로
확인됐다(고유 3,530개 / 앞1자리 16그룹 / 앞3자리 261그룹).

그런데 **구조가 있다는 것과 예측에 쓸모가 있다는 것은 다른 문제다.**
ai#57 리뷰가 요구한 것이 정확히 이 검증이다.

    "full institution categorical / prefix only / 둘 다 없음의 ablation 으로
     실제 증분 WAPE·BIAS 확인"

이 결과가 먼저 나와야 취급 정책 논의가 의미를 갖는다. 증분이 없으면 안 쓰면
그만이고, 있으면 그때 비용을 따진다.

## 취급 제한 준수 (ai#57)

- **지역명을 쓰지 않는다.** 컬럼명은 `institution_prefix_l1` / `_l3` 중립명이다.
  권역↔시도 대응표는 이 스크립트에서 참조하지 않으며 산출물에도 남지 않는다.
- **매핑을 출력하지 않는다.** 그룹 ID 는 학습 내부에서만 쓰이고 결과 파일에는
  집계 지표만 남는다.
- **희소 그룹은 RARE 병합한다.** 그룹 크기 편차가 크다(최소 18 ~ 최대 570 기관).
- **인코더는 train 구간에서만 fit 한다.** validation 에만 있는 범주는 RARE 로
  떨어뜨려 누수를 막는다.

## 조건

    A. baseline        기관 정보 없음 (현행)
    B. prefix only     institution_prefix_l1 + _l3
    C. full            institution_code 전체 (3,530 범주)

같은 4-fold 롤링 원점, 같은 파라미터로 돌려 WAPE·BIAS 증분만 본다.

실행:
    python scripts/analysis/institution_prefix_ablation.py
"""
import json
import pathlib
import sys
import time

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.config import MODEL_VARIANTS  # noqa: E402
from src.modeling.training import (  # noqa: E402
    _load_feature_table,
    load_historical_training_policy,
    production_lgbm_params,
    select_feature_columns,
)
from src.modeling.tune_hyperparameters import (  # noqa: E402
    _evaluate_params,
    _fold_frames,
    _prepare_matrices,
)

VARIANT = "stock_model_a_usage_only"
# 그룹이 이보다 작으면 RARE 로 병합한다. 작은 그룹은 22~73개 기관뿐이라
# 범주 그대로 두면 과적합한다.
MIN_GROUP_ROWS = 5000
OUT_PATH = ROOT / "outputs" / "institution_prefix_ablation.json"


def _fit_categories(train_values: pd.Series, minimum_rows: int) -> list[str]:
    """train 구간에서만 범주를 정한다. 여기서 빠진 값은 RARE 가 된다."""
    counts = train_values.value_counts()
    return sorted(counts[counts >= minimum_rows].index.tolist())


def _encode(values: pd.Series, categories: list[str]) -> pd.Series:
    return pd.Categorical(
        values.where(values.isin(categories), "RARE"),
        categories=[*categories, "RARE"],
    ).codes.astype("int32")


def add_institution_features(folds: list[dict], mode: str) -> tuple[list[dict], list[str]]:
    """fold 별로 train 에서 범주를 fit 하고 train/valid 에 적용한다.

    범주 결정은 반드시 그 fold 의 **train 행만** 본다. valid 에만 있는 값은
    RARE 로 떨어져 누수가 생기지 않는다(ai#57 리뷰 4번).
    """
    if mode == "baseline":
        return folds, []

    if mode == "prefix":
        column_slices = {"institution_prefix_l1": 1, "institution_prefix_l3": 3}
    elif mode == "full":
        column_slices = {"institution_code_categorical": None}
    else:
        raise ValueError(f"Unknown mode: {mode}")

    updated = []
    for fold in folds:
        train, valid = fold["train"].copy(), fold["valid"].copy()
        train_code = train["institution_code"].astype(str)
        valid_code = valid["institution_code"].astype(str)
        for column, width in column_slices.items():
            train_values = train_code if width is None else train_code.str[:width]
            valid_values = valid_code if width is None else valid_code.str[:width]
            categories = _fit_categories(train_values, MIN_GROUP_ROWS)
            train[column] = _encode(train_values, categories)
            valid[column] = _encode(valid_values, categories)
        updated.append({**fold, "train": train, "valid": valid})
    return updated, list(column_slices)


def main() -> None:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    options = MODEL_VARIANTS[VARIANT]
    feature_table = _load_feature_table(options)
    feature_table = feature_table.loc[feature_table["rolling_mean_3"].notna()]
    historical_weight = float(
        load_historical_training_policy().get("selected_historical_weight", 0.0)
    )
    base_folds = _fold_frames(feature_table, historical_weight)
    base_features = select_feature_columns(
        feature_table,
        use_news=options["use_news"],
        use_commodity=options["use_commodity"],
        use_module_c=options.get("use_module_c", False),
    )
    first_train = base_folds[0]["train"]
    base_features = [c for c in base_features if not first_train[c].isna().all()]
    params = production_lgbm_params(options["objective"])
    print(f"기준 피처 {len(base_features)}개 / fold {len(base_folds)}개\n")

    results = {}
    for mode in ("baseline", "prefix", "full"):
        started = time.time()
        folds, added = add_institution_features(base_folds, mode)
        metrics = _evaluate_params(
            params, _prepare_matrices(folds, base_features + added, historical_weight)
        )
        combined = metrics["combined"]
        elapsed = (time.time() - started) / 60
        results[mode] = {
            "added_features": added,
            "WAPE": combined["WAPE"],
            "BIAS": combined.get("BIAS"),
            "per_fold": [
                {"fold": f["fold"], "WAPE": f["WAPE"], "BIAS": f["BIAS"]}
                for f in metrics["per_fold"]
            ],
            "minutes": round(elapsed, 1),
        }
        print(f"[{mode:<9}] WAPE={combined['WAPE']:.4f}  ({elapsed:.1f}분)")

    base = results["baseline"]["WAPE"]
    print("\n=== 증분 (baseline 대비) ===")
    for mode in ("prefix", "full"):
        delta = results[mode]["WAPE"] - base
        results[mode]["delta_wape_pp"] = round(delta, 4)
        verdict = "개선" if delta < 0 else "악화"
        print(f"  {mode:<9} {delta:+.4f}%p  ({verdict})")

    OUT_PATH.write_text(
        json.dumps(
            {
                "variant": VARIANT,
                "min_group_rows_for_category": MIN_GROUP_ROWS,
                "encoder_fit_scope": "train_fold_only",
                "region_names_used": False,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n저장: {OUT_PATH}")


if __name__ == "__main__":
    main()
