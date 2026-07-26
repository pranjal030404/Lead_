from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..config import settings
from ..db import execute, query, query_one, scalar, utcnow
from ..fastjson import rows_response
from ..dedup import dedupe_existing_leads
from ..enrich import enrich_lead, parse_issues
from ..outreach import log_interaction, render_template, whatsapp_link

router = APIRouter(prefix="/api", tags=["leads"])

EDITABLE_FIELDS = {
    "business_name", "owner_name", "phone", "email", "whatsapp", "website_url",
    "address", "city", "area", "pincode", "state", "country", "instagram_url",
    "facebook_url", "linkedin_url", "status", "niche", "tags", "notes",
    "estimated_budget", "business_size", "follow_up_date", "lead_priority",
}

# What the leads table actually renders. A row carries 56 columns, and sending
# all of them for a 50-row page is ~86KB of which the UI reads about a tenth -
# the drawer fetches the full record from /api/leads/{id} when you open a lead.
# Pass full=true for the complete row (the CSV export has its own endpoint).
LIST_COLUMNS = [
    "id", "business_name", "area", "city", "phone", "status",
    "lead_score", "lead_priority", "data_quality_score",
    "website_url", "website_score", "created_at",
]

SORTABLE = {
    "created_at": "created_at", "lead_score": "lead_score",
    "business_name": "business_name", "updated_at": "updated_at",
    "data_quality_score": "data_quality_score", "follow_up_date": "follow_up_date",
}


def _decorate(lead: dict) -> dict:
    lead["website_issues"] = parse_issues(lead.get("website_issues"))
    lead["score_reasons"] = parse_issues(lead.get("score_reasons"))
    lead["tag_list"] = [t.strip() for t in (lead.get("tags") or "").split(",") if t.strip()]
    return lead


def _build_filters(
    q, status, priority, city, niche, country, has_website, tag, min_quality,
    enrichment_status, source,
) -> tuple[str, list]:
    clauses, params = [], []
    if q:
        clauses.append(
            "(business_name LIKE ? OR phone LIKE ? OR email LIKE ? OR address LIKE ?)"
        )
        params += [f"%{q}%"] * 4
    for column, value in (
        ("status", status), ("lead_priority", priority), ("city", city),
        ("niche", niche), ("country", country), ("enrichment_status", enrichment_status),
        ("source", source),
    ):
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
    if has_website is not None:
        clauses.append("COALESCE(has_website, CASE WHEN website_url IS NULL THEN 0 ELSE 1 END) = ?")
        params.append(1 if has_website else 0)
    if tag:
        clauses.append("tags LIKE ?")
        params.append(f"%{tag}%")
    if min_quality is not None:
        clauses.append("data_quality_score >= ?")
        params.append(min_quality)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


@router.get("/leads")
def list_leads(
    q: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    city: str | None = None,
    niche: str | None = None,
    country: str | None = None,
    source: str | None = None,
    has_website: bool | None = None,
    tag: str | None = None,
    min_quality: int | None = None,
    enrichment_status: str | None = None,
    sort: str = "created_at",
    direction: str = "desc",
    limit: int = Query(50, ge=1),
    offset: int = 0,
    full: bool = False,
):
    # Ceiling comes from config so it can be raised without a code change; a
    # literal `le=` in the signature is baked in at import and can't be.
    limit = min(limit, settings.max_page_size)
    where, params = _build_filters(q, status, priority, city, niche, country,
                                   has_website, tag, min_quality, enrichment_status, source)
    order_column = SORTABLE.get(sort, "created_at")
    order_dir = "ASC" if direction.lower() == "asc" else "DESC"
    total = scalar(f"SELECT COUNT(*) FROM leads{where}", tuple(params))
    columns = "*" if full else ", ".join(LIST_COLUMNS)
    rows = query(
        f"SELECT {columns} FROM leads{where} "
        f"ORDER BY {order_column} {order_dir} LIMIT ? OFFSET ?",
        (*params, limit, offset),
    )
    return rows_response({
        "total": total, "limit": limit, "offset": offset,
        "leads": [_decorate(r) for r in rows] if full else rows,
    })


@router.get("/leads/export")
def export_leads(
    q: str | None = None, status: str | None = None, priority: str | None = None,
    city: str | None = None, niche: str | None = None, country: str | None = None,
    source: str | None = None, has_website: bool | None = None, tag: str | None = None,
    min_quality: int | None = None, enrichment_status: str | None = None,
):
    """CSV export - doubles as the manual 'get everything out' safety net (Part 7.4)."""
    where, params = _build_filters(q, status, priority, city, niche, country,
                                   has_website, tag, min_quality, enrichment_status, source)
    rows = query(f"SELECT * FROM leads{where} ORDER BY id", tuple(params))

    buffer = io.StringIO()
    if rows:
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()),
                                extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    else:
        buffer.write("no leads matched\n")
    buffer.seek(0)

    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="leads.csv"'},
    )


@router.get("/leads/facets")
def facets():
    def distinct(column: str):
        return [
            r[column] for r in
            query(f"SELECT DISTINCT {column} FROM leads WHERE {column} IS NOT NULL "
                  f"AND {column} != '' ORDER BY {column}")
        ]
    return {
        "cities": distinct("city"), "niches": distinct("niche"),
        "countries": distinct("country"), "statuses": distinct("status"),
        "sources": distinct("source"),
    }


@router.get("/leads/{lead_id}")
def get_lead(lead_id: int):
    lead = query_one("SELECT * FROM leads WHERE id = ?", (lead_id,))
    if not lead:
        raise HTTPException(404, "Lead not found")
    lead = _decorate(lead)
    lead["interactions"] = query(
        "SELECT * FROM interactions WHERE lead_id = ? ORDER BY date DESC", (lead_id,)
    )
    lead["queued_messages"] = query(
        "SELECT * FROM approval_queue WHERE lead_id = ? ORDER BY id DESC", (lead_id,)
    )
    return lead


class LeadPatch(BaseModel):
    model_config = {"extra": "allow"}


@router.patch("/leads/{lead_id}")
def update_lead(lead_id: int, patch: LeadPatch):
    if not query_one("SELECT id FROM leads WHERE id = ?", (lead_id,)):
        raise HTTPException(404, "Lead not found")
    fields = {k: v for k, v in patch.model_dump().items() if k in EDITABLE_FIELDS}
    if not fields:
        raise HTTPException(400, f"No editable fields. Allowed: {sorted(EDITABLE_FIELDS)}")
    fields["updated_at"] = utcnow()
    assignments = ", ".join(f"{k} = ?" for k in fields)
    execute(f"UPDATE leads SET {assignments} WHERE id = ?", (*fields.values(), lead_id))
    return _decorate(query_one("SELECT * FROM leads WHERE id = ?", (lead_id,)))


@router.delete("/leads/{lead_id}")
def delete_lead(lead_id: int):
    """Hard delete, including interaction history - for GDPR / DPDP erasure
    requests (Part 7.1, 7.5). This is not recoverable outside a backup."""
    lead = query_one("SELECT business_name, email FROM leads WHERE id = ?", (lead_id,))
    if not lead:
        raise HTTPException(404, "Lead not found")
    execute("DELETE FROM interactions WHERE lead_id = ?", (lead_id,))
    execute("DELETE FROM approval_queue WHERE lead_id = ?", (lead_id,))
    execute("DELETE FROM leads WHERE id = ?", (lead_id,))
    return {"deleted": lead_id, "business_name": lead["business_name"]}


@router.post("/leads/{lead_id}/enrich")
def enrich(lead_id: int):
    try:
        return _decorate(enrich_lead(lead_id))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


class BulkStatus(BaseModel):
    ids: list[int]
    status: str


@router.post("/leads/bulk-status")
def bulk_status(payload: BulkStatus):
    if not payload.ids:
        raise HTTPException(400, "No lead ids given")
    placeholders = ",".join("?" for _ in payload.ids)
    execute(
        f"UPDATE leads SET status = ?, updated_at = ? WHERE id IN ({placeholders})",
        (payload.status, utcnow(), *payload.ids),
    )
    return {"updated": len(payload.ids), "status": payload.status}


class BulkTag(BaseModel):
    ids: list[int]
    tag: str


@router.post("/leads/bulk-tag")
def bulk_tag(payload: BulkTag):
    updated = 0
    for lead_id in payload.ids:
        lead = query_one("SELECT tags FROM leads WHERE id = ?", (lead_id,))
        if not lead:
            continue
        tags = {t.strip() for t in (lead["tags"] or "").split(",") if t.strip()}
        tags.add(payload.tag.strip())
        execute("UPDATE leads SET tags = ?, updated_at = ? WHERE id = ?",
                (",".join(sorted(tags)), utcnow(), lead_id))
        updated += 1
    return {"updated": updated}


@router.post("/leads/dedupe")
def run_dedupe():
    """Sweep the whole table for duplicates that predate a rule change."""
    return {"merged": dedupe_existing_leads()}


# ------------------------------------------------------------ interactions --

class InteractionPayload(BaseModel):
    type: str
    subject: str | None = None
    content: str | None = None
    outcome: str | None = None
    next_step: str | None = None
    follow_up_date: str | None = None
    new_status: str | None = None


@router.get("/leads/{lead_id}/interactions")
def list_interactions(lead_id: int):
    return query("SELECT * FROM interactions WHERE lead_id = ? ORDER BY date DESC", (lead_id,))


@router.post("/leads/{lead_id}/interactions")
def add_interaction(lead_id: int, payload: InteractionPayload):
    if not query_one("SELECT id FROM leads WHERE id = ?", (lead_id,)):
        raise HTTPException(404, "Lead not found")
    now = utcnow()
    interaction_id = execute(
        """INSERT INTO interactions
           (lead_id, type, direction, date, subject, content, outcome, next_step,
            follow_up_date, automated, created_at)
           VALUES(?,?,'out',?,?,?,?,?,?,0,?)""",
        (lead_id, payload.type, now, payload.subject, payload.content, payload.outcome,
         payload.next_step, payload.follow_up_date, now),
    )
    updates = {
        "first_contacted_date": None, "last_contacted_date": now,
        "contact_method": payload.type, "updated_at": now,
    }
    execute(
        """UPDATE leads SET
             status = CASE WHEN ? IS NOT NULL THEN ?
                           WHEN status = 'NEW' THEN 'CONTACTED' ELSE status END,
             first_contacted_date = COALESCE(first_contacted_date, ?),
             last_contacted_date = ?,
             total_touchpoints = total_touchpoints + 1,
             contact_method = ?,
             follow_up_date = COALESCE(?, follow_up_date),
             updated_at = ?
           WHERE id = ?""",
        (payload.new_status, payload.new_status, now, now, payload.type,
         payload.follow_up_date, now, lead_id),
    )
    return {"id": interaction_id, "lead": _decorate(query_one(
        "SELECT * FROM leads WHERE id = ?", (lead_id,)))}


@router.post("/leads/{lead_id}/message")
def generate_message(lead_id: int, template: str):
    lead = query_one("SELECT * FROM leads WHERE id = ?", (lead_id,))
    if not lead:
        raise HTTPException(404, "Lead not found")
    try:
        rendered = render_template(template, lead)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    rendered["whatsapp_link"] = whatsapp_link(lead, rendered["body"])
    rendered["to"] = lead.get("email")
    rendered["mailto"] = (
        f"mailto:{lead['email']}?subject={rendered['subject']}"
        if lead.get("email") else None
    )
    return rendered
