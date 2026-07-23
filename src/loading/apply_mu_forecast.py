"""mu_forecast(roll3 일수요율) DB 적재. compute_mu_forecast.py 산출물을 inventory 에 반영.

컬럼은 IF NOT EXISTS 로 보장(스키마=backend 정의와 동일, 여기선 안전 보장용).
값만 UPDATE — status/SS/ROP/mu_corrected 등 다른 계산은 건드리지 않음.
DRY_RUN=1(기본) 이면 조인 통계만 출력하고 롤백.
"""
import io
import os

import pandas as pd
import psycopg

DRY_RUN = os.environ.get("DRY_RUN", "1") == "1"
HANDOFF = os.environ.get("HANDOFF", "data/handoff/mu_forecast.csv")

df = pd.read_csv(HANDOFF)[["institution_id", "standard_code", "mu_forecast"]]
print(f"handoff {len(df):,}행 로드")

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    cur = conn.cursor()
    cur.execute("ALTER TABLE inventory ADD COLUMN IF NOT EXISTS mu_forecast DOUBLE PRECISION")

    cur.execute("DROP TABLE IF EXISTS _mu_forecast_up")
    cur.execute("""CREATE TEMP TABLE _mu_forecast_up
        (institution_id TEXT, standard_code TEXT, mu_forecast DOUBLE PRECISION) ON COMMIT DROP""")
    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False)
    buf.seek(0)
    with cur.copy("COPY _mu_forecast_up FROM STDIN WITH (FORMAT CSV)") as cp:
        cp.write(buf.read())

    cur.execute("SELECT count(*) FROM inventory i JOIN _mu_forecast_up u USING(institution_id,standard_code)")
    joined = cur.fetchone()[0]
    print(f"inventory 조인: {joined:,} / handoff {len(df):,} ({joined / len(df) * 100:.1f}%)")

    if DRY_RUN:
        conn.rollback()
        print("*** DRY_RUN=1 미반영 ***")
    else:
        cur.execute("""UPDATE inventory i SET mu_forecast = u.mu_forecast
            FROM _mu_forecast_up u
            WHERE i.institution_id = u.institution_id AND i.standard_code = u.standard_code""")
        print(f"UPDATE {cur.rowcount:,}행")
        cur.execute("""SELECT count(mu_forecast),
                              count(*) FILTER (WHERE status='CRITICAL' AND mu_forecast>0)
                       FROM inventory""")
        r = cur.fetchone()
        print(f"mu_forecast 채워진 행 {r[0]:,} / CRITICAL+실수요>0 {r[1]:,}")
        conn.commit()
        print("*** 커밋 완료 ***")
