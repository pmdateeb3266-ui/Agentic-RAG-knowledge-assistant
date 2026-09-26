RAG Knowledge Assistant v6.4.0
================================

Secure local access release
---------------------------
- Shows a one-time Create administrator screen after upgrading.
- Has no preset or plaintext default password.
- Stores salted PBKDF2-SHA256 password hashes in the structured database.
- Supports Administrator and Reviewer accounts.
- Administrators manage users, documents, OCR/integrity jobs, reports, and backups/restores.
- Reviewers can search, export CSV files, and manually verify/correct records.
- Secure, HTTP-only same-site sessions expire after 30 minutes of inactivity.
- Every state-changing request requires a separate anti-forgery token.
- Disabled accounts and password resets immediately revoke active sessions.
- Every signed-in user can change their own password from the header.
- The last active administrator cannot be disabled.
- Manual verification and rollback history now records the signed-in username.
- Administrators can view recent security activity from the sidebar.
- Verified backups include user accounts and security activity.
- A local administrator password-recovery utility is included.

All v6.3.0 search, OCR, recovery, verification, backup/restore, export, and
acceptance-test behavior is preserved.

This application remains bound to 127.0.0.1 by default. Do not expose it
directly to the public internet. A future network deployment should add HTTPS
and a production reverse proxy.
