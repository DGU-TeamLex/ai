"""리드타임을 조달청 실측 분위수로 교체하고 하류를 재계산한다 (ai#39, ai#54).

## 왜 바꾸나

현행 `lead_time_used` 는 **재고소진기간 p25** 다.

    method = stockout_duration_p25
    = "재고가 0 이던 기간의 하위 25%" 를 조달 소요일의 대용으로 쓴다

이 값은 검증된 적이 없다. "재고가 0 이던 기간" 에는 발주를 안 한 기간도 섞이고,
실제 발주-입고 기록이 아니다. 실측 결과 79.8% 가 30일 미만으로 나온다.

조달청 납품요구는 **실제 조달 기록** 이다. 24개월 전수 30,815건에서

    p50 30   p60 30   p70 30   p75 30   p80 31   p90 60   p95 94

이 분포를 쓴다.

## 전 품목이 대상이다

앞서 만든 `quantile_lead_time_recommendation.csv` 는 피처테이블 스냅샷 기준이라
102,660행뿐이고 조인키도 품목명이라 약하다. 여기서는 **DB inventory 409,459행
전부** 에 적용한다. 각 행에 이미 있는 `supply_risk_level` 로 분위수를 정하므로
누락이 없다.

## 위험등급 → 분위수 → 리드타임

    NORMAL    p50   30일    위험 없음 = 표준 계약
    CAUTION   p75   30일    표준 계약 안에서 해결된다
    WARNING   p90   60일    오래 걸리는 건의 비중이 늘어난다
    CRITICAL  p95   94일    최악 대비

절대 점수가 아니라 **등급** 을 쓰는 이유는, 점수의 절대 척도가 신호 결합 방식에
따라 임의로 정해지기 때문이다(ai#20 결함 O). 등급은 그 영향을 덜 받는다.

리드타임이 30일에서 안 움직이는 구간(p50~p80)이 넓은 것은 데이터 성질이다.
계약 납기를 관행적으로 30일로 찍기 때문에 하위 80% 에 변동이 없다. 위험이
높은 소수만 꼬리로 보내는 것이 이 매핑의 의도다.

## 하류 재계산

리드타임만 바꾸면 SS·ROP·target 이 옛 리드타임 기준으로 남아 불일치한다.
같이 재계산한다.

    보호기간   R + L          R = 30일 (정기검토, ai#54)
    SS         z · σ · √(R+L)
    ROP        μ · L + SS
    target     μ · (R+L) + SS
    발주권고    max(0, target − 가용)

DRY_RUN=1(기본)이면 영향만 출력하고 롤백한다.

실행:
    DATABASE_URL=... python -m src.loading.apply_contract_lead_time
    DATABASE_URL=... DRY_RUN=0 python -m src.loading.apply_contract_lead_time
"""
from __future__ import annotations

import os
import sys

# 조달청 납품요구 24개월 전수 30,815건 실측(계약 납기).
# outputs/procurement_lead_time_by_item.csv 및 ai#20 코멘트 참조.
CONTRACT_QUANTILES = {50: 30.0, 60: 30.0, 70: 30.0, 75: 30.0, 80: 31.0, 90: 60.0, 95: 94.0}

# 위험등급 → 쓸 분위수. 등급이 없거나 모르는 값이면 NORMAL 로 본다(보수적).
LEVEL_TO_QUANTILE = {
    "NORMAL": 50,
    "CAUTION": 75,
    "WARNING": 90,
    "CRITICAL": 95,
}
DEFAULT_QUANTILE = 50

# 정기검토 주기. ai#54 에서 검토 방식(periodic)은 확정, 주기 값은 품목별 실측이
# 1~434일로 흩어져 미결이다. 여기서는 기존 기본값을 유지한다 — 리드타임 교체의
# 효과만 분리해 보기 위해서다.
REVIEW_PERIOD_DAYS = 30.0


def main() -> int:
    import psycopg

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("DATABASE_URL 환경변수가 필요하다.", file=sys.stderr)
        return 2
    dry_run = os.environ.get("DRY_RUN", "1") == "1"

    level_days = {
        level: CONTRACT_QUANTILES[q] for level, q in LEVEL_TO_QUANTILE.items()
    }
    print("조달청 실측 분위수 기반 리드타임")
    for level, quantile in LEVEL_TO_QUANTILE.items():
        print(f"  {level:<10}p{quantile:<4}{CONTRACT_QUANTILES[quantile]:>6.0f}일")
    print(f"  검토주기 R {REVIEW_PERIOD_DAYS:.0f}일\n")

    # CASE 식을 만든다. 등급이 NULL 이거나 모르는 값이면 기본 분위수를 쓴다.
    case_sql = " ".join(
        f"WHEN supply_risk_level = '{level}' THEN {days}"
        for level, days in level_days.items()
    )
    new_lead = f"(CASE {case_sql} ELSE {CONTRACT_QUANTILES[DEFAULT_QUANTILE]} END)"

    with psycopg.connect(dsn, connect_timeout=60) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM inventory")
            total = cursor.fetchone()[0]
            cursor.execute(
                f"""
                SELECT supply_risk_level, count(*), avg(lead_time_used), avg({new_lead}),
                       sum(order_recommendation)
                  FROM inventory GROUP BY 1 ORDER BY 2 DESC
                """
            )
            print(f"inventory {total:,}행")
            print(f"  {'등급':<10}{'행수':>10}{'현행 L':>9}{'신규 L':>9}{'현행 발주':>13}")
            for level, count, old_l, new_l, orders in cursor.fetchall():
                print(f"  {str(level):<10}{count:>10,}{float(old_l):>9.1f}"
                      f"{float(new_l):>9.1f}{int(orders or 0):>13,}")

            # 하류까지 한 번에 재계산한다. 리드타임만 바꾸면 SS/ROP/target 이
            # 옛 기준으로 남아 서로 어긋난다.
            cursor.execute(
                f"""
                UPDATE inventory SET
                    lead_time_used = {new_lead},
                    ss     = z_used * sigma * sqrt({REVIEW_PERIOD_DAYS} + {new_lead}),
                    rop    = mu * {new_lead}
                             + z_used * sigma * sqrt({REVIEW_PERIOD_DAYS} + {new_lead}),
                    target = mu * ({REVIEW_PERIOD_DAYS} + {new_lead})
                             + z_used * sigma * sqrt({REVIEW_PERIOD_DAYS} + {new_lead}),
                    order_recommendation = GREATEST(0, ROUND(
                        mu * ({REVIEW_PERIOD_DAYS} + {new_lead})
                        + z_used * sigma * sqrt({REVIEW_PERIOD_DAYS} + {new_lead})
                        - available))::int,
                    -- 상한 플래그는 더 이상 의미가 없다. 조달청 분위수는 94일이 최대다.
                    lead_time_policy_capped = FALSE
                """
            )
            print(f"\n  UPDATE {cursor.rowcount:,}행")

            cursor.execute(
                "SELECT avg(lead_time_used), min(lead_time_used), max(lead_time_used), "
                "sum(order_recommendation), sum(target) FROM inventory"
            )
            avg_l, min_l, max_l, orders, target = cursor.fetchone()
            print(f"  적용 후 L 평균 {float(avg_l):.1f}일  범위 {float(min_l):.0f}~{float(max_l):.0f}")
            print(f"  발주권고 합계 {int(orders):,}   target 합계 {float(target):,.0f}")

            if dry_run:
                connection.rollback()
                print("\n*** DRY_RUN=1 미반영 (롤백) ***")
                return 0
            connection.commit()
            print("\n*** 커밋 완료 ***")
            print("리드타임 근거가 재고소진기간 p25 → 조달청 계약 납기 실측으로 바뀌었다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
