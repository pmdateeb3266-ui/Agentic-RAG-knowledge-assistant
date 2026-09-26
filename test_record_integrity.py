import sqlite3
import unittest
from unittest.mock import patch

from record_integrity import audit_document, compact_ranges, format_ranges
from table_ocr import valid_house_number


class RecordIntegrityTests(unittest.TestCase):
    def test_compact_ranges(self):
        self.assertEqual(compact_ranges([1, 2, 3, 7, 9, 10]), [(1, 3), (7, 7), (9, 10)])
        self.assertEqual(format_ranges([1, 2, 3, 7, 9, 10]), "1-3,7,9-10")

    def test_house_identifiers_do_not_require_a_hyphen(self):
        for value in ("9-31", "11/34", "76B", "89", "9-280-A", "--", "—_—-_"):
            self.assertTrue(valid_house_number(value), value)
        for value in ("", "   ", "()"):
            self.assertFalse(valid_house_number(value), value)

    @patch("record_integrity.infer_expected_records", return_value=5)
    def test_audit_detects_internal_and_trailing_gaps(self, _infer):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE documents (
                source TEXT PRIMARY KEY, page_count INTEGER,
                expected_records INTEGER, index_status TEXT
            );
            CREATE TABLE pages (
                id INTEGER PRIMARY KEY, source TEXT, page INTEGER,
                extraction_method TEXT
            );
            CREATE TABLE records (
                id INTEGER PRIMARY KEY, source TEXT, page INTEGER,
                row_index INTEGER, serial_number TEXT
            );
            INSERT INTO documents VALUES ('roll.pdf', 3, 0, 'unknown');
            INSERT INTO pages(source, page, extraction_method) VALUES
                ('roll.pdf', 1, 'ocr'), ('roll.pdf', 2, 'table_ocr'),
                ('roll.pdf', 3, 'table_ocr');
            INSERT INTO records(source, page, row_index, serial_number) VALUES
                ('roll.pdf', 2, 1, '1'), ('roll.pdf', 2, 2, '2'),
                ('roll.pdf', 3, 1, '4');
            """
        )
        report = audit_document(connection, "roll.pdf", refresh_expected=True)
        self.assertEqual(report["expected_records"], 5)
        self.assertEqual(report["missing_serials"], [3, 5])
        self.assertEqual(report["missing_count"], 2)
        self.assertEqual(report["index_status"], "incomplete")
        self.assertIn(3, report["candidate_pages"])
        connection.close()


if __name__ == "__main__":
    unittest.main()
