"""국가 가중치를 관세청 실측 수입 비중으로 생성 (ai#20).

## 왜 필요한가

`data/mapping/country_weight.csv` 에 Korea/Malaysia/Global/Unknown 4개만 있고,
GDELT 수집분은 전량 `country="Unknown"` 이라 **모든 기사가 0.5 를 받는다.**
상수는 점수를 깎기만 할 뿐 기사 간 구분에 기여하지 않는다.

우리 조달과 무관한 나라의 사건이 같은 무게로 들어오는 것도 문제다. 실측하면
미국 사건이 146건으로 가장 많은데 실제 수입 비중은 0.25% 다.

## 근거

관세청 수입액(USD)은 조달 의존도의 직접 측정치다. 그 나라에서 실제로 얼마나
사오는지가 그 나라 사건의 중요도다. 임의 가정이 아니다.

가중치는 **최대 비중을 1.0 으로 정규화** 한다. 비중 자체를 쓰면 바레인 0.497 이
최대가 되어 전체 점수가 다시 눌린다. 순서와 상대 크기는 보존된다.

Unknown 은 0.3 으로 둔다. 국가를 특정하지 못한 기사를 0 으로 만들면 정보를
버리는 것이고, 0.5 로 두면 실제 조달국(오만 0.08)보다 높아진다.

실행:
    python scripts/analysis/build_country_weight.py
"""
import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

WINDOW = ("2024-01", "2025-12")
MIN_SHARE = 0.001
UNKNOWN_WEIGHT = 0.3
OUT_PATH = ROOT / "data" / "mapping" / "country_weight.csv"

# 기사 제목에서 국가를 찾을 때 쓸 표기. 코드는 관세청 country_code 와 맞춘다.
COUNTRY_ALIASES = {
    "BH": ["Bahrain"],
    "CN": ["China", "Chinese"],
    "AE": ["UAE", "Emirates", "Dubai", "Abu Dhabi"],
    "OM": ["Oman"],
    "QA": ["Qatar"],
    "SG": ["Singapore"],
    "JP": ["Japan", "Japanese"],
    "TH": ["Thailand", "Thai"],
    "DE": ["Germany", "German"],
    "VN": ["Vietnam"],
    "US": ["United States", "U.S.", "American"],
    "NL": ["Netherlands", "Dutch"],
    "MY": ["Malaysia"],
    "IN": ["India", "Indian"],
    "KR": ["Korea", "Korean"],
}


def main() -> None:
    trade = pd.read_csv(
        ROOT / "data" / "external" / "trade" / "kcs_trade_country_monthly.csv",
        low_memory=False,
    )
    trade["STD_YYYYMM"] = trade["STD_YYYYMM"].astype(str)
    trade = trade[trade["STD_YYYYMM"].between(*WINDOW)]

    grouped = (
        trade.groupby(["country_code", "country_name"])["import_value_usd"]
        .sum()
        .sort_values(ascending=False)
    )
    total = grouped.sum()
    share = grouped / total
    share = share[share >= MIN_SHARE]

    # 최대 비중을 1.0 으로. 비중 그대로 쓰면 최대가 0.497 이라 점수가 또 눌린다.
    weight = share / share.max()

    rows = [
        {
            "country": code,
            "country_name": name,
            "region_weight": round(float(value), 4),
            "import_share": round(float(share.loc[(code, name)]), 4),
            "aliases": ";".join(COUNTRY_ALIASES.get(code, [])),
        }
        for (code, name), value in weight.items()
    ]
    # 국가 미상. 버리지 않되 실제 조달국보다 낮게 둔다.
    rows.append(
        {
            "country": "Unknown",
            "country_name": "미상",
            "region_weight": UNKNOWN_WEIGHT,
            "import_share": 0.0,
            "aliases": "",
        }
    )

    result = pd.DataFrame(rows)
    result.to_csv(OUT_PATH, index=False, encoding="utf-8-sig")

    print(f"국가 가중치 {len(result)}행 → {OUT_PATH}")
    print(f"{'코드':<6}{'국가':<20}{'수입비중':>9}{'가중치':>9}")
    for row in result.itertuples():
        print(
            f"{row.country:<6}{row.country_name:<20}"
            f"{row.import_share:>8.2%}{row.region_weight:>9.3f}"
        )
    print(
        "\n최대 비중을 1.0 으로 정규화했다. 비중 자체를 쓰면 최대가 0.497 이라 "
        "전체 점수가 다시 눌린다."
    )


if __name__ == "__main__":
    main()
