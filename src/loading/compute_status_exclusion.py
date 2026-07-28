"""재고 0 원인별 status 제외 대상을 산출한다 (ai#38).

`zero_stock_reason` 은 ai#32 로 적재됐지만 `status` 는 여전히 on_hand 대비 rop 만
보고 판정한다. 그래서 해당 기관이 취급하지 않는 품목까지 CRITICAL 로 뜨고 발주까지
권고된다(2026-07-23 기관 리뷰회의 지적).

이 스크립트는 **제외 대상 목록과 영향 규모만** 만든다. DB 는 읽기만 하며 반영하지 않는다.
반영은 handoff CSV 를 받아 별도 apply 스크립트에서 수행한다(소유권 경계상 ai 소유).

## 판정 규칙

    NOT_OPERATED                    → 제외. 전 기간 정상출고 합이 0 이라 수요 자체가 없다.
    DATA_MISSING & recent3m <= 0    → 제외. 정합성 위반이 있고 최근 수요도 없다.
    DATA_MISSING & recent3m >  0    → 유지. 최근 3개월 실수요가 있으므로 발주 검토 대상.
    TRUE_STOCKOUT                   → 유지. 실제 결품이다.

`recent3m`(최근 3개월 정상출고 합)은 handoff CSV 에 이미 들어 있다.

## 제안 status 값 — `EXCLUDED`

기존 enum 은 OK / WATCH / BELOW_ROP / CRITICAL 뿐이다.
- `OK` 로 내리면 "정상 재고 보유"로 오독된다. 실제로는 재고가 0 이다.
- 사유별로 값을 늘리면 화면 배지가 두 개 늘고, 사유는 이미 `zero_stock_reason` 에
  그대로 있어 중복이다.

그래서 `EXCLUDED`(재고 판정 대상 아님) 하나만 제안한다. 사유는 `zero_stock_reason` 을 본다.
backend `/alerts/derived` 는 `status = ANY('{CRITICAL,BELOW_ROP}')` 로 거르므로
이 값이 되면 부족 알림과 발주권고에서 자동으로 빠진다.
frontend `StatusBadge` 는 미지 status 를 원문+중립 스타일로 폴백해 화면은 깨지지 않지만,
라벨(`STATUS_LABEL`)은 추가해야 한다 — frontend 협의 항목.

실행:
    DATABASE_URL=... python3 src/loading/compute_status_exclusion.py
"""
import os

import pandas as pd
import psycopg

HANDOFF = os.environ.get("HANDOFF", "data/handoff/zero_stock_reason.csv")
OUT = os.environ.get("OUT", "data/handoff/status_exclusion.csv")
PROPOSED_STATUS = os.environ.get("PROPOSED_STATUS", "EXCLUDED")
TARGET_STATUSES = ("CRITICAL", "BELOW_ROP", "WATCH")


def main():
    df = pd.read_csv(HANDOFF)[
        ["institution_id", "standard_code", "zero_stock_reason", "recent3m"]
    ]
    df["recent3m"] = pd.to_numeric(df["recent3m"], errors="coerce").fillna(0)
    print(f"handoff {len(df):,}행 로드")

    is_excl = (df["zero_stock_reason"] == "NOT_OPERATED") | (
        (df["zero_stock_reason"] == "DATA_MISSING") & (df["recent3m"] <= 0)
    )
    excl = df[is_excl].copy()
    kept = int(((df["zero_stock_reason"] == "DATA_MISSING") & (df["recent3m"] > 0)).sum())
    print(f"제외 후보 {len(excl):,}행   DATA_MISSING 중 최근수요 있어 유지 {kept:,}행")

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT zero_stock_reason, status, count(*), sum(order_recommendation)
               FROM inventory WHERE zero_stock_reason IS NOT NULL
               GROUP BY 1, 2 ORDER BY 1, 2"""
        )
        print("현재 사유 × status:")
        for reason, status, n, orders in cur.fetchall():
            print(f"  {reason:<14} {status:<10} {n:>7,}행  발주 {orders or 0:>9,}")

        cur.execute("SELECT institution_id, standard_code, status, order_recommendation FROM inventory")
        inv = pd.DataFrame(cur.fetchall(),
                           columns=["institution_id", "standard_code", "status", "order_recommendation"])

    m = excl.merge(inv, on=["institution_id", "standard_code"], how="inner")
    hit = m[m["status"].isin(TARGET_STATUSES)]
    print(f"\n영향 행수 {len(hit):,}   제거될 발주권고 {int(hit['order_recommendation'].sum()):,}개")
    print("  기존 status 내역:", hit["status"].value_counts().to_dict())

    out = hit[["institution_id", "standard_code", "zero_stock_reason", "recent3m",
               "status", "order_recommendation"]].copy()
    out = out.rename(columns={"status": "status_current",
                              "order_recommendation": "order_recommendation_current"})
    out["status_proposed"] = PROPOSED_STATUS
    out["order_recommendation_proposed"] = 0

    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    out.to_csv(OUT, index=False)
    print(f"WROTE {OUT} ({len(out):,}행)")


if __name__ == "__main__":
    main()
