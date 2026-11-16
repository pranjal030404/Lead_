"""Read-only API tokens - let Claude, a script or a dashboard query *your* leads safely.

A token acts as the user who created it, inside the workspace it was created in, and can only
make GET requests to a short allow-list of data endpoints. It can never create/edit/delete,
manage users, send messages or mint more tokens. Only the SHA-256 of a token is stored, so a
database leak does not leak working tokens; the plain token is shown exactly once.
"""

from __future__ import annotations

import hashlib
import secrets

from .db import execute, query, query_one, utcnow

PREFIX = "lgk_"
READ_PREFIXES = ("/api/leads", "/api/stats", "/api/pipeline/health", "/api/follow-ups",
                 "/api/discovery/sources", "/api/geo/map", "/api/auth/me")
MAX_TOKENS_PER_USER = 10


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def allowed(method: str, path: str) -> bool:
    if path == "/mcp":                    # MCP speaks JSON-RPC over POST; its tools are read-only
        return method in ("POST", "GET")
    return method in ("GET", "HEAD") and any(path == p or path.startswith(p + "/") or path.startswith(p + "?")
                                             for p in READ_PREFIXES) and not path.startswith("/api/tokens")


def create(user_id: int, workspace_id: int, name: str) -> dict:
    name = (name or "").strip()[:80] or "API token"
    n = query_one("SELECT COUNT(*) AS n FROM api_tokens WHERE user_id = ? AND revoked = 0", (user_id,))["n"]
    if n >= MAX_TOKENS_PER_USER:
        raise ValueError(f"You already have {MAX_TOKENS_PER_USER} active tokens - revoke one first")
    token = PREFIX + secrets.token_urlsafe(32)
    token_id = execute(
        "INSERT INTO api_tokens(workspace_id, user_id, name, prefix, token_hash, created_at) VALUES(?,?,?,?,?,?)",
        (workspace_id, user_id, name, token[:10], _hash(token), utcnow()))
    return {"id": token_id, "name": name, "token": token, "prefix": token[:10]}


def list_for(user_id: int) -> list[dict]:
    return query("SELECT id, name, prefix, created_at, last_used_at, revoked FROM api_tokens "
                 "WHERE user_id = ? ORDER BY id DESC", (user_id,))


def revoke(user_id: int, token_id: int) -> bool:
    return bool(execute("UPDATE api_tokens SET revoked = 1 WHERE id = ? AND user_id = ? AND revoked = 0",
                        (token_id, user_id)))


def authenticate(token: str) -> dict | None:
    """-> {user_id, workspace_id, username} for a live token of an active user, else None."""
    if not token.startswith(PREFIX) or len(token) > 100:
        return None
    row = query_one(
        "SELECT t.id, t.user_id, t.workspace_id, u.username FROM api_tokens t JOIN users u ON u.id = t.user_id "
        "WHERE t.token_hash = ? AND t.revoked = 0 AND u.is_active = 1", (_hash(token),))
    if row:
        execute("UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (utcnow(), row["id"]))
    return row
