import fitz # PyMuPDF
import os


def convert_pdfs_to_pngs(input_folder, output_folder, zoom=2):
    """
    Converts all PDFs in input_folder to PNGs in output_folder.
    Each page becomes a separate PNG.
    """
    # Create output folder if it doesn't exist
    os.makedirs(output_folder, exist_ok=True)

    for filename in os.listdir(input_folder):
        if filename.lower().endswith(".pdf"):
            pdf_path = os.path.join(input_folder, filename)
            doc = fitz.open(pdf_path)

            for page_num in range(len(doc)):
                page = doc[page_num]

                mat = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=mat)

                output_filename = f"{os.path.splitext(filename)[0]}_page{page_num + 1}.png"
                output_path = os.path.join(output_folder, output_filename)

                pix.save(output_path)

            doc.close()

    print(f"Done converting PDFs in '{input_folder}' to PNGs in '{output_folder}'")

