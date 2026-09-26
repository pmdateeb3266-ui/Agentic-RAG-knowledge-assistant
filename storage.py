import json
import os
import sqlite3

from config import DATABASE_FOLDER, SQLITE_PATH
from text_utils import name_token_keys


def connect():
    os.makedirs(DATABASE_FOLDER, exist_ok=True)
    connection = sqlite3.connect(SQLITE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database(clean=False):
    if clean and os.path.exists(SQLITE_PATH):
        os.remove(SQLITE_PATH)

    conn = connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT UNIQUE NOT NULL,
            page_count INTEGER,
            language_hint TEXT,
            expected_records INTEGER DEFAULT 0,
            index_status TEXT DEFAULT 'unknown',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            page INTEGER NOT NULL,
            extraction_method TEXT NOT NULL,
            ocr_languages TEXT,
            ocr_confidence REAL,
            page_text TEXT,
            UNIQUE(source, page)
        );

        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            page INTEGER NOT NULL,
            row_index INTEGER,
            serial_number TEXT,
            house_number TEXT,
            voter_name TEXT,
            relationship TEXT,
            related_name TEXT,
            gender TEXT,
            age TEXT,
            epic_id TEXT,
            voter_name_keys TEXT,
            related_name_keys TEXT,
            ocr_confidence REAL,
            raw_json TEXT,
            verification_status TEXT DEFAULT 'unreviewed',
            verified_at TEXT,
            verified_by TEXT,
            verification_note TEXT,
            record_revision INTEGER DEFAULT 0,
            UNIQUE(source, page, row_index)
        );

        CREATE TABLE IF NOT EXISTS record_corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            page INTEGER NOT NULL,
            serial_number TEXT,
            action TEXT NOT NULL,
            before_json TEXT NOT NULL,
            after_json TEXT NOT NULL,
            changed_fields TEXT,
            note TEXT,
            reversed_correction_id INTEGER,
            reverted_at TEXT,
            reverted_by INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE COLLATE NOCASE NOT NULL,
            display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_by TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_login_at TEXT
        );

        CREATE TABLE IF NOT EXISTS security_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            action TEXT NOT NULL,
            success INTEGER NOT NULL DEFAULT 1,
            details TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_records_source_page ON records(source, page);
        CREATE INDEX IF NOT EXISTS idx_records_serial ON records(serial_number);
        CREATE INDEX IF NOT EXISTS idx_records_house ON records(house_number);
        CREATE INDEX IF NOT EXISTS idx_records_epic ON records(epic_id);
        CREATE INDEX IF NOT EXISTS idx_records_age ON records(age);
        CREATE INDEX IF NOT EXISTS idx_corrections_record ON record_corrections(record_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_corrections_source_page ON record_corrections(source, page);
        CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
        CREATE INDEX IF NOT EXISTS idx_security_audit_created ON security_audit(id DESC);
        """
    )
    document_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(documents)").fetchall()
    }
    if "expected_records" not in document_columns:
        conn.execute("ALTER TABLE documents ADD COLUMN expected_records INTEGER DEFAULT 0")
    if "index_status" not in document_columns:
        conn.execute("ALTER TABLE documents ADD COLUMN index_status TEXT DEFAULT 'unknown'")
    record_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(records)").fetchall()
    }
    record_migrations = {
        "verification_status": "TEXT DEFAULT 'unreviewed'",
        "verified_at": "TEXT",
        "verified_by": "TEXT",
        "verification_note": "TEXT",
        "record_revision": "INTEGER DEFAULT 0",
    }
    for column, definition in record_migrations.items():
        if column not in record_columns:
            conn.execute(f"ALTER TABLE records ADD COLUMN {column} {definition}")
    correction_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(record_corrections)").fetchall()
    }
    if "actor" not in correction_columns:
        conn.execute("ALTER TABLE record_corrections ADD COLUMN actor TEXT")
    conn.execute(
        "UPDATE records SET verification_status='unreviewed' "
        "WHERE verification_status IS NULL OR verification_status=''"
    )
    conn.execute(
        "UPDATE records SET record_revision=0 WHERE record_revision IS NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_records_verification ON records(verification_status)"
    )
    conn.execute("PRAGMA optimize")
    conn.commit()
    return conn


def upsert_document(conn, source, page_count, language_hint):
    conn.execute(
        """
        INSERT INTO documents(source, page_count, language_hint)
        VALUES (?, ?, ?)
        ON CONFLICT(source) DO UPDATE SET
            page_count=excluded.page_count,
            language_hint=excluded.language_hint
        """,
        (source, page_count, language_hint or ""),
    )
    conn.commit()


def update_document_integrity(conn, source, expected_records, index_status):
    conn.execute(
        """
        UPDATE documents
        SET expected_records=?, index_status=?
        WHERE source=?
        """,
        (max(0, int(expected_records or 0)), str(index_status or "unknown"), source),
    )
    conn.commit()


def upsert_page(conn, source, page, extraction_method, ocr_languages, ocr_confidence, page_text):
    conn.execute(
        """
        INSERT INTO pages(source, page, extraction_method, ocr_languages, ocr_confidence, page_text)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, page) DO UPDATE SET
            extraction_method=excluded.extraction_method,
            ocr_languages=excluded.ocr_languages,
            ocr_confidence=excluded.ocr_confidence,
            page_text=excluded.page_text
        """,
        (source, page, extraction_method, ocr_languages or "", float(ocr_confidence or 0), page_text or ""),
    )
    conn.commit()


def insert_record(conn, record):
    voter_keys = " ".join(name_token_keys(record.get("voter_name", "")))
    related_keys = " ".join(name_token_keys(record.get("related_name", "")))

    conn.execute(
        """
        INSERT OR REPLACE INTO records(
            source, page, row_index, serial_number, house_number,
            voter_name, relationship, related_name, gender, age, epic_id,
            voter_name_keys, related_name_keys, ocr_confidence, raw_json,
            verification_status, verified_at, verified_by, verification_note,
            record_revision
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.get("source", ""),
            record.get("page", 0),
            record.get("row_index", 0),
            record.get("serial_number", ""),
            record.get("house_number", ""),
            record.get("voter_name", ""),
            record.get("relationship", ""),
            record.get("related_name", ""),
            record.get("gender", ""),
            record.get("age", ""),
            record.get("epic_id", ""),
            voter_keys,
            related_keys,
            float(record.get("ocr_confidence", 0) or 0),
            json.dumps(record, ensure_ascii=False),
            record.get("verification_status", "unreviewed") or "unreviewed",
            record.get("verified_at"),
            record.get("verified_by"),
            record.get("verification_note"),
            int(record.get("record_revision", 0) or 0),
        ),
    )


def all_records(conn):
    return conn.execute("SELECT * FROM records ORDER BY source, page, row_index").fetchall()


def exact_record_lookup(conn, value):
    value = (value or "").strip()
    if not value:
        return []
    return conn.execute(
        """
        SELECT * FROM records
        WHERE serial_number = ? COLLATE NOCASE
           OR house_number = ? COLLATE NOCASE
           OR epic_id = ? COLLATE NOCASE
        ORDER BY source, page, row_index
        """,
        (value, value, value),
    ).fetchall()


def exact_field_lookup(conn, field, value):
    """Exact lookup against a whitelisted structured record field."""
    allowed = {
        "house_number",
        "epic_id",
        "serial_number",
        "age",
    }
    if field not in allowed:
        raise ValueError(f"Unsupported exact lookup field: {field}")

    value = (value or "").strip()
    if not value:
        return []

    # Field name is selected only from the whitelist above; value remains a
    # bound parameter. This keeps the SQL exact and safe.
    sql = f"""
        SELECT * FROM records
        WHERE {field} = ? COLLATE NOCASE
        ORDER BY source, page, row_index
    """
    return conn.execute(sql, (value,)).fetchall()
