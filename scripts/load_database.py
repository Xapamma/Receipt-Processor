from receipt_processor.db_ingest import (
    initialize_database,
    insert_receipts_from_folder,
    print_db_snapshot,
)


def main():
    initialize_database()
    insert_receipts_from_folder("data/texts25")
    print("All receipts inserted.")
    print_db_snapshot()


if __name__ == "__main__":
    main()
