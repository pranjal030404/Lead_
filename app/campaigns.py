"""Campaigns: multi-step email sequences over a set of leads.

A campaign is an ordered list of steps (template + delay in days). Leads are *enrolled*; the
engine (`tick`, run hourly by the scheduler or on demand) walks each enrolment:

  * Step N is only ever *queued for approval* (approval_queue) - a person approves before
    anything is sent, exactly like every other automated message.
  * The delay before step N+1 starts when step N is actually SENT, not when it was queued, so a
    message waiting for approval doesn't burn the gap.
  * A lead sits in at most one active campaign, and leads in a campaign are skipped by the
    legacy day-3/7/14 auto follow-ups, so nobody is mailed by two systems.
  * Any real reply (apply_reply), a terminal/advanced lead status, a suppressed or invalid
    address, or a skipped/failed message stops the enrolment with a recorded reason.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from . import eligibility
from .db import current_ws, execute, lead_scope, log_failure, query, query_one, utcnow
from .outreach import ensure_email_validated, is_suppressed, queue_message

ACTIVE_LEAD_STATUSES = ("NEW", "CONTACTED")      # anything further along stops the sequence
MAX_ENROLL_PER_CALL = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse(value: str | None) -> datetime | None:
    try:
        when = datetime.fromisoformat(value) if value else None
    except ValueError:
        return None
    return when.replace(tzinfo=timezone.utc) if when and when.tzinfo is None else when


def step_key(campaign_id: int, position: int) -> str:
    return f"c{campaign_id}s{position}"


def variant_for(step: dict, lead_id: int) -> str:
    """A/B split: a step with a second template sends B to odd lead ids and A to even ones.
    Deterministic, so a lead always gets the same variant and stats need no extra storage."""
    return "B" if step.get("template_b") and lead_id % 2 else "A"


# --------------------------------------------------------------- enrolment ---
def enroll(campaign_id: int, lead_ids: list[int]) -> dict:
    """Enrol leads the caller may see. -> {enrolled, skipped: {reason: count}}."""
    camp = query_one("SELECT * FROM campaigns WHERE id = ? AND workspace_id = ?", (campaign_id, current_ws()))
    if not camp:
        raise LookupError("Campaign not found")
    steps = query("SELECT position, delay_days FROM campaign_steps WHERE campaign_id = ? ORDER BY position",
                  (campaign_id,))
    if not steps:
        raise ValueError("Add at least one step before enrolling leads")
    scope, params = lead_scope()
    skipped: dict[str, int] = {}
    enrolled = 0

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for lead_id in list(dict.fromkeys(lead_ids))[:MAX_ENROLL_PER_CALL]:
        lead = query_one(f"SELECT * FROM leads WHERE id = ? AND {scope}", (lead_id, *params))
        if not lead:
            skip("not found"); continue
        if lead["status"] not in ACTIVE_LEAD_STATUSES:
            skip(f"lead is {lead['status']}"); continue
        if query_one("SELECT 1 AS x FROM campaign_enrollments WHERE lead_id = ? AND status = 'ACTIVE' "
                     "AND workspace_id = ?", (lead_id, current_ws())):
            skip("already in an active campaign"); continue
        if query_one("SELECT 1 AS x FROM campaign_enrollments WHERE campaign_id = ? AND lead_id = ?",
                     (campaign_id, lead_id)):
            skip("already in this campaign before"); continue
        ensure_email_validated(lead)
        if not lead.get("email"):
            skip("no email address"); continue
        if is_suppressed(lead["email"]):
            skip("suppressed"); continue
        if not eligibility.outreach_eligibility(lead, "email")["eligible"]:
            skip("not eligible (invalid or closed)"); continue
        now = _now()
        execute("INSERT INTO campaign_enrollments(workspace_id, campaign_id, lead_id, status, next_position, "
                "next_due_at, enrolled_at, updated_at) VALUES(?,?,?,'ACTIVE',0,?,?,?)",
                (current_ws(), campaign_id, lead_id, _iso(now + timedelta(days=steps[0]["delay_days"])),
                 _iso(now), _iso(now)))
        enrolled += 1
    return {"enrolled": enrolled, "skipped": skipped}


def matching_lead_ids(criteria: dict, limit: int = MAX_ENROLL_PER_CALL) -> list[int]:
    """Leads (in the caller's scope) matching simple filters: niche, city, priorities, min_score."""
    scope, params = lead_scope()
    clauses, args = [scope, "status IN ('NEW','CONTACTED')", "email IS NOT NULL", "email != ''"], list(params)
    if criteria.get("niche"):
        clauses.append("niche LIKE ?"); args.append(f"%{criteria['niche']}%")
    if criteria.get("city"):
        clauses.append("city LIKE ?"); args.append(f"%{criteria['city']}%")
    if criteria.get("priorities"):
        marks = ",".join("?" for _ in criteria["priorities"])
        clauses.append(f"lead_priority IN ({marks})"); args += list(criteria["priorities"])
    if criteria.get("min_score") is not None:
        clauses.append("lead_score >= ?"); args.append(criteria["min_score"])
    rows = query(f"SELECT id FROM leads WHERE {' AND '.join(clauses)} ORDER BY lead_score DESC, id LIMIT ?",
                 (*args, limit))
    return [r["id"] for r in rows]


# ------------------------------------------------------------------ engine ---
def _stop(enr: dict, reason: str) -> None:
    execute("UPDATE campaign_enrollments SET status = 'STOPPED', stop_reason = ?, waiting_queue_id = NULL, "
            "updated_at = ? WHERE id = ? AND workspace_id = ?", (reason[:120], utcnow(), enr["id"], current_ws()))


def stop_for_lead(lead_id: int, reason: str) -> int:
    """Stop every active enrolment of a lead (a reply arrived, they opted out...)."""
    return execute("UPDATE campaign_enrollments SET status = 'STOPPED', stop_reason = ?, "
                   "waiting_queue_id = NULL, updated_at = ? WHERE workspace_id = ? AND lead_id = ? "
                   "AND status = 'ACTIVE'", (reason[:120], utcnow(), current_ws(), lead_id)) or 0


def _advance(enr: dict, steps: list[dict], now: datetime) -> str:
    """Move one active enrolment forward. -> 'queued' | 'stopped' | 'completed' | 'wait'."""
    lead = query_one("SELECT * FROM leads WHERE id = ? AND workspace_id = ?", (enr["lead_id"], current_ws()))
    if not lead:
        _stop(enr, "lead deleted"); return "stopped"
    if lead["status"] not in ACTIVE_LEAD_STATUSES:
        _stop(enr, f"lead is {lead['status']}"); return "stopped"

    if enr["waiting_queue_id"]:
        q = query_one("SELECT status, sent_at FROM approval_queue WHERE id = ? AND workspace_id = ?",
                      (enr["waiting_queue_id"], current_ws()))
        status = (q or {}).get("status")
        if status in ("PENDING", "APPROVED"):
            return "wait"
        if status != "SENT":
            _stop(enr, f"message {(status or 'missing').lower()}"); return "stopped"
        if enr["next_position"] >= len(steps):
            execute("UPDATE campaign_enrollments SET status = 'COMPLETED', waiting_queue_id = NULL, "
                    "updated_at = ? WHERE id = ? AND workspace_id = ?", (utcnow(), enr["id"], current_ws()))
            return "completed"
        sent = _parse(q["sent_at"]) or now
        due = sent + timedelta(days=steps[enr["next_position"]]["delay_days"])
        execute("UPDATE campaign_enrollments SET waiting_queue_id = NULL, next_due_at = ?, updated_at = ? "
                "WHERE id = ? AND workspace_id = ?", (_iso(due), utcnow(), enr["id"], current_ws()))
        enr["waiting_queue_id"], enr["next_due_at"] = None, _iso(due)

    due_at = _parse(enr["next_due_at"])
    if due_at and due_at > now:
        return "wait"
    if enr["next_position"] >= len(steps):
        execute("UPDATE campaign_enrollments SET status = 'COMPLETED', updated_at = ? WHERE id = ? "
                "AND workspace_id = ?", (utcnow(), enr["id"], current_ws()))
        return "completed"

    step = steps[enr["next_position"]]
    key = step_key(enr["campaign_id"], step["position"])
    existing = query_one("SELECT id FROM approval_queue WHERE workspace_id = ? AND lead_id = ? AND step = ? "
                         "AND status IN ('PENDING','APPROVED','SENT') ORDER BY id DESC",
                         (current_ws(), lead["id"], key))
    try:
        qid = existing["id"] if existing else queue_message(
            lead, step["template_b"] if variant_for(step, lead["id"]) == "B" else step["template_name"],
            key, "email")
    except (ValueError, PermissionError) as exc:
        _stop(enr, f"cannot queue: {exc}"); return "stopped"
    if qid is None:
        _stop(enr, "not eligible (no usable email, suppressed or blocked)"); return "stopped"
    execute("UPDATE campaign_enrollments SET waiting_queue_id = ?, next_position = ?, updated_at = ? "
            "WHERE id = ? AND workspace_id = ?", (qid, enr["next_position"] + 1, utcnow(), enr["id"], current_ws()))
    return "queued"


def tick(campaign_id: int | None = None) -> dict:
    """Process due enrolments of ACTIVE campaigns in the current workspace."""
    out = {"queued": 0, "stopped": 0, "completed": 0}
    now = _now()
    clauses, args = ["c.workspace_id = ?", "c.status = 'ACTIVE'", "e.status = 'ACTIVE'"], [current_ws()]
    if campaign_id is not None:
        clauses.append("c.id = ?"); args.append(campaign_id)
    rows = query("SELECT e.* FROM campaign_enrollments e JOIN campaigns c ON c.id = e.campaign_id "
                 f"WHERE {' AND '.join(clauses)} ORDER BY e.id", tuple(args))
    steps_cache: dict[int, list[dict]] = {}
    for enr in rows:
        steps = steps_cache.setdefault(enr["campaign_id"], query(
            "SELECT position, template_name, template_b, delay_days FROM campaign_steps WHERE campaign_id = ? "
            "ORDER BY position", (enr["campaign_id"],)))
        try:
            result = _advance(enr, steps, now)
        except Exception as exc:  # noqa: BLE001 - one bad lead must not sink the run
            log_failure("campaigns.tick", str(exc), enr["lead_id"])
            continue
        if result in out:
            out[result] += 1
    return out


# ------------------------------------------------------------------- stats ---
def stats(campaign_id: int) -> dict:
    counts = {r["status"]: r["n"] for r in query(
        "SELECT status, COUNT(*) AS n FROM campaign_enrollments WHERE campaign_id = ? AND workspace_id = ? "
        "GROUP BY status", (campaign_id, current_ws()))}
    like = f"c{campaign_id}s%"
    queue = {r["status"]: r["n"] for r in query(
        "SELECT status, COUNT(*) AS n FROM approval_queue WHERE workspace_id = ? AND step LIKE ? GROUP BY status",
        (current_ws(), like))}
    replied = query_one("SELECT COUNT(*) AS n FROM campaign_enrollments WHERE campaign_id = ? AND "
                        "workspace_id = ? AND stop_reason LIKE ?", (campaign_id, current_ws(), "replied%"))["n"]
    return {"variants": ab_results(campaign_id), "active": counts.get("ACTIVE", 0), "completed": counts.get("COMPLETED", 0),
            "stopped": counts.get("STOPPED", 0), "enrolled": sum(counts.values()),
            "awaiting_approval": queue.get("PENDING", 0), "approved": queue.get("APPROVED", 0),
            "sent": queue.get("SENT", 0), "replied": replied}


def ab_results(campaign_id: int) -> list[dict]:
    """Per A/B step: sent and replied counts for each variant (reply is credited to the step
    that was last sent to the lead before they answered)."""
    out = []
    for st in query("SELECT position, template_name, template_b FROM campaign_steps WHERE campaign_id = ? "
                    "AND template_b IS NOT NULL ORDER BY position", (campaign_id,)):
        key = step_key(campaign_id, st["position"])
        sent = {"A": 0, "B": 0}
        for r in query("SELECT lead_id FROM approval_queue WHERE workspace_id = ? AND step = ? AND status = 'SENT'",
                       (current_ws(), key)):
            sent[variant_for(st, r["lead_id"])] += 1
        replied = {"A": 0, "B": 0}
        for r in query("SELECT lead_id FROM campaign_enrollments WHERE campaign_id = ? AND workspace_id = ? "
                       "AND stop_reason LIKE ? AND next_position = ?",
                       (campaign_id, current_ws(), "replied%", st["position"] + 1)):
            replied[variant_for(st, r["lead_id"])] += 1
        out.append({"position": st["position"],
                    "A": {"template": st["template_name"], "sent": sent["A"], "replied": replied["A"]},
                    "B": {"template": st["template_b"], "sent": sent["B"], "replied": replied["B"]}})
    return out


def parse_criteria(raw) -> dict:
    try:
        return json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        return {}
