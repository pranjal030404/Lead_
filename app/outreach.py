"""Outreach: template merge, suppression, approval queue, rate-limited sending.

The hard rule from Part 7.1 / 8.5: nothing automated ever contacts a human
without a person approving it first. The scheduler only ever *writes* to
approval_queue. Sending reads from it, and only rows you marked APPROVED.

Every send checks the suppression list immediately before going out - not when
the message was generated, which could have been days earlier.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from . import emailintel, http
from .config import settings
from .db import (current_ws, execute, lead_scope, log_automation, log_failure, query,
                 query_one, scalar, utcnow, workspace_context)
from .scoring import observation_for, website_issue_for

RESEND_URL = "https://api.resend.com/emails"

CADENCE = [
    (3, "day3", "follow_up_day_3"),
    (7, "day7", "follow_up_day_7"),
    (14, "day14", "follow_up_day_14"),
]
GIVE_UP_DAYS = 21


# ------------------------------------------------------------ suppression --

def is_suppressed(value: str | None, kind: str = "email") -> bool:
    if not value:
        return False
    return bool(query_one(
        "SELECT 1 AS x FROM suppression_list WHERE workspace_id = ? "
        "AND LOWER(value) = LOWER(?) AND kind = ?",
        (current_ws(), value.strip(), kind),
    ))


def suppress(value: str, kind: str = "email", reason: str = "manual") -> None:
    execute(
        "INSERT IGNORE INTO suppression_list(workspace_id, value, kind, reason, created_at) "
        "VALUES(?,?,?,?,?)",
        (current_ws(), value.strip(), kind, reason, utcnow()),
    )
    log_automation("suppression", f"Suppressed {kind} {value} ({reason})")


def unsuppress(value: str, kind: str = "email") -> None:
    execute("DELETE FROM suppression_list WHERE workspace_id = ? AND value = ? AND kind = ?",
            (current_ws(), value, kind))


# --------------------------------------------------------------- templates --

MERGE_FIELD_RE = re.compile(r"\{(\w+)\}")


def possessive(name: str) -> str:
    """"Django Meals" -> "Django Meals'", "Sharma Dental" -> "Sharma Dental's".

    Cold email is the whole point of this tool; a mangled possessive in the
    subject line is the first thing a recipient sees.
    """
    if not name:
        return "your business's"
    return f"{name}'" if name.rstrip().endswith(("s", "S")) else f"{name}'s"


def merge_context(lead: dict) -> dict:
    owner = (lead.get("owner_name") or "").strip()
    return {
        "business_name": lead.get("business_name") or "your business",
        "business_possessive": possessive(lead.get("business_name") or ""),
        "owner_name": owner or "there",
        "city": lead.get("city") or "",
        "area": lead.get("area") or "",
        "niche": (lead.get("niche") or "local").rstrip("s"),
        "phone": lead.get("phone") or "",
        "email": lead.get("email") or "",
        "website_url": lead.get("website_url") or "",
        "observation": observation_for(lead),
        "website_issue": website_issue_for(lead),
        "portfolio_url": settings_value("portfolio_url", "https://example.com/work"),
        "sender_name": settings.mail_from_name or "Me",
        "sender_company": settings_value("company_name", "") or brand_company_name(),
    }


def settings_value(key: str, default: str) -> str:
    from .db import current_ws_or_none, get_ws_setting, get_setting

    if current_ws_or_none() is not None:
        return get_ws_setting(key) or default
    return get_setting(key) or default


def brand_company_name() -> str:
    """The company identity used in outreach footers and email signatures.

    Precedence: operator-set company_name, then the white-label brand config,
    so a rebranded install signs its outreach correctly without touching the
    legacy identity setting."""
    from .branding import get_branding

    brand = get_branding()
    return brand["brand_company_name"] or brand["brand_name"]


def render(text: str | None, context: dict) -> str:
    if not text:
        return ""
    return MERGE_FIELD_RE.sub(lambda m: str(context.get(m.group(1), m.group(0))), text)


def render_template(template_name: str, lead: dict) -> dict:
    template = query_one("SELECT * FROM templates WHERE workspace_id = ? AND name = ?",
                         (current_ws(), template_name))
    if not template:
        raise ValueError(f"Template '{template_name}' not found")
    context = merge_context(lead)
    return {
        "template_name": template_name,
        "type": template["type"],
        "subject": render(template["subject"], context),
        "body": render(template["body"], context),
        "unfilled": sorted(set(MERGE_FIELD_RE.findall(
            (template["subject"] or "") + template["body"]
        )) - set(context)),
    }


def whatsapp_link(lead: dict, message: str) -> str | None:
    """Click-to-chat only. Per Part 7.1 this app never auto-sends WhatsApp."""
    number = lead.get("whatsapp")
    if not number:
        return None
    return f"https://wa.me/{number}?text={quote(message)}"


# ---------------------------------------------------------- approval queue --

def queue_message(lead: dict, template_name: str, step: str, channel: str = "email") -> int | None:
    if lead.get("workspace_id") != current_ws():
        raise PermissionError("lead belongs to a different workspace")
    rendered = render_template(template_name, lead)
    to_address = lead.get("email") if channel == "email" else lead.get("whatsapp")
    if channel == "email" and (not to_address or is_suppressed(to_address)):
        return None
    if channel == "email" and emailintel.email_blocked(lead["id"], to_address):
        return None

    existing = query_one(
        "SELECT id FROM approval_queue WHERE workspace_id = ? AND lead_id = ? AND step = ? "
        "AND status IN ('PENDING','APPROVED','SENT')",
        (current_ws(), lead["id"], step),
    )
    if existing:
        return None

    return execute(
        """INSERT INTO approval_queue
           (workspace_id, lead_id, channel, template_name, step, to_address, subject, body,
            status, created_at)
           VALUES(?,?,?,?,?,?,?,?,'PENDING',?)""",
        (current_ws(), lead["id"], channel, template_name, step, to_address,
         rendered["subject"], rendered["body"], utcnow()),
    )


def _days_since(value: str | None) -> int | None:
    if not value:
        return None
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).days


def generate_followups() -> dict:
    """Generation only - never sends. Part 8.5. Acts on the current workspace.

    Scoped to the caller's leads when a signed-in user is present (a regular
    user only nudges their own pipeline); background runs with no user context
    cover the whole workspace."""
    generated, lost = 0, 0
    scope, params = lead_scope()
    leads = query(f"SELECT * FROM leads WHERE {scope} AND status = 'CONTACTED' "
                  f"AND last_contacted_date IS NOT NULL", tuple(params))

    for lead in leads:
        days = _days_since(lead["last_contacted_date"])
        if days is None:
            continue

        if days >= GIVE_UP_DAYS:
            execute(
                "UPDATE leads SET status = 'LOST', "
                "notes = CONCAT(COALESCE(notes,''), ?), updated_at = ? "
                "WHERE id = ? AND workspace_id = ?",
                (f"\n[auto] No response after {GIVE_UP_DAYS} days.", utcnow(), lead["id"],
                 current_ws()),
            )
            lost += 1
            continue

        step_due = None
        for threshold, step, template_name in CADENCE:
            if days >= threshold:
                step_due = (step, template_name)
        if not step_due:
            continue

        step, template_name = step_due
        channel = "email" if lead.get("email") else "whatsapp"
        if channel == "whatsapp":
            template_name = "whatsapp_follow_up"
        try:
            if queue_message(lead, template_name, step, channel):
                generated += 1
                log_automation(
                    "followup",
                    f"Generated {step} follow-up for {lead['business_name']} - awaiting approval",
                )
        except Exception as exc:  # noqa: BLE001
            log_failure("followup.generate", str(exc), lead["id"])

    return {"generated": generated, "marked_lost": lost}


def set_approval_status(queue_id: int, status: str) -> None:
    execute(
        "UPDATE approval_queue SET status = ?, reviewed_at = ? WHERE id = ? AND workspace_id = ?",
        (status, utcnow(), queue_id, current_ws()),
    )


# ------------------------------------------------------------------ sending --

def sent_last_hour() -> int:
    """Emails sent in the last hour across ALL workspaces, on purpose: the cap
    protects the shared sending domain/API key, which every tenant draws on."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    return int(scalar(
        "SELECT COUNT(*) FROM approval_queue WHERE status = 'SENT' AND sent_at >= ?", (cutoff,)
    ))


def _footer() -> str:
    address = settings.unsubscribe_mailto or settings.mail_from
    if not address:
        return ""
    return (
        "\n\n---\n"
        f"{settings.mail_from_name or ''} - {brand_company_name()}\n"
        f"Don't want to hear from me again? Reply STOP or email {address} "
        "and I'll remove you immediately."
    )


def send_email(to_address: str, subject: str, body: str) -> dict:
    """Send via Resend. DRY_RUN logs instead of sending (Part 7.7)."""
    if is_suppressed(to_address):
        return {"ok": False, "error": "recipient is on the suppression list"}

    full_body = body + _footer()

    if settings.dry_run:
        log_automation("send", f"[DRY RUN] would email {to_address}: {subject}")
        return {"ok": True, "dry_run": True}

    if not settings.resend_api_key or not settings.mail_from:
        return {"ok": False, "error": "RESEND_API_KEY / MAIL_FROM not configured"}

    sender = (
        f"{settings.mail_from_name} <{settings.mail_from}>"
        if settings.mail_from_name else settings.mail_from
    )
    unsubscribe = settings.unsubscribe_mailto or settings.mail_from
    try:
        response = http.post(
            RESEND_URL,
            breaker="resend",
            retries=3,
            headers={"Authorization": f"Bearer {settings.resend_api_key}",
                     "Content-Type": "application/json"},
            json={
                "from": sender,
                "to": [to_address],
                "subject": subject,
                "text": full_body,
                "headers": {
                    "List-Unsubscribe": f"<mailto:{unsubscribe}?subject=unsubscribe>",
                    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
                },
            },
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    if response.status_code >= 400:
        detail = response.text[:300]
        # Hard bounce / invalid recipient - suppress so we never retry it.
        if response.status_code in (400, 422) and "invalid" in detail.lower():
            suppress(to_address, "email", "hard bounce / invalid address")
        return {"ok": False, "error": f"{response.status_code}: {detail}"}
    return {"ok": True, "id": response.json().get("id")}


def log_interaction(lead_id: int, kind: str, subject: str, content: str,
                    outcome: str = "", automated: bool = True) -> None:
    now = utcnow()
    execute(
        """INSERT INTO interactions
           (workspace_id, lead_id, type, direction, date, subject, content, outcome,
            automated, created_at)
           VALUES(?,?,?,'out',?,?,?,?,?,?)""",
        (current_ws(), lead_id, kind, now, subject, content, outcome, 1 if automated else 0, now),
    )
    execute(
        """UPDATE leads SET
             status = CASE WHEN status = 'NEW' THEN 'CONTACTED' ELSE status END,
             first_contacted_date = COALESCE(first_contacted_date, ?),
             last_contacted_date = ?,
             total_touchpoints = total_touchpoints + 1,
             contact_method = ?,
             updated_at = ?
           WHERE id = ? AND workspace_id = ?""",
        (now, now, kind, now, lead_id, current_ws()),
    )


def send_approved(limit: int | None = None, workspace_id: int | None = None,
                  owner_user_id: int | None = None) -> dict:
    """Send APPROVED email rows, respecting the (shared) hourly cap.

    `workspace_id` limits sending to one tenant (the API passes the caller's).
    None means every workspace - only the scheduler uses that. Each row is sent
    inside its own workspace so suppression and logging use that tenant's data.
    `owner_user_id` narrows further to one person's leads (a regular user's
    manual send); None leaves every owner in scope (scheduler/admin)."""
    budget = settings.max_emails_per_hour - sent_last_hour()
    if budget <= 0:
        return {"sent": 0, "failed": 0, "skipped": 0,
                "note": f"hourly cap of {settings.max_emails_per_hour} already used"}
    if limit:
        budget = min(budget, limit)

    clauses, params = ["q.status = 'APPROVED'", "q.channel = 'email'"], []
    source = "approval_queue q"
    if owner_user_id is not None:
        source += " JOIN leads l ON l.id = q.lead_id"
        clauses.append("l.owner_user_id = ?")
        params.append(owner_user_id)
    if workspace_id is not None:
        clauses.append("q.workspace_id = ?")
        params.append(workspace_id)
    params.append(budget)
    rows = query(
        f"SELECT q.* FROM {source} WHERE {' AND '.join(clauses)} ORDER BY q.id LIMIT ?",
        tuple(params),
    )
    sent = failed = skipped = 0

    for row in rows:
        with workspace_context(row["workspace_id"]):
            outcome = _send_row(row)
        if outcome == "sent":
            sent += 1
        elif outcome == "failed":
            failed += 1
        else:
            skipped += 1

    if sent:
        log_automation(
            "send",
            f"Sent {sent} approved message(s)" + (" (dry run)" if settings.dry_run else ""),
        )
    return {"sent": sent, "failed": failed, "skipped": skipped, "dry_run": settings.dry_run}


def _send_row(row: dict) -> str:
    """Send one approved row. Caller has entered the row's workspace."""
    wid = row["workspace_id"]
    if is_suppressed(row["to_address"]):
        execute(
            "UPDATE approval_queue SET status = 'SKIPPED', error_message = ? "
            "WHERE id = ? AND workspace_id = ?",
            ("recipient suppressed at send time", row["id"], wid),
        )
        return "skipped"

    blocked = emailintel.email_blocked(row["lead_id"], row["to_address"])
    if blocked:
        execute(
            "UPDATE approval_queue SET status = 'SKIPPED', error_message = ? "
            "WHERE id = ? AND workspace_id = ?",
            (f"recipient blocked at send time: {blocked}", row["id"], wid),
        )
        return "skipped"

    result = send_email(row["to_address"], row["subject"], row["body"])
    if result.get("ok"):
        execute(
            "UPDATE approval_queue SET status = 'SENT', sent_at = ? "
            "WHERE id = ? AND workspace_id = ?",
            (utcnow(), row["id"], wid),
        )
        log_interaction(
            row["lead_id"], "email", row["subject"], row["body"],
            outcome="dry-run" if result.get("dry_run") else "sent",
        )
        return "sent"
    execute(
        "UPDATE approval_queue SET status = 'FAILED', error_message = ? "
        "WHERE id = ? AND workspace_id = ?",
        (result.get("error"), row["id"], wid),
    )
    log_failure("outreach.send", result.get("error", "unknown"), row["lead_id"])
    return "failed"
