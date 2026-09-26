import argparse
import hashlib
import math
from datetime import datetime
from pathlib import Path

import chromadb
import pymupdf
from sentence_transformers import SentenceTransformer

from config import (
    CHUNK_COLLECTION_NAME, CHUNK_OVERLAP, CHUNK_SIZE, DATABASE_FOLDER,
    DOCUMENTS_FOLDER, EMBEDDING_MODEL, RECORD_COLLECTION_NAME,
)
from retriever import validate_record
from record_integrity import audit_document, numeric_serial
from storage import connect, initialize_database, insert_record, update_document_integrity, upsert_page
from table_ocr import extract_voter_roll_records, format_record, records_to_page_text, render_page


def _warning_count(records):
    return sum(len(validate_record(record)) for record in records)


def _average_confidence(records):
    values = [float(record["ocr_confidence"] or 0) for record in records]
    return sum(values) / len(values) if values else 0.0


def _is_safe_improvement(old_records, new_records):
    if not new_records:
        return False, "no structured records were detected"
    minimum_count = max(1, math.floor(len(old_records) * 0.90))
    if len(new_records) < minimum_count:
        return False, f"record count fell from {len(old_records)} to {len(new_records)}"
    old_serials = {
        numeric_serial(record["serial_number"]) for record in old_records
        if numeric_serial(record["serial_number"]) is not None
    }
    new_serials = {
        numeric_serial(record.get("serial_number")) for record in new_records
        if numeric_serial(record.get("serial_number")) is not None
    }
    lost_serials = sorted(old_serials - new_serials)
    if lost_serials:
        return False, f"would remove existing serials {lost_serials}"
    old_warnings = _warning_count(old_records)
    new_warnings = _warning_count(new_records)
    old_rate = old_warnings / max(1, len(old_records))
    new_rate = new_warnings / max(1, len(new_records))
    old_confidence = _average_confidence(old_records)
    new_confidence = _average_confidence(new_records)
    improved = new_rate < old_rate or (
        new_warnings <= old_warnings and new_confidence >= old_confidence + 2.0
    )
    if not improved:
        return False, (
            f"no measured improvement (warnings {old_warnings}->{new_warnings}, "
            f"confidence {old_confidence:.1f}->{new_confidence:.1f})"
        )
    return True, (
        f"warnings {old_warnings}->{new_warnings}, confidence "
        f"{old_confidence:.1f}->{new_confidence:.1f}"
    )


def _sha1_id(*parts):
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _split_text(text):
    words = text.split()
    chunks = []
    step = max(1, CHUNK_SIZE - CHUNK_OVERLAP)
    for start in range(0, len(words), step):
        chunk = " ".join(words[start:start + CHUNK_SIZE]).strip()
        if chunk:
            chunks.append(chunk)
    return chunks


def _where(source, page):
    return {"$and": [{"source": source}, {"page": int(page)}]}


def _refresh_collection(collection, source, page, ids, documents, metadatas, embeddings):
    existing = set(collection.get(where=_where(source, page), include=[]).get("ids", []))
    if ids:
        collection.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)
    stale = sorted(existing - set(ids))
    if stale:
        collection.delete(ids=stale)


def _review_pages(connection, source_filter=None):
    records = list(connection.execute("SELECT * FROM records ORDER BY source, page, row_index"))
    verified_pages = {
        (str(record["source"]), int(record["page"]))
        for record in records
        if str(record["verification_status"] or "") == "verified"
    }
    pages = {}
    for record in records:
        if source_filter and str(record["source"]).casefold() != source_filter.casefold():
            continue
        # Never overwrite a page after a person has verified any row on it.
        # Remaining flagged rows on that page stay available in the manual queue.
        if (str(record["source"]), int(record["page"])) in verified_pages:
            continue
        if validate_record(record):
            pages.setdefault((str(record["source"]), int(record["page"])), None)
    return sorted(pages)


def main():
    parser = argparse.ArgumentParser(description="Safely re-OCR only pages containing Review records.")
    parser.add_argument("--apply", action="store_true", help="Apply only candidates that measurably improve quality.")
    parser.add_argument("--source", help="Limit review to one indexed PDF filename.")
    parser.add_argument("--max-pages", type=int, default=0, help="Optional processing limit; 0 means all review pages.")
    args = parser.parse_args()

    initialize_database(clean=False).close()
    connection = connect()
    review_pages = _review_pages(connection, args.source)
    if args.max_pages > 0:
        review_pages = review_pages[:args.max_pages]
    print(f"REVIEW_PAGES {len(review_pages)}")
    if not review_pages:
        connection.close()
        print("REVIEW_COMPLETE accepted=0 skipped=0")
        return 0

    if args.apply:
        backup_path = Path(DATABASE_FOLDER) / f"structured_before_review_{datetime.now():%Y%m%d_%H%M%S}.db"
        backup_connection = __import__("sqlite3").connect(backup_path)
        connection.backup(backup_connection)
        backup_connection.close()
        print(f"BACKUP {backup_path}")

    document_folder = Path(DOCUMENTS_FOLDER)
    accepted = 0
    skipped = 0
    affected_sources = set()
    semantic = None
    record_collection = None
    chunk_collection = None
    open_documents = {}

    try:
        for position, (source, page_number) in enumerate(review_pages, start=1):
            print(f"PROGRESS {position}/{len(review_pages)} {source} page {page_number}")
            path = document_folder / source
            if not path.is_file():
                print(f"SKIP {source} page {page_number}: PDF file not found")
                skipped += 1
                continue
            if source not in open_documents:
                open_documents[source] = pymupdf.open(path)
            document = open_documents[source]
            if not 1 <= page_number <= len(document):
                print(f"SKIP {source} page {page_number}: page outside PDF")
                skipped += 1
                continue
            language_row = connection.execute(
                "SELECT ocr_languages FROM pages WHERE source=? AND page=?", (source, page_number)
            ).fetchone()
            languages = str(language_row["ocr_languages"] if language_row else "") or "tel+eng"
            old_records = list(connection.execute(
                "SELECT * FROM records WHERE source=? AND page=? ORDER BY row_index", (source, page_number)
            ))
            image = render_page(document[page_number - 1])
            try:
                new_records = extract_voter_roll_records(
                    image=image, languages=languages, source=source, page_number=page_number
                )
            except Exception as error:
                print(f"SKIP {source} page {page_number}: OCR error: {error}")
                skipped += 1
                continue
            better, reason = _is_safe_improvement(old_records, new_records)
            if not better:
                print(f"SKIP {source} page {page_number}: {reason}")
                skipped += 1
                continue
            print(f"CANDIDATE {source} page {page_number}: {reason}")
            if not args.apply:
                accepted += 1
                continue

            if semantic is None:
                semantic = SentenceTransformer(EMBEDDING_MODEL)
                client = chromadb.PersistentClient(path=DATABASE_FOLDER)
                record_collection = client.get_collection(RECORD_COLLECTION_NAME)
                chunk_collection = client.get_collection(CHUNK_COLLECTION_NAME)

            record_documents = [format_record(record) for record in new_records]
            record_ids = [
                _sha1_id(record["source"], record["page"], record["row_index"], record.get("serial_number", ""))
                for record in new_records
            ]
            record_metadatas = [{
                "source": record["source"], "page": record["page"], "row_index": record["row_index"],
                "serial_number": record.get("serial_number", ""), "house_number": record.get("house_number", ""),
                "epic_id": record.get("epic_id", ""), "extraction_method": "table_ocr_review",
                "ocr_confidence": float(record.get("ocr_confidence", 0) or 0),
            } for record in new_records]
            record_embeddings = semantic.encode(record_documents, normalize_embeddings=True).tolist()

            page_text = records_to_page_text(new_records)
            chunks = _split_text(page_text)
            chunk_ids = [_sha1_id(source, page_number, index, chunk) for index, chunk in enumerate(chunks)]
            chunk_metadatas = [{
                "source": source, "page": page_number, "chunk": index,
                "extraction_method": "table_ocr_review", "ocr_languages": languages,
                "ocr_confidence": _average_confidence(new_records), "structured_records": len(new_records),
            } for index, _ in enumerate(chunks)]
            chunk_embeddings = semantic.encode(chunks, normalize_embeddings=True).tolist() if chunks else []

            _refresh_collection(
                record_collection, source, page_number, record_ids, record_documents,
                record_metadatas, record_embeddings,
            )
            _refresh_collection(
                chunk_collection, source, page_number, chunk_ids, chunks,
                chunk_metadatas, chunk_embeddings,
            )
            try:
                connection.execute("DELETE FROM records WHERE source=? AND page=?", (source, page_number))
                for record in new_records:
                    insert_record(connection, record)
                upsert_page(
                    connection, source, page_number, "table_ocr_review", languages,
                    _average_confidence(new_records), page_text,
                )
                connection.commit()
                accepted += 1
                affected_sources.add(source)
                print(f"APPLIED {source} page {page_number}")
            except Exception:
                connection.rollback()
                raise
    finally:
        for document in open_documents.values():
            document.close()
        for source in sorted(affected_sources):
            integrity = audit_document(connection, source, refresh_expected=False)
            update_document_integrity(
                connection,
                source,
                integrity["expected_records"],
                integrity["index_status"],
            )
        connection.close()

    print(f"REVIEW_COMPLETE accepted={accepted} skipped={skipped} mode={'apply' if args.apply else 'dry-run'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
