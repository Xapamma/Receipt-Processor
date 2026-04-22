import json
import os
from pathlib import Path

import fitz
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from database import insert_receipt, save_receipt_images
from ocr_png_to_text import extract_text_from_image
from src.receipt_processor.main_functions import (
    export_receipts_to_dataframe,
    get_category_budgets,
    get_monthly_budget,
    get_receipt_details,
    get_receipt_images,
    get_recent_receipts,
    get_total_spending,
    initialize_database,
    initialize_budget_database,
    save_category_budget,
    save_monthly_budget,
    update_receipt_details,
)


st.set_page_config(page_title="Receipt Processor", layout="wide")
st.title("Receipt Processor")

db_path = st.sidebar.text_input(
    "Receipt database path",
    value="",
    placeholder="e.g., receipts.db",
).strip()
budget_db_path = st.sidebar.text_input(
    "Budget database path",
    value="",
    placeholder="e.g., budget.db",
).strip()

if not db_path or not budget_db_path:
    st.info("Enter both database paths in the sidebar to load or create your databases.")
    st.stop()

initialize_database(db_path=db_path)
initialize_budget_database(db_path=budget_db_path)

db_upload_folder = f"{Path(db_path).stem}_uploads"
UPLOAD_FOLDER = str(Path(db_upload_folder))
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
        return json.loads(json_text), png_paths
    except json.JSONDecodeError:
        return None, png_paths


def cleanup_temp_upload_files(file_paths):
    """Best-effort deletion of temporary upload/converted files."""
    for file_path in file_paths:
        if not file_path:
            continue
        path_obj = Path(file_path)
        try:
            if path_obj.exists() and path_obj.is_file():
                path_obj.unlink()
        except OSError:
            # Cleanup failure should not break app flow.
            pass


def _extract_page_number(path_obj):
    name = path_obj.stem
    if "_page" in name:
        tail = name.split("_page")[-1]
        digits = "".join(ch for ch in tail if ch.isdigit())
        if digits:
            return int(digits)
    return 999999


def find_receipt_images_for_receipt(receipt_id, db_path, upload_folder):
    """
    Find likely receipt images for a receipt ID.

    - For receipts.db, search the legacy receipts_pngs folder.
    - For other DBs, search the DB-scoped uploads folder.
    """
    if Path(db_path).name == "receipts.db":
        search_dirs = [Path("receipts_pngs")]
    else:
        search_dirs = [Path(upload_folder)]

    rid = str(receipt_id)
    candidates = []
    for base_dir in search_dirs:
        if not base_dir.exists():
            continue
        for p in base_dir.glob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                continue
            name = p.name
            stem = p.stem
            if (
                name.startswith(f"{rid}_page")
                or name == f"{rid}.png"
                or name == f"{rid}.jpg"
                or name == f"{rid}.jpeg"
                or stem == rid
            ):
                candidates.append(p)

    candidates.sort(key=lambda p: (_extract_page_number(p), p.name))
    return candidates


def _parse_manual_items(raw_text):
    """
    Parse manual item lines in format:
    item name | price | category
    """
    items = []
    for idx, line in enumerate(raw_text.splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        parts = [p.strip() for p in text.split("|")]
        if len(parts) != 3:
            raise ValueError(
                f"Line {idx} is invalid. Use: item name | price | category"
            )
        name, price_text, category = parts
        try:
            price = float(price_text.replace("$", "").replace(",", ""))
        except ValueError as exc:
            raise ValueError(f"Line {idx} has invalid price: {price_text}") from exc
        items.append(
            {
                "item_name": name,
                "price": price,
                "category": category,
            }
        )
    return items


uploaded_file = st.sidebar.file_uploader(
    "Upload receipt (PDF or image)",
    type=["pdf", "png", "jpg", "jpeg"],
)

if uploaded_file is not None:
    file_path = os.path.join(UPLOAD_FOLDER, uploaded_file.name)
    with open(file_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    with st.spinner("Processing receipt..."):
        parsed, parsed_image_paths = process_receipt(file_path)
        if parsed:
            new_receipt_id = insert_receipt(parsed, db_path=db_path)
            save_receipt_images(
                receipt_id=new_receipt_id,
                image_paths=parsed_image_paths,
                db_path=db_path,
                store_blob=True,
            )
            st.sidebar.success("Receipt uploaded and added to the database.")
            st.rerun()
        else:
            cleanup_targets = {file_path}
            for temp_path in parsed_image_paths or []:
                cleanup_targets.add(temp_path)
            cleanup_temp_upload_files(cleanup_targets)
            st.sidebar.error("Could not parse receipt JSON from the uploaded file.")

with st.sidebar.expander("Manually enter receipt data", expanded=False):
    with st.form("manual_receipt_form", clear_on_submit=True):
        manual_date = st.text_input("Date (YYYY-MM-DD)", value="")
        manual_time = st.text_input("Time (HH:MM or HH:MM:SS)", value="")
        manual_vendor = st.text_input("Vendor", value="")
        manual_total = st.number_input("Total amount ($)", min_value=0.0, value=0.0, step=0.01)
        manual_items_raw = st.text_area(
            "Items (one per line: item name | price | category)",
            value="",
            height=150,
            placeholder="Milk | 4.29 | Grocery\nBread | 2.99 | Grocery",
        )
        submit_manual = st.form_submit_button("Add Manual Receipt")

    if submit_manual:
        try:
            transactions = _parse_manual_items(manual_items_raw)
            if not manual_vendor.strip():
                st.error("Vendor is required.")
            elif not transactions:
                st.error("Add at least one item line before saving.")
            else:
                manual_data = {
                    "date": manual_date.strip() or None,
                    "time": manual_time.strip() or None,
                    "store_name": manual_vendor.strip(),
                    "total_amount": float(manual_total),
                    "transactions": transactions,
                }
                insert_receipt(manual_data, db_path=db_path)
                st.success("Manual receipt added to the database.")
                st.rerun()
        except ValueError as exc:
            st.error(str(exc))

df = export_receipts_to_dataframe(db_path=db_path)
recent = get_recent_receipts(limit=10, db_path=db_path)
total_spend = get_total_spending(db_path=db_path)
item_spend_col = "price_with_tax"

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
available_months_all = sorted(
    [m for m in receipts_df["month_label"].dropna().unique().tolist()],
    reverse=True,
)


def render_time_window_selector(
    prefix,
    title,
    available_months,
    default_months=12,
    max_months=24,
    modes=None,
):
    st.markdown(f"**{title}**")
    if modes is None:
        modes = ["Current month", "Specific month", "Last N months", "Calendar year"]
    mode = st.radio(
        "Time window",
        options=modes,
        horizontal=True,
        key=f"{prefix}_time_mode",
    )

    today_month = pd.Timestamp.today().to_period("M").to_timestamp()
    latest_data_month = max(available_months) if available_months else today_month

    if mode == "Current month":
        start = today_month
        end = today_month
        label = today_month.strftime("%b %Y")
    elif mode == "Specific month":
        month_options = available_months if available_months else [today_month]
        if today_month not in month_options:
            month_options = [today_month] + month_options
        selected_month = st.selectbox(
            "Choose month",
            options=month_options,
            format_func=lambda d: pd.Timestamp(d).strftime("%b %Y"),
            key=f"{prefix}_specific_month",
        )
        start = pd.Timestamp(selected_month)
        end = pd.Timestamp(selected_month)
        label = start.strftime("%b %Y")
    elif mode == "Last N months":
        months = st.slider(
            "How many months",
            min_value=1,
            max_value=max_months,
            value=min(default_months, max_months),
            step=1,
            key=f"{prefix}_last_n_months",
        )
        end = latest_data_month
        start = end - pd.DateOffset(months=months - 1)
        label = f"Last {months} months"
    else:
        available_years = sorted(
            {int(pd.Timestamp(m).year) for m in available_months},
            reverse=True,
        )
        if int(today_month.year) not in available_years:
            available_years = [int(today_month.year)] + available_years
        if not available_years:
            available_years = [int(today_month.year)]
        selected_year = st.selectbox(
            "Choose year",
            options=available_years,
            key=f"{prefix}_calendar_year",
        )
        start = pd.Timestamp(year=int(selected_year), month=1, day=1).to_period("M").to_timestamp()
        end = pd.Timestamp(year=int(selected_year), month=12, day=1).to_period("M").to_timestamp()
        label = f"Calendar year {selected_year}"

    month_index = pd.date_range(start=start, end=end, freq="MS")
    return start, end, month_index, label

col1, col2, col3 = st.columns(3)
col1.metric("Total Receipts", f"{len(receipts_df):,}")
col2.metric("Total Spending", f"${total_spend:,.2f}")
col3.metric("Average Receipt", f"${receipts_df['total_amount'].mean():,.2f}")

_, _, window_index, trend_window_label = render_time_window_selector(
    "trend",
    "Trend Chart Filters",
    available_months_all,
    default_months=12,
    max_months=24,
    modes=["Last N months", "Calendar year"],
)

monthly_totals = (
    receipts_df.groupby("month_label")["total_amount"]
    .sum()
    .reindex(window_index, fill_value=0.0)
    .rename_axis("month_label")
    .reset_index()
)
monthly_totals["month_name"] = monthly_totals["month_label"].dt.strftime("%b %Y")
monthly_totals["avg_spend"] = monthly_totals["total_amount"].mean()

st.subheader(f"Monthly Spend Trend ({trend_window_label})")
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

st.markdown(f"**Selected Window Metrics ({trend_window_label})**")
w1, w2, w3 = st.columns(3)
w1.metric("Window Receipts", f"{len(window_receipts):,}")
w2.metric("Window Spending", f"${window_total_spending:,.2f}")
w3.metric("Window Avg Receipt", f"${window_avg_receipt:,.2f}")

st.subheader("Monthly Spend Stacked Bar Breakdown")
category_monthly = df[["date", "vendor", "category", item_spend_col]].copy()
category_monthly["date"] = pd.to_datetime(category_monthly["date"], errors="coerce")
category_monthly["month_label"] = category_monthly["date"].dt.to_period("M").dt.to_timestamp()
category_monthly["vendor"] = category_monthly["vendor"].fillna("Unknown Vendor")
category_monthly["category"] = category_monthly["category"].fillna("Uncategorized")
category_monthly[item_spend_col] = pd.to_numeric(
    category_monthly[item_spend_col], errors="coerce"
).fillna(0.0)
_, _, stack_window_index, stack_window_label = render_time_window_selector(
    "stack",
    "Stacked Chart Filters",
    available_months_all,
    default_months=12,
    max_months=24,
    modes=["Last N months", "Calendar year"],
)
category_monthly = category_monthly[category_monthly["month_label"].isin(stack_window_index)]
st.caption(f"Showing: {stack_window_label}")

if category_monthly.empty:
    st.info("No category item data available for the selected month window.")
else:
    vendor_order = (
        category_monthly.groupby("vendor", as_index=False)[item_spend_col]
        .sum()
        .sort_values(item_spend_col, ascending=False)["vendor"]
        .tolist()
    )
    selected_categories = []
    selected_vendors = []

    with st.expander("Choose filters for stacked bar chart", expanded=False):
        breakdown_mode = st.radio(
            "Break down bars by",
            options=["Category", "Vendor"],
            horizontal=True,
            key="stack_breakdown_mode",
        )

        st.markdown("**Vendors**")
        v_select_col, v_clear_col = st.columns(2)
        if v_select_col.button("Select all vendors", key="stack_select_all_vendors"):
            for vendor in vendor_order:
                st.session_state[f"stack_vendor_{vendor}"] = True
        if v_clear_col.button("Clear all vendors", key="stack_clear_all_vendors"):
            for vendor in vendor_order:
                st.session_state[f"stack_vendor_{vendor}"] = False

        vendor_cols = st.columns(3)
        for idx, vendor in enumerate(vendor_order):
            key = f"stack_vendor_{vendor}"
            if key not in st.session_state:
                st.session_state[key] = True
            with vendor_cols[idx % 3]:
                st.checkbox(vendor, key=key)

        selected_vendors = [
            vendor
            for vendor in vendor_order
            if st.session_state.get(f"stack_vendor_{vendor}", True)
        ]

        filtered_for_categories = (
            category_monthly[category_monthly["vendor"].isin(selected_vendors)]
            if selected_vendors
            else category_monthly.iloc[0:0]
        )

        category_order = (
            filtered_for_categories.groupby("category", as_index=False)[item_spend_col]
            .sum()
            .sort_values(item_spend_col, ascending=False)["category"]
            .tolist()
        )

        st.markdown("**Categories**")
        select_col, clear_col = st.columns(2)
        if select_col.button("Select all categories", key="stack_select_all_categories"):
            for category in category_order:
                st.session_state[f"stack_cat_{category}"] = True
        if clear_col.button("Clear all categories", key="stack_clear_all_categories"):
            for category in category_order:
                st.session_state[f"stack_cat_{category}"] = False

        checkbox_cols = st.columns(3)
        for idx, category in enumerate(category_order):
            key = f"stack_cat_{category}"
            if key not in st.session_state:
                st.session_state[key] = True
            with checkbox_cols[idx % 3]:
                st.checkbox(category, key=key)

        selected_categories = [
            category
            for category in category_order
            if st.session_state.get(f"stack_cat_{category}", True)
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

        if breakdown_mode == "Category":
            stack_field = "category"
            stack_title = "Category"
            stack_order = (
                filtered.groupby("category", as_index=False)[item_spend_col]
                .sum()
                .sort_values(item_spend_col, ascending=False)["category"]
                .tolist()
            )
        else:
            stack_field = "vendor"
            stack_title = "Vendor"
            stack_order = (
                filtered.groupby("vendor", as_index=False)[item_spend_col]
                .sum()
                .sort_values(item_spend_col, ascending=False)["vendor"]
                .tolist()
            )

        grouped = filtered.groupby(["month_label", stack_field], as_index=False)[
            item_spend_col
        ].sum()
        full_index = pd.MultiIndex.from_product(
            [stack_window_index, stack_order], names=["month_label", stack_field]
        )
        grouped = (
            grouped.set_index(["month_label", stack_field])
            .reindex(full_index, fill_value=0.0)
            .reset_index()
        )

        bar_fig = go.Figure()
        for stack_value in stack_order:
            bar_data = grouped[grouped[stack_field] == stack_value].copy()
            bar_data["month_name"] = bar_data["month_label"].dt.strftime("%b %Y")
            bar_fig.add_trace(
                go.Bar(
                    x=bar_data["month_label"],
                    y=bar_data[item_spend_col],
                    name=stack_value,
                    customdata=bar_data["month_name"],
                    hovertemplate=f"Month: %{{customdata}}<br>{stack_title}: %{{fullData.name}}<br>Spend: $%{{y:,.2f}}<extra></extra>",
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

st.subheader("Spend by Category and Vendor")
bar_base_df = df[["date", "vendor", "category", item_spend_col]].copy()
bar_base_df["date"] = pd.to_datetime(bar_base_df["date"], errors="coerce")
bar_base_df["month_label"] = bar_base_df["date"].dt.to_period("M").dt.to_timestamp()
bar_base_df["category"] = bar_base_df["category"].fillna("Uncategorized")
bar_base_df["vendor"] = bar_base_df["vendor"].fillna("Unknown Vendor")
bar_base_df[item_spend_col] = pd.to_numeric(bar_base_df[item_spend_col], errors="coerce").fillna(0.0)
bar_start, bar_end, _, bar_filter_label = render_time_window_selector(
    "bar",
    "Bar Chart Filters",
    available_months_all,
    default_months=12,
    max_months=24,
)

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
            filtered_items.groupby("category", as_index=False)[item_spend_col]
            .sum()
            .rename(columns={item_spend_col: "amount"})
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
budget_items_df = df[["date", "category", item_spend_col]].copy()
budget_items_df["date"] = pd.to_datetime(budget_items_df["date"], errors="coerce")
budget_items_df["month_label"] = budget_items_df["date"].dt.to_period("M").dt.to_timestamp()
budget_items_df["category"] = budget_items_df["category"].fillna("Uncategorized")
budget_items_df[item_spend_col] = pd.to_numeric(
    budget_items_df[item_spend_col], errors="coerce"
).fillna(0.0)

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
    .groupby("category", as_index=False)[item_spend_col]
    .sum()
    .rename(columns={item_spend_col: "spent"})
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

all_receipt_ids = (
    receipts_df["receipt_id"]
    .dropna()
    .astype(int)
    .drop_duplicates()
    .sort_values(ascending=False)
    .tolist()
)

if all_receipt_ids:
    selected_receipt_id = st.selectbox(
        "Inspect receipt details",
        options=all_receipt_ids,
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

        with st.expander("See receipt image", expanded=False):
            linked_images = get_receipt_images(selected_receipt_id, db_path=db_path)
            if linked_images:
                for img in linked_images:
                    page_num = img.get("page_num")
                    img_path = img.get("image_path")
                    img_blob = img.get("image_blob")
                    caption = f"Page {page_num}" if page_num is not None else "Receipt image"
                    if img_path and Path(img_path).exists():
                        st.image(str(img_path), caption=caption, use_container_width=True)
                    elif img_blob:
                        st.image(img_blob, caption=caption, use_container_width=True)
            else:
                # Legacy fallback only for pre-linked receipts.db images
                fallback_images = find_receipt_images_for_receipt(
                    selected_receipt_id,
                    db_path=db_path,
                    upload_folder=UPLOAD_FOLDER,
                )
                if fallback_images:
                    for src in fallback_images:
                        st.image(str(src), caption=src.name, use_container_width=True)
                else:
                    st.info(
                        f"No linked image found for receipt #{selected_receipt_id}."
                    )

        with st.expander("Edit this receipt", expanded=False):
            edit_date = st.text_input(
                "Date (YYYY-MM-DD)",
                value=details.get("date") or "",
                key=f"edit_date_{selected_receipt_id}",
            )
            edit_time = st.text_input(
                "Time (HH:MM:SS)",
                value=details.get("time") or "",
                key=f"edit_time_{selected_receipt_id}",
            )
            edit_vendor = st.text_input(
                "Vendor",
                value=details.get("vendor") or "",
                key=f"edit_vendor_{selected_receipt_id}",
            )
            edit_total = st.number_input(
                "Total amount ($)",
                min_value=0.0,
                value=float(details.get("total_amount") or 0.0),
                step=0.01,
                key=f"edit_total_{selected_receipt_id}",
            )

            editable_items_df = pd.DataFrame(details["items"])[["item_name", "price", "category"]].copy()
            edited_items_df = st.data_editor(
                editable_items_df,
                use_container_width=True,
                num_rows="dynamic",
                key=f"edit_items_table_{selected_receipt_id}",
            )

            if st.button("Save receipt changes", key=f"save_receipt_{selected_receipt_id}"):
                clean_items = []
                for row in edited_items_df.to_dict(orient="records"):
                    item_name = str(row.get("item_name") or "").strip()
                    category = str(row.get("category") or "").strip()
                    price_raw = row.get("price")
                    if not item_name and (price_raw is None or str(price_raw).strip() == "") and not category:
                        continue
                    try:
                        price = float(price_raw)
                    except (TypeError, ValueError):
                        st.error(f"Invalid price for item '{item_name or '(blank)'}'.")
                        clean_items = None
                        break

                    clean_items.append(
                        {
                            "item_name": item_name or "Unknown Item",
                            "price": price,
                            "category": category or "Uncategorized",
                        }
                    )

                if clean_items is not None:
                    if not edit_vendor.strip():
                        st.error("Vendor cannot be empty.")
                    elif not clean_items:
                        st.error("Please keep at least one item.")
                    else:
                        updated = update_receipt_details(
                            receipt_id=selected_receipt_id,
                            date=edit_date.strip() or None,
                            time=edit_time.strip() or None,
                            vendor=edit_vendor.strip(),
                            total_amount=edit_total,
                            items=clean_items,
                            db_path=db_path,
                        )
                        if updated:
                            st.success("Receipt updated.")
                            st.rerun()
                        else:
                            st.error("Receipt not found. Refresh and try again.")
