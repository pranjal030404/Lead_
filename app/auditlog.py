"""Append-only record of who changed what: every authenticated state-changing API call.

Stores method, path, status, user and IP - never request bodies, so passwords and message
text can't leak into it. Failures here must never break the request.
"""

from __future__ import annotations

from .db import current_ws_or_none, execute, query, utcnow

SKIP_PREFIXES = ("/api/auth/login", "/api/auth/admin/login")


def record(user: dict | None, method: str, path: str, status: int, ip: str) -> None:
    if method in ("GET", "HEAD", "OPTIONS") or path.startswith(SKIP_PREFIXES):
        return
    try:
        execute("INSERT INTO audit_log(workspace_id, user_id, username, method, path, status, ip, at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (current_ws_or_none(), (user or {}).get("id"), (user or {}).get("username"),
                 method, path[:255], status, ip[:64], utcnow()))
    except Exception:  # noqa: BLE001 - auditing never breaks a request
        pass


def recent(limit: int = 100, workspace_id: int | None = None) -> list[dict]:
    if workspace_id is None:
        return query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
    return query("SELECT * FROM audit_log WHERE workspace_id = ? ORDER BY id DESC LIMIT ?",
                 (workspace_id, limit))
