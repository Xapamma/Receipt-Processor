from receipt_processor.llm_extraction import process_image_folder

def main():
    process_image_folder("data/receipts_pngs", "data/texts25", "data/manual_review_25")

if __name__ == "__main__":
    main()

