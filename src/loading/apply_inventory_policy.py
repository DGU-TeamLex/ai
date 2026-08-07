"""handoff(inventory_policy.csv) → DB inventory 반영. mu/sigma 교체 + SS/ROP 재계산 (ai#52).

[왜 재계산까지 하나]
  기존 apply_* 스크립트들은 컬럼만 덧칠했다. 특히 apply_supply_risk_policy.py 는 주석에
  "z_used/lead_time_used/SS/ROP는 안 건드림" 이라고 명시돼 있다. 그래서 mu_corrected /
  mu_forecast 를 새로 넣어도 SS·ROP 는 옛 mu 로 계산된 값이 그대로 남았다.

  실측(2026-07-30, 헤모포스정 WMD0317947):
      mu 63.12 · mu_corrected 70.18 · sigma 219.96 · z_used 2.05 · L 16.2
      저장 ss 1,817.7 / rop 2,843.5  →  (rop-ss)/L = 63.32  = mu 기준
  즉 ROP 는 mu 로, 화면 소진곡선은 mu_corrected 로 그려져 기준이 어긋났다(frontend#43 에서
  화면 쪽 임시 대응). mu 만 갈아끼우고 SS/ROP 를 그냥 두면 같은 어긋남이 다시 생긴다.

[계산식] backend/scripts/ai_policy_adapter.py 에 문서화된 계약과 동일하게 둔다.
      SS     = z_used * sigma * sqrt(lead_time_used)
      ROP    = mu * lead_time_used + SS
      target = mu * (lead_time_used + 1) + SS
      권고량  = max(0, round(target - available))
  z_used·lead_time_used 는 건드리지 않는다 — 그건 공급위험·리드타임 정책 소관이다.

[발주 억제] AI 정책(docs/2026-07-29_05_GITHUB_ISSUE_REMEDIATION.md)
      DORMANT / NOT_OPERATED           → 권고량 0
      DATA_MISSING / stale             → 정책은 null 이지만 컬럼이 NOT NULL 이라 0 (편차 보고)

[안 건드리는 것] status 파생, z_used, lead_time_used, supply_risk_level.
  status 는 서빙 시점 관심사라 backend 소관이다(schema.sql 주석).

실행
  DRY_RUN=1 DATABASE_URL=... python3 src/loading/apply_inventory_policy.py   # 검증만
  DRY_RUN=0 DATABASE_URL=... python3 src/loading/apply_inventory_policy.py   # 실제 반영
"""
import io
import os

import pandas as pd
import psycopg

DRY_RUN = os.environ.get("DRY_RUN", "1") == "1"
HANDOFF = os.environ.get("HANDOFF", "data/handoff/inventory_policy.csv")
# 조인율이 이 값 미만이면 반영하지 않는다 — 키 규약이 어긋난 상태로 덮어쓰는 사고를 막는다.
MIN_JOIN_RATIO = float(os.environ.get("MIN_JOIN_RATIO", "0.90"))

COLS = [
    "institution_id", "standard_code", "on_hand", "mu", "sigma", "mu_forecast",
    "demand_class", "zero_stock_reason", "order_suppress_reason",
]

df = pd.read_csv(HANDOFF)
missing = sorted(set(COLS) - set(df.columns))
if missing:
    raise SystemExit(f"[중단] handoff 에 컬럼이 없다: {missing}")
df = df[COLS]
print(f"handoff {len(df):,}행 로드 ({HANDOFF})")

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    cur = conn.cursor()

    cur.execute("""
        SELECT count(*),
               count(*) FILTER (WHERE mu <= 0.5),
               count(*) FILTER (WHERE sigma <= 0.1),
               round(avg(rop)::numeric, 1)
        FROM inventory
    """)
    n0, mu0, sg0, rop0 = cur.fetchone()
    print(f"반영 전: 전체 {n0:,} · mu<=0.5 {mu0:,} ({mu0/n0*100:.1f}%) · sigma<=0.1 {sg0:,} · 평균 ROP {rop0}")

    cur.execute("""
        CREATE TEMP TABLE _inv_policy (
            institution_id TEXT, standard_code TEXT,
            on_hand DOUBLE PRECISION, mu DOUBLE PRECISION, sigma DOUBLE PRECISION,
            mu_forecast DOUBLE PRECISION, demand_class TEXT,
            zero_stock_reason TEXT, order_suppress_reason TEXT
        ) ON COMMIT DROP
    """)
    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False)
    buf.seek(0)
    with cur.copy("COPY _inv_policy FROM STDIN WITH (FORMAT CSV)") as cp:
        cp.write(buf.read())

    cur.execute("""
        SELECT count(*) FROM inventory i JOIN _inv_policy u USING (institution_id, standard_code)
    """)
    joined = cur.fetchone()[0]
    ratio = joined / len(df) if len(df) else 0
    print(f"조인: {joined:,} / handoff {len(df):,} ({ratio*100:.1f}%) · DB 미포함 {len(df)-joined:,}")
    if ratio < MIN_JOIN_RATIO:
        raise SystemExit(
            f"[중단] 조인율 {ratio*100:.1f}% < {MIN_JOIN_RATIO*100:.0f}%. "
            "키 규약(institution_id 매핑 / standard_code=물품코드)을 먼저 맞출 것."
        )

    # SS/ROP 를 새 mu·sigma 로 재계산한다. z_used·lead_time_used 는 그대로 쓴다.
    cur.execute("""
        UPDATE inventory i SET
            on_hand   = greatest(0, round(u.on_hand))::int,
            available = greatest(0, round(u.on_hand))::int,
            mu        = u.mu,
            sigma     = u.sigma,
            mu_forecast = u.mu_forecast,
            demand_class = coalesce(u.demand_class, i.demand_class),
            zero_stock_reason = coalesce(u.zero_stock_reason, i.zero_stock_reason),
            ss     = round((i.z_used * u.sigma * sqrt(i.lead_time_used))::numeric, 1),
            rop    = round((u.mu * i.lead_time_used
                            + i.z_used * u.sigma * sqrt(i.lead_time_used))::numeric, 1),
            target = round((u.mu * (i.lead_time_used + 1)
                            + i.z_used * u.sigma * sqrt(i.lead_time_used))::numeric, 1),
            order_recommendation = CASE
                WHEN u.order_suppress_reason IS NOT NULL THEN 0
                ELSE greatest(0, round(
                    u.mu * (i.lead_time_used + 1)
                    + i.z_used * u.sigma * sqrt(i.lead_time_used)
                    - greatest(0, round(u.on_hand))
                ))::int
            END,
            updated_at = now()
        FROM _inv_policy u
        WHERE i.institution_id = u.institution_id AND i.standard_code = u.standard_code
    """)
    print(f"UPDATE {cur.rowcount:,}행")

    cur.execute("""
        SELECT count(*) FILTER (WHERE mu <= 0.5),
               count(*) FILTER (WHERE sigma <= 0.1),
               round(avg(rop)::numeric, 1),
               count(*) FILTER (WHERE order_recommendation > 0),
               -- 재계산이 실제로 일관되는지: (rop-ss)/L 이 mu 와 같아야 한다
               count(*) FILTER (
                   WHERE lead_time_used > 0
                     AND abs((rop - ss) / lead_time_used - mu) > 0.05
               )
        FROM inventory
    """)
    mu1, sg1, rop1, rec1, incons = cur.fetchone()
    print(f"반영 후: mu<=0.5 {mu1:,} · sigma<=0.1 {sg1:,} · 평균 ROP {rop1} · 발주권고>0 {rec1:,}")
    print(f"ROP 일관성 위반((rop-ss)/L != mu): {incons:,}행  ← 0 이어야 한다")

    cur.execute("SELECT count(*) FROM _inv_policy WHERE order_suppress_reason IN ('DATA_MISSING','STALE')")
    nullish = cur.fetchone()[0]
    if nullish:
        print(f"⚠ 정책상 권고량 null 대상 {nullish:,}행을 0 으로 넣었다 — order_recommendation NOT NULL 제약")

    if DRY_RUN:
        conn.rollback()
        print("\n*** DRY_RUN=1 — 롤백했다. 반영하려면 DRY_RUN=0 ***")
    else:
        conn.commit()
        print("\n반영 완료(commit).")
