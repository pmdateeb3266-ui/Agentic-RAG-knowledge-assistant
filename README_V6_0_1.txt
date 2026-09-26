RAG v6.0.1 - incremental PDF indexing hotfix
================================================

ISSUE FIXED
The web upload previously launched the full rebuild mode. That mode deleted and
recreated the SQLite/Chroma indexes and processed every PDF in the documents
folder, so adding one PDF became slower as the collection grew.

NEW UPLOAD BEHAVIOR
- Upload & index passes only the new PDF filename to ingest.py.
- Existing documents, SQLite records, pages, and Chroma embeddings remain intact.
- Progress totals include only pages in the newly uploaded PDF.
- If an incremental job fails, partial database and vector entries for only that
  new PDF are removed. Existing documents are never cleaned by that recovery.
- The same uploaded file can be retried after a failed job.

FULL REBUILD REMAINS AVAILABLE
Running `python ingest.py` manually still performs the intentional full rebuild
of every PDF. The web interface never uses that mode when adding a PDF.

INSTALL
1. Stop the server with Ctrl+C.
2. Copy this complete bundle into C:\Users\abc\rag-assistant and replace matching
   code files. Keep documents, database, exports, reports, and .venv.
3. Run: python run_web.py
4. Refresh the browser with Ctrl+F5.

No re-index is required. The next uploaded PDF will use incremental indexing.
