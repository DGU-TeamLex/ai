"""재고 0 원인(zero_stock_reason) DB 적재 — compute_zero_stock_reason.py 산출물 반영 (ai#32).

NOT_OPERATED(미운영) / DATA_MISSING(데이터누락) / TRUE_STOCKOUT(실제결품).
재고 > 0 인 행은 NULL. 개별 status 는 건드리지 않고 원인만 부가한다.
DRY_RUN=1(기본)이면 조인 통계만 출력하고 롤백.
"""
import io
import os

import pandas as pd
import psycopg

DRY_RUN = os.environ.get("DRY_RUN", "1") == "1"
HANDOFF = os.environ.get("HANDOFF", "data/handoff/zero_stock_reason.csv")

df = pd.read_csv(HANDOFF)[["institution_id", "standard_code", "zero_stock_reason"]]
print(f"handoff {len(df):,}행 로드")

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    cur = conn.cursor()
    cur.execute("ALTER TABLE inventory ADD COLUMN IF NOT EXISTS zero_stock_reason TEXT")

    cur.execute("DROP TABLE IF EXISTS _zsr_up")
    cur.execute("""CREATE TEMP TABLE _zsr_up
        (institution_id TEXT, standard_code TEXT, zero_stock_reason TEXT) ON COMMIT DROP""")
    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False)
    buf.seek(0)
    with cur.copy("COPY _zsr_up FROM STDIN WITH (FORMAT CSV)") as cp:
        cp.write(buf.read())

    cur.execute("""SELECT count(*) FROM inventory i
        JOIN _zsr_up u USING(institution_id, standard_code) WHERE i.status='CRITICAL'""")
    print(f"CRITICAL 행과 조인: {cur.fetchone()[0]:,} / handoff {len(df):,}")

    if DRY_RUN:
        conn.rollback()
        print("*** DRY_RUN=1 미반영 ***")
    else:
        cur.execute("""UPDATE inventory i SET zero_stock_reason = u.zero_stock_reason
            FROM _zsr_up u
            WHERE i.institution_id = u.institution_id AND i.standard_code = u.standard_code""")
        print(f"UPDATE {cur.rowcount:,}행")
        cur.execute("""SELECT zero_stock_reason, count(*) FROM inventory
                       WHERE status='CRITICAL' GROUP BY 1 ORDER BY 2 DESC""")
        for r, n in cur.fetchall():
            print(f"   CRITICAL · {str(r):<15} {n:,}")
        conn.commit()
        print("*** 커밋 완료 ***")
