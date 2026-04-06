import os
import json
from database import init_db, insert_receipt
import sqlite3

# Step 1: initialize DB
init_db()

folder_path = "texts5"

# Step 2: loop through all JSON files 
for filename in os.listdir(folder_path):
    if filename.endswith(".json"):
        file_path = os.path.join(folder_path, filename)

        with open(file_path, "r") as f:
            data = json.load(f)

        insert_receipt(data)

print("All receipts inserted.")

# Step 3: check what's inside
conn = sqlite3.connect("receipts.db")
cursor = conn.cursor()

print("\nReceipts:")
for row in cursor.execute("SELECT * FROM receipts"):
    print(row)

print("\nItems:")
for row in cursor.execute("SELECT * FROM items"):
    print(row)

conn.close()