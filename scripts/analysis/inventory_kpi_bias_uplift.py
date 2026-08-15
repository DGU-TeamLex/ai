"""어제 'WAPE 를 악화시켜 폐기' 한 보정을 재고 기준으로 다시 판정한다.

## 왜 다시 보나

어제 결론은 이랬다.

    패턴별 BIAS 보정: WAPE 36.844% → 37.202% (+0.358%p 악화) → 폐기

그런데 `inventory_kpi_vs_accuracy.py` 에서 WAPE 순위와 충족률 순위가 **일치하지
않는다** 는 것이 우리 데이터로 확인됐다(naive 가 WAPE 4위인데 충족률 3위).
Petropoulos et al. (2024, EJOR) 의 M5 결과가 우리 데이터에서도 재현된 것이다.

그렇다면 "WAPE 를 악화시킨다" 는 폐기 사유가 성립하지 않는다. 재고 기준으로
다시 재야 한다.

## 기대

현행 모형은 BIAS -6.44% 로 **과소예측** 이다. 과소예측은 목표재고를 낮추고
품절을 늘린다. 배율을 올리면 WAPE 는 나빠지지만 충족률은 올라갈 것이다.

핵심 질문은 "올라가느냐" 가 아니라 **"재고를 얼마나 더 쌓는 대가로 올라가느냐"**
다. 그냥 배율을 키우면 충족률은 당연히 오른다. 공짜가 아니다.

그래서 **같은 평균재고에서 충족률이 더 높은가** 를 본다. 배율을 키운 것과
안전계수 z 를 키운 것을 같은 재고 수준에서 맞대어, 배율 보정이 z 상향보다
효율적인지 따진다. 이게 아니면 배율 보정은 z 를 돌려 쓰면 되는 일이라 의미가
없다.

## 한계

* sigma 를 평가구간 실측으로 추정한다(4개월). 절대 수준은 낙관적이다.
* 손실판매 가정. 이월로 보면 수치가 달라진다.
* 비용 파라미터를 모른다. 충족률과 재고를 따로 낸다.

실행:
    .venv/Scripts/python.exe scripts/analysis/inventory_kpi_bias_uplift.py
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.analysis.inventory_kpi_vs_accuracy import (  # noqa: E402
    PROTECTION, Z_SERVICE, _load, _simulate, _wape,
)

OUT_PATH = ROOT / "outputs" / "inventory_kpi_bias_uplift.json"
START = 1
MULTIPLIERS = [1.00, 1.05, 1.10, 1.15, 1.20, 1.30]
Z_LEVELS = [1.645, 1.75, 1.88, 2.05, 2.33]


def _simulate_z(mu_hat, actual, sigma, z):
    """z 만 바꿔 같은 시뮬레이션을 돌린다."""
    import scripts.analysis.inventory_kpi_vs_accuracy as base

    original = base.Z_SERVICE
    base.Z_SERVICE = z
    try:
        return base._simulate(mu_hat, actual, sigma, START)
    finally:
        base.Z_SERVICE = original


def main() -> None:
    pattern, actual, model, months = _load()
    sigma = actual.std(axis=1, ddof=0)
    print(f"계열 {len(actual):,}개 / 평가 {months[START]}~{months[-1]}")
    print(f"보호기간 {PROTECTION:.0f}개월 / 기준 z={Z_SERVICE}\n")

    print("=== A. 예측 배율을 올린다 (z 고정) ===")
    print(f"{'배율':<8}{'WAPE':>9}{'충족률':>10}{'품절월':>10}{'평균재고':>11}")
    arm_multiplier = []
    for mult in MULTIPLIERS:
        kpi = _simulate(model * mult, actual, sigma, START)
        kpi["wape"] = _wape(model * mult, actual, START)
        kpi["knob"] = mult
        arm_multiplier.append(kpi)
        print(f"  {mult:<6.2f}{kpi['wape']:>9.3f}{kpi['fill_rate']*100:>9.2f}%"
              f"{kpi['stockout_month_rate']*100:>9.2f}%{kpi['mean_on_hand']:>11.1f}")

    print("\n=== B. 안전계수 z 를 올린다 (배율 고정) ===")
    print(f"{'z':<8}{'WAPE':>9}{'충족률':>10}{'품절월':>10}{'평균재고':>11}")
    arm_z = []
    for z in Z_LEVELS:
        kpi = _simulate_z(model, actual, sigma, z)
        kpi["wape"] = _wape(model, actual, START)  # z 는 WAPE 에 영향 없음
        kpi["knob"] = z
        arm_z.append(kpi)
        print(f"  {z:<6.3f}{kpi['wape']:>9.3f}{kpi['fill_rate']*100:>9.2f}%"
              f"{kpi['stockout_month_rate']*100:>9.2f}%{kpi['mean_on_hand']:>11.1f}")

    # 같은 재고 수준에서 어느 손잡이가 충족률을 더 주는가.
    # z 곡선을 재고축에 대해 선형보간해 배율 지점과 맞댄다.
    print("\n=== C. 같은 평균재고에서 비교 (핵심) ===")
    z_stock = np.array([k["mean_on_hand"] for k in arm_z])
    z_fill = np.array([k["fill_rate"] for k in arm_z])
    order = np.argsort(z_stock)
    z_stock, z_fill = z_stock[order], z_fill[order]

    print(f"{'배율':<8}{'평균재고':>11}{'배율 충족률':>13}{'z 등가 충족률':>15}{'차이':>10}")
    verdict_rows = []
    for kpi in arm_multiplier:
        stock = kpi["mean_on_hand"]
        if not (z_stock.min() <= stock <= z_stock.max()):
            print(f"  {kpi['knob']:<6.2f}{stock:>11.1f}   z 곡선 범위 밖, 비교 불가")
            continue
        equivalent = float(np.interp(stock, z_stock, z_fill))
        gap = kpi["fill_rate"] - equivalent
        verdict_rows.append({"multiplier": kpi["knob"], "mean_on_hand": stock,
                             "fill_multiplier": kpi["fill_rate"],
                             "fill_z_equivalent": equivalent, "gap_pp": gap * 100})
        print(f"  {kpi['knob']:<6.2f}{stock:>11.1f}{kpi['fill_rate']*100:>12.2f}%"
              f"{equivalent*100:>14.2f}%{gap*100:>+9.3f}%p")

    best = max(verdict_rows, key=lambda r: r["gap_pp"]) if verdict_rows else None
    print()
    if best is None:
        print("  판정 불가: 비교 가능한 구간이 없다.")
    elif best["gap_pp"] > 0.05:
        print(f"  판정: 배율 보정이 z 상향보다 낫다. 최대 +{best['gap_pp']:.3f}%p "
              f"(배율 {best['multiplier']:.2f}, 재고 {best['mean_on_hand']:.1f})")
    else:
        print(f"  판정: 배율 보정은 z 상향과 사실상 같다 (최대 {best['gap_pp']:+.3f}%p).")
        print("        같은 일을 하는 손잡이가 둘이면 z 하나만 쓰는 편이 낫다.")

    OUT_PATH.write_text(json.dumps({
        "eval_months": months[START:],
        "series": int(len(actual)),
        "arm_multiplier": arm_multiplier,
        "arm_z": arm_z,
        "equal_stock_comparison": verdict_rows,
        "caveats": [
            "sigma 를 평가구간 실측으로 추정 — 절대 수준 낙관 편향",
            "손실판매 가정",
            "z 등가 충족률은 선형보간값",
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {OUT_PATH}")


if __name__ == "__main__":
    main()
