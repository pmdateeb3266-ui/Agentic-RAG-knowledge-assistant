RAG Knowledge Assistant v6.3.0
================================

Data safety release
-------------------
- Creates timestamped ZIP backups from the web interface.
- Uses SQLite's online backup operation for consistent database snapshots.
- Includes the structured database, semantic index files, manual verification
  status, correction history, and a checksum manifest.
- Verifies every file, SHA-256 checksum, SQLite integrity result, and stored
  record/document total before accepting a backup.
- Lists, verifies, and downloads previous backups from the interface.
- Restore requires both a warning confirmation and typing RESTORE exactly.
- A separate automatic safety backup is created before any restore is staged.
- Restores are applied before the database opens on the next server start, so
  Windows never replaces an open database or embedding index.
- A failed restore rolls back to the database that was active before restart.
- Backup and restore are blocked while indexing or accuracy jobs are running.

All v6.2.1 query, OCR, recovery, manual verification, semantic-search, export,
and acceptance-test behavior is preserved.
