import json
import os
import sqlite3
from datetime import datetime

from src.receipt_processor.main_functions import initialize_database

DB_NAME = "receipts.db"


def init_db(db_path=DB_NAME):
    """Initialize the app database using the package-level schema."""
    initialize_database(db_path=db_path)


def _normalize_date(raw_date):
    """Normalize date to YYYY-MM-DD for database storage."""
    if not raw_date:
        return None

    date_str = str(raw_date).strip()
    formats = [
        "%Y-%m-%d",
        "%m/%d/%y",
        "%m/%d/%Y",
        "%b %d, %Y",
        "%B %d, %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    return date_str


def _normalize_time(raw_time):
    """Normalize time to HH:MM:SS for database storage."""
    if not raw_time:
        return None

    time_str = str(raw_time).strip()
    formats = [
        "%H:%M:%S",
        "%H:%M",
        "%I:%M:%S %p",
        "%I:%M %p",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(time_str, fmt).strftime("%H:%M:%S")
        except ValueError:
            continue

    return time_str


def insert_receipt(data, db_path=DB_NAME):
    """Insert one parsed receipt dict into receipts/items tables."""
    normalized_date = _normalize_date(data.get("date"))
    normalized_time = _normalize_time(data.get("time"))

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO receipts (date, time, vendor, total_amount)
            VALUES (?, ?, ?, ?)
            """,
            (
                normalized_date,
                normalized_time,
                data.get("store_name"),
                data.get("total_amount"),
            ),
        )

        receipt_id = cursor.lastrowid

        for item in data.get("transactions", []):
            cursor.execute(
                """
                INSERT INTO items (receipt_id, item_name, price, category)
                VALUES (?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    item.get("item_name"),
                    item.get("price"),
                    item.get("category"),
                ),
            )

        conn.commit()


def insert_receipts_from_folder(folder_path, db_path=DB_NAME):
    """Bulk insert all JSON receipts from a folder."""
    for filename in sorted(os.listdir(folder_path)):
        if not filename.endswith(".json"):
            continue

        file_path = os.path.join(folder_path, filename)
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        insert_receipt(data, db_path=db_path)


def print_db_snapshot(db_path=DB_NAME):
    """Print receipts and items for quick manual validation."""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        print("\nReceipts:")
        for row in cursor.execute("SELECT * FROM receipts"):
            print(row)

        print("\nItems:")
        for row in cursor.execute("SELECT * FROM items"):
            print(row)
