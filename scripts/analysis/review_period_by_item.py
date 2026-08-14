"""품목별 검토주기 R 을 원장 입고 간격에서 추정한다 (ai#54).

## 왜 필요한가

정기검토 목표재고는 `μ × (R + L) + SS` 이고 보호기간이 `R + L` 이다.
현행은 `R = 30` 고정인데, 실측하면 품목별 중앙 R 이 1~434일로 흩어진다.
21~40일 구간에 드는 품목은 7.6% 뿐이고, 주력 의약품은 전부 3개월을 넘는다.

R 을 상수로 두면 백신류는 과다발주, 분기 조달 품목은 재고 소진이다. 방향이
반대라 평균으로 상쇄되지도 않는다.

## 왜 조달청이 아니라 원장인가

조달청 납품요구는 발주 시점 그 자체라 원래 더 좋은 원천이다. 그러나 품목
세계가 겹치지 않는다 — 원장 상위는 의약품인데 의약품은 나라장터가 아니라
도매상 직구매라 조달청 데이터에 없다. 품목명 매칭 커버리지가 행 기준 1.3% 다.

## 입고 간격을 발주 간격의 대용으로 쓰는 근거

    입고_t − 입고_{t−1} = 발주간격 + (L_t − L_{t−1})

리드타임의 상수 성분은 차분에서 소거된다. 남는 것은 리드타임 변동분이고
이것은 체계적 편향이 아니라 잡음이라 중앙값이 대부분 흡수한다.

## 수축

분할표본 검정에서 Spearman rho = 0.530 (p ≈ 0) 이다. 신호는 확실하나 품목
단위 실측값을 그대로 쓰기엔 잡음이 크다. Bühlmann-Straub 신뢰도로 전체
중앙값 쪽으로 당긴다.

    Z_i = n_i / (n_i + k),   k = 품목내 분산 / 품목간 분산
    R_i = Z_i · R_i(실측) + (1 − Z_i) · R(전체 중앙)

근거: Bühlmann & Straub (1970); Klugman, Panjer & Willmot,
*Loss Models: From Data to Decisions*, Ch.20.

실행:
    python scripts/analysis/review_period_by_item.py
"""
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.config import CURRENT_RAW_STOCK_FILE_PATTERN, RAW_STOCK_DIR  # noqa: E402
from src.data_loader import _read_stock_chunks, discover_raw_stock_files  # noqa: E402

CHUNK_ROWS = 400_000
# 하루 미만은 같은 발주의 분할 입고로 본다. 548일(18개월)을 넘으면 단종·재도입
# 이라 정기 발주 주기로 읽을 수 없다.
GAP_MIN_DAYS = 1
GAP_MAX_DAYS = 548
MIN_SAMPLES = 5
REQUIRED = ["물품코드", "물품명", "보건기관코드_en", "재고마감일", "입고량"]
OUT_PATH = ROOT / "data" / "mapping" / "review_period_by_item.csv"


def _load_inbound_events() -> pd.DataFrame:
    frames = []
    for path in discover_raw_stock_files(RAW_STOCK_DIR, CURRENT_RAW_STOCK_FILE_PATTERN):
        for chunk in _read_stock_chunks(path, CHUNK_ROWS):
            if any(column not in chunk.columns for column in REQUIRED):
                continue
            frame = chunk[REQUIRED].copy()
            frame["재고마감일"] = pd.to_datetime(frame["재고마감일"], errors="coerce")
            frame["입고량"] = pd.to_numeric(frame["입고량"], errors="coerce")
            frames.append(frame[(frame["입고량"] > 0) & frame["재고마감일"].notna()])
    if not frames:
        raise RuntimeError("입고 이벤트를 찾지 못했다. 원장 경로를 확인하라.")
    return pd.concat(frames, ignore_index=True)


def _gaps(events: pd.DataFrame) -> pd.DataFrame:
    events = events.sort_values(["물품코드", "보건기관코드_en", "재고마감일"])
    key = events["물품코드"].astype(str) + "|" + events["보건기관코드_en"].astype(str)
    events["_series"] = pd.factorize(key)[0]
    events["gap"] = events.groupby("_series", sort=False)["재고마감일"].diff().dt.days
    gaps = events.dropna(subset=["gap"])
    return gaps[gaps["gap"].between(GAP_MIN_DAYS, GAP_MAX_DAYS)]


def _split_half_stability(gaps: pd.DataFrame) -> tuple[float, float]:
    from scipy.stats import spearmanr

    midpoint = gaps["재고마감일"].quantile(0.5)
    early = gaps[gaps["재고마감일"] < midpoint].groupby("물품코드")["gap"].agg(["median", "size"])
    late = gaps[gaps["재고마감일"] >= midpoint].groupby("물품코드")["gap"].agg(["median", "size"])
    early = early[early["size"] >= 10]
    late = late[late["size"] >= 10]
    joined = early.join(late, lsuffix="_early", rsuffix="_late", how="inner")
    rho, p_value = spearmanr(joined["median_early"], joined["median_late"])
    return float(rho), float(p_value)


def main() -> None:
    gaps = _gaps(_load_inbound_events())
    print(f"간격 표본 {len(gaps):,}건")

    rho, p_value = _split_half_stability(gaps)
    print(f"분할표본 안정성  Spearman rho = {rho:.3f}  p = {p_value:.3g}")

    stats = (
        gaps.groupby(["물품코드", "물품명"])["gap"]
        .agg(sample_size="size", raw_median_days="median", _sd="std")
        .reset_index()
    )
    stats = stats[stats["sample_size"] >= MIN_SAMPLES].copy()

    grand_median = float(gaps["gap"].median())
    within = float(np.nanmean(stats["_sd"] ** 2))
    between = float(np.var(stats["raw_median_days"], ddof=1))
    k = within / max(between, 1e-9)
    print(f"품목내 분산 {within:,.0f}  품목간 분산 {between:,.0f}  →  k = {k:.1f}")

    stats["credibility"] = stats["sample_size"] / (stats["sample_size"] + k)
    stats["review_period_days"] = (
        stats["credibility"] * stats["raw_median_days"]
        + (1 - stats["credibility"]) * grand_median
    ).round(1)

    result = stats[
        ["물품코드", "물품명", "sample_size", "raw_median_days", "credibility", "review_period_days"]
    ]
    result.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    final = result["review_period_days"]
    print(f"전체 중앙 R = {grand_median:.0f}일")
    print(
        f"수축 후 R  p10 {final.quantile(.1):.0f}  중앙 {final.median():.0f}  "
        f"p90 {final.quantile(.9):.0f}   범위 {final.min():.0f}~{final.max():.0f}"
    )
    print(f"→ {OUT_PATH}  {len(result):,}행")

    weighted = float((final * result["sample_size"]).sum() / result["sample_size"].sum())
    print(
        f"\n보호기간(R+L): 현행 45일(R=30,L=15) → 실측 {weighted + 30:.0f}일(R={weighted:.0f},L=30)"
        f"  ({(weighted + 30) / 45:.2f}배)"
    )
    print("이 표는 기본값 제안이다. 매핑에 없는 품목은 기존 폴백을 쓴다.")


if __name__ == "__main__":
    main()
