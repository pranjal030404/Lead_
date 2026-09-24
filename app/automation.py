"""The Lead Automator (Part 8).

Discovery, enrichment and scoring run fully automatically. Follow-up messages
are only ever *generated* into the approval queue - a person approves before
anything reaches a human.

Every job checks two switches before doing anything: the master kill switch and
its own per-job switch. Both live in the database, so flipping one takes effect
on the next tick without a restart.
"""

from __future__ import annotations

import os
import socket
import threading
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .backup import run_backup
from .config import parse_hhmm, settings
from .db import (execute, get_conn, get_setting, log_automation, log_failure, query,
                 query_one, scalar, set_setting, today, utcnow)
from . import notify
from .enrich import enrich_pending
from .outreach import generate_followups, send_approved
from .quota import usage_summary

scheduler: BackgroundScheduler | None = None

JOB_SWITCHES = {
    "discovery": "job_discovery",
    "enrichment": "job_enrichment",
    "followup_generation": "job_followup_generation",
    "sending": "job_sending",
}


def master_on() -> bool:
    return get_setting("automation_master", "off") == "on"


def job_on(job: str) -> bool:
    return master_on() and get_setting(JOB_SWITCHES.get(job, ""), "off") == "on"


def set_switch(name: str, value: bool) -> None:
    set_setting(name, "on" if value else "off")
    log_automation("control", f"{name} switched {'ON' if value else 'OFF'}")


def switches() -> dict:
    return {
        "automation_master": master_on(),
        **{key: get_setting(key, "off") == "on" for key in JOB_SWITCHES.values()},
    }


# --------------------------------------------------------------------- jobs --

def _next_date(auto_search: str) -> str:
    days = {"daily": 1, "weekly": 7, "monthly": 30}.get(auto_search, 7)
    return (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()


def job_discovery(force: bool = False) -> dict:
    """`force` is set when you press Run now in the UI - an explicit operator
    action shouldn't silently no-op just because the schedule is paused."""
    if not force and not job_on("discovery"):
        return {"skipped": "discovery switched off"}

    from .search_service import get_coverage, run_search

    due = query(
        """SELECT * FROM targets
           WHERE is_active = 1 AND auto_search != 'manual'
             AND (next_search_date IS NULL OR next_search_date <= ?)
           ORDER BY COALESCE(last_searched, '') ASC
           LIMIT ?""",
        (today(), settings.targets_per_run),
    )
    if not due:
        log_automation("discovery", "No targets due today")
        return {"ran": 0}

    ran, skipped = 0, 0
    for target in due:
        coverage = get_coverage(target["niche"], target["city"], target["area"],
                                target["country"])
        if coverage["status"] == "EXHAUSTED":
            # Three barren runs in a row - stop burning quota here and say so.
            execute("UPDATE targets SET is_active = 0 WHERE id = ?", (target["id"],))
            log_automation(
                "discovery",
                f"Target '{target['niche']}' in {target['area'] or target['city']} is "
                "EXHAUSTED - deactivated. Add a new area for this city to keep going.",
                "warn",
            )
            skipped += 1
            continue
        try:
            result = run_search(
                niche=target["niche"], city=target["city"], area=target["area"] or "",
                country=target["country"] or "", max_results=target["max_results"],
                triggered_by="automation",
            )
            ran += 1
            execute(
                "UPDATE targets SET last_searched = ?, next_search_date = ? WHERE id = ?",
                (utcnow(), _next_date(target["auto_search"]), target["id"]),
            )
            if result.get("quota_hit"):
                log_automation("discovery", "Daily API cap reached - stopping for today", "warn")
                break
        except Exception as exc:  # noqa: BLE001
            log_failure("automation.discovery", str(exc), target["id"])
    return {"ran": ran, "skipped": skipped}


def job_enrichment(force: bool = False) -> dict:
    if not force and not job_on("enrichment"):
        return {"skipped": "enrichment switched off"}
    result = enrich_pending(limit=100)
    log_automation(
        "enrichment",
        f"Enriched {result['enriched']} lead(s), {result['failed']} failed, "
        f"{result['new_hot']} new HOT",
    )
    return result


def job_scoring(force: bool = False) -> dict:
    """Re-score anything enriched since the last pass (cheap, no API calls).
    Run now re-scores everything, which is what you want after a rules change."""
    if not force and not job_on("enrichment"):
        return {"skipped": "enrichment switched off"}
    from .enrich import _update
    from .scoring import score_lead

    if force:
        leads = query("SELECT * FROM leads")
    else:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
        leads = query("SELECT * FROM leads WHERE updated_at >= ?", (cutoff,))
    for lead in leads:
        _update(lead["id"], score_lead(lead))
    log_automation("scoring", f"Re-scored {len(leads)} recently-updated lead(s)")
    return {"scored": len(leads)}


def job_followups(force: bool = False) -> dict:
    if not force and not job_on("followup_generation"):
        return {"skipped": "follow-up generation switched off"}
    result = generate_followups()
    log_automation(
        "followup",
        f"{result['generated']} follow-up(s) queued for approval, "
        f"{result['marked_lost']} marked Lost - No Response",
    )
    return result


def job_sending(force: bool = False) -> dict:
    # Even forced, this only ever sends messages already marked APPROVED, and
    # still checks the suppression list and dry-run flag before each send.
    if not force and not job_on("sending"):
        return {"skipped": "sending switched off"}
    return send_approved()


def job_backup(force: bool = False) -> dict:
    try:
        result = run_backup()
        notify.prune()
        return result
    except Exception as exc:  # noqa: BLE001
        log_failure("automation.backup", str(exc))
        log_automation("backup", f"Backup FAILED: {exc}", "error")
        return {"error": str(exc)}


def build_digest() -> str:
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    new_leads = scalar("SELECT COUNT(*) FROM leads WHERE created_at >= ?", (since,))
    hot = scalar("SELECT COUNT(*) FROM leads WHERE lead_priority = 'HOT' AND created_at >= ?",
                 (since,))
    pending = scalar("SELECT COUNT(*) FROM approval_queue WHERE status = 'PENDING'")
    due = scalar("SELECT COUNT(*) FROM leads WHERE follow_up_date IS NOT NULL "
                 "AND follow_up_date <= ?", (today(),))
    failures = scalar("SELECT COUNT(*) FROM failed_jobs WHERE resolved = 0")
    searches = query(
        "SELECT niche, city, area, new_leads, status FROM searches WHERE started_at >= ?",
        (since,),
    )
    usage = usage_summary()
    backup_row = query_one(
        "SELECT created_at FROM automation_log WHERE job = 'backup' ORDER BY id DESC LIMIT 1"
    )

    lines = [
        f"Arthvex LeadGen - daily digest {today()}",
        "",
        f"  New leads (24h):        {new_leads}",
        f"  New HOT leads:          {hot}",
        f"  Awaiting your approval: {pending}",
        f"  Follow-ups due today:   {due}",
        f"  Unresolved job errors:  {failures}",
        "",
        f"  API calls today:        {usage['google']['used_today']} / {usage['google']['cap']}"
        f" ({usage['google']['pct']}%)",
        f"  Last backup:            {backup_row['created_at'] if backup_row else 'NEVER - check this'}",
    ]

    if settings.backup_remote:
        synced = query_one(
            "SELECT created_at FROM automation_log WHERE job = 'backup' "
            "AND message LIKE ? ORDER BY id DESC LIMIT 1",
            ("Off-site sync OK%",),
        )
        lines.append(
            f"  Last off-site sync:     "
            f"{synced['created_at'] if synced else 'NEVER - check BACKUP_REMOTE'}"
        )

    lines += ["", "Searches in the last 24h:"]
    if searches:
        lines += [
            f"  - {s['niche']} in {s['area'] or s['city']}: {s['new_leads']} new ({s['status']})"
            for s in searches
        ]
    else:
        lines.append("  - none")

    if failures:
        lines += ["", "Check the Automation page - there are unresolved job failures."]
    return "\n".join(lines)


def job_digest(force: bool = False) -> dict:
    """Health check + daily digest (Part 7.6). Runs even when automation is off,
    so a silent morning genuinely means something is broken.

    Goes out through `notify`, not `outreach.send_email`: this is mail to the
    operator, so it sends during DRY_RUN too. A dry run you can't see the
    results of is not a test - 7.7 asks you to read a week of output before
    going live, which requires the output to actually arrive.
    """
    digest = build_digest()
    log_automation("digest", digest)
    result = notify.send_digest(digest)
    if result.get("error"):
        log_failure("automation.digest", result["error"])
    return {"digest": digest, "emailed": bool(result.get("ok"))}


JOBS = {
    "discovery": job_discovery,
    "enrichment": job_enrichment,
    "scoring": job_scoring,
    "followups": job_followups,
    "sending": job_sending,
    "digest": job_digest,
    "backup": job_backup,
}


# Jobs that can take minutes (website checks, whole-table passes). Running these
# inline would hold the HTTP request open until they finish and hang the browser.
LONG_RUNNING = {"discovery", "enrichment", "scoring", "followups"}


def run_job_now(name: str) -> dict:
    job = JOBS.get(name)
    if not job:
        raise ValueError(f"Unknown job '{name}'")
    log_automation("control", f"Job '{name}' run manually")

    if name in LONG_RUNNING:
        def runner() -> None:
            try:
                job(force=True)
            except Exception as exc:  # noqa: BLE001
                log_failure(f"manual.{name}", str(exc))
                log_automation(name, f"Manual run failed: {exc}", "error")

        threading.Thread(target=runner, daemon=True).start()
        return {"started": True, "note": "running in the background - watch the activity feed"}

    return job(force=True)


# ------------------------------------------------------- leader election --

OWNER = f"{socket.gethostname()}:{os.getpid()}"
LEASE_SECONDS = 120
LEASE_TICK_SECONDS = 45

_is_leader = False


def claim_lease() -> bool:
    """Take or renew the scheduler lease. False means another worker holds it.

    The transaction takes the write lock before reading, so two workers starting
    at the same instant can't both conclude the lease is free.
    """
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(seconds=LEASE_SECONDS)).isoformat(timespec="seconds")
    with get_conn() as conn:
        conn.execute("START TRANSACTION")
        try:
            row = conn.execute(
                "SELECT owner, expires_at FROM scheduler_lease WHERE id = 1 FOR UPDATE"
            ).fetchone()
            mine_or_stale = row is None or row["owner"] == OWNER or (
                (row["expires_at"] or "") < now.isoformat(timespec="seconds")
            )
            if not mine_or_stale:
                conn.execute("ROLLBACK")
                return False
            conn.execute(
                "INSERT INTO scheduler_lease(id, owner, expires_at) VALUES(1,?,?) "
                "ON DUPLICATE KEY UPDATE owner = VALUES(owner), "
                "expires_at = VALUES(expires_at)",
                (OWNER, expires),
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise


def lease_holder() -> dict | None:
    return query_one("SELECT owner, expires_at FROM scheduler_lease WHERE id = 1")


def is_leader() -> bool:
    return _is_leader


# ---------------------------------------------------------------- scheduler --

SCHEDULED_PLAN = [
    ("discovery", job_discovery, "discovery_time", (6, 0)),
    ("enrichment", job_enrichment, "enrichment_time", (6, 30)),
    ("scoring", job_scoring, "scoring_time", (7, 0)),
    ("followups", job_followups, "followup_time", (7, 30)),
    ("digest", job_digest, "digest_time", (8, 0)),
    ("backup", job_backup, "backup_time", (2, 0)),
]


def _add_real_jobs() -> None:
    for name, func, attr, fallback in SCHEDULED_PLAN:
        hour, minute = parse_hhmm(getattr(settings, attr), fallback)
        scheduler.add_job(
            func, CronTrigger(hour=hour, minute=minute), id=name,
            replace_existing=True, misfire_grace_time=3600, coalesce=True,
        )
    scheduler.add_job(
        job_sending, CronTrigger(minute="*/30"), id="sending",
        replace_existing=True, misfire_grace_time=600, coalesce=True,
    )


def _remove_real_jobs() -> None:
    for name, *_ in SCHEDULED_PLAN:
        if scheduler.get_job(name):
            scheduler.remove_job(name)
    if scheduler.get_job("sending"):
        scheduler.remove_job("sending")


def _leader_tick() -> None:
    """Renew the lease, or pick it up if the previous holder went away."""
    global _is_leader
    try:
        won = claim_lease()
    except Exception as exc:  # noqa: BLE001 - never let this kill the scheduler
        log_failure("scheduler.lease", str(exc))
        return

    if won and not _is_leader:
        _is_leader = True
        _add_real_jobs()
        log_automation("scheduler", f"{OWNER} took the scheduler lease - jobs active")
    elif not won and _is_leader:
        # Another worker took over, most likely because this one stalled long
        # enough for the lease to lapse. Stand down rather than double-run.
        _is_leader = False
        _remove_real_jobs()
        log_automation("scheduler", f"{OWNER} lost the scheduler lease - jobs paused", "warn")


def start_scheduler() -> BackgroundScheduler:
    global scheduler
    if scheduler and scheduler.running:
        return scheduler

    scheduler = BackgroundScheduler(timezone="UTC")
    # The lease tick runs in every worker; only the winner gets the real jobs.
    scheduler.add_job(
        _leader_tick, "interval", seconds=LEASE_TICK_SECONDS, id="_leader",
        replace_existing=True, next_run_time=datetime.now(timezone.utc),
    )
    scheduler.start()
    _leader_tick()
    if not _is_leader:
        holder = lease_holder()
        log_automation(
            "scheduler",
            f"{OWNER} started as standby - {holder['owner'] if holder else 'another worker'} "
            "holds the scheduler lease",
        )
    return scheduler


def stop_scheduler() -> None:
    global scheduler, _is_leader
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        scheduler = None
    if _is_leader:
        # Hand the lease back on a clean shutdown so a standby worker picks the
        # jobs up immediately instead of waiting out the full lease.
        try:
            execute("DELETE FROM scheduler_lease WHERE id = 1 AND owner = ?", (OWNER,))
        except Exception:  # noqa: BLE001 - shutdown must not raise
            pass
        _is_leader = False


def scheduled_jobs() -> list[dict]:
    if not scheduler:
        return []
    return [
        {
            "id": job.id,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
        }
        for job in scheduler.get_jobs()
    ]
