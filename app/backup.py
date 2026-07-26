"""Nightly database backup with retention (Part 7.4).

Uses SQLite's online backup API rather than copying the file, so a backup taken
while a search is mid-write is still consistent.

A local backup only survives losing the *file*. It does not survive losing the
box, which is the failure that costs you every lead and every relationship
history at once - so `BACKUP_REMOTE` pushes each night's file off-server via
rclone. It is off by default because it needs an rclone remote configured first.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import settings
from .db import log_automation, log_failure


def run_backup() -> dict:
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    destination = settings.backup_dir / f"leadgen-{stamp}.db"

    source = sqlite3.connect(settings.db_path)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    removed = prune_backups()
    size_kb = destination.stat().st_size // 1024
    log_automation("backup", f"Backup written: {destination.name} ({size_kb} KB), "
                             f"{removed} old backup(s) pruned")

    result = {"file": str(destination), "size_kb": size_kb, "pruned": removed}
    result["sync"] = sync_offsite(destination)
    return result


def sync_offsite(path: Path) -> dict:
    """Copy one backup to `BACKUP_REMOTE` with rclone (Part 7.4).

    Failures are logged and returned, never raised - a remote that is down must
    not cost you the local backup that already succeeded. The digest surfaces
    the failure the next morning so it can't stay broken quietly.
    """
    if not settings.backup_remote:
        return {"skipped": "BACKUP_REMOTE not set"}

    binary = shutil.which(settings.rclone_path)
    if not binary:
        message = f"rclone not found on PATH as '{settings.rclone_path}'"
        log_automation("backup", f"Off-site sync skipped: {message}", "warn")
        log_failure("backup.sync", message)
        return {"ok": False, "error": message}

    try:
        completed = subprocess.run(
            [binary, "copy", str(path), settings.backup_remote],
            capture_output=True, text=True, timeout=settings.backup_sync_timeout,
        )
    except subprocess.TimeoutExpired:
        message = f"rclone timed out after {settings.backup_sync_timeout}s"
        log_automation("backup", f"Off-site sync failed: {message}", "error")
        log_failure("backup.sync", message)
        return {"ok": False, "error": message}
    except Exception as exc:  # noqa: BLE001
        log_automation("backup", f"Off-site sync failed: {exc}", "error")
        log_failure("backup.sync", str(exc))
        return {"ok": False, "error": str(exc)}

    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "unknown error").strip()[:300]
        log_automation("backup", f"Off-site sync failed: {message}", "error")
        log_failure("backup.sync", message)
        return {"ok": False, "error": message}

    log_automation("backup", f"Off-site sync OK: {path.name} -> {settings.backup_remote}")
    return {"ok": True, "remote": settings.backup_remote}


def prune_backups() -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.backup_keep_days)
    removed = 0
    for path in settings.backup_dir.glob("leadgen-*.db"):
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if modified < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def list_backups() -> list[dict]:
    items = []
    for path in sorted(settings.backup_dir.glob("leadgen-*.db"), reverse=True):
        stat = path.stat()
        items.append({
            "name": path.name,
            "size_kb": stat.st_size // 1024,
            "created": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                              .isoformat(timespec="seconds"),
        })
    return items
