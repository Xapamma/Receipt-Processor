# Receipt Processor

Receipt Processor is a Python package for extracting, structuring, and analyzing receipt data. It combines OCR, database storage, budget tracking, and a Streamlit interface so you can convert receipt images into searchable spending records.

## What it does

- Extracts text from receipt images using EasyOCR and using that text for LLM parsing
- Stores receipts and item details in SQLite databases
- Provides spending analysis by total, month, category, and vendor
- Supports budget tracking for monthly and category budgets
- Includes a Streamlit app for upload, review, and manual correction

## Key Features

- **OCR extraction** for receipt images and PDFs
- **LLM-guided item categorization** with a preset category vocabulary
- **Database ingestion** using `receipt_processor.db_ingest`
- **Reporting and analytics** using `receipt_processor.db_queries`
- **Interactive app** in `app.py` for visual review and budget monitoring

## Installation

### Install from PyPI

When published, you can install Receipt Processor directly from PyPI:

```bash
pip install receipt-processor
```

### Install from source

For development or the latest repository version, use `uv` to create an environment and install dependencies:

```bash
pip install uv
cd /path/to/Receipt-Processor
uv sync
```

After `uv sync` completes, activate the created virtual environment:

```bash
# Windows
.venv\Scripts\activate

# macOS/Linux
source .venv/bin/activate
```

## Quick Start

Run the Streamlit interface:

```bash
streamlit run app.py
```

Then provide database paths such as:

- `data/receipts.db`
- `data/budget.db`

The app can create these databases automatically.

## Basic Usage

### Extract OCR text

```python
from receipt_processor.ocr_utils import extract_text_from_image
text = extract_text_from_image("receipts/my_receipt.png")
print(text)
```

### Initialize databases

```python
from receipt_processor.db_ingest import initialize_database, initialize_budget_database
initialize_database(db_path="data/receipts.db")
initialize_budget_database(db_path="data/budget.db")
```

### Insert receipt data

```python
from receipt_processor.db_ingest import insert_receipt
receipt_id = insert_receipt(
    vendor="Walmart",
    date="2024-01-15",
    time="14:30:00",
    total_amount=45.99,
    tax_amount=3.50,
    items=[
        {"item_name": "Milk", "price": 3.99, "category": "Grocery"},
        {"item_name": "Bread", "price": 2.49, "category": "Grocery"},
    ],
    db_path="data/receipts.db"
)
```

### Query spending

```python
from receipt_processor.db_queries import get_total_spending, get_category_breakdown
print(get_total_spending(db_path="data/receipts.db"))
print(get_category_breakdown(db_path="data/receipts.db"))
```

## Notes

- Automatic categorization uses an LLM prompt with a fixed set of categories.
- Batch receipt processing is designed for simple single-page receipts without refunds.
- Complex receipts are safer to process manually using the insert workflow.

## Documentation

A more detailed tutorial is available in `tutorial.qmd` and via the generated GitHub Pages site. The repository also includes API documentation in `api.qmd`.

## Links

- **Documentation source**: `api.qmd`
- **Tutorial source**: `tutorial.qmd`
- **Report source**: `technical-report.qmd`
- **Streamlit app**: run `streamlit run app.py`
- **GitHub repository**: https://github.com/Xapamma/Receipt-Processor

## Repository Layout

- `app.py` — Streamlit front-end for receipt upload and review
- `cat_try.py` — category classification and LLM prompt logic
- `ocr_png_to_text.py` — OCR extraction helpers
- `src/receipt_processor/` — core package implementation
- `scripts/` — example scripts for extraction, ingestion, and validation
- `_quarto.yml` — website publishing configuration
- `pyproject.toml` — project dependencies and metadata
