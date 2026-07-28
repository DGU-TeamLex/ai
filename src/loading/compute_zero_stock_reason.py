"""재고 0 품목의 '원인' 판별 — 미운영 / 데이터누락 / 실제결품 (ai#32).

[문제] status=CRITICAL(재고 0) 128,250건이 전부 '긴급 부족'으로 표시되나, 실제로는
  운영하지 않는 품목·재고기록 누락이 섞여 있어 실무 우선순위를 흐린다(7/23 기관 리뷰회의 지적).

[판별 규칙] 원장 pair(기관×물품) 단위, 최종 마감재고 ≤ 0 인 건에 한해 부여.
  ①NOT_OPERATED (미운영)   : 전 기간 정상출고량 합 = 0 → 재고만 0이고 쓴 적이 없음
  ②DATA_MISSING (데이터누락): 출고 이력은 있고 **정합성 위반**이 존재
       위반 = 정상출고량 > (이전최종재고량 + 입고량)
       → 있지도 않은 재고를 출고 = 물리적으로 불가 = 재고 기재 누락의 직접 증거
  ③TRUE_STOCKOUT (실제결품): 출고 이력 있고 정합성도 정상 → 소진 후 미보충

[실측 2026-07-23] 최종재고≤0 pair 128,281 중
  ①미운영 60,876(47.5%) ②데이터누락 4,000(3.1%) ③실제결품 63,405(49.4%).
  최근3개월 실수요가 있는 건 ②892 + ③10,377 = 11,269 → 이것이 실제 발주 검토 대상.

[주의] 최종재고는 반드시 **재고마감일 정렬 후** last 를 취해야 한다(정렬 없이 last 를 쓰면
  128,281이 아니라 56,559로 나와 DB CRITICAL 과 어긋난다 — 실제로 한 번 틀렸던 지점).

[조인키] mu_forecast 와 동일 — 전체 원장 anon 집합 정렬-zip 으로 institution_id 매핑,
  standard_code = 물품코드.
"""
import os

import pandas as pd

LEDGER_PATH = os.environ.get("LEDGER_PATH", os.path.expanduser("~/Downloads/물품재고_정규화완료.parquet"))
INST_MAP = os.environ.get("INST_MAP", "data/mapping/institution_ids_sorted.csv")
LAST3 = ["2025-10", "2025-11", "2025-12"]

led = pd.read_parquet(LEDGER_PATH, columns=[
    "보건기관코드_en", "물품코드", "재고마감일", "정상출고량", "마감재고량", "이전최종재고량", "입고량"])

# 정합성 위반: 있지도 않은 재고를 출고한 행
avail = led["이전최종재고량"].fillna(0) + led["입고량"].fillna(0)
led["violation"] = led["정상출고량"].fillna(0) > avail

led = led.sort_values("재고마감일")           # ★ 정렬 필수 (last 의 의미가 달라짐)
key = ["보건기관코드_en", "물품코드"]
g = led.groupby(key, observed=True).agg(
    ship_sum=("정상출고량", "sum"), violations=("violation", "sum"))
g["last_stock"] = led.groupby(key, observed=True)["마감재고량"].last()

led["ym"] = led["재고마감일"].dt.to_period("M").astype(str)
g["recent3m"] = (led[led["ym"].isin(LAST3)].groupby(key, observed=True)["정상출고량"].sum()
                 .reindex(g.index).fillna(0))

z = g[g["last_stock"].fillna(0) <= 0].copy()
z["zero_stock_reason"] = "TRUE_STOCKOUT"
z.loc[z["ship_sum"] <= 0, "zero_stock_reason"] = "NOT_OPERATED"
z.loc[(z["ship_sum"] > 0) & (z["violations"] > 0), "zero_stock_reason"] = "DATA_MISSING"
z = z.reset_index()

# 익명 기관코드 → 실 기관 ID (전체 원장 집합 기준 정렬 zip — 부분집합이면 오매핑)
anon_full = sorted(led["보건기관코드_en"].dropna().unique())
real = pd.read_csv(INST_MAP)["institution_id"].tolist()
mapping = dict(zip(anon_full, sorted(real)))
z["institution_id"] = z["보건기관코드_en"].map(mapping)
z = z.dropna(subset=["institution_id"]).rename(columns={"물품코드": "standard_code"})

out = z[["institution_id", "standard_code", "zero_stock_reason", "recent3m"]]
os.makedirs("data/handoff", exist_ok=True)
out.to_csv("data/handoff/zero_stock_reason.csv", index=False, encoding="utf-8-sig")
print(f"저장: data/handoff/zero_stock_reason.csv ({len(out):,}행)")
for r, n in out["zero_stock_reason"].value_counts().items():
    act = int(((out["zero_stock_reason"] == r) & (out["recent3m"] > 0)).sum())
    print(f"  {r:<15} {n:>8,} ({n / len(out) * 100:4.1f}%) | 최근3개월 실수요>0 {act:,}")
