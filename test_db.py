from database import init_db, insert_receipts_from_folder, print_db_snapshot


def main():
    init_db()
    insert_receipts_from_folder("texts25")
    print("All receipts inserted.")
    print_db_snapshot()


if __name__ == "__main__":
    main()
