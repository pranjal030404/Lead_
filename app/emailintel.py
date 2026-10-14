"""Email intelligence: classification, registry, inference, verification.

Vocabulary (the spec's single list, split into two orthogonal axes):
  email_type  PERSONAL | GENERIC | ROLE      - what kind of mailbox it looks like
  status      UNVERIFIED | VERIFIED | INFERRED | INVALID | DISPOSABLE

`VERIFIED` is written only after a real verification provider says the mailbox
is deliverable. Finding an address on a website, or building it from a name
pattern, never makes it verified. INFERRED / INVALID / DISPOSABLE addresses are
refused by outreach (`email_blocked`).
"""

from __future__ import annotations

import re

from . import http
from .config import settings
from .db import current_ws, execute, query, query_one, log_failure, utcnow
from .lineage import SOURCE_CONFIDENCE
from .quota import QuotaExceeded, check_and_reserve
from .validation import DISPOSABLE_DOMAINS, EMAIL_RE, ROLE_PREFIXES

GENERIC_LOCALS = {
    "info", "contact", "hello", "hi", "enquiry", "enquiries", "inquiry", "inquiries",
    "sales", "support", "admin", "office", "mail", "team", "help", "care", "customercare",
    "booking", "bookings", "reservations", "orders", "service", "services", "reception",
    "frontdesk", "general",
}
ROLE_LOCALS = {
    "ceo", "cto", "cfo", "coo", "founder", "cofounder", "owner", "director", "manager",
    "hr", "careers", "jobs", "recruitment", "marketing", "accounts", "accounting",
    "billing", "finance", "press", "media", "legal", "procurement", "purchase", "ops",
}
BLOCKING_STATUSES = ("INVALID", "DISPOSABLE", "INFERRED")
HUNTER_VERIFY_URL = "https://api.hunter.io/v2/email-verifier"


def classify(email: str | None) -> dict:
    """Pure classification of one address -> {email, email_type, status, problem}."""
    cleaned = (email or "").strip().lower().rstrip(".,;")
    if not cleaned or not EMAIL_RE.match(cleaned):
        return {"email": cleaned, "email_type": "GENERIC", "status": "INVALID",
                "problem": "malformed"}
    local, _, domain = cleaned.partition("@")
    base = re.split(r"[+]", local)[0]
    if domain in DISPOSABLE_DOMAINS:
        return {"email": cleaned, "email_type": "GENERIC", "status": "DISPOSABLE",
                "problem": "disposable domain"}
    if base in ROLE_PREFIXES:
        return {"email": cleaned, "email_type": "GENERIC", "status": "INVALID",
                "problem": "no-reply address"}
    if base in GENERIC_LOCALS:
        kind = "GENERIC"
    elif base in ROLE_LOCALS:
        kind = "ROLE"
    else:
        kind = "PERSONAL"
    return {"email": cleaned, "email_type": kind, "status": "UNVERIFIED", "problem": None}


def register(lead_id: int, email: str | None, source: str, source_type: str,
             confidence: float | None = None, contact_id: int | None = None,
             inferred: bool = False) -> dict | None:
    """Record an address against a lead. Never downgrades a VERIFIED row and
    never overwrites provenance of an address already known from a better source
    unless this observation is a verified one."""
    info = classify(email)
    if not info["email"]:
        return None
    status = "INFERRED" if (inferred and info["status"] == "UNVERIFIED") else info["status"]
    conf = SOURCE_CONFIDENCE.get(source, 0.5) if confidence is None else confidence
    if status in ("INVALID", "DISPOSABLE"):
        conf = min(conf, 0.05)
    now = utcnow()
    if inferred:
        # A guess must never touch an address we already hold from any real
        # observation (it would downgrade it to INFERRED): add-if-absent only.
        existing = query_one(
            "SELECT * FROM email_addresses WHERE workspace_id = ? AND lead_id = ? AND email = ?",
            (current_ws(), lead_id, info["email"]))
        if existing:
            return existing
    execute(
        """INSERT INTO email_addresses
           (workspace_id, lead_id, contact_id, email, email_type, status, confidence,
            source, source_type, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)
           ON DUPLICATE KEY UPDATE
             email_type = VALUES(email_type),
             contact_id = COALESCE(VALUES(contact_id), contact_id),
             status = IF(status = 'VERIFIED', status, VALUES(status)),
             confidence = IF(status = 'VERIFIED', confidence, VALUES(confidence)),
             source = IF(status = 'VERIFIED', source, VALUES(source)),
             source_type = IF(status = 'VERIFIED', source_type, VALUES(source_type)),
             updated_at = VALUES(updated_at)""",
        (current_ws(), lead_id, contact_id, info["email"], info["email_type"], status,
         conf, source, source_type, now, now),
    )
    return query_one(
        "SELECT * FROM email_addresses WHERE workspace_id = ? AND lead_id = ? AND email = ?",
        (current_ws(), lead_id, info["email"]))


def email_blocked(lead_id: int | None, address: str | None) -> str | None:
    """Reason outreach must not use this address, or None if it may.

    Looks the address up across the *workspace* (any lead): once an address is
    known to be invalid/disposable/inferred, no lead may send to it."""
    if not address:
        return None
    info = classify(address)
    if info["status"] in ("INVALID", "DISPOSABLE"):
        return f"address is {info['status'].lower()} ({info['problem']})"
    rows = query(
        "SELECT status FROM email_addresses WHERE workspace_id = ? AND email = ?",
        (current_ws(), info["email"]))
    statuses = {r["status"] for r in rows}
    if "VERIFIED" in statuses:
        return None
    for bad in BLOCKING_STATUSES:
        if bad in statuses:
            return f"address is {bad.lower()}"
    return None


# --------------------------------------------------------------- inference --

def _pattern_candidates(first: str, last: str, domain: str) -> list[tuple[str, str]]:
    f, l = first[:1], last[:1]
    pats = [("first.last", f"{first}.{last}"), ("first", first), ("flast", f"{f}{last}"),
            ("firstl", f"{first}{l}"), ("first_last", f"{first}_{last}"),
            ("last", last)]
    return [(name, f"{local}@{domain}") for name, local in pats if local and local != "@"]


def _clean_name(part: str) -> str:
    return re.sub(r"[^a-z]", "", (part or "").lower())


def known_pattern(lead_id: int, domain: str) -> str | None:
    """If a PERSONAL address at this domain already follows a pattern, return its
    name - real evidence for how this business formats addresses."""
    people = query("SELECT name FROM contacts WHERE workspace_id = ? AND lead_id = ?",
                   (current_ws(), lead_id))
    mails = query(
        "SELECT email FROM email_addresses WHERE workspace_id = ? AND lead_id = ? "
        "AND email_type = 'PERSONAL' AND status IN ('UNVERIFIED','VERIFIED') "
        "AND email LIKE ?", (current_ws(), lead_id, f"%@{domain}"))
    for person in people:
        parts = (person["name"] or "").split()
        if len(parts) < 2:
            continue
        first, last = _clean_name(parts[0]), _clean_name(parts[-1])
        for pattern, address in _pattern_candidates(first, last, domain):
            if any(m["email"] == address for m in mails):
                return pattern
    return None


def infer(lead_id: int, contact_name: str, domain: str) -> list[dict]:
    """Guess addresses for a *real, already-recorded* person at the business's
    own domain. Stored as INFERRED (never sendable, never VERIFIED).

    Confidence: 0.55 when this domain's real addresses confirm the pattern, else
    0.25. A guess with no evidence is labelled as exactly that."""
    domain = (domain or "").strip().lower().removeprefix("www.")
    parts = (contact_name or "").split()
    if len(parts) < 2 or not domain or "." not in domain:
        return []
    first, last = _clean_name(parts[0]), _clean_name(parts[-1])
    if not first or not last:
        return []
    pattern = known_pattern(lead_id, domain)
    out = []
    for name, address in _pattern_candidates(first, last, domain):
        if pattern and name != pattern:
            continue
        row = register(lead_id, address, "inferred", "inferred",
                       confidence=0.55 if pattern else 0.25, inferred=True)
        # Only report guesses; an address we already hold keeps its real status.
        if row and row["status"] == "INFERRED":
            row["pattern"] = name
            out.append(row)
    return out


# ------------------------------------------------------------ verification --

class VerifierUnavailable(RuntimeError):
    pass


def verifier_available() -> bool:
    return bool(settings.hunter_api_key)


def verify(email_id: int) -> dict:
    """Verify a stored address with the configured provider (Hunter).

    Quota is reserved *before* the paid call. Raises VerifierUnavailable when no
    provider is configured, QuotaExceeded when the cap is hit. Only Hunter's
    'valid' result produces VERIFIED; 'invalid'/'disposable' are recorded;
    anything ambiguous (accept_all, unknown, webmail) stays UNVERIFIED."""
    row = query_one("SELECT * FROM email_addresses WHERE id = ? AND workspace_id = ?",
                    (email_id, current_ws()))
    if not row:
        raise LookupError("email not found")
    if not verifier_available():
        raise VerifierUnavailable("No email verification provider configured (HUNTER_API_KEY)")
    check_and_reserve("hunter")
    try:
        response = http.get(HUNTER_VERIFY_URL, breaker="hunter", retries=2,
                            params={"email": row["email"], "api_key": settings.hunter_api_key})
        if response.status_code >= 400:
            raise RuntimeError(f"provider returned {response.status_code}")
        verdict = ((response.json().get("data") or {}).get("status") or "unknown").lower()
    except QuotaExceeded:
        raise
    except Exception as exc:  # noqa: BLE001
        log_failure("email.verify", str(exc), row["lead_id"])
        raise
    status = {"valid": "VERIFIED", "invalid": "INVALID", "disposable": "DISPOSABLE"}.get(
        verdict, "UNVERIFIED" if row["status"] != "INFERRED" else "INFERRED")
    now = utcnow()
    execute(
        "UPDATE email_addresses SET status = ?, confidence = ?, last_verified_at = ?, "
        "source = IF(?, 'hunter', source), source_type = IF(?, 'enrichment_api', source_type), "
        "updated_at = ? WHERE id = ? AND workspace_id = ?",
        (status, 0.95 if status == "VERIFIED" else (0.05 if status in ("INVALID", "DISPOSABLE")
                                                    else row["confidence"]),
         now, status in ("VERIFIED", "INVALID", "DISPOSABLE"),
         status in ("VERIFIED", "INVALID", "DISPOSABLE"), now, email_id, current_ws()),
    )
    out = query_one("SELECT * FROM email_addresses WHERE id = ?", (email_id,))
    out["provider_verdict"] = verdict
    return out
