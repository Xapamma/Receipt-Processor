from receipt_processor.db_ingest import (
    insert_receipts_from_folder,
    print_db_snapshot,
    reset_database,
)


def main():
    reset_database(confirm=True)
    insert_receipts_from_folder("data/texts28")
    insert_receipts_from_folder("data/completed_manual")
    print("All receipts inserted.")
    print_db_snapshot()


if __name__ == "__main__":
    main()
