import json
import os
from pathlib import Path

import fitz
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from database import insert_receipt
from llm_pdf_to_text import extract_text_from_images
from src.receipt_processor.main_functions import (
    export_receipts_to_dataframe,
    get_category_budgets,
    get_monthly_budget,
    get_receipt_details,
    get_recent_receipts,
    get_total_spending,
    initialize_database,
    initialize_budget_database,
    save_category_budget,
    save_monthly_budget,
)


st.set_page_config(page_title="Receipt Processor", layout="wide")
st.title("Receipt Processor")

db_path = st.sidebar.text_input("Database path", value="receipts.db")
initialize_database(db_path=db_path)
budget_db_path = st.sidebar.text_input("Budget DB path", value="budget.db")
initialize_budget_database(db_path=budget_db_path)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def convert_pdf_to_png(pdf_path):
    png_paths = []
    doc = fitz.open(pdf_path)
    for page_num in range(len(doc)):
        page = doc[page_num]
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        png_path = pdf_path.replace(".pdf", f"_page{page_num + 1}.png")
        pix.save(png_path)
        png_paths.append(png_path)
    doc.close()
    return png_paths


def process_receipt(file_path):
    path = Path(file_path)
    png_paths = convert_pdf_to_png(file_path) if path.suffix.lower() == ".pdf" else [file_path]
    json_text = extract_text_from_images(png_paths)
    try:
        return json.loads(json_text)
    except json.JSONDecodeError:
        return None


uploaded_file = st.sidebar.file_uploader(
    "Upload receipt (PDF or image)",
    type=["pdf", "png", "jpg", "jpeg"],
)

if uploaded_file is not None:
    file_path = os.path.join(UPLOAD_FOLDER, uploaded_file.name)
    with open(file_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    with st.spinner("Processing receipt..."):
        parsed = process_receipt(file_path)
        if parsed:
            insert_receipt(parsed, db_path=db_path)
            st.sidebar.success("Receipt uploaded and added to the database.")
            st.rerun()
        else:
            st.sidebar.error("Could not parse receipt JSON from the uploaded file.")

df = export_receipts_to_dataframe(db_path=db_path)
recent = get_recent_receipts(limit=10, db_path=db_path)
total_spend = get_total_spending(db_path=db_path)

if df.empty:
    st.info("No data found yet. Add receipts first, then refresh.")
    st.stop()

# Receipt-level view (avoid double-counting receipt totals due to item rows)
receipts_df = (
    df[["receipt_id", "date", "time", "vendor", "total_amount"]]
    .drop_duplicates(subset=["receipt_id"])
    .copy()
)
receipts_df["date"] = pd.to_datetime(
    receipts_df["date"],
    errors="coerce",
)
receipts_df["month_label"] = receipts_df["date"].dt.to_period("M").dt.to_timestamp()

col1, col2, col3 = st.columns(3)
col1.metric("Total Receipts", f"{len(receipts_df):,}")
col2.metric("Total Spending", f"${total_spend:,.2f}")
col3.metric("Average Receipt", f"${receipts_df['total_amount'].mean():,.2f}")

months_to_show = st.slider(
    "Months to include in trend/average",
    min_value=2,
    max_value=24,
    value=12,
    step=1,
)
end_month = pd.Timestamp.today().to_period("M").to_timestamp()
start_month = end_month - pd.DateOffset(months=months_to_show - 1)
window_index = pd.date_range(start=start_month, end=end_month, freq="MS")

monthly_totals = (
    receipts_df.groupby("month_label")["total_amount"]
    .sum()
    .reindex(window_index, fill_value=0.0)
    .rename_axis("month_label")
    .reset_index()
)
monthly_totals["month_name"] = monthly_totals["month_label"].dt.strftime("%b %Y")
monthly_totals["avg_spend"] = monthly_totals["total_amount"].mean()

st.subheader("Monthly Spend Trend")
trend_fig = go.Figure()
trend_fig.add_trace(
    go.Scatter(
        x=monthly_totals["month_label"],
        y=monthly_totals["total_amount"],
        mode="lines+markers",
        name="Monthly Spend",
        line={"width": 3},
        customdata=monthly_totals["month_name"],
        hovertemplate="Month: %{customdata}<br>Monthly Spend: $%{y:,.2f}<extra></extra>",
    )
)
trend_fig.add_trace(
    go.Scatter(
        x=monthly_totals["month_label"],
        y=monthly_totals["avg_spend"],
        mode="lines",
        name="Average Spend",
        line={"width": 3, "dash": "dash"},
        customdata=monthly_totals["month_name"],
        hovertemplate="Month: %{customdata}<br>Average Spend: $%{y:,.2f}<extra></extra>",
    )
)
trend_fig.update_layout(
    hovermode="x unified",
    xaxis_title="Month",
    yaxis_title="Total Spend ($)",
    xaxis=dict(tickformat="%b %Y"),
    margin=dict(l=20, r=20, t=20, b=20),
)
st.plotly_chart(trend_fig, use_container_width=True)

window_receipts = receipts_df[receipts_df["month_label"].isin(window_index)].copy()
window_total_spending = window_receipts["total_amount"].sum()
window_avg_receipt = window_receipts["total_amount"].mean() if not window_receipts.empty else 0.0

st.markdown(f"**Selected Window Metrics ({months_to_show} months)**")
w1, w2, w3 = st.columns(3)
w1.metric("Window Receipts", f"{len(window_receipts):,}")
w2.metric("Window Spending", f"${window_total_spending:,.2f}")
w3.metric("Window Avg Receipt", f"${window_avg_receipt:,.2f}")

st.subheader("Monthly Spend Stacked Bar by Category")
category_monthly = df[["date", "vendor", "category", "price"]].copy()
category_monthly["date"] = pd.to_datetime(category_monthly["date"], errors="coerce")
category_monthly["month_label"] = category_monthly["date"].dt.to_period("M").dt.to_timestamp()
category_monthly["vendor"] = category_monthly["vendor"].fillna("Unknown Vendor")
category_monthly["category"] = category_monthly["category"].fillna("Uncategorized")
category_monthly["price"] = pd.to_numeric(category_monthly["price"], errors="coerce").fillna(0.0)
category_monthly = category_monthly[category_monthly["month_label"].isin(window_index)]

if category_monthly.empty:
    st.info("No category item data available for the selected month window.")
else:
    vendor_order = (
        category_monthly.groupby("vendor", as_index=False)["price"]
        .sum()
        .sort_values("price", ascending=False)["vendor"]
        .tolist()
    )
    selected_categories = []
    selected_vendors = []

    with st.expander("Choose filters for stacked bar chart", expanded=False):
        st.markdown("**Vendors**")
        v_select_col, v_clear_col = st.columns(2)
        if v_select_col.button("Select all vendors"):
            for vendor in vendor_order:
                st.session_state[f"area_vendor_{vendor}"] = True
        if v_clear_col.button("Clear all vendors"):
            for vendor in vendor_order:
                st.session_state[f"area_vendor_{vendor}"] = False

        vendor_cols = st.columns(3)
        for idx, vendor in enumerate(vendor_order):
            key = f"area_vendor_{vendor}"
            if key not in st.session_state:
                st.session_state[key] = True
            with vendor_cols[idx % 3]:
                st.checkbox(vendor, key=key)

        selected_vendors = [
            vendor
            for vendor in vendor_order
            if st.session_state.get(f"area_vendor_{vendor}", True)
        ]

        filtered_for_categories = category_monthly[
            category_monthly["vendor"].isin(selected_vendors)
        ] if selected_vendors else category_monthly.iloc[0:0]

        category_order = (
            filtered_for_categories.groupby("category", as_index=False)["price"]
            .sum()
            .sort_values("price", ascending=False)["category"]
            .tolist()
        )

        st.markdown("**Categories**")
        select_col, clear_col = st.columns(2)
        if select_col.button("Select all categories"):
            for category in category_order:
                st.session_state[f"area_cat_{category}"] = True
        if clear_col.button("Clear all categories"):
            for category in category_order:
                st.session_state[f"area_cat_{category}"] = False

        checkbox_cols = st.columns(3)
        for idx, category in enumerate(category_order):
            key = f"area_cat_{category}"
            if key not in st.session_state:
                st.session_state[key] = True
            with checkbox_cols[idx % 3]:
                st.checkbox(category, key=key)

        selected_categories = [
            category
            for category in category_order
            if st.session_state.get(f"area_cat_{category}", True)
        ]

    if not selected_vendors:
        st.info("Select at least one vendor to display the chart.")
    elif not selected_categories:
        st.info("Select at least one category to display the chart.")
    else:
        filtered = category_monthly[
            category_monthly["vendor"].isin(selected_vendors)
            & category_monthly["category"].isin(selected_categories)
        ]
        grouped = (
            filtered.groupby(["month_label", "category"], as_index=False)["price"].sum()
        )
        full_index = pd.MultiIndex.from_product(
            [window_index, selected_categories], names=["month_label", "category"]
        )
        grouped = (
            grouped.set_index(["month_label", "category"])
            .reindex(full_index, fill_value=0.0)
            .reset_index()
        )
        ordered_categories = (
            grouped.groupby("category", as_index=False)["price"]
            .sum()
            .sort_values("price", ascending=False)["category"]
            .tolist()
        )

        bar_fig = go.Figure()
        for category in ordered_categories:
            cat_data = grouped[grouped["category"] == category].copy()
            cat_data["month_name"] = cat_data["month_label"].dt.strftime("%b %Y")
            bar_fig.add_trace(
                go.Bar(
                    x=cat_data["month_label"],
                    y=cat_data["price"],
                    name=category,
                    customdata=cat_data["month_name"],
                    hovertemplate="Month: %{customdata}<br>Category: %{fullData.name}<br>Spend: $%{y:,.2f}<extra></extra>",
                )
            )

        bar_fig.update_layout(
            barmode="stack",
            hovermode="x unified",
            xaxis_title="Month",
            yaxis_title="Spend ($)",
            xaxis=dict(tickformat="%b %Y"),
            margin=dict(l=20, r=20, t=20, b=20),
        )
        st.plotly_chart(bar_fig, use_container_width=True)
        st.caption("Stacked bar chart is zero-filled for missing months so gaps still count as $0.00.")

st.subheader("Spend by Category and Vendor")
bar_base_df = df[["date", "vendor", "category", "price"]].copy()
bar_base_df["date"] = pd.to_datetime(bar_base_df["date"], errors="coerce")
bar_base_df["month_label"] = bar_base_df["date"].dt.to_period("M").dt.to_timestamp()
bar_base_df["category"] = bar_base_df["category"].fillna("Uncategorized")
bar_base_df["vendor"] = bar_base_df["vendor"].fillna("Unknown Vendor")
bar_base_df["price"] = pd.to_numeric(bar_base_df["price"], errors="coerce").fillna(0.0)

available_months = sorted(
    [m for m in receipts_df["month_label"].dropna().unique().tolist()],
    reverse=True,
)
bar_period_mode = st.radio(
    "Bar chart time filter",
    options=["Current month", "Specific month", "Last N months"],
    horizontal=True,
    key="bar_time_filter_mode",
)
bar_current_month = pd.Timestamp.today().to_period("M").to_timestamp()

if bar_period_mode == "Current month":
    bar_start = bar_current_month
    bar_end = bar_current_month
    bar_filter_label = bar_current_month.strftime("%b %Y")
elif bar_period_mode == "Specific month":
    month_options = available_months if available_months else [bar_current_month]
    selected_month = st.selectbox(
        "Choose month",
        options=month_options,
        format_func=lambda d: pd.Timestamp(d).strftime("%b %Y"),
        key="bar_specific_month",
    )
    bar_start = pd.Timestamp(selected_month)
    bar_end = pd.Timestamp(selected_month)
    bar_filter_label = bar_start.strftime("%b %Y")
else:
    bar_months = st.slider(
        "How many months",
        min_value=1,
        max_value=24,
        value=12,
        step=1,
        key="bar_last_n_months",
    )
    bar_end = bar_current_month
    bar_start = bar_end - pd.DateOffset(months=bar_months - 1)
    bar_filter_label = f"Last {bar_months} months"

filtered_items = bar_base_df[
    (bar_base_df["month_label"] >= bar_start) & (bar_base_df["month_label"] <= bar_end)
].copy()
filtered_receipts = receipts_df[
    (receipts_df["month_label"] >= bar_start) & (receipts_df["month_label"] <= bar_end)
].copy()

left, right = st.columns(2)
with left:
    st.subheader(f"Spend by Category ({bar_filter_label})")
    if not filtered_items.empty:
        cat_df = (
            filtered_items.groupby("category", as_index=False)["price"]
            .sum()
            .rename(columns={"price": "amount"})
            .sort_values("amount", ascending=False)
        )
        cat_fig = go.Figure(
            go.Bar(
                x=cat_df["category"],
                y=cat_df["amount"],
                hovertemplate="Category: %{x}<br>Spend: $%{y:,.2f}<extra></extra>",
            )
        )
        cat_fig.update_layout(
            xaxis_title="Category",
            yaxis_title="Spend ($)",
            margin=dict(l=20, r=20, t=20, b=20),
        )
        st.plotly_chart(cat_fig, use_container_width=True)
    else:
        st.write("No category data for this time selection.")

with right:
    st.subheader(f"Spend by Vendor ({bar_filter_label})")
    if not filtered_receipts.empty:
        ven_df = (
            filtered_receipts.groupby("vendor", as_index=False)["total_amount"]
            .sum()
            .rename(columns={"total_amount": "amount"})
            .sort_values("amount", ascending=False)
        )
        ven_fig = go.Figure(
            go.Bar(
                x=ven_df["vendor"],
                y=ven_df["amount"],
                hovertemplate="Vendor: %{x}<br>Spend: $%{y:,.2f}<extra></extra>",
            )
        )
        ven_fig.update_layout(
            xaxis_title="Vendor",
            yaxis_title="Spend ($)",
            margin=dict(l=20, r=20, t=20, b=20),
        )
        st.plotly_chart(ven_fig, use_container_width=True)
    else:
        st.write("No vendor data for this time selection.")

st.subheader("Monthly Budget Dashboard")
budget_items_df = df[["date", "category", "price"]].copy()
budget_items_df["date"] = pd.to_datetime(budget_items_df["date"], errors="coerce")
budget_items_df["month_label"] = budget_items_df["date"].dt.to_period("M").dt.to_timestamp()
budget_items_df["category"] = budget_items_df["category"].fillna("Uncategorized")
budget_items_df["price"] = pd.to_numeric(budget_items_df["price"], errors="coerce").fillna(0.0)

available_budget_months = sorted(
    [m for m in receipts_df["month_label"].dropna().unique().tolist()],
    reverse=True,
)
current_month = pd.Timestamp.today().to_period("M").to_timestamp()
if current_month not in available_budget_months:
    available_budget_months = [current_month] + available_budget_months

selected_budget_month = st.selectbox(
    "Select month for budget",
    options=available_budget_months,
    format_func=lambda d: pd.Timestamp(d).strftime("%b %Y"),
    key="monthly_budget_selected_month",
)
selected_budget_month = pd.Timestamp(selected_budget_month)
selected_budget_key = selected_budget_month.strftime("%Y-%m")

selected_month_receipts = receipts_df[receipts_df["month_label"] == selected_budget_month].copy()
selected_month_spend = selected_month_receipts["total_amount"].sum()

selected_month_category_totals = (
    budget_items_df[budget_items_df["month_label"] == selected_budget_month]
    .groupby("category", as_index=False)["price"]
    .sum()
    .rename(columns={"price": "spent"})
    .sort_values("spent", ascending=False)
)

saved_total_budget = get_monthly_budget(selected_budget_key, db_path=budget_db_path)
saved_category_budgets = get_category_budgets(selected_budget_key, db_path=budget_db_path)

total_budget_state_key = f"budget_total_{selected_budget_key}"
if total_budget_state_key not in st.session_state:
    st.session_state[total_budget_state_key] = (
        float(saved_total_budget) if saved_total_budget is not None else 1000.0
    )

total_budget_value = st.number_input(
    "Total monthly budget ($)",
    min_value=0.0,
    value=float(st.session_state[total_budget_state_key]),
    step=50.0,
    key=total_budget_state_key,
)

b1, b2, b3 = st.columns(3)
b1.metric("Month spend", f"${selected_month_spend:,.2f}")
b2.metric("Budget", f"${total_budget_value:,.2f}")
b3.metric("Remaining", f"${(total_budget_value - selected_month_spend):,.2f}")
st.progress(min(selected_month_spend / total_budget_value, 1.0) if total_budget_value > 0 else 0.0)

category_names = sorted(
    set(selected_month_category_totals["category"].tolist()) | set(saved_category_budgets.keys())
)
category_budget_values = {}

if category_names:
    st.markdown("**Category Budgets**")
    for category in category_names:
        spent_row = selected_month_category_totals[selected_month_category_totals["category"] == category]
        spent = float(spent_row["spent"].iloc[0]) if not spent_row.empty else 0.0

        cat_state_key = f"budget_{selected_budget_key}_{category}"
        if cat_state_key not in st.session_state:
            st.session_state[cat_state_key] = float(saved_category_budgets.get(category, 0.0))

        c1, c2 = st.columns([2, 1])
        with c1:
            cat_budget = st.number_input(
                f"{category} budget ($)",
                min_value=0.0,
                value=float(st.session_state[cat_state_key]),
                step=10.0,
                key=cat_state_key,
            )
        with c2:
            st.metric("Spent", f"${spent:,.2f}")

        st.progress(min(spent / cat_budget, 1.0) if cat_budget > 0 else 0.0)
        category_budget_values[category] = float(cat_budget)
else:
    st.info("No category data available for this month yet.")

if st.button("Save Budget", key=f"save_budget_{selected_budget_key}"):
    save_monthly_budget(selected_budget_key, total_budget_value, db_path=budget_db_path)
    for category, budget in category_budget_values.items():
        save_category_budget(selected_budget_key, category, budget, db_path=budget_db_path)
    st.success(f"Saved budget for {selected_budget_month.strftime('%b %Y')} to {budget_db_path}.")

st.subheader("Recent Receipts")
recent_df = pd.DataFrame(recent)
st.dataframe(recent_df, use_container_width=True)

if not recent_df.empty:
    selected_receipt_id = st.selectbox(
        "Inspect receipt details",
        options=recent_df["id"].tolist(),
        format_func=lambda x: f"Receipt #{x}",
    )
    details = get_receipt_details(selected_receipt_id, db_path=db_path)
    if details:
        st.write(
            f"**Vendor:** {details['vendor']} | "
            f"**Date:** {details['date']} | "
            f"**Time:** {details['time']} | "
            f"**Total:** ${details['total_amount']:.2f}"
        )
        st.dataframe(pd.DataFrame(details["items"]), use_container_width=True)
