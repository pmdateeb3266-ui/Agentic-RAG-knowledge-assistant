import argparse
import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

import chromadb
import pymupdf
from sentence_transformers import SentenceTransformer

from config import (
    CHUNK_COLLECTION_NAME,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    DATABASE_FOLDER,
    DOCUMENTS_FOLDER,
    EMBEDDING_MODEL,
    RECORD_COLLECTION_NAME,
)
from record_integrity import audit_all_documents, numeric_serial
from storage import initialize_database, insert_record, update_document_integrity
from table_ocr import extract_voter_roll_records, format_record, records_to_page_text, render_page


def _sha1_id(*parts):
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _split_text(text):
    words = text.split()
    step = max(1, CHUNK_SIZE - CHUNK_OVERLAP)
    return [
        " ".join(words[start:start + CHUNK_SIZE]).strip()
        for start in range(0, len(words), step)
        if " ".join(words[start:start + CHUNK_SIZE]).strip()
    ]


def _where(source, page):
    return {"$and": [{"source": source}, {"page": int(page)}]}


def _refresh_collection(collection, source, page, ids, documents, metadatas, embeddings):
    existing = set(collection.get(where=_where(source, page), include=[]).get("ids", []))
    if ids:
        collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=embeddings,
        )
    stale = sorted(existing - set(ids))
    if stale:
        collection.delete(ids=stale)


def _document_language(connection, source):
    row = connection.execute(
        "SELECT language_hint FROM documents WHERE source=?", (source,)
    ).fetchone()
    if row and str(row["language_hint"] or "").strip():
        return str(row["language_hint"]).strip()
    row = connection.execute(
        """
        SELECT ocr_languages, COUNT(*) AS uses
        FROM pages WHERE source=? AND ocr_languages<>''
        GROUP BY ocr_languages ORDER BY uses DESC LIMIT 1
        """,
        (source,),
    ).fetchone()
    return str(row["ocr_languages"] if row else "") or "tel+eng"


def _unique_row_index(record, used):
    candidate = int(record.get("row_index", 0) or 0)
    if candidate not in used:
        used.add(candidate)
        return candidate
    candidate = max(used, default=0) + 1
    while candidate in used:
        candidate += 1
    used.add(candidate)
    return candidate


def _refresh_page_vectors(model, record_collection, chunk_collection, source, page, records, languages):
    record_documents = [format_record(record) for record in records]
    record_ids = [
        _sha1_id(source, page, record.get("row_index", 0), record.get("serial_number", ""))
        for record in records
    ]
    record_metadata = [{
        "source": source,
        "page": int(page),
        "row_index": int(record.get("row_index", 0) or 0),
        "serial_number": record.get("serial_number", ""),
        "house_number": record.get("house_number", ""),
        "epic_id": record.get("epic_id", ""),
        "extraction_method": "table_ocr_recovery",
        "ocr_confidence": float(record.get("ocr_confidence", 0) or 0),
    } for record in records]
    record_embeddings = model.encode(
        record_documents, normalize_embeddings=True
    ).tolist() if record_documents else []
    _refresh_collection(
        record_collection,
        source,
        page,
        record_ids,
        record_documents,
        record_metadata,
        record_embeddings,
    )

    page_text = records_to_page_text(records)
    chunks = _split_text(page_text)
    average = sum(float(item.get("ocr_confidence", 0) or 0) for item in records) / max(1, len(records))
    chunk_ids = [_sha1_id(source, page, index, chunk) for index, chunk in enumerate(chunks)]
    chunk_metadata = [{
        "source": source,
        "page": int(page),
        "chunk": index,
        "extraction_method": "table_ocr_recovery",
        "ocr_languages": languages,
        "ocr_confidence": average,
        "structured_records": len(records),
    } for index, _ in enumerate(chunks)]
    chunk_embeddings = model.encode(chunks, normalize_embeddings=True).tolist() if chunks else []
    _refresh_collection(
        chunk_collection,
        source,
        page,
        chunk_ids,
        chunks,
        chunk_metadata,
        chunk_embeddings,
    )
    return page_text, average


def main():
    parser = argparse.ArgumentParser(
        description="Audit voter-roll serials and recover only missing records."
    )
    parser.add_argument("--apply", action="store_true", help="Apply recovered records.")
    parser.add_argument("--source", help="Limit recovery to one indexed PDF filename.")
    parser.add_argument("--max-pages", type=int, default=0, help="Optional page limit for diagnostics.")
    args = parser.parse_args()

    connection = initialize_database(clean=False)
    reports = audit_all_documents(
        connection,
        source=args.source,
        refresh_expected=True,
    )
    for report in reports:
        update_document_integrity(
            connection,
            report["source"],
            report["expected_records"],
            report["index_status"],
        )
        print(
            f"INTEGRITY {report['source']} indexed={report['record_count']} "
            f"expected={report['expected_records']} missing={report['missing_count']}"
        )

    work = [
        (report, page)
        for report in reports
        if report["index_status"] == "incomplete"
        for page in report["candidate_pages"]
    ]
    if args.max_pages > 0:
        work = work[:args.max_pages]
    print(f"RECOVERY_DOCUMENTS {sum(1 for item in reports if item['index_status'] == 'incomplete')}")
    print(f"RECOVERY_PAGES {len(work)}")
    if not args.apply:
        for report in reports:
            if report["missing_count"]:
                print(
                    f"DRY_RUN {report['source']} missing={report['missing_count']} "
                    f"ranges={report['missing_ranges']} pages={report['candidate_pages']}"
                )
        connection.close()
        print("RECOVERY_COMPLETE added=0 remaining=" + str(sum(item["missing_count"] for item in reports)))
        return 0
    if not work:
        connection.close()
        print("RECOVERY_COMPLETE added=0 remaining=0")
        return 0

    backup_path = Path(DATABASE_FOLDER) / f"structured_before_recovery_{datetime.now():%Y%m%d_%H%M%S}.db"
    backup_connection = sqlite3.connect(backup_path)
    connection.backup(backup_connection)
    backup_connection.close()
    print(f"BACKUP {backup_path}")

    model = SentenceTransformer(EMBEDDING_MODEL)
    client = chromadb.PersistentClient(path=DATABASE_FOLDER)
    record_collection = client.get_collection(RECORD_COLLECTION_NAME)
    chunk_collection = client.get_collection(CHUNK_COLLECTION_NAME)
    missing_by_source = {
        report["source"]: set(report["missing_serials"])
        for report in reports
    }
    added = 0
    open_documents = {}
    document_folder = Path(DOCUMENTS_FOLDER)
    try:
        for position, (report, page_number) in enumerate(work, start=1):
            source = report["source"]
            print(f"PROGRESS {position}/{len(work)} {source} page {page_number}")
            path = document_folder / source
            if not path.is_file():
                print(f"SKIP {source} page {page_number}: PDF file not found")
                continue
            if source not in open_documents:
                open_documents[source] = pymupdf.open(path)
            document = open_documents[source]
            if not 1 <= page_number <= len(document):
                continue
            languages = _document_language(connection, source)
            try:
                extracted = extract_voter_roll_records(
                    render_page(document[page_number - 1]),
                    languages,
                    source,
                    page_number,
                )
            except Exception as error:
                print(f"SKIP {source} page {page_number}: OCR error: {error}")
                continue
            existing = [
                dict(row) for row in connection.execute(
                    "SELECT * FROM records WHERE source=? AND page=? ORDER BY row_index",
                    (source, page_number),
                )
            ]
            existing_serials = {
                numeric_serial(row.get("serial_number")) for row in existing
            }
            used_rows = {int(row.get("row_index", 0) or 0) for row in existing}
            recovered = []
            for record in extracted:
                serial = numeric_serial(record.get("serial_number"))
                if (
                    serial is None
                    or serial not in missing_by_source[source]
                    or serial in existing_serials
                ):
                    continue
                record["row_index"] = _unique_row_index(record, used_rows)
                recovered.append(record)
                existing_serials.add(serial)
            if not recovered:
                continue

            combined = existing + recovered
            combined.sort(key=lambda item: numeric_serial(item.get("serial_number")) or 10**9)
            page_text, average = _refresh_page_vectors(
                model,
                record_collection,
                chunk_collection,
                source,
                page_number,
                combined,
                languages,
            )
            try:
                for record in recovered:
                    insert_record(connection, record)
                    missing_by_source[source].discard(numeric_serial(record.get("serial_number")))
                connection.execute(
                    """
                    INSERT INTO pages(
                        source, page, extraction_method, ocr_languages,
                        ocr_confidence, page_text
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source, page) DO UPDATE SET
                        extraction_method=excluded.extraction_method,
                        ocr_languages=excluded.ocr_languages,
                        ocr_confidence=excluded.ocr_confidence,
                        page_text=excluded.page_text
                    """,
                    (
                        source,
                        page_number,
                        "table_ocr_recovery",
                        languages,
                        average,
                        page_text,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            added += len(recovered)
            print(f"APPLIED {source} page {page_number} added={len(recovered)}")
    finally:
        for document in open_documents.values():
            document.close()

    final_reports = audit_all_documents(connection, source=args.source, refresh_expected=False)
    remaining = 0
    for report in final_reports:
        update_document_integrity(
            connection,
            report["source"],
            report["expected_records"],
            report["index_status"],
        )
        remaining += report["missing_count"]
        print(
            f"FINAL_INTEGRITY {report['source']} indexed={report['record_count']} "
            f"expected={report['expected_records']} missing={report['missing_count']}"
        )
    connection.close()
    print(f"RECOVERY_COMPLETE added={added} remaining={remaining}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
