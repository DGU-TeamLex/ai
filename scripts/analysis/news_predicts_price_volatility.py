"""뉴스가 원자재 가격 변동을 **선행**하는가.

## 검정하려는 것

뉴스를 쓰는 목적은 원자재 가격이 흔들릴 가능성을 미리 아는 것이다. 그런데
가격 자체는 이미 갖고 있다(Alpha Vantage, 2015-01~). 따라서 뉴스가 쓸모 있으려면
**가격 시계열이 이미 담고 있는 정보를 넘어서는 무언가** 를 줘야 한다.

    H0: 지난달 변동성을 통제하면, 뉴스 건수는 이번달 변동성을 설명하지 못한다
    H1: 통제 후에도 뉴스 건수가 유의하다  → 뉴스에 선행 정보가 있다

이 통제가 없으면 "뉴스가 많은 달에 변동성이 크다" 는 동어반복이 된다. 변동성이
크면 보도가 늘어나기 때문이다(역인과). 그래서 lag 를 두고 과거 변동성을 통제한다.

## 방법

월별로 집계한다.
  vol_t   = 해당 월 일간 로그수익률의 표준편차 (원자재별)
  news_t  = 해당 월 공급차질 기사 수 (log1p)

  vol_{t+1} ~ vol_t + news_t

news 계수의 부호와 p 값을 본다. 원자재별로 따로 돌리고, 다중검정이므로
Bonferroni 보정을 함께 보고한다(Dunn 1961, JASA 56(293):52-64).

표본이 18개월로 짧다. 이 결과는 확정이 아니라 **방향 확인** 이다.

실행:
    python scripts/analysis/news_predicts_price_volatility.py
"""
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

NEWS_PATH = ROOT / "data" / "raw" / "news" / "gdelt_supply_disruption_news.csv"
PRICE_PATH = ROOT / "data" / "external" / "market" / "commodity_prices.csv"
OUT_PATH = ROOT / "outputs" / "news_price_volatility_test.csv"

# 뉴스 카테고리 material 은 원자재 전반이라 특정 시세와 1:1 이 아니다.
# logistics 는 원자재를 가리지 않으므로 전 원자재 공통 신호로 쓴다.
FACTORS = ("ALUMINUM", "BRENT_CRUDE", "COPPER", "CORN", "COTTON", "SUGAR", "WTI_CRUDE")


def monthly_volatility(prices: pd.DataFrame) -> pd.DataFrame:
    """월별 '가격 움직임' 지표.

    시세마다 관측 주기가 다르다. BRENT/WTI/NATURAL_GAS 는 일별이고
    ALUMINUM/COPPER/CORN/COTTON/SUGAR 는 월별이다. 월별 시계열에 월내
    표준편차를 쓰면 관측이 1개라 NaN 이 되어 **알루미늄이 통째로 빠진다**
    (수요비중 13.37%로 PP 와 대등한 핵심 원자재다).

    그래서 주기에 맞춰 지표를 나눈다.
      일별 시계열 → 월내 로그수익률 표준편차
      월별 시계열 → 전월 대비 로그수익률의 절대값
    둘 다 "그 달에 가격이 얼마나 움직였나" 를 재며, 원자재 간 절대 크기를
    비교하지 않고 각 원자재의 시간 변화만 보므로 혼용해도 된다.
    """
    prices = prices.sort_values("date").copy()
    prices["month"] = prices["date"].dt.to_period("M")

    frames = []
    for factor, block in prices.groupby("market_factor_id"):
        per_month = block.groupby("month").size()
        if per_month.median() >= 5:
            block = block.copy()
            block["log_return"] = np.log(block["price"]).diff()
            measure = (
                block.dropna(subset=["log_return"])
                .groupby("month")["log_return"]
                .agg(["std", "count"])
                .rename(columns={"std": "volatility", "count": "observations"})
            )
            measure = measure[measure["observations"] >= 5]
            measure["measure_kind"] = "intra_month_std"
        else:
            monthly = block.groupby("month")["price"].mean()
            measure = pd.DataFrame(
                {
                    "volatility": np.log(monthly).diff().abs(),
                    "observations": 1,
                    "measure_kind": "abs_month_over_month",
                }
            ).dropna(subset=["volatility"])
        measure["market_factor_id"] = factor
        frames.append(measure.reset_index())
    return pd.concat(frames, ignore_index=True)


def main() -> None:
    from scipy import stats

    news = pd.read_csv(NEWS_PATH)
    news["month"] = pd.to_datetime(news["date"]).dt.to_period("M")
    counts = news.groupby("month").size().rename("news_count")

    prices = pd.read_csv(PRICE_PATH)
    prices["date"] = pd.to_datetime(prices["date"])
    volatility = monthly_volatility(prices)

    window = sorted(counts.index)
    print(f"뉴스 구간 {window[0]} ~ {window[-1]} ({len(window)}개월)")
    print(f"월평균 공급차질 기사 {counts.mean():.0f}건\n")

    rows = []
    for factor in FACTORS:
        series = (
            volatility[volatility["market_factor_id"] == factor]
            .set_index("month")["volatility"]
            .reindex(window)
        )
        frame = pd.DataFrame({"volatility": series, "news": np.log1p(counts)})
        frame["volatility_next"] = frame["volatility"].shift(-1)
        frame = frame.dropna()
        if len(frame) < 8:
            print(f"{factor:<14} 표본 부족 ({len(frame)})")
            continue

        # vol_{t+1} ~ vol_t + news_t 를 부분상관으로 본다.
        # 과거 변동성을 통제한 뒤 뉴스가 남는 설명력을 갖는가.
        residual_next = _residual(frame["volatility_next"], frame["volatility"])
        residual_news = _residual(frame["news"], frame["volatility"])
        correlation, p_value = stats.pearsonr(residual_news, residual_next)

        raw_correlation, raw_p = stats.pearsonr(frame["news"], frame["volatility_next"])
        rows.append(
            {
                "market_factor": factor,
                "n_months": len(frame),
                "raw_corr": round(raw_correlation, 3),
                "raw_p": round(raw_p, 4),
                "partial_corr": round(correlation, 3),
                "partial_p": round(p_value, 4),
                "bonferroni_p": round(min(p_value * len(FACTORS), 1.0), 4),
            }
        )

    result = pd.DataFrame(rows)
    print(
        f"{'원자재':<14}{'n':>4}{'단순상관':>10}{'p':>8}"
        f"{'부분상관':>10}{'p':>8}{'Bonferroni':>12}"
    )
    for row in result.itertuples():
        flag = "  *" if row.bonferroni_p < 0.05 else ""
        print(
            f"{row.market_factor:<14}{row.n_months:>4}{row.raw_corr:>10.3f}{row.raw_p:>8.3f}"
            f"{row.partial_corr:>10.3f}{row.partial_p:>8.3f}{row.bonferroni_p:>12.3f}{flag}"
        )

    result.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")
    significant = result[result["bonferroni_p"] < 0.05]
    print(f"\nBonferroni 보정 후 유의: {len(significant)}/{len(result)}")
    print(
        "부분상관은 지난달 변동성을 통제한 값이다. 단순상관만 크고 부분상관이 "
        "사라지면, 뉴스는 변동성을 뒤따라간 것이지 선행한 것이 아니다."
    )
    print(f"저장: {OUT_PATH}")


def _residual(target: pd.Series, control: pd.Series) -> np.ndarray:
    design = np.column_stack([np.ones(len(control)), control.to_numpy()])
    coefficients, *_ = np.linalg.lstsq(design, target.to_numpy(), rcond=None)
    return target.to_numpy() - design @ coefficients


if __name__ == "__main__":
    main()
