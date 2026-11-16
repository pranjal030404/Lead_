"""Saved segments (named lead filters) and the job-queue admin view."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import jobqueue
from ..db import current_ws, execute, query, query_one, scalar, sees_all, utcnow
from ..security import require_role
from .leads import _build_filters

router = APIRouter(prefix="/api", tags=["segments"])

FILTER_KEYS = ("q", "status", "priority", "city", "niche", "country", "has_website", "tag", "min_quality",
               "enrichment_status", "source", "min_overall", "tier", "min_intent")


class Filters(BaseModel):
    q: str | None = Field(None, max_length=100)
    status: str | None = None
    priority: str | None = Field(None, pattern="^(HOT|WARM|COLD)$")
    city: str | None = None
    niche: str | None = None
    country: str | None = None
    has_website: bool | None = None
    tag: str | None = None
    min_quality: int | None = Field(None, ge=0, le=10)
    enrichment_status: str | None = None
    source: str | None = None
    min_overall: int | None = Field(None, ge=0, le=100)
    tier: str | None = None
    min_intent: int | None = Field(None, ge=0, le=100)


class SegmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    filters: Filters


def lead_where(filters: dict) -> tuple[str, list]:
    """WHERE clause for a stored filter set, always inside the caller's lead scope."""
    f = {k: filters.get(k) for k in FILTER_KEYS}
    return _build_filters(f["q"], f["status"], f["priority"], f["city"], f["niche"], f["country"], f["has_website"],
                          f["tag"], f["min_quality"], f["enrichment_status"], f["source"], f["min_overall"],
                          f["tier"], f["min_intent"])


def lead_ids(filters: dict, limit: int = 500) -> list[int]:
    where, params = lead_where(filters)
    return [r["id"] for r in query(f"SELECT id FROM leads{where} ORDER BY lead_score DESC, id LIMIT ?", (*params, limit))]


def _own(segment_id: int, request: Request) -> dict:
    row = query_one("SELECT * FROM segments WHERE id = ? AND workspace_id = ?", (segment_id, current_ws()))
    user = getattr(request.state, "user", None) or {}
    if not row or (not sees_all(user) and row["owner_user_id"] != user.get("id")):
        raise HTTPException(404, "Segment not found")
    return row


def _out(row: dict) -> dict:
    filters = json.loads(row["filters"])
    where, params = lead_where(filters)
    return {"id": row["id"], "name": row["name"], "filters": filters, "created_at": row["created_at"],
            "count": scalar(f"SELECT COUNT(*) FROM leads{where}", tuple(params), 0)}


@router.get("/segments")
def list_segments(request: Request):
    user = getattr(request.state, "user", None) or {}
    sql, args = "SELECT * FROM segments WHERE workspace_id = ?", [current_ws()]
    if not sees_all(user):
        sql += " AND owner_user_id = ?"; args.append(user.get("id"))
    return {"segments": [_out(r) for r in query(sql + " ORDER BY name", tuple(args))]}


@router.post("/segments")
def create_segment(p: SegmentIn, request: Request):
    filters = p.filters.model_dump(exclude_none=True)
    if not filters:
        raise HTTPException(400, "Pick at least one filter")
    uid = request.state.user["id"]
    if query_one("SELECT 1 AS x FROM segments WHERE workspace_id = ? AND owner_user_id = ? AND name = ?",
                 (current_ws(), uid, p.name.strip())):
        raise HTTPException(409, "You already have a segment with that name")
    sid = execute("INSERT INTO segments(workspace_id, owner_user_id, name, filters, created_at) VALUES(?,?,?,?,?)",
                  (current_ws(), uid, p.name.strip(), json.dumps(filters), utcnow()))
    return _out(_own(sid, request))


@router.delete("/segments/{segment_id}")
def delete_segment(segment_id: int, request: Request):
    _own(segment_id, request)
    execute("DELETE FROM segments WHERE id = ? AND workspace_id = ?", (segment_id, current_ws()))
    return {"ok": True}


@router.get("/segments/{segment_id}/leads")
def segment_leads(segment_id: int, request: Request, limit: int = Query(100, le=500)):
    filters = json.loads(_own(segment_id, request)["filters"])
    where, params = lead_where(filters)
    rows = query("SELECT id, business_name, city, niche, status, lead_priority, lead_score, email, phone "
                 f"FROM leads{where} ORDER BY lead_score DESC, id LIMIT ?", (*params, limit))
    return {"leads": rows, "count": len(rows)}


# ------------------------------------------------------------- job queue ---
@router.get("/jobs")
def jobs(_: dict = Depends(require_role("superadmin", "admin"))):
    rows = query("SELECT id, kind, status, attempts, max_attempts, run_at, last_error, updated_at FROM job_queue "
                 "WHERE workspace_id = ? AND status != 'DONE' ORDER BY id DESC LIMIT 100", (current_ws(),))
    return {"stats": jobqueue.stats(current_ws()), "jobs": rows}


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, _: dict = Depends(require_role("superadmin", "admin"))):
    if not query_one("SELECT 1 AS x FROM job_queue WHERE id = ? AND workspace_id = ?", (job_id, current_ws())):
        raise HTTPException(404, "Job not found")
    if not jobqueue.retry_failed(job_id):
        raise HTTPException(409, "Only failed jobs can be retried")
    return {"ok": True}
