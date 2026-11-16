"""Meetings API."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import meetings as svc
from ..db import sees_all, current_ws, execute, query, query_one, utcnow
from .leads import _own_lead

router = APIRouter(prefix="/api/meetings", tags=["meetings"])


class MeetingIn(BaseModel):
    lead_id: int
    title: str = Field(min_length=1, max_length=200)
    starts_at: str = Field(max_length=40)
    duration_min: int = Field(30, ge=5, le=480)
    location: str = Field("", max_length=300)
    notes: str = Field("", max_length=4000)


class MeetingPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    starts_at: str | None = Field(None, max_length=40)
    duration_min: int | None = Field(None, ge=5, le=480)
    location: str | None = Field(None, max_length=300)
    notes: str | None = Field(None, max_length=4000)
    status: str | None = Field(None, pattern="^(SCHEDULED|COMPLETED|CANCELLED|NO_SHOW)$")


def _when(value: str):
    try:
        return svc.parse_when(value)
    except ValueError as exc:
        raise HTTPException(400, "starts_at must be an ISO 8601 date-time, e.g. 2026-12-01T15:30:00+05:30") from exc


def _is_admin(request: Request) -> bool:
    return sees_all(getattr(request.state, "user", None))


def _own(meeting_id: int, request: Request) -> dict:
    row = query_one("SELECT m.*, l.business_name FROM meetings m JOIN leads l ON l.id = m.lead_id "
                    "WHERE m.id = ? AND m.workspace_id = ?", (meeting_id, current_ws()))
    uid = (getattr(request.state, "user", None) or {}).get("id")
    if not row or (not _is_admin(request) and row["owner_user_id"] != uid):
        raise HTTPException(404, "Meeting not found")
    return row


@router.get("")
def list_meetings(request: Request, when: str = Query("upcoming", pattern="^(upcoming|past|all)$"),
                  lead_id: int | None = None, limit: int = Query(200, le=500)):
    clauses, args = ["m.workspace_id = ?"], [current_ws()]
    if not _is_admin(request):
        clauses.append("m.owner_user_id = ?"); args.append(request.state.user["id"])
    if lead_id is not None:
        clauses.append("m.lead_id = ?"); args.append(lead_id)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if when == "upcoming":
        clauses.append("m.status = 'SCHEDULED' AND m.starts_at >= ?"); args.append(now)
    elif when == "past":
        clauses.append("(m.status != 'SCHEDULED' OR m.starts_at < ?)"); args.append(now)
    order = "ASC" if when == "upcoming" else "DESC"
    rows = query("SELECT m.*, l.business_name FROM meetings m JOIN leads l ON l.id = m.lead_id "
                 f"WHERE {' AND '.join(clauses)} ORDER BY m.starts_at {order} LIMIT ?", (*args, limit))
    return {"meetings": rows, "count": len(rows)}


@router.post("")
def create(p: MeetingIn, request: Request):
    lead = _own_lead(p.lead_id, "id, business_name")
    start = _when(p.starts_at)
    uid = request.state.user["id"]
    clash = svc.conflicts(uid, start, p.duration_min)
    mid = execute("INSERT INTO meetings(workspace_id, lead_id, owner_user_id, title, starts_at, duration_min, "
                  "location, notes, status, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?, 'SCHEDULED', ?, ?)",
                  (current_ws(), p.lead_id, uid, p.title.strip(), start.isoformat(timespec="seconds"),
                   p.duration_min, p.location, p.notes, utcnow(), utcnow()))
    row = _own(mid, request)
    svc.on_scheduled(row, lead)
    return {**row, "conflicts": clash}


@router.get("/export")
def export_csv(request: Request):
    from .. import exportutil
    rows = [{"when": m["starts_at"], "title": m["title"], "lead": m["business_name"], "minutes": m["duration_min"],
             "location": m["location"], "status": m["status"], "notes": m["notes"]}
            for m in list_meetings(request, "all", None, 500)["meetings"]]
    return Response(exportutil.to_csv(rows) if rows else "no meetings\n", media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="meetings.csv"'})


@router.get("/calendar.ics")
def calendar_ics(request: Request):
    """Every upcoming scheduled meeting as one calendar file - import it into Google/Apple/Outlook."""
    rows = list_meetings(request, "upcoming", None, 500)["meetings"]
    events = []
    for m in rows:
        one = svc.to_ics(m, m["business_name"]).split("BEGIN:VEVENT", 1)[1].rsplit("END:VEVENT", 1)[0]
        events.append("BEGIN:VEVENT" + one + "END:VEVENT\r\n")
    body = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//LeadGen//Meetings//EN\r\nCALSCALE:GREGORIAN\r\n" \
           + "".join(events) + "END:VCALENDAR\r\n"
    return Response(body, media_type="text/calendar", headers={"Content-Disposition": 'attachment; filename="meetings.ics"'})


@router.get("/{meeting_id}")
def get(meeting_id: int, request: Request):
    return _own(meeting_id, request)


@router.patch("/{meeting_id}")
def update(meeting_id: int, p: MeetingPatch, request: Request):
    row = _own(meeting_id, request)
    new = {**row, **{k: v for k, v in p.model_dump().items() if v is not None}}
    start = _when(new["starts_at"])
    moved = p.starts_at is not None and start.isoformat(timespec="seconds") != row["starts_at"]
    clash = svc.conflicts(row["owner_user_id"], start, new["duration_min"], exclude_id=meeting_id) \
        if new["status"] == "SCHEDULED" else []
    execute("UPDATE meetings SET title = ?, starts_at = ?, duration_min = ?, location = ?, notes = ?, "
            "status = ?, updated_at = ? WHERE id = ? AND workspace_id = ?",
            (new["title"], start.isoformat(timespec="seconds"), new["duration_min"], new["location"],
             new["notes"], new["status"], utcnow(), meeting_id, current_ws()))
    saved = _own(meeting_id, request)
    if p.status == "COMPLETED" and row["status"] != "COMPLETED":
        svc.on_completed(saved, {"id": row["lead_id"], "business_name": row["business_name"]})
    if moved and saved["status"] == "SCHEDULED":
        from .. import webhooks
        webhooks.emit("meeting.scheduled", {"meeting_id": meeting_id, "lead_id": row["lead_id"],
                                            "starts_at": saved["starts_at"], "title": saved["title"], "rescheduled": True})
    return {**saved, "conflicts": clash}


@router.delete("/{meeting_id}")
def delete(meeting_id: int, request: Request):
    _own(meeting_id, request)
    execute("DELETE FROM meetings WHERE id = ? AND workspace_id = ?", (meeting_id, current_ws()))
    return {"ok": True}


@router.get("/{meeting_id}/ics")
def ics(meeting_id: int, request: Request):
    row = _own(meeting_id, request)
    return Response(svc.to_ics(row, row["business_name"]), media_type="text/calendar",
                    headers={"Content-Disposition": f'attachment; filename="meeting-{meeting_id}.ics"'})
