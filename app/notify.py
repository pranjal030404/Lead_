"""Operator alerts (Part 7.6).

Three things the system should tell *you* about without you going looking: a
lead worth dropping everything for, an API budget about to run out, and whether
last night's jobs ran at all.

These are transactional messages to the operator, not outreach, so they take a
deliberately different path from `outreach.send_email`:

  - **No unsubscribe footer.** You can't unsubscribe from your own health check.
  - **No suppression-list check.** The suppression list protects *prospects*. If
    your own address ever landed on it - easy enough, replying "stop" to your own
    test message does it - alerts would go quiet exactly when you needed them,
    which is the failure this whole section exists to prevent.
  - **They send even when DRY_RUN is on.** Dry run exists so nothing reaches a
    *prospect* during the trial week (7.7). A week of silence from your own
    health check would defeat the point of running the trial at all.

Every alert is claimed through the `alerts_sent` table before it goes out, so a
HOT lead alerts once ever and a quota warning fires once a day - however many
times the code paths that raise them get re-entered.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import http
from .config import settings
from .db import get_conn, log_automation, log_failure, query, today, utcnow

RESEND_URL = "https://api.resend.com/emails"


def recipient() -> str:
    """Where alerts go. ALERT_EMAIL first, else whatever address sends mail."""
    return settings.alert_email or settings.mail_from


def _claim(alert_key: str, kind: str) -> bool:
    """Atomically claim an alert key. False means it already went out.

    The INSERT is the claim - two workers racing on the same key can't both
    win, so a lead can never be alerted twice.
    """
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT IGNORE INTO alerts_sent(alert_key, kind, created_at) VALUES(?,?,?)",
            (alert_key, kind, utcnow()),
        )
        return cur.rowcount > 0


def _send(subject: str, body: str) -> dict:
    """Deliver to the operator. Never routed through outreach.send_email - see
    the module docstring for why the suppression check must not apply here."""
    to_address = recipient()
    if not to_address:
        return {"ok": False, "error": "no ALERT_EMAIL / MAIL_FROM configured"}
    if not settings.resend_api_key:
        return {"ok": False, "error": "RESEND_API_KEY not configured"}

    sender = (
        f"{settings.mail_from_name} <{settings.mail_from}>"
        if settings.mail_from_name else settings.mail_from
    )
    try:
        response = http.post(
            RESEND_URL,
            breaker="resend",
            retries=3,
            headers={"Authorization": f"Bearer {settings.resend_api_key}",
                     "Content-Type": "application/json"},
            json={"from": sender, "to": [to_address], "subject": subject, "text": body},
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    if response.status_code >= 400:
        return {"ok": False, "error": f"{response.status_code}: {response.text[:300]}"}
    return {"ok": True, "id": response.json().get("id")}


def alert(alert_key: str, kind: str, subject: str, body: str) -> dict:
    """Send one deduplicated operator alert.

    Always writes to the activity feed, even when email isn't configured or is
    switched off - the feed is the fallback channel, so an alert is never lost
    just because mail isn't set up yet.
    """
    if not _claim(alert_key, kind):
        return {"skipped": "already alerted"}

    log_automation("alert", subject, "warn" if kind == "quota" else "info")

    if not settings.alerts_enabled:
        return {"skipped": "alerts disabled", "logged": True}
    if not recipient() or not settings.resend_api_key:
        return {"skipped": "email not configured", "logged": True}

    result = _send(subject, body)
    if not result.get("ok"):
        log_failure(f"alert.{kind}", result.get("error", "unknown"))
    return result


# ------------------------------------------------------------- hot leads --

def _describe(lead: dict) -> str:
    bits = [f"  {lead['business_name']} - score {lead['lead_score']}/10"]
    where = ", ".join(p for p in (lead.get("area"), lead.get("city")) if p)
    if where:
        bits.append(f"    {where}")
    if lead.get("phone"):
        bits.append(f"    phone: {lead['phone']}")
    if lead.get("email"):
        bits.append(f"    email: {lead['email']}")
    if not lead.get("website_url"):
        bits.append("    no website at all")
    elif lead.get("has_website") == 0:
        bits.append(f"    listed site is dead: {lead['website_url']}")
    return "\n".join(bits)


def alert_new_hot_leads(lead_ids: list[int]) -> dict:
    """Alert on leads that scored at or above HOT_ALERT_MIN_SCORE (Part 7.6).

    Batched into a single email - ten separate alerts for one overnight run is
    how an operator learns to ignore alerts. Safe to call from every path that
    creates leads, because each lead is claimed individually.
    """
    if not lead_ids:
        return {"alerted": 0}

    placeholders = ",".join("?" for _ in lead_ids)
    leads = query(
        f"SELECT * FROM leads WHERE id IN ({placeholders}) AND lead_score >= ? "
        "ORDER BY lead_score DESC, id ASC",
        (*lead_ids, settings.hot_alert_min_score),
    )
    fresh = [lead for lead in leads if _claim(f"hot:{lead['id']}", "hot_lead")]
    if not fresh:
        return {"alerted": 0}

    if len(fresh) == 1:
        subject = f"New HOT lead - {fresh[0]['business_name']}"
    else:
        subject = f"{len(fresh)} new HOT leads"
    body = "\n\n".join([
        f"Scored {settings.hot_alert_min_score}+/10 and waiting in the dashboard:",
        "\n\n".join(_describe(lead) for lead in fresh),
        "Open the Leads page and filter by HOT to work them.",
    ])

    log_automation("alert", f"{len(fresh)} new HOT lead(s): "
                            + ", ".join(lead["business_name"] for lead in fresh))

    if not settings.alerts_enabled or not recipient() or not settings.resend_api_key:
        return {"alerted": len(fresh), "emailed": False}

    result = _send(subject, body)
    if not result.get("ok"):
        log_failure("alert.hot_lead", result.get("error", "unknown"))
    return {"alerted": len(fresh), "emailed": bool(result.get("ok"))}


# ----------------------------------------------------------------- quota --

def alert_quota(used: int, cap: int) -> dict:
    """Warn once a day when the API budget crosses QUOTA_WARN_PCT (Part 7.6).

    Deliberately fires on the *approach*, not the hard stop - by the time the
    cap is hit the day's discovery has already stopped, and knowing an hour
    earlier is what lets you raise the cap or leave it alone on purpose.

    Takes the counts directly rather than a usage dict, because this sits on the
    path of every API call - it must not cost a query until it actually fires.
    """
    if not cap:
        return {"skipped": "no cap"}
    pct = round(used / cap * 100)
    if pct < settings.quota_warn_pct:
        return {"skipped": "under threshold"}

    return alert(
        f"quota:google:{today()}",
        "quota",
        f"API budget {pct}% used today ({used}/{cap})",
        f"Google Places calls today: {used} of {cap} ({pct}%).\n\n"
        f"At the cap, discovery stops for the day and resumes tomorrow - nothing\n"
        f"breaks and no money is spent past the limit. Raise\n"
        f"MAX_GOOGLE_CALLS_PER_DAY only if the Cloud Console quota limit allows\n"
        f"for it too; the in-app cap is the second layer, not the real one.",
    )


def send_digest(body: str) -> dict:
    """Daily health-check digest. Not deduplicated - it is supposed to arrive
    every morning, and its *absence* is the signal that something is broken."""
    if not settings.alerts_enabled:
        return {"skipped": "alerts disabled"}
    if not recipient() or not settings.resend_api_key:
        return {"skipped": "email not configured"}
    return _send(f"LeadGen digest {today()}", body)


def prune(days: int = 90) -> int:
    """Drop old claim rows. Hot-lead keys are unique per lead so they never
    re-fire, but there's no reason to keep quota keys around forever.

    The cutoff is built in Python rather than with SQLite's `datetime('now')`:
    `created_at` is written by `utcnow()` as `...T...+00:00`, and SQLite renders
    a space separator with no offset, so the two don't sort against each other.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM alerts_sent WHERE kind = 'quota' AND created_at < ?",
            (cutoff,),
        )
        return cur.rowcount
