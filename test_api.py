import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from api import _run_ingestion, _run_tool_job, app, jobs, jobs_lock
import storage


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.state.auth_test_bypass = True
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        app.state.auth_test_bypass = False

    def test_health(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], "6.4.0")

    def test_home(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Knowledge Assistant", response.text)

    def test_manual_review_queue_lists_only_actionable_records_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "structured.db"
            with patch.object(storage, "DATABASE_FOLDER", directory), patch.object(
                storage, "SQLITE_PATH", str(path)
            ):
                connection = storage.initialize_database(clean=False)
                connection.execute(
                    """
                    INSERT INTO records(
                        source, page, row_index, serial_number, house_number,
                        voter_name, gender, age, epic_id, ocr_confidence, raw_json
                    ) VALUES ('roll.pdf', 2, 1, '1', '9-1', '', 'female', '35', '', 40, '{}')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO records(
                        source, page, row_index, serial_number, house_number,
                        voter_name, gender, age, epic_id, ocr_confidence, raw_json
                    ) VALUES ('roll.pdf', 2, 2, '2', '9-1', 'లక్ష్మి', 'female', '30', 'ABC1234567', 90, '{}')
                    """
                )
                connection.commit()
                connection.close()

            def temporary_connect():
                item = sqlite3.connect(path, check_same_thread=False)
                item.row_factory = sqlite3.Row
                return item

            with patch("api.connect", side_effect=temporary_connect):
                response = self.client.get("/api/review/records")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["total"], 1)
            self.assertEqual(data["records"][0]["fields"]["serial_number"], "1")
            self.assertIn("missing voter name", data["records"][0]["warnings"])

    @patch("api._document_page_total", return_value=3)
    @patch("api.subprocess.Popen")
    def test_upload_ingestion_targets_only_new_pdf(self, popen, _page_total):
        process = MagicMock()
        process.stdout = iter([
            "PROCESSING: new-roll.pdf\n",
            "Page 1: native text\n",
        ])
        process.wait.return_value = 0
        popen.return_value = process
        with jobs_lock:
            jobs["incremental-test"] = {"status": "queued"}
        _run_ingestion("incremental-test", "new-roll.pdf")
        command = popen.call_args.args[0]
        self.assertEqual(command[-2:], ["--source", "new-roll.pdf"])
        self.assertEqual(jobs["incremental-test"]["status"], "complete")
        self.assertEqual(jobs["incremental-test"]["total_pages"], 3)

    @patch("api._cleanup_partial_index")
    @patch("api._document_page_total", return_value=1)
    @patch("api.subprocess.Popen")
    def test_failed_incremental_index_cleans_only_new_source(self, popen, _page_total, cleanup):
        process = MagicMock()
        process.stdout = iter(["ERROR: OCR stopped\n"])
        process.wait.return_value = 1
        popen.return_value = process
        with jobs_lock:
            jobs["incremental-failure"] = {"status": "queued"}
        _run_ingestion("incremental-failure", "failed-roll.pdf")
        cleanup.assert_called_once_with("failed-roll.pdf")
        self.assertEqual(jobs["incremental-failure"]["status"], "failed")

    @patch("api.subprocess.Popen")
    def test_recovery_job_tracks_targeted_pages(self, popen):
        process = MagicMock()
        process.stdout = iter([
            "RECOVERY_PAGES 4\n",
            "PROGRESS 1/4 roll.pdf page 7\n",
            "RECOVERY_COMPLETE added=2 remaining=0\n",
        ])
        process.wait.return_value = 0
        popen.return_value = process
        with jobs_lock:
            jobs["recovery-test"] = {"status": "queued"}
        _run_tool_job(
            "recovery-test",
            "recover_missing_records.py",
            ["--apply", "--source", "roll.pdf"],
            "recovery",
        )
        self.assertEqual(jobs["recovery-test"]["status"], "complete")
        self.assertEqual(jobs["recovery-test"]["total_pages"], 4)
        self.assertEqual(jobs["recovery-test"]["processed_pages"], 1)


if __name__ == "__main__":
    unittest.main()
