"""Pre-draft eligibility: should we write to this lead at all? Pure.

Checked *before* a message is generated, so effort and sending reputation are never spent on
closed businesses, dead contacts or people who already told us no.
"""

from __future__ import annotations

TERMINAL_STATUSES = {"WON", "LOST"}
CLOSED = {"CLOSED_PERMANENTLY", "PERMANENTLY_CLOSED"}


def outreach_eligibility(lead: dict, channel: str = "email", suppressed: bool = False,
                         address_blocked: bool = False) -> dict:
    """-> {eligible, reasons}. `suppressed` / `address_blocked` come from the DB layer."""
    reasons: list[str] = []
    if (lead.get("business_status") or "").upper() in CLOSED:
        reasons.append("business is permanently closed")
    if lead.get("status") in TERMINAL_STATUSES:
        reasons.append(f"lead is already {lead['status']}")
    if suppressed:
        reasons.append("recipient opted out / is on the suppression list")
    if channel == "email":
        if not lead.get("email"):
            reasons.append("no email address")
        elif lead.get("email_valid") in (0, False):
            reasons.append("email failed validation")
        elif address_blocked:
            reasons.append("email is inferred, invalid or disposable")
    else:
        if not (lead.get("whatsapp") or lead.get("phone")):
            reasons.append("no phone / WhatsApp number")
        elif lead.get("phone_valid") in (0, False) and not lead.get("whatsapp"):
            reasons.append("phone number failed validation")
    return {"eligible": not reasons, "reasons": reasons}
