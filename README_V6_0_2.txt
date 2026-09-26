RAG v6.0.2 - voter-roll completeness and targeted recovery
=============================================================

WHAT WAS WRONG
The number shown beside a PDF was the number of structured rows accepted by
OCR, not the final serial number printed in the roll. Some valid rows were
discarded because their house identifier did not contain a hyphen. Short final
pages with fewer than three rows were also discarded. A missed serial cell
could therefore create an additional gap even when the rest of the row was
readable.

WHAT IS FIXED
- Valid house formats such as 9-31, 11/34, 76B, 89, and 9-280-A are accepted.
- One-row and two-row final voter tables are retained.
- Serial cells use a conservative second OCR pass.
- Every voter-roll PDF can store an expected final serial and integrity status.
- The Documents list shows indexed/expected counts and missing records.
- Recover missing records audits final serials and re-OCRs only candidate pages.
- Recovery adds missing serials without deleting existing correct records.
- Only affected SQLite page text and Chroma record/page vectors are refreshed.
- A timestamped SQLite backup is created before recovery is applied.
- Accuracy reports fail when a PDF is incomplete or has not been audited.
- The quality count is explicitly labelled as a snapshot, not live progress.

INSTALL WITHOUT LOSING DATA
1. Stop the server with Ctrl+C.
2. Extract this complete bundle into C:\Users\abc\rag-assistant and replace
   matching code files.
3. Keep the existing documents, database, exports, reports, and .venv folders.
4. Run: python run_web.py
5. Refresh the browser with Ctrl+F5.
6. Select All indexed documents and click Recover missing records once.

Recovery can take several minutes because it verifies the final serial of each
PDF and OCRs only pages likely to contain missing rows. Keep the server window
open until the progress message says recovery is complete.

COMMAND-LINE OPTIONS
- Audit only: python audit_serials.py --refresh-expected
- Preview recovery: python recover_missing_records.py
- Apply all: python recover_missing_records.py --apply
- Apply one PDF: python recover_missing_records.py --apply --source FILE.pdf

Adding a new PDF remains incremental. Older PDFs are not re-indexed.
