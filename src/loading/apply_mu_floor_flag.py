"""`mu_is_floored` / `sigma_is_floored` 플래그를 실제로 채운다 (ai#52).

## 왜

컬럼은 있는데 **0건이다.** 표시하려고 만든 플래그가 비어 있어서 화면·API 가
"이 값은 추정치가 아니라 바닥값" 이라는 것을 알 수 없다.

실측하면 규모가 크다.

    mu = 0.5 정확히        296,537 / 409,459  (72.42%)
    mu_is_floored = TRUE         0            (0.00%)
    mu 중앙값                   0.500          ← 중앙값이 바닥값이다

바닥값 행이 전체 발주권고의 18.36%, target 의 14.41% 를 만든다.

가장 이상한 것은 `DORMANT`(전 기간 소비 0 인 사장재고)에서 발주가 나가는
비율이 `ACTIVE` 보다 높다는 점이다.

    ACTIVE     182,792행   발주>0  34.4%
    DORMANT    134,892행   발주>0  41.2%   ← 안 쓰는 품목이 더 자주 발주된다
    CENSORED    91,775행   발주>0  42.5%

## 무엇을 하고 무엇을 하지 않나

**한다**: 바닥값에 걸린 행을 표시한다. 값은 바꾸지 않는다.

**하지 않는다**: 발주 억제. `order_suppress_reason` 에 DORMANT 를 넣는 것은
정책 결정이라 여기서 하지 않는다(ai#52 에서 판단 대기).

표시만 해도 얻는 것이 있다. 화면이 "추정 불가" 로 구분할 수 있고, 얼마나 많은
값이 실제 추정이 아닌지 팀이 계속 볼 수 있다. 되돌리기도 쉽다.

## 바닥값 판정

정확히 바닥값과 같은지로 본다. 부동소수 비교라 허용오차를 둔다. 원 추정치가
우연히 정확히 0.5 인 경우가 섞일 수 있으나, 소비가 0 인 계열이 45.6% 라
대부분은 진짜 바닥값이다. 구분이 필요하면 상류에서 플래그를 같이 내려야 한다.

DRY_RUN=1(기본)이면 영향만 출력하고 롤백한다.

실행:
    DATABASE_URL=... python -m src.loading.apply_mu_floor_flag
    DATABASE_URL=... DRY_RUN=0 python -m src.loading.apply_mu_floor_flag
"""
from __future__ import annotations

import os
import sys

# src/modeling 의 바닥값과 같은 값이어야 한다. 상수가 갈라지면 플래그가 거짓이 된다.
MU_FLOOR = 0.5
SIGMA_FLOOR = 0.1
TOLERANCE = 1e-9


def main() -> int:
    import psycopg

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("DATABASE_URL 환경변수가 필요하다.", file=sys.stderr)
        return 2
    dry_run = os.environ.get("DRY_RUN", "1") == "1"

    with psycopg.connect(dsn, connect_timeout=30) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM inventory")
            total = cursor.fetchone()[0]
            print(f"inventory {total:,}행")
            print(f"바닥값 기준  mu={MU_FLOOR}  sigma={SIGMA_FLOOR}\n")

            for column, floor in (("mu", MU_FLOOR), ("sigma", SIGMA_FLOOR)):
                flag = f"{column}_is_floored"
                cursor.execute(
                    f"SELECT count(*) FROM inventory WHERE abs({column} - %s) < %s",
                    (floor, TOLERANCE),
                )
                at_floor = cursor.fetchone()[0]
                cursor.execute(
                    f"SELECT count(*) FROM inventory WHERE {flag} IS TRUE"
                )
                already = cursor.fetchone()[0]
                print(f"  {column:<6} 바닥값 {at_floor:>9,} ({at_floor/total:>6.2%})"
                      f"   현재 플래그 TRUE {already:,}")
                cursor.execute(
                    f"UPDATE inventory SET {flag} = (abs({column} - %s) < %s)",
                    (floor, TOLERANCE),
                )
                print(f"         UPDATE {cursor.rowcount:,}행")

            cursor.execute(
                "SELECT demand_class, count(*) FROM inventory "
                "WHERE mu_is_floored IS TRUE GROUP BY 1 ORDER BY 2 DESC"
            )
            print("\n  바닥값 행의 demand_class:")
            for demand_class, count in cursor.fetchall():
                print(f"    {str(demand_class):<10}{count:>9,}")

            if dry_run:
                connection.rollback()
                print("\n*** DRY_RUN=1 미반영 (롤백) ***")
                return 0
            connection.commit()
            print("\n*** 커밋 완료 ***")
            print("값은 바꾸지 않았다. 발주 억제는 정책 결정이라 ai#52 에서 판단한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
