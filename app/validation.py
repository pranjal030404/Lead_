"""Data validation and the per-lead data quality score (Part 1.4)."""

from __future__ import annotations

import re

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]{2,}$")

DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
    "yopmail.com", "trashmail.com", "sharklasers.com", "getnada.com",
    "throwawaymail.com", "maildrop.cc", "temp-mail.org", "fakeinbox.com",
}

# Addresses that are technically valid but almost never a person worth pitching.
ROLE_PREFIXES = {"noreply", "no-reply", "donotreply", "postmaster", "abuse", "webmaster"}


def normalise_phone(raw: str | None, country: str | None = None) -> tuple[str | None, bool]:
    """Return (cleaned_phone, is_valid).

    Indian numbers are normalised to a bare 10-digit form; everything else is
    kept in E.164-ish form. Anything under 7 digits is treated as invalid.
    """
    if not raw:
        return None, False
    digits = re.sub(r"[^\d+]", "", raw)
    if digits.startswith("+"):
        plus, digits = "+", digits[1:]
    else:
        plus = ""
    if not digits:
        return None, False

    is_india = (country or "").strip().lower() in ("in", "india", "")
    if is_india:
        if digits.startswith("91") and len(digits) == 12:
            digits = digits[2:]
        elif digits.startswith("0") and len(digits) == 11:
            digits = digits[1:]
        # 10 digits is the valid national form for both mobiles (start 6-9) and
        # landlines (STD code + subscriber, e.g. 129-2974246 for Faridabad).
        # Plenty of established businesses still list only a landline, so
        # rejecting those would throw away good leads.
        if len(digits) == 10:
            return digits, True
        return (plus + digits) or None, False

    cleaned = plus + digits
    return cleaned, 8 <= len(digits) <= 15


def whatsapp_number(phone: str | None, country: str | None) -> str | None:
    """Digits-only international form used by wa.me links.

    Returns None for Indian landlines - a wa.me link to one just opens a dead
    chat, which is worse than showing no WhatsApp button at all.
    """
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return None
    if len(digits) == 10 and (country or "").lower() in ("in", "india", ""):
        if digits[0] not in "6789":
            return None
        digits = "91" + digits
    return digits


def validate_email(raw: str | None) -> tuple[str | None, bool, str | None]:
    """Return (cleaned_email, is_valid, problem)."""
    if not raw:
        return None, False, None
    email = raw.strip().lower().rstrip(".,;")
    if not EMAIL_RE.match(email):
        return email, False, "malformed"
    local, _, domain = email.partition("@")
    if domain in DISPOSABLE_DOMAINS:
        return email, False, "disposable domain"
    if local in ROLE_PREFIXES:
        return email, False, "no-reply address"
    return email, True, None


def data_quality_score(lead: dict) -> int:
    """0-10 completeness score. Straight from Part 1.4."""
    score = 0
    if lead.get("phone"):
        score += 2
    if lead.get("email"):
        score += 2
    if lead.get("address"):
        score += 1
    if lead.get("website_checked_at") or lead.get("has_website") is not None:
        score += 1
    if lead.get("owner_name"):
        score += 2
    if lead.get("instagram_url") or lead.get("facebook_url") or lead.get("linkedin_url"):
        score += 1
    if lead.get("phone_valid"):
        score += 1
    return min(score, 10)
