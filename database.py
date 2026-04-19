import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
import shutil

from src.receipt_processor.main_functions import (
    allocate_weighted_item_tax,
    initialize_database,
)

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


def _to_float(value, default=0.0):
    """Best-effort float parser for incoming JSON values."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def insert_receipt(data, db_path=DB_NAME):
    """Insert one parsed receipt dict into receipts/items tables."""
    normalized_date = _normalize_date(data.get("date"))
    normalized_time = _normalize_time(data.get("time"))
    transactions = data.get("transactions", [])
    receipt_total = _to_float(data.get("total_amount"), default=0.0)
    items_with_tax = allocate_weighted_item_tax(transactions, receipt_total)

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
                receipt_total,
            ),
        )

        receipt_id = cursor.lastrowid

        for item in items_with_tax:
            cursor.execute(
                """
                INSERT INTO items (receipt_id, item_name, price, allocated_tax, price_with_tax, category)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    item.get("item_name"),
                    item.get("price"),
                    item.get("allocated_tax"),
                    item.get("price_with_tax"),
                    item.get("category"),
                ),
            )

        conn.commit()
    return receipt_id


def _get_db_image_folder(db_path):
    db_stem = Path(db_path).stem or "receipts"
    return Path("receipt_images") / db_stem


def save_receipt_images(receipt_id, image_paths, db_path=DB_NAME, store_blob=True):
    """
    Save receipt images and link them to receipt_id in receipt_images table.
    """
    if not image_paths:
        return []

    out_dir = _get_db_image_folder(db_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")

        cursor.execute(
            "SELECT COALESCE(MAX(page_num), 0) FROM receipt_images WHERE receipt_id = ?",
            (receipt_id,),
        )
        current_max = int(cursor.fetchone()[0] or 0)

        for idx, image_path in enumerate(image_paths, start=1):
            src = Path(image_path)
            if not src.exists():
                continue

            page_num = current_max + idx
            ext = src.suffix.lower() if src.suffix else ".png"
            dest = out_dir / f"{receipt_id}_page{page_num}{ext}"
            shutil.copy2(src, dest)

            blob = src.read_bytes() if store_blob else None
            cursor.execute(
                """
                INSERT INTO receipt_images (receipt_id, page_num, image_path, image_blob)
                VALUES (?, ?, ?, ?)
                """,
                (receipt_id, page_num, str(dest), blob),
            )
            saved.append(str(dest))

        conn.commit()
    return saved


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
