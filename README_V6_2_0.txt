RAG v6.2.0 - manual verification workspace
==========================================

This release adds a safe human-review workflow for the 230 records that have
actionable OCR warnings. It preserves the v6.1.0 search, multilingual answers,
incremental indexing, missing-record recovery, and accuracy checks.

WHAT CHANGED
- Manual verification opens from the Accuracy & OCR section.
- The default queue contains only records with actionable warnings.
- Filter the queue by PDF, warning category, PDF page, or verification status.
- Each record shows a cropped image of its original row in the source PDF.
- Correct serial number, house number, names, relationship, gender, age, and
  EPIC in one form.
- Verify an accurate row without changing its extracted values.
- Every save uses a revision check so an older browser view cannot overwrite a
  newer correction.
- Every verification and correction is written to a permanent audit history.
- The latest active correction can be rolled back from the same screen.
- A successful save refreshes SQLite page text, the record embeddings, and the
  page-chunk embeddings before it is accepted.
- Manually verified rows no longer count as actionable OCR warnings.
- Automatic OCR improvement skips pages containing human-verified rows, so it
  cannot overwrite manual corrections.
- The accuracy report now includes the number of manually verified records.

INSTALL
1. Stop the running server with Ctrl+C.
2. Extract the complete bundle into C:\Users\abc\rag-assistant and replace
   matching code files.
3. Preserve documents, database, exports, reports, and .venv.
4. Run: python run_web.py
5. Refresh the browser with Ctrl+F5 and confirm v6.2.0.

No re-index is required. On first server start, the existing database receives
the verification and audit-history fields automatically without changing the
4,852 indexed records.

USING MANUAL VERIFICATION
1. Select Manual verification under Accuracy & OCR.
2. Select a flagged record in the left queue.
3. Compare the editable values with the original PDF row shown above them.
4. Use Save & verify when you corrected a value, or Verify as shown when the
   extraction is already faithful to the source.
5. Add a short note when the source is unclear or uses a placeholder.
6. Change Status to Verified to inspect completed work and its audit history.

Important: the source PDF is never edited. A correction changes only the local
structured record and its search representations. A deliberate full rebuild
with `python ingest.py` creates a new database and therefore resets manual
verification history; keep the automatic backup or copy the database first if
you intentionally perform a full rebuild.

