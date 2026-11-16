"""Campaigns API: build a sequence, enrol leads, activate, watch it run."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import campaigns
from ..db import sees_all, current_ws, execute, query, query_one, utcnow

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


class StepIn(BaseModel):
    template: str = Field(min_length=1, max_length=128)
    template_b: str | None = Field(None, min_length=1, max_length=128)   # optional A/B variant
    delay_days: int = Field(0, ge=0, le=90)


class Criteria(BaseModel):
    niche: str | None = None
    city: str | None = None
    priorities: list[str] | None = None
    min_score: int | None = Field(None, ge=0, le=10)


class CampaignIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    steps: list[StepIn] = Field(min_length=1, max_length=10)
    criteria: Criteria | None = None


class CampaignPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    status: str | None = Field(None, pattern="^(ACTIVE|PAUSED)$")
    criteria: Criteria | None = None


class EnrollIn(BaseModel):
    lead_ids: list[int] | None = Field(None, max_length=500)
    use_criteria: bool = False
    segment_id: int | None = None          # enrol everyone currently in a saved segment


def _own(campaign_id: int, request: Request) -> dict:
    row = query_one("SELECT * FROM campaigns WHERE id = ? AND workspace_id = ?", (campaign_id, current_ws()))
    user = getattr(request.state, "user", None) or {}
    if not row or (not sees_all(user) and row["owner_user_id"] != user.get("id")):
        raise HTTPException(404, "Campaign not found")      # other people's campaigns don't exist to you
    return row


def _steps(campaign_id: int) -> list[dict]:
    return query("SELECT position, template_name AS template, template_b, delay_days FROM campaign_steps "
                 "WHERE campaign_id = ? ORDER BY position", (campaign_id,))


def _check_templates(names: list[str]) -> None:
    for name in names:
        t = query_one("SELECT type FROM templates WHERE workspace_id = ? AND name = ?", (current_ws(), name))
        if not t:
            raise HTTPException(400, f"Template '{name}' does not exist")
        if t["type"] != "email":
            raise HTTPException(400, f"Template '{name}' is not an email template - campaigns send email")


def _out(row: dict) -> dict:
    return {**{k: row[k] for k in ("id", "name", "status", "created_at", "updated_at")},
            "criteria": campaigns.parse_criteria(row["criteria"]),
            "steps": _steps(row["id"]), "stats": campaigns.stats(row["id"])}


@router.get("")
def list_campaigns(request: Request):
    user = getattr(request.state, "user", None) or {}
    sql, args = "SELECT * FROM campaigns WHERE workspace_id = ?", [current_ws()]
    if not sees_all(user):
        sql += " AND owner_user_id = ?"; args.append(user.get("id"))
    return {"campaigns": [_out(r) for r in query(sql + " ORDER BY id DESC", tuple(args))]}


@router.post("")
def create(p: CampaignIn, request: Request):
    _check_templates([s.template for s in p.steps] + [s.template_b for s in p.steps if s.template_b])
    user = getattr(request.state, "user", None) or {}
    now = utcnow()
    cid = execute("INSERT INTO campaigns(workspace_id, owner_user_id, name, status, criteria, created_at, "
                  "updated_at) VALUES(?,?,?,'DRAFT',?,?,?)",
                  (current_ws(), user.get("id"), p.name.strip(),
                   json.dumps(p.criteria.model_dump(exclude_none=True)) if p.criteria else None, now, now))
    for i, s in enumerate(p.steps):
        execute("INSERT INTO campaign_steps(workspace_id, campaign_id, position, template_name, template_b, "
                "delay_days) VALUES(?,?,?,?,?,?)", (current_ws(), cid, i, s.template, s.template_b, s.delay_days))
    return _out(_own(cid, request))


@router.get("/{campaign_id}")
def detail(campaign_id: int, request: Request):
    return _out(_own(campaign_id, request))


@router.patch("/{campaign_id}")
def update(campaign_id: int, p: CampaignPatch, request: Request):
    row = _own(campaign_id, request)
    if p.status == "ACTIVE" and not _steps(campaign_id):
        raise HTTPException(400, "Add steps before activating")
    execute("UPDATE campaigns SET name = ?, status = ?, criteria = ?, updated_at = ? "
            "WHERE id = ? AND workspace_id = ?",
            (p.name.strip() if p.name else row["name"], p.status or row["status"],
             json.dumps(p.criteria.model_dump(exclude_none=True)) if p.criteria else row["criteria"],
             utcnow(), campaign_id, current_ws()))
    return _out(_own(campaign_id, request))


@router.delete("/{campaign_id}")
def delete(campaign_id: int, request: Request):
    _own(campaign_id, request)
    # Nothing from a deleted campaign may still go out.
    execute("UPDATE approval_queue SET status = 'SKIPPED', error_message = 'campaign deleted' "
            "WHERE workspace_id = ? AND step LIKE ? AND status IN ('PENDING','APPROVED')",
            (current_ws(), f"c{campaign_id}s%"))
    execute("DELETE FROM campaigns WHERE id = ? AND workspace_id = ?", (campaign_id, current_ws()))
    return {"ok": True}


@router.post("/{campaign_id}/enroll")
def enroll(campaign_id: int, p: EnrollIn, request: Request):
    camp = _own(campaign_id, request)
    ids = p.lead_ids or []
    if p.segment_id is not None:
        from .segments import _own as _own_segment, lead_ids as segment_ids
        ids = segment_ids(json.loads(_own_segment(p.segment_id, request)["filters"]))
    elif p.use_criteria:
        ids = campaigns.matching_lead_ids(campaigns.parse_criteria(camp["criteria"]))
    if not ids:
        raise HTTPException(400, "No leads to enrol - pass lead_ids or set criteria and use_criteria")
    try:
        return campaigns.enroll(campaign_id, ids)
    except (ValueError, LookupError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{campaign_id}/run")
def run_now(campaign_id: int, request: Request):
    """Process due steps for this campaign now (queues for approval - sends nothing)."""
    camp = _own(campaign_id, request)
    if camp["status"] != "ACTIVE":
        raise HTTPException(400, "Activate the campaign first")
    return campaigns.tick(campaign_id)


@router.get("/{campaign_id}/enrollments")
def enrollments(campaign_id: int, request: Request, status: str | None = Query(None, pattern="^(ACTIVE|COMPLETED|STOPPED)$"),
                limit: int = Query(200, le=500)):
    _own(campaign_id, request)
    sql = ("SELECT e.id, e.lead_id, l.business_name, l.email, e.status, e.next_position, e.next_due_at, "
           "e.stop_reason, e.enrolled_at FROM campaign_enrollments e JOIN leads l ON l.id = e.lead_id "
           "WHERE e.campaign_id = ? AND e.workspace_id = ?")
    args: list = [campaign_id, current_ws()]
    if status:
        sql += " AND e.status = ?"; args.append(status)
    return {"enrollments": query(sql + " ORDER BY e.id DESC LIMIT ?", (*args, limit))}


@router.post("/{campaign_id}/enrollments/{enrollment_id}/stop")
def stop_enrollment(campaign_id: int, enrollment_id: int, request: Request):
    _own(campaign_id, request)
    n = execute("UPDATE campaign_enrollments SET status = 'STOPPED', stop_reason = 'stopped manually', "
                "waiting_queue_id = NULL, updated_at = ? WHERE id = ? AND campaign_id = ? AND workspace_id = ? "
                "AND status = 'ACTIVE'", (utcnow(), enrollment_id, campaign_id, current_ws()))
    if not n:
        raise HTTPException(404, "No active enrolment with that id")
    return {"ok": True}
