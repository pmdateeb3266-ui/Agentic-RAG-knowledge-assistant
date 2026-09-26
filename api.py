import io
import hmac
import json
import os
import re
import subprocess
import sys
import threading
import uuid
import zipfile
from pathlib import Path

import ollama
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import (
    APP_VERSION, BACKUPS_FOLDER, CHUNK_COLLECTION_NAME, DATABASE_FOLDER, DOCUMENTS_FOLDER, EXPORTS_FOLDER,
    LLM_MODEL, OLLAMA_TIMEOUT_SECONDS, RECORD_COLLECTION_NAME,
)
from backup_manager import (
    BackupError, backup_file_path, create_backup, list_backups,
    stage_restore, verify_backup,
)
from auth_manager import (
    AuthError, audit_events, authenticate, change_own_password, create_first_admin, create_user,
    end_session, list_users, log_security_event, read_session,
    reset_user_password, set_user_enabled, start_session, user_count,
)
from evidence_processor import deterministic_summary, no_evidence_message, render_structured_records, verified_facts_text
from query_analyzer import analyze_query
from record_editor import (
    EDITABLE_FIELDS, RecordEditConflict, RecordEditError, RecordNotFound,
    apply_record_update, correction_history, rollback_correction,
    row_value, sync_record_page, restore_page_vectors,
)
from result_exporter import export_csv, export_entity_csv
from retriever import assess_record_quality, retrieve, validate_record
from storage import connect, initialize_database

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
REPORTS_DIR = BASE_DIR / "reports"
jobs = {}
jobs_lock = threading.Lock()


class QueryRequest(BaseModel):
    question: str
    language: str | None = None
    document: str | None = None
    page: int = 1
    show_all: bool = False


class ExportRequest(QueryRequest):
    pass


class ReviewUpdateRequest(BaseModel):
    serial_number: str | None = None
    house_number: str | None = None
    voter_name: str | None = None
    relationship: str | None = None
    related_name: str | None = None
    gender: str | None = None
    age: str | None = None
    epic_id: str | None = None
    note: str = ""
    expected_revision: int | None = None


class RollbackRequest(BaseModel):
    expected_revision: int | None = None


class RestoreBackupRequest(BaseModel):
    confirmation: str


class SetupRequest(BaseModel):
    username: str
    display_name: str = ""
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(SetupRequest):
    role: str = "reviewer"


class UserEnabledRequest(BaseModel):
    enabled: bool


class PasswordResetRequest(BaseModel):
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


initialize_database(clean=False).close()
app = FastAPI(title="Multilingual RAG Knowledge Assistant", version=APP_VERSION)
app.state.auth_test_bypass = False

SESSION_COOKIE = "rag_session"
PUBLIC_API_PATHS = {
    "/api/health", "/api/auth/status", "/api/auth/setup", "/api/auth/login",
}


def _session_user_payload(session):
    if not session:
        return None
    return {
        "id": session["user_id"], "username": session["username"],
        "display_name": session["display_name"], "role": session["role"],
    }


def _set_session_cookie(response, token):
    from config import AUTH_COOKIE_SECURE, SESSION_TIMEOUT_MINUTES
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_TIMEOUT_MINUTES * 60,
        httponly=True, secure=AUTH_COOKIE_SECURE, samesite="strict", path="/",
    )


def _requires_admin(path, method):
    if path.startswith("/api/admin/") or path == "/api/admin":
        return True
    if path.startswith("/api/backups") or path.startswith("/api/reports/"):
        return True
    return path in {
        "/api/documents/upload", "/api/quality/reprocess",
        "/api/integrity/recover", "/api/accuracy/run",
    }


@app.middleware("http")
async def protect_api(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api") or path in PUBLIC_API_PATHS or app.state.auth_test_bypass:
        return await call_next(request)
    session_token = request.cookies.get(SESSION_COOKIE)
    session = read_session(session_token)
    if not session:
        detail = "Sign in to continue" if user_count() else "Create the first administrator account to continue"
        return JSONResponse({"detail": detail}, status_code=401)
    request.state.user = session
    if _requires_admin(path, request.method) and session["role"] != "admin":
        try:
            log_security_event(session, "permission_denied", False, {"method": request.method, "path": path})
        except Exception:
            pass
        return JSONResponse({"detail": "Administrator permission is required"}, status_code=403)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if not hmac.compare_digest(
            str(request.headers.get("X-CSRF-Token") or ""), str(session["csrf"])
        ):
            try:
                log_security_event(session, "security_token_denied", False, {"method": request.method, "path": path})
            except Exception:
                pass
            return JSONResponse({"detail": "The security token is missing or expired. Sign in again."}, status_code=403)
    response = await call_next(request)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and path != "/api/query":
        try:
            log_security_event(
                session, f"{request.method} {path}", response.status_code < 400,
                {"status": response.status_code},
            )
        except Exception:
            pass
    if path not in {"/api/auth/logout", "/api/auth/change-password"} and read_session(session_token):
        _set_session_cookie(response, session_token)
    return response


def _trust_payload(plan, facts, results):
    structured = plan.get("intent") == "STRUCTURED_QUERY" or facts.get("execution_path") in {
        "sqlite_structured", "sqlite_exact"
    }
    citations = []
    seen = set()
    if structured:
        for item in results:
            metadata = item.get("metadata") or {}
            key = (str(metadata.get("source", "")), metadata.get("page"))
            if key in seen:
                continue
            seen.add(key)
            citations.append({"source": key[0], "page": key[1]})
        if not citations:
            citations = [{"source": source, "page": None} for source in facts.get("documents", [])]
    else:
        citations = list(facts.get("semantic_citations") or [])
    return {
        "path": facts.get("execution_path") or ("sqlite_structured" if structured else "chroma_semantic"),
        "confidence": facts.get("trust_confidence") or ("deterministic" if structured else "insufficient"),
        "citations": citations,
        "evidence_count": facts.get("semantic_evidence_count") if not structured else facts.get("matched_records", 0),
    }


def _documents():
    conn = connect()
    rows = conn.execute(
        """
        SELECT source, page_count, language_hint, expected_records,
               index_status, created_at
        FROM documents ORDER BY source
        """
    ).fetchall()
    counts = dict(conn.execute("SELECT source, COUNT(*) FROM records GROUP BY source").fetchall())
    conn.close()
    documents = []
    for row in rows:
        item = dict(row)
        item["record_count"] = counts.get(row["source"], 0)
        expected = int(item.get("expected_records") or 0)
        item["missing_records"] = max(0, expected - item["record_count"]) if expected else None
        documents.append(item)
    return documents


def _prepare_plan(request):
    question = request.question.strip()
    if not question:
        raise HTTPException(400, "Question is required")
    if request.language:
        names = {"en": "English", "te": "Telugu", "hi": "Hindi", "kn": "Kannada", "de": "German", "ar": "Arabic"}
        question += f" in {names.get(request.language, 'English')}"
    if request.document and request.document != "all":
        question += f" from {request.document}"
    if request.page > 1:
        question += f" page {request.page}"
    if request.show_all:
        question += " show all"
    return question, analyze_query(question)


def _semantic_answer(question, plan, bundle):
    evidence = "\n\n".join(
        f"[S{i}] {item['metadata'].get('source')} page {item['metadata'].get('page')}\n{item['document']}"
        for i, item in enumerate(bundle["results"][:16], 1)
    )
    prompt = f"""/no_think
Answer only from the supplied evidence. Use {plan['output_language']} for the answer.
Preserve personal names in their original script; optionally add romanization. Cite facts with [S1], [S2].
Question: {question}
Verified facts: {verified_facts_text(plan, bundle['facts'])}
Evidence:\n{evidence}
Return only the answer."""
    client = ollama.Client(timeout=OLLAMA_TIMEOUT_SECONDS)
    response = client.chat(model=LLM_MODEL, messages=[{"role": "user", "content": prompt}], options={"temperature": 0})
    message = getattr(response, "message", None)
    return str(getattr(message, "content", "") or "")


def _grounded_semantic_fallback(plan, bundle):
    """Return cited evidence when the local answer model is slow or unavailable."""
    results = bundle.get("results") or []
    language = plan.get("output_language", "en")
    headings = {
        "en": "Relevant evidence from the selected documents:",
        "te": "ఎంచుకున్న పత్రాల నుండి సంబంధిత ఆధారాలు:",
        "hi": "चुने गए दस्तावेज़ों से संबंधित साक्ष्य:",
        "kn": "ಆಯ್ಕೆಮಾಡಿದ ದಾಖಲೆಗಳಿಂದ ಸಂಬಂಧಿತ ಸಾಕ್ಷ್ಯ:",
        "de": "Relevante Belege aus den ausgewählten Dokumenten:",
        "ar": "الأدلة ذات الصلة من المستندات المحددة:",
    }
    lines = [headings.get(language, headings["en"])]
    for index, item in enumerate(results[:5], 1):
        metadata = item.get("metadata") or {}
        snippet = re.sub(r"\s+", " ", str(item.get("snippet") or item.get("document") or "")).strip()
        if len(snippet) > 450:
            snippet = snippet[:447].rstrip() + "..."
        lines.append(f"[S{index}] {metadata.get('source', '')} page {metadata.get('page', '')}: {snippet}")
    return "\n".join(lines)


@app.get("/api/health")
def health():
    return {"status": "ok", "version": APP_VERSION}


@app.get("/api/auth/status")
def auth_status(request: Request):
    setup_required = user_count() == 0
    session = read_session(request.cookies.get(SESSION_COOKIE))
    return {
        "setup_required": setup_required,
        "authenticated": bool(session),
        "user": _session_user_payload(session),
        "csrf_token": session.get("csrf") if session else None,
    }


@app.post("/api/auth/setup")
def setup_administrator(request: SetupRequest):
    try:
        user = create_first_admin(request.username, request.display_name, request.password)
    except AuthError as error:
        raise HTTPException(400, str(error)) from error
    token, session = start_session(user)
    response = JSONResponse({
        "authenticated": True, "user": user, "csrf_token": session["csrf"],
    })
    _set_session_cookie(response, token)
    return response


@app.post("/api/auth/login")
def login(request: LoginRequest):
    if user_count() == 0:
        raise HTTPException(409, "Create the first administrator account before signing in")
    user = authenticate(request.username, request.password)
    if not user:
        log_security_event({"username": str(request.username or "")}, "login_failed", False)
        raise HTTPException(401, "Incorrect username or password")
    token, session = start_session(user)
    log_security_event(session, "login", True)
    response = JSONResponse({
        "authenticated": True, "user": user, "csrf_token": session["csrf"],
    })
    _set_session_cookie(response, token)
    return response


@app.post("/api/auth/logout")
def logout(request: Request):
    session = request.state.user
    log_security_event(session, "logout", True)
    end_session(request.cookies.get(SESSION_COOKIE))
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/api/auth/me")
def current_user(request: Request):
    return {"user": _session_user_payload(request.state.user), "csrf_token": request.state.user["csrf"]}


@app.post("/api/auth/change-password")
def change_password(request: ChangePasswordRequest, http_request: Request):
    try:
        change_own_password(
            http_request.state.user["user_id"], request.current_password, request.new_password
        )
        response = JSONResponse({"changed": True, "sign_in_again": True})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response
    except AuthError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/api/admin/users")
def admin_users():
    return {"users": list_users()}


@app.post("/api/admin/users")
def admin_create_user(request: CreateUserRequest, http_request: Request):
    try:
        user = create_user(
            request.username, request.display_name, request.password,
            request.role, http_request.state.user["username"],
        )
        return {"user": user}
    except AuthError as error:
        raise HTTPException(400, str(error)) from error


@app.put("/api/admin/users/{user_id}/enabled")
def admin_set_user_enabled(user_id: int, request: UserEnabledRequest, http_request: Request):
    try:
        return {"user": set_user_enabled(user_id, request.enabled, http_request.state.user["user_id"])}
    except AuthError as error:
        raise HTTPException(400, str(error)) from error


@app.put("/api/admin/users/{user_id}/password")
def admin_reset_password(user_id: int, request: PasswordResetRequest):
    try:
        reset_user_password(user_id, request.password)
        return {"changed": True}
    except AuthError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/api/admin/audit")
def admin_audit(limit: int = Query(100, ge=1, le=500)):
    return {"events": audit_events(limit)}


@app.get("/api/documents")
def list_documents():
    documents = _documents()
    known = [item for item in documents if int(item.get("expected_records") or 0) > 0]
    return {
        "documents": documents,
        "totals": {
            "indexed": sum(int(item["record_count"]) for item in documents),
            "expected": sum(int(item["expected_records"]) for item in known),
            "missing": sum(int(item["missing_records"] or 0) for item in known),
            "audited_documents": len(known),
        },
    }


@app.get("/api/integrity")
def integrity_status():
    return list_documents()


@app.post("/api/query")
def query(request: QueryRequest):
    question, plan = _prepare_plan(request)
    bundle = retrieve(question, plan)
    if plan["intent"] == "STRUCTURED_QUERY":
        answer = render_structured_records(plan, bundle["facts"], bundle["results"]) if bundle["results"] else deterministic_summary(plan, bundle["facts"])
    else:
        if not bundle["results"]:
            answer = no_evidence_message(plan.get("output_language", "en"))
        else:
            try:
                answer = _semantic_answer(question, plan, bundle)
            except Exception:
                answer = _grounded_semantic_fallback(plan, bundle)
    return {
        "question": question,
        "plan": plan,
        "facts": bundle["facts"],
        "results": bundle["results"],
        "answer": answer,
        "trust": _trust_payload(plan, bundle["facts"], bundle["results"]),
    }


@app.post("/api/export")
def export(request: ExportRequest):
    question, plan = _prepare_plan(request)
    plan["pagination"] = {"page": 1, "show_all": True}
    bundle = retrieve(question, plan)
    if plan["intent"] != "STRUCTURED_QUERY":
        raise HTTPException(400, "CSV export requires a structured query")
    if plan.get("entity") in {"polling_booth", "document"}:
        path, record_count = export_entity_csv(question, bundle["facts"])
    else:
        path = export_csv(question, bundle["results"], plan.get("output_language", "en"))
        record_count = len(bundle["results"])
    return {"filename": Path(path).name, "records": record_count, "download_url": f"/api/exports/{Path(path).name}"}


@app.get("/api/exports/{filename}")
def download_export(filename: str):
    safe = Path(filename).name
    path = Path(EXPORTS_FOLDER).resolve() / safe
    if not path.is_file():
        raise HTTPException(404, "Export not found")
    return FileResponse(path, filename=safe, media_type="text/csv")


def _ensure_backup_idle():
    with jobs_lock:
        active = [
            value for value in jobs.values()
            if value.get("status") in {"queued", "running"}
        ]
    if active:
        raise HTTPException(409, "Wait for the current indexing or accuracy job to finish before backing up or restoring")


@app.get("/api/backups")
def backups():
    pending = (Path(BACKUPS_FOLDER).resolve() / "pending_restore.zip").is_file()
    return {"backups": list_backups(), "restore_pending": pending}


@app.post("/api/backups")
def create_data_backup():
    _ensure_backup_idle()
    try:
        return create_backup("manual")
    except BackupError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/api/backups/{filename}/verify")
def verify_data_backup(filename: str):
    try:
        return verify_backup(filename)
    except (BackupError, OSError, zipfile.BadZipFile) as error:
        raise HTTPException(400, str(error)) from error


@app.post("/api/backups/{filename}/restore")
def restore_data_backup(filename: str, request: RestoreBackupRequest):
    _ensure_backup_idle()
    try:
        return stage_restore(filename, request.confirmation)
    except BackupError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/api/backups/{filename}/download")
def download_data_backup(filename: str):
    try:
        path = backup_file_path(filename)
    except BackupError as error:
        raise HTTPException(404, str(error)) from error
    return FileResponse(path, filename=path.name, media_type="application/zip")


@app.get("/api/quality")
def quality_status():
    connection = connect()
    rows = list(connection.execute("SELECT * FROM records"))
    connection.close()
    review = 0
    notice_records = 0
    warnings = {}
    notices = {}
    review_pages = set()
    manually_verified = 0
    for row in rows:
        if str(row_value(row, "verification_status", "")) == "verified":
            manually_verified += 1
        assessment = assess_record_quality(row)
        issues = assessment["warnings"]
        source_notices = assessment["notices"]
        if issues:
            review += 1
            review_pages.add((str(row["source"]), int(row["page"])))
        for issue in issues:
            category = re.sub(r"\s*\([^)]*\)$", "", issue)
            warnings[category] = warnings.get(category, 0) + 1
        if source_notices:
            notice_records += 1
        for notice in source_notices:
            notices[notice] = notices.get(notice, 0) + 1
    return {
        "total_records": len(rows),
        "review_records": review,
        "clear_records": len(rows) - review,
        "review_pages": len(review_pages),
        "warning_categories": dict(sorted(warnings.items(), key=lambda item: (-item[1], item[0]))),
        "notice_records": notice_records,
        "notice_categories": dict(sorted(notices.items(), key=lambda item: (-item[1], item[0]))),
        "manually_verified_records": manually_verified,
        "snapshot": True,
    }


def _warning_category(value):
    return re.sub(r"\s*\([^)]*\)$", "", str(value or "")).strip()


def _review_item(row, raw_assessment=None):
    raw_assessment = raw_assessment or assess_record_quality(row, respect_verification=False)
    current_assessment = assess_record_quality(row)
    return {
        "id": int(row["id"]),
        "source": str(row["source"]),
        "page": int(row["page"]),
        "row_index": int(row["row_index"] or 0),
        "ocr_confidence": float(row["ocr_confidence"] or 0),
        "warnings": raw_assessment["warnings"],
        "notices": current_assessment["notices"],
        "verification_status": str(row_value(row, "verification_status", "unreviewed")),
        "verified_at": row_value(row, "verified_at"),
        "verified_by": row_value(row, "verified_by"),
        "verification_note": row_value(row, "verification_note"),
        "record_revision": int(row_value(row, "record_revision", 0) or 0),
        "fields": {field: str(row_value(row, field, "") or "") for field in EDITABLE_FIELDS},
        "image_url": f"/api/review/records/{int(row['id'])}/image",
    }


def _review_row(connection, record_id):
    row = connection.execute("SELECT * FROM records WHERE id=?", (int(record_id),)).fetchone()
    if not row:
        raise HTTPException(404, "Review record not found")
    return row


@app.get("/api/review/records")
def review_records(
    source: str = "all",
    document_page: int | None = Query(None, ge=1),
    warning: str = "all",
    status: str = "needs_review",
    queue_page: int = Query(1, alias="page", ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if status not in {"needs_review", "verified", "all"}:
        raise HTTPException(400, "Unsupported review status")
    connection = connect()
    rows = list(connection.execute("SELECT * FROM records ORDER BY source, page, row_index"))
    connection.close()
    source_names = sorted({str(row["source"]) for row in rows})
    warning_names = sorted({
        _warning_category(item)
        for row in rows
        for item in assess_record_quality(row, respect_verification=False)["warnings"]
    })
    filtered = []
    for row in rows:
        if source != "all" and str(row["source"]) != Path(source).name:
            continue
        if document_page is not None and int(row["page"]) != int(document_page):
            continue
        raw = assess_record_quality(row, respect_verification=False)
        verified = str(row_value(row, "verification_status", "")) == "verified"
        if status == "needs_review" and (verified or not raw["warnings"]):
            continue
        if status == "verified" and not verified:
            continue
        if status == "all" and not (verified or raw["warnings"]):
            continue
        if warning != "all" and warning not in {_warning_category(item) for item in raw["warnings"]}:
            continue
        filtered.append((row, raw))
    filtered_pages = sorted({int(row["page"]) for row, _ in filtered})
    total = len(filtered)
    total_pages = max(1, (total + page_size - 1) // page_size)
    queue_page = min(queue_page, total_pages)
    start = (queue_page - 1) * page_size
    return {
        "records": [_review_item(row, raw) for row, raw in filtered[start:start + page_size]],
        "total": total,
        "page": queue_page,
        "page_size": page_size,
        "total_pages": total_pages,
        "filters": {
            "sources": source_names,
            "warnings": warning_names,
            "document_pages": filtered_pages,
        },
    }


@app.get("/api/review/records/{record_id}")
def review_record_detail(record_id: int):
    connection = connect()
    try:
        row = _review_row(connection, record_id)
        return {
            "record": _review_item(row),
            "history": correction_history(connection, record_id),
        }
    finally:
        connection.close()


@app.get("/api/review/records/{record_id}/image")
def review_record_image(record_id: int):
    connection = connect()
    try:
        row = _review_row(connection, record_id)
        source = str(row["source"])
        page_number = int(row["page"])
        row_index = int(row["row_index"] or 0)
        try:
            raw = json.loads(str(row["raw_json"] or "{}"))
        except (TypeError, ValueError):
            raw = {}
    finally:
        connection.close()

    folder = Path(DOCUMENTS_FOLDER)
    if not folder.is_absolute():
        folder = BASE_DIR / folder
    folder = folder.resolve()
    pdf_path = (folder / Path(source).name).resolve()
    if pdf_path.parent != folder or not pdf_path.is_file():
        raise HTTPException(404, "The source PDF is not available")

    import pymupdf
    from table_ocr import detect_table_grid, render_page, _normalize_grid_for_eight_columns

    try:
        with pymupdf.open(pdf_path) as document:
            if not 1 <= page_number <= len(document):
                raise HTTPException(404, "The source page is not available")
            image = render_page(document[page_number - 1])
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(500, f"The source row could not be rendered: {error}") from error

    bbox = raw.get("bbox") if isinstance(raw, dict) else None
    if not (
        isinstance(bbox, list) and len(bbox) == 4
        and all(isinstance(value, (int, float)) for value in bbox)
    ):
        grid = detect_table_grid(image)
        grid = _normalize_grid_for_eight_columns(grid) if grid else None
        ys = grid["y_lines"] if grid else []
        xs = grid["x_lines"] if grid else []
        if not xs or row_index < 0 or row_index + 1 >= len(ys):
            raise HTTPException(404, "A source-row position is not available for this record")
        bbox = [xs[0], ys[row_index], xs[-1], ys[row_index + 1]]

    margin = 18
    left = max(0, int(bbox[0]) - margin)
    top = max(0, int(bbox[1]) - margin)
    right = min(image.width, int(bbox[2]) + margin)
    bottom = min(image.height, int(bbox[3]) + margin)
    if right <= left or bottom <= top:
        raise HTTPException(404, "The stored source-row position is invalid")
    crop = image.crop((left, top, right, bottom))
    stream = io.BytesIO()
    crop.save(stream, format="PNG", optimize=True)
    stream.seek(0)
    return StreamingResponse(
        stream,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=300"},
    )


def _request_payload(request):
    if hasattr(request, "model_dump"):
        return request.model_dump(exclude_unset=True)
    return request.dict(exclude_unset=True)


def _commit_review_change(connection, source, page, operation):
    synchronized = False
    try:
        result = operation()
        sync = sync_record_page(connection, source, page)
        synchronized = True
        connection.commit()
        return result, sync
    except Exception:
        connection.rollback()
        if synchronized:
            try:
                restore_page_vectors(connection, source, page)
            except Exception:
                pass
        raise


@app.put("/api/review/records/{record_id}")
def update_review_record(record_id: int, request: ReviewUpdateRequest, http_request: Request):
    connection = connect()
    try:
        existing = _review_row(connection, record_id)
        source, page = str(existing["source"]), int(existing["page"])
        payload = _request_payload(request)
        updates = {field: payload[field] for field in EDITABLE_FIELDS if field in payload}
        connection.execute("BEGIN IMMEDIATE")

        def operation():
            actor = getattr(http_request.state, "user", {}).get("username", "local_user")
            return apply_record_update(
                connection,
                record_id,
                updates,
                note=request.note,
                expected_revision=request.expected_revision,
                verified_by=actor,
            )

        (updated, correction_id), sync = _commit_review_change(
            connection, source, page, operation
        )
        return {
            "record": _review_item(updated),
            "history": correction_history(connection, record_id),
            "correction_id": correction_id,
            "synchronized": sync,
        }
    except HTTPException:
        raise
    except RecordNotFound as error:
        raise HTTPException(404, str(error)) from error
    except RecordEditConflict as error:
        raise HTTPException(409, str(error)) from error
    except RecordEditError as error:
        raise HTTPException(422, str(error)) from error
    except Exception as error:
        raise HTTPException(500, f"The correction was not saved: {error}") from error
    finally:
        connection.close()


@app.post("/api/review/corrections/{correction_id}/rollback")
def rollback_review_correction(correction_id: int, request: RollbackRequest, http_request: Request):
    connection = connect()
    try:
        correction = connection.execute(
            "SELECT record_id FROM record_corrections WHERE id=?", (int(correction_id),)
        ).fetchone()
        if not correction:
            raise HTTPException(404, "Correction history entry not found")
        existing = _review_row(connection, int(correction["record_id"]))
        source, page = str(existing["source"]), int(existing["page"])
        connection.execute("BEGIN IMMEDIATE")

        def operation():
            actor = getattr(http_request.state, "user", {}).get("username", "local_user")
            return rollback_correction(
                connection,
                correction_id,
                expected_revision=request.expected_revision,
                reverted_by=actor,
            )

        (updated, rollback_id), sync = _commit_review_change(
            connection, source, page, operation
        )
        return {
            "record": _review_item(updated),
            "history": correction_history(connection, int(updated["id"])),
            "rollback_id": rollback_id,
            "synchronized": sync,
        }
    except HTTPException:
        raise
    except RecordNotFound as error:
        raise HTTPException(404, str(error)) from error
    except RecordEditConflict as error:
        raise HTTPException(409, str(error)) from error
    except RecordEditError as error:
        raise HTTPException(422, str(error)) from error
    except Exception as error:
        raise HTTPException(500, f"The correction could not be rolled back: {error}") from error
    finally:
        connection.close()


@app.post("/api/quality/reprocess")
def reprocess_quality(background_tasks: BackgroundTasks, source: str | None = None):
    if _active_job():
        raise HTTPException(409, "Another indexing, review, or accuracy job is already running")
    arguments = ["--apply"]
    if source:
        arguments.extend(["--source", Path(source).name])
    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {
            "status": "queued", "kind": "review", "message": "Preparing targeted OCR review…",
            "processed_pages": 0, "total_pages": 0, "progress_percent": None,
        }
    background_tasks.add_task(_run_tool_job, job_id, "review_ocr.py", arguments, "review")
    return {"job_id": job_id}


@app.post("/api/integrity/recover")
def recover_integrity(background_tasks: BackgroundTasks, source: str | None = None):
    if _active_job():
        raise HTTPException(409, "Another indexing, recovery, review, or accuracy job is already running")
    arguments = ["--apply"]
    if source and source != "all":
        arguments.extend(["--source", Path(source).name])
    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {
            "status": "queued",
            "kind": "recovery",
            "message": "Auditing serial numbers and locating missing records…",
            "processed_pages": 0,
            "total_pages": 0,
            "progress_percent": None,
        }
    background_tasks.add_task(
        _run_tool_job,
        job_id,
        "recover_missing_records.py",
        arguments,
        "recovery",
    )
    return {"job_id": job_id}


@app.post("/api/accuracy/run")
def run_accuracy(background_tasks: BackgroundTasks):
    if _active_job():
        raise HTTPException(409, "Another indexing, review, or accuracy job is already running")
    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {
            "status": "queued", "kind": "accuracy", "message": "Preparing accuracy checks…",
            "processed_pages": 0, "total_pages": 0, "progress_percent": None,
        }
    background_tasks.add_task(_run_tool_job, job_id, "accuracy_report.py", [], "accuracy")
    return {"job_id": job_id}


@app.get("/api/reports/{filename}")
def download_report(filename: str):
    safe = Path(filename).name
    report_directory = REPORTS_DIR.resolve()
    path = (report_directory / safe).resolve()
    if path.parent != report_directory or not path.is_file():
        raise HTTPException(404, "Report not found")
    media_type = "application/json" if path.suffix.casefold() == ".json" else "text/plain"
    return FileResponse(path, filename=safe, media_type=media_type)


def _document_page_total(filename=None):
    try:
        import pymupdf
        folder = Path(DOCUMENTS_FOLDER)
        if not folder.is_absolute():
            folder = BASE_DIR / folder
        paths = [folder / Path(filename).name] if filename else list(folder.glob("*.pdf"))
        total = 0
        for path in paths:
            if not path.is_file():
                continue
            with pymupdf.open(path) as document:
                total += len(document)
        return total
    except Exception:
        return 0


def _active_job():
    with jobs_lock:
        return next((job_id for job_id, item in jobs.items() if item.get("status") in {"queued", "running"}), None)


def _cleanup_partial_index(filename):
    """Remove only an interrupted new document, leaving every old document intact."""
    safe = Path(filename).name
    connection = connect()
    try:
        connection.execute("DELETE FROM records WHERE source=?", (safe,))
        connection.execute("DELETE FROM pages WHERE source=?", (safe,))
        connection.execute("DELETE FROM documents WHERE source=?", (safe,))
        connection.commit()
    finally:
        connection.close()
    try:
        import chromadb
        client = chromadb.PersistentClient(path=DATABASE_FOLDER)
        for collection_name in (CHUNK_COLLECTION_NAME, RECORD_COLLECTION_NAME):
            try:
                client.get_collection(collection_name).delete(where={"source": safe})
            except Exception:
                pass
    except Exception:
        pass


def _run_tool_job(job_id, script_name, arguments, kind):
    log_tail = []
    total = 0
    completed = 0
    report_filename = None
    with jobs_lock:
        jobs[job_id].update({"status": "running", "message": "Starting…"})
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        [sys.executable, "-u", str(BASE_DIR / script_name), *arguments],
        cwd=BASE_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        creationflags=creation_flags,
    )
    for raw_line in process.stdout or []:
        line = raw_line.strip()
        if not line:
            continue
        log_tail = (log_tail + [line])[-30:]
        review_total = re.match(r"(?:REVIEW|RECOVERY)_PAGES\s+(\d+)", line)
        progress = re.match(r"PROGRESS\s+(\d+)/(\d+)\s+(.+)", line)
        report = re.match(r"ACCURACY_REPORT\s+(.+)", line)
        if review_total:
            total = int(review_total.group(1))
        if progress:
            completed, total = int(progress.group(1)), int(progress.group(2))
            message = f"Reviewing {progress.group(3)}"
        else:
            messages = {
                "accuracy": "Running accuracy and index checks…",
                "recovery": "Auditing serial numbers and recovering missing records…",
                "review": "Reviewing OCR quality…",
            }
            message = messages.get(kind, "Working…")
        if report:
            report_filename = Path(report.group(1)).name
        percent = round(completed * 100 / total) if total else None
        with jobs_lock:
            jobs[job_id].update({
                "message": message,
                "processed_pages": completed,
                "total_pages": total,
                "progress_percent": percent,
            })
    code = process.wait()
    if code == 0:
        messages = {
            "accuracy": "Accuracy report complete",
            "recovery": "Missing-record recovery complete",
            "review": "OCR review complete",
        }
        message = messages.get(kind, "Job complete")
        update = {"status": "complete", "message": message, "progress_percent": 100}
        if report_filename:
            update["download_url"] = f"/api/reports/{report_filename}"
    else:
        update = {
            "status": "failed",
            "message": "The job failed. " + (log_tail[-1] if log_tail else "No diagnostic message was returned."),
        }
    with jobs_lock:
        jobs[job_id].update(update)


def _run_ingestion(job_id, filename):
    total_pages = _document_page_total(filename)
    processed_pages = 0
    current_document = ""
    log_tail = []
    with jobs_lock:
        jobs[job_id] = {
            "status": "running", "kind": "index", "message": "Loading indexing models…",
            "processed_pages": 0, "total_pages": total_pages, "progress_percent": 0 if total_pages else None,
        }
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            [sys.executable, "-u", str(BASE_DIR / "ingest.py"), "--source", Path(filename).name],
            cwd=BASE_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creation_flags,
        )
    except Exception as error:
        _cleanup_partial_index(filename)
        with jobs_lock:
            jobs[job_id].update({"status": "failed", "message": f"Indexing could not start: {error}"})
        return
    for raw_line in process.stdout or []:
        line = raw_line.strip()
        if not line:
            continue
        log_tail = (log_tail + [line])[-30:]
        document_match = re.match(r"PROCESSING:\s*(.+)", line)
        if document_match:
            current_document = document_match.group(1).strip()
        if re.match(r"Page\s+\d+:", line):
            processed_pages += 1
        percent = round(processed_pages * 100 / total_pages) if total_pages else None
        message = f"Indexing {current_document or 'documents'}"
        if total_pages:
            message += f" · page {processed_pages} of {total_pages}"
        elif processed_pages:
            message += f" · page {processed_pages}"
        with jobs_lock:
            jobs[job_id].update({
                "message": message,
                "current_document": current_document,
                "processed_pages": processed_pages,
                "total_pages": total_pages,
                "progress_percent": percent,
            })
    return_code = process.wait()
    if return_code != 0:
        _cleanup_partial_index(filename)
    with jobs_lock:
        if return_code == 0:
            jobs[job_id].update({
                "status": "complete", "message": f"Indexing complete · {processed_pages} pages processed",
                "progress_percent": 100,
            })
        else:
            useful_error = next((line for line in reversed(log_tail) if "error" in line.casefold()), None)
            jobs[job_id].update({
                "status": "failed",
                "message": useful_error or (log_tail[-1] if log_tail else "Indexing failed. Check the server window for details."),
                "progress_percent": None,
            })


@app.post("/api/documents/upload")
async def upload_document(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    if _active_job():
        raise HTTPException(409, "Another indexing or review job is already running")
    filename = Path(file.filename or "").name
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported")
    folder = Path(DOCUMENTS_FOLDER)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / filename
    data = await file.read()
    if len(data) > 200 * 1024 * 1024:
        raise HTTPException(413, "PDF exceeds the 200 MB limit")
    if target.exists():
        connection = connect()
        indexed = connection.execute("SELECT 1 FROM documents WHERE source=?", (filename,)).fetchone()
        connection.close()
        if indexed:
            raise HTTPException(409, "A PDF with this filename is already indexed")
        if target.read_bytes() != data:
            raise HTTPException(409, "A different unindexed PDF has this filename. Rename the new PDF and try again")
    else:
        target.write_bytes(data)
    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {"status": "queued", "kind": "index", "message": "Preparing index…"}
    background_tasks.add_task(_run_ingestion, job_id, filename)
    return {"job_id": job_id, "filename": filename}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    with jobs_lock:
        return jobs.get(job_id, {"status": "unknown", "message": "Job not found"})


app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")


@app.get("/")
def home():
    return FileResponse(WEB_DIR / "index.html")
