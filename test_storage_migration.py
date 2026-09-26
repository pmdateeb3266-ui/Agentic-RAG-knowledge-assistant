import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import storage


class StorageMigrationTests(unittest.TestCase):
    def test_existing_v61_database_receives_verification_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "structured.db"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT UNIQUE NOT NULL,
                    page_count INTEGER,
                    language_hint TEXT,
                    expected_records INTEGER DEFAULT 0,
                    index_status TEXT DEFAULT 'unknown',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE pages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    page INTEGER NOT NULL,
                    extraction_method TEXT NOT NULL,
                    ocr_languages TEXT,
                    ocr_confidence REAL,
                    page_text TEXT,
                    UNIQUE(source, page)
                );
                CREATE TABLE records (
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
                    UNIQUE(source, page, row_index)
                );
                """
            )
            connection.close()
            with patch.object(storage, "DATABASE_FOLDER", directory), patch.object(
                storage, "SQLITE_PATH", str(path)
            ):
                migrated = storage.initialize_database(clean=False)
                columns = {
                    row[1] for row in migrated.execute("PRAGMA table_info(records)")
                }
                tables = {
                    row[0] for row in migrated.execute(
                        "SELECT name FROM sqlite_schema WHERE type='table'"
                    )
                }
                correction_columns = {
                    row[1] for row in migrated.execute("PRAGMA table_info(record_corrections)")
                }
                migrated.close()
            self.assertIn("verification_status", columns)
            self.assertIn("record_revision", columns)
            self.assertIn("record_corrections", tables)
            self.assertIn("users", tables)
            self.assertIn("security_audit", tables)
            self.assertIn("actor", correction_columns)


if __name__ == "__main__":
    unittest.main()
