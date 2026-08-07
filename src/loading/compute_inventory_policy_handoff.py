"""AI 재고판정 산출물(부서 포함) → DB inventory 키(부서 없음) handoff 변환.

[배경] 두 쪽의 키 단위가 다르다.
  AI  : (institution_code, department, item_code)  = 416,128 시계열
  DB  : (institution_id,             standard_code) = 409,459 행  ← 부서 축이 없다
DB 의 mu/sigma 는 지금 backend 스톱갭(scripts/fix_inventory_stats.py)이 계산한 값이고,
AI 산출물이 아니다. 그래서 인공 바닥값(mu=0.5 / sigma=0.1)이 그대로 박혀 있다(ai#52).
이 스크립트는 AI 판정 결과를 DB 키로 접어 handoff CSV 로 내보낸다.

[부서 접기] 근사가 아니라 모멘트 합으로 정확히 재계산한다.
  AI 산출물이 시계열별로 모멘트 합을 그대로 들고 있기 때문에 가능하다.

    all_time_normal_outbound     합
    normal_outbound_squared_sum  합
    first_observation_date       최소
    observation_period_days    = (최신관측일 - 최소첫관측일) + 1
    mu                         = 출고합 / 노출일수
    sigma                      = sqrt(제곱합/노출일수 - mu^2)      (0 하한)
    mu_forecast                = 최근출고합 / forecast_window_days

  src/modeling/inventory_status.py 의 _prepare_series_status 와 같은 식이다.

  ⚠️ 단 sigma 는 부서가 2개 이상인 조합에서 **부서간 공분산을 무시**한다.
     같은 날 부서 A·B 수요가 함께 움직이면 실제 결합분산보다 작게 나온다(SS 과소).
     대부분 조합이 부서 1개라 영향이 제한적이므로, 몇 %가 해당되는지 실행 때 출력한다.

[조인키] 기존 handoff(mu_forecast / zero_stock_reason)와 동일 규약.
  standard_code  = 물품코드 원본
  institution_id = 익명 기관코드를 전체 원장 집합 기준 정렬-zip 으로 매핑

  ⚠️ 정렬-zip 은 **부분집합이면 전부 밀려서 오매핑**된다. 기존 스크립트는 이 검사가 없어
     조용히 틀릴 수 있었다. 여기서는 익명코드 개수와 institution_id 개수가 다르면 중단한다.
     AI 산출물은 3,530 기관인데 전체는 3,598 이라 산출물만으로는 만들 수 없다 —
     반드시 전체 원장(LEDGER_PATH) 또는 2열 매핑(INST_CODE_MAP)을 줘야 한다.

실행
  INVENTORY_STATUS=outputs/stock_inventory_status.csv \
  LEDGER_PATH=~/Downloads/물품재고_정규화완료.parquet \
  python3 src/loading/compute_inventory_policy_handoff.py

  또는 2열 매핑이 이미 있으면(권장 — 정렬-zip 을 매번 다시 하지 않아도 된다)
  INST_CODE_MAP=data/mapping/institution_code_map.csv python3 ...
"""
import json
import os
import sys

import numpy as np
import pandas as pd

STATUS_PATH = os.environ.get("INVENTORY_STATUS", "outputs/stock_inventory_status.csv")
POLICY_PATH = os.environ.get("POLICY", "data/mapping/inventory_status_policy.json")
INST_IDS = os.environ.get("INST_IDS", "data/mapping/institution_ids_sorted.csv")
INST_CODE_MAP = os.environ.get("INST_CODE_MAP", "")
LEDGER_PATH = os.path.expanduser(os.environ.get("LEDGER_PATH", ""))
OUT_PATH = os.environ.get("OUT", "data/handoff/inventory_policy.csv")

SERIES_KEYS = ["institution_code", "department", "item_code"]
COLLAPSE_KEYS = ["institution_code", "item_code"]

# AI 산출물에서 반드시 있어야 하는 컬럼. 없으면 추측하지 않고 중단한다.
REQUIRED = [
    *SERIES_KEYS,
    "on_hand",
    "all_time_normal_outbound",
    "normal_outbound_squared_sum",
    "recent_normal_outbound",
    "first_observation_date",
    "observation_period_days",
    "demand_class",
    "zero_stock_reason",
]


def die(msg: str) -> None:
    print(f"[중단] {msg}", file=sys.stderr)
    raise SystemExit(1)


def load_institution_mapping(anon_codes: list[str]) -> dict[str, str]:
    """익명 기관코드 → institution_id.

    2열 매핑이 있으면 그것을 쓴다(정렬 가정 없음 = 안전).
    없으면 전체 원장에서 익명코드 전수를 뽑아 정렬-zip 하되, 개수가 어긋나면 중단한다.
    """
    if INST_CODE_MAP and os.path.exists(INST_CODE_MAP):
        m = pd.read_csv(INST_CODE_MAP)
        need = {"institution_code", "institution_id"}
        if not need.issubset(m.columns):
            die(f"{INST_CODE_MAP} 에 {sorted(need)} 컬럼이 필요하다")
        print(f"기관 매핑: {INST_CODE_MAP} ({len(m):,}건) — 정렬 가정 없음")
        return dict(zip(m["institution_code"].astype(str), m["institution_id"].astype(str)))

    real = pd.read_csv(INST_IDS)["institution_id"].astype(str).tolist()
    if not LEDGER_PATH:
        die(
            "INST_CODE_MAP 도 LEDGER_PATH 도 없다. 산출물의 기관 집합만으로 정렬-zip 하면 "
            f"오매핑된다(산출물 {len(set(anon_codes)):,} vs 전체 {len(real):,})."
        )
    if not os.path.exists(LEDGER_PATH):
        die(f"LEDGER_PATH 가 없다: {LEDGER_PATH}")

    led = pd.read_parquet(LEDGER_PATH, columns=["보건기관코드_en"])
    anon_full = sorted(led["보건기관코드_en"].dropna().astype(str).unique())
    if len(anon_full) != len(real):
        die(
            f"익명 기관코드 {len(anon_full):,}개 ≠ institution_id {len(real):,}개. "
            "정렬-zip 이 전부 밀린다. 전체 원장인지 확인할 것."
        )
    missing = set(anon_codes) - set(anon_full)
    if missing:
        die(f"산출물에 있으나 원장에 없는 기관코드 {len(missing):,}개 — 매핑 근거가 다르다")
    print(f"기관 매핑: 정렬-zip {len(anon_full):,}건 (개수 일치 확인)")
    return dict(zip(anon_full, sorted(real)))


def main() -> None:
    if not os.path.exists(STATUS_PATH):
        die(
            f"AI 산출물이 없다: {STATUS_PATH}\n"
            "        outputs/ 는 .gitignore 대상이라 레포에 없다. AI 파이프라인을 돌리거나 "
            "산출물을 받아서 경로를 INVENTORY_STATUS 로 지정할 것."
        )
    policy = json.load(open(POLICY_PATH, encoding="utf-8"))
    window = int(policy["demand_parameters"]["forecast_window_days"])
    mean_floor = float(policy["demand_parameters"]["mean_daily_usage_floor"])
    stddev_floor = float(policy["demand_parameters"]["daily_demand_stddev_floor"])
    print(f"정책 {policy['version']} · mu 하한 {mean_floor} · sigma 하한 {stddev_floor} · 예측창 {window}일")

    st = pd.read_csv(STATUS_PATH)
    missing = sorted(set(REQUIRED) - set(st.columns))
    if missing:
        die(f"산출물에 필요한 컬럼이 없다: {missing}")
    for k in SERIES_KEYS:
        st[k] = st[k].astype(str).str.strip()
    st["first_observation_date"] = pd.to_datetime(st["first_observation_date"], errors="coerce")
    if st["first_observation_date"].isna().any():
        die("first_observation_date 에 파싱 불가 값이 있다")

    # 최신 관측일을 노출일수로 역산한다(산출물이 그 날짜를 직접 담지 않으므로).
    latest = (st["first_observation_date"] + pd.to_timedelta(st["observation_period_days"] - 1, "D")).max()
    print(f"시계열 {len(st):,} · 최신 관측일 {latest:%Y-%m-%d}")

    # ── 부서 접기 ────────────────────────────────────────────────
    dept = st.groupby(COLLAPSE_KEYS, sort=False)["department"].nunique()
    multi = int((dept > 1).sum())
    g = st.groupby(COLLAPSE_KEYS, sort=False, as_index=False).agg(
        on_hand=("on_hand", "sum"),
        outbound_sum=("all_time_normal_outbound", "sum"),
        squared_sum=("normal_outbound_squared_sum", "sum"),
        recent_sum=("recent_normal_outbound", "sum"),
        first_obs=("first_observation_date", "min"),
        department_count=("department", "nunique"),
        # 접힌 조합의 사유는 '가장 조치가 급한 것'을 남긴다(실결품 > 데이터점검 > 미운영).
        zero_reasons=("zero_stock_reason", lambda s: sorted(set(s.dropna()))),
        demand_classes=("demand_class", lambda s: sorted(set(s.dropna()))),
    )
    print(f"접기: {len(st):,} 시계열 → {len(g):,} 조합 (부서 2개 이상 {multi:,} = {multi/len(g)*100:.2f}%)")

    days = (latest - g["first_obs"]).dt.days + 1
    if (days <= 0).any():
        die("노출일수가 0 이하인 조합이 있다")
    g["observation_period_days"] = days.astype("int64")
    raw_mu = g["outbound_sum"] / days
    var = (g["squared_sum"] / days - raw_mu.pow(2)).clip(lower=0)
    raw_sigma = var.pow(0.5)

    g["mu_is_floored"] = raw_mu.lt(mean_floor)
    g["sigma_is_floored"] = raw_sigma.lt(stddev_floor)
    g["mu"] = raw_mu.clip(lower=mean_floor)
    g["sigma"] = raw_sigma.clip(lower=stddev_floor)
    g["mu_forecast"] = g["recent_sum"] / window

    ZSR_RANK = {"TRUE_STOCKOUT": 0, "DATA_MISSING": 1, "STALE_OR_MISSING_OBSERVATION": 2, "NOT_OPERATED": 3}
    g["zero_stock_reason"] = g["zero_reasons"].map(
        lambda xs: min(xs, key=lambda v: ZSR_RANK.get(v, 9)) if xs else None
    )
    # 접힌 조합이 하나라도 활동 중이면 DORMANT 로 보지 않는다(발주 억제는 보수적으로).
    g["demand_class"] = g["demand_classes"].map(
        lambda xs: "DORMANT" if xs and set(xs) == {"DORMANT"} else (xs[0] if len(xs) == 1 else "ACTIVE")
    )

    # 발주 억제 사유 — AI 정책(2026-07-29_05) 기준.
    #   DORMANT / NOT_OPERATED → 권고량 0
    #   DATA_MISSING / stale   → 권고량 null 이 정책이지만 DB 가 NOT NULL 이라 0 으로 둔다(아래 보고).
    g["order_suppress_reason"] = None
    g.loc[g["zero_stock_reason"].eq("DATA_MISSING"), "order_suppress_reason"] = "DATA_MISSING"
    g.loc[g["zero_stock_reason"].eq("STALE_OR_MISSING_OBSERVATION"), "order_suppress_reason"] = "STALE"
    g.loc[g["zero_stock_reason"].eq("NOT_OPERATED"), "order_suppress_reason"] = "NOT_OPERATED"
    g.loc[g["demand_class"].eq("DORMANT"), "order_suppress_reason"] = "DORMANT"

    mapping = load_institution_mapping(g["institution_code"].tolist())
    g["institution_id"] = g["institution_code"].map(mapping)
    unmapped = int(g["institution_id"].isna().sum())
    if unmapped:
        print(f"⚠ 매핑 실패 {unmapped:,}행 — 제외한다")
    g = g.dropna(subset=["institution_id"])
    g = g.rename(columns={"item_code": "standard_code"})

    out = g[[
        "institution_id", "standard_code", "on_hand", "mu", "sigma", "mu_forecast",
        "demand_class", "zero_stock_reason", "order_suppress_reason",
        "department_count", "mu_is_floored", "sigma_is_floored", "observation_period_days",
    ]]
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    out.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    print(f"\n저장: {OUT_PATH} ({len(out):,}행)")
    print(f"  mu = 0            {int(out['mu'].eq(0).sum()):>8,}")
    print(f"  mu <= 0.5         {int(out['mu'].le(0.5).sum()):>8,}   ← DB 현재값 296,537 과 비교")
    print(f"  mu_is_floored     {int(out['mu_is_floored'].sum()):>8,}")
    print(f"  sigma_is_floored  {int(out['sigma_is_floored'].sum()):>8,}")
    sup = out["order_suppress_reason"].value_counts(dropna=True)
    print("  발주 억제 사유:")
    for k, v in sup.items():
        print(f"    {k:<14} {v:>8,}")
    nullish = int(out["order_suppress_reason"].isin(["DATA_MISSING", "STALE"]).sum())
    if nullish:
        print(
            f"  ⚠ 정책상 권고량 null 이어야 하는 {nullish:,}행을 0 으로 내보낸다 — "
            "inventory.order_recommendation 이 NOT NULL 이다(backend DDL 변경 필요)"
        )


if __name__ == "__main__":
    main()
