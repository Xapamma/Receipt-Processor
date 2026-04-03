import streamlit as st
import os
from streamlit_elements import elements, dashboard, mui
from database import init_db

init_db()

UPLOAD_FOLDER = "uploads"

# Make sure uploads folder exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

st.title("Receipt Processor")

st.subheader("Upload a Receipt")

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

    # Optional: show file name
    st.write(f"Saved to: {file_path}")



layout = [
    dashboard.Item("chart", 0, 0, 2, 2),
    dashboard.Item("upload", 2, 0, 2, 1),
]

with elements("dashboard"):
    with dashboard.Grid(layout):
        
        with mui.Paper(key="chart"):
            mui.Typography("Chart goes here")

        with mui.Paper(key="upload"):
            mui.Typography("Upload section")