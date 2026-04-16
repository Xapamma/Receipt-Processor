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
    get_category_breakdown,
    get_receipt_details,
    get_recent_receipts,
    get_total_spending,
    get_vendor_breakdown,
    initialize_database,
)


st.set_page_config(page_title="Receipt Processor Demo", layout="wide")
st.title("Receipt Processor Demo (main_functions.py)")
st.caption("This demo app uses helper functions from receipt_processor/main_functions.py.")

db_path = st.sidebar.text_input("Database path", value="receipts.db")
initialize_database(db_path=db_path)

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
category_breakdown = get_category_breakdown(db_path=db_path)
vendor_breakdown = get_vendor_breakdown(db_path=db_path)
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
st.caption("Missing months are included as $0.00 in the chart and in the average.")

window_receipts = receipts_df[receipts_df["month_label"].isin(window_index)].copy()
window_total_spending = window_receipts["total_amount"].sum()
window_avg_receipt = window_receipts["total_amount"].mean() if not window_receipts.empty else 0.0

st.markdown(f"**Selected Window Metrics ({months_to_show} months)**")
w1, w2, w3 = st.columns(3)
w1.metric("Window Receipts", f"{len(window_receipts):,}")
w2.metric("Window Spending", f"${window_total_spending:,.2f}")
w3.metric("Window Avg Receipt", f"${window_avg_receipt:,.2f}")

st.subheader("Monthly Spend Area by Category")
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

    with st.expander("Choose categories for area chart", expanded=False):
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
        st.info("Select at least one vendor to display the area chart.")
    elif not selected_categories:
        st.info("Select at least one category to display the area chart.")
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

        area_fig = go.Figure()
        for i, category in enumerate(ordered_categories):
            cat_data = grouped[grouped["category"] == category].copy()
            cat_data["month_name"] = cat_data["month_label"].dt.strftime("%b %Y")
            area_fig.add_trace(
                go.Scatter(
                    x=cat_data["month_label"],
                    y=cat_data["price"],
                    mode="lines",
                    name=category,
                    stackgroup="one",
                    fill="tozeroy" if i == 0 else "tonexty",
                    customdata=cat_data["month_name"],
                    hovertemplate="Month: %{customdata}<br>Category: %{fullData.name}<br>Spend: $%{y:,.2f}<extra></extra>",
                )
            )

        area_fig.update_layout(
            hovermode="x unified",
            xaxis_title="Month",
            yaxis_title="Spend ($)",
            xaxis=dict(tickformat="%b %Y"),
            margin=dict(l=20, r=20, t=20, b=20),
        )
        st.plotly_chart(area_fig, use_container_width=True)
        st.caption("Category area chart is zero-filled for missing months so gaps still count as $0.00.")

        st.subheader("Monthly Spend Stacked Bar by Category")
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
        st.caption("Stacked bar chart uses the same vendor/category selections and zero-filled missing months.")

left, right = st.columns(2)
with left:
    st.subheader("Spend by Category")
    if category_breakdown:
        cat_df = (
            pd.DataFrame(
                [{"category": k, "amount": v} for k, v in category_breakdown.items()]
            )
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
        st.write("No category data yet.")

with right:
    st.subheader("Spend by Vendor")
    if vendor_breakdown:
        ven_df = (
            pd.DataFrame(
                [{"vendor": k, "amount": v} for k, v in vendor_breakdown.items()]
            )
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
        st.write("No vendor data yet.")

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
