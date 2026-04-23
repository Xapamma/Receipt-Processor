import easyocr
from pathlib import Path
import warnings


warnings.filterwarnings("ignore", message=".*pin_memory.*")

reader = easyocr.Reader(['en'], gpu=False)

def extract_text_from_image(image_path, y_tol=15):
    """
    Run OCR on one image and return line-ordered text.

    Args:
    - image_path: Path to an image file (PNG/JPG/JPEG).
    - y_tol: Vertical tolerance used to group OCR tokens into the same line.

    Returns:
    - String containing extracted text with one reconstructed line per row.
    """
    results = reader.readtext(str(image_path))

    # Sort by top-left y, then x (top to bottom, left to right)
    results.sort(key=lambda x: (x[0][0][1], x[0][0][0]))
    
    lines = []
    
    for bbox, text, conf in results:
        y = bbox[0][1]  # top-left y
        
        # Find a line that this word belongs to
        added = False
        for line in lines:
            # line = [average_y, [(x, word), ...]]
            avg_y = line[0]
            if abs(y - avg_y) <= y_tol:
                # Add word to this line
                line[1].append((bbox[0][0], text))  # store x and text
                # update average y
                line[0] = (avg_y * len(line[1]) + y) / (len(line[1]) + 1)
                added = True
                break
        if not added:
            # Start a new line
            lines.append([y, [(bbox[0][0], text)]])
    
    # Now build text lines, sorting words by x (left-to-right)
    final_lines = []
    for avg_y, words in lines:
        words.sort(key=lambda x: x[0])  # sort by x-coordinate
        final_lines.append(" ".join([w[1] for w in words]))
    
    full_text = "\n".join(final_lines)
    
    #full_text = fix_prices_safe(full_text)
    
    return full_text

def process_image_folder(input_folder, output_folder):
    """
    OCR all PNG images in a folder and write one text file per image.

    Args:
    - input_folder: Directory containing `.png` images.
    - output_folder: Directory where `.txt` OCR outputs are written.

    Side effects:
    - Creates `output_folder` when missing.
    - Skips files whose output `.txt` already exists.
    """
    input_folder = Path(input_folder)
    output_folder = Path(output_folder)

    output_folder.mkdir(parents=True, exist_ok=True)

    all_images = list(input_folder.glob("*.png"))

    to_process = []

    # Build list of images that still need processing
    for image_path in all_images:
        output_file = output_folder / (image_path.stem + ".txt")

        # Skip if already processed
        if output_file.exists():
            continue

        to_process.append(image_path)

    skipped = len(all_images) - len(to_process)
    if skipped > 0:
        print(f"Skipping {skipped} already processed images...")

    if not to_process:
        print("Everything is already up to date!")
        return

    for image_path in to_process:
        print(f"Processing: {image_path.name}")

        try:
            text = extract_text_from_image(image_path)

            output_file = output_folder / (image_path.stem + ".txt")

            with open(output_file, "w", encoding="utf-8") as f:
                f.write(text)

        except Exception as e:
            print(f"Error processing {image_path.name}: {e}")

    print("✅ Done processing all images.")
