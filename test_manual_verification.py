import sqlite3
import unittest

from record_editor import (
    RecordEditConflict,
    RecordEditError,
    apply_record_update,
    correction_history,
    rollback_correction,
)
from retriever import assess_record_quality


class ManualVerificationTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
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
                verification_status TEXT DEFAULT 'unreviewed',
                verified_at TEXT,
                verified_by TEXT,
                verification_note TEXT,
                record_revision INTEGER DEFAULT 0
            );
            CREATE TABLE record_corrections (
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
                reverted_by TEXT,
                actor TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cursor = self.connection.execute(
            """
            INSERT INTO records(
                source, page, row_index, serial_number, house_number,
                voter_name, relationship, related_name, gender, age,
                epic_id, ocr_confidence, raw_json
            ) VALUES ('roll.pdf', 2, 1, '1', '9-1', '', 'భ', 'రాము', 'పు', '35', '', 42, '{}')
            """
        )
        self.record_id = cursor.lastrowid
        self.connection.execute(
            """
            INSERT INTO records(
                source, page, row_index, serial_number, house_number,
                voter_name, relationship, related_name, gender, age,
                epic_id, ocr_confidence, raw_json
            ) VALUES ('roll.pdf', 2, 2, '2', '9-1', 'లక్ష్మి', 'భ', 'రాము', 'స్త్రీ', '30', 'ABC1234567', 90, '{}')
            """
        )
        self.connection.commit()

    def tearDown(self):
        self.connection.close()

    def test_update_verifies_record_and_rollback_restores_it(self):
        updated, correction_id = apply_record_update(
            self.connection,
            self.record_id,
            {"voter_name": "సీత", "epic_id": "abc1234567"},
            note="Checked against source row",
            expected_revision=0,
            verified_by="reviewer.one",
        )
        self.connection.commit()
        self.assertEqual(updated["voter_name"], "సీత")
        self.assertEqual(updated["epic_id"], "ABC1234567")
        self.assertEqual(updated["verification_status"], "verified")
        self.assertEqual(updated["record_revision"], 1)
        self.assertEqual(assess_record_quality(updated)["warnings"], [])
        self.assertTrue(assess_record_quality(updated, respect_verification=False)["warnings"])
        self.assertEqual(correction_history(self.connection, self.record_id)[0]["id"], correction_id)
        self.assertEqual(correction_history(self.connection, self.record_id)[0]["actor"], "reviewer.one")

        restored, rollback_id = rollback_correction(
            self.connection, correction_id, expected_revision=1, reverted_by="owner"
        )
        self.connection.commit()
        self.assertEqual(restored["voter_name"], "")
        self.assertEqual(restored["epic_id"], "")
        self.assertEqual(restored["verification_status"], "unreviewed")
        self.assertEqual(restored["record_revision"], 2)
        self.assertGreater(rollback_id, correction_id)
        history = correction_history(self.connection, self.record_id)
        self.assertEqual(history[0]["actor"], "owner")
        self.assertEqual(history[1]["reverted_by"], "owner")

    def test_stale_revision_is_rejected(self):
        with self.assertRaises(RecordEditConflict):
            apply_record_update(
                self.connection,
                self.record_id,
                {"voter_name": "సీత"},
                expected_revision=4,
            )

    def test_duplicate_serial_is_rejected(self):
        with self.assertRaises(RecordEditError):
            apply_record_update(
                self.connection,
                self.record_id,
                {"serial_number": "2"},
                expected_revision=0,
            )


if __name__ == "__main__":
    unittest.main()
