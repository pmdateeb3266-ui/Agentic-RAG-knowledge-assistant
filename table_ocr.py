import io
import re
import shutil

import cv2
import numpy as np
import pymupdf
import pytesseract
from PIL import Image, ImageOps
from pytesseract import Output

from config import (
    CELL_PADDING,
    OCR_DPI,
    TABLE_LINE_KERNEL_PIXELS,
    TABLE_MIN_HORIZONTAL_LINES,
    TABLE_MIN_VERTICAL_LINES,
    TABLE_PROJECTION_RATIO,
    VOTER_ROLL_COLUMNS,
)
from text_utils import clean_identifier, clean_numeric, clean_ocr_text


def configure_tesseract():
    path = shutil.which("tesseract")
    if not path:
        windows = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        import os
        if os.path.exists(windows):
            path = windows
    if not path:
        raise FileNotFoundError("Tesseract was not found. Install Tesseract OCR first.")
    pytesseract.pytesseract.tesseract_cmd = path


configure_tesseract()


def render_page(page, dpi=OCR_DPI):
    zoom = dpi / 72.0
    matrix = pymupdf.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")


def _group_positions(indices, max_gap=2):
    indices = list(indices)
    if not indices:
        return []
    groups = []
    start = previous = indices[0]
    for value in indices[1:]:
        if value > previous + max_gap:
            groups.append((start, previous))
            start = value
        previous = value
    groups.append((start, previous))
    return [int((a + b) / 2) for a, b in groups]


def detect_table_grid(image):
    """Return major vertical and horizontal grid-line coordinates."""
    gray = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
    binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

    height, width = gray.shape
    horizontal_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(TABLE_LINE_KERNEL_PIXELS, width // 45), 1)
    )
    vertical_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (1, max(TABLE_LINE_KERNEL_PIXELS, height // 45))
    )

    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel)

    horizontal_projection = (horizontal > 0).sum(axis=1)
    vertical_projection = (vertical > 0).sum(axis=0)

    min_h = max(80, int(width * TABLE_PROJECTION_RATIO))
    min_v = max(80, int(height * TABLE_PROJECTION_RATIO))

    y_indices = np.where(horizontal_projection >= min_h)[0]
    x_indices = np.where(vertical_projection >= min_v)[0]

    ys = _group_positions(y_indices)
    xs = _group_positions(x_indices)

    # Remove very close duplicate lines caused by thick borders.
    def dedupe(values, gap=8):
        out = []
        for value in values:
            if not out or value - out[-1] > gap:
                out.append(value)
            else:
                out[-1] = int((out[-1] + value) / 2)
        return out

    xs = dedupe(xs)
    ys = dedupe(ys)

    if len(xs) < TABLE_MIN_VERTICAL_LINES or len(ys) < TABLE_MIN_HORIZONTAL_LINES:
        return None

    return {"x_lines": xs, "y_lines": ys}


def preprocess_cell(cell, upscale=True):
    gray = ImageOps.grayscale(cell)
    # Upscale small text a little; page is already rendered at 300 DPI.
    if upscale and gray.width < 500:
        gray = gray.resize((gray.width * 2, gray.height * 2), Image.Resampling.LANCZOS)
    arr = np.array(gray)
    arr = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    return Image.fromarray(arr)


def ocr_cell(cell, languages, psm=7, whitelist=None, upscale=True):
    cell = preprocess_cell(cell, upscale=upscale)
    config = f"--psm {psm}"
    if whitelist:
        config += f" -c tessedit_char_whitelist={whitelist}"

    data = pytesseract.image_to_data(
        cell,
        lang=languages,
        config=config,
        output_type=Output.DICT,
    )

    words = []
    confidences = []
    for text, conf in zip(data["text"], data["conf"]):
        text = clean_ocr_text(text)
        if not text:
            continue
        words.append(text)
        try:
            value = float(conf)
        except Exception:
            value = -1
        if value >= 0:
            confidences.append(value)

    final = clean_ocr_text(" ".join(words))
    confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return final, confidence


def _crop_cell(image, x1, y1, x2, y2):
    left = min(x2 - 1, x1 + CELL_PADDING)
    top = min(y2 - 1, y1 + CELL_PADDING)
    right = max(left + 1, x2 - CELL_PADDING)
    bottom = max(top + 1, y2 - CELL_PADDING)
    return image.crop((left, top, right, bottom))


def _normalize_grid_for_eight_columns(grid):
    xs = grid["x_lines"]
    ys = grid["y_lines"]

    # Prefer a run of exactly 9 strong vertical lines. Many electoral-roll
    # pages have one 8-column table and little else. If extra vertical lines
    # exist, choose the 9-line window spanning the largest coherent width.
    if len(xs) < VOTER_ROLL_COLUMNS + 1:
        return None

    if len(xs) == VOTER_ROLL_COLUMNS + 1:
        selected_xs = xs
    else:
        candidates = []
        for i in range(0, len(xs) - VOTER_ROLL_COLUMNS):
            window = xs[i:i + VOTER_ROLL_COLUMNS + 1]
            gaps = [b - a for a, b in zip(window, window[1:])]
            if min(gaps) < 35:
                continue
            candidates.append((window[-1] - window[0], window))
        if not candidates:
            return None
        selected_xs = max(candidates, key=lambda item: item[0])[1]

    return {"x_lines": selected_xs, "y_lines": ys}


def _serial_from_cell(cell):
    """Read a serial cell with a conservative second pass when needed."""
    attempts = [
        ocr_cell(cell, "eng", psm=7, whitelist="0123456789", upscale=False),
        ocr_cell(cell, "eng", psm=6, whitelist="0123456789", upscale=True),
    ]
    candidates = []
    for text, confidence in attempts:
        serial = clean_numeric(text)
        if not serial or len(serial) > 5:
            continue
        try:
            number = int(serial)
        except ValueError:
            continue
        if number > 0:
            candidates.append((confidence, serial))
    return max(candidates, default=(0.0, ""), key=lambda item: item[0])


def extract_serial_numbers(image):
    """Extract serials from a voter table without depending on other fields."""
    grid = detect_table_grid(image)
    if not grid:
        return []
    grid = _normalize_grid_for_eight_columns(grid)
    if not grid:
        return []
    xs = grid["x_lines"]
    serials = []
    for y1, y2 in zip(grid["y_lines"], grid["y_lines"][1:]):
        if y2 - y1 < 22:
            continue
        serial_cell = _crop_cell(image, xs[0], y1, xs[1], y2)
        _, serial = _serial_from_cell(serial_cell)
        if serial:
            serials.append(int(serial))
    return sorted(set(serials))


def _looks_like_column_number_row(cells):
    markers = 0
    for index, value in enumerate(cells, start=1):
        compact = re.sub(r"[^0-9]", "", str(value or ""))
        if compact == str(index):
            markers += 1
    return markers >= 3


def valid_house_number(value):
    """Accept real-world house identifiers without assuming one punctuation style."""
    value = re.sub(r"\s+", "", str(value or ""))
    placeholder = bool(re.fullmatch(r"[-_–—]+", value))
    return bool(
        value
        and len(value) <= 40
        and (re.search(r"[0-9A-Za-z]", value) or placeholder)
    )


def extract_voter_roll_records(image, languages, source, page_number):
    grid = detect_table_grid(image)
    if not grid:
        return []

    grid = _normalize_grid_for_eight_columns(grid)
    if not grid:
        return []

    xs = grid["x_lines"]
    ys = grid["y_lines"]
    records = []

    # Every pair of horizontal lines is a candidate row. Headers are rejected
    # because the first cell does not OCR to a plain serial number.
    for band_index, (y1, y2) in enumerate(zip(ys, ys[1:])):
        if y2 - y1 < 22:
            continue

        serial_cell = _crop_cell(image, xs[0], y1, xs[1], y2)
        serial_conf, serial = _serial_from_cell(serial_cell)

        if not serial or len(serial) > 5:
            continue
        try:
            serial_num = int(serial)
        except ValueError:
            continue
        if serial_num <= 0:
            continue

        cells = []
        cell_confs = [serial_conf]
        for col_index, (x1, x2) in enumerate(zip(xs, xs[1:])):
            cell = _crop_cell(image, x1, y1, x2, y2)

            if col_index == 0:
                text, conf = serial, serial_conf
            elif col_index == 1:
                text, conf = ocr_cell(
                    cell,
                    "eng",
                    psm=7,
                    whitelist="0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-/*",
                    upscale=False,
                )
            elif col_index in {2, 3, 4, 5}:
                text, conf = ocr_cell(cell, languages, psm=7)
            elif col_index == 6:
                text, conf = ocr_cell(cell, "eng", psm=7, whitelist="0123456789", upscale=False)
            else:
                text, conf = ocr_cell(
                    cell,
                    "eng",
                    psm=7,
                    whitelist="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789/-",
                    upscale=False,
                )

            cells.append(clean_ocr_text(text))
            cell_confs.append(conf)

        if len(cells) != 8:
            continue

        if _looks_like_column_number_row(cells):
            continue

        house_number = re.sub(r"\s+", "", cells[1])
        if not valid_house_number(house_number):
            continue

        voter_name = cells[2]
        relationship = cells[3]
        related_name = cells[4]
        gender = cells[5]
        age = clean_numeric(cells[6])
        epic_id = clean_identifier(cells[7])

        # Some Telugu name cells need a multi-line segmentation pass even
        # when every numeric/categorical cell was read correctly.
        if not voter_name and not related_name:
            voter_name = clean_ocr_text(ocr_cell(
                _crop_cell(image, xs[2], y1, xs[3], y2),
                languages,
                psm=6,
                upscale=True,
            )[0])
            related_name = clean_ocr_text(ocr_cell(
                _crop_cell(image, xs[4], y1, xs[5], y2),
                languages,
                psm=6,
                upscale=True,
            )[0])

        # Keep a structurally complete voter row even when OCR cannot read
        # either name. The row remains quality-flagged instead of disappearing.
        structural_signals = sum(bool(value) for value in (
            house_number, relationship, gender, age, epic_id
        ))
        if not voter_name and not related_name and structural_signals < 3:
            continue
        if age and len(age) > 3:
            age = ""

        confidence_values = [c for c in cell_confs if c >= 0]
        confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0

        records.append(
            {
                "source": source,
                "page": page_number,
                "row_index": band_index,
                "serial_number": serial,
                "house_number": house_number,
                "voter_name": voter_name,
                "relationship": relationship,
                "related_name": related_name,
                "gender": gender,
                "age": age,
                "epic_id": epic_id,
                "ocr_confidence": confidence,
                "bbox": [xs[0], y1, xs[-1], y2],
            }
        )

    return records


def format_record(record):
    fields = [
        f"Serial: {record.get('serial_number', '')}",
        f"House No: {record.get('house_number', '')}",
        f"Voter Name: {record.get('voter_name', '')}",
        f"Relationship: {record.get('relationship', '')}",
        f"Related Name: {record.get('related_name', '')}",
        f"Gender: {record.get('gender', '')}",
        f"Age: {record.get('age', '')}",
        f"EPIC: {record.get('epic_id', '')}",
    ]
    return " | ".join(fields)


def records_to_page_text(records):
    return "\n".join(format_record(record) for record in records)
