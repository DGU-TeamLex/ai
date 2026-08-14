"""품목별 최적 리드타임 실측 (ai#39).

`compute_lead_time_quantile_policy.py` 는 DB 연결을 요구하고, 문서에
"원장은 2018~19 만 로컬에 있다 / 2024~25 원장을 확보하면 재산출할 것" 이라고
적혀 있다. 지금 2024~25 원장을 확보했으므로 그 조건이 충족됐다.

여기서는 DB 없이 원장만으로 두 기간을 각각 산출하고 비교한다.

표본 정의는 원 스크립트와 동일하게 맞춘다.
    조건 : 입고량 > 0  AND  이전최종재고량 == 0   (품절 상태에서 입고)
    lag  : 재고마감일 - 같은 (물품 x 기관) 직전 거래일
    범위 : 0 < lag <= 365

주의: 이 값은 [발주지연 + 순수 리드타임] 의 합이므로 상한이다.
      순수 공급 리드타임은 P10~P25 쪽에 가깝다(원 스크립트 주석).
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd

# 레포 루트를 sys.path 에 넣는다. 임시 폴더에서 옮겨온 스크립트라
# 절대경로가 박혀 있었다(다른 사람 PC 에서는 실행되지 않는다).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from src.config import (  # noqa: E402
    CURRENT_RAW_STOCK_FILE_PATTERN,
    DEFAULT_LEAD_TIME_DAYS,
    HISTORICAL_RAW_STOCK_FILE_PATTERN,
    RAW_STOCK_DIR,
)
from src.data_loader import _read_stock_chunks, discover_raw_stock_files  # noqa: E402

ROOT = str(pathlib.Path(__file__).resolve().parents[2])
QUANTILES = [0.10, 0.25, 0.50, 0.75, 0.90]


def collect_samples(pattern: str, label: str) -> pd.DataFrame:
    files = discover_raw_stock_files(RAW_STOCK_DIR, pattern)
    if not files:
        raise SystemExit(f"{label}: 원장 파일 없음 ({pattern})")
    print(f"[{label}] {len(files)}개 파일 스캔", flush=True)

    frames = []
    for path in files:
        for chunk in _read_stock_chunks(path, 300_000):
            need = ["물품코드", "보건기관코드_en", "재고마감일", "입고량", "이전최종재고량"]
            if any(c not in chunk.columns for c in need):
                continue
            d = chunk[need].copy()
            d["재고마감일"] = pd.to_datetime(d["재고마감일"], errors="coerce")
            for c in ("입고량", "이전최종재고량"):
                d[c] = pd.to_numeric(d[c], errors="coerce")
            frames.append(d.dropna(subset=["재고마감일"]))
    raw = pd.concat(frames, ignore_index=True)
    print(f"[{label}] 원장 {len(raw):,}행", flush=True)

    raw = raw.sort_values(["물품코드", "보건기관코드_en", "재고마감일"])
    key = raw["물품코드"].astype(str) + "|" + raw["보건기관코드_en"].astype(str)
    raw["k"] = pd.factorize(key)[0]
    raw["prev_date"] = raw.groupby("k", sort=False)["재고마감일"].shift()
    so = raw[(raw["입고량"] > 0) & (raw["이전최종재고량"] == 0)].copy()
    so["lag"] = (so["재고마감일"] - so["prev_date"]).dt.days
    so = so[(so["lag"] > 0) & (so["lag"] <= 365)]
    print(f"[{label}] 리드타임 표본 {len(so):,}건 / 품목 {so['물품코드'].nunique():,}종", flush=True)
    return so


def summarise(so: pd.DataFrame, label: str) -> dict:
    q = so["lag"].quantile(QUANTILES)
    by_item = so.groupby("물품코드")["lag"]
    counts = by_item.size()
    out = {
        "label": label,
        "samples": int(len(so)),
        "items": int(so["물품코드"].nunique()),
        "overall": {f"p{int(k*100)}": round(float(v), 1) for k, v in q.items()},
        "mean": round(float(so["lag"].mean()), 1),
        "max": int(so["lag"].max()),
        "items_with_5plus_samples": int((counts >= 5).sum()),
    }
    print(f"\n=== [{label}] 전체 분포 ===")
    for k, v in out["overall"].items():
        print(f"  {k}: {v:>6.1f}일")
    print(f"  평균 {out['mean']}일 | 최대 {out['max']}일 | 표본 {out['samples']:,}건 | 품목 {out['items']:,}종")
    print(f"  표본 5건 이상 품목: {out['items_with_5plus_samples']:,}종")

    # 품목별 대표값
    item_stats = pd.DataFrame({
        "median": by_item.median(),
        "p25": by_item.quantile(0.25),
        "p10": by_item.quantile(0.10),
        "n": counts,
    })
    reliable = item_stats[item_stats["n"] >= 5]
    print(f"\n  [표본 5건 이상 {len(reliable):,}종의 품목별 대표값 분포]")
    print(f"  {'통계':<8}{'median':>10}{'p25':>10}{'p10':>10}")
    for label_q, qq in (("하위25%", 0.25), ("중앙", 0.50), ("상위25%", 0.75), ("상위10%", 0.90)):
        print(f"  {label_q:<8}{reliable['median'].quantile(qq):>10.1f}{reliable['p25'].quantile(qq):>10.1f}{reliable['p10'].quantile(qq):>10.1f}")
    out["item_level"] = {
        "reliable_items": int(len(reliable)),
        "median_of_medians": round(float(reliable["median"].median()), 1),
        "median_of_p25": round(float(reliable["p25"].median()), 1),
        "median_of_p10": round(float(reliable["p10"].median()), 1),
    }
    return out, item_stats


def main():
    print(f"현재 운영 fallback: {DEFAULT_LEAD_TIME_DAYS}일 (전 품목 동일)\n")
    results = {}
    tables = {}
    for pattern, label in (
        (CURRENT_RAW_STOCK_FILE_PATTERN, "2024-25"),
        (HISTORICAL_RAW_STOCK_FILE_PATTERN, "2018-19"),
    ):
        try:
            so = collect_samples(pattern, label)
        except SystemExit as exc:
            print(exc)
            continue
        summary, table = summarise(so, label)
        results[label] = summary
        tables[label] = table

    if len(tables) == 2:
        cur, his = tables["2024-25"], tables["2018-19"]
        common = cur.index.intersection(his.index)
        both = pd.DataFrame({
            "median_2425": cur.loc[common, "median"],
            "median_1819": his.loc[common, "median"],
            "n_2425": cur.loc[common, "n"],
            "n_1819": his.loc[common, "n"],
        })
        both = both[(both["n_2425"] >= 5) & (both["n_1819"] >= 5)]
        print(f"\n=== 두 기간 공통 품목 {len(both):,}종 (각 5건 이상) ===")
        if len(both):
            corr = both["median_2425"].corr(both["median_1819"])
            print(f"  기간 간 품목별 median 상관: {corr:.3f}")
            print(f"  2018-19 median 중앙값: {both['median_1819'].median():.1f}일")
            print(f"  2024-25 median 중앙값: {both['median_2425'].median():.1f}일")
            results["cross_period"] = {
                "common_items": int(len(both)),
                "correlation": round(float(corr), 3),
                "median_1819": round(float(both["median_1819"].median()), 1),
                "median_2425": round(float(both["median_2425"].median()), 1),
            }

    print(f"\n=== 현재 15일 fallback 과 비교 ===")
    for label, r in results.items():
        if label == "cross_period":
            continue
        o = r["overall"]
        print(f"  [{label}] p10={o['p10']} p25={o['p25']} median={o['p50']} p75={o['p75']} p90={o['p90']}")
        print(f"           → 현재 15일은 전체 분포의 어디쯤인가: "
              f"{'p10 미만' if 15 < o['p10'] else ('p10~p25' if 15 < o['p25'] else ('p25~median' if 15 < o['p50'] else 'median 이상'))}")

    out = rf"{ROOT}\outputs\lead_time_optimal.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    for label, table in tables.items():
        table.to_csv(rf"{ROOT}\outputs\lead_time_by_item_{label}.csv", encoding="utf-8-sig")
    print("\nsaved:", out)


if __name__ == "__main__":
    main()
