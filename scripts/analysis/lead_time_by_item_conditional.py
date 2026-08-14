"""품목별 조건부 리드타임 표를 만든다 (ai#20).

## 왜

`lead_time_prediction_model.py` 가 리드타임이 조건부로 예측된다는 것을 보였다.

    이항 분류(L>30) 검증 AUC      0.7275
    분위수 회귀 pinball 개선      q50 +31.2%  q75 +48.1%  q90 +59.8%
    품목 단독 AUC                 0.7501   ← 전체 모형보다 높다

그런데 현행 정책은 **전 품목에 같은 분포** 를 적용하고 위험 점수로만 조정한다.
이 표는 그 정보를 쓸 수 있게 품목별 분위수를 낸다.

`review_period_by_item.csv`(검토주기 R)와 같은 형태다. 보호기간 `R + L` 의 두
항을 같은 방식으로 다룬다.

## 수축

품목별 표본 수가 크게 다르다. 표본이 적은 품목의 분위수를 그대로 쓰면 우연한
값이 정책이 된다. Bühlmann-Straub 신뢰도로 전체 분위수 쪽으로 당긴다.

    Z = n / (n + k),   k = 품목내 분산 / 품목간 분산
    L_품목 = Z · L_품목(실측) + (1 − Z) · L_전체

근거: Bühlmann & Straub (1970); Klugman/Panjer/Willmot, *Loss Models*, Ch.20.

## 적용하지 않는다

`review_status=pending` 으로 낸다. 보호기간이 바뀌면 목표재고가 바뀌고 그건
예산 문제다. R 결정(#54)과 함께 판단해야 한다.

실행:
    python scripts/analysis/lead_time_by_item_conditional.py
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

RAW_GLOB = "procurement_delivery_requests*.jsonl"
OUT_PATH = ROOT / "outputs" / "lead_time_by_item_conditional.csv"
REPORT_PATH = ROOT / "outputs" / "lead_time_by_item_conditional_report.json"
LEAD_MIN, LEAD_MAX = 0, 365
MIN_SAMPLES = 5
QUANTILES = (0.50, 0.75, 0.90)
# 정책이 실제로 쓰는 분위수. 여기 기준으로 수축 강도를 정한다.
POLICY_QUANTILE = 0.75


def _load() -> pd.DataFrame:
    rows = []
    for path in (ROOT / "data" / "processed").glob(RAW_GLOB):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    frame = pd.DataFrame(rows).drop_duplicates("dlvrReqNo")
    frame["rcpt"] = pd.to_datetime(frame["dlvrReqRcptDate"], errors="coerce")
    frame["due"] = pd.to_datetime(frame["maxDlvrTmlmtDate"], errors="coerce")
    frame["lead_days"] = (frame["due"] - frame["rcpt"]).dt.days
    frame = frame.dropna(subset=["rcpt", "lead_days"])
    return frame[frame["lead_days"].between(LEAD_MIN, LEAD_MAX)].copy()


def main() -> None:
    frame = _load()
    key = ["rprsntDtilPrdctClsfcNo", "rprsntDtilPrdctClsfcNoNm"]
    print(f"표본 {len(frame):,}건  세부품명 {frame[key[0]].nunique():,}종")

    grand = {q: float(frame["lead_days"].quantile(q)) for q in QUANTILES}
    print("전체 분위수: " + "  ".join(f"q{int(q*100)} {v:.0f}일" for q, v in grand.items()))

    grouped = frame.groupby(key, observed=True)["lead_days"]
    stats = grouped.agg(sample_size="size", sd="std").reset_index()
    for q in QUANTILES:
        stats[f"raw_q{int(q*100)}"] = grouped.quantile(q).to_numpy()
    stats = stats[stats["sample_size"] >= MIN_SAMPLES].copy()

    # 신뢰도 계수. 정책이 쓰는 q75 의 품목간 분산을 기준으로 잡는다.
    within = float(np.nanmean(stats["sd"] ** 2))
    between = float(np.var(stats[f"raw_q{int(POLICY_QUANTILE*100)}"], ddof=1))
    k = within / max(between, 1e-9)
    stats["credibility"] = stats["sample_size"] / (stats["sample_size"] + k)
    print(f"품목내 분산 {within:,.0f}  품목간 분산 {between:,.0f}  →  k = {k:.1f}")

    for q in QUANTILES:
        label = int(q * 100)
        stats[f"lead_time_q{label}"] = (
            stats["credibility"] * stats[f"raw_q{label}"]
            + (1 - stats["credibility"]) * grand[q]
        ).round(1)

    result = stats.rename(
        columns={
            "rprsntDtilPrdctClsfcNo": "detail_product_class_no",
            "rprsntDtilPrdctClsfcNoNm": "detail_product_class_name",
        }
    ).drop(columns=["sd"])
    result["review_status"] = "pending"
    result["mapping_version"] = "lead-time-by-item-v1-pending-review"
    result.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    policy = result[f"lead_time_q{int(POLICY_QUANTILE*100)}"]
    print(f"\n수축 후 q75:  p10 {policy.quantile(.1):.0f}  중앙 {policy.median():.0f}"
          f"  p90 {policy.quantile(.9):.0f}   범위 {policy.min():.0f}~{policy.max():.0f}")
    print(f"신뢰도 Z:  p10 {result['credibility'].quantile(.1):.2f}"
          f"  중앙 {result['credibility'].median():.2f}"
          f"  p90 {result['credibility'].quantile(.9):.2f}")

    print(f"\n전체 q75({grand[0.75]:.0f}일) 대비 다른 품목:")
    differs = result[policy.sub(grand[0.75]).abs() > 5].sort_values(
        f"lead_time_q{int(POLICY_QUANTILE*100)}", ascending=False
    )
    print(f"  ±5일 초과 {len(differs)}종 / {len(result)}종 ({len(differs)/len(result):.1%})")
    for row in differs.head(10).itertuples():
        value = getattr(row, f"lead_time_q{int(POLICY_QUANTILE*100)}")
        print(f"    {str(row.detail_product_class_name)[:26]:<28}{value:>6.0f}일"
              f"  n={row.sample_size:,}")

    REPORT_PATH.write_text(
        json.dumps(
            {
                "samples": int(len(frame)),
                "items": int(len(result)),
                "grand_quantiles": grand,
                "credibility_k": k,
                "policy_quantile": POLICY_QUANTILE,
                "differs_by_more_than_5_days": int(len(differs)),
                "review_status": "pending",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n저장: {OUT_PATH}  ({len(result):,}종)")
    print("review_status=pending 이다. 보호기간 변경은 #54 의 R 결정과 함께 판단한다.")


if __name__ == "__main__":
    main()
