import argparse
import hashlib
import os
import re
from pathlib import Path

import chromadb
import pymupdf
import pytesseract
from pytesseract import Output
from sentence_transformers import SentenceTransformer

from config import (
    CHUNK_COLLECTION_NAME,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    DATABASE_FOLDER,
    DOCUMENTS_FOLDER,
    DOCUMENT_LANGUAGE_HINTS,
    EMBEDDING_MODEL,
    OCR_CANDIDATES,
    RECORD_COLLECTION_NAME,
)
from record_integrity import audit_document
from storage import (
    initialize_database,
    insert_record,
    update_document_integrity,
    upsert_document,
    upsert_page,
)
from table_ocr import extract_voter_roll_records, format_record, records_to_page_text, render_page
from text_utils import clean_ocr_text, detect_script, native_text_is_good


os.makedirs(DOCUMENTS_FOLDER, exist_ok=True)
os.makedirs(DATABASE_FOLDER, exist_ok=True)

parser = argparse.ArgumentParser(description="Index all PDFs or append one new PDF.")
parser.add_argument(
    "--source",
    help="Index only this PDF and preserve every existing SQLite and Chroma record.",
)
args = parser.parse_args()
incremental_source = Path(args.source).name if args.source else None
if args.source and str(args.source) != incremental_source:
    raise ValueError("--source must be a PDF filename, not a path")


def verify_tesseract_languages():
    installed = set(pytesseract.get_languages(config=""))
    required = {"eng", "deu", "ara", "hin", "kan", "tel"}
    missing = sorted(required - installed)
    if missing:
        print("\nWARNING: Missing Tesseract languages: " + ", ".join(missing))
        print("Documents using those languages may OCR poorly.\n")


verify_tesseract_languages()

sqlite_conn = initialize_database(clean=not bool(incremental_source))
existing_document_count = int(sqlite_conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
if incremental_source:
    already_indexed = sqlite_conn.execute(
        "SELECT 1 FROM documents WHERE source=?", (incremental_source,)
    ).fetchone()
    if already_indexed:
        sqlite_conn.close()
        raise ValueError(
            f"{incremental_source} is already indexed. Rename the PDF or run a deliberate full rebuild."
        )

client = chromadb.PersistentClient(path=DATABASE_FOLDER)
if incremental_source:
    try:
        chunk_collection = client.get_collection(CHUNK_COLLECTION_NAME)
        record_collection = client.get_collection(RECORD_COLLECTION_NAME)
    except Exception as error:
        if existing_document_count:
            sqlite_conn.close()
            raise RuntimeError(
                "Existing SQLite documents were found but their Chroma collections are missing. "
                "Run python ingest.py once to rebuild the complete index."
            ) from error
        chunk_collection = client.get_or_create_collection(
            name=CHUNK_COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
        )
        record_collection = client.get_or_create_collection(
            name=RECORD_COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
        )
else:
    for name in (CHUNK_COLLECTION_NAME, RECORD_COLLECTION_NAME):
        try:
            client.delete_collection(name)
        except Exception:
            pass
    chunk_collection = client.create_collection(
        name=CHUNK_COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )
    record_collection = client.create_collection(
        name=RECORD_COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )

print("\nLoading multilingual embedding model...")
embedding_model = SentenceTransformer(EMBEDDING_MODEL)


def split_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    words = text.split()
    chunks = []
    start = 0
    step = max(1, chunk_size - overlap)
    while start < len(words):
        chunk = " ".join(words[start:start + chunk_size]).strip()
        if chunk:
            chunks.append(chunk)
        start += step
    return chunks


def sha1_id(*parts):
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def script_bonus(text, languages):
    script = detect_script(text)
    expected = {
        "ara": "arabic",
        "hin": "devanagari",
        "kan": "kannada",
        "tel": "telugu",
    }
    for code, script_name in expected.items():
        if code in languages and script == script_name:
            return 15.0
    return 0.0


def whole_page_ocr(image, languages):
    data = pytesseract.image_to_data(
        image,
        lang=languages,
        config="--psm 6",
        output_type=Output.DICT,
    )
    words = []
    confs = []
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
            confs.append(value)
    final = clean_ocr_text(" ".join(words))
    confidence = sum(confs) / len(confs) if confs else 0.0
    return final, confidence


def choose_ocr_language(image):
    best = None
    # One quick pass per candidate. This only happens when there is no hint.
    for languages in OCR_CANDIDATES:
        try:
            text, confidence = whole_page_ocr(image, languages)
        except pytesseract.TesseractError:
            continue
        score = confidence + script_bonus(text, languages)
        if best is None or score > best["score"]:
            best = {
                "languages": languages,
                "text": text,
                "confidence": confidence,
                "score": score,
            }
    return best or {"languages": "eng", "text": "", "confidence": 0.0, "score": 0.0}


def add_page_chunks(source, page_number, page_text, metadata):
    chunks = split_text(page_text)
    if not chunks:
        return 0

    embeddings = embedding_model.encode(chunks, normalize_embeddings=True).tolist()
    ids = [sha1_id(source, page_number, index, chunk) for index, chunk in enumerate(chunks)]
    metadatas = []
    for index in range(len(chunks)):
        item = dict(metadata)
        item["source"] = source
        item["page"] = page_number
        item["chunk"] = index
        metadatas.append(item)

    chunk_collection.add(
        ids=ids,
        documents=chunks,
        embeddings=embeddings,
        metadatas=metadatas,
    )
    return len(chunks)


def add_structured_records(records):
    if not records:
        return
    documents = [format_record(record) for record in records]
    embeddings = embedding_model.encode(documents, normalize_embeddings=True).tolist()
    ids = [sha1_id(record["source"], record["page"], record["row_index"], record.get("serial_number", "")) for record in records]
    metadatas = []
    for record in records:
        metadatas.append(
            {
                "source": record["source"],
                "page": record["page"],
                "row_index": record["row_index"],
                "serial_number": record.get("serial_number", ""),
                "house_number": record.get("house_number", ""),
                "epic_id": record.get("epic_id", ""),
                "extraction_method": "table_ocr",
                "ocr_confidence": float(record.get("ocr_confidence", 0) or 0),
            }
        )

    record_collection.add(
        ids=ids,
        documents=documents,
        embeddings=embeddings,
        metadatas=metadatas,
    )


if incremental_source:
    if not incremental_source.lower().endswith(".pdf"):
        raise ValueError("--source must name a PDF file")
    source_path = Path(DOCUMENTS_FOLDER) / incremental_source
    if not source_path.is_file():
        raise FileNotFoundError(f"PDF not found: {source_path}")
    pdf_files = [incremental_source]
    print(f"INDEX_MODE incremental source={incremental_source}")
else:
    pdf_files = sorted(
        file for file in os.listdir(DOCUMENTS_FOLDER)
        if file.lower().endswith(".pdf")
    )
    print("INDEX_MODE full_rebuild")

if not pdf_files:
    raise FileNotFoundError("No PDF files were found inside the documents folder.")

for filename in pdf_files:
    path = os.path.join(DOCUMENTS_FOLDER, filename)
    pdf = pymupdf.open(path)
    hint = DOCUMENT_LANGUAGE_HINTS.get(filename, "")
    chosen_document_language = hint

    print("\n" + "=" * 70)
    print(f"PROCESSING: {filename}")
    print("=" * 70)

    upsert_document(sqlite_conn, filename, len(pdf), hint)

    for page_number, page in enumerate(pdf, start=1):
        native_text = page.get_text("text") or ""
        native_good = native_text_is_good(native_text)
        image = render_page(page)

        # Determine OCR language once per document when no explicit hint exists.
        if not chosen_document_language and not native_good:
            selected = choose_ocr_language(image)
            chosen_document_language = selected["languages"]
            print(f"Auto-selected OCR language for {filename}: {chosen_document_language}")

        languages = chosen_document_language or hint or "eng"

        # ----------------------------------------------------
        # 1) TABLE-AWARE OCR (preferred for broken/scanned tables)
        # ----------------------------------------------------
        records = []
        if not native_good:
            try:
                records = extract_voter_roll_records(
                    image=image,
                    languages=languages,
                    source=filename,
                    page_number=page_number,
                )
            except Exception as error:
                print(f"Page {page_number}: table OCR warning: {error}")
                records = []

        if records:
            page_text = records_to_page_text(records)
            confidence = sum(record["ocr_confidence"] for record in records) / len(records)
            extraction_method = "table_ocr"

            for record in records:
                insert_record(sqlite_conn, record)
            sqlite_conn.commit()
            add_structured_records(records)

            print(
                f"Page {page_number}: TABLE OCR [{languages}] "
                f"records={len(records)} confidence={confidence:.1f}"
            )

        # ----------------------------------------------------
        # 2) TRUSTWORTHY NATIVE TEXT
        # ----------------------------------------------------
        elif native_good:
            page_text = native_text.strip()
            confidence = 100.0
            extraction_method = "native"
            print(f"Page {page_number}: native text")

        # ----------------------------------------------------
        # 3) WHOLE-PAGE OCR FALLBACK
        # ----------------------------------------------------
        else:
            page_text, confidence = whole_page_ocr(image, languages)
            extraction_method = "ocr"
            print(
                f"Page {page_number}: whole-page OCR [{languages}] "
                f"confidence={confidence:.1f}"
            )

        if not page_text.strip():
            print(f"Page {page_number}: NO USABLE TEXT")
            continue

        upsert_page(
            sqlite_conn,
            filename,
            page_number,
            extraction_method,
            languages if extraction_method != "native" else "",
            confidence,
            page_text,
        )

        chunk_count = add_page_chunks(
            filename,
            page_number,
            page_text,
            {
                "extraction_method": extraction_method,
                "ocr_languages": languages if extraction_method != "native" else "",
                "ocr_confidence": float(confidence),
                "structured_records": len(records),
            },
        )
        print(f"          {chunk_count} chunks stored")

    pdf.close()
    integrity = audit_document(sqlite_conn, filename, refresh_expected=True)
    update_document_integrity(
        sqlite_conn,
        filename,
        integrity["expected_records"],
        integrity["index_status"],
    )
    print(
        f"INTEGRITY {filename} indexed={integrity['record_count']} "
        f"expected={integrity['expected_records']} missing={integrity['missing_count']} "
        f"status={integrity['index_status']}"
    )

sqlite_conn.close()

print("\n" + "=" * 70)
print("INCREMENTAL INGESTION COMPLETE" if incremental_source else "INGESTION COMPLETE")
print(f"Semantic chunks: {chunk_collection.count()}")
print(f"Structured records: {record_collection.count()}")
print("=" * 70)
