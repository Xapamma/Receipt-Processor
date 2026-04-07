import sqlite3
from datetime import datetime 

DB_NAME = "receipts.db"


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Receipts table 
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS receipts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        datetime TEXT,
        vendor TEXT,
        total_amount REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Items table 
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        receipt_id INTEGER,
        item_name TEXT,
        price REAL,
        category TEXT,
        FOREIGN KEY (receipt_id) REFERENCES receipts(id)
    )
    """)

    conn.commit()
    conn.close()



def parse_datetime(date_str, time_str):
    if not date_str or not time_str:
        return None

    date_str = date_str.strip()
    time_str = time_str.strip()

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%y %H:%M:%S",
        "%m/%d/%Y %H:%M:%S"
    ]

    for fmt in formats:
        try:
            return datetime.strptime(f"{date_str} {time_str}", fmt).isoformat()
        except:
            continue

    return None


def insert_receipt(data):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    dt_str = parse_datetime(
        data.get("date"),
        data.get("time")
    )

    # Insert into receipts
    cursor.execute("""
    INSERT INTO receipts (datetime, vendor, total_amount)
    VALUES (?, ?, ?)
    """, (
        dt_str,
        data.get("store_name"),
        data.get("total_amount")
    ))

    receipt_id = cursor.lastrowid

    # Insert items
    for item in data.get("transactions", []):
        cursor.execute("""
        INSERT INTO items (receipt_id, item_name, price, category)
        VALUES (?, ?, ?, ?)
        """, (
            receipt_id,
            item.get("item_name"),
            item.get("price"),
            item.get("category")
        ))

    conn.commit()
    conn.close()