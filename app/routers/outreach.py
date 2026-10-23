from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..config import settings
from ..db import (current_user, current_ws, execute, get_ws_setting, lead_scope, query,
                  query_one, set_ws_setting, utcnow)
from ..outreach import (generate_followups, queue_message, send_approved,
                        set_approval_status, suppress, unsuppress)

router = APIRouter(prefix="/api", tags=["outreach"])


# --------------------------------------------------------------- templates --

class TemplatePayload(BaseModel):
    name: str
    type: str = "email"
    stage: str | None = None
    subject: str | None = None
    body: str
    target_market: str = "india"
    language: str = "en"


@router.get("/templates")
def list_templates(type: str | None = None):
    if type:
        return query("SELECT * FROM templates WHERE workspace_id = ? AND type = ? ORDER BY name",
                     (current_ws(), type))
    return query("SELECT * FROM templates WHERE workspace_id = ? ORDER BY type, name",
                 (current_ws(),))


@router.post("/templates")
def upsert_template(payload: TemplatePayload):
    existing = query_one("SELECT id FROM templates WHERE workspace_id = ? AND name = ?",
                         (current_ws(), payload.name))
    if existing:
        execute(
            "UPDATE templates SET type = ?, stage = ?, subject = ?, body = ?, "
            "target_market = ?, language = ? WHERE id = ? AND workspace_id = ?",
            (payload.type, payload.stage, payload.subject, payload.body,
             payload.target_market, payload.language, existing["id"], current_ws()),
        )
    else:
        execute(
            """INSERT INTO templates(workspace_id, name, type, stage, subject, body, language,
               target_market, created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
            (current_ws(), payload.name, payload.type, payload.stage, payload.subject, payload.body,
             payload.language, payload.target_market, utcnow()),
        )
    return query_one("SELECT * FROM templates WHERE workspace_id = ? AND name = ?",
                     (current_ws(), payload.name))


@router.delete("/templates/{template_id}")
def delete_template(template_id: int):
    execute("DELETE FROM templates WHERE id = ? AND workspace_id = ?",
            (template_id, current_ws()))
    return {"deleted": template_id}


# ---------------------------------------------------------- approval queue --

def _own_queue(queue_id: int) -> dict:
    scope, params = lead_scope("l")
    row = query_one(
        "SELECT q.* FROM approval_queue q JOIN leads l ON l.id = q.lead_id "
        f"WHERE q.id = ? AND {scope}", (queue_id, *params))
    if not row:
        raise HTTPException(404, "Queued message not found")
    return row


@router.get("/approvals")
def list_approvals(status: str = "PENDING", limit: int = Query(100, le=500)):
    scope, params = lead_scope("l")
    return query(
        f"""SELECT q.*, l.business_name, l.city, l.lead_priority, l.lead_score, l.phone
           FROM approval_queue q JOIN leads l ON l.id = q.lead_id
           WHERE {scope} AND q.status = ?
           ORDER BY l.lead_score DESC, q.id ASC LIMIT ?""",
        (*params, status, limit),
    )


class ApprovalEdit(BaseModel):
    subject: str | None = None
    body: str | None = None


@router.patch("/approvals/{queue_id}")
def edit_approval(queue_id: int, payload: ApprovalEdit):
    row = _own_queue(queue_id)
    if row["status"] not in ("PENDING", "APPROVED"):
        raise HTTPException(400, f"Cannot edit a message that is already {row['status']}")
    execute(
        "UPDATE approval_queue SET subject = COALESCE(?, subject), body = COALESCE(?, body) "
        "WHERE id = ? AND workspace_id = ?",
        (payload.subject, payload.body, queue_id, current_ws()),
    )
    return _own_queue(queue_id)


@router.post("/approvals/{queue_id}/approve")
def approve(queue_id: int):
    _own_queue(queue_id)
    set_approval_status(queue_id, "APPROVED")
    return {"id": queue_id, "status": "APPROVED"}


@router.post("/approvals/{queue_id}/skip")
def skip(queue_id: int):
    _own_queue(queue_id)
    set_approval_status(queue_id, "SKIPPED")
    return {"id": queue_id, "status": "SKIPPED"}


class BulkApprove(BaseModel):
    ids: list[int]


@router.post("/approvals/approve-all")
def approve_all(payload: BulkApprove):
    scope, params = lead_scope("l")
    approved = 0
    for queue_id in payload.ids:
        owned = query_one(
            "SELECT q.id FROM approval_queue q JOIN leads l ON l.id = q.lead_id "
            f"WHERE q.id = ? AND {scope}", (queue_id, *params))
        if not owned:
            continue
        set_approval_status(queue_id, "APPROVED")
        approved += 1
    return {"approved": approved}


class QueuePayload(BaseModel):
    lead_id: int
    template: str
    step: str = "manual"
    channel: str = "email"


@router.post("/approvals/queue")
def queue_one(payload: QueuePayload):
    scope, params = lead_scope()
    lead = query_one(f"SELECT * FROM leads WHERE id = ? AND {scope}",
                     (payload.lead_id, *params))
    if not lead:
        raise HTTPException(404, "Lead not found")
    queue_id = queue_message(lead, payload.template, payload.step, payload.channel)
    if not queue_id:
        raise HTTPException(
            400,
            "Nothing queued - the lead has no email, the address is suppressed, "
            "or this step is already queued.",
        )
    return {"id": queue_id}


@router.post("/approvals/generate")
def generate_now():
    return generate_followups()


@router.post("/approvals/send")
def send_now(limit: int | None = None):
    user = current_user()
    owner = user["id"] if user and user.get("role") not in ("admin", "superadmin") else None
    return send_approved(limit, workspace_id=current_ws(), owner_user_id=owner)


# ------------------------------------------------------------- suppression --

class SuppressPayload(BaseModel):
    value: str
    kind: str = "email"
    reason: str = "manual"


@router.get("/suppression")
def list_suppression():
    return query("SELECT * FROM suppression_list WHERE workspace_id = ? ORDER BY id DESC",
                 (current_ws(),))


@router.post("/suppression")
def add_suppression(payload: SuppressPayload):
    suppress(payload.value, payload.kind, payload.reason)
    return {"ok": True}


@router.delete("/suppression")
def remove_suppression(value: str, kind: str = "email"):
    unsuppress(value, kind)
    return {"ok": True}


# ---------------------------------------------------------------- identity --

class IdentityPayload(BaseModel):
    company_name: str | None = None
    portfolio_url: str | None = None


@router.get("/identity")
def get_identity():
    from ..branding import get_branding
    from ..outreach import brand_company_name

    brand = get_branding()
    return {
        "company_name": get_ws_setting("company_name", "") or brand_company_name(),
        "portfolio_url": get_ws_setting("portfolio_url", "http://arthvex.co.in/"),
        "sender_name": settings.mail_from_name,
        "sender_email": settings.mail_from,
        "dry_run": settings.dry_run,
        "brand_name": brand["brand_name"],
    }


@router.post("/identity")
def save_identity(payload: IdentityPayload):
    if payload.company_name:
        set_ws_setting("company_name", payload.company_name)
    if payload.portfolio_url:
        set_ws_setting("portfolio_url", payload.portfolio_url)
    return get_identity()
