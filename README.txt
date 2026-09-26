# Agentic RAG Knowledge Assistant

A secure, local-first multilingual assistant for searching, verifying, and managing structured information extracted from PDF documents.

## Features

- Deterministic searches for names, houses, ages, gender, EPIC IDs, polling booths, and family members
- Semantic document questions with supporting evidence
- Multilingual answers and CSV export
- OCR quality checks and missing-record recovery
- Manual record verification with correction history
- Verified backup, download, and safe restore
- Administrator and reviewer access roles
- Security activity tracking and protected local sessions

## Run locally

1. Create and activate a Python virtual environment.
2. Install dependencies:

   ```powershell
   pip install -r requirements.txt
3. Start the application:
   ```powershell
   python run_web.py
4. Open:
   http://127.0.0.1:8000

Data privacy
This project is designed to keep sensitive PDFs, databases, exports, backups, and user data on the local computer. These files are excluded from GitHub.
Access roles
- Administrator: manages users, documents, maintenance, reports, backups, and restore.
- Reviewer: searches, exports CSV files, and manually verifies records.
Current release
v6.4.0 — secure local access, administrator/reviewer accounts, named verification history, verified backups, restore protection, and session security.
