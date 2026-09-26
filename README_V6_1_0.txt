RAG v6.1.0 - actionable OCR quality review
============================================

The v6.0.3 accuracy report proves that routing, record completeness, and
SQLite/Chroma synchronization are correct. The large "need review" number was
still misleading because it mixed genuine OCR problems with values faithfully
copied from the source PDFs.

WHAT CHANGED
- OCR warnings are now separated from source placeholder/redaction notices.
- Repeated-zero EPIC values remain searchable but appear as Source note rather
  than incorrectly triggering OCR reprocessing.
- Official XXX/-- placeholder rows are labelled as source notices.
- The document-scoped gender alias for 17576784804969.pdf normalizes OCR value
  "3" to female, matching the printed Telugu female value on the inspected page.
- The web interface shows actionable OCR warnings and source notes separately.
- Expand "View quality categories" to see exact counts by category.
- Result rows display Review, Source note, or Clear.
- Deterministic answers include source notes where relevant.
- Improve reviewed OCR now targets only actionable warnings.
- A safety check prevents OCR improvement from removing any existing serial.
- The accuracy report includes both warning categories and source-note counts.

INSTALL
1. Stop the server with Ctrl+C.
2. Extract the complete bundle into C:\Users\abc\rag-assistant and replace
   matching code files.
3. Preserve documents, database, exports, reports, and .venv.
4. Run: python run_web.py
5. Refresh the browser with Ctrl+F5 and confirm v6.1.0.

No re-index or missing-record recovery is required. First run the accuracy
report. Then expand View quality categories. Use Improve reviewed OCR only if
you want to attempt repairs for the remaining actionable warning pages.
