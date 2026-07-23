"""supply_risk_level 재산정 — AI팀 정책(supply_risk_level_policy.json) 반영.

experiment/material-mapping-inventory-pilot 브랜치에서 AI팀이 확정한 정책.
우리 보고서(report_공급위험레벨_안전재고_과적재.md)를 반영해, GENERAL_LOW_RISK가
CRITICAL로 잡히던 오정합(184,032행=45%)을 code_rules로 바로잡는다.

정책: raw_material_risk_meta_code → baseline_level (NORMAL/CAUTION/WARNING/CRITICAL).
inventory.supply_risk_level 을 이 정책으로 재산정. 매핑없는 행은 NORMAL(보수적).

⚠️ z_used/lead_time_used/SS/ROP는 이 스크립트에서 안 건드림 — 레벨만 교체.
   레벨이 바뀌면 SS/ROP도 재계산해야 하나(z·LT배수), 그건 별도(ai#23).
   여기선 '레벨 표기 정합'만 우선 바로잡는다.
"""
import json, os, io
import psycopg

DRY_RUN = os.environ.get("DRY_RUN", "1") == "1"
POL = os.environ.get("POLICY_PATH", "data/handoff/supply_risk_level_policy.json")

pol = json.load(open(POL))
rules = {r["supply_risk_meta_code"]: r["baseline_level"] for r in pol["code_rules"]}
print(f"정책 version={pol['version']} status={pol['policy_status']} / code_rules {len(rules)}개")

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    cur = conn.cursor()
    # 매핑 임시테이블
    cur.execute("DROP TABLE IF EXISTS _riskpol")
    cur.execute("CREATE TEMP TABLE _riskpol (code TEXT, level TEXT) ON COMMIT DROP")
    buf = io.StringIO()
    for code, lvl in rules.items(): buf.write(f"{code},{lvl}\n")
    buf.seek(0)
    with cur.copy("COPY _riskpol FROM STDIN WITH (FORMAT CSV)") as cp: cp.write(buf.read())

    if DRY_RUN:
        cur.execute("""SELECT count(*) FILTER(WHERE i.supply_risk_level='CRITICAL'),
            count(*) FILTER(WHERE coalesce(p.level,'NORMAL')='CRITICAL')
            FROM inventory i LEFT JOIN item_meta_map m USING(standard_code)
            LEFT JOIN _riskpol p ON p.code=m.raw_material_risk_meta_code""")
        a,b=cur.fetchone(); print(f"[DRY] CRITICAL {a:,} → 정책적용 {b:,}")
        conn.rollback(); print("*** DRY_RUN=1 미반영 ***")
    else:
        cur.execute("""UPDATE inventory i
            SET supply_risk_level = coalesce(p.level,'NORMAL'), updated_at=now()
            FROM item_meta_map m LEFT JOIN _riskpol p ON p.code=m.raw_material_risk_meta_code
            WHERE i.standard_code=m.standard_code""")
        n1=cur.rowcount
        # 메타코드 매핑 없는 inventory 행은 NORMAL
        cur.execute("""UPDATE inventory SET supply_risk_level='NORMAL', updated_at=now()
            WHERE standard_code NOT IN (SELECT standard_code FROM item_meta_map)
            AND supply_risk_level IS DISTINCT FROM 'NORMAL'""")
        print(f"supply_risk_level 재산정: 매핑분 {n1:,} + 미매핑 {cur.rowcount:,}")
        cur.execute("SELECT supply_risk_level, count(*) FROM inventory GROUP BY 1 ORDER BY 2 DESC")
        for k,v in cur.fetchall(): print(f"   {k:<10} {v:,}")
        conn.commit(); print("*** 커밋 완료 ***")
