import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from config import APP_VERSION, BACKUPS_FOLDER, DATABASE_FOLDER


class BackupError(RuntimeError):
    pass


def _database_path(value=None):
    return Path(value or DATABASE_FOLDER).resolve()


def _backups_path(value=None):
    path = Path(value or BACKUPS_FOLDER).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_label(value):
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "manual")).strip("-")
    return value[:40] or "manual"


def _sqlite_snapshot(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(str(source), timeout=60)
    destination_connection = sqlite3.connect(str(destination))
    try:
        source_connection.backup(destination_connection)
        result = destination_connection.execute("PRAGMA quick_check").fetchone()
        if not result or str(result[0]).casefold() != "ok":
            raise BackupError(f"SQLite verification failed for {source.name}")
    finally:
        destination_connection.close()
        source_connection.close()


def _database_counts(sqlite_path):
    counts = {
        "documents": 0, "records": 0, "manually_verified": 0,
        "corrections": 0, "users": 0, "security_events": 0,
    }
    connection = sqlite3.connect(str(sqlite_path))
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "documents" in tables:
            counts["documents"] = int(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
        if "records" in tables:
            counts["records"] = int(connection.execute("SELECT COUNT(*) FROM records").fetchone()[0])
            columns = {row[1] for row in connection.execute("PRAGMA table_info(records)")}
            if "verification_status" in columns:
                counts["manually_verified"] = int(connection.execute(
                    "SELECT COUNT(*) FROM records WHERE verification_status='verified'"
                ).fetchone()[0])
        if "record_corrections" in tables:
            counts["corrections"] = int(connection.execute("SELECT COUNT(*) FROM record_corrections").fetchone()[0])
        if "users" in tables:
            counts["users"] = int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        if "security_audit" in tables:
            counts["security_events"] = int(connection.execute("SELECT COUNT(*) FROM security_audit").fetchone()[0])
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
        if not quick_check or str(quick_check[0]).casefold() != "ok":
            raise BackupError("The structured database did not pass its integrity check")
    finally:
        connection.close()
    return counts


def _copy_database_snapshot(source_root, destination_root):
    if not source_root.is_dir():
        raise BackupError(f"Database folder not found: {source_root}")
    destination_root.mkdir(parents=True, exist_ok=True)
    skipped_suffixes = ("-wal", "-shm", "-journal")
    for source in source_root.rglob("*"):
        relative = source.relative_to(source_root)
        destination = destination_root / relative
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        if source.name.endswith(skipped_suffixes):
            continue
        if source.suffix.casefold() in {".db", ".sqlite", ".sqlite3"}:
            try:
                _sqlite_snapshot(source, destination)
                continue
            except sqlite3.DatabaseError:
                pass
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _manifest_for(staged_database, label):
    structured = staged_database / "structured.db"
    if not structured.is_file():
        raise BackupError("structured.db was not found; the backup was not created")
    files = []
    for path in sorted(staged_database.rglob("*")):
        if not path.is_file():
            continue
        files.append({
            "path": (Path("database") / path.relative_to(staged_database)).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        })
    return {
        "format_version": 1,
        "application_version": APP_VERSION,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "label": label,
        "database_counts": _database_counts(structured),
        "files": files,
    }


def create_backup(label="manual", database_folder=None, backups_folder=None):
    database_root = _database_path(database_folder)
    backups_root = _backups_path(backups_folder)
    safe_label = _safe_label(label)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename = f"rag_backup_{stamp}_{safe_label}.zip"
    final_path = backups_root / filename
    temporary_archive = backups_root / f".{filename}.part"
    stage = Path(tempfile.mkdtemp(prefix=".backup-stage-", dir=str(backups_root)))
    try:
        staged_database = stage / "database"
        _copy_database_snapshot(database_root, staged_database)
        manifest = _manifest_for(staged_database, safe_label)
        (stage / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with zipfile.ZipFile(temporary_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(stage).as_posix())
        os.replace(temporary_archive, final_path)
        try:
            verified = verify_backup_path(final_path)
        except Exception:
            final_path.unlink(missing_ok=True)
            raise
        return {**verified, "filename": filename, "download_url": f"/api/backups/{filename}/download"}
    finally:
        if temporary_archive.exists():
            temporary_archive.unlink()
        shutil.rmtree(stage, ignore_errors=True)


def _safe_members(archive):
    members = archive.infolist()
    for member in members:
        path = Path(member.filename)
        if path.is_absolute() or ".." in path.parts:
            raise BackupError("The backup contains an unsafe file path")
    return members


def verify_backup_path(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise BackupError("Backup file not found")
    with zipfile.ZipFile(path, "r") as archive:
        members = _safe_members(archive)
        names = {item.filename for item in members}
        if "manifest.json" not in names:
            raise BackupError("Backup manifest is missing")
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        for item in manifest.get("files") or []:
            name = str(item.get("path") or "")
            if name not in names:
                raise BackupError(f"Backup file is missing: {name}")
            payload = archive.read(name)
            expected_size = item.get("size")
            if expected_size is None or len(payload) != int(expected_size):
                raise BackupError(f"Backup file size mismatch: {name}")
            if hashlib.sha256(payload).hexdigest() != item.get("sha256"):
                raise BackupError(f"Backup checksum mismatch: {name}")
        structured_payload = archive.read("database/structured.db")
    with tempfile.TemporaryDirectory(prefix="rag-backup-verify-") as temporary:
        sqlite_path = Path(temporary) / "structured.db"
        sqlite_path.write_bytes(structured_payload)
        counts = _database_counts(sqlite_path)
    expected_counts = manifest.get("database_counts") or {}
    if any(counts.get(key) != value for key, value in expected_counts.items()):
        raise BackupError("Backup database totals do not match the manifest")
    return {
        "valid": True,
        "filename": path.name,
        "size": path.stat().st_size,
        "created_at": manifest.get("created_at"),
        "application_version": manifest.get("application_version"),
        "label": manifest.get("label"),
        "database_counts": counts,
        "file_count": len(manifest.get("files") or []),
        "sha256": _sha256(path),
    }


def _backup_file(filename, backups_folder=None):
    safe = Path(str(filename or "")).name
    if safe != filename or not safe.startswith("rag_backup_") or not safe.endswith(".zip"):
        raise BackupError("Invalid backup filename")
    path = _backups_path(backups_folder) / safe
    if not path.is_file():
        raise BackupError("Backup file not found")
    return path


def verify_backup(filename, backups_folder=None):
    return verify_backup_path(_backup_file(filename, backups_folder))


def backup_file_path(filename, backups_folder=None):
    return _backup_file(filename, backups_folder)


def list_backups(backups_folder=None):
    root = _backups_path(backups_folder)
    items = []
    for path in sorted(root.glob("rag_backup_*.zip"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            info = verify_backup_path(path)
            info["download_url"] = f"/api/backups/{path.name}/download"
        except Exception as error:
            info = {
                "valid": False,
                "filename": path.name,
                "size": path.stat().st_size,
                "error": str(error),
                "download_url": f"/api/backups/{path.name}/download",
            }
        items.append(info)
    return items


def stage_restore(filename, confirmation, database_folder=None, backups_folder=None):
    if confirmation != "RESTORE":
        raise BackupError("Type RESTORE exactly to confirm")
    database_root = _database_path(database_folder)
    backups_root = _backups_path(backups_folder)
    selected = _backup_file(filename, backups_root)
    selected_info = verify_backup_path(selected)
    safety = create_backup("before-restore", database_root, backups_root)
    pending = backups_root / "pending_restore.zip"
    temporary = backups_root / ".pending_restore.zip.part"
    shutil.copy2(selected, temporary)
    os.replace(temporary, pending)
    marker = {
        "selected_backup": selected.name,
        "selected_sha256": selected_info["sha256"],
        "safety_backup": safety["filename"],
        "staged_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    (backups_root / "pending_restore.json").write_text(
        json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "staged": True,
        **marker,
        "message": "Restore verified and staged. Restart the server to apply it.",
    }


def apply_pending_restore(database_folder=None, backups_folder=None):
    database_root = _database_path(database_folder)
    backups_root = _backups_path(backups_folder)
    pending = backups_root / "pending_restore.zip"
    marker_path = backups_root / "pending_restore.json"
    if not pending.is_file():
        return None
    verified = verify_backup_path(pending)
    parent = database_root.parent
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".restore-stage-", dir=str(parent)))
    old_database = parent / f".{database_root.name}.before-restore-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    try:
        with zipfile.ZipFile(pending, "r") as archive:
            _safe_members(archive)
            archive.extractall(stage)
        incoming = stage / "database"
        if not incoming.is_dir():
            raise BackupError("The staged backup does not contain a database folder")
        _database_counts(incoming / "structured.db")
        if database_root.exists():
            database_root.rename(old_database)
        try:
            incoming.rename(database_root)
            restored_counts = _database_counts(database_root / "structured.db")
            if restored_counts != verified["database_counts"]:
                raise BackupError("Restored database totals do not match the verified backup")
        except Exception:
            if database_root.exists():
                shutil.rmtree(database_root, ignore_errors=True)
            if old_database.exists():
                old_database.rename(database_root)
            raise
        if old_database.exists():
            shutil.rmtree(old_database)
        marker = json.loads(marker_path.read_text(encoding="utf-8")) if marker_path.is_file() else {}
        result = {
            **marker,
            "applied_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "database_counts": restored_counts,
        }
        (backups_root / "last_restore.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        pending.unlink()
        if marker_path.exists():
            marker_path.unlink()
        return result
    finally:
        shutil.rmtree(stage, ignore_errors=True)
