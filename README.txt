RAG v3 - layout-aware multilingual document assistant

WHY THIS VERSION EXISTS
-----------------------
The supplied electoral-roll PDF visually contains Telugu text but its embedded
PDF text is legacy/corrupted. Whole-page OCR also performs poorly on dense
8-column tables. This version therefore detects table grids and OCRs each cell
individually before indexing.

STORAGE
-------
1. SQLite database/database/structured.db
   - exact structured fields (serial, house, voter name, related name, age, EPIC)
2. Chroma rag_records
   - semantic search over each structured row
3. Chroma rag_documents
   - semantic search over general page/chunk text

INSTALL
-------
pip install -r requirements.txt

Tesseract must be installed with the required language packs.
For the supplied PDF, tel + eng are required.

RUN
---
1. Put PDFs in documents/
2. python ingest.py
3. python search.py
4. python answer.py

IMPORTANT
---------
This v3 changes the database schema and extraction method. Delete/rebuild the
old database by running ingest.py once; ingest.py performs a clean rebuild.

TEST QUERY
----------
How many times the name Altaf is mentioned and give other details related to the name

Expected query plan:
Intent: COUNT
Target: Altaf
Structured scope: all_name_fields
Exhaustive retrieval: True

The count is computed by Python from all structured records. Qwen only explains
the verified evidence.

V3.1 FIELD-ROUTING PATCH
------------------------
This patch fixes exact structured lookups such as:
  Who is in house number 9-78
  Find EPIC AP251724259702
  Show serial number 75
  Show people age 86

These queries now route to exact SQLite fields and NEVER fall back to semantic
similarity. Unicode dash variants (for example 9–78) are normalized to 9-78.

If you already completed V3 ingestion successfully, you DO NOT need to ingest
again for this patch. Replace the Python files and run:
  python search.py
