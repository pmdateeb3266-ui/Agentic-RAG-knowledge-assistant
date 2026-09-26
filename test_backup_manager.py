import sqlite3
import tempfile
import unittest
from pathlib import Path

from backup_manager import (
    BackupError, apply_pending_restore, create_backup, list_backups,
    stage_restore, verify_backup,
)


def _make_database(root, record_count=2):
    root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(root / "structured.db")
    connection.executescript(
        """
        CREATE TABLE documents (source TEXT);
        CREATE TABLE records (id INTEGER PRIMARY KEY, verification_status TEXT);
        CREATE TABLE record_corrections (id INTEGER PRIMARY KEY);
        CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT);
        CREATE TABLE security_audit (id INTEGER PRIMARY KEY, action TEXT);
        """
    )
    connection.execute("INSERT INTO documents VALUES ('one.pdf')")
    for index in range(record_count):
        status = "verified" if index == 0 else "unreviewed"
        connection.execute("INSERT INTO records VALUES (?, ?)", (index + 1, status))
    connection.execute("INSERT INTO record_corrections VALUES (1)")
    connection.execute("INSERT INTO users VALUES (1, 'owner')")
    connection.execute("INSERT INTO security_audit VALUES (1, 'login')")
    connection.commit()
    connection.close()
    (root / "vector-index").mkdir()
    (root / "vector-index" / "vectors.bin").write_bytes(b"embedded-vectors")
    (root / "vector-index" / "empty-link-list.bin").write_bytes(b"")


class BackupManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.database = root / "database"
        self.backups = root / "backups"
        _make_database(self.database)

    def tearDown(self):
        self.temporary.cleanup()

    def test_create_list_and_verify_backup(self):
        created = create_backup("manual", self.database, self.backups)
        self.assertTrue(created["valid"])
        self.assertEqual(created["database_counts"], {
            "documents": 1, "records": 2, "manually_verified": 1, "corrections": 1,
            "users": 1, "security_events": 1,
        })
        verified = verify_backup(created["filename"], self.backups)
        self.assertEqual(verified["sha256"], created["sha256"])
        listed = list_backups(self.backups)
        self.assertEqual(len(listed), 1)
        self.assertTrue(listed[0]["valid"])

    def test_restore_requires_exact_confirmation(self):
        created = create_backup("manual", self.database, self.backups)
        with self.assertRaises(BackupError):
            stage_restore(created["filename"], "restore", self.database, self.backups)

    def test_staged_restore_creates_safety_backup_and_applies_on_restart(self):
        created = create_backup("manual", self.database, self.backups)
        connection = sqlite3.connect(self.database / "structured.db")
        connection.execute("INSERT INTO records VALUES (3, 'unreviewed')")
        connection.commit()
        connection.close()

        staged = stage_restore(created["filename"], "RESTORE", self.database, self.backups)
        self.assertTrue(staged["staged"])
        self.assertTrue((self.backups / "pending_restore.zip").is_file())
        self.assertTrue((self.backups / staged["safety_backup"]).is_file())

        restored = apply_pending_restore(self.database, self.backups)
        self.assertEqual(restored["database_counts"]["records"], 2)
        self.assertFalse((self.backups / "pending_restore.zip").exists())
        connection = sqlite3.connect(self.database / "structured.db")
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM records").fetchone()[0], 2)
        self.assertEqual(connection.execute("SELECT username FROM users").fetchone()[0], "owner")
        connection.close()


if __name__ == "__main__":
    unittest.main()
