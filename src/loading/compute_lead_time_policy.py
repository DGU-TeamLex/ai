"""리드타임 극단값이 안전재고에 미치는 영향을 정량화한다 (ai#39).

`SS = z × sigma × √L`, `ROP = mu × L + SS` 구조상 L 이 안전재고와 재주문점을 전부
좌우한다. 현재 적용값에는 최댓값 547.5일이 남아 있어 안전재고를 비정상적으로 팽창시킨다.

이 스크립트는 **상한(cap)을 씌웠을 때 SS/ROP/발주권고 총량이 얼마나 줄어드는지**를
DB 실측으로 계산한다. 정책 결정(어느 상한을 쓸지)에 필요한 근거를 만드는 것이 목적이고,
DB 는 읽기만 한다.

## 할 수 있는 것과 없는 것

  ○ 상한 도입 효과 — 적용된 `lead_time_used` 가 DB 에 있으므로 전량 재계산 가능
  ✗ median → p25 전환 효과 — 품목별 p25 는 **원장(1,626만행)에서 다시 뽑아야** 한다.
    DB 에는 품목별 대표값 하나만 남아 있어 분위수를 되살릴 수 없다.
    p25 시뮬레이션이 필요하면 `물품재고_정규화완료.parquet` 를 입력으로 별도 산출할 것.

실행:
    DATABASE_URL=... python3 src/loading/compute_lead_time_policy.py
"""
import os

CAPS = [int(c) for c in os.environ.get("CAPS", "180,120,90,60,30").split(",")]


def main():
    import psycopg

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        cur = conn.cursor()

        cur.execute(
            """SELECT count(*), avg(lead_time_used), min(lead_time_used), max(lead_time_used),
                      percentile_cont(0.10) WITHIN GROUP (ORDER BY lead_time_used),
                      percentile_cont(0.25) WITHIN GROUP (ORDER BY lead_time_used),
                      percentile_cont(0.50) WITHIN GROUP (ORDER BY lead_time_used),
                      percentile_cont(0.90) WITHIN GROUP (ORDER BY lead_time_used),
                      count(DISTINCT lead_time_used)
               FROM inventory"""
        )
        n, avg, mn, mx, p10, p25, p50, p90, distinct = cur.fetchone()
        print(f"적용 리드타임 분포 (inventory {n:,}행)")
        print(f"  평균 {avg:.2f}  최소 {mn}  최대 {mx}  고유값 {distinct:,}종")
        print(f"  P10 {p10:.1f} / P25 {p25:.1f} / P50 {p50:.1f} / P90 {p90:.1f}")

        for c in sorted(CAPS, reverse=True):
            cur.execute("SELECT count(*) FROM inventory WHERE lead_time_used > %s", (c,))
            print(f"  {c}일 초과: {cur.fetchone()[0]:,}행")

        # 현행 총량
        cur.execute(
            """SELECT sum(ss), sum(rop), sum(order_recommendation)
               FROM inventory WHERE status <> 'EXCLUDED'"""
        )
        ss0, rop0, ord0 = cur.fetchone()
        print(f"\n현행 총량  SS {ss0:,.0f}   ROP {rop0:,.0f}   발주권고 {ord0:,}")

        print("\n상한 도입 시 재계산 (SS = z·sigma·√L, ROP = mu·L + SS)")
        print(f"  {'상한':>6} {'SS 총합':>14} {'감소율':>8} {'ROP 총합':>14} {'감소율':>8} {'영향행':>9}")
        for c in sorted(CAPS, reverse=True):
            cur.execute(
                """SELECT sum(z_used * sigma * sqrt(LEAST(lead_time_used, %s))),
                          sum(mu * LEAST(lead_time_used, %s)
                              + z_used * sigma * sqrt(LEAST(lead_time_used, %s))),
                          count(*) FILTER (WHERE lead_time_used > %s)
                   FROM inventory WHERE status <> 'EXCLUDED'""",
                (c, c, c, c),
            )
            ss1, rop1, affected = cur.fetchone()
            print(
                f"  {c:>4}일 {ss1:>14,.0f} {100 * (1 - ss1 / ss0):>7.1f}% "
                f"{rop1:>14,.0f} {100 * (1 - rop1 / rop0):>7.1f}% {affected:>9,}"
            )

        # 상한이 걸리는 구간이 실제로 어떤 품목인지
        cur.execute(
            """SELECT si.standard_name, i.lead_time_used, count(*) AS rows
               FROM inventory i JOIN standard_items si ON si.standard_code = i.standard_code
               WHERE i.lead_time_used > 120
               GROUP BY 1, 2 ORDER BY rows DESC LIMIT 10"""
        )
        print("\n120일 초과 상위 품목")
        for name, lt, rows in cur.fetchall():
            print(f"  {lt:>6.1f}일  {rows:>5,}행  {name[:44]}")


if __name__ == "__main__":
    main()
