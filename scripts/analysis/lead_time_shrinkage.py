"""품목별 '최적' 리드타임 추정량 선택 (ai#39 후속).

앞선 lead_time_optimal.py 에서 품목별 median 의 기간 간 상관이 0.079 로 나왔다.
= 2018-19 의 품목별 median 은 2024-25 를 거의 예측하지 못한다.
따라서 "품목별 최적 리드타임" 을 원시 품목 median 으로 쓰면 안 된다.

여기서는 추정량 후보를 **기간 간 out-of-sample** 로 비교한다.
  학습: 2018-19 원장에서 추정
  평가: 2024-25 의 실제 품목별 median 을 맞추는 MAE / 정책 관점 손실

후보
  (a) 현행 fallback 15일 고정
  (b) 전 품목 공통 = 2018-19 전체 median
  (c) 품목별 raw median (2018-19)
  (d) Buhlmann-Straub 신뢰도 축소: Z = n/(n+k), L_i = Z*median_i + (1-Z)*global
      k = 개체내분산 / 개체간분산  (Buhlmann & Straub 1970, ASTIN Bulletin 4:199-207;
      재고 수요/리드타임 추정에 대한 적용은 Kalchschmidt et al. 2006 참고)

정책 관점 손실도 같이 본다. 리드타임을 과소추정하면 품절, 과대추정하면 과재고이며
비대칭이다. 서비스수준 alpha 에 대해 pinball loss 로 평가한다
  (Koenker & Bassett 1978, Econometrica 46:33-50).
"""
import json
import pathlib

import numpy as np
import pandas as pd

ROOT = str(pathlib.Path(__file__).resolve().parents[2])
MIN_N = 5
SERVICE_ALPHA = 0.90  # 품절 비용이 과재고 비용보다 크다고 보는 표준 가정


def pinball(actual: np.ndarray, pred: np.ndarray, alpha: float) -> float:
    diff = actual - pred
    return float(np.mean(np.maximum(alpha * diff, (alpha - 1) * diff)))


def main() -> None:
    his = pd.read_csv(rf"{ROOT}\outputs\lead_time_by_item_2018-19.csv", index_col=0)
    cur = pd.read_csv(rf"{ROOT}\outputs\lead_time_by_item_2024-25.csv", index_col=0)

    common = his.index.intersection(cur.index)
    df = pd.DataFrame(
        {
            "his_median": his.loc[common, "median"],
            "his_n": his.loc[common, "n"],
            "cur_median": cur.loc[common, "median"],
            "cur_n": cur.loc[common, "n"],
        }
    )
    df = df[df["cur_n"] >= MIN_N]
    print(f"공통 품목 {len(df):,}종 (평가기간 표본 {MIN_N}건 이상)")
    print(f"  학습기간 표본 {MIN_N}건 이상인 품목: {(df['his_n'] >= MIN_N).sum():,}종")

    # --- 신뢰도 계수 k 추정 (2018-19 만 사용) -----------------------------
    train = his[his["n"] >= 2].copy()
    global_mean = float(train["median"].mean())
    between_var = float(train["median"].var(ddof=1))
    # 개체내 분산은 품목별 median 의 표준오차로 근사: Var(median) ~ within/n
    # within 을 직접 얻으려면 원장 재스캔이 필요하므로, IQR 기반 근사치를 쓴다.
    within_proxy = float(((train["median"] - train["p25"]) ** 2).mean()) * 2.0
    k = within_proxy / between_var if between_var > 0 else np.inf
    print(f"\n신뢰도 계수 k = within/between = {within_proxy:.1f}/{between_var:.1f} = {k:.2f}")
    print(f"  → 표본 n건일 때 품목 고유값 반영 비중 Z = n/(n+k)")
    for n in (1, 5, 10, 30, 100):
        print(f"     n={n:>4}: Z={n / (n + k):.3f}")

    # --- 후보 추정량 -------------------------------------------------------
    his_global_median = float(his["median"].median())
    z = df["his_n"] / (df["his_n"] + k)
    candidates = {
        "(a) 현행 15일 고정": np.full(len(df), 15.0),
        "(b) 전품목 공통(2018-19 median)": np.full(len(df), his_global_median),
        "(c) 품목별 raw median(2018-19)": df["his_median"].to_numpy(float),
        "(d) 신뢰도 축소(Buhlmann-Straub)": (
            z * df["his_median"] + (1 - z) * global_mean
        ).to_numpy(float),
    }
    actual = df["cur_median"].to_numpy(float)

    print(f"\n=== 2024-25 실제 품목별 median 을 맞추는 성능 ===")
    print(f"  (전품목 공통값 = {his_global_median:.1f}일, 전체평균 = {global_mean:.1f}일)")
    print(f"\n  {'추정량':<34}{'MAE':>9}{'중앙절대오차':>14}{'과소비율':>10}{'pinball@0.9':>13}")
    results = {}
    for name, pred in candidates.items():
        mae = float(np.mean(np.abs(actual - pred)))
        medae = float(np.median(np.abs(actual - pred)))
        under = float(np.mean(pred < actual))
        pb = pinball(actual, pred, SERVICE_ALPHA)
        results[name] = {
            "MAE": round(mae, 2),
            "MedAE": round(medae, 2),
            "under_rate": round(under, 3),
            "pinball_0.9": round(pb, 2),
        }
        print(f"  {name:<34}{mae:>9.2f}{medae:>14.2f}{under:>10.1%}{pb:>13.2f}")

    # --- 서비스수준 분위수 후보 -------------------------------------------
    print(f"\n=== 전품목 공통값을 분위수로 바꾸면 (2018-19에서 산출) ===")
    print(f"  {'분위수':<10}{'값(일)':>9}{'MAE':>9}{'과소비율':>10}{'pinball@0.9':>13}")
    quantile_rows = {}
    for q in (0.25, 0.50, 0.75, 0.90, 0.95):
        value = float(his["median"].quantile(q))
        pred = np.full(len(df), value)
        mae = float(np.mean(np.abs(actual - pred)))
        under = float(np.mean(pred < actual))
        pb = pinball(actual, pred, SERVICE_ALPHA)
        quantile_rows[f"q{int(q * 100)}"] = {
            "days": round(value, 1),
            "MAE": round(mae, 2),
            "under_rate": round(under, 3),
            "pinball_0.9": round(pb, 2),
        }
        print(f"  q{int(q * 100):<9}{value:>9.1f}{mae:>9.2f}{under:>10.1%}{pb:>13.2f}")

    best_pb = min(quantile_rows.items(), key=lambda kv: kv[1]["pinball_0.9"])
    print(f"\n  pinball@{SERVICE_ALPHA} 최소 = {best_pb[0]} ({best_pb[1]['days']}일)")

    out = {
        "common_items": int(len(df)),
        "credibility_k": round(float(k), 3),
        "global_median_1819": round(his_global_median, 1),
        "estimators": results,
        "global_quantiles": quantile_rows,
        "recommended_global_days": best_pb[1]["days"],
        "service_alpha": SERVICE_ALPHA,
    }
    path = rf"{ROOT}\outputs\lead_time_estimator_selection.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\nsaved:", path)


if __name__ == "__main__":
    main()
