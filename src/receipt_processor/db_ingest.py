import json
import os
import sqlite3
from datetime import datetime

DEFAULT_RECEIPTS_DB = "data/receipts.db"
DEFAULT_BUDGET_DB = "data/budget.db"
DB_NAME = DEFAULT_RECEIPTS_DB


def _to_float(value, default=0.0):
    """Best-effort float conversion."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


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


def allocate_weighted_item_tax(items, receipt_total):
    """Allocate receipt-level difference (for example tax) back to item rows."""
    if not items:
        return []

    prices = [_to_float(item.get("price"), default=0.0) for item in items]
    subtotal = sum(prices)
    difference = _to_float(receipt_total, default=subtotal) - subtotal

    diff_cents = int(round(difference * 100))
    sign = 1 if diff_cents >= 0 else -1
    abs_diff_cents = abs(diff_cents)

    weights = [max(p, 0.0) for p in prices]
    weight_total = sum(weights)
    if weight_total <= 0:
        weights = [1.0] * len(items)
        weight_total = float(len(items))

    raw_alloc = [(abs_diff_cents * w) / weight_total for w in weights]
    base_alloc = [int(x) for x in raw_alloc]
    remainder = abs_diff_cents - sum(base_alloc)

    ranked = sorted(
        range(len(raw_alloc)),
        key=lambda i: raw_alloc[i] - base_alloc[i],
        reverse=True,
    )
    for i in ranked[:remainder]:
        base_alloc[i] += 1

    signed_alloc = [sign * cents for cents in base_alloc]

    allocated_items = []
    for item, base_price, alloc_cents in zip(items, prices, signed_alloc):
        allocated_tax = round(alloc_cents / 100.0, 2)
        allocated_items.append(
            {
                "item_name": item.get("item_name"),
                "price": base_price,
                "allocated_tax": allocated_tax,
                "price_with_tax": round(base_price + allocated_tax, 2),
                "category": item.get("category"),
            }
        )
    return allocated_items


def initialize_database(db_path=DEFAULT_RECEIPTS_DB):
    """Initialize the receipts SQLite database."""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        cursor.execute("PRAGMA foreign_keys = ON;")

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                time TEXT,
                vendor TEXT,
                total_amount REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                receipt_id INTEGER,
                item_name TEXT,
                price REAL,
                allocated_tax REAL DEFAULT 0,
                price_with_tax REAL,
                category TEXT,
                FOREIGN KEY (receipt_id) REFERENCES receipts(id)
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS receipt_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                receipt_id INTEGER NOT NULL,
                page_num INTEGER,
                image_path TEXT,
                image_blob BLOB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (receipt_id) REFERENCES receipts(id)
            )
            """
        )

        conn.commit()


def initialize_budget_database(db_path=DEFAULT_BUDGET_DB):
    """Initialize the budget SQLite database."""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS monthly_budgets (
                month_key TEXT PRIMARY KEY,
                total_budget REAL NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS category_budgets (
                month_key TEXT NOT NULL,
                category TEXT NOT NULL,
                budget REAL NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (month_key, category)
            )
            """
        )

        conn.commit()


def insert_receipt(data, db_path=DEFAULT_RECEIPTS_DB):
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


def insert_receipts_from_folder(folder_path, db_path=DEFAULT_RECEIPTS_DB):
    """Bulk insert all JSON receipts from a folder."""
    for filename in sorted(os.listdir(folder_path)):
        if not filename.endswith(".json"):
            continue

        file_path = os.path.join(folder_path, filename)
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        insert_receipt(data, db_path=db_path)


def update_receipt_details(receipt_id, date, time, vendor, total_amount, items, db_path=DEFAULT_RECEIPTS_DB):
    """Update one receipt and replace its items."""
    allocated_items = allocate_weighted_item_tax(items, total_amount)

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")

        cursor.execute("SELECT id FROM receipts WHERE id = ?", (receipt_id,))
        exists = cursor.fetchone()
        if exists is None:
            return False

        cursor.execute(
            """
            UPDATE receipts
            SET date = ?, time = ?, vendor = ?, total_amount = ?
            WHERE id = ?
            """,
            (
                date,
                time,
                vendor,
                _to_float(total_amount, default=0.0),
                receipt_id,
            ),
        )

        cursor.execute("DELETE FROM items WHERE receipt_id = ?", (receipt_id,))
        for item in allocated_items:
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
    return True


def delete_receipt(receipt_id, db_path=DEFAULT_RECEIPTS_DB):
    """Delete a receipt and all associated items."""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")

        cursor.execute("SELECT id FROM receipts WHERE id = ?", (receipt_id,))
        result = cursor.fetchone()

        if result is None:
            return False

        cursor.execute("DELETE FROM items WHERE receipt_id = ?", (receipt_id,))
        cursor.execute("DELETE FROM receipts WHERE id = ?", (receipt_id,))

    return True


def reset_database(db_path=DEFAULT_RECEIPTS_DB, confirm=False):
    """Reset the receipts database by dropping and recreating its tables."""
    if not confirm:
        raise ValueError("Set confirm=True to reset the database.")

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.execute("DROP TABLE IF EXISTS receipt_images;")
        cursor.execute("DROP TABLE IF EXISTS items;")
        cursor.execute("DROP TABLE IF EXISTS receipts;")

    initialize_database(db_path=db_path)


def print_db_snapshot(db_path=DEFAULT_RECEIPTS_DB):
    """Print receipts and items for quick manual validation."""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        print("\nReceipts:")
        for row in cursor.execute("SELECT * FROM receipts"):
            print(row)

        print("\nItems:")
        for row in cursor.execute("SELECT * FROM items"):
            print(row)
