"""Business profile, lineage, contacts, email and social intelligence.

Every route is workspace-scoped; a lead (or contact/email/social row) owned by
another workspace is a 404. Unavailable providers answer 503 with a clear
message - never a pretend success.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .. import emailintel, leadscore, lineage, people, social
from ..db import current_ws, execute, lead_scope, query, query_one, utcnow
from ..quota import QuotaExceeded
from .leads import _decorate, _own_lead

router = APIRouter(prefix="/api", tags=["profile"])


def _own(table: str, row_id: int) -> dict:
    scope, params = lead_scope("l")
    row = query_one(
        f"SELECT t.* FROM {table} t JOIN leads l ON l.id = t.lead_id "
        f"WHERE t.id = ? AND {scope}", (row_id, *params))
    if not row:
        raise HTTPException(404, "Not found")
    return row


# ------------------------------------------------------- profile / lineage --

PROFILE_SECTIONS = {
    "identity": ["business_name", "owner_name", "niche", "business_status",
                 "business_size", "estimated_budget"],
    "contact": ["phone", "whatsapp", "email", "website_url", "address", "area", "city",
                "state", "country", "pincode", "lat", "lng"],
    "digital": ["instagram_url", "facebook_url", "linkedin_url", "google_maps_url",
                "has_website", "website_platform", "website_ssl", "website_mobile_friendly",
                "website_speed_ms", "website_score"],
    "reputation": ["google_rating", "google_reviews"],
}


@router.get("/leads/{lead_id}/profile")
def business_profile(lead_id: int):
    """The lead as a Business Profile: each field with its provenance, plus the
    people, addresses and social profiles attached to it."""
    lead = _decorate(_own_lead(lead_id))
    rows = {r["field"]: r for r in lineage.lineage_for(lead_id)}
    sections = {}
    for name, fields in PROFILE_SECTIONS.items():
        sections[name] = {
            f: {"value": lead.get(f), "source": (rows.get(f) or {}).get("source"),
                "source_type": (rows.get(f) or {}).get("source_type"),
                "confidence": (rows.get(f) or {}).get("effective_confidence"),
                "freshness": (rows.get(f) or {}).get("freshness"),
                "verified": (rows.get(f) or {}).get("verified", False)}
            for f in fields
        }
    return {
        "lead_id": lead_id,
        "sections": sections,
        "data_confidence_score": lead.get("data_confidence_score"),
        "data_quality_score": lead.get("data_quality_score"),
        "freshness_status": lead.get("freshness_status"),
        "last_verified_at": lead.get("last_verified_at"),
        "next_verification_at": lead.get("next_verification_at"),
        "contacts": people.list_for_lead(lead_id),
        "emails": query("SELECT * FROM email_addresses WHERE workspace_id = ? AND lead_id = ? "
                        "ORDER BY status = 'VERIFIED' DESC, confidence DESC",
                        (current_ws(), lead_id)),
        "socials": social.list_for_lead(lead_id),
        "notes": {"inferred_values": "Addresses with status INFERRED are guesses and are "
                                     "never used for outreach.",
                  "confidence": "Effective confidence = source trust x freshness decay."},
    }


@router.get("/leads/{lead_id}/lineage")
def lead_lineage(lead_id: int):
    _own_lead(lead_id, "id")
    return {"fields": lineage.lineage_for(lead_id)}


class VerifyPayload(BaseModel):
    field: str


@router.post("/leads/{lead_id}/verify")
def verify_field(lead_id: int, payload: VerifyPayload):
    """A person attests the field's current value is correct."""
    _own_lead(lead_id, "id")
    if payload.field not in lineage.TRACKED_FIELDS:
        raise HTTPException(400, f"Field must be one of {sorted(lineage.TRACKED_FIELDS)}")
    if not lineage.mark_verified(lead_id, payload.field):
        raise HTTPException(404, "No recorded value for that field")
    return {"field": payload.field, "summary": lineage.refresh_lead(lead_id)}


@router.get("/freshness/summary")
def freshness_summary():
    return lineage.freshness_summary()


# ---------------------------------------------------------------- contacts --

class ContactPayload(BaseModel):
    name: str
    title: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    notes: str | None = None


@router.get("/leads/{lead_id}/contacts")
def lead_contacts(lead_id: int):
    _own_lead(lead_id, "id")
    return people.list_for_lead(lead_id)


@router.post("/leads/{lead_id}/contacts")
def add_contact(lead_id: int, payload: ContactPayload):
    _own_lead(lead_id, "id")
    contact = people.add_contact(lead_id, payload.name, payload.title, payload.email,
                                 payload.phone, payload.linkedin_url, "manual", "user",
                                 notes=payload.notes)
    if not contact:
        raise HTTPException(400, "A name of at least 2 characters is required")
    leadscore.safe_recompute(lead_id)
    return contact


@router.get("/contacts")
def list_contacts(q: str | None = None, min_probability: float = Query(0, ge=0, le=1),
                  role: str | None = None, limit: int = Query(50, ge=1, le=500),
                  offset: int = Query(0, ge=0)):
    scope, scope_params = lead_scope("l")
    clauses, params = [scope, "c.decision_maker_probability >= ?"], \
        [*scope_params, min_probability]
    if q:
        clauses.append("(c.name LIKE ? OR c.title LIKE ? OR l.business_name LIKE ?)")
        params += [f"%{q}%"] * 3
    if role:
        clauses.append("c.role_category = ?")
        params.append(role.upper())
    where = " AND ".join(clauses)
    rows = query(
        f"SELECT c.*, l.business_name FROM contacts c JOIN leads l ON l.id = c.lead_id "
        f"WHERE {where} ORDER BY c.decision_maker_probability DESC, c.id LIMIT ? OFFSET ?",
        (*params, limit, offset))
    total = query_one(f"SELECT COUNT(*) AS n FROM contacts c JOIN leads l ON l.id = c.lead_id "
                      f"WHERE {where}", tuple(params))["n"]
    return {"total": total, "limit": limit, "offset": offset, "contacts": rows}


@router.patch("/contacts/{contact_id}")
def update_contact(contact_id: int, payload: ContactPayload):
    row = _own("contacts", contact_id)
    title = payload.title if payload.title is not None else row["title"]
    category, probability = people.classify_title(title)
    execute(
        "UPDATE contacts SET name = ?, title = ?, role_category = ?, "
        "decision_maker_probability = ?, email = ?, phone = ?, linkedin_url = ?, notes = ?, "
        "updated_at = ? WHERE id = ? AND workspace_id = ?",
        (payload.name.strip() or row["name"], title, category, probability,
         payload.email if payload.email is not None else row["email"],
         payload.phone if payload.phone is not None else row["phone"],
         payload.linkedin_url if payload.linkedin_url is not None else row["linkedin_url"],
         payload.notes if payload.notes is not None else row["notes"],
         utcnow(), contact_id, current_ws()))
    return _own("contacts", contact_id)


@router.post("/contacts/{contact_id}/verify")
def verify_contact(contact_id: int):
    _own("contacts", contact_id)
    execute("UPDATE contacts SET verified_at = ?, updated_at = ? WHERE id = ? "
            "AND workspace_id = ?", (utcnow(), utcnow(), contact_id, current_ws()))
    return _own("contacts", contact_id)


@router.delete("/contacts/{contact_id}")
def delete_contact(contact_id: int):
    _own("contacts", contact_id)
    execute("DELETE FROM contacts WHERE id = ? AND workspace_id = ?",
            (contact_id, current_ws()))
    return {"deleted": contact_id}


# ------------------------------------------------------------------- emails --

class EmailPayload(BaseModel):
    email: str


class InferPayload(BaseModel):
    contact_id: int


@router.get("/leads/{lead_id}/emails")
def lead_emails(lead_id: int):
    _own_lead(lead_id, "id")
    return query("SELECT * FROM email_addresses WHERE workspace_id = ? AND lead_id = ? "
                 "ORDER BY id", (current_ws(), lead_id))


@router.post("/leads/{lead_id}/emails")
def add_email(lead_id: int, payload: EmailPayload):
    _own_lead(lead_id, "id")
    if emailintel.classify(payload.email)["problem"] == "malformed":
        raise HTTPException(400, "Not an email address")
    row = emailintel.register(lead_id, payload.email, "manual", "user")
    leadscore.safe_recompute(lead_id)
    return row


@router.post("/leads/{lead_id}/emails/infer")
def infer_emails(lead_id: int, payload: InferPayload):
    """Build candidate addresses for a recorded contact at the business's domain.
    Results are INFERRED guesses (low confidence, never sendable)."""
    from urllib.parse import urlparse

    lead = _own_lead(lead_id, "id, website_url")
    contact = _own("contacts", payload.contact_id)
    if contact["lead_id"] != lead_id:
        raise HTTPException(404, "Not found")
    site = lead.get("website_url") or ""
    domain = urlparse(site if "//" in site else "//" + site).hostname if site else None
    if not domain:
        raise HTTPException(400, "The lead has no website domain to infer addresses on")
    if len(contact["name"].split()) < 2:
        raise HTTPException(400, "Need a contact with a first and last name")
    out = emailintel.infer(lead_id, contact["name"], domain)
    return {"candidates": out, "warning": "INFERRED addresses are unverified guesses.",
            "note": None if out else "No new candidates: the address(es) this pattern "
                                     "produces are already on file."}


@router.post("/emails/{email_id}/verify")
def verify_email(email_id: int):
    _own("email_addresses", email_id)
    try:
        return emailintel.verify(email_id)
    except emailintel.VerifierUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except QuotaExceeded as exc:
        raise HTTPException(429, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - provider failure
        raise HTTPException(502, f"Verification provider failed: {exc}") from exc


@router.get("/email-verification/status")
def verification_status():
    return {"available": emailintel.verifier_available(), "provider": "hunter"}


# ------------------------------------------------------------------ socials --

class SocialPayload(BaseModel):
    url: str


@router.get("/leads/{lead_id}/socials")
def lead_socials(lead_id: int):
    _own_lead(lead_id, "id")
    return social.list_for_lead(lead_id)


@router.post("/leads/{lead_id}/socials")
def add_social(lead_id: int, payload: SocialPayload):
    _own_lead(lead_id, "id")
    row = social.register(lead_id, payload.url, "manual", "user")
    if not row:
        raise HTTPException(400, "Not a recognised social profile URL")
    column = social.LEAD_URL_COLUMNS.get(row["platform"])
    if column:
        execute(f"UPDATE leads SET {column} = ?, updated_at = ? WHERE id = ? "
                "AND workspace_id = ?", (row["url"], utcnow(), lead_id, current_ws()))
        lineage.record(lead_id, column, "manual", "user")
        lineage.refresh_lead(lead_id)
    return row


@router.delete("/socials/{social_id}")
def delete_social(social_id: int):
    _own("social_profiles", social_id)
    execute("DELETE FROM social_profiles WHERE id = ? AND workspace_id = ?",
            (social_id, current_ws()))
    return {"deleted": social_id}
