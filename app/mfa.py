"""TOTP two-factor auth (RFC 6238: HMAC-SHA1, 30s step, 6 digits) - standard authenticator apps.

The secret has to be readable to verify codes, so it is stored as-is in users.mfa_secret;
protect the database accordingly. A code is accepted for +/-1 step to tolerate clock drift.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from .db import execute, query_one

STEP = 30


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def code_at(secret: str, for_time: float | None = None) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    counter = int((for_time if for_time is not None else time.time()) // STEP)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{value:06d}"


def verify_code(secret: str, code: str | None, now: float | None = None) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not (secret and len(code) == 6 and code.isdigit()):
        return False
    now = now if now is not None else time.time()
    return any(hmac.compare_digest(code_at(secret, now + d * STEP), code) for d in (-1, 0, 1))


def provisioning_uri(secret: str, username: str, issuer: str) -> str:
    return (f"otpauth://totp/{quote(issuer)}:{quote(username)}?secret={secret}"
            f"&issuer={quote(issuer)}&digits=6&period={STEP}")


def state(user_id: int) -> dict:
    row = query_one("SELECT mfa_secret, mfa_enabled FROM users WHERE id = ?", (user_id,)) or {}
    return {"secret": row.get("mfa_secret"), "enabled": bool(row.get("mfa_enabled"))}


def begin_setup(user_id: int) -> str:
    secret = new_secret()
    execute("UPDATE users SET mfa_secret = ?, mfa_enabled = 0 WHERE id = ?", (secret, user_id))
    return secret


def enable(user_id: int, code: str) -> bool:
    st = state(user_id)
    if not st["secret"] or not verify_code(st["secret"], code):
        return False
    execute("UPDATE users SET mfa_enabled = 1 WHERE id = ?", (user_id,))
    return True


def disable(user_id: int) -> None:
    execute("UPDATE users SET mfa_enabled = 0, mfa_secret = NULL WHERE id = ?", (user_id,))
