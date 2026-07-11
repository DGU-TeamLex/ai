import pandas as pd
import streamlit as st

from src.config import PREDICTION_PATH


st.set_page_config(page_title="WeP-Stock Inventory Forecast", layout="wide")
st.title("WeP-Stock Inventory Forecast")

if not PREDICTION_PATH.exists():
    st.warning("predictions.csv not found. Run `python -m src.main` first.")
    st.stop()

df = pd.read_csv(PREDICTION_PATH)
df["year_month"] = pd.to_datetime(df["year_month"]).dt.strftime("%Y-%m")
df["institution_code"] = df["institution_code"].astype(str)
df["department"] = df["department"].astype(str)
df["item_code"] = df["item_code"].astype(str)

col1, col2, col3, col4 = st.columns(4)
yyyymm = col1.selectbox("월", sorted(df["year_month"].unique()))
institution_code = col2.selectbox("기관", sorted(df["institution_code"].unique()))
department = col3.selectbox("부서", sorted(df["department"].unique()))
item_code = col4.selectbox("물품코드", sorted(df["item_code"].unique()))

row_df = df[
    (df["year_month"] == yyyymm)
    & (df["institution_code"] == institution_code)
    & (df["department"] == department)
    & (df["item_code"] == item_code)
]
if row_df.empty:
    st.info("선택한 조건의 예측 결과가 없습니다.")
    st.stop()

row = row_df.iloc[0]
metric_cols = st.columns(4)
metric_cols[0].metric("예측 사용량", f"{row['predicted_usage']:.2f}")
metric_cols[1].metric("권장 재고량", f"{row['recommended_stock']:.2f}")
metric_cols[2].metric("외부 위험 점수", f"{row['external_risk_score']:.2f}")
metric_cols[3].metric("실제 사용량", f"{row['actual_usage']:.2f}")

current_stock = st.number_input("현재 재고", value=float(row.get("current_stock", 0.0)))
recommended_order = max(row["recommended_stock"] - current_stock, 0)
st.metric("권장 발주량", f"{recommended_order:.2f}")

st.subheader("위험 점수")
st.bar_chart(
    pd.DataFrame(
        {
            "risk": [
                row.get("disease_news_risk", 0),
                row.get("supply_news_risk", 0),
                row.get("commodity_risk", 0),
            ]
        },
        index=["감염병", "수급불안", "원자재"],
    )
)

st.subheader("선택 행")
st.dataframe(row_df)
