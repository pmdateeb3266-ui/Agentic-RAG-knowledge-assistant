import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import api
import auth_manager
import storage


class ApiAuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        folder = Path(self.temporary.name)
        database = folder / "structured.db"
        self.patches = [
            patch.object(storage, "DATABASE_FOLDER", str(folder)),
            patch.object(storage, "SQLITE_PATH", str(database)),
            patch.object(auth_manager, "connect", side_effect=storage.connect),
            patch.object(api, "connect", side_effect=storage.connect),
        ]
        for item in self.patches:
            item.start()
        storage.initialize_database(clean=True).close()
        with auth_manager._sessions_lock:
            auth_manager._sessions.clear()
        api.app.state.auth_test_bypass = False
        self.client = TestClient(api.app)

    def tearDown(self):
        self.client.close()
        with auth_manager._sessions_lock:
            auth_manager._sessions.clear()
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def _setup_admin(self):
        response = self.client.post("/api/auth/setup", json={
            "username": "owner", "display_name": "Project Owner",
            "password": "secure-pass-123",
        })
        self.assertEqual(response.status_code, 200)
        return response.json()["csrf_token"]

    def test_first_run_requires_setup_and_protects_data(self):
        status = self.client.get("/api/auth/status").json()
        self.assertTrue(status["setup_required"])
        self.assertEqual(self.client.get("/api/documents").status_code, 401)
        csrf = self._setup_admin()
        self.assertEqual(self.client.get("/api/documents").status_code, 200)
        self.assertEqual(self.client.post("/api/admin/users", json={}).status_code, 403)
        created = self.client.post(
            "/api/admin/users",
            headers={"X-CSRF-Token": csrf},
            json={
                "username": "reviewer.one", "display_name": "Reviewer One",
                "password": "review-pass-123", "role": "reviewer",
            },
        )
        self.assertEqual(created.status_code, 200)

    def test_reviewer_can_read_but_cannot_run_admin_actions(self):
        csrf = self._setup_admin()
        self.client.post(
            "/api/admin/users", headers={"X-CSRF-Token": csrf},
            json={
                "username": "reviewer.one", "display_name": "Reviewer One",
                "password": "review-pass-123", "role": "reviewer",
            },
        )
        reviewer = TestClient(api.app)
        login = reviewer.post("/api/auth/login", json={
            "username": "reviewer.one", "password": "review-pass-123",
        })
        self.assertEqual(login.status_code, 200)
        reviewer_csrf = login.json()["csrf_token"]
        self.assertEqual(reviewer.get("/api/documents").status_code, 200)
        self.assertEqual(
            reviewer.post("/api/backups", headers={"X-CSRF-Token": reviewer_csrf}).status_code,
            403,
        )
        reviewer.close()


if __name__ == "__main__":
    unittest.main()
