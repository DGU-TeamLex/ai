import pandas as pd
import streamlit as st

from src.config import PREDICTION_PATH
from src.modeling.inventory_policy import add_inventory_recommendations


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
if bool(row.get("is_stale_data", False)):
    st.warning(
        f"원본 기준월은 {row['forecast_origin_month']}이며 현재 운영에 사용하기에는 오래된 예측입니다."
    )
metric_cols = st.columns(5)
metric_cols[0].metric("예측 사용량", f"{row['predicted_usage']:.2f}")
metric_cols[1].metric("기본 재고량", f"{row.get('base_stock', row['recommended_stock']):.2f}")
metric_cols[2].metric("목표 재고량", f"{row.get('target_stock', row['recommended_stock']):.2f}")
metric_cols[3].metric("외부 위험 점수", f"{row['external_risk_score']:.2f}")
actual_usage = "미확정" if pd.isna(row.get("actual_usage")) else f"{row['actual_usage']:.2f}"
metric_cols[4].metric("실제 사용량", actual_usage)

policy_cols = st.columns(5)
current_stock = policy_cols[0].number_input(
    "현재 재고",
    min_value=0.0,
    value=float(row.get("current_stock", 0.0)),
)
on_order_qty = policy_cols[1].number_input("입고 예정", min_value=0.0, value=0.0)
backorder_qty = policy_cols[2].number_input("미납 수량", min_value=0.0, value=0.0)
lead_time_days = policy_cols[3].number_input(
    "조달 리드타임(일)", min_value=0, max_value=365, value=0
)
review_period_days = policy_cols[4].number_input(
    "검토 주기(일)", min_value=1, max_value=365, value=30
)

policy_input = row_df.head(1).copy()
policy_input["current_stock"] = current_stock
policy_input["on_order_qty"] = on_order_qty
policy_input["backorder_qty"] = backorder_qty
policy_input["lead_time_days"] = lead_time_days
policy_input["review_period_days"] = review_period_days
policy = add_inventory_recommendations(
    policy_input,
    current_stock_col="current_stock",
    on_order_qty_col="on_order_qty",
    backorder_qty_col="backorder_qty",
    lead_time_days_col="lead_time_days",
    review_period_days_col="review_period_days",
).iloc[0]

order_cols = st.columns(3)
order_cols[0].metric("기본 재고량", f"{policy['base_stock']:.2f}")
order_cols[1].metric("위험조정 목표 재고량", f"{policy['target_stock']:.2f}")
order_cols[2].metric("권장 발주량", f"{policy['recommended_order']:.2f}")

st.subheader("위험 점수")
st.bar_chart(
    pd.DataFrame(
        {
            "risk": [
                policy.get("demand_risk_score", 0),
                policy.get("supply_risk_score", 0),
                policy.get("material_risk_score", 0),
            ]
        },
        index=["수요", "공급", "원자재"],
    )
)

st.subheader("선택 행")
st.dataframe(row_df)
