import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import chromadb

import record_editor
from config import CHUNK_COLLECTION_NAME, RECORD_COLLECTION_NAME


class _Vectors(list):
    def tolist(self):
        return list(self)


class _FakeEmbeddingModel:
    def encode(self, texts, normalize_embeddings=True):
        return _Vectors([[1.0, 0.0, 0.0] for _ in texts])


class ManualIndexSyncTests(unittest.TestCase):
    def test_corrected_page_refreshes_record_and_chunk_collections(self):
        with tempfile.TemporaryDirectory() as directory:
            client = chromadb.PersistentClient(path=directory)
            client.create_collection(
                RECORD_COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
            )
            client.create_collection(
                CHUNK_COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
            )
            connection = sqlite3.connect(":memory:")
            connection.row_factory = sqlite3.Row
            connection.executescript(
                """
                CREATE TABLE pages (
                    source TEXT, page INTEGER, extraction_method TEXT,
                    ocr_languages TEXT, ocr_confidence REAL, page_text TEXT
                );
                CREATE TABLE records (
                    id INTEGER PRIMARY KEY, source TEXT, page INTEGER,
                    row_index INTEGER, serial_number TEXT, house_number TEXT,
                    voter_name TEXT, relationship TEXT, related_name TEXT,
                    gender TEXT, age TEXT, epic_id TEXT, ocr_confidence REAL,
                    verification_status TEXT
                );
                INSERT INTO pages VALUES ('roll.pdf', 2, 'table_ocr', 'tel+eng', 80, 'old text');
                INSERT INTO records VALUES (
                    1, 'roll.pdf', 2, 1, '1', '9-1', 'సీత', 'భ', 'రాము',
                    'స్త్రీ', '35', 'ABC1234567', 80, 'verified'
                );
                """
            )
            with patch.object(record_editor, "DATABASE_FOLDER", directory), patch.object(
                record_editor, "_embedding_model", _FakeEmbeddingModel()
            ):
                result = record_editor.sync_record_page(connection, "roll.pdf", 2)
            record_collection = client.get_collection(RECORD_COLLECTION_NAME)
            chunk_collection = client.get_collection(CHUNK_COLLECTION_NAME)
            stored = record_collection.get(include=["documents", "metadatas"])
            self.assertEqual(result["record_embeddings"], 1)
            self.assertEqual(record_collection.count(), 1)
            self.assertGreater(chunk_collection.count(), 0)
            self.assertIn("సీత", stored["documents"][0])
            self.assertTrue(stored["metadatas"][0]["human_verified"])
            page = connection.execute("SELECT * FROM pages").fetchone()
            self.assertEqual(page["extraction_method"], "table_ocr_manual")
            connection.close()
            client._system.stop()


if __name__ == "__main__":
    unittest.main()
