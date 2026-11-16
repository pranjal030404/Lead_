"""Free, built-in email finder - no Hunter/UpLead account, no API key.

Runs automatically inside the enrichment pipeline (enrich.stage_email), after
the basic contact-page scrape comes up empty. Three free steps, cheapest first:

  1. Deep crawl  - team / staff / leadership / impressum / privacy pages and the
                   sitemap, which often hold addresses the contact page does not.
  2. Mail check  - DNS MX lookup. A domain with no mail server can never receive
                   mail, so nothing is guessed for it and a found address is flagged.
  3. Guessing    - name patterns for a *known* person, plus a couple of generic
                   mailboxes. Stored INFERRED, which outreach refuses to send to:
                   a guess is a lead for a human, never an automatic send.

Only step 1 can return a real `email` for the lead. Steps 2-3 never write
VERIFIED, matching the policy in emailintel.
"""

from __future__ import annotations

import re
import socket
from functools import lru_cache
from urllib.parse import urlparse

from . import contactparse, emailintel, netsafe, website
from .db import log_failure

try:  # dnspython is optional; without it we fall back to "does the domain resolve".
    import dns.exception
    import dns.resolver
except Exception:  # noqa: BLE001
    dns = None  # type: ignore[assignment]

EXTRA_PATHS = ("/team", "/our-team", "/people", "/staff", "/leadership", "/management",
               "/about-us/team", "/impressum", "/privacy", "/privacy-policy", "/legal",
               "/support", "/help")
GENERIC_GUESSES = ("info", "contact")
MAX_PAGES = 8


def domain_of(lead: dict) -> str:
    url = website._normalise_url(lead.get("website_url") or "") if lead.get("website_url") else ""
    host = urlparse(url).netloc.lower().split(":")[0]
    return contactparse.canonical_domain(host) if host else ""


@lru_cache(maxsize=2048)
def has_mail_server(domain: str) -> bool:
    """True if the domain can plausibly receive mail (MX, else an A record per RFC 5321)."""
    if not domain or "." not in domain:
        return False
    if dns is not None:
        try:
            if len(dns.resolver.resolve(domain, "MX", lifetime=4)) > 0:
                return True
        except dns.resolver.NXDOMAIN:
            return False
        except (dns.exception.DNSException, OSError):
            pass  # no MX / timeout: fall through to the A-record check
    try:
        socket.getaddrinfo(domain, None)
        return True
    except OSError:
        return False


def _sitemap_pages(base: str) -> list[str]:
    try:
        resp = netsafe.safe_get(base + "/sitemap.xml", breaker="website_check", retries=0, timeout=5)
    except Exception:  # noqa: BLE001
        return []
    if resp.status_code >= 400:
        return []
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", resp.text or "", re.I)
    keep = re.compile(r"/(team|about|people|staff|contact|leadership|impressum)", re.I)
    host = urlparse(base).netloc
    return [u for u in locs if urlparse(u).netloc == host and keep.search(u)][:4]


def deep_crawl(website_url: str | None) -> list[str]:
    """Emails from pages the basic contact scrape does not visit. Own-domain first."""
    if not website_url:
        return []
    base = website._normalise_url(website_url).rstrip("/")
    urls = [base + p for p in EXTRA_PATHS] + _sitemap_pages(base)
    found: list[str] = []
    for url in list(dict.fromkeys(urls))[:MAX_PAGES]:
        try:
            resp = netsafe.safe_get(url, breaker="website_check", retries=0, timeout=5)
        except Exception:  # noqa: BLE001
            continue
        if resp.status_code < 400:
            found += contactparse.extract_emails(resp.text or "")
        if len(found) >= 3:
            break
    return contactparse.rank_emails(list(dict.fromkeys(found)), base)


def guess(lead: dict, domain: str) -> int:
    """Record INFERRED candidates for the lead; returns how many were stored."""
    stored = 0
    owner = (lead.get("owner_name") or "").strip()
    if owner:
        stored += len(emailintel.infer(lead["id"], owner, domain))
    if not stored:
        for local in GENERIC_GUESSES:
            row = emailintel.register(lead["id"], f"{local}@{domain}", "inferred", "inferred",
                                      confidence=0.15, inferred=True)
            stored += 1 if row and row["status"] == "INFERRED" else 0
    return stored


def find(lead: dict) -> dict:
    """Entry point for the enrichment stage. Returns {"email", "source", "guessed"}
    - `email` only when a real address was found on the site."""
    result = {"email": None, "guessed": 0, "mail_server": None}
    from .routers.growth import enabled
    if not enabled("growth_finder_enabled"):
        return result
    domain = domain_of(lead)
    if not domain:
        return result
    try:
        result["mail_server"] = has_mail_server(domain)
        if not result["mail_server"]:
            return result                      # nothing can be delivered here; don't guess
        for candidate in deep_crawl(lead.get("website_url")):
            if candidate.rpartition("@")[2].lower().removeprefix("www.").endswith(domain):
                result["email"] = candidate
                return result
        result["guessed"] = guess(lead, domain)
    except Exception as exc:  # noqa: BLE001 - the finder must never sink enrichment
        log_failure("finder", str(exc), lead.get("id"))
    return result
