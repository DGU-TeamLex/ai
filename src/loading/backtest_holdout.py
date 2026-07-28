"""홀드아웃 백테스트 — 소진예측 수요율 예측기들을 실측 대비 평가(2026-07-23).

목적: "우리가 서빙하는 수요율이 실측 대비 얼마나 맞나"를 원장으로 직접 검증.
설계: 학습 2024-01~2025-09 / **미검증 3개월 2025-10~12를 홀드아웃**으로 떼어 예측 vs 실측.
지표: WAPE(=Σ|실측-예측|/Σ|실측|). 수요유형(Syntetos-Boylan)별로도 분해.

핵심 결과(실측):
  roll3 42.6% < roll6 44.5% < prev_month 47.4% < cumulative 48.3%
  < static_mu 49.9%(=DB mu_corrected 유래, 현재 소진예측에 쓰던 값=최악) < prev_year 67.4%
  → 소진예측 수요를 static_mu 대신 roll3 로 교체(compute_mu_forecast.py).
  참고: AI팀 LightGBM 독립테스트 37.6%(모듈A 평가문서) = 최종 교체 목표(ai#28).

입력: 원장 parquet(정상출고량=사용량, 이 PC 로컬 ~/Downloads). gitignore라 레포엔 없음.
"""
import os

import numpy as np
import pandas as pd

LEDGER_PATH = os.environ.get("LEDGER_PATH", os.path.expanduser("~/Downloads/물품재고_정규화완료.parquet"))
MONTHS = [f"2024-{m:02d}" for m in range(1, 13)] + [f"2025-{m:02d}" for m in range(1, 13)]
MI = {m: i for i, m in enumerate(MONTHS)}
TEST = ["2025-10", "2025-11", "2025-12"]
TRAIN_END = MI["2025-09"]  # idx 20


def wape(a, p):
    a, p = np.asarray(a), np.asarray(p)
    s = np.abs(a).sum()
    return np.nan if s == 0 else np.abs(a - p).sum() / s * 100


def biaspct(a, p):
    a, p = np.asarray(a), np.asarray(p)
    s = np.abs(a).sum()
    return np.nan if s == 0 else (p.sum() - a.sum()) / s * 100


def classify(hist):
    """Syntetos-Boylan: ADI(평균수요간격)·CV²(비영수요 변동)로 4분류."""
    nz = hist[hist > 0]
    if len(nz) == 0:
        return "no_demand"
    adi = len(hist) / len(nz)
    cv2 = (nz.std() / nz.mean()) ** 2 if nz.mean() > 0 else 0
    if adi < 1.32 and cv2 < 0.49:
        return "smooth"
    if adi >= 1.32 and cv2 < 0.49:
        return "intermittent"
    if adi < 1.32 and cv2 >= 0.49:
        return "erratic"
    return "lumpy"


led = pd.read_parquet(LEDGER_PATH, columns=["보건기관코드_en", "표준품목ID", "재고마감일", "정상출고량"])
led["ym"] = led["재고마감일"].dt.to_period("M").astype(str)
led["표준품목ID"] = led["표준품목ID"].fillna("__NA__")
panel = led.groupby(["보건기관코드_en", "표준품목ID", "ym"], observed=True, as_index=False)["정상출고량"].sum()
panel = panel.rename(columns={"정상출고량": "demand"})
panel.loc[panel["demand"] < 0, "demand"] = 0
panel["ci"] = panel["ym"].map(MI)
panel["sid"] = panel.groupby(["보건기관코드_en", "표준품목ID"]).ngroup()

S = int(panel["sid"].max()) + 1
M = np.full((S, 24), np.nan)
M[panel["sid"].to_numpy(), panel["ci"].to_numpy()] = panel["demand"].to_numpy()
first = np.array([np.argmax(~np.isnan(M[i])) if np.any(~np.isnan(M[i])) else 99 for i in range(S)])
Mf = np.nan_to_num(M, nan=0.0)
print(f"시계열 {S:,} × 24개월")

preds = {k: [] for k in ["static_mu", "roll3", "roll6", "prev_month", "cumulative", "prev_year"]}
actuals, seg_of, seg_cache = [], [], {}
for t in [MI[x] for x in TEST]:
    idx = np.where(~np.isnan(M[:, t]))[0]
    a = M[idx, t]
    for i in idx:
        if i not in seg_cache:
            fo = first[i]
            hist = Mf[i, fo:TRAIN_END + 1] if fo <= TRAIN_END else np.array([])
            seg_cache[i] = classify(hist) if len(hist) else "cold_start"
    seg = np.array([seg_cache[i] for i in idx])
    denom = np.clip(TRAIN_END - first[idx] + 1, 1, None).astype(float)
    smu = Mf[idx, 0:TRAIN_END + 1].sum(axis=1) / denom
    smu[first[idx] > TRAIN_END] = 0
    preds["static_mu"] += list(smu)
    preds["roll3"] += list(Mf[idx, t - 3:t].mean(axis=1))
    preds["roll6"] += list(Mf[idx, t - 6:t].mean(axis=1))
    preds["prev_month"] += list(Mf[idx, t - 1])
    preds["cumulative"] += list([Mf[i, first[i]:t].mean() if first[i] < t else 0 for i in idx])
    preds["prev_year"] += list(Mf[idx, t - 12] if t - 12 >= 0 else np.zeros(len(idx)))
    actuals += list(a)
    seg_of += list(seg)

actuals = np.array(actuals)
seg_of = np.array(seg_of)
print(f"평가행 {len(actuals):,} / 실측수요 {actuals.sum():,.0f}\n")
print("=== 전체 WAPE (미검증 3개월) ===")
for k, v in preds.items():
    v = np.array(v)
    print(f"  {k:<12} WAPE {wape(actuals, v):5.1f}%   BIAS {biaspct(actuals, v):+5.1f}%")

print("\n=== 세그먼트별 WAPE ===")
print(f"  {'세그먼트':<14}{'건수':>9}{'실측합':>12}  static_mu  roll3")
for sg in ["smooth", "intermittent", "erratic", "lumpy", "cold_start", "no_demand"]:
    m = seg_of == sg
    if m.sum() == 0:
        continue
    a = actuals[m]
    print(f"  {sg:<14}{m.sum():>9,}{a.sum():>12,.0f}   "
          f"{wape(a, np.array(preds['static_mu'])[m]):6.1f}%  {wape(a, np.array(preds['roll3'])[m]):5.1f}%")
