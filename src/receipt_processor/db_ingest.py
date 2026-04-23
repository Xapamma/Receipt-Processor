import json
import os
import sqlite3
from datetime import datetime

DEFAULT_RECEIPTS_DB = "data/receipts.db"
DEFAULT_BUDGET_DB = "data/budget.db"


def _to_float(value, default=0.0):
    """Convert a value to float, falling back to `default` on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _normalize_date(raw_date):
    """Normalize a date-like string to `YYYY-MM-DD` when possible.

    Supports several common receipt date formats. If parsing fails, returns the
    original trimmed string so callers can still store the raw value.
    """
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
    """Normalize a time-like string to `HH:MM:SS` when possible.

    Supports 24-hour and AM/PM formats. If parsing fails, returns the original
    trimmed string so callers can still store the raw value.
    """
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
    """Allocate receipt-level difference across item rows by weighted price.

    Args:
    - items: List of item dictionaries containing at least `price`.
    - receipt_total: Receipt total used as the target sum for item totals.

    Returns:
    - A new list of item dictionaries with `allocated_tax` and `price_with_tax`.

    Notes:
    - Allocation is done in cents to minimize floating-point drift.
    - If all item prices are zero/missing, allocation falls back to equal weights.
    """
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
    """Create receipts database tables if they do not already exist.

    Args:
    - db_path: Path to the receipts SQLite database file.

    Side effects:
    - Ensures `receipts` and `items` tables exist.
    - Enables SQLite foreign key enforcement for the connection.
    """
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

        conn.commit()


def initialize_budget_database(db_path=DEFAULT_BUDGET_DB):
    """Create budget database tables if they do not already exist.

    Args:
    - db_path: Path to the budget SQLite database file.

    Side effects:
    - Ensures `monthly_budgets` and `category_budgets` tables exist.
    """
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
    """
    Insert one parsed receipt payload into `receipts` and `items` tables.

    Args:
    - data: Parsed receipt dictionary.
    - db_path: Path to the receipts SQLite database file.

    Expected `data` shape:
    - `store_name` (str | None)
    - `date` (str | None)
    - `time` (str | None)
    - `total_amount` (number-like)
    - `transactions` (list[dict]) where each item includes:
      `item_name`, `price`, and optional `category`.

    Behavior:
    - Normalizes date/time strings when possible.
    - Computes weighted per-item tax allocation so item totals align with receipt total.
    - Inserts one receipt row plus its associated item rows in the same DB transaction.

    Returns:
    - `receipt_id` (int): primary key of the inserted receipt row.
    """
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
    """Insert every JSON receipt file in a folder into the receipts database.

    Args:
    - folder_path: Directory containing parsed receipt `.json` files.
    - db_path: Path to the receipts SQLite database file.

    Side effects:
    - Inserts new receipt and item rows for each JSON file discovered.
    """
    for filename in sorted(os.listdir(folder_path)):
        if not filename.endswith(".json"):
            continue

        file_path = os.path.join(folder_path, filename)
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        insert_receipt(data, db_path=db_path)


def update_receipt_details(receipt_id, date, time, vendor, total_amount, items, db_path=DEFAULT_RECEIPTS_DB):
    """Update a receipt row and fully replace its associated item rows.

    Args:
    - receipt_id: Target receipt primary key.
    - date: New receipt date value.
    - time: New receipt time value.
    - vendor: New vendor/store name.
    - total_amount: New receipt total.
    - items: List of item dicts (`item_name`, `price`, optional `category`).
    - db_path: Path to the receipts SQLite database file.

    Returns:
    - `True` if the receipt exists and update succeeds, otherwise `False`.
    """
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
    """Delete a receipt and all related item rows.

    Args:
    - receipt_id: Target receipt primary key.
    - db_path: Path to the receipts SQLite database file.

    Returns:
    - `True` if the receipt existed and was deleted, otherwise `False`.
    """
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
    """Drop and recreate receipts tables.

    Args:
    - db_path: Path to the receipts SQLite database file.
    - confirm: Must be `True` to allow destructive reset.

    Raises:
    - ValueError: If `confirm` is not `True`.
    """
    if not confirm:
        raise ValueError("Set confirm=True to reset the database.")

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.execute("DROP TABLE IF EXISTS items;")
        cursor.execute("DROP TABLE IF EXISTS receipts;")

    initialize_database(db_path=db_path)


def print_db_snapshot(db_path=DEFAULT_RECEIPTS_DB):
    """Print current `receipts` and `items` table rows for quick debugging."""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        print("\nReceipts:")
        for row in cursor.execute("SELECT * FROM receipts"):
            print(row)

        print("\nItems:")
        for row in cursor.execute("SELECT * FROM items"):
            print(row)
