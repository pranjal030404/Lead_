"""Meetings: schedule calls with leads, keep the pipeline and tasks in step, export to calendars."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import pipeline, webhooks
from .db import current_ws, execute, query, query_one, utcnow

STATUSES = ("SCHEDULED", "COMPLETED", "CANCELLED", "NO_SHOW")


def parse_when(value: str) -> datetime:
    """ISO 8601 -> aware UTC datetime. A value with no timezone is taken as UTC."""
    when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when).astimezone(timezone.utc)


def conflicts(owner_user_id: int | None, start: datetime, minutes: int, exclude_id: int | None = None) -> list[dict]:
    """Other SCHEDULED meetings of the same owner overlapping [start, start+minutes)."""
    end = start + timedelta(minutes=minutes)
    rows = query("SELECT id, title, starts_at, duration_min FROM meetings WHERE workspace_id = ? "
                 "AND status = 'SCHEDULED' AND owner_user_id <=> ? AND id <> ?",
                 (current_ws(), owner_user_id, exclude_id or 0))
    out = []
    for r in rows:
        s = parse_when(r["starts_at"])
        if s < end and s + timedelta(minutes=r["duration_min"]) > start:
            out.append(r)
    return out


def on_scheduled(meeting: dict, lead: dict) -> None:
    """Side effects of a meeting being booked: pipeline, sequences, prep task, webhook."""
    from . import campaigns
    campaigns.stop_for_lead(lead["id"], "meeting scheduled")        # first, so the reason names the meeting
    pipeline.set_lead_status(lead["id"], "MEETING_SET", f"Meeting booked: {meeting['title']}", forward_only=True)
    start = parse_when(meeting["starts_at"])
    prep_due = start - timedelta(hours=2)
    if prep_due < datetime.now(timezone.utc):
        prep_due = start
    execute("INSERT INTO tasks(workspace_id, lead_id, owner_user_id, title, due_at, status, created_at) "
            "VALUES(?,?,?,?,?, 'OPEN', ?)",
            (current_ws(), lead["id"], meeting["owner_user_id"],
             f"Prepare for meeting with {lead['business_name']}", prep_due.isoformat(timespec="seconds"), utcnow()))
    webhooks.emit("meeting.scheduled", {"meeting_id": meeting["id"], "lead_id": lead["id"],
                                        "starts_at": meeting["starts_at"], "title": meeting["title"]})


def on_completed(meeting: dict, lead: dict) -> None:
    """After a meeting happens, the next move is a proposal."""
    execute("INSERT INTO tasks(workspace_id, lead_id, owner_user_id, title, due_at, status, created_at) "
            "VALUES(?,?,?,?,?, 'OPEN', ?)",
            (current_ws(), lead["id"], meeting["owner_user_id"],
             f"Send proposal to {lead['business_name']}",
             (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="seconds"), utcnow()))


def _ics_escape(text: str) -> str:
    out = (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return out.replace("\r", "").replace("\n", "\\n")


def to_ics(meeting: dict, lead_name: str) -> str:
    """RFC 5545 single-event calendar file (works with Google/Apple/Outlook)."""
    start = parse_when(meeting["starts_at"])
    end = start + timedelta(minutes=meeting["duration_min"])
    fmt = "%Y%m%dT%H%M%SZ"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//LeadGen//Meetings//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "BEGIN:VEVENT",
             f"UID:meeting-{meeting['id']}-{current_ws()}@leadgen",
             f"DTSTAMP:{datetime.now(timezone.utc).strftime(fmt)}",
             f"DTSTART:{start.strftime(fmt)}", f"DTEND:{end.strftime(fmt)}",
             f"SUMMARY:{_ics_escape(meeting['title'])}",
             f"DESCRIPTION:{_ics_escape('With ' + lead_name + (chr(10) + meeting['notes'] if meeting.get('notes') else ''))}"]
    if meeting.get("location"):
        lines.append(f"LOCATION:{_ics_escape(meeting['location'])}")
    lines += ["STATUS:CANCELLED" if meeting["status"] == "CANCELLED" else "STATUS:CONFIRMED",
              "BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Meeting soon", "TRIGGER:-PT15M", "END:VALARM",
              "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"
