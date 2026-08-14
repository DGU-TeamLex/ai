"""DB 의 `lead_time_used` 를 레포 정책에 맞춘다 (ai#39, ai#54).

## 왜

`data/mapping/supply_risk_level_policy.json` 이 정본이다.

    method          stockout_duration_p25
    minimum_days    1.0
    maximum_days    120.0
    fallback_days   15.0

그런데 프로덕션 DB 의 값이 이 계약을 벗어나 있다. 상한 적용 전에 적재됐거나
적용 경로가 빠진 것으로 보인다.

    상한 120일 초과   8,183행 (2.00%)   최대 547.5일
    하한 1일 미만         0행

**2% 의 행이 전체 발주권고의 19.7% 를 만들고 있다.**

    상한 초과분 평균 리드타임   210.7일  →  상한 적용 시 120.0일
    그 행들의 target 합계       4,085,021   (전체의 11.1%)
    그 행들의 발주권고 합계      2,246,007   (전체의 19.7%)

리드타임이 547일이면 보호기간이 1년 반이 되고 목표재고가 그만큼 커진다.
정책이 상한을 120일로 정한 이유가 바로 이것이다.

## 무엇을 바꾸나

`lead_time_used` 만 상한으로 자른다. **ss·rop·target·order_recommendation 은
건드리지 않는다.** 그것들은 검토주기 R 에 의존하고 R 은 아직 미결이다(ai#54).
리드타임을 고치면 그 값들도 다시 계산해야 하지만, 그 재계산은 R 결정과 함께
해야 한다.

즉 이 스크립트를 돌리면 **DB 안에서 lead_time_used 와 ss/rop/target 이 일시적으로
불일치한다.** 그것을 감수하는 이유는, 잘못된 리드타임을 그대로 두면 다음
재계산이 또 잘못된 값에서 출발하기 때문이다. 불일치는 `lead_time_policy_capped`
플래그로 표시해 추적 가능하게 한다.

DRY_RUN=1(기본)이면 영향만 출력하고 롤백한다.

실행:
    DATABASE_URL=... python -m src.loading.apply_lead_time_policy_cap
    DATABASE_URL=... DRY_RUN=0 python -m src.loading.apply_lead_time_policy_cap
"""
from __future__ import annotations

import os
import sys

from ..module_c.supply_risk_policy import load_supply_risk_policy


def main() -> int:
    import psycopg

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("DATABASE_URL 환경변수가 필요하다.", file=sys.stderr)
        return 2
    dry_run = os.environ.get("DRY_RUN", "1") == "1"

    policy = load_supply_risk_policy()
    estimation = policy["lead_time_estimation"]
    minimum = float(estimation["minimum_days"])
    maximum = float(estimation["maximum_days"])
    print(f"정책 {policy['version']}")
    print(f"  min {minimum} / max {maximum} / {estimation['estimator_status']}\n")

    with psycopg.connect(dsn, connect_timeout=30) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM inventory")
            total = cursor.fetchone()[0]
            cursor.execute(
                "SELECT count(*), avg(lead_time_used), max(lead_time_used) "
                "FROM inventory WHERE lead_time_used > %s",
                (maximum,),
            )
            over_count, over_avg, over_max = cursor.fetchone()
            cursor.execute(
                "SELECT count(*) FROM inventory WHERE lead_time_used < %s",
                (minimum,),
            )
            under_count = cursor.fetchone()[0]

            print(f"inventory {total:,}행")
            print(f"  상한 초과 {over_count:,} ({over_count/total:.2%})  "
                  f"평균 {float(over_avg or 0):.1f}일  최대 {float(over_max or 0):.1f}일")
            print(f"  하한 미만 {under_count:,} ({under_count/total:.2%})")

            if not over_count and not under_count:
                print("\n정책을 벗어난 행이 없다. 변경하지 않는다.")
                return 0

            # 감사용 플래그. 이 행들은 lead_time_used 와 ss/rop/target 이
            # 일시적으로 불일치한다는 표시다.
            cursor.execute(
                "ALTER TABLE inventory ADD COLUMN IF NOT EXISTS "
                "lead_time_policy_capped BOOLEAN"
            )
            cursor.execute(
                """
                UPDATE inventory
                   SET lead_time_used = LEAST(GREATEST(lead_time_used, %s), %s),
                       lead_time_policy_capped = TRUE
                 WHERE lead_time_used > %s OR lead_time_used < %s
                """,
                (minimum, maximum, maximum, minimum),
            )
            changed = cursor.rowcount
            cursor.execute(
                "SELECT avg(lead_time_used), max(lead_time_used) FROM inventory"
            )
            new_avg, new_max = cursor.fetchone()
            print(f"\n  UPDATE {changed:,}행")
            print(f"  적용 후 평균 {float(new_avg):.2f}일  최대 {float(new_max):.1f}일")

            if dry_run:
                connection.rollback()
                print("\n*** DRY_RUN=1 미반영 (롤백) ***")
                return 0
            connection.commit()
            print("\n*** 커밋 완료 ***")
            print("주의: ss/rop/target/order_recommendation 은 아직 옛 리드타임 기준이다.")
            print("      R 결정(ai#54) 후 함께 재계산해야 한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
