from pathlib import Path
import re
import os
from ollama import chat 
from tqdm import tqdm
from collections import defaultdict
from PIL import Image, ImageChops # for image merging




def autocrop_image(img, border_color=(255, 255, 255)):
    """
    Crops white/blank margins from an image.
    Assumes white background by default.
    """
    # Convert to RGB just in case
    img = img.convert("RGB")
    
    # Create mask of non-background
    bg = Image.new("RGB", img.size, border_color)
    diff = ImageChops.difference(img, bg)
    bbox = diff.getbbox()  # bounding box of non-white area
    
    if bbox:
        return img.crop(bbox)
    else:
        # Image is all white
        return img


def resize_if_needed(img, max_width=1000):
    if img.width > max_width:
        w_percent = max_width / float(img.width)
        h_size = int(float(img.height) * w_percent)
        return img.resize((max_width, h_size))
    return img


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


def merge_images_vertically(image_paths, output_path, max_width=1000):
    images = [Image.open(p) for p in image_paths]

    # Crop and resize
    processed_images = []
    for img in images:
        img = autocrop_image(img)
        img = resize_if_needed(img, max_width)
        processed_images.append(img)

    # Calculate total height and max width for merged image
    padding = 20 # 20 pixels of white space between pages
    total_height = sum(img.height for img in processed_images) + (len(processed_images) - 1) * padding
    merged_width = max(img.width for img in processed_images)

    # Create the merged image
    merged_img = Image.new("RGB", (merged_width, total_height))

    # Paste images one by one
    y_offset = 0
    for img in processed_images:
        merged_img.paste(img, (0, y_offset))
        y_offset += img.height + padding

    # Final resize if merged image is still too wide
    merged_img = resize_if_needed(merged_img, max_width)

    merged_img.save(output_path)
    return output_path


def extract_text_from_images(image_paths, receipt_id=None):

    """
    Handles:
    - single page → just process
    - multi-page → crop + resize + merge first
    """

    # Create unique temp filename per receipt (IMPORTANT)
    if receipt_id:
        temp_path = Path(f"temp_{receipt_id}.png")
    else:
        temp_path = Path("temp_merged_receipt.png")

    # If more than one image, merge first
    if len(image_paths) > 1:
        merged_path = merge_images_vertically(image_paths, temp_path)
        final_paths = [str(merged_path.resolve())]
    else:
        final_paths = [str(Path(image_paths[0]).resolve())]

    # Verify files exist
    for p in final_paths:
        if not os.path.exists(p):
            return f"{{'error': 'File not found: {p}'}}"
   
    # Simplified, literal prompt
    prompt = """
        Analyze the attached receipt image carefully.    

        Extract the ACTUAL text from the image into JSON. 
        DO NOT make up fake data. If you cannot read a field, return null.

        IMPORTANT:
        - Use the final grand total from the receipt
        - Combine all line items

        Extract these specific fields:
        - store_name: The name of the business.
        - address: The full street, city, state, and zip.
        - date: Transaction date (YYYY-MM-DD).
        - time: Transaction time (HH:MM:SS).
        - total_amount: The final grand total paid (numeric).
        - items_sold_count: The total number of items purchased (usually listed at the bottom).
        - transactions: A list of objects with 'item_name' and 'price' (numeric).

        JSON Structure:
        {
        "store_name": "actual store name",
        "address": "actual address",
        "date": "YYYY-MM-DD",
        "time": "HH:MM:SS",
        "total_amount": 0.00,
        "items_sold_count": 0,
        "transactions": [
            {"item_name": "item name", "price": 0.00}
            ]
        }
        """

    # Call the model
    response = chat(
        model='llama3.2-vision',
        messages=[{
            "role": "user",
            "content": prompt,
            "images": final_paths  
        }],
        format="json",  # This forces the model to output valid JSON
        options={"temperature": 0,  # This makes the output deterministic and less "creative
                 "num_ctx": 4096,      # Give it enough "memory" for long receipts
                 "num_predict": 2048,   # Stop it if it starts looping forever
                 "repeat_penalty": 1.2  # Discourage repeating the same line (Dill Pickles!)
    }
    )

    # The `.message.content` field contains the model output
    text = response["message"]["content"]

    return text


def process_image_folder(input_folder, output_folder):
    input_p = Path(input_folder)
    output_p = Path(output_folder)
    output_p.mkdir(parents=True, exist_ok=True)

    # Get list of all PNGs in the input folder
    all_images = list(input_p.glob("*.png"))
    
     # Group images
    grouped = group_receipt_images(all_images)

    # filter out images that have already been processed
    to_process = []
    for key, image_group in grouped.items():
        output_file = output_p / f"{key}.json"
        if not output_file.exists() or len(image_group) > 1:
            to_process.append((key, image_group))

    skipped = len(grouped) - len(to_process)
    if skipped > 0:
        print(f"Skipping {skipped} already processed receipts...")

    if not to_process:
        print("Everything is already up to date!")
        return
    
    # Add tqdm progress bar
    for key, image_group in tqdm(to_process, desc="Processing receipts", unit="receipt"):
        try:
            json_text = extract_text_from_images(image_group)

            output_file = output_p / f"{key}.json"

            with open(output_file, "w", encoding="utf-8") as f:
                f.write(json_text)

        except Exception as e:
            tqdm.write(f"Error processing {key}: {e}")

    print(f"\nBatch complete! Processed {len(to_process)} receipts.")
# Run
# process_image_folder("receipts_pngs", "texts2")




# Image test
# --- Set your receipt 25 folder/files ---
image_paths = sorted(Path("receipts_pngs").glob("25*.png"))  # e.g., 25_page1.png, 25_page2.png
output_path = Path("receipts_pngs/25_processed.png")

# Merge, crop, resize
merged_path = merge_images_vertically(image_paths, output_path, max_width=1000)

print(f"Processed merged image saved to: {merged_path}")

# Optional: open the image to check
Image.open(merged_path).show()