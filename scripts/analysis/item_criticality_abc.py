"""대표품목 기준 사용량 중요도 등급 — VED 판정용 검토 목록 (ai#56).

## 왜

지금은 모든 품목이 동등하다. 백신이 떨어지는 것과 종이컵이 떨어지는 것이
시스템상 같은 무게다. ABC-VED 매트릭스가 이 이슈의 제안인데, 두 축의 성격이
전혀 다르다.

    volume class   사용량 기준 상대 등급                         → 데이터로 계산된다
    VED   필수도.    Vital / Essential / Desirable        → 사람이 정해야 한다

VED 는 원장에 없다. "이 약이 없으면 진료가 멈추는가" 는 보건소 실무 판단이다.

**그래서 이 스크립트는 대표품목별 사용량 등급을 계산하고, VED 판정이 필요한
품목을 골라 검토 목록으로 낸다.** `USE`는 기관 로컬 코드이므로 대표품목이
미해결이면 기관·코드 조합을 유지한다.

## 중요도 산정 — 금액이 아니라 **사용량** 기준이다

교과서 ABC 는 사용금액을 쓴다. 그런데 원장의 `구입단가` 가 **99.1% 결측** 이다.
단가가 있는 행이 0.1% 뿐이라 금액 기준 ABC 는 산출 자체가 불가능하다.

    구입단가  결측 99.1%  /  0 이하 0.8%  /  양수 0.1%

그래서 **연간 사용량** 으로 대신한다.

    누적 사용량 비중 80% 까지 A,  80~95% B,  나머지 C

한계를 분명히 한다. 사용량 ABC 는 **비싸지만 적게 쓰는 품목을 놓친다.** 백신이
대표적이다 — 수량은 적어도 금액과 중요도가 크다. 그래서 이 등급을 그대로
쓰면 안 되고, VED 로 보완해야 한다. 그것이 ABC-VED 매트릭스의 원래 취지다.

Pareto 기반 재고 분류 근거: Silver, Pyke & Peterson (1998)
*Inventory Management and Production Planning and Scheduling*, Ch.3.

단가가 확보되면 금액 기준으로 바꿔야 한다. 조달청 납품요구에 단가가 있으나
(dlvrReqAmt/dlvrReqQty) 원장 물품코드와 조인이 1.3% 라 지금은 쓸 수 없다.

실행:
    python scripts/analysis/item_criticality_abc.py
"""
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.config import (  # noqa: E402
    CURRENT_RAW_STOCK_FILE_PATTERN,
    ITEM_ALIAS_TO_PRODUCT_PATH,
    RAW_STOCK_DIR,
)
from src.data_loader import _read_stock_chunks, discover_raw_stock_files  # noqa: E402

CHUNK_ROWS = 400_000
A_CUTOFF = 0.80
B_CUTOFF = 0.95
# 실무자에게 물어볼 범위. 전 품목을 다 물으면 답이 안 온다.
REVIEW_TOP_N = 200
OUT_PATH = ROOT / "outputs" / "item_criticality_abc.csv"
REVIEW_PATH = ROOT / "outputs" / "item_ved_review_list.csv"


def _load() -> pd.DataFrame:
    need = [
        "보건기관코드_en",
        "물품코드",
        "물품명",
        "정상출고량",
        "구입단가",
    ]
    frames = []
    for path in discover_raw_stock_files(RAW_STOCK_DIR, CURRENT_RAW_STOCK_FILE_PATTERN):
        for chunk in _read_stock_chunks(path, CHUNK_ROWS):
            if any(column not in chunk.columns for column in need):
                continue
            frame = chunk[need].copy()
            for column in ("정상출고량", "구입단가"):
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
            frames.append(frame)
    if not frames:
        raise RuntimeError("원장을 읽지 못했다.")
    return pd.concat(frames, ignore_index=True)


def _load_alias_links() -> pd.DataFrame:
    if not ITEM_ALIAS_TO_PRODUCT_PATH.exists():
        raise FileNotFoundError(
            "Representative item mapping is required before importance grading: "
            f"{ITEM_ALIAS_TO_PRODUCT_PATH}"
        )
    links = pd.read_parquet(
        ITEM_ALIAS_TO_PRODUCT_PATH,
        columns=["local_item_key", "representative_item_id"],
    ).drop_duplicates()
    if links["local_item_key"].duplicated().any():
        raise ValueError("Representative mapping has conflicting local_item_key rows")
    return links


def build_item_importance_table(
    ledger: pd.DataFrame,
    alias_links: pd.DataFrame,
) -> pd.DataFrame:
    ledger = ledger.copy()
    ledger = ledger[ledger["정상출고량"].fillna(0) > 0]
    ledger["local_item_key"] = (
        ledger["보건기관코드_en"].astype(str)
        + "::"
        + ledger["물품코드"].astype(str)
    )
    links = alias_links[
        ["local_item_key", "representative_item_id"]
    ].drop_duplicates()
    if links["local_item_key"].duplicated().any():
        raise ValueError("Representative mapping is not unique by local_item_key")
    ledger = ledger.merge(
        links,
        on="local_item_key",
        how="left",
        validate="many_to_one",
    )
    representative = (
        ledger["representative_item_id"]
        .astype("string")
        .fillna("")
        .str.strip()
    )
    ledger["analysis_item_key"] = representative.where(
        representative.ne(""),
        "UNRESOLVED_LOCAL::" + ledger["local_item_key"],
    )
    ledger["is_unresolved_local"] = representative.eq("")

    # 단가는 결측이 많다. 물품별 중앙값으로 채운다.
    median_price = ledger.groupby("analysis_item_key")["구입단가"].median()
    ledger["unit_price"] = ledger["구입단가"].fillna(
        ledger["analysis_item_key"].map(median_price)
    )
    ledger["usage_value"] = ledger["정상출고량"] * ledger["unit_price"]
    item = (
        ledger.groupby(
            ["analysis_item_key", "물품명"],
            observed=True,
        )
        .agg(
            representative_item_id=("representative_item_id", "first"),
            is_unresolved_local=("is_unresolved_local", "max"),
            usage_qty=("정상출고량", "sum"),
            observed_usage_value=("usage_value", "sum"),
            priced_usage_qty=(
                "정상출고량",
                lambda values: float(
                    values[
                        ledger.loc[values.index, "unit_price"].notna()
                    ].sum()
                ),
            ),
            unit_price=("unit_price", "median"),
            transactions=("정상출고량", "size"),
        )
        .reset_index()
    )
    item = item.sort_values("usage_qty", ascending=False).drop_duplicates(
        "analysis_item_key"
    )
    item["price_coverage"] = (
        item["priced_usage_qty"] / item["usage_qty"].replace(0, np.nan)
    ).fillna(0.0)
    item = item.sort_values("usage_qty", ascending=False).reset_index(drop=True)
    item["volume_share"] = item["usage_qty"] / item["usage_qty"].sum()
    item["cumulative_volume_share"] = item["volume_share"].cumsum()
    item["volume_class"] = np.where(
        item["cumulative_volume_share"] <= A_CUTOFF,
        "A",
        np.where(item["cumulative_volume_share"] <= B_CUTOFF, "B", "C"),
    )
    item["observed_value_class"] = ""
    observed_value = item[item["observed_usage_value"].gt(0)].copy()
    if not observed_value.empty:
        observed_value = observed_value.sort_values(
            "observed_usage_value",
            ascending=False,
        )
        share = (
            observed_value["observed_usage_value"]
            / observed_value["observed_usage_value"].sum()
        )
        cumulative = share.cumsum()
        classes = np.where(
            cumulative <= A_CUTOFF,
            "A",
            np.where(cumulative <= B_CUTOFF, "B", "C"),
        )
        item.loc[
            observed_value.index,
            "observed_value_class",
        ] = classes
    item["ved_class"] = ""
    item["volume_rank"] = range(1, len(item) + 1)
    return item


def main() -> None:
    ledger = _load()
    alias_links = _load_alias_links()
    valued = build_item_importance_table(ledger, alias_links)
    print(f"출고 행 {len(ledger):,}  분석 품목 {len(valued):,}종")

    print(f"\n{'등급':<6}{'품목':>8}{'품목 비중':>10}{'사용량 비중':>12}")
    for grade in ("A", "B", "C"):
        block = valued[valued["volume_class"].eq(grade)]
        print(f"  {grade:<4}{len(block):>8,}{len(block)/len(valued):>10.1%}"
              f"{block['volume_share'].sum():>12.1%}")
    priced_items = int(valued["unit_price"].notna().sum())
    print(f"\n  참고: 단가를 구할 수 있는 품목 {priced_items:,}종 "
          f"({priced_items/len(valued):.1%}) — 관측 금액 등급은 이 범위에서만 유효하다")

    valued.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    clinical_keyword = valued["물품명"].astype(str).str.contains(
        r"백신|vaccine|응급|인슐린|해독|에피네프린|아드레날린",
        case=False,
        regex=True,
    )
    review = pd.concat(
        [
            valued.head(REVIEW_TOP_N).assign(review_reason="top_volume"),
            valued[clinical_keyword].assign(
                review_reason="clinical_keyword_candidate"
            ),
        ],
        ignore_index=True,
    ).drop_duplicates("analysis_item_key")
    review = review[
        [
            "volume_rank",
            "analysis_item_key",
            "representative_item_id",
            "물품명",
            "usage_qty",
            "unit_price",
            "price_coverage",
            "volume_share",
            "cumulative_volume_share",
            "volume_class",
            "observed_value_class",
            "ved_class",
            "review_reason",
        ]
    ].copy()
    review.to_csv(REVIEW_PATH, index=False, encoding="utf-8-sig")

    print(f"\n상위 {REVIEW_TOP_N}종이 전체 사용량의 "
          f"{valued.head(REVIEW_TOP_N)['volume_share'].sum():.1%} 를 차지한다")
    print(f"\n{'순위':<5}{'물품명':<32}{'사용량':>14}{'누적':>8}")
    for row in review.head(15).to_dict("records"):
        print(f"  {row['volume_rank']:<3}{str(row['물품명'])[:30]:<32}"
              f"{row['usage_qty']:>14,.0f}{row['cumulative_volume_share']:>8.1%}")

    print(f"\n저장: {OUT_PATH}  ({len(valued):,}종)")
    print(f"      {REVIEW_PATH}  (VED 판정 대상 {len(review)}종)")
    print("\nved_class 컬럼이 비어 있다. 보건소 실무자가 V/E/D 를 채워야 한다.")
    print("  V(Vital)     없으면 즉시 위험")
    print("  E(Essential) 없으면 진료 차질")
    print("  D(Desirable) 없어도 됨")


if __name__ == "__main__":
    main()
