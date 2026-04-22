from receipt_processor.ocr_utils import process_image_folder

def main():
    process_image_folder("data/receipts_pngs", "data/texts")

if __name__ == "__main__":
    main()
    