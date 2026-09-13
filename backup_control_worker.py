#!/usr/bin/env python3
"""Privileged, allowlisted database backup worker for the PW155 admin panel."""

import argparse
import json
import os
import re
import subprocess
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import grp
except ImportError:  # pragma: no cover - grp is present on the Ubuntu target
    grp = None


CONTROL_DIR = Path(os.environ.get(
    "PW155_BACKUP_CONTROL_DIR", "/var/lib/pw155-backup-control"
))
REQUEST_DIR = CONTROL_DIR / "requests"
FILE_DIR = CONTROL_DIR / "files"
BACKUP_ROOT = Path(os.environ.get(
    "PW155_BACKUP_ROOT", "/srv/pw155/backups/database"
))
BACKUP_SCRIPT = Path(os.environ.get(
    "PW155_BACKUP_SCRIPT", "/srv/pw155/tools/pw155-backup-db.sh"
))
RETENTION_DAYS = int(os.environ.get("PW155_BACKUP_RETENTION_DAYS", "14"))
STAMP_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
ARCHIVE_RE = re.compile(r"^PW155-database-([0-9]{8}T[0-9]{6}Z)\.tar\.gz$")


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return fallback


def set_group(path):
    if grp is None:
        return
    try:
        os.chown(path, 0, grp.getgrnam("pwbackup").gr_gid)
    except KeyError:
        pass


def atomic_json(path, value):
    CONTROL_DIR.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(CONTROL_DIR))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
        os.chmod(temporary, 0o640)
        set_group(temporary)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def backup_files():
    rows = []
    try:
        candidates = list(FILE_DIR.iterdir())
    except OSError:
        candidates = []
    for path in candidates:
        match = ARCHIVE_RE.fullmatch(path.name)
        if not match or path.is_symlink() or not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        rows.append({
            "filename": path.name,
            "created_at": match.group(1),
            "bytes": stat.st_size,
        })
    return sorted(rows, key=lambda item: item["created_at"], reverse=True)


def refresh(extra=None):
    previous = load_json(CONTROL_DIR / "status.json", {})
    state = {
        "updated_at": now(),
        "busy": bool(previous.get("busy", False)),
        "backups": backup_files(),
        "last_action": previous.get("last_action"),
        "error": previous.get("error"),
    }
    if extra:
        state.update(extra)
    atomic_json(CONTROL_DIR / "status.json", state)
    return state


def validate_backup_directory(path):
    root = BACKUP_ROOT.resolve()
    candidate = Path(path).resolve()
    if candidate.parent != root or not STAMP_RE.fullmatch(candidate.name):
        raise RuntimeError("Lokasi hasil backup tidak valid")
    if candidate.is_symlink() or not candidate.is_dir():
        raise RuntimeError("Direktori hasil backup tidak ditemukan")
    for required in ("pw.sql.gz", "pw_portal.sql.gz", "metadata.txt", "SHA256SUMS"):
        source = candidate / required
        if source.is_symlink() or not source.is_file():
            raise RuntimeError(f"Hasil backup tidak lengkap: {required}")
    return candidate


def create_download_archive(source):
    FILE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"PW155-database-{source.name}.tar.gz"
    destination = FILE_DIR / filename
    descriptor, temporary = tempfile.mkstemp(prefix=".backup-", suffix=".tar.gz", dir=str(FILE_DIR))
    os.close(descriptor)
    try:
        with tarfile.open(temporary, "w:gz") as archive:
            archive.add(source, arcname=source.name, recursive=True)
        os.chmod(temporary, 0o640)
        set_group(temporary)
        os.replace(temporary, destination)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return destination


def remove_expired_archives():
    cutoff = time.time() - (max(1, RETENTION_DAYS) * 86400)
    for item in backup_files():
        path = FILE_DIR / item["filename"]
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def process_request(path):
    request = load_json(path, None)
    if not isinstance(request, dict) or request.get("action") != "create":
        raise ValueError("Permintaan backup tidak valid")
    actor = str(request.get("actor", "admin"))[:20]
    action = {
        "id": str(request.get("id", path.stem))[:64],
        "action": "create",
        "actor": actor,
        "requested_at": str(request.get("requested_at", "-"))[:40],
        "started_at": now(),
        "status": "running",
    }
    refresh({"busy": True, "last_action": action, "error": None})
    try:
        result = subprocess.run(
            [str(BACKUP_SCRIPT)], capture_output=True, text=True,
            timeout=300, check=False,
            env={**os.environ, "PW155_BACKUP_ROOT": str(BACKUP_ROOT)},
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode:
            raise RuntimeError(output[-1200:] or "Script backup database gagal")
        match = re.search(r"Backup selesai:\s*(/\S+)", output)
        if not match:
            raise RuntimeError("Script backup tidak mengembalikan lokasi hasil")
        source = validate_backup_directory(match.group(1))
        archive = create_download_archive(source)
        remove_expired_archives()
        action.update({
            "status": "completed", "finished_at": now(),
            "filename": archive.name,
            "message": f"Backup {source.name} siap disimpan di VM dan diunduh",
        })
        refresh({"busy": False, "last_action": action, "error": None})
    except (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError) as error:
        action.update({"status": "failed", "finished_at": now(), "message": str(error)[-1200:]})
        refresh({"busy": False, "last_action": action, "error": str(error)[-1200:]})


def process_queue():
    REQUEST_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(REQUEST_DIR.glob("*.json")):
        try:
            process_request(path)
        except Exception as error:
            refresh({"busy": False, "error": str(error)[-1200:], "last_action": {
                "id": path.stem, "status": "failed", "finished_at": now(),
                "message": str(error)[-1200:],
            }})
        finally:
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("refresh", "process"))
    args = parser.parse_args()
    if args.mode == "refresh":
        refresh()
    else:
        process_queue()


if __name__ == "__main__":
    main()
