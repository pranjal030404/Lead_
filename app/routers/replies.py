"""Inbound replies, loss reasons and pipeline health."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import eligibility, pacing, replies
from ..config import settings
from ..db import current_ws, execute, lead_scope, query, query_one, scalar, utcnow
from ..outreach import is_suppressed, sent_last_hour, sent_today
from .leads import _own_lead

router = APIRouter(prefix="/api", tags=["replies"])

LOSS_REASONS = ("NOT_INTERESTED", "NO_BUDGET", "HAS_VENDOR", "BAD_TIMING", "NO_RESPONSE",
                "WRONG_FIT", "CLOSED_BUSINESS", "OTHER")


class ReplyPayload(BaseModel):
    text: str = Field(min_length=1, max_length=10000)
    channel: str = Field("email", pattern="^(email|whatsapp|phone|sms|dm)$")


@router.post("/replies/classify")
def classify_only(p: ReplyPayload):
    """Preview what a reply would be treated as, without touching any lead."""
    return replies.classify(p.text)


@router.post("/leads/{lead_id}/reply")
def log_reply(lead_id: int, p: ReplyPayload):
    lead = _own_lead(lead_id, "id, business_name")
    result = replies.apply_reply(lead_id, p.text, p.channel)
    # Turn the classifier's suggested next step into a task, and tell any webhooks.
    step = (result.get("action") or {}).get("next_step")
    if step and result.get("intent") not in ("STOP", "AUTO_REPLY"):
        title = f"{step} - {lead.get('business_name') or 'lead'}"
        # A second reply must not pile up an identical to-do while the first is still open.
        if not query_one("SELECT 1 AS x FROM tasks WHERE workspace_id = ? AND lead_id = ? AND title = ? "
                         "AND status = 'OPEN'", (current_ws(), lead_id, title)):
            due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
            execute("INSERT INTO tasks(workspace_id, lead_id, title, due_at, status, created_at) "
                    "VALUES(?,?,?,?, 'OPEN', ?)", (current_ws(), lead_id, title, due, utcnow()))
    from .. import webhooks
    webhooks.emit("reply.received", {"lead_id": lead_id, "intent": result.get("intent")})
    return result


class LossPayload(BaseModel):
    reason: str
    note: str = Field("", max_length=1000)


@router.post("/leads/{lead_id}/loss")
def mark_lost(lead_id: int, p: LossPayload):
    _own_lead(lead_id, "id")
    reason = p.reason.upper()
    if reason not in LOSS_REASONS:
        raise HTTPException(400, f"reason must be one of {', '.join(LOSS_REASONS)}")
    now = utcnow()
    execute("""INSERT INTO interactions (workspace_id, lead_id, type, direction, date, subject,
               content, outcome, automated, created_at) VALUES(?,?,'note','out',?,?,?,?,0,?)""",
            (current_ws(), lead_id, now, "Marked lost", p.note, f"LOST:{reason}", now))
    execute("UPDATE leads SET status = 'LOST', updated_at = ? WHERE id = ? AND workspace_id = ? "
            "AND status != 'WON'", (now, lead_id, current_ws()))
    return {"lead_id": lead_id, "status": "LOST", "reason": reason}


@router.get("/lead-eligibility/{lead_id}")
def lead_eligibility(lead_id: int, channel: str = Query("email", pattern="^(email|whatsapp)$")):
    lead = _own_lead(lead_id)
    addr = lead.get("email") if channel == "email" else (lead.get("whatsapp") or lead.get("phone"))
    return eligibility.outreach_eligibility(
        lead, channel, suppressed=is_suppressed(addr, "email" if channel == "email" else "phone"))


@router.get("/pipeline/health")
def pipeline_health(stale_days: int = Query(7, ge=1, le=90)):
    """What needs attention: replies awaiting an answer, stale contacted leads, loss reasons,
    and whether sending is currently allowed."""
    scope, params = lead_scope("l")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_days)).isoformat(timespec="seconds")
    awaiting = query(
        f"SELECT l.id, l.business_name, l.status, MAX(i.date) AS replied_at FROM leads l "
        f"JOIN interactions i ON i.lead_id = l.id AND i.direction = 'in' "
        f"WHERE {scope} AND l.status IN ('REPLIED','MEETING_SET') "
        f"AND NOT EXISTS (SELECT 1 FROM interactions o WHERE o.lead_id = l.id AND o.direction = 'out' "
        f"AND o.date > i.date) GROUP BY l.id, l.business_name, l.status "
        f"ORDER BY replied_at ASC LIMIT 50", tuple(params))
    stale = query(
        f"SELECT l.id, l.business_name, l.last_contacted_date FROM leads l WHERE {scope} "
        f"AND l.status = 'CONTACTED' AND l.last_contacted_date < ? "
        f"ORDER BY l.last_contacted_date ASC LIMIT 50", (*params, cutoff))
    losses = query(
        f"SELECT i.outcome, COUNT(*) AS n FROM interactions i JOIN leads l ON l.id = i.lead_id "
        f"WHERE {scope} AND i.outcome LIKE ? GROUP BY i.outcome ORDER BY n DESC", (*params, "LOST:%"))
    intents = query(
        f"SELECT i.outcome, COUNT(*) AS n FROM interactions i JOIN leads l ON l.id = i.lead_id "
        f"WHERE {scope} AND i.direction = 'in' GROUP BY i.outcome ORDER BY n DESC", tuple(params))
    decision = pacing.send_decision(
        datetime.now(timezone.utc), sent_last_hour(), sent_today(),
        hourly_cap=settings.max_emails_per_hour, daily_cap=settings.max_emails_per_day,
        quiet_start=settings.send_quiet_start, quiet_end=settings.send_quiet_end,
        tz_name=settings.send_timezone, weekdays_only=settings.send_weekdays_only)
    return {"awaiting_reply_from_us": awaiting, "stale_contacted": stale,
            "stale_days": stale_days,
            "loss_reasons": [{"reason": r["outcome"].split(":", 1)[1], "count": r["n"]} for r in losses],
            "reply_intents": [{"intent": r["outcome"], "count": r["n"]} for r in intents],
            "sending": {**decision, "sent_last_hour": sent_last_hour(), "sent_today": sent_today(),
                        "hourly_cap": settings.max_emails_per_hour, "daily_cap": settings.max_emails_per_day,
                        "dry_run": settings.dry_run}}
