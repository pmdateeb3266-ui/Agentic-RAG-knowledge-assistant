import os

APP_VERSION = "6.4.0"

# ============================================================
# PATHS
# ============================================================

DOCUMENTS_FOLDER = os.getenv("RAG_DOCUMENTS_FOLDER", "documents")
DATABASE_FOLDER = os.getenv("RAG_DATABASE_FOLDER", "database")
SQLITE_PATH = os.path.join(DATABASE_FOLDER, "structured.db")

CHUNK_COLLECTION_NAME = os.getenv("RAG_CHUNK_COLLECTION", "rag_documents")
RECORD_COLLECTION_NAME = os.getenv("RAG_RECORD_COLLECTION", "rag_records")

# ============================================================
# MODELS
# ============================================================

EMBEDDING_MODEL = os.getenv(
    "RAG_EMBEDDING_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
LLM_MODEL = os.getenv("RAG_LLM_MODEL", "qwen3:1.7b")
OLLAMA_TIMEOUT_SECONDS = float(os.getenv("RAG_OLLAMA_TIMEOUT_SECONDS", "30"))

# ============================================================
# OCR
# ============================================================

OCR_DPI = int(os.getenv("RAG_OCR_DPI", "300"))
MIN_NATIVE_TEXT_LENGTH = int(os.getenv("RAG_MIN_NATIVE_TEXT_LENGTH", "60"))
MIN_OCR_CONFIDENCE = float(os.getenv("RAG_MIN_OCR_CONFIDENCE", "30"))

OCR_CANDIDATES = [
    "eng+deu",
    "ara+eng",
    "hin+eng",
    "kan+eng",
    "tel+eng",
]

# Add known PDFs here. This is recommended because it avoids testing every
# OCR language combination on every page.
DOCUMENT_LANGUAGE_HINTS = {
    "17576784747921.pdf": "tel+eng",
}

# Known, document-specific OCR substitutions for categorical fields. Keep these
# scoped to a source file: a value such as "3" must never mean female globally.
DOCUMENT_GENDER_ALIASES = {
    "17576784761392.pdf": {"3": "female"},
    "17576784804969.pdf": {"3": "female"},
}

# ============================================================
# TABLE DETECTION / CELL OCR
# ============================================================

TABLE_MIN_VERTICAL_LINES = int(os.getenv("RAG_TABLE_MIN_VERTICAL", "4"))
TABLE_MIN_HORIZONTAL_LINES = int(os.getenv("RAG_TABLE_MIN_HORIZONTAL", "5"))
TABLE_PROJECTION_RATIO = float(os.getenv("RAG_TABLE_PROJECTION_RATIO", "0.12"))
TABLE_LINE_KERNEL_PIXELS = int(os.getenv("RAG_TABLE_LINE_KERNEL", "80"))
CELL_PADDING = int(os.getenv("RAG_CELL_PADDING", "5"))

# A voter/electoral-roll style table is recognized when the grid has 8 columns
# and several rows whose first cell looks like a serial number.
VOTER_ROLL_COLUMNS = 8
VOTER_ROLL_MIN_VALID_ROWS = int(os.getenv("RAG_VOTER_MIN_ROWS", "3"))

# ============================================================
# CHUNKING / RETRIEVAL
# ============================================================

CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "220"))
CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "40"))
SEMANTIC_RESULTS = int(os.getenv("RAG_SEMANTIC_RESULTS", "8"))
RECORD_SEMANTIC_RESULTS = int(os.getenv("RAG_RECORD_SEMANTIC_RESULTS", "8"))
SEMANTIC_MAX_DISTANCE = float(os.getenv("RAG_SEMANTIC_MAX_DISTANCE", "0.72"))
SEMANTIC_HIGH_CONFIDENCE_DISTANCE = float(os.getenv("RAG_SEMANTIC_HIGH_CONFIDENCE_DISTANCE", "0.38"))
SEMANTIC_MODERATE_CONFIDENCE_DISTANCE = float(os.getenv("RAG_SEMANTIC_MODERATE_CONFIDENCE_DISTANCE", "0.55"))
SEMANTIC_MIN_EVIDENCE = int(os.getenv("RAG_SEMANTIC_MIN_EVIDENCE", "1"))
MAX_CONTEXT_CHARACTERS = int(os.getenv("RAG_MAX_CONTEXT_CHARS", "32000"))
STRUCTURED_PAGE_SIZE = int(os.getenv("RAG_STRUCTURED_PAGE_SIZE", "25"))
CURRENT_DOCUMENT = os.getenv("RAG_CURRENT_DOCUMENT", "").strip()
EXPORTS_FOLDER = os.getenv("RAG_EXPORTS_FOLDER", "exports")
BACKUPS_FOLDER = os.getenv("RAG_BACKUPS_FOLDER", "backups")
LOW_RECORD_CONFIDENCE = float(os.getenv("RAG_LOW_RECORD_CONFIDENCE", "60"))
PREFERRED_OUTPUT_LANGUAGE = os.getenv("RAG_PREFERRED_OUTPUT_LANGUAGE", "").strip().lower()

# ============================================================
# LOCAL ACCESS CONTROL
# ============================================================

SESSION_TIMEOUT_MINUTES = max(5, int(os.getenv("RAG_SESSION_TIMEOUT_MINUTES", "30")))
AUTH_COOKIE_SECURE = os.getenv("RAG_AUTH_COOKIE_SECURE", "false").strip().lower() in {
    "1", "true", "yes", "on",
}

# ============================================================
# OLLAMA
# ============================================================

MAX_OUTPUT_TOKENS = int(os.getenv("RAG_MAX_OUTPUT_TOKENS", "800"))
OLLAMA_NUM_CTX = int(os.getenv("RAG_OLLAMA_NUM_CTX", "16384"))
