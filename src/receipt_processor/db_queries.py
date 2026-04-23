import sqlite3

import pandas as pd

from .db_ingest import DEFAULT_BUDGET_DB, DEFAULT_RECEIPTS_DB, initialize_budget_database


def get_total_spending(start_date=None, end_date=None, db_path=DEFAULT_RECEIPTS_DB):
    """Return total receipt spending for an optional date range.

    Args:
    - start_date: Inclusive lower date bound (`YYYY-MM-DD`) or `None`.
    - end_date: Inclusive upper date bound (`YYYY-MM-DD`) or `None`.
    - db_path: Path to the receipts SQLite database file.

    Returns:
    - Float total spend. Returns `0.0` when no rows match.
    """
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        query = "SELECT SUM(total_amount) FROM receipts"
        params = []

        if start_date and end_date:
            query += " WHERE date BETWEEN ? AND ?"
            params.extend([start_date, end_date])
        elif start_date:
            query += " WHERE date >= ?"
            params.append(start_date)
        elif end_date:
            query += " WHERE date <= ?"
            params.append(end_date)

        cursor.execute(query, params)
        result = cursor.fetchone()[0]

    return result if result else 0.0


def get_monthly_spending(start_date=None, end_date=None, db_path=DEFAULT_RECEIPTS_DB):
    """Return spending totals grouped by calendar month.

    Args:
    - start_date: Inclusive lower date bound (`YYYY-MM-DD`) or `None`.
    - end_date: Inclusive upper date bound (`YYYY-MM-DD`) or `None`.
    - db_path: Path to the receipts SQLite database file.

    Returns:
    - Dict mapping month key (`YYYY-MM`) to total spend.
    """
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        query = """
        SELECT strftime('%Y-%m', date) as month, SUM(total_amount)
        FROM receipts
        """
        params = []

        if start_date and end_date:
            query += " WHERE date BETWEEN ? AND ?"
            params.extend([start_date, end_date])
        elif start_date:
            query += " WHERE date >= ?"
            params.append(start_date)
        elif end_date:
            query += " WHERE date <= ?"
            params.append(end_date)

        query += " GROUP BY month ORDER BY month"

        cursor.execute(query, params)
        results = cursor.fetchall()

    return {month: total for month, total in results}


def get_category_breakdown(start_date=None, end_date=None, db_path=DEFAULT_RECEIPTS_DB):
    """Return item-level spending totals grouped by category.

    Args:
    - start_date: Inclusive lower date bound (`YYYY-MM-DD`) or `None`.
    - end_date: Inclusive upper date bound (`YYYY-MM-DD`) or `None`.
    - db_path: Path to the receipts SQLite database file.

    Returns:
    - Dict mapping category name to total spend.
    - Missing/empty categories are returned as `"Uncategorized"`.
    """
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        query = """
        SELECT i.category, SUM(i.price_with_tax)
        FROM items i
        JOIN receipts r ON i.receipt_id = r.id
        """
        params = []

        if start_date and end_date:
            query += " WHERE r.date BETWEEN ? AND ?"
            params.extend([start_date, end_date])
        elif start_date:
            query += " WHERE r.date >= ?"
            params.append(start_date)
        elif end_date:
            query += " WHERE r.date <= ?"
            params.append(end_date)

        query += " GROUP BY i.category ORDER BY SUM(i.price_with_tax) DESC"

        cursor.execute(query, params)
        results = cursor.fetchall()

    return {category or "Uncategorized": total for category, total in results}


def get_vendor_breakdown(start_date=None, end_date=None, db_path=DEFAULT_RECEIPTS_DB):
    """Return receipt-level spending totals grouped by vendor.

    Args:
    - start_date: Inclusive lower date bound (`YYYY-MM-DD`) or `None`.
    - end_date: Inclusive upper date bound (`YYYY-MM-DD`) or `None`.
    - db_path: Path to the receipts SQLite database file.

    Returns:
    - Dict mapping vendor name to total spend.
    """
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        query = """
        SELECT vendor, SUM(total_amount)
        FROM receipts
        """
        params = []

        if start_date and end_date:
            query += " WHERE date BETWEEN ? AND ?"
            params.extend([start_date, end_date])
        elif start_date:
            query += " WHERE date >= ?"
            params.append(start_date)
        elif end_date:
            query += " WHERE date <= ?"
            params.append(end_date)

        query += " GROUP BY vendor ORDER BY SUM(total_amount) DESC"

        cursor.execute(query, params)
        results = cursor.fetchall()

    return {vendor: total for vendor, total in results}


def get_recent_receipts(limit=10, db_path=DEFAULT_RECEIPTS_DB):
    """Return the most recent receipts sorted by date/time descending.

    Args:
    - limit: Maximum number of receipt rows to return.
    - db_path: Path to the receipts SQLite database file.

    Returns:
    - List of dicts with keys: `id`, `date`, `time`, `vendor`, `total_amount`.
    """
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        query = """
        SELECT id, date, time, vendor, total_amount
        FROM receipts
        ORDER BY date DESC, time DESC
        LIMIT ?
        """
        cursor.execute(query, (limit,))
        rows = cursor.fetchall()

    return [
        {
            "id": r[0],
            "date": r[1],
            "time": r[2],
            "vendor": r[3],
            "total_amount": r[4],
        }
        for r in rows
    ]


def get_receipt_details(receipt_id, db_path=DEFAULT_RECEIPTS_DB):
    """Return one receipt and its item rows.

    Args:
    - receipt_id: Receipt primary key to retrieve.
    - db_path: Path to the receipts SQLite database file.

    Returns:
    - Dict containing receipt fields and an `items` list if found.
    - `None` if the receipt does not exist.
    """
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        cursor.execute(
            "SELECT id, date, time, vendor, total_amount FROM receipts WHERE id = ?",
            (receipt_id,),
        )
        receipt = cursor.fetchone()

        if receipt is None:
            return None

        cursor.execute(
            """
            SELECT item_name, price, allocated_tax, price_with_tax, category
            FROM items
            WHERE receipt_id = ?
            """,
            (receipt_id,),
        )
        items = cursor.fetchall()

    return {
        "id": receipt[0],
        "date": receipt[1],
        "time": receipt[2],
        "vendor": receipt[3],
        "total_amount": receipt[4],
        "items": [
            {
                "item_name": i[0],
                "price": i[1],
                "allocated_tax": i[2],
                "price_with_tax": i[3],
                "category": i[4],
            }
            for i in items
        ],
    }


def export_receipts_to_dataframe(db_path=DEFAULT_RECEIPTS_DB):
    """Export joined receipts/items data as a pandas DataFrame.

    Args:
    - db_path: Path to the receipts SQLite database file.

    Returns:
    - DataFrame with columns:
      `receipt_id`, `date`, `time`, `vendor`, `total_amount`,
      `item_name`, `price`, `allocated_tax`, `price_with_tax`, `category`.
    - Each receipt appears once per item row (receipt-level fields repeat).
    """
    with sqlite3.connect(db_path) as conn:
        query = """
        SELECT
            r.id AS receipt_id,
            r.date,
            r.time,
            r.vendor,
            r.total_amount,
            i.item_name,
            i.price,
            i.allocated_tax,
            i.price_with_tax,
            i.category
        FROM receipts r
        LEFT JOIN items i ON r.id = i.receipt_id
        ORDER BY r.date DESC, r.time DESC
        """

        df = pd.read_sql_query(query, conn)

    return df


def export_receipts_to_csv(file_path, db_path=DEFAULT_RECEIPTS_DB):
    """Write joined receipts/items data to a CSV file.

    Args:
    - file_path: Output CSV file path.
    - db_path: Path to the receipts SQLite database file.
    """
    df = export_receipts_to_dataframe(db_path)
    df.to_csv(file_path, index=False)


def get_monthly_budget(month_key, db_path=DEFAULT_BUDGET_DB):
    """Return the saved total budget value for one month.

    Args:
    - month_key: Budget month key (`YYYY-MM`).
    - db_path: Path to the budget SQLite database file.

    Returns:
    - Float total budget if present, otherwise `None`.
    """
    initialize_budget_database(db_path=db_path)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT total_budget FROM monthly_budgets WHERE month_key = ?",
            (month_key,),
        )
        row = cursor.fetchone()
    return float(row[0]) if row else None


def save_monthly_budget(month_key, total_budget, db_path=DEFAULT_BUDGET_DB):
    """Create or update one monthly total budget value.

    Args:
    - month_key: Budget month key (`YYYY-MM`).
    - total_budget: Budget amount to store.
    - db_path: Path to the budget SQLite database file.
    """
    initialize_budget_database(db_path=db_path)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO monthly_budgets (month_key, total_budget, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(month_key) DO UPDATE SET
                total_budget = excluded.total_budget,
                updated_at = CURRENT_TIMESTAMP
            """,
            (month_key, float(total_budget)),
        )
        conn.commit()


def get_category_budgets(month_key, db_path=DEFAULT_BUDGET_DB):
    """Return saved category budgets for one month.

    Args:
    - month_key: Budget month key (`YYYY-MM`).
    - db_path: Path to the budget SQLite database file.

    Returns:
    - Dict mapping category name to budget float.
    """
    initialize_budget_database(db_path=db_path)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT category, budget
            FROM category_budgets
            WHERE month_key = ?
            """,
            (month_key,),
        )
        rows = cursor.fetchall()
    return {category: float(budget) for category, budget in rows}


def save_category_budget(month_key, category, budget, db_path=DEFAULT_BUDGET_DB):
    """Create or update one category budget value for a month.

    Args:
    - month_key: Budget month key (`YYYY-MM`).
    - category: Category name.
    - budget: Budget amount to store for the category.
    - db_path: Path to the budget SQLite database file.
    """
    initialize_budget_database(db_path=db_path)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO category_budgets (month_key, category, budget, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(month_key, category) DO UPDATE SET
                budget = excluded.budget,
                updated_at = CURRENT_TIMESTAMP
            """,
            (month_key, category, float(budget)),
        )
        conn.commit()
