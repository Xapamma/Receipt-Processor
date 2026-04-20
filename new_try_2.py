import json
from pathlib import Path
import re
import os
from sys import path
from ollama import chat 
from tqdm import tqdm
from collections import defaultdict
from PIL import Image, ImageChops
from json_repair import repair_json
from ocr_png_to_text import extract_text_from_image # for image merging


def extract_items_using_sku_only(ocr_text):
    """
    Extract ONLY item names + SKU.
    NO prices used from OCR.
    """

    lines = ocr_text.split("\n")
    items = []

    sku_pattern = re.compile(r"\b\d{8,14}\b")

    for line in lines:
        clean = line.strip()

        if not clean:
            continue

        # Get rid of ref and terminal matches
        if "#" in clean:
            continue

        sku_match = sku_pattern.search(clean)
        if not sku_match:
            continue

        sku = sku_match.group()

        # everything before SKU = item name
        name_part = clean[:sku_match.start()].strip()
        name_part = re.sub(r"[^A-Za-z0-9\s]", "", name_part).strip()

        if len(name_part) < 2:
            continue

        items.append({
            "item_name": name_part,
            "sku": sku,
            "raw_line": clean
        })

    return items


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

def extract_store_metadata(image_path):
    prompt = """
    Extract ONLY store-level information from this receipt image.

    Return STRICT JSON:
    {
        "store_name": "",
        "date": "",
        "time": "",
        "total_amount": 0.00,
        "items_sold_count": 0
    }

    Do NOT extract items or transactions.
    """

    response = chat(
        model='llama3.2-vision',
        messages=[{
            "role": "user",
            "content": prompt,
            "images": [image_path]
        }],
        format="json",
        options={
            "temperature": 0,
            "num_ctx": 4096,
            "num_predict": 2000
        }
    )

    return json.loads(response["message"]["content"])

def build_transactions(ocr_items, image_path):
    transactions = []

    for item in ocr_items:
        # OPTIONAL: lightweight price extraction per item (ONLY if needed)
        prompt = f"""
        Find the price for this item in the receipt image.

        Item: {item['item_name']}
        SKU: {item['sku']}

        Return JSON:
        {{
            "price": 0.00
        }}

        If not found, return null.
        """

        response = chat(
            model='llama3.2-vision',
            messages=[{
                "role": "user",
                "content": prompt,
                "images": [image_path]
            }],
            format="json",
            options={
                "temperature": 0,
                "num_ctx": 2048,
                "num_predict": 80   # small output
            }
        )

        try:
            price_data = json.loads(response["message"]["content"])
            price = price_data.get("price")
        except:
            price = None

        transactions.append({
            "item_name": item["item_name"],
            "sku": item["sku"],
            "price": price
        })

    return transactions

def process_receipt(image_path, ocr_text):
    ocr_items = extract_items_using_sku_only(ocr_text)

    print("OCR items:", len(ocr_items))

    # 1. metadata (single call)
    metadata = extract_store_metadata(image_path)

    # 2. transactions (reliable loop)
    transactions = build_transactions(ocr_items, image_path)

    # 3. final JSON
    final_output = {
        **metadata,
        "transactions": transactions
    }

    return final_output

def extract_text_from_images(image_paths, receipt_id=None):
    """
    Handles single and multi-page receipts by running inference on 
    each page individually and merging the resulting JSONs.
    """
    total_pages = len(image_paths)
    failed_pages = 0
    total_ocr_items = 0
    all_page_data = []  

    # IMPORTANT: use FIRST VALID IMAGE for metadata
    first_valid_image = None

    all_ocr_items = []
    image_path = str(Path(image_paths[0]).resolve())
    
    for i, path in enumerate(image_paths):
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
                    # all_page_data.append({})
                    continue
        except Exception:
            pass # If the check fails, just proceed to AI         

        if first_valid_image is None:
            first_valid_image = final_path

        # OCR Part 
        try:
            ocr_text = extract_text_from_image(final_path)
            ocr_items = extract_items_using_sku_only(ocr_text)

            if not ocr_items:
                print(f"No OCR items found: {path.name}")
                failed_pages += 1
                continue

            # attach image path so pricing works per 
            for item in ocr_items:
                item['image_path'] = final_path

            all_ocr_items.extend(ocr_items)

        except Exception as e:
            print(f"Error processing {path.name}: {e}")
            failed_pages += 1
        
        print(f'OCR ITEMS FOUND: {len(ocr_items)}')

    # ----------------------------
    # SAFETY CHECK
    # ----------------------------
    if not first_valid_image:
        return {
            "error": "no_valid_images_found",
            "reason": "all pages blank or missing"
        }
    
    # ----------------------------
    # OCR FAILURE CHECK
    # ----------------------------
    if len(all_ocr_items) == 0:
        return {
            "error": "no_ocr_items_detected",
            "reason": "ocr_failed_on_all_pages",
            "failed_pages": failed_pages,
            "total_pages": total_pages
        }

    # ----------------------------
    # 2. METADATA (LLM ONCE)
    # ----------------------------
    metadata = extract_store_metadata(first_valid_image)

    # ----------------------------
    # 3. TRANSACTIONS (NO PROMPT LOOP BUGS)
    # ----------------------------
    transactions = []

    for item in all_ocr_items:
        # OPTIONAL: only call LLM if price is unknown
         # 🔥 PUT SAFETY CHECK HERE
        image_path = item.get("image_path")

        if not image_path or not isinstance(image_path, str):
            print(f"Skipping item with missing image_path: {item}")
            continue

        image_path = str(Path(image_path).resolve())

        if not os.path.exists(image_path):
            print(f"Invalid image path: {image_path}")
            continue

        prompt = f"""
            Find ONLY the price for this item from the receipt image.

            Item: {item['item_name']}
            SKU: {item['sku']}

            IMPORTANT RULES:
            - The price may be on the SAME LINE or the NEXT LINE BELOW
            - Produce items (bananas, apples, avocado, etc.) often have price BELOW
            - Do NOT take a price from a different item
            - Only use a price visually associated with this item

            Return JSON:
            {{
                "price": 0.00
            }}

            If the price is not clearly visible, return null.
            Do NOT estimate, infer, or assume a price.
            Do NOT use 0 or 0.0 as a placeholder.
            Only return a number if explicitly readable.
            """

        response = chat(
            model='llama3.2-vision',
            messages=[{
                "role": "user",
                "content": prompt,
                "images": [item['image_path']]
            }],
            format="json",
            options={
                "temperature": 0,
                "num_ctx": 2048,
                "num_predict": 60
            }
        )

        try:
            raw_price = json.loads(response["message"]["content"]).get("price")

        except:
            price = None

        # Normalize bad outputs
        if raw_price in [0, 0.0, "0", "0.0", "0.00"]:
            price = None
        else:
            price = raw_price

        transactions.append({
            "item_name": item["item_name"],
            "sku": item["sku"],
            "price": price
        })

    # ----------------------------
    # 4. FINAL MERGE
    # ----------------------------
    final_output = {
        **metadata,
        "transactions": transactions
    }

    return final_output

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
            output_file = output_p / f"{key}.json"
            manual_file = manual_p / f"{key}.json"

            # Only process with the LLM if there are 1 pages. If more, save for manual review.
            if len(image_group) > 1:
                manual_records = {
                    "receipt_id": key,
                    "num_pages": len(image_group),
                    "image_paths": [str(p) for p in image_group],
                    "status": "requires_manual_llm"
                }

                with open(manual_file, "w", encoding="utf-8") as f:
                    json.dump(manual_records, f, indent=4)
                
                tqdm.write(f"Skipped {key} (>{1} pages → manual review)")
                continue

            # 🔍 Pre-check for refunds using OCR BEFORE LLM
            refund_flag = False

            for p in image_group:
                try:
                    ocr_text = extract_text_from_image(str(p))
                    if "refund" in ocr_text.lower():
                        refund_flag = True
                        break
                except:
                    continue

            if refund_flag:
                manual_record = {
                    "receipt_id": key,
                    "status": "refund_detected",
                    "reason": "contains refund keywords",
                    "image_paths": [str(p).replace("\\", "/") for p in image_group]
                }

                with open(manual_file, "w", encoding="utf-8") as f:
                    json.dump(manual_record, f, indent=4)

                tqdm.write(f"Sent {key} → manual review (refund detected)")
                continue


            # Runs only for <= 1 pages
            receipt_data = extract_text_from_images(image_group, receipt_id=key)

            # handle failed parse and save for manual review
            if isinstance(receipt_data, dict) and receipt_data.get("error"):
                manual_record = {
                    "receipt_id": key,
                    "status": "failed_parse",
                    "reason": receipt_data.get("reason"),
                    "failed_pages": receipt_data.get("failed_pages", 0),
                    "image_paths": [str(p).replace("\\", "/") for p in image_group]
                }

                with open(manual_file, "w", encoding="utf-8") as f:
                    json.dump(manual_record, f, indent=4)

                tqdm.write(f"Sent {key} → manual review (parse failed)")
                continue


            # Saving the data
            with open(output_file, "w", encoding="utf-8") as f:
                # If extract_text_from_images returns a DICT:
                json.dump(receipt_data, f, indent=4)

        except Exception as e:
            tqdm.write(f"Error processing {key}: {e}")

    print(f"\nBatch complete! Processed {len(to_process)} receipts.")


# Run
process_image_folder("receipts_pngs", "texts25", "manual_review_25")
