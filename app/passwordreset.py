"""Self-service password reset: single-use, expiring, stored hashed.

The emailed token is random and only its SHA-256 is stored, so a database leak cannot be
turned into account takeovers. `request_reset` never reveals whether an account exists.
Confirming a reset revokes every existing session of that user.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from . import http
from .config import settings
from .db import execute, query_one, utcnow
from .security import hash_password, resolve_login, revoke_sessions

RESET_TTL_MINUTES = 30
MIN_PASSWORD_LEN = 8
RESEND_URL = "https://api.resend.com/emails"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def create_token(user_id: int) -> str:
    """Issue a fresh token, invalidating this user's earlier unused ones."""
    execute("UPDATE password_resets SET used_at = ? WHERE user_id = ? AND used_at IS NULL",
            (utcnow(), user_id))
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(minutes=RESET_TTL_MINUTES)
    execute("INSERT INTO password_resets(user_id, token_hash, expires_at, created_at) VALUES(?,?,?,?)",
            (user_id, _hash(token), _iso(expires), utcnow()))
    return token


def send_reset_email(to_address: str, token: str) -> bool:
    """Plain Resend call; independent of the outreach pipeline (no workspace, no suppression
    list - an account holder asked for this message)."""
    base = (settings.public_url or "").rstrip("/")
    link = f"{base}/login?reset={token}" if base else f"/login?reset={token}"
    if not settings.resend_api_key or not settings.mail_from:
        return False
    body = (f"Someone asked to reset the password for this account.\n\nUse this link within "
            f"{RESET_TTL_MINUTES} minutes:\n{link}\n\nIf it wasn't you, ignore this email.")
    try:
        r = http.post(RESEND_URL, breaker="resend", retries=2,
                      headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                      json={"from": settings.mail_from, "to": [to_address],
                            "subject": "Reset your password", "text": body})
        return r.status_code < 400
    except Exception:  # noqa: BLE001
        return False


def request_reset(identifier: str) -> str | None:
    """Start a reset for a username/email. Returns the token only to the caller in code
    (tests); the HTTP layer discards it. None when there is nothing to reset."""
    username = resolve_login(identifier)
    user = query_one("SELECT id, email, is_active FROM users WHERE username = ?", (username,)) \
        if username else None
    if not user or not user["is_active"] or not user["email"]:
        return None
    token = create_token(user["id"])
    send_reset_email(user["email"], token)
    return token


def confirm_reset(token: str, new_password: str) -> bool:
    if len(new_password or "") < MIN_PASSWORD_LEN:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} characters")
    row = query_one("SELECT id, user_id, expires_at, used_at FROM password_resets WHERE token_hash = ?",
                    (_hash(token or ""),))
    if not row or row["used_at"] or row["expires_at"] < _iso(datetime.now(timezone.utc)):
        return False
    execute("UPDATE password_resets SET used_at = ? WHERE id = ? AND used_at IS NULL",
            (utcnow(), row["id"]))
    execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (hash_password(new_password), utcnow(), row["user_id"]))
    revoke_sessions(row["user_id"])
    return True
