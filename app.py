import streamlit as st
import os
import json
from pathlib import Path
import fitz
from llm_pdf_to_text import extract_text_from_images
from database import init_db, insert_receipt
import sqlite3
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime

st.set_page_config(page_title="Receipt Processor", layout="wide")
init_db()

UPLOAD_FOLDER = "uploads"

# Make sure uploads folder exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

st.title("Receipt Processor")
st.write("Upload a receipt to automatically capture spend data, then explore your spending trends below.")

uploaded_file = st.file_uploader(
    "Choose a receipt (PDF or image)",
    type=["pdf", "png", "jpg", "jpeg"]
)

if uploaded_file is not None:
    file_path = os.path.join(UPLOAD_FOLDER, uploaded_file.name)

    # Save file
    with open(file_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    st.success("File uploaded successfully!")

    # Process the file
    with st.spinner("Processing receipt..."):
        try:
            data = process_receipt(file_path)
            if data:
                insert_receipt(data)
                st.success("Receipt processed and added to database!")
            else:
                st.error("Failed to extract data from receipt.")
        except Exception as e:
            st.error(f"Error processing receipt: {e}")

def process_receipt(file_path):
    path = Path(file_path)
    if path.suffix.lower() == '.pdf':
        # Convert PDF to PNG
        png_paths = convert_pdf_to_png(file_path)
    else:
        png_paths = [file_path]
    
    # Extract text using LLM
    json_text = extract_text_from_images(png_paths)
    try:
        data = json.loads(json_text)
        return data
    except json.JSONDecodeError:
        return None

def convert_pdf_to_png(pdf_path):
    png_paths = []
    doc = fitz.open(pdf_path)
    for page_num in range(len(doc)):
        page = doc[page_num]
        mat = fitz.Matrix(2, 2)
        pix = page.get_pixmap(matrix=mat)
        png_path = pdf_path.replace('.pdf', f'_page{page_num + 1}.png')
        pix.save(png_path)
        png_paths.append(png_path)
    doc.close()
    return png_paths

def load_receipt_data():
    conn = sqlite3.connect("receipts.db")
    receipts_df = pd.read_sql_query(
        "SELECT id, datetime, vendor, total_amount FROM receipts ORDER BY datetime",
        conn
    )
    items_df = pd.read_sql_query(
        "SELECT receipt_id, category, price FROM items WHERE price IS NOT NULL",
        conn
    )
    conn.close()
    return receipts_df, items_df


def build_dashboard(receipts_df, items_df, monthly_budget):
    if receipts_df.empty:
        st.write("No receipts processed yet. Upload one to see your spending dashboard.")
        return

    receipts_df['datetime'] = pd.to_datetime(receipts_df['datetime'])
    receipts_df['month_period'] = receipts_df['datetime'].dt.to_period('M')
    receipts_df['month_label'] = receipts_df['month_period'].dt.to_timestamp()
    monthly = receipts_df.groupby('month_label')['total_amount'].sum().reset_index()
    monthly['year'] = monthly['month_label'].dt.year
    monthly['yearly_avg'] = monthly.groupby('year')['total_amount'].transform('mean')

    current_month_label = pd.to_datetime(datetime.now().strftime('%Y-%m') + '-01')
    last_12_start = current_month_label - pd.DateOffset(months=11)
    monthly = monthly[monthly['month_label'] >= last_12_start].copy()
    monthly['month_name'] = monthly['month_label'].dt.strftime('%b %Y')

    st.subheader("Monthly Spending")
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(monthly['month_name'], monthly['total_amount'], marker='o', label='Monthly spend')
    ax.plot(monthly['month_name'], monthly['yearly_avg'], linestyle='--', label='Yearly average')
    ax.set_title('Monthly Spending with Yearly Average')
    ax.set_xlabel('Month')
    ax.set_ylabel('Total Spend ($)')
    ax.legend()
    ax.grid(alpha=0.3)
    plt.xticks(rotation=45)
    st.pyplot(fig)

    current_month_label = pd.to_datetime(datetime.now().strftime('%Y-%m') + '-01')
    current_spending = monthly.loc[monthly['month_label'] == current_month_label, 'total_amount'].sum()
    remaining = monthly_budget - current_spending

    with st.container():
        col1, col2, col3 = st.columns(3)
        col1.metric("Total receipts", len(receipts_df))
        col2.metric("Average receipt", f"${receipts_df['total_amount'].mean():.2f}")
        col3.metric("Current month spend", f"${current_spending:.2f}", delta=f"${remaining:.2f} remaining")

    if monthly_budget > 0:
        progress = min(current_spending / monthly_budget, 1.0)
    else:
        progress = 0
    st.progress(progress)

    with st.container():
        left, right = st.columns([1, 1])

        with left:
            st.subheader("Spending by Category")
            if not items_df.empty and items_df['category'].notna().any():
                items_df['category'] = items_df['category'].fillna('Uncategorized')
                category_totals = items_df.groupby('category')['price'].sum().sort_values(ascending=False)
                fig2, ax2 = plt.subplots(figsize=(6, 6))
                ax2.pie(
                    category_totals,
                    labels=category_totals.index,
                    autopct='%1.1f%%',
                    startangle=140,
                    textprops={'fontsize': 9}
                )
                ax2.set_title('Spend Breakdown by Category')
                ax2.axis('equal')
                st.pyplot(fig2)
            else:
                st.info('No item category data available yet. Showing vendor spend share instead.')
                vendor_total = receipts_df.groupby('vendor')['total_amount'].sum().sort_values(ascending=False)
                fig2, ax2 = plt.subplots(figsize=(6, 6))
                ax2.pie(
                    vendor_total,
                    labels=vendor_total.index,
                    autopct='%1.1f%%',
                    startangle=140,
                    textprops={'fontsize': 9}
                )
                ax2.set_title('Spend Breakdown by Vendor')
                ax2.axis('equal')
                st.pyplot(fig2)

        with right:
            st.subheader('Top vendors')
            vendor_total = receipts_df.groupby('vendor')['total_amount'].sum().sort_values(ascending=False)
            st.bar_chart(vendor_total)

            st.subheader('Monthly spend table')
            st.dataframe(
                monthly[['month_name', 'total_amount', 'yearly_avg']]
                .rename(columns={
                    'month_name': 'Month',
                    'total_amount': 'Total Spend',
                    'yearly_avg': 'Yearly Avg'
                })
                .style.format({
                    'Total Spend': '${:,.2f}',
                    'Yearly Avg': '${:,.2f}'
                })
            )


receipts_df, items_df = load_receipt_data()

st.sidebar.header("Budget Settings")
monthly_budget = st.sidebar.number_input("Monthly Budget ($)", min_value=0.0, value=1000.0)

st.subheader("Spending Dashboard")
build_dashboard(receipts_df, items_df, monthly_budget)

# Old dashboard code removed for now
# layout = [
#     dashboard.Item("chart", 0, 0, 2, 2),
#     dashboard.Item("upload", 2, 0, 2, 1),
# ]

# with elements("dashboard"):
#     with dashboard.Grid(layout):
        
#         with mui.Paper(key="chart"):
#             mui.Typography("Chart goes here")

#         with mui.Paper(key="upload"):
#             mui.Typography("Upload section")