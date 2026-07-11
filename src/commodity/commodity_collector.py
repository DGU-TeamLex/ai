import pandas as pd


def collect_commodity_prices() -> pd.DataFrame:
    months = pd.date_range("2024-01-01", "2025-12-01", freq="MS")
    materials = {
        "oil_plastic": 100.0,
        "latex": 80.0,
        "general_material": 70.0,
        "metal": 90.0,
        "cotton_pulp": 60.0,
    }
    rows = []
    for material, base_price in materials.items():
        for i, month in enumerate(months):
            shock = 1.0
            if material == "oil_plastic" and month >= pd.Timestamp("2025-03-01"):
                shock = 1.12
            if material == "latex" and month >= pd.Timestamp("2025-02-01"):
                shock = 1.10
            price = base_price * shock * (1 + 0.01 * i)
            rows.append(
                {
                    "date": month.strftime("%Y-%m-%d"),
                    "material": material,
                    "price": round(price, 4),
                    "volume": 1000 + i * 10,
                    "inventory": None,
                    "open_interest": None,
                }
            )
    return pd.DataFrame(rows)


if __name__ == "__main__":
    print(collect_commodity_prices().head().to_string(index=False))
