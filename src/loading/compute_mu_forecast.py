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
# 원장 판본에 따라 `재고마감일` 이 datetime 이기도 하고 "20241220" 문자열이기도
# 하다. 문자열이면 .dt 접근이 터진다.
#
# `format="mixed"` 를 쓰면 안 된다. "20241220" 을 epoch 정수로 읽어 전부
# 1970-01 이 된다(실측: 전 구간이 1970-01 로 뭉개짐). 8자리 숫자면 %Y%m%d 로
# 명시해 파싱한다.
if not pd.api.types.is_datetime64_any_dtype(led["재고마감일"]):
    raw_dates = led["재고마감일"].astype(str).str.strip()
    if raw_dates.str.fullmatch(r"\d{8}").mean() > 0.9:
        led["재고마감일"] = pd.to_datetime(raw_dates, format="%Y%m%d", errors="coerce")
    else:
        led["재고마감일"] = pd.to_datetime(raw_dates, errors="coerce")
led = led.dropna(subset=["재고마감일"])
led["ym"] = led["재고마감일"].dt.to_period("M").astype(str)

# 검증된 2열 매핑이 있으면 그것을 쓴다. 없을 때만 정렬-zip 으로 내려간다.
#
# 정렬-zip 은 두 집합의 정렬 순서가 정확히 같을 때만 맞는데, 원장 3,530 vs
# institutions 3,598 로 68개 차이가 나서 위치가 밀릴 위험이 있었다. 그 매핑을
# DB on_hand 와 대조해 검증했고(재고 일치 99.52%, 한 칸 민 대조군 16.24%),
# 결과를 data/mapping/institution_id_mapping.csv 에 고정했다.
#
# 파일이 있으면 그것을 쓰는 편이 낫다. 검증 근거가 파일에 붙어 있고, 원장
# 집합이 바뀌어도 매핑이 조용히 달라지지 않는다.
VERIFIED_MAP = os.environ.get(
    "INST_CODE_MAP", "data/mapping/institution_id_mapping.csv"
)
anon_full = sorted(led["보건기관코드_en"].dropna().unique())
mapping = None
if os.path.exists(VERIFIED_MAP):
    verified = pd.read_csv(VERIFIED_MAP, dtype=str)
    if {"anon_institution_code", "institution_id"}.issubset(verified.columns) and len(verified):
        mapping = dict(
            zip(verified["anon_institution_code"], verified["institution_id"])
        )
        covered = sum(1 for code in anon_full if code in mapping)
        print(f"[매핑] 검증본 사용: {VERIFIED_MAP} ({len(mapping):,}행), "
              f"원장 기관 커버리지 {covered/len(anon_full):.2%}")
if mapping is None:
    real = pd.read_csv(INST_MAP)["institution_id"].tolist()
    if len(anon_full) != len(real):
        print(f"[경고] 검증 매핑이 없다. anon {len(anon_full)} vs real {len(real)} 로 "
              f"정렬 zip 을 쓴다 — 위치가 밀릴 수 있다.")
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
