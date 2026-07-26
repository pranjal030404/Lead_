"""Shared HTTP helpers: timeouts, exponential backoff, circuit breaker.

Every outbound call in this app goes through here (Part 7.3), so no single slow
or dead dependency can hang a batch or hammer something that's already down.
"""

from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx

from .config import settings

USER_AGENT = "ArthvexLeadGen/1.0 (self-hosted lead research tool)"

MAX_ATTEMPTS = 5
BREAKER_THRESHOLD = 5
BREAKER_COOLDOWN = timedelta(minutes=15)

_lock = threading.Lock()
_failures: dict[str, int] = {}
_open_until: dict[str, datetime] = {}

_client_lock = threading.Lock()
_client: httpx.Client | None = None


def client() -> httpx.Client:
    """One shared, pooled client for the whole process.

    Building an httpx.Client per request meant a fresh TCP connection and a
    fresh TLS handshake every time - roughly 100-300ms of pure setup against a
    remote host, paid on every website check and every API call. httpx.Client is
    thread-safe, so one pooled instance serves the enrichment workers too, and
    keep-alive means repeat calls to the same host skip the handshake entirely.
    """
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = httpx.Client(
                    timeout=settings.http_timeout,
                    follow_redirects=True,
                    limits=httpx.Limits(
                        max_connections=settings.http_pool_size,
                        max_keepalive_connections=settings.http_pool_size // 2,
                    ),
                    headers={"User-Agent": USER_AGENT},
                )
    return _client


def close_client() -> None:
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
            _client = None


class CircuitOpen(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def breaker_check(name: str) -> None:
    with _lock:
        until = _open_until.get(name)
        if until and until > _now():
            raise CircuitOpen(
                f"{name} is failing repeatedly - paused until {until:%H:%M UTC}"
            )
        if until:
            _open_until.pop(name, None)
            _failures[name] = 0


def breaker_success(name: str) -> None:
    with _lock:
        _failures[name] = 0


def breaker_failure(name: str) -> None:
    with _lock:
        _failures[name] = _failures.get(name, 0) + 1
        if _failures[name] >= BREAKER_THRESHOLD:
            _open_until[name] = _now() + BREAKER_COOLDOWN


def breaker_state() -> dict:
    with _lock:
        return {
            name: {
                "failures": _failures.get(name, 0),
                "open_until": until.isoformat() if (until := _open_until.get(name)) else None,
            }
            for name in set(_failures) | set(_open_until)
        }


def request(
    method: str,
    url: str,
    *,
    breaker: str,
    timeout: int | None = None,
    retries: int = MAX_ATTEMPTS,
    **kwargs,
) -> httpx.Response:
    """Request with backoff on 429/5xx, capped attempts, and a circuit breaker."""
    breaker_check(breaker)
    timeout = timeout or settings.http_timeout
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    last_error: Exception | None = None

    for attempt in range(retries):
        try:
            response = client().request(
                method, url, headers=headers, timeout=timeout, **kwargs
            )
            if response.status_code in (429, 500, 502, 503, 504):
                last_error = httpx.HTTPStatusError(
                    f"{response.status_code} from {url}", request=response.request,
                    response=response,
                )
                raise last_error
            breaker_success(breaker)
            return response
        except Exception as exc:  # noqa: BLE001 - deliberately broad, we retry then give up
            last_error = exc
            if attempt == retries - 1:
                break
            # 1s, 2s, 4s, 8s ... with jitter so parallel workers don't sync up
            time.sleep((2 ** attempt) + random.uniform(0, 0.5))

    breaker_failure(breaker)
    raise last_error if last_error else RuntimeError(f"{method} {url} failed")


def get(url: str, *, breaker: str, **kwargs) -> httpx.Response:
    return request("GET", url, breaker=breaker, **kwargs)


def post(url: str, *, breaker: str, **kwargs) -> httpx.Response:
    return request("POST", url, breaker=breaker, **kwargs)
