"""리드타임 정책(median vs p25)과 극단값 상한의 효과를 정량화한다 (ai#39).

`SS = z × sigma × √L`, `ROP = mu × L + SS` 구조상 L 이 안전재고와 재주문점을 전부
좌우한다. 현재 적용값에는 최댓값 547.5일이 남아 있어 안전재고를 비정상적으로 팽창시킨다.

## 표본 정의 (backend/scripts/fix_inventory_stats.py 와 동일)

품절 상태에서 입고가 일어난 건을 잡아 **품절 지속일수**를 리드타임 표본으로 쓴다.

    표본 조건 : 입고량 > 0  AND  이전최종재고량 == 0
    lag       : 재고마감일 − 같은 (물품 × 기관) 직전 거래일
    유효 범위 : 0 < lag <= 365

이 값은 [발주지연 + 순수 리드타임] 의 합이므로 **산출값은 상한**이다. 순수 공급 리드타임은
P10~P25 쪽에 가깝다 — 이것이 p25 정책을 검토하는 이유다.

## 산출

품목별 median 과 p25 를 각각 뽑고, DB `inventory` 의 mu/sigma/z 를 그대로 써서
정책별 SS/ROP 총량을 재계산해 비교한다. DB 는 읽기만 한다.

⚠️ 원장은 2018~19 만 로컬에 있다. DB 의 적용값은 2024~25 로 산출된 것이라 기간이 다르다.
2024~25 원장을 확보하면 `SSIS_STOCK_GLOB` 만 바꿔 같은 스크립트로 재산출할 것.

실행:
    DATABASE_URL=... SSIS_STOCK_GLOB='~/Downloads/SSIS_20260728/stock_*.DAT' \
    python3 src/loading/compute_lead_time_quantile_policy.py
"""
import glob
import os

import numpy as np
import pandas as pd
import psycopg

STOCK_GLOB = os.path.expanduser(
    os.environ.get("SSIS_STOCK_GLOB", "~/Downloads/SSIS_20260728/stock_*.DAT")
)
CAPS = [int(c) for c in os.environ.get("CAPS", "120,60").split(",")]
COLS = ["물품코드", "보건기관코드_en", "재고마감일", "입고량", "이전최종재고량"]


def load_ledger():
    frames = []
    for path in sorted(glob.glob(STOCK_GLOB)):
        df = pd.read_csv(
            path, sep="|", quotechar='"', usecols=COLS, dtype=str,
            engine="python", on_bad_lines="skip",
        )
        frames.append(df)
        print(f"  {os.path.basename(path)}: {len(df):,}행")
    df = pd.concat(frames, ignore_index=True)
    df["재고마감일"] = pd.to_datetime(df["재고마감일"], format="%Y%m%d", errors="coerce")
    for c in ("입고량", "이전최종재고량"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    return df.dropna(subset=["재고마감일"])


def lead_time_samples(df):
    """품절 상태 입고의 품절 지속일수(lag) 표본."""
    d = df.sort_values(["물품코드", "보건기관코드_en", "재고마감일"])
    key = d["물품코드"].astype(str) + "|" + d["보건기관코드_en"].astype(str)
    d = d.assign(k=pd.factorize(key)[0])
    d["prev_date"] = d.groupby("k", sort=False)["재고마감일"].shift()
    so = d[(d["입고량"] > 0) & (d["이전최종재고량"] == 0)].copy()
    so["lag"] = (so["재고마감일"] - so["prev_date"]).dt.days
    return so[(so["lag"] > 0) & (so["lag"] <= 365)]


def main():
    print(f"원장 로드: {STOCK_GLOB}")
    df = load_ledger()
    print(f"총 {len(df):,}행")

    so = lead_time_samples(df)
    print(f"\n리드타임 표본 {len(so):,}건 (품절 상태 입고)")
    q = so["lag"].quantile([0.10, 0.25, 0.50, 0.75, 0.90])
    print("  전체 분위수: " + "  ".join(f"P{int(k*100)}={v:.0f}" for k, v in q.items()))
    print(f"  평균 {so['lag'].mean():.2f}  최대 {so['lag'].max():.0f}")

    l_med = so.groupby("물품코드")["lag"].median()
    l_p25 = so.groupby("물품코드")["lag"].quantile(0.25)
    d_med, d_p25 = so["lag"].median(), so["lag"].quantile(0.25)
    print(f"  품목별 산출 {l_med.size:,}종  (기본값 median={d_med:.0f} / p25={d_p25:.0f})")
    print(f"  품목별 median 최대 {l_med.max():.0f}일  120일 초과 {int((l_med > 120).sum()):,}종")
    print(f"  품목별 p25    최대 {l_p25.max():.0f}일  120일 초과 {int((l_p25 > 120).sum()):,}종")

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        inv = pd.read_sql(
            """SELECT standard_code, mu, sigma, z_used, lead_time_used, ss, rop
               FROM inventory WHERE status <> 'EXCLUDED'""",
            conn,
        )
    print(f"\nDB inventory {len(inv):,}행 로드")

    inv["L_med"] = inv["standard_code"].map(l_med).fillna(d_med)
    inv["L_p25"] = inv["standard_code"].map(l_p25).fillna(d_p25)
    matched = inv["standard_code"].isin(l_med.index).mean()
    print(f"품목별 실측값 매칭률 {100 * matched:.1f}%  (미매칭은 전체 기본값 사용)")

    def totals(L):
        ss = inv["z_used"] * inv["sigma"] * np.sqrt(L)
        rop = inv["mu"] * L + ss
        return ss.sum(), rop.sum()

    ss0, rop0 = inv["ss"].sum(), inv["rop"].sum()
    print(f"\n현행(DB 적용값)        SS {ss0:15,.0f}   ROP {rop0:15,.0f}")

    rows = [("2018~19 median", inv["L_med"]), ("2018~19 p25", inv["L_p25"])]
    for c in CAPS:
        rows.append((f"median + {c}일 상한", inv["L_med"].clip(upper=c)))
        rows.append((f"p25 + {c}일 상한", inv["L_p25"].clip(upper=c)))

    print(f"{'정책':<22} {'SS 총합':>15} {'대비':>8} {'ROP 총합':>15} {'대비':>8}")
    for label, L in rows:
        ss1, rop1 = totals(L)
        print(
            f"{label:<22} {ss1:>15,.0f} {100*(ss1/ss0-1):>+7.1f}% "
            f"{rop1:>15,.0f} {100*(rop1/rop0-1):>+7.1f}%"
        )


if __name__ == "__main__":
    main()
