import pandas as pd
import streamlit as st

from src.config import PREDICTION_PATH


st.set_page_config(page_title="Medical Device Inventory Forecast", layout="wide")
st.title("Medical Device Inventory Forecast MVP")

if not PREDICTION_PATH.exists():
    st.warning("predictions.csv not found. Run `python -m src.predict` first.")
    st.stop()

df = pd.read_csv(PREDICTION_PATH)
df["year_month"] = pd.to_datetime(df["year_month"]).dt.strftime("%Y-%m")
df["SIDO"] = df["SIDO"].astype(str)
df["MED_DEVICE_5"] = df["MED_DEVICE_5"].astype(str)

col1, col2, col3 = st.columns(3)
yyyymm = col1.selectbox("월", sorted(df["year_month"].unique()))
sido = col2.selectbox("시도", sorted(df["SIDO"].unique()))
item_code = col3.selectbox("치료재료코드", sorted(df["MED_DEVICE_5"].unique()))

row_df = df[(df["year_month"] == yyyymm) & (df["SIDO"] == sido) & (df["MED_DEVICE_5"] == item_code)]
if row_df.empty:
    st.info("선택한 조건의 예측 결과가 없습니다.")
    st.stop()

row = row_df.iloc[0]
metric_cols = st.columns(4)
metric_cols[0].metric("예측 사용량", f"{row['predicted_usage']:.2f}")
metric_cols[1].metric("권장 재고량", f"{row['recommended_stock']:.2f}")
metric_cols[2].metric("외부 위험 점수", f"{row['external_risk_score']:.2f}")
metric_cols[3].metric("실제 사용량", f"{row['actual_usage']:.2f}")

current_stock = st.number_input("현재 재고", min_value=0.0, value=0.0)
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

