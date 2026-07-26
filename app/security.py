"""Single-operator auth and endpoint rate limiting (Part 7.5).

This tool holds phone numbers, emails and your whole pipeline - it must not sit
on a public URL unauthenticated. Sessions are signed cookies (HMAC), so there's
no session store to keep in sync.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode

from .config import settings

SESSION_COOKIE = "leadgen_session"

_rate_lock = threading.Lock()
_hits: dict[str, list[float]] = {}


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, _ = stored.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt), stored)


def check_credentials(username: str, password: str) -> bool:
    user_ok = hmac.compare_digest(username or "", settings.admin_user)
    pass_ok = hmac.compare_digest(password or "", settings.admin_password)
    return user_ok and pass_ok


def _sign(payload: str) -> str:
    return hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue_token(username: str) -> str:
    expires = int(time.time()) + settings.session_hours * 3600
    payload = f"{username}|{expires}"
    encoded = urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"{encoded}.{_sign(payload)}"


def verify_token(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    encoded, signature = token.rsplit(".", 1)
    try:
        padding = "=" * (-len(encoded) % 4)
        payload = urlsafe_b64decode(encoded + padding).decode()
    except Exception:  # noqa: BLE001
        return None
    if not hmac.compare_digest(_sign(payload), signature):
        return None
    username, _, expires = payload.partition("|")
    try:
        if int(expires) < time.time():
            return None
    except ValueError:
        return None
    return username


_last_sweep = 0.0
SWEEP_EVERY_SECONDS = 60


def _sweep(now: float, window_seconds: int) -> None:
    """Drop keys with no recent hits. Called under _rate_lock.

    Without this, `_hits` keeps one entry per client IP that has ever called the
    API, forever - fine for one operator on a laptop, a slow memory leak for an
    instance exposed to the internet or behind a proxy that varies the address.
    """
    global _last_sweep
    if now - _last_sweep < SWEEP_EVERY_SECONDS:
        return
    _last_sweep = now
    cutoff = now - window_seconds
    for key in [k for k, times in _hits.items() if not times or times[-1] < cutoff]:
        del _hits[key]


def client_ip(request) -> str:
    """The address to rate-limit against.

    Only reads X-Forwarded-For when TRUST_PROXY is set, and takes the first
    entry - the original client, everything after it being the proxy chain.
    Untrusted, the header is attacker-controlled and would make the limiter
    trivially bypassable by sending a different value each request.
    """
    if settings.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real = request.headers.get("x-real-ip", "").strip()
        if real:
            return real
    return request.client.host if request.client else "unknown"


def rate_limit(key: str, limit: int, window_seconds: int) -> bool:
    """Return True if the call is allowed. In-process sliding window.

    In-process means each worker keeps its own counter, so running N workers
    effectively multiplies the limit by N. That's deliberate - a shared counter
    would need Redis, and the point of this limiter is to stop runaway loops and
    casual scraping, not to be an exact quota.
    """
    now = time.time()
    with _rate_lock:
        _sweep(now, window_seconds)
        hits = [t for t in _hits.get(key, []) if now - t < window_seconds]
        if len(hits) >= limit:
            _hits[key] = hits
            return False
        hits.append(now)
        _hits[key] = hits
        return True


def rate_limit_state() -> dict:
    with _rate_lock:
        return {"tracked_keys": len(_hits)}
