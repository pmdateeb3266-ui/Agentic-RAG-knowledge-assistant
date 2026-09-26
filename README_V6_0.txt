RAG v6.0 - accuracy, evidence, and OCR trust layer
===================================================

WHAT IS NEW
1. Every answer identifies its execution path:
   - Verified database for deterministic SQLite queries.
   - Verified exact lookup for exact identifiers.
   - Semantic evidence for ChromaDB retrieval.

2. Semantic retrieval now has a configurable relevance cutoff. Unrelated vector
   matches are discarded. If nothing passes the cutoff, the assistant returns a
   clear no-evidence answer in the selected language instead of asking Qwen to
   invent an answer.

3. Semantic evidence is displayed as source/page/excerpt rows with relevance
   confidence. Structured queries retain the complete deterministic record view.

4. Upload indexing has visible page progress and returns a useful failure message.

5. The new Accuracy & OCR panel provides:
   - current clear/review record counts;
   - targeted re-OCR of only pages containing review records;
   - an automatic accuracy and index-consistency report.

TARGETED OCR SAFETY
- The review tool creates a timestamped SQLite backup before applying changes.
- It accepts a page only when record retention is safe and warnings or confidence
  measurably improve.
- It refreshes both SQLite and the affected Chroma vectors for accepted pages.
- A command-line run is dry-run unless --apply is explicitly supplied. The web
  button supplies --apply after these safety checks.

AUTOMATIC ACCURACY REPORT
The report checks all 72 saved routing prototypes from v5.7-v5.9, document and
record totals, OCR quality warnings, per-document Chroma counts, and mismatches
between SQLite records and record embeddings. Both readable text and detailed
JSON files are saved under reports/.

PRIOR BEHAVIOR PRESERVED
All earlier solutions remain included: generalized age ranges and gender counts,
strict person-name matching, arbitrary house numbers, polling-booth/document
queries, largest household with ties, same-household family lookup, multilingual
labels and romanization, multi-document scoping, pagination, and CSV export.

INSTALL
1. Stop the existing web server.
2. Copy this bundle into C:\Users\abc\rag-assistant and replace matching code
   files. Do not delete the existing documents, database, exports, or .venv.
3. Run: python run_web.py
4. Refresh the browser with Ctrl+F5.

No PDF re-index is required for the v6.0 code upgrade. Use Improve reviewed OCR
later only when you want the guarded quality pass to reprocess flagged pages.

OPTIONAL SETTINGS
RAG_SEMANTIC_MAX_DISTANCE=0.72
RAG_SEMANTIC_HIGH_CONFIDENCE_DISTANCE=0.38
RAG_SEMANTIC_MODERATE_CONFIDENCE_DISTANCE=0.55
RAG_SEMANTIC_MIN_EVIDENCE=1
