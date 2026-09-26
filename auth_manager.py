import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone

from config import SESSION_TIMEOUT_MINUTES
from storage import connect


PASSWORD_ITERATIONS = 390_000
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,49}$")
ROLES = {"admin", "reviewer"}
_sessions = {}
_sessions_lock = threading.Lock()


class AuthError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc)


def _timestamp():
    return _now().isoformat(timespec="seconds")


def _clean_identity(username, display_name=""):
    username = str(username or "").strip()
    if not USERNAME_PATTERN.fullmatch(username):
        raise AuthError("Username must be 3-50 characters and use only letters, numbers, dots, dashes, or underscores")
    display_name = str(display_name or username).strip()
    if not display_name or len(display_name) > 80:
        raise AuthError("Display name must be between 1 and 80 characters")
    return username, display_name


def validate_password(password):
    password = str(password or "")
    if len(password) < 10:
        raise AuthError("Password must contain at least 10 characters")
    if len(password) > 256:
        raise AuthError("Password is too long")
    return password


def hash_password(password):
    password = validate_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password, encoded):
    try:
        algorithm, iterations, salt, wanted = str(encoded).split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            str(password or "").encode("utf-8"),
            base64.urlsafe_b64decode(salt.encode("ascii")),
            int(iterations),
        )
        return hmac.compare_digest(
            base64.urlsafe_b64encode(digest).decode("ascii"), wanted
        )
    except (TypeError, ValueError):
        return False


def _public_user(row):
    return {
        "id": int(row["id"]),
        "username": str(row["username"]),
        "display_name": str(row["display_name"]),
        "role": str(row["role"]),
        "enabled": bool(row["enabled"]),
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"],
    }


def user_count():
    connection = connect()
    try:
        return int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])
    finally:
        connection.close()


def create_first_admin(username, display_name, password):
    username, display_name = _clean_identity(username, display_name)
    password_hash = hash_password(password)
    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        if int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]):
            raise AuthError("Administrator setup has already been completed")
        cursor = connection.execute(
            "INSERT INTO users(username, display_name, password_hash, role, created_by) VALUES (?, ?, ?, 'admin', ?)",
            (username, display_name, password_hash, username),
        )
        connection.execute(
            "INSERT INTO security_audit(user_id, username, action, details) VALUES (?, ?, 'initial_admin_created', ?)",
            (int(cursor.lastrowid), username, json.dumps({"role": "admin"})),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM users WHERE id=?", (int(cursor.lastrowid),)).fetchone()
        return _public_user(row)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def authenticate(username, password):
    connection = connect()
    try:
        row = connection.execute(
            "SELECT * FROM users WHERE username=? COLLATE NOCASE", (str(username or "").strip(),)
        ).fetchone()
        if not row or not bool(row["enabled"]) or not verify_password(password, row["password_hash"]):
            return None
        connection.execute("UPDATE users SET last_login_at=? WHERE id=?", (_timestamp(), int(row["id"])))
        connection.commit()
        return _public_user(row)
    finally:
        connection.close()


def list_users():
    connection = connect()
    try:
        return [_public_user(row) for row in connection.execute("SELECT * FROM users ORDER BY username")]
    finally:
        connection.close()


def create_user(username, display_name, password, role, created_by):
    username, display_name = _clean_identity(username, display_name)
    password_hash = hash_password(password)
    role = str(role or "").strip().lower()
    if role not in ROLES:
        raise AuthError("Role must be admin or reviewer")
    connection = connect()
    try:
        cursor = connection.execute(
            "INSERT INTO users(username, display_name, password_hash, role, created_by) VALUES (?, ?, ?, ?, ?)",
            (username, display_name, password_hash, role, str(created_by or "")),
        )
        connection.commit()
        return _public_user(connection.execute("SELECT * FROM users WHERE id=?", (int(cursor.lastrowid),)).fetchone())
    except Exception as error:
        connection.rollback()
        if "unique" in str(error).casefold():
            raise AuthError("That username already exists") from error
        raise
    finally:
        connection.close()


def set_user_enabled(user_id, enabled, actor_id):
    user_id, actor_id = int(user_id), int(actor_id)
    if user_id == actor_id and not enabled:
        raise AuthError("You cannot disable your own account")
    connection = connect()
    try:
        row = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise AuthError("User not found")
        if str(row["role"]) == "admin" and not enabled:
            other_admins = int(connection.execute(
                "SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1 AND id<>?", (user_id,)
            ).fetchone()[0])
            if not other_admins:
                raise AuthError("The last active administrator cannot be disabled")
        connection.execute("UPDATE users SET enabled=? WHERE id=?", (1 if enabled else 0, user_id))
        connection.commit()
        if not enabled:
            revoke_user_sessions(user_id)
        return _public_user(connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
    finally:
        connection.close()


def reset_user_password(user_id, password):
    password_hash = hash_password(password)
    connection = connect()
    try:
        row = connection.execute("SELECT id FROM users WHERE id=?", (int(user_id),)).fetchone()
        if not row:
            raise AuthError("User not found")
        connection.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, int(user_id)))
        connection.commit()
        revoke_user_sessions(int(user_id))
    finally:
        connection.close()


def change_own_password(user_id, current_password, new_password):
    password_hash = hash_password(new_password)
    connection = connect()
    try:
        row = connection.execute("SELECT password_hash FROM users WHERE id=? AND enabled=1", (int(user_id),)).fetchone()
        if not row or not verify_password(current_password, row["password_hash"]):
            raise AuthError("Current password is incorrect")
        connection.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, int(user_id)))
        connection.commit()
        revoke_user_sessions(int(user_id))
    finally:
        connection.close()


def start_session(user):
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    session = {
        "user_id": int(user["id"]),
        "username": str(user["username"]),
        "display_name": str(user["display_name"]),
        "role": str(user["role"]),
        "csrf": csrf,
        "last_seen": _now(),
    }
    with _sessions_lock:
        _sessions[token] = session
    return token, dict(session)


def read_session(token):
    if not token:
        return None
    with _sessions_lock:
        session = _sessions.get(str(token))
        if not session:
            return None
        if _now() - session["last_seen"] > timedelta(minutes=SESSION_TIMEOUT_MINUTES):
            _sessions.pop(str(token), None)
            return None
        user_id = int(session["user_id"])
    connection = connect()
    try:
        row = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    finally:
        connection.close()
    if not row or not bool(row["enabled"]):
        end_session(token)
        return None
    with _sessions_lock:
        current = _sessions.get(str(token))
        if not current:
            return None
        current.update({
            "username": str(row["username"]), "display_name": str(row["display_name"]),
            "role": str(row["role"]), "last_seen": _now(),
        })
        return dict(current)


def end_session(token):
    with _sessions_lock:
        _sessions.pop(str(token or ""), None)


def revoke_user_sessions(user_id):
    with _sessions_lock:
        for token in [key for key, value in _sessions.items() if int(value["user_id"]) == int(user_id)]:
            _sessions.pop(token, None)


def log_security_event(user, action, success=True, details=None):
    user = user or {}
    connection = connect()
    try:
        connection.execute(
            "INSERT INTO security_audit(user_id, username, action, success, details) VALUES (?, ?, ?, ?, ?)",
            (
                user.get("user_id") or user.get("id"), user.get("username"), str(action),
                1 if success else 0,
                json.dumps(details, ensure_ascii=False) if isinstance(details, (dict, list)) else str(details or ""),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def audit_events(limit=100):
    connection = connect()
    try:
        rows = connection.execute(
            "SELECT id, username, action, success, details, created_at FROM security_audit ORDER BY id DESC LIMIT ?",
            (max(1, min(500, int(limit))),),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()
