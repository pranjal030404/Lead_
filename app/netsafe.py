"""SSRF guard for every fetch whose target is not a fixed, trusted API host.

Lead website URLs come from providers and from users (PATCH /leads), so they are
attacker-influenced. Without a guard, a lead with website `http://169.254.169.254/`
or `http://localhost:3306/` makes the server fetch internal resources.

`safe_get` validates the scheme and port, resolves the hostname and rejects any
non-public address (loopback, private, link-local, cloud metadata, multicast,
reserved, unspecified, IPv4-mapped IPv6), then follows redirects *manually* so
every hop is re-validated.

Known limit: DNS is resolved here and again by the HTTP client, so a hostile DNS
server could rebind between the two (TOCTOU). Closing that needs an IP-pinned
transport; until then, run the app with egress filtering to internal ranges.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

from . import http

ALLOWED_SCHEMES = ("http", "https")
ALLOWED_PORTS = (80, 443, 8080, 8443)
MAX_REDIRECTS = 5
MAX_BODY_BYTES = 2_000_000

BLOCKED_HOSTNAMES = {
    "localhost", "metadata.google.internal", "metadata", "instance-data",
}
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".corp")


class UnsafeURL(ValueError):
    """The URL points somewhere the server must never fetch."""


def _ip_is_public(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10"))
    )


def validate_url(url: str) -> str:
    """Return the URL if it is safe to fetch, else raise UnsafeURL."""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise UnsafeURL(f"scheme not allowed: {parsed.scheme or '(none)'}")
    if parsed.username or parsed.password:
        raise UnsafeURL("credentials in URL are not allowed")
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeURL("missing host")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeURL("invalid port") from exc
    if port not in ALLOWED_PORTS:
        raise UnsafeURL(f"port not allowed: {port}")
    if host in BLOCKED_HOSTNAMES or host.endswith(BLOCKED_SUFFIXES):
        raise UnsafeURL(f"host not allowed: {host}")

    # Literal IP (including decimal/hex forms normalised by getaddrinfo below).
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not _ip_is_public(literal):
            raise UnsafeURL(f"non-public address: {host}")
        return url

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeURL(f"cannot resolve host: {host}") from exc
    if not infos:
        raise UnsafeURL(f"cannot resolve host: {host}")
    for info in infos:
        if not _ip_is_public(ipaddress.ip_address(info[4][0])):
            raise UnsafeURL(f"{host} resolves to a non-public address")
    return url


def is_safe_url(url: str) -> bool:
    try:
        validate_url(url)
        return True
    except UnsafeURL:
        return False


def safe_get_chain(url: str, *, breaker: str, **kwargs) -> tuple[httpx.Response, list[str]]:
    """Like `safe_get` but also returns the URLs visited (first to final)."""
    current = validate_url(url)
    chain = [current]
    for _ in range(MAX_REDIRECTS + 1):
        response = http.get(current, breaker=breaker, follow_redirects=False, **kwargs)
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location")
            if not location:
                return response, chain
            current = validate_url(urljoin(current, location))
            chain.append(current)
            continue
        if len(response.content) > MAX_BODY_BYTES:
            response._content = response.content[:MAX_BODY_BYTES]  # noqa: SLF001
        return response, chain
    raise UnsafeURL("too many redirects")


def safe_get(url: str, *, breaker: str, **kwargs) -> httpx.Response:
    """GET an untrusted URL: validate, then follow redirects with re-validation.

    Raises UnsafeURL for a blocked target (never retried, never counted against
    the circuit breaker). Other errors propagate like `http.get`.
    """
    return safe_get_chain(url, breaker=breaker, **kwargs)[0]
