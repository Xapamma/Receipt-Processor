import json
from pathlib import Path
import re
import os
from ollama import chat 
from tqdm import tqdm
from collections import defaultdict
from PIL import Image, ImageChops # for image merging


def group_receipt_images(image_paths):
    """
    Groups images like:
    11_page1.png, 11_page2.png → same receipt (key = "11")
    """

    groups = defaultdict(list)

    for path in image_paths:
        name = path.stem.lower()

        # Match pattern like "11_page1"
        match = re.match(r"(.+)_page(\d+)", name)

        if match:
            receipt_id = match.group(1)   # "11"
            page_num = int(match.group(2))  # 1, 2, etc.
        else:
            # fallback: treat as single-page receipt
            receipt_id = name
            page_num = 0
            print(f"Warning: Unrecognized filename format: {name}")

        groups[receipt_id].append((page_num, path))

    # Sort each group by page number
    sorted_groups = {}
    for key, items in groups.items():
        sorted_items = sorted(items, key=lambda x: x[0])
        sorted_groups[key] = [p for _, p in sorted_items]

    return sorted_groups

def is_empty(val):
    """Helper to catch None, empty strings, and literal 'null' strings from the LLM."""
    if val is None:
        return True
    if isinstance(val, str) and val.strip().lower() in ["null", "n/a", "none", ""]:
        return True
    return False

def merge_receipt_data(json_list):
    """Merges a list of parsed JSON receipt dictionaries into one master dictionary."""
    merged = {
        "store_name": None,
        "date": None,
        "time": None,
        "total_amount": None,
        "items_sold_count": None,
        "transactions": []
    }
    
    for data in json_list:
        if not isinstance(data, dict): 
            continue
        
        # 1. Capture Header Info (Store name, etc.)
        # We take the FIRST valid value we find across all pages.
        for key in ["store_name", "date", "time"]:
            val = data.get(key)
            if not merged[key] and val and str(val).lower() not in ["null", "none", ""]:
                merged[key] = val
                
        # 2. Capture Totals (Total amount, item count)
        # We take the LAST valid value found (since totals are usually at the end).
        for key in ["total_amount", "items_sold_count"]:
            val = data.get(key)
            if val and str(val).lower() not in ["null", "none", "", "0", "0.0"]:
                merged[key] = val
                
        # 3. Append Transactions
        txs = data.get("transactions")
        if isinstance(txs, list):
            for t in txs:
                # Only add if it looks like a real item (has a name and price)
                if isinstance(t, dict) and t.get("item_name") and t.get("price") is not None:
                    merged["transactions"].append(t)
            
    return merged

def extract_text_from_images(image_paths, receipt_id=None):
    """
    Handles single and multi-page receipts by running inference on 
    each page individually and merging the resulting JSONs.
    """
    total_pages = len(image_paths)
    all_page_data = []
    
    for i, path in enumerate(image_paths):
        current_page = i + 1
        final_path = str(Path(path).resolve())
        
        if not os.path.exists(final_path):
            return f"{{\"error\": \"File not found: {final_path}\"}}"

        # NEW: Quick check for blank images
        try:
            with Image.open(final_path) as img:
                # If the image is very small or has almost no color variation
                if img.getextrema() == ((255, 255), (255, 255), (255, 255)) or \
                   ImageChops.difference(img, Image.new("RGB", img.size, (255,255,255))).getbbox() is None:
                    print(f"Skipping {path.name} (detected as blank)")
                    all_page_data.append({})
                    continue
        except Exception:
            pass # If the check fails, just proceed to AI         

    
        # Updated Prompt: Removed quotes around null, clarified instructions
        prompt = f"""
            Analyze this image of a receipt. This image represents a COMPLETE receipt 
            (or possibly a blank/irrelevant page). This is PAGE {current_page} of {total_pages} for this receipt.

            Extract all visible receipt data into a structured JSON format.

            ### CRITICAL RULE:
            - If you do NOT see a valid receipt, or the image is blank (white/black), return ONLY: {{}}
            - Do NOT invent any data.
            - Do NOT guess missing values.
            - Only extract what is clearly visible.
            
            ### RECEIPT ASSUMPTIONS:
            - This is a single complete receipt (not part of a multi-page sequence).
            - All totals and transaction data should be interpreted as final.

            ### EXTRACTION REQUIREMENTS:

            - store_name:
            The name of the business.

            - date:
            Transaction date in YYYY-MM-DD format.

            - time:
            Transaction time in HH:MM:SS format.

            - total_amount:
            The final grand total paid (numeric).
            Look for "TOTAL" or "GRAND TOTAL".
            Ignore "SUBTOTAL", "TAX", "CHANGE".

            - items_sold_count:
            Total number of items purchased (numeric), if explicitly shown.

            - transactions:
            A list of all items purchased.

            For each item:
            - item_name: Clean descriptive name (no SKUs or codes included)
            - sku / upc / item code:
                Extract SKU / UPC / item code IF clearly associated with the item.
                This may appear:
                - before or after the item name
                - as a short numeric or alphanumeric code
                If not clearly tied to the item, omit this field.
            - price: Numeric price

            ### IMPORTANT RULES:
            - Each item must be unique unless explicitly repeated on the receipt.
            - Do NOT duplicate items to match item count.
            - Do NOT merge separate items into one.
            - If an item has multiple quantities, list it once (unless repeated lines exist).
            - Ignore unrelated long numbers unless they clearly correspond to an item.

            ### OUTPUT FORMAT:
            Return ONLY a valid JSON object.
            - No markdown
            - No explanations
            - No extra text

            Structure (omit fields if not found):

            {{
                "store_name": "string",
                "date": "YYYY-MM-DD",
                "time": "HH:MM:SS",
                "total_amount": 0.00,
                "items_sold_count": 0,
                "transactions": [
                    {{
                        "item_name": "string",
                        "price": 0.00,
                        "sku": "string"
                    }}
                ]
            }}
            """
        
        response = chat(
            model='llama3.2-vision',
            messages=[{
                "role": "user",
                "content": prompt,
                "images": [final_path]
            }],
            format="json", 
            options={
                "temperature": 0.1,
                "num_ctx": 8192,  
                "num_predict": 1000, 
                "num_thread": os.cpu_count(),
                "repeat_penalty": 1.15
            }
        )
        
        text = response["message"]["content"]
        
        # Clean markdown formatting if the model hallucinates it
        text = re.sub(r"^```json\s*", "", text.strip(), flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        
        try:
            page_data = json.loads(text)
            all_page_data.append(page_data)
        except json.JSONDecodeError:
            print(f"Warning: Failed to parse JSON from {final_path}. Raw output: {text[:100]}...")
            
    final_merged_data = merge_receipt_data(all_page_data)
    return json.dumps(final_merged_data, indent=4)

def process_image_folder(input_folder, output_folder, manual_folder):
    input_p = Path(input_folder)
    output_p = Path(output_folder)
    manual_p = Path(manual_folder)

    manual_p.mkdir(parents=True, exist_ok=True)
    output_p.mkdir(parents=True, exist_ok=True)

    all_images = list(input_p.glob("*.png"))
    grouped = group_receipt_images(all_images)

    to_process = []
    for key, image_group in grouped.items():
        output_file = output_p / f"{key}.json"
        manual_file = manual_p / f"{key}.json"

        # Only process if the JSON doesn't exist yet
        if not output_file.exists() and not manual_file.exists():
            to_process.append((key, image_group))

    skipped = len(grouped) - len(to_process)
    if skipped > 0:
        print(f"Skipping {skipped} already processed receipts...")

    if not to_process:
        print("Everything is already up to date!")
        return
    
    for key, image_group in tqdm(to_process, desc="Processing receipts", unit="receipt"):
        try:
            # Only process with the LLM if there are 2 or fewer pages. If more, save for manual review.
            if len(image_group) > 2:

                with open(manual_file, "w", encoding="utf-8") as f:
                    json.dump({
                        "receipt_id": key,
                        "num_pages": len(image_group),
                        "image_paths": [str(p) for p in image_group],
                        "status": "requires_manual_llm"
                    }, f, indent=4)

                tqdm.write(f"Skipped {key} (>{2} pages → manual review)")
                continue

            # Runs only for <= 2 pages
            receipt_data = extract_text_from_images(image_group, receipt_id=key)

            output_file = output_p / f"{key}.json"

            # Saving the data
            with open(output_file, "w", encoding="utf-8") as f:
                # If extract_text_from_images returns a DICT:
                if isinstance(receipt_data, dict):
                    json.dump(receipt_data, f, indent=4)
                # If it returns a STRING:
                else:
                    f.write(receipt_data)

        except Exception as e:
            tqdm.write(f"Error processing {key}: {e}")

    print(f"\nBatch complete! Processed {len(to_process)} receipts.")


# Run
process_image_folder("receipts_pngs", "texts12", "manual_review_12")
