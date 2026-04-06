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

def merge_receipt_data(json_list):
    """Merges a list of parsed JSON receipt dictionaries into one master dictionary."""
    merged = {
        "store_name": None,
        "address": None,
        "date": None,
        "time": None,
        "total_amount": None,
        "items_sold_count": None,
        "transactions": []
    }
    
    for data in json_list:
        if not data: continue
        
        # Take the first non-null value for header info (usually on Page 1)
        for key in ["store_name", "address", "date", "time"]:
            if merged[key] is None and data.get(key):
                merged[key] = data[key]
                
        # Take the max or last found for totals (usually on the last page)
        # By continually overwriting, we keep the data from the bottom-most page
        if data.get("total_amount") is not None:
            merged["total_amount"] = data["total_amount"]
        if data.get("items_sold_count") is not None:
            merged["items_sold_count"] = data["items_sold_count"]
            
        # Combine all transactions across pages
        if data.get("transactions"):
            merged["transactions"].extend(data["transactions"])
            
    return merged

def extract_text_from_images(image_paths, receipt_id=None):
    """
    Handles single and multi-page receipts by running inference on 
    each page individually and merging the resulting JSONs.
    """
    total_pages = len(image_paths)
    all_page_data = []
    
    # Loop through each page separately
    for i, path in enumerate(image_paths):
        current_page = i + 1
        final_path = str(Path(path).resolve())
        
        if not os.path.exists(final_path):
            return f"{{\"error\": \"File not found: {final_path}\"}}"
            
        # Dynamic prompt that changes based on the page number
        prompt = f"""
        Analyze the attached receipt image carefully. This is PAGE {current_page} of {total_pages} for this receipt.
        
        Extract the ACTUAL text from THIS SPECIFIC PAGE into JSON. 
        DO NOT make up fake data. If you cannot read a field on this exact page, return null.
        
        IMPORTANT:
        - Only extract what is visible on Page {current_page}.
        - If this is a partial receipt and the total or store name is on another page, return null for those fields.
        - List all items/transactions visible on this page.
        
        Extract these specific fields:
        - store_name: The name of the business (if visible).
        - address: The full street, city, state, and zip (if visible).
            - This may be a full street address OR just 'City, State, Zip'. Capture whatever is there.
        - date: Transaction date (YYYY-MM-DD) (if visible).
        - time: Transaction time (HH:MM:SS) (if visible).
        - total_amount: The final grand total paid (numeric, if visible).
            - Look for 'TOTAL', 'TEND', or 'GRAND TOTAL'.
           - IMPORTANT: Ignore the 'SUBTOTAL' or 'TAX' amounts.
           - Only extract the final, largest amount at the bottom of the calculation block.
        - items_sold_count: The total number of items purchased (if visible).
        - transactions: A list of objects with 'item_name' and 'price' (numeric) visible ON THIS PAGE.
            - Extract 'item_name' and 'price'. 
            - IMPORTANT: Ignore numeric codes, UPCs, or SKU numbers (e.g., 004123456789). 
            - ONLY capture the descriptive name of the item.

        JSON Structure:
        {{
        "store_name": "actual store name or null",
        "address": "actual address or null",
        "date": "YYYY-MM-DD or null",
        "time": "HH:MM:SS or null",
        "total_amount": 0.00 or null,
        "items_sold_count": 0 or null,
        "transactions": [
            {{"item_name": "item name", "price": 0.00}}
            ]
        }}
        """
        
        # Call the model for just this single page
        response = chat(
            model='llama3.2-vision',
            messages=[{
                "role": "user",
                "content": prompt,
                "images": [final_path]  # Only sending the current page
            }],
            format="json", 
            options={
                "temperature": 0,
                "num_ctx": 8192,  
                "num_predict": 2048, 
                "repeat_penalty": 1.2
            }
        )
        
        text = response["message"]["content"]
        
        # Parse the JSON safely so we can manipulate it in Python
        try:
            page_data = json.loads(text)
            all_page_data.append(page_data)
        except json.JSONDecodeError:
            print(f"Warning: Failed to parse JSON from {final_path}")
            
    # Merge the parsed data from all pages into one final dictionary
    final_merged_data = merge_receipt_data(all_page_data)
    
    # Return as a formatted JSON string to match your original pipeline
    return json.dumps(final_merged_data, indent=4)

def process_image_folder(input_folder, output_folder):
    input_p = Path(input_folder)
    output_p = Path(output_folder)
    output_p.mkdir(parents=True, exist_ok=True)

    all_images = list(input_p.glob("*.png"))
    grouped = group_receipt_images(all_images)

    to_process = []
    for key, image_group in grouped.items():
        output_file = output_p / f"{key}.json"
        # Only process if the JSON doesn't exist yet
        if not output_file.exists():
            to_process.append((key, image_group))

    skipped = len(grouped) - len(to_process)
    if skipped > 0:
        print(f"Skipping {skipped} already processed receipts...")

    if not to_process:
        print("Everything is already up to date!")
        return
    
    for key, image_group in tqdm(to_process, desc="Processing receipts", unit="receipt"):
        try:
            # This now calls our new page-by-page loop
            # pass the 'key' (receipt_id) so the function knows what it's working on
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
process_image_folder("receipts_pngs", "texts5")
