import io
import os

import pymupdf
import pytesseract
from PIL import Image


# Tell Python where Tesseract is installed
pytesseract.pytesseract.tesseract_cmd = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)


# Find the first PDF in documents/
pdf_files = [
    f for f in os.listdir("documents")
    if f.lower().endswith(".pdf")
]

if not pdf_files:
    raise FileNotFoundError(
        "No PDF found inside the documents folder."
    )


pdf_path = os.path.join(
    "documents",
    pdf_files[0]
)

print(f"Testing PDF: {pdf_path}")


# Open PDF
pdf = pymupdf.open(pdf_path)

# Test page 1
page = pdf[0]


# Render page at approximately 300 DPI
zoom = 300 / 72

pix = page.get_pixmap(
    matrix=pymupdf.Matrix(zoom, zoom),
    alpha=False
)


# Convert rendered page to an image
image = Image.open(
    io.BytesIO(
        pix.tobytes("png")
    )
)


# OCR using English + Telugu for this first test
text = pytesseract.image_to_string(
    image,
    lang="eng+tel",
    config="--psm 6"
)


print("\n---------- OCR RESULT ----------\n")

print(text)

print("\n-------------------------------")


pdf.close()