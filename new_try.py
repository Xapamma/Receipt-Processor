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
from png_to_text import extract_text_from_image # for image merging


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

def extract_text_from_images(image_paths, receipt_id=None):
    """
    Handles single and multi-page receipts by running inference on 
    each page individually and merging the resulting JSONs.
    """
    total_pages = len(image_paths)
    all_page_data = []
    failed_pages = 0
    total_ocr_items = 0
    
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


        # OCR Part 
        ocr_text = extract_text_from_image(final_path)
        ocr_items = extract_items_using_sku_only(ocr_text)
        total_ocr_items += len(ocr_items)
        print(f"Total OCR items found: {total_ocr_items}")

        if not ocr_items:
            items_str = "No OCR items detected. Extract items directly from the image."
            print(f"No OCR items found: {path.name}")
            all_page_data.append({})
            continue

        # Build clean item list for LLM
        items_str = "\n".join([
            f"- {item['item_name']} (SKU: {item['sku']})"
            for item in ocr_items
        ])
    
        # Updated Prompt: Removed quotes around null, clarified instructions
        prompt = f"""
            Analyze this receipt image.

            You are given a high-quality OCR-derived item list.
            This list defines the COMPLETE structure of the receipt transactions.

            ---

            ### OCR ITEM LIST (REFERENCE ONLY)
            {items_str}

            ---

            ## CORE PRINCIPLE
            The OCR list is the authoritative transaction structure.

            - Each OCR item = exactly one transaction
            - Do NOT add or remove items
            - Do NOT reorder items
            - Do NOT merge items

            The image may be used ONLY to:
            - confirm item names
            - infer missing or unclear prices

            ---

            ## TASK

            You must process the OCR list sequentially.

            For index i from 1 to N:
            - produce transaction i
            - do not skip any index
            - do not stop early
            - do not summarize remaining items

            Think of this as filling a checklist.

            For each OCR item, produce one transaction with:

            - item_name (use OCR, corrected only if clearly wrong)
            - sku (use OCR exactly)
            - price (extract from image if visible, otherwise null)

            ---

            ## STRICT OUTPUT REQUIREMENT

            You will be given N OCR items.
            You MUST output a transaction for EACH index 1 through N in order.

            If unsure, still output a placeholder transaction.
            Never stop before index N.

            Process OCR items one-by-one in order.
            Do not generate the full list at once.
            After completing each item, proceed to the next index.

            If a value is uncertain:
            → still include the transaction with best available guess or null price

            ---

            ## STORE INFO EXTRACTION
            Extract independently from image:
            - store_name
            - date (YYYY-MM-DD)
            - time (HH:MM:SS)

            ---

            ## TOTALS
            - total_amount: final total only (ignore subtotal/tax/change)
            - items_sold_count: use receipt value if present

            ---

            ## OUTPUT FORMAT (STRICT JSON ONLY)

            {{
                "store_name": "string",
                "date": "YYYY-MM-DD",
                "time": "HH:MM:SS",
                "total_amount": 0.00,
                "items_sold_count": 0,
                "transactions": [
                    {{
                        "item_name": "string",
                        "sku": "string",
                        "price": 0.00
                    }}
                ]
            }}
            """
        
        for attempt in range(2):
            response = chat(
                model='llama3.2-vision',
                messages=[{
                    "role": "user",
                    "content": prompt,
                    "images": [final_path]
                }],
                format="json", 
                options={
                    "temperature": 0,
                    "num_ctx": 8192,  
                    "num_predict": 2500, 
                    "num_thread": os.cpu_count(),
                    "repeat_penalty": 1.05
                }
            )
            
            text = response["message"]["content"]
        
            # Clean markdown formatting if the model hallucinates it
            text = re.sub(r"^```json\s*", "", text.strip(), flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)

            try:
                fixed = repair_json(text)
                page_data = json.loads(fixed)
                all_page_data.append(page_data)
                break
            except:
                if attempt == 1:
                    print(f"Warning: Failed to parse JSON from {final_path}. Raw output: {text[:100]}...")
                    failed_pages += 1
                    all_page_data.append({})  # Append empty dict for failed pages to maintain page count      
              
    final_merged_data = merge_receipt_data(all_page_data)

    # 🔥 VALIDATION STEP (IMPORTANT)
    if not final_merged_data or len(final_merged_data.get("transactions", [])) == 0:
        return {
            "error": "parse_failed",
            "reason": "empty_or_invalid_merge",
            "failed_pages": failed_pages
        }

    return final_merged_data

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
process_image_folder("receipts_pngs", "texts22", "manual_review_22")
