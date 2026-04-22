from receipt_processor.pdf_utils import convert_pdfs_to_pngs


def main():
    convert_pdfs_to_pngs("data/receipts", "data/receipts_pngs")
    convert_pdfs_to_pngs("data/online_order_receipts", "data/online_receipts_pngs")
    print("✅ Done converting all PDFs to PNGs.")   

if __name__ == "__main__":
    main()
