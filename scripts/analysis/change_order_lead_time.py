"""납품요구 변경차수가 납기와 관계있는가 (ai#39).

## 왜 묻나

`L_계약` 은 24개월 내내 median 30일이고 월별 표준편차가 0.00 이다. 계약 납기를
관행적으로 30일로 찍기 때문이라, 어떤 외부 지표로도 설명되지 않았다.

수집기가 API 응답 30개 필드 중 17개만 담고 있던 것을 고치면서 `dlvrReqChgOrd`
(납품요구 변경차수)가 들어왔다. 계약이 **사후 변경된 건** 을 식별할 수 있게
된 것이다. 변경이 지연의 흔적이라면 그 건들은 납기가 길어야 한다.

## 앞선 결과 (표본 21,838건, 변경 있음 122건)

    변경 없음(00)  21,716건  p50 30일  평균 39.4
    변경 있음(01+)    122건  p50 51일  평균 74.9
    Mann-Whitney p = 3.86e-10

방향은 맞았으나 **변경 있음이 122건뿐** 이라 표본이 작았다.

## 해석 주의 — 이 검정이 말하지 못하는 것

* **인과 방향을 모른다.** 오래 걸릴 건이라 변경된 것인지, 변경 때문에 늘어난
  것인지 이 데이터로는 구분되지 않는다.
* 실납품일이 아니다. 여전히 계약상 납기다. 나라장터 API 에 실납품일 필드가
  없다는 것은 응답 30개 필드를 전수 확인해 확정했다.

그럼에도 의미가 있다. **"계약 납기는 전혀 안 움직인다" 는 종전 판단은 조건을
넣으면 성립하지 않는다.**

실행:
    python scripts/analysis/change_order_lead_time.py
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

RAW_GLOB = "procurement_delivery_requests*.jsonl"
OUT_PATH = ROOT / "outputs" / "change_order_lead_time.json"
LEAD_MIN, LEAD_MAX = 0, 365


def _load() -> pd.DataFrame:
    rows = []
    for path in (ROOT / "data" / "processed").glob(RAW_GLOB):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # 변경차수가 없는 건은 옛 17필드 판본이라 이 검정에 쓸 수 없다.
                if "dlvrReqChgOrd" in record:
                    rows.append(record)
    if not rows:
        raise SystemExit("변경차수를 가진 레코드가 없다. 재수집이 필요하다.")
    return pd.DataFrame(rows).drop_duplicates("dlvrReqNo")


def main() -> None:
    from scipy.stats import mannwhitneyu

    frame = _load()
    frame["rcpt"] = pd.to_datetime(frame["dlvrReqRcptDate"], errors="coerce")
    frame["due"] = pd.to_datetime(frame["maxDlvrTmlmtDate"], errors="coerce")
    frame["lead_days"] = (frame["due"] - frame["rcpt"]).dt.days
    frame["change_order"] = pd.to_numeric(frame["dlvrReqChgOrd"], errors="coerce").fillna(0)
    frame = frame.dropna(subset=["lead_days", "rcpt"])
    frame = frame[frame["lead_days"].between(LEAD_MIN, LEAD_MAX)]

    unchanged = frame[frame["change_order"].eq(0)]["lead_days"]
    changed = frame[frame["change_order"].gt(0)]["lead_days"]
    print(f"표본 {len(frame):,}건  변경 있음 {len(changed):,}건 ({len(changed)/len(frame):.2%})")
    print(f"기간 {frame['rcpt'].min():%Y-%m} ~ {frame['rcpt'].max():%Y-%m}\n")

    print(f"{'구분':<16}{'건수':>9}{'p25':>6}{'p50':>6}{'p75':>6}{'p90':>7}{'평균':>9}")
    for label, block in (("변경 없음(00)", unchanged), ("변경 있음(01+)", changed)):
        if not len(block):
            continue
        print(f"  {label:<14}{len(block):>9,}{block.quantile(.25):>6.0f}"
              f"{block.median():>6.0f}{block.quantile(.75):>6.0f}"
              f"{block.quantile(.9):>7.0f}{block.mean():>9.1f}")

    result = {"samples": int(len(frame)), "changed": int(len(changed))}
    if len(changed) >= 20:
        statistic, p_value = mannwhitneyu(unchanged, changed)
        # 효과크기도 낸다. n 이 크면 p 는 작아지지만 실무적 크기는 따로 봐야 한다.
        # rank-biserial correlation: 0 = 차이 없음, 1 = 완전 분리.
        effect = 1 - (2 * statistic) / (len(unchanged) * len(changed))
        print(f"\n  Mann-Whitney p = {p_value:.4g}")
        print(f"  효과크기(rank-biserial) = {effect:+.3f}")
        print(f"  중앙값 차이 {changed.median() - unchanged.median():+.0f}일   "
              f"평균 차이 {changed.mean() - unchanged.mean():+.1f}일")
        result.update({
            "p_value": float(p_value),
            "rank_biserial": float(effect),
            "median_unchanged": float(unchanged.median()),
            "median_changed": float(changed.median()),
            "mean_unchanged": float(unchanged.mean()),
            "mean_changed": float(changed.mean()),
        })
    else:
        print(f"\n  변경 있음이 {len(changed)}건뿐이라 검정을 생략한다.")

    # 변경차수별로도 본다. 차수가 오를수록 길어지면 단조 관계라 더 설득력이 있다.
    print(f"\n  변경차수별")
    by_order = frame.groupby(frame["change_order"].clip(upper=3))["lead_days"].agg(
        ["size", "median", "mean"]
    )
    for order, row in by_order.iterrows():
        label = f"{int(order)}차" + ("+" if order >= 3 else "")
        print(f"    {label:<6}{int(row['size']):>8,}  median {row['median']:>5.0f}일  "
              f"평균 {row['mean']:>6.1f}일")
    result["by_change_order"] = {
        str(int(k)): {"n": int(v["size"]), "median": float(v["median"]), "mean": float(v["mean"])}
        for k, v in by_order.iterrows()
    }

    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {OUT_PATH}")
    print("\n주의: 인과 방향은 이 데이터로 알 수 없다. 오래 걸릴 건이라 변경된 것인지,")
    print("      변경 때문에 늘어난 것인지 구분되지 않는다. 여전히 계약 납기이지")
    print("      실납품일이 아니다.")


if __name__ == "__main__":
    main()
