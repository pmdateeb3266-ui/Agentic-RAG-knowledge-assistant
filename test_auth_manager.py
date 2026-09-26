import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import auth_manager
import storage


class AuthManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        folder = Path(self.temporary.name)
        self.database = folder / "structured.db"
        self.storage_patches = [
            patch.object(storage, "DATABASE_FOLDER", str(folder)),
            patch.object(storage, "SQLITE_PATH", str(self.database)),
        ]
        for item in self.storage_patches:
            item.start()
        self.auth_patch = patch.object(auth_manager, "connect", side_effect=storage.connect)
        self.auth_patch.start()
        storage.initialize_database(clean=True).close()
        with auth_manager._sessions_lock:
            auth_manager._sessions.clear()

    def tearDown(self):
        self.auth_patch.stop()
        for item in reversed(self.storage_patches):
            item.stop()
        self.temporary.cleanup()

    def test_first_admin_password_and_session(self):
        admin = auth_manager.create_first_admin("owner", "Project Owner", "secure-pass-123")
        self.assertEqual(admin["role"], "admin")
        self.assertIsNone(auth_manager.authenticate("owner", "wrong-password"))
        signed_in = auth_manager.authenticate("OWNER", "secure-pass-123")
        token, session = auth_manager.start_session(signed_in)
        self.assertEqual(auth_manager.read_session(token)["username"], "owner")
        self.assertTrue(session["csrf"])
        auth_manager.end_session(token)
        self.assertIsNone(auth_manager.read_session(token))

    def test_setup_cannot_run_twice(self):
        auth_manager.create_first_admin("owner", "Owner", "secure-pass-123")
        with self.assertRaises(auth_manager.AuthError):
            auth_manager.create_first_admin("other", "Other", "secure-pass-456")

    def test_admin_can_create_and_disable_reviewer(self):
        admin = auth_manager.create_first_admin("owner", "Owner", "secure-pass-123")
        reviewer = auth_manager.create_user(
            "reviewer.one", "Reviewer One", "review-pass-123", "reviewer", "owner"
        )
        self.assertEqual(reviewer["role"], "reviewer")
        token, _ = auth_manager.start_session(reviewer)
        changed = auth_manager.set_user_enabled(reviewer["id"], False, admin["id"])
        self.assertFalse(changed["enabled"])
        self.assertIsNone(auth_manager.read_session(token))
        self.assertIsNone(auth_manager.authenticate("reviewer.one", "review-pass-123"))

    def test_last_admin_and_self_disable_are_blocked(self):
        admin = auth_manager.create_first_admin("owner", "Owner", "secure-pass-123")
        with self.assertRaises(auth_manager.AuthError):
            auth_manager.set_user_enabled(admin["id"], False, admin["id"])

    def test_passwords_are_salted_and_reset_revokes_sessions(self):
        admin = auth_manager.create_first_admin("owner", "Owner", "secure-pass-123")
        first = auth_manager.hash_password("same-password")
        second = auth_manager.hash_password("same-password")
        self.assertNotEqual(first, second)
        token, _ = auth_manager.start_session(admin)
        auth_manager.reset_user_password(admin["id"], "new-secure-pass")
        self.assertIsNone(auth_manager.read_session(token))
        self.assertIsNotNone(auth_manager.authenticate("owner", "new-secure-pass"))

    def test_user_can_change_own_password(self):
        admin = auth_manager.create_first_admin("owner", "Owner", "secure-pass-123")
        token, _ = auth_manager.start_session(admin)
        with self.assertRaises(auth_manager.AuthError):
            auth_manager.change_own_password(admin["id"], "wrong-password", "new-secure-pass")
        auth_manager.change_own_password(admin["id"], "secure-pass-123", "new-secure-pass")
        self.assertIsNone(auth_manager.read_session(token))
        self.assertIsNone(auth_manager.authenticate("owner", "secure-pass-123"))
        self.assertIsNotNone(auth_manager.authenticate("owner", "new-secure-pass"))


if __name__ == "__main__":
    unittest.main()
