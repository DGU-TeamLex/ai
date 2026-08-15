"""정확도(WAPE)가 재고 성과로 이어지는가 — 우리 데이터로 직접 검정.

## 왜 이걸 하나

어제 배율 보정 3종을 다 실패시키고 "WAPE 는 더 못 줄인다" 로 정리했다. 그런데
그 결론은 **WAPE 가 옳은 판단 기준이라는 전제** 위에 서 있었다. 그 전제를
검증한 적이 없다.

문헌은 그 전제를 정면으로 부정한다.

* Petropoulos et al. (2024), *Forecast accuracy and inventory performance:
  Insights on their relationship from the M5 competition data*, EJOR —
  M5 데이터로 재고 시뮬레이션을 돌린 결과, 정확도와 재고비용의 관계가 약하고
  품목 특성·재고정책·비용구조에 따라 달라진다.
* Syntetos & Boylan 계열 연구 일관되게: 통계적으로 최우수인 모형(XGBoost,
  RF)이 총재고비용을 최소화하지 않고, 정확도가 낮은 고전적 간헐수요 기법
  (Croston/SBA/TSB)이 더 낮은 비용을 내는 경우가 있다.

우리 과제명은 "적정재고 예측 모형" 이다. **납품물은 재고 결정이지 예측값이
아니다.** 그러면 판단 기준도 재고 성과여야 한다.

## 무엇을 재나

정기발주 (R,S) 를 5개월 백테스트 구간에 그대로 돌린다. 운영 코드와 같은 정책.

    보호기간 = R + L = 30 + 30 = 60일 = 2개월
    목표재고 S_t = mu_hat_t x 2 + z x sigma x sqrt(2)
    발주량 q_t = max(0, S_t - 재고포지션)
    입고 = L(1개월) 후
    미충족 = 손실판매 (보건소는 긴급조달로 메우므로 이월 아님)

예측기 4종을 같은 정책에 꽂아 넣고 **재고 KPI 로 순위** 를 매긴 뒤, **WAPE
순위와 비교** 한다. 두 순위가 다르면 우리가 잘못된 지표를 최적화한 것이다.

    model       현행 모형
    naive       직전월 실측
    cumavg      누적평균
    oracle      실측 그 자체 (하한선)

## 한계 — 반드시 읽을 것

* sigma 를 평가구간 실측으로 잡는다. 5개월뿐이라 구간 밖 추정이 불가능하다.
  **네 예측기에 동일하게 적용** 하므로 순위 비교는 유효하지만, 절대 수준은
  낙관적으로 치우친다.
* 비용 파라미터(보유비/품절비)를 모른다. 그래서 비용 단일 수치 대신
  **충족률과 평균재고를 따로** 낸다. 어느 쪽이 중한지는 팀장 판단 사항이다.
* 손실판매로 가정했다. 이월(backorder) 로 보면 충족률이 달라진다.
* 초기재고를 S_0 으로 둔다. 실제 시작 재고와 다르다.

실행:
    .venv/Scripts/python.exe scripts/analysis/inventory_kpi_vs_accuracy.py
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SOURCE = ROOT / "data" / "handoff" / "backtest_predictions.parquet"
OUT_PATH = ROOT / "outputs" / "inventory_kpi_vs_accuracy.json"

REVIEW_MONTHS = 1.0          # R = 30일
LEAD_MONTHS = 1.0            # L = 30일 (조달청 계약 납기 p50)
PROTECTION = REVIEW_MONTHS + LEAD_MONTHS
Z_SERVICE = 1.645            # 95% 서비스수준


def _load() -> tuple[pd.DataFrame, np.ndarray, np.ndarray, list[str]]:
    frame = pd.read_parquet(
        SOURCE,
        columns=["year_month", "stock_item_key", "actual_usage",
                 "predicted_usage", "demand_pattern"],
    )
    frame["year_month"] = frame["year_month"].astype(str)
    months = sorted(frame["year_month"].unique())

    actual = frame.pivot_table(
        index="stock_item_key", columns="year_month",
        values="actual_usage", aggfunc="sum",
    ).reindex(columns=months)
    pred = frame.pivot_table(
        index="stock_item_key", columns="year_month",
        values="predicted_usage", aggfunc="sum",
    ).reindex(columns=months)

    # 5개월이 모두 있는 계열만 쓴다. 중간에 빠진 계열은 시뮬레이션이 성립하지
    # 않는다(발주-입고 연쇄가 끊긴다).
    complete = actual.notna().all(axis=1) & pred.notna().all(axis=1)
    actual, pred = actual[complete], pred[complete]

    pattern = (frame.drop_duplicates("stock_item_key")
               .set_index("stock_item_key")["demand_pattern"]
               .reindex(actual.index))
    return pattern, actual.to_numpy(float), pred.to_numpy(float), months


def _forecasts(actual: np.ndarray, model: np.ndarray) -> dict[str, np.ndarray]:
    """t 시점에 쓸 수 있는 정보만으로 만든 예측기들."""
    naive = np.empty_like(actual)
    naive[:, 0] = np.nan
    naive[:, 1:] = actual[:, :-1]

    cumavg = np.empty_like(actual)
    cumavg[:, 0] = np.nan
    for t in range(1, actual.shape[1]):
        cumavg[:, t] = actual[:, :t].mean(axis=1)

    return {"model": model, "naive": naive, "cumavg": cumavg, "oracle": actual}


def _simulate(mu_hat: np.ndarray, actual: np.ndarray, sigma: np.ndarray,
              start: int) -> dict[str, float]:
    """정기발주 (R,S) 를 start 시점부터 돌린다. 손실판매 가정."""
    n_series, n_months = actual.shape
    safety = Z_SERVICE * sigma * np.sqrt(PROTECTION)

    target = np.clip(mu_hat, 0, None) * PROTECTION + safety[:, None]
    on_hand = target[:, start].copy()   # 초기재고를 첫 목표재고로 둔다
    pipeline = np.zeros(n_series)       # 다음 달 입고분

    unmet_total = np.zeros(n_series)
    demand_total = np.zeros(n_series)
    on_hand_sum = np.zeros(n_series)
    stockout_months = np.zeros(n_series)

    for t in range(start, n_months):
        arriving, pipeline = pipeline, np.zeros(n_series)
        on_hand = on_hand + arriving

        position = on_hand + pipeline
        pipeline = np.clip(target[:, t] - position, 0, None)

        demand = actual[:, t]
        served = np.minimum(on_hand, demand)
        unmet = demand - served
        on_hand = on_hand - served

        unmet_total += unmet
        demand_total += demand
        on_hand_sum += on_hand
        stockout_months += (unmet > 0).astype(float)

    periods = n_months - start
    positive = demand_total > 0
    return {
        "fill_rate": float(1 - unmet_total[positive].sum() / demand_total[positive].sum()),
        "stockout_month_rate": float(stockout_months.sum() / (n_series * periods)),
        "mean_on_hand": float(on_hand_sum.sum() / (n_series * periods)),
        # 재고회전: 낮을수록 재고가 오래 잠긴다.
        "turns": float(demand_total.sum() / max(on_hand_sum.sum() / periods, 1e-9)),
    }


def _wape(mu_hat: np.ndarray, actual: np.ndarray, start: int) -> float:
    a, p = actual[:, start:], mu_hat[:, start:]
    return float(np.abs(a - p).sum() / max(np.abs(a).sum(), 1e-9) * 100)


def main() -> None:
    pattern, actual, model, months = _load()
    start = 1  # naive/cumavg 가 성립하는 첫 달. 모든 예측기에 동일 적용.
    print(f"계열 {len(actual):,}개 / 월 {months[0]}~{months[-1]}")
    print(f"평가 구간 {months[start]}~{months[-1]} ({actual.shape[1]-start}개월)\n")

    # sigma 는 계열 자기 실측의 표준편차. 평가구간을 쓰므로 낙관 편향이 있으나
    # 네 예측기에 동일 적용이라 순위 비교는 유효하다.
    sigma = actual.std(axis=1, ddof=0)

    rows = []
    for name, mu_hat in _forecasts(actual, model).items():
        kpi = _simulate(mu_hat, actual, sigma, start)
        kpi["method"] = name
        kpi["wape"] = _wape(mu_hat, actual, start)
        rows.append(kpi)

    table = pd.DataFrame(rows).set_index("method")
    table["wape_rank"] = table["wape"].rank().astype(int)
    table["fill_rank"] = (-table["fill_rate"]).rank().astype(int)

    print(f"{'예측기':<10}{'WAPE':>9}{'충족률':>10}{'품절월비율':>12}"
          f"{'평균재고':>11}{'회전':>8}{'WAPE순':>8}{'충족순':>8}")
    for name, row in table.iterrows():
        print(f"  {name:<8}{row['wape']:>9.3f}{row['fill_rate']*100:>9.2f}%"
              f"{row['stockout_month_rate']*100:>11.2f}%{row['mean_on_hand']:>11.1f}"
              f"{row['turns']:>8.2f}{int(row['wape_rank']):>8}{int(row['fill_rank']):>8}")

    ranks_agree = bool((table["wape_rank"] == table["fill_rank"]).all())
    print(f"\n  WAPE 순위 == 충족률 순위 ? {'예' if ranks_agree else '아니오'}")

    # 수요패턴별로도 본다. erratic 이 오차의 17.9% 인데 재고 성과에서도
    # 그런지는 별개 문제다.
    print(f"\n=== 수요패턴별 (model 기준) ===")
    per_pattern = {}
    for label in pd.Series(pattern).dropna().unique():
        mask = (pattern == label).to_numpy()
        if mask.sum() < 500:
            continue
        kpi = _simulate(model[mask], actual[mask], sigma[mask], start)
        kpi["wape"] = _wape(model[mask], actual[mask], start)
        kpi["n"] = int(mask.sum())
        per_pattern[label] = kpi
        print(f"  {label:<14}{kpi['n']:>9,}  WAPE {kpi['wape']:>7.2f}  "
              f"충족률 {kpi['fill_rate']*100:>6.2f}%  품절월 {kpi['stockout_month_rate']*100:>6.2f}%")

    OUT_PATH.write_text(json.dumps({
        "eval_months": months[start:],
        "series": int(len(actual)),
        "policy": {"review_months": REVIEW_MONTHS, "lead_months": LEAD_MONTHS,
                   "z": Z_SERVICE, "unmet": "lost_sales"},
        "methods": table.reset_index().to_dict("records"),
        "wape_rank_equals_fill_rank": ranks_agree,
        "by_pattern": per_pattern,
        "caveats": [
            "sigma 를 평가구간 실측으로 추정 — 절대 수준은 낙관 편향, 순위 비교는 유효",
            "보유비/품절비 파라미터 미상 — 단일 비용으로 합치지 않음",
            "손실판매 가정 (이월 아님)",
            "초기재고를 첫 목표재고로 둠",
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {OUT_PATH}")


if __name__ == "__main__":
    main()
