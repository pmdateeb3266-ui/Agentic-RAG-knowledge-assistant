RAG v6.0.3 - final two-row recovery hotfix
============================================

This update preserves every v6.0.2 fix and handles two final OCR edge cases:

- A real voter row is retained when its serial, house, relationship, gender,
  age, and EPIC are readable but both Telugu name cells are unreadable. The
  record remains marked for quality review instead of being silently dropped.
- Official placeholder/redacted rows such as house "--" and voter name "XXX"
  are accepted as records when the table structure and serial are valid.

INSTALL
1. Stop the server with Ctrl+C.
2. Extract this bundle into C:\Users\abc\rag-assistant and replace matching
   code files. Preserve documents, database, exports, reports, and .venv.
3. Run: python run_web.py
4. Refresh the browser with Ctrl+F5 and confirm v6.0.3.
5. Select All indexed documents and click Recover missing records once more.
6. Confirm the summary shows 4852/4852 records and 0 missing.

The second recovery pass examines only the remaining candidate pages. It does
not rebuild or delete the 4,850 records already present.
