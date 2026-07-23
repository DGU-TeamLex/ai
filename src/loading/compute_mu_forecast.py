"""mu_forecast 산출 — 직전 3개월 일수요율(roll3) 예측기.

[근거] 팀장 홀드아웃 백테스트(2026-07-23): 미검증 3개월(2025-10~12) 실측 대비
  WAPE — roll3 42.6% < ... < static_mu(=DB mu_corrected) 49.9%. 즉 현재 소진예측에
  쓰는 정적평균(static_mu)이 최악. roll3가 7%p 정확 → 소진예측 수요를 이걸로 교체.
  (최종적으로는 AI팀 LightGBM 37.6%로 교체 예정 — ai#28 handoff. 이 컬럼에 값만 갈아끼움.)

[정의] mu_forecast = (직전 3개월 정상출고량 합) / 92일 = 현재 일수요율(daily burn rate).
  최근 무활동 pair는 산출 대상 아님(handoff에 미포함) → 프론트에서 muCorrected 폴백.

[조인키] 반드시 '전체 원장' 보건기관코드_en 집합으로 정렬-zip 매핑해야 inventory 와
  정합(부분집합으로 zip 하면 정렬 위치가 밀려 오매핑 — 실측: 부분집합 14.5% vs 전체 100%).
  standard_code = 물품코드(원본코드, DB inventory.standard_code 와 동일 포맷).
"""
import os

import pandas as pd

LEDGER_PATH = os.environ.get("LEDGER_PATH", os.path.expanduser("~/Downloads/물품재고_정규화완료.parquet"))
INST_MAP = os.environ.get("INST_MAP", "data/mapping/institution_ids_sorted.csv")
LAST3 = ["2025-10", "2025-11", "2025-12"]
DAYS = 92  # 31+30+31

led = pd.read_parquet(LEDGER_PATH, columns=["보건기관코드_en", "물품코드", "재고마감일", "정상출고량"])
led["ym"] = led["재고마감일"].dt.to_period("M").astype(str)

# ★ 전체 원장 anon 집합으로 매핑(원본 inventory 적재와 동일 정렬 위치)
anon_full = sorted(led["보건기관코드_en"].dropna().unique())
real = pd.read_csv(INST_MAP)["institution_id"].tolist()
if len(anon_full) != len(real):
    print(f"[경고] anon {len(anon_full)} vs real {len(real)} — inventory 와 동일한 정렬 zip 로 진행"
          f"(초과분은 원장 미등장 기관이라 무해).")
mapping = dict(zip(anon_full, sorted(real)))

g = (led[led["ym"].isin(LAST3)]
     .groupby(["보건기관코드_en", "물품코드"], observed=True, as_index=False)["정상출고량"].sum())
g["mu_forecast"] = g["정상출고량"].clip(lower=0) / DAYS
g["institution_id"] = g["보건기관코드_en"].map(mapping)
g = g.dropna(subset=["institution_id"]).rename(columns={"물품코드": "standard_code"})

out = g[["institution_id", "standard_code", "mu_forecast"]]
os.makedirs("data/handoff", exist_ok=True)
out.to_csv("data/handoff/mu_forecast.csv", index=False, encoding="utf-8-sig")
print(f"저장: data/handoff/mu_forecast.csv ({len(out):,}행)")
print(f"일수요율 평균 {out['mu_forecast'].mean():.3f} / 중앙 {out['mu_forecast'].median():.3f} "
      f"/ >0 {(out['mu_forecast'] > 0).mean() * 100:.0f}%")
