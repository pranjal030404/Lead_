"""Nightly database backup with retention (Part 7.4).

Uses `mysqldump --single-transaction`, so the dump is consistent even if a
search is mid-write while it runs - on InnoDB that takes a read snapshot
instead of locking tables.

A local backup only survives losing the *file*. It does not survive losing the
box, which is the failure that costs you every lead and every relationship
history at once - so `BACKUP_REMOTE` pushes each night's file off-server via
rclone. It is off by default because it needs an rclone remote configured first.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import settings
from .db import log_automation, log_failure

BACKUP_GLOB = "leadgen-*.sql"


def run_backup() -> dict:
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    destination = settings.backup_dir / f"leadgen-{stamp}.sql"

    dump_env = dict(os.environ)
    dump_env["MYSQL_PWD"] = settings.mysql_password
    command = [
        settings.mysqldump_path,
        f"-h{settings.mysql_host}",
        f"-P{settings.mysql_port}",
        f"-u{settings.mysql_user}",
        "--single-transaction",
        "--routines",
        "--triggers",
        "--default-character-set=utf8mb4",
        settings.mysql_database,
    ]
    with destination.open("wb") as output:
        completed = subprocess.run(
            command, stdout=output, stderr=subprocess.PIPE,
            env=dump_env, text=False, timeout=600,
        )
    if completed.returncode != 0:
        message = (completed.stderr.decode("utf-8", "replace") or "unknown error").strip()[:500]
        destination.unlink(missing_ok=True)
        log_automation("backup", f"Backup FAILED: {message}", "error")
        log_failure("backup.run", message)
        return {"error": message}

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
    for path in settings.backup_dir.glob(BACKUP_GLOB):
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if modified < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def list_backups() -> list[dict]:
    items = []
    for path in sorted(settings.backup_dir.glob(BACKUP_GLOB), reverse=True):
        stat = path.stat()
        items.append({
            "name": path.name,
            "size_kb": stat.st_size // 1024,
            "created": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                              .isoformat(timespec="seconds"),
        })
    return items