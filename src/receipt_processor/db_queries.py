import sqlite3

import pandas as pd

from .db_ingest import DEFAULT_BUDGET_DB, DEFAULT_RECEIPTS_DB, initialize_budget_database


def get_total_spending(start_date=None, end_date=None, db_path=DEFAULT_RECEIPTS_DB):
    """Calculate total spending within an optional date range."""
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
    """Get total spending grouped by month."""
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
    """Get total spending by category."""
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
    """Get total spending by vendor."""
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
    """Get recent receipts sorted by date/time descending."""
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
    """Get one receipt and its items."""
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


def get_receipt_images(receipt_id, db_path=DEFAULT_RECEIPTS_DB):
    """Get image records linked to a receipt."""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT page_num, image_path, image_blob
            FROM receipt_images
            WHERE receipt_id = ?
            ORDER BY page_num ASC, id ASC
            """,
            (receipt_id,),
        )
        rows = cursor.fetchall()
    return [
        {
            "page_num": row[0],
            "image_path": row[1],
            "image_blob": row[2],
        }
        for row in rows
    ]


def export_receipts_to_dataframe(db_path=DEFAULT_RECEIPTS_DB):
    """Export all receipt/item rows to a DataFrame."""
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
    """Export all receipt data to CSV."""
    df = export_receipts_to_dataframe(db_path)
    df.to_csv(file_path, index=False)


def get_monthly_budget(month_key, db_path=DEFAULT_BUDGET_DB):
    """Get saved total budget for a month."""
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
    """Save or update total budget for a month."""
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
    """Get saved category budgets for a month."""
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
    """Save or update one category budget for a month."""
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
