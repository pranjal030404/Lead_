"""Durable job queue (MySQL-backed) with retry and backoff.

Used for work that must not be lost when it fails or the process restarts - today, webhook
deliveries. A job is a row: PENDING -> RUNNING -> DONE, or back to PENDING with a later `run_at`
after a failure, and FAILED once `max_attempts` is spent. Claiming is an atomic conditional
UPDATE, so several workers (or a worker and an inline first attempt) never run the same job twice.
Jobs left RUNNING by a crashed worker are released after STALE_SECONDS.

Handlers are registered with @handler("kind") and receive the decoded payload; raising means
"retry later".
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Callable

from .db import current_ws_or_none, execute, log_failure, query, query_one, utcnow, workspace_context

BACKOFF_SECONDS = (60, 300, 1800, 7200)       # after attempt 1,2,3,4 ... then FAILED at max_attempts
STALE_SECONDS = 600
HANDLERS: dict[str, Callable[[dict], None]] = {}


def handler(kind: str):
    def register(fn):
        HANDLERS[kind] = fn
        return fn
    return register


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def enqueue(kind: str, payload: dict, *, delay_seconds: int = 0, max_attempts: int = 5) -> int:
    run_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
    return execute("INSERT INTO job_queue(workspace_id, kind, payload, status, attempts, max_attempts, run_at, "
                   "created_at, updated_at) VALUES(?,?,?, 'PENDING', 0, ?, ?, ?, ?)",
                   (current_ws_or_none(), kind, json.dumps(payload, default=str), max_attempts, _iso(run_at),
                    utcnow(), utcnow()))


def _claim(job_id: int) -> bool:
    return bool(execute("UPDATE job_queue SET status = 'RUNNING', locked_at = ?, updated_at = ? "
                        "WHERE id = ? AND status = 'PENDING'", (utcnow(), utcnow(), job_id)))


def run_one(job_id: int) -> str | None:
    """Run one job now if it can be claimed. -> final status of this attempt, or None if not claimed."""
    if not _claim(job_id):
        return None
    job = query_one("SELECT * FROM job_queue WHERE id = ?", (job_id,))
    fn = HANDLERS.get(job["kind"])
    attempts = job["attempts"] + 1
    try:
        if fn is None:
            raise RuntimeError(f"no handler for job kind '{job['kind']}'")
        ctx = workspace_context(job["workspace_id"]) if job["workspace_id"] is not None else None
        if ctx:
            with ctx:
                fn(json.loads(job["payload"]))
        else:
            fn(json.loads(job["payload"]))
    except Exception as exc:  # noqa: BLE001 - any failure schedules a retry
        err = str(exc)[:500]
        if attempts >= job["max_attempts"]:
            execute("UPDATE job_queue SET status = 'FAILED', attempts = ?, last_error = ?, updated_at = ? WHERE id = ?",
                    (attempts, err, utcnow(), job_id))
            log_failure(f"jobqueue.{job['kind']}", err, job_id)
            return "FAILED"
        wait = BACKOFF_SECONDS[min(attempts - 1, len(BACKOFF_SECONDS) - 1)]
        execute("UPDATE job_queue SET status = 'PENDING', attempts = ?, last_error = ?, run_at = ?, "
                "locked_at = NULL, updated_at = ? WHERE id = ?",
                (attempts, err, _iso(datetime.now(timezone.utc) + timedelta(seconds=wait)), utcnow(), job_id))
        return "RETRY"
    execute("UPDATE job_queue SET status = 'DONE', attempts = ?, last_error = NULL, locked_at = NULL, "
            "updated_at = ? WHERE id = ?", (attempts, utcnow(), job_id))
    return "DONE"


def run_due(limit: int = 50) -> dict:
    """Release stale locks, then run every job whose time has come (the scheduler calls this each minute)."""
    stale = _iso(datetime.now(timezone.utc) - timedelta(seconds=STALE_SECONDS))
    released = execute("UPDATE job_queue SET status = 'PENDING', locked_at = NULL WHERE status = 'RUNNING' "
                       "AND locked_at < ?", (stale,)) or 0
    due = query("SELECT id FROM job_queue WHERE status = 'PENDING' AND run_at <= ? ORDER BY run_at, id LIMIT ?",
                (_iso(datetime.now(timezone.utc)), limit))
    out = {"released": released, "done": 0, "retry": 0, "failed": 0}
    for row in due:
        result = run_one(row["id"])
        if result:
            out[{"DONE": "done", "RETRY": "retry", "FAILED": "failed"}[result]] += 1
    return out


def stats(workspace_id: int | None = None) -> dict:
    sql, args = "SELECT status, COUNT(*) AS n FROM job_queue", []
    if workspace_id is not None:
        sql += " WHERE workspace_id = ?"; args.append(workspace_id)
    return {r["status"]: r["n"] for r in query(sql + " GROUP BY status", tuple(args))}


def retry_failed(job_id: int) -> bool:
    return bool(execute("UPDATE job_queue SET status = 'PENDING', attempts = 0, run_at = ?, last_error = NULL, "
                        "updated_at = ? WHERE id = ? AND status = 'FAILED'", (utcnow(), utcnow(), job_id)))
