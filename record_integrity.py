from bisect import bisect_left
from collections import Counter
from pathlib import Path

import pymupdf

from config import DOCUMENTS_FOLDER
from table_ocr import extract_serial_numbers, render_page


def numeric_serial(value):
    text = str(value or "").strip()
    return int(text) if text.isdigit() and int(text) > 0 else None


def compact_ranges(values):
    numbers = sorted(set(int(value) for value in values))
    if not numbers:
        return []
    output = []
    start = previous = numbers[0]
    for number in numbers[1:]:
        if number != previous + 1:
            output.append((start, previous))
            start = number
        previous = number
    output.append((start, previous))
    return output


def format_ranges(values):
    return ",".join(
        str(start) if start == end else f"{start}-{end}"
        for start, end in compact_ranges(values)
    )


def infer_expected_records(pdf_path, tail_pages=3):
    """Infer a roll's expected total from serials printed on its final pages."""
    path = Path(pdf_path)
    if not path.is_file():
        return 0
    found = []
    with pymupdf.open(path) as document:
        first = max(0, len(document) - max(1, int(tail_pages)))
        for page_index in range(first, len(document)):
            try:
                found.extend(extract_serial_numbers(render_page(document[page_index])))
            except Exception:
                continue
    return max(found, default=0)


def _indexed_serials(connection, source):
    serials = []
    for row in connection.execute(
        "SELECT serial_number FROM records WHERE source=?", (source,)
    ):
        value = numeric_serial(row["serial_number"])
        if value is not None:
            serials.append(value)
    return serials


def _candidate_pages(connection, source, page_count, missing, indexed_serials):
    page_rows = list(connection.execute(
        """
        SELECT p.page, p.extraction_method, COUNT(r.id) AS record_count
        FROM pages p
        LEFT JOIN records r ON r.source=p.source AND r.page=p.page
        WHERE p.source=?
        GROUP BY p.page, p.extraction_method
        ORDER BY p.page
        """,
        (source,),
    ))
    positive_counts = [
        int(row["record_count"])
        for row in page_rows
        if int(row["record_count"]) > 0
    ]
    modal_count = Counter(positive_counts).most_common(1)[0][0] if positive_counts else 0
    candidates = {
        int(row["page"])
        for row in page_rows
        if int(row["page"]) > 1 and (
            int(row["record_count"]) == 0
            or (modal_count and int(row["record_count"]) < modal_count)
            or not str(row["extraction_method"] or "").startswith("table_ocr")
        )
    }
    if page_count > 1:
        candidates.add(page_count)

    known = sorted(set(indexed_serials))
    serial_pages = {}
    for row in connection.execute(
        "SELECT serial_number, page FROM records WHERE source=? ORDER BY CAST(serial_number AS INTEGER)",
        (source,),
    ):
        serial = numeric_serial(row["serial_number"])
        if serial is not None:
            serial_pages.setdefault(serial, int(row["page"]))

    for start, end in compact_ranges(missing):
        left = bisect_left(known, start)
        before = known[left - 1] if left > 0 else None
        right = bisect_left(known, end + 1)
        after = known[right] if right < len(known) else None
        if before is not None and after is not None:
            first_page, last_page = serial_pages[before], serial_pages[after]
        elif after is not None:
            first_page, last_page = 2, serial_pages[after]
        elif before is not None:
            first_page, last_page = serial_pages[before], page_count
        else:
            first_page, last_page = 2, page_count
        candidates.update(range(max(2, first_page), min(page_count, last_page) + 1))
    return sorted(page for page in candidates if 1 <= page <= page_count)


def audit_document(
    connection,
    source,
    refresh_expected=False,
    documents_folder=DOCUMENTS_FOLDER,
):
    document = connection.execute(
        "SELECT source, page_count, expected_records, index_status FROM documents WHERE source=?",
        (source,),
    ).fetchone()
    if not document:
        raise ValueError(f"Document is not indexed: {source}")
    page_count = int(document["page_count"] or 0)
    indexed_serials = _indexed_serials(connection, source)
    record_count = int(connection.execute(
        "SELECT COUNT(*) FROM records WHERE source=?", (source,)
    ).fetchone()[0])
    expected = int(document["expected_records"] or 0)
    if refresh_expected or expected <= 0:
        inferred = infer_expected_records(Path(documents_folder) / source)
        expected = max(inferred, max(indexed_serials, default=0))
    missing = (
        sorted(set(range(1, expected + 1)) - set(indexed_serials))
        if expected else []
    )
    duplicate_count = sum(
        count - 1 for count in Counter(indexed_serials).values() if count > 1
    )
    if expected <= 0:
        status = "not_applicable"
    elif not missing and duplicate_count == 0 and record_count == expected:
        status = "complete"
    else:
        status = "incomplete"
    return {
        "source": source,
        "page_count": page_count,
        "record_count": record_count,
        "expected_records": expected,
        "missing_count": len(missing),
        "missing_serials": missing,
        "missing_ranges": format_ranges(missing),
        "duplicate_count": duplicate_count,
        "index_status": status,
        "candidate_pages": _candidate_pages(
            connection, source, page_count, missing, indexed_serials
        ) if status == "incomplete" else [],
    }


def audit_all_documents(
    connection,
    source=None,
    refresh_expected=False,
    documents_folder=DOCUMENTS_FOLDER,
):
    if source:
        sources = [Path(source).name]
    else:
        sources = [
            str(row["source"])
            for row in connection.execute("SELECT source FROM documents ORDER BY source")
        ]
    return [
        audit_document(connection, item, refresh_expected, documents_folder)
        for item in sources
    ]
