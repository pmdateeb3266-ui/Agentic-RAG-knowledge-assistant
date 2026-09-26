import hashlib
import json
import re
from datetime import datetime, timezone


from config import (
    CHUNK_COLLECTION_NAME,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    DATABASE_FOLDER,
    EMBEDDING_MODEL,
    RECORD_COLLECTION_NAME,
)
from table_ocr import format_record, records_to_page_text
from text_utils import name_token_keys


_embedding_model = None


EDITABLE_FIELDS = (
    "serial_number",
    "house_number",
    "voter_name",
    "relationship",
    "related_name",
    "gender",
    "age",
    "epic_id",
)

SNAPSHOT_FIELDS = EDITABLE_FIELDS + (
    "verification_status",
    "verified_at",
    "verified_by",
    "verification_note",
    "record_revision",
)


class RecordEditError(ValueError):
    pass


class RecordEditConflict(RecordEditError):
    pass


class RecordNotFound(RecordEditError):
    pass


def row_value(row, key, default=None):
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def record_snapshot(row):
    return {field: row_value(row, field) for field in SNAPSHOT_FIELDS}


def _clean_updates(updates):
    cleaned = {}
    for field in EDITABLE_FIELDS:
        if field not in updates:
            continue
        value = str(updates[field] or "").strip()
        if field in {"serial_number", "age"}:
            value = re.sub(r"\s+", "", value)
        elif field == "house_number":
            value = re.sub(r"\s+", "", value)
        elif field == "epic_id":
            value = re.sub(r"\s+", "", value).upper()
        cleaned[field] = value
    return cleaned


def validate_updates(connection, record_id, source, updates):
    errors = []
    serial = updates.get("serial_number")
    if serial is not None:
        if not serial.isdigit() or int(serial) <= 0:
            errors.append("Serial number must be a positive whole number")
        else:
            duplicate = connection.execute(
                "SELECT id FROM records WHERE source=? AND serial_number=? AND id<>? LIMIT 1",
                (source, serial, int(record_id)),
            ).fetchone()
            if duplicate:
                errors.append(f"Serial number {serial} already exists in {source}")
    age = updates.get("age")
    if age:
        if not age.isdigit():
            errors.append("Age must contain numbers only")
        elif not 18 <= int(age) <= 120:
            errors.append("Age must be between 18 and 120")
    house = updates.get("house_number")
    if house is not None and not house:
        errors.append("House number cannot be empty; use the source placeholder if one is printed")
    if errors:
        raise RecordEditError("; ".join(errors))


def _load_raw_json(row):
    try:
        value = json.loads(str(row_value(row, "raw_json", "") or "{}"))
    except (TypeError, ValueError):
        value = {}
    return value if isinstance(value, dict) else {}


def _insert_audit(
    connection,
    record_id,
    source,
    page,
    serial_number,
    action,
    before,
    after,
    changed_fields,
    note,
    reversed_correction_id=None,
    actor="local_user",
):
    correction_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(record_corrections)")
    }
    if "actor" not in correction_columns:
        cursor = connection.execute(
            """
            INSERT INTO record_corrections(
                record_id, source, page, serial_number, action,
                before_json, after_json, changed_fields, note,
                reversed_correction_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(record_id), source, int(page), serial_number, action,
                json.dumps(before, ensure_ascii=False),
                json.dumps(after, ensure_ascii=False),
                json.dumps(changed_fields, ensure_ascii=False), note,
                reversed_correction_id,
            ),
        )
        return int(cursor.lastrowid)
    cursor = connection.execute(
        """
        INSERT INTO record_corrections(
            record_id, source, page, serial_number, action,
            before_json, after_json, changed_fields, note,
            reversed_correction_id, actor
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(record_id), source, int(page), serial_number, action,
            json.dumps(before, ensure_ascii=False),
            json.dumps(after, ensure_ascii=False),
            json.dumps(changed_fields, ensure_ascii=False),
            note,
            reversed_correction_id,
            str(actor or "local_user"),
        ),
    )
    return int(cursor.lastrowid)


def apply_record_update(
    connection,
    record_id,
    updates,
    note="",
    expected_revision=None,
    verified_by="local_user",
):
    row = connection.execute("SELECT * FROM records WHERE id=?", (int(record_id),)).fetchone()
    if not row:
        raise RecordNotFound("Record not found")
    current_revision = int(row_value(row, "record_revision", 0) or 0)
    if expected_revision is not None and int(expected_revision) != current_revision:
        raise RecordEditConflict(
            "This record changed after you opened it. Reload it before saving."
        )

    cleaned = _clean_updates(updates)
    source = str(row["source"])
    validate_updates(connection, record_id, source, cleaned)
    before = record_snapshot(row)
    values = {field: str(row_value(row, field, "") or "") for field in EDITABLE_FIELDS}
    values.update(cleaned)
    changed_fields = [field for field in EDITABLE_FIELDS if values[field] != before.get(field)]
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    note = str(note or "").strip()
    next_revision = current_revision + 1
    raw = _load_raw_json(row)
    raw.update(values)
    raw["verification_status"] = "verified"
    raw["verified_at"] = timestamp
    raw["verification_note"] = note

    connection.execute(
        """
        UPDATE records SET
            serial_number=?, house_number=?, voter_name=?, relationship=?,
            related_name=?, gender=?, age=?, epic_id=?,
            voter_name_keys=?, related_name_keys=?, raw_json=?,
            verification_status='verified', verified_at=?, verified_by=?,
            verification_note=?, record_revision=?
        WHERE id=? AND record_revision=?
        """,
        (
            values["serial_number"], values["house_number"], values["voter_name"],
            values["relationship"], values["related_name"], values["gender"],
            values["age"], values["epic_id"],
            " ".join(name_token_keys(values["voter_name"])),
            " ".join(name_token_keys(values["related_name"])),
            json.dumps(raw, ensure_ascii=False), timestamp, verified_by, note,
            next_revision, int(record_id), current_revision,
        ),
    )
    if connection.execute("SELECT changes()").fetchone()[0] != 1:
        raise RecordEditConflict(
            "This record changed while it was being saved. Reload it and try again."
        )
    updated = connection.execute("SELECT * FROM records WHERE id=?", (int(record_id),)).fetchone()
    after = record_snapshot(updated)
    correction_id = _insert_audit(
        connection,
        record_id,
        source,
        int(row["page"]),
        values["serial_number"],
        "update_and_verify" if changed_fields else "verify",
        before,
        after,
        changed_fields,
        note,
        actor=verified_by,
    )
    return updated, correction_id


def correction_history(connection, record_id):
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(record_corrections)")}
    actor_projection = "actor" if "actor" in columns else "NULL AS actor"
    rows = connection.execute(
        f"""
        SELECT id, record_id, source, page, serial_number, action,
               changed_fields, note, reversed_correction_id,
               reverted_at, reverted_by, created_at, {actor_projection}
        FROM record_corrections
        WHERE record_id=?
        ORDER BY id DESC
        """,
        (int(record_id),),
    ).fetchall()
    output = []
    for row in rows:
        item = dict(row)
        try:
            item["changed_fields"] = json.loads(item.get("changed_fields") or "[]")
        except (TypeError, ValueError):
            item["changed_fields"] = []
        output.append(item)
    return output


def rollback_correction(connection, correction_id, expected_revision=None, reverted_by="local_user"):
    correction = connection.execute(
        "SELECT * FROM record_corrections WHERE id=?", (int(correction_id),)
    ).fetchone()
    if not correction:
        raise RecordNotFound("Correction history entry not found")
    if str(correction["action"]) == "rollback" or correction["reverted_at"]:
        raise RecordEditConflict("That correction has already been rolled back")
    record_id = int(correction["record_id"])
    latest = connection.execute(
        """
        SELECT id FROM record_corrections
        WHERE record_id=? AND action<>'rollback' AND reverted_at IS NULL
        ORDER BY id DESC LIMIT 1
        """,
        (record_id,),
    ).fetchone()
    if not latest or int(latest["id"]) != int(correction_id):
        raise RecordEditConflict("Only the latest active correction can be rolled back")
    row = connection.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
    if not row:
        raise RecordNotFound("The corrected record no longer exists")
    current_revision = int(row_value(row, "record_revision", 0) or 0)
    if expected_revision is not None and int(expected_revision) != current_revision:
        raise RecordEditConflict(
            "This record changed after you opened it. Reload it before rolling back."
        )
    try:
        restore = json.loads(str(correction["before_json"] or "{}"))
    except (TypeError, ValueError) as error:
        raise RecordEditError("The saved correction history is invalid") from error
    before = record_snapshot(row)
    values = {
        field: str(restore.get(field, row_value(row, field, "")) or "")
        for field in EDITABLE_FIELDS
    }
    validate_updates(connection, record_id, str(row["source"]), values)
    next_revision = current_revision + 1
    raw = _load_raw_json(row)
    raw.update(values)
    verification_status = str(restore.get("verification_status") or "unreviewed")
    verified_at = restore.get("verified_at")
    verified_by = restore.get("verified_by")
    verification_note = restore.get("verification_note")
    raw["verification_status"] = verification_status
    raw["verified_at"] = verified_at
    raw["verification_note"] = verification_note
    connection.execute(
        """
        UPDATE records SET
            serial_number=?, house_number=?, voter_name=?, relationship=?,
            related_name=?, gender=?, age=?, epic_id=?,
            voter_name_keys=?, related_name_keys=?, raw_json=?,
            verification_status=?, verified_at=?, verified_by=?,
            verification_note=?, record_revision=?
        WHERE id=? AND record_revision=?
        """,
        (
            values["serial_number"], values["house_number"], values["voter_name"],
            values["relationship"], values["related_name"], values["gender"],
            values["age"], values["epic_id"],
            " ".join(name_token_keys(values["voter_name"])),
            " ".join(name_token_keys(values["related_name"])),
            json.dumps(raw, ensure_ascii=False), verification_status, verified_at,
            verified_by, verification_note, next_revision, record_id, current_revision,
        ),
    )
    if connection.execute("SELECT changes()").fetchone()[0] != 1:
        raise RecordEditConflict(
            "This record changed while it was being restored. Reload it and try again."
        )
    updated = connection.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
    after = record_snapshot(updated)
    changed_fields = [field for field in SNAPSHOT_FIELDS if before.get(field) != after.get(field)]
    rollback_id = _insert_audit(
        connection,
        record_id,
        str(row["source"]),
        int(row["page"]),
        values["serial_number"],
        "rollback",
        before,
        after,
        changed_fields,
        f"Rolled back correction #{correction_id}",
        reversed_correction_id=int(correction_id),
        actor=reverted_by,
    )
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    connection.execute(
        "UPDATE record_corrections SET reverted_at=?, reverted_by=? WHERE id=?",
        (timestamp, str(reverted_by or "local_user"), int(correction_id)),
    )
    return updated, rollback_id


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


def _snapshot_collection(collection, source, page):
    return collection.get(
        where=_where(source, page),
        include=["documents", "metadatas", "embeddings"],
    )


def _restore_collection(collection, source, page, snapshot):
    existing = collection.get(where=_where(source, page), include=[]).get("ids", [])
    if existing:
        collection.delete(ids=existing)
    ids = list(snapshot.get("ids") or [])
    if ids:
        documents = snapshot.get("documents")
        metadatas = snapshot.get("metadatas")
        embeddings = snapshot.get("embeddings")
        collection.add(
            ids=ids,
            documents=list(documents) if documents is not None else None,
            metadatas=list(metadatas) if metadatas is not None else None,
            embeddings=embeddings.tolist() if hasattr(embeddings, "tolist") else list(embeddings or []),
        )


def _replace_collection_page(collection, source, page, ids, documents, metadatas, embeddings):
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


def sync_record_page(connection, source, page):
    """Refresh the corrected page in both Chroma collections.

    The caller owns the SQLite transaction. If synchronization fails, both
    Chroma collections are restored to their pre-edit snapshots.
    """
    global _embedding_model
    import chromadb
    from sentence_transformers import SentenceTransformer

    rows = [dict(row) for row in connection.execute(
        "SELECT * FROM records WHERE source=? AND page=? ORDER BY row_index",
        (source, int(page)),
    )]
    if not rows:
        raise RecordEditError("The record page no longer contains structured rows")
    page_row = connection.execute(
        "SELECT * FROM pages WHERE source=? AND page=?", (source, int(page))
    ).fetchone()
    languages = str(row_value(page_row, "ocr_languages", "") or "")
    page_text = records_to_page_text(rows)
    average_confidence = sum(float(row.get("ocr_confidence") or 0) for row in rows) / len(rows)
    connection.execute(
        """
        UPDATE pages SET extraction_method='table_ocr_manual', page_text=?,
                         ocr_confidence=?
        WHERE source=? AND page=?
        """,
        (page_text, average_confidence, source, int(page)),
    )

    if _embedding_model is None:
        _embedding_model = SentenceTransformer(EMBEDDING_MODEL)
    model = _embedding_model
    client = chromadb.PersistentClient(path=DATABASE_FOLDER)
    record_collection = client.get_collection(RECORD_COLLECTION_NAME)
    chunk_collection = client.get_collection(CHUNK_COLLECTION_NAME)
    record_before = _snapshot_collection(record_collection, source, page)
    chunk_before = _snapshot_collection(chunk_collection, source, page)

    record_documents = [format_record(row) for row in rows]
    record_ids = [
        _sha1_id(row["source"], row["page"], row["row_index"], row.get("serial_number", ""))
        for row in rows
    ]
    record_metadatas = [{
        "source": row["source"],
        "page": int(row["page"]),
        "row_index": int(row.get("row_index") or 0),
        "serial_number": str(row.get("serial_number") or ""),
        "house_number": str(row.get("house_number") or ""),
        "epic_id": str(row.get("epic_id") or ""),
        "extraction_method": "table_ocr_manual",
        "ocr_confidence": float(row.get("ocr_confidence") or 0),
        "human_verified": str(row.get("verification_status") or "") == "verified",
    } for row in rows]
    record_embeddings = model.encode(record_documents, normalize_embeddings=True).tolist()

    chunks = _split_text(page_text)
    chunk_ids = [_sha1_id(source, page, index, chunk) for index, chunk in enumerate(chunks)]
    chunk_metadatas = [{
        "source": source,
        "page": int(page),
        "chunk": index,
        "extraction_method": "table_ocr_manual",
        "ocr_languages": languages,
        "ocr_confidence": average_confidence,
        "structured_records": len(rows),
    } for index, _ in enumerate(chunks)]
    chunk_embeddings = model.encode(chunks, normalize_embeddings=True).tolist() if chunks else []

    try:
        _replace_collection_page(
            record_collection, source, page, record_ids, record_documents,
            record_metadatas, record_embeddings,
        )
        _replace_collection_page(
            chunk_collection, source, page, chunk_ids, chunks,
            chunk_metadatas, chunk_embeddings,
        )
    except Exception:
        for collection, snapshot in (
            (record_collection, record_before),
            (chunk_collection, chunk_before),
        ):
            try:
                _restore_collection(collection, source, page, snapshot)
            except Exception:
                pass
        raise
    return {
        "record_embeddings": len(record_ids),
        "chunk_embeddings": len(chunk_ids),
    }


def restore_page_vectors(connection, source, page):
    """Best-effort restoration after a SQLite commit failure."""
    sync_record_page(connection, source, page)
