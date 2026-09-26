RAG Knowledge Assistant v6.2.1
================================

This release fixes the issues found by the 60-question real-data acceptance suite.

Improvements
------------
- PDF names now act as document scopes for voter queries instead of changing
  the query into a document-metadata query.
- Document scoping also works when a filename is followed by sentence punctuation.
- Exact EPIC and document-scoped serial-number searches no longer acquire an
  accidental name filter.
- Single oldest/youngest requests honor their requested sort direction and limit.
- Cross-script name matching rejects nearby but different consonant names such
  as Naseem, Nasir and Nazim.
- Document summaries, topic overviews and comparisons retrieve representative
  evidence directly from the embedded collections.
- Local answer generation has a 30-second default time limit and returns cited
  evidence if the local model is slow or unavailable.
- Low-confidence semantic neighbours cannot support factual answers; unsupported
  questions now return the explicit no-evidence response instead of random rows.

Existing indexing, recovery, manual verification, exports and supported query
behavior from v6.2.0 remain unchanged.

Acceptance baseline corrections
-------------------------------
- The five oldest indexed ages are 97, 91, 90, 89 and 88.
- Strict all-name-field matching finds seven reviewed Naseem records. Similar
  but different names such as Nasir, Nazim and Asim are excluded.
