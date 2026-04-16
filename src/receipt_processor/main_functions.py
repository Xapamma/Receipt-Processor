import sqlite3
import pandas as pd

def process_receipt(file_path):  
    # Placeholder for receipt processing logic
    # This function would read the receipt image, extract text, and parse it into structured data 
    # and add it to the database
    pass

def process_receipt_folder(folder_path):
    # Placeholder for processing all receipts in a folder
    # This function would loop through all receipt images in the specified folder and call process_receipt on each
    pass


def initialize_database(db_path="receipts.db"):
    """
    Initialize the SQLite database for storing receipt data.

    This creates the required tables if they do not already exist:
    - receipts
    - items

    Parameters
    ----------
    db_path : str, optional
        Path to the SQLite database file (default is "receipts.db").
    """
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        # Enable foreign key constraints
        cursor.execute("PRAGMA foreign_keys = ON;")

        # Receipts table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            time TEXT,
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

        # Budget settings table (one total budget per month)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS monthly_budgets (
            month_key TEXT PRIMARY KEY,
            total_budget REAL NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # Category budget settings table (multiple categories per month)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS category_budgets (
            month_key TEXT NOT NULL,
            category TEXT NOT NULL,
            budget REAL NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (month_key, category)
        )
        """)

        conn.commit()

def reset_database(db_path="receipts.db", confirm=False):
    """
    Reset the database by dropping all tables and recreating them.

    WARNING: This permanently deletes all stored receipt data.

    Parameters
    ----------
    db_path : str, optional
        Path to the SQLite database file (default is "receipts.db").
    confirm : bool
        Must be set to True to execute the reset.
    """
    if not confirm:
        raise ValueError("Set confirm=True to reset the database.")

    # Drop tables
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.execute("DROP TABLE IF EXISTS items;")
        cursor.execute("DROP TABLE IF EXISTS receipts;")

    # Recreate tables
    initialize_database(db_path)

def delete_receipt(receipt_id, db_path="receipts.db"):
    """
    Delete a receipt and all associated items from the database.

    Parameters
    ----------
    receipt_id : int
        ID of the receipt to delete.
    db_path : str, optional
        Path to the SQLite database file (default is "receipts.db").

    Returns
    -------
    bool
        True if the receipt was deleted, False if it did not exist.
    """
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")

        # Check if receipt exists
        cursor.execute("SELECT id FROM receipts WHERE id = ?", (receipt_id,))
        result = cursor.fetchone()

        if result is None:
            return False

        # Delete items first
        cursor.execute("DELETE FROM items WHERE receipt_id = ?", (receipt_id,))

        # Delete receipt
        cursor.execute("DELETE FROM receipts WHERE id = ?", (receipt_id,))

    return True


def get_total_spending(start_date=None, end_date=None, db_path="receipts.db"):
    """
    Calculate total spending within an optional date range.

    Parameters
    ----------
    start_date : str, optional
    end_date : str, optional
    db_path : str, optional

    Returns
    -------
    float
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

def get_monthly_spending(start_date=None, end_date=None, db_path="receipts.db"):
    """
    Get total spending grouped by month.

    Parameters
    ----------
    start_date : str, optional
    end_date : str, optional
    db_path : str, optional

    Returns
    -------
    dict
        { "YYYY-MM": total }
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

def get_category_breakdown(start_date=None, end_date=None, db_path="receipts.db"):
    """
    Get total spending by category.

    Parameters
    ----------
    start_date : str, optional
    end_date : str, optional
    db_path : str, optional

    Returns
    -------
    dict
        { category: total }
    """
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        query = """
        SELECT i.category, SUM(i.price)
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

        query += " GROUP BY i.category ORDER BY SUM(i.price) DESC"

        cursor.execute(query, params)
        results = cursor.fetchall()

    return {category or "Uncategorized": total for category, total in results}

def get_vendor_breakdown(start_date=None, end_date=None, db_path="receipts.db"):
    """
    Get total spending by vendor.

    Parameters
    ----------
    start_date : str, optional
    end_date : str, optional
    db_path : str, optional

    Returns
    -------
    dict
        { vendor: total }
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

def get_recent_receipts(limit=10, db_path="receipts.db"):
    """
    Get the most recent receipts.
    
    Parameters
    ----------
    limit : int, optional
        The number of recent receipts to retrieve.
    db_path : str, optional
        The path to the SQLite database file.
    
    Returns
    -------
    list of dict
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

def get_receipt_details(receipt_id, db_path="receipts.db"):
    """
    Get detailed information about a specific receipt, including items.

    Parameters
    ----------
    receipt_id : int
        The ID of the receipt for which to retrieve details.
    db_path : str, optional
        The path to the SQLite database file.

    Returns
    -------
    dict or None
    """
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        # Get receipt
        cursor.execute(
            "SELECT id, date, time, vendor, total_amount FROM receipts WHERE id = ?",
            (receipt_id,)
        )
        receipt = cursor.fetchone()

        if receipt is None:
            return None

        # Get items
        cursor.execute(
            """
            SELECT item_name, price, category
            FROM items
            WHERE receipt_id = ?
            """,
            (receipt_id,)
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
                "category": i[2],
            }
            for i in items
        ]
    }


def export_receipts_to_dataframe(db_path="receipts.db"):
    """
    Export all receipt and item data as a pandas DataFrame.

    Each row represents a single item.

    Parameters
    ----------
        db_path : str, optional
            The path to the SQLite database file (default is "receipts.db").

    Returns
    -------
    pandas.DataFrame
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
            i.category
        FROM receipts r
        LEFT JOIN items i ON r.id = i.receipt_id
        ORDER BY r.date DESC, r.time DESC
        """

        df = pd.read_sql_query(query, conn)

    return df

def export_receipts_to_csv(file_path, db_path="receipts.db"):
    """
    Export all receipt data to a CSV file.

    Parameters
    ----------
    file_path : str
        Path where the CSV file will be saved.
    db_path : str, optional
        Path to the SQLite database file.
    """
    df = export_receipts_to_dataframe(db_path)
    df.to_csv(file_path, index=False)


def get_monthly_budget(month_key, db_path="receipts.db"):
    """
    Get saved total budget for a month.

    Parameters
    ----------
    month_key : str
        Month key in YYYY-MM format.
    db_path : str, optional
        Path to SQLite DB.

    Returns
    -------
    float or None
    """
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT total_budget FROM monthly_budgets WHERE month_key = ?",
            (month_key,),
        )
        row = cursor.fetchone()
    return float(row[0]) if row else None


def save_monthly_budget(month_key, total_budget, db_path="receipts.db"):
    """
    Save/update total budget for a month. 

    Parameters
    ----------
    month_key : str
        Month key in YYYY-MM format.
    total_budget : float
        Total budget amount to save.
    db_path : str, optional
        Path to SQLite DB.
    """
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


def get_category_budgets(month_key, db_path="receipts.db"):
    """
    Get saved category budgets for a month.

    Parameters
    ----------
    month_key : str
        Month key in YYYY-MM format.
    db_path : str, optional
        Path to SQLite DB.

    Returns
    -------
    dict
        {category: budget}
    """
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


def save_category_budget(month_key, category, budget, db_path="receipts.db"):
    """
    Save/update one category budget for a month.

    Parameters
    ----------
    month_key : str
        Month key in YYYY-MM format.
    category : str
        Category name.
    budget : float
        Budget amount to save.
    db_path : str, optional
        Path to SQLite DB.
    """
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
