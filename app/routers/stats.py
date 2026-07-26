from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from ..db import query, query_one, scalar, today
from ..quota import usage_summary

router = APIRouter(prefix="/api", tags=["stats"])

STAGE_ORDER = ["NEW", "CONTACTED", "REPLIED", "MEETING_SET", "PROPOSAL_SENT",
               "NEGOTIATION", "WON"]
STAGE_RANK = {name: i for i, name in enumerate(STAGE_ORDER)}


def _days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


@router.get("/stats")
def dashboard_stats():
    total = scalar("SELECT COUNT(*) FROM leads")
    hot = scalar("SELECT COUNT(*) FROM leads WHERE lead_priority = 'HOT'")
    warm = scalar("SELECT COUNT(*) FROM leads WHERE lead_priority = 'WARM'")
    # Fall back to the listed URL when the site hasn't been checked yet -
    # otherwise every un-enriched lead is miscounted as having no website.
    no_site = scalar(
        "SELECT COUNT(*) FROM leads WHERE "
        "COALESCE(has_website, CASE WHEN COALESCE(website_url,'') = '' THEN 0 ELSE 1 END) = 0"
    )
    contacted = scalar("SELECT COUNT(*) FROM leads WHERE total_touchpoints > 0")
    won = scalar("SELECT COUNT(*) FROM leads WHERE status = 'WON'")
    won_this_month = scalar(
        "SELECT COUNT(*) FROM leads WHERE status = 'WON' AND updated_at >= ?",
        (datetime.now(timezone.utc).replace(day=1).isoformat(timespec="seconds"),),
    )
    due_today = scalar(
        "SELECT COUNT(*) FROM leads WHERE follow_up_date IS NOT NULL AND follow_up_date <= ?"
        " AND status NOT IN ('WON','LOST')", (today(),)
    )
    pending_approval = scalar("SELECT COUNT(*) FROM approval_queue WHERE status = 'PENDING'")
    new_this_week = scalar("SELECT COUNT(*) FROM leads WHERE created_at >= ?", (_days_ago(7),))
    pending_enrichment = scalar(
        "SELECT COUNT(*) FROM leads WHERE enrichment_status IN ('pending','partial')"
    )
    unresolved_failures = scalar("SELECT COUNT(*) FROM failed_jobs WHERE resolved = 0")

    return {
        "total_leads": total,
        "hot_leads": hot,
        "warm_leads": warm,
        "no_website": no_site,
        "contacted": contacted,
        "won": won,
        "won_this_month": won_this_month,
        "follow_ups_due": due_today,
        "pending_approval": pending_approval,
        "new_this_week": new_this_week,
        "pending_enrichment": pending_enrichment,
        "unresolved_failures": unresolved_failures,
        "by_priority": query(
            "SELECT lead_priority AS priority, COUNT(*) AS n FROM leads GROUP BY lead_priority"
        ),
        "by_status": query("SELECT status, COUNT(*) AS n FROM leads GROUP BY status"),
        "by_niche": query(
            "SELECT niche, COUNT(*) AS n, SUM(CASE WHEN lead_priority='HOT' THEN 1 ELSE 0 END) "
            "AS hot FROM leads WHERE niche IS NOT NULL GROUP BY niche ORDER BY n DESC LIMIT 12"
        ),
        "by_city": query(
            "SELECT city, COUNT(*) AS n, SUM(CASE WHEN lead_priority='HOT' THEN 1 ELSE 0 END) "
            "AS hot FROM leads WHERE city IS NOT NULL GROUP BY city ORDER BY n DESC LIMIT 12"
        ),
        "quota": usage_summary(),
    }


@router.get("/pipeline")
def pipeline():
    stages = query("SELECT * FROM pipeline_stages ORDER BY position")
    counts = {r["status"]: r["n"] for r in
              query("SELECT status, COUNT(*) AS n FROM leads GROUP BY status")}
    result = []
    for stage in stages:
        leads = query(
            "SELECT id, business_name, city, lead_score, lead_priority, phone, email, "
            "last_contacted_date, follow_up_date FROM leads WHERE status = ? "
            "ORDER BY lead_score DESC LIMIT 25",
            (stage["name"],),
        )
        result.append({**stage, "count": counts.get(stage["name"], 0), "leads": leads})
    return result


@router.get("/stats/conversions")
def conversions():
    """Funnel rates. A lead's current status implies it passed every earlier
    stage; LOST leads count as contacted if they have any touchpoint logged."""
    leads = query("SELECT status, total_touchpoints FROM leads")
    reached = {stage: 0 for stage in STAGE_ORDER}
    for lead in leads:
        rank = STAGE_RANK.get(lead["status"])
        if rank is None:  # LOST or a custom stage
            rank = 1 if (lead["total_touchpoints"] or 0) > 0 else 0
        for stage in STAGE_ORDER[: rank + 1]:
            reached[stage] += 1

    steps = []
    for i in range(len(STAGE_ORDER) - 1):
        source, target = STAGE_ORDER[i], STAGE_ORDER[i + 1]
        base = reached[source] or 0
        steps.append({
            "from": source, "to": target,
            "from_count": base, "to_count": reached[target],
            "rate": round(reached[target] / base * 100, 1) if base else 0.0,
        })

    searched = scalar("SELECT COALESCE(SUM(results_found), 0) FROM searches")
    total_leads = scalar("SELECT COUNT(*) FROM leads")
    return {
        "reached": reached,
        "steps": steps,
        "search_to_lead": round(total_leads / searched * 100, 1) if searched else 0.0,
        "search_to_won": round(reached["WON"] / searched * 100, 2) if searched else 0.0,
        "results_seen": searched,
    }


@router.get("/stats/weekly")
def weekly_report():
    since = _days_ago(7)
    interactions = query(
        "SELECT type, COUNT(*) AS n FROM interactions WHERE date >= ? GROUP BY type", (since,)
    )
    return {
        "period_start": since[:10],
        "period_end": today(),
        "leads_found": scalar("SELECT COUNT(*) FROM leads WHERE created_at >= ?", (since,)),
        "hot_found": scalar(
            "SELECT COUNT(*) FROM leads WHERE created_at >= ? AND lead_priority = 'HOT'", (since,)
        ),
        "searches_run": scalar("SELECT COUNT(*) FROM searches WHERE started_at >= ?", (since,)),
        "outreach": {r["type"]: r["n"] for r in interactions},
        "total_touchpoints": sum(r["n"] for r in interactions),
        "meetings_set": scalar(
            "SELECT COUNT(*) FROM leads WHERE status = 'MEETING_SET' AND updated_at >= ?", (since,)
        ),
        "deals_won": scalar(
            "SELECT COUNT(*) FROM leads WHERE status = 'WON' AND updated_at >= ?", (since,)
        ),
        "by_niche": query(
            """SELECT niche,
                      COUNT(*) AS leads,
                      SUM(CASE WHEN status = 'WON' THEN 1 ELSE 0 END) AS won,
                      SUM(CASE WHEN total_touchpoints > 0 THEN 1 ELSE 0 END) AS contacted
               FROM leads WHERE niche IS NOT NULL GROUP BY niche ORDER BY leads DESC"""
        ),
        "by_city": query(
            """SELECT city,
                      COUNT(*) AS leads,
                      SUM(CASE WHEN status = 'WON' THEN 1 ELSE 0 END) AS won
               FROM leads WHERE city IS NOT NULL GROUP BY city ORDER BY leads DESC"""
        ),
        "cost_per_hot_lead": cost_per_hot_lead(),
    }


def cost_per_hot_lead() -> dict:
    """Part 9.7 - the one number that tells you if a change was really cheaper."""
    month_start = datetime.now(timezone.utc).replace(day=1).date().isoformat()
    calls = scalar(
        "SELECT COALESCE(SUM(calls),0) FROM api_usage WHERE service = 'google_places' "
        "AND day >= ?", (month_start,)
    )
    hot = scalar(
        "SELECT COUNT(*) FROM leads WHERE lead_priority = 'HOT' AND created_at >= ?",
        (month_start,)
    )
    return {
        "api_calls_this_month": calls,
        "hot_leads_this_month": hot,
        "calls_per_hot_lead": round(calls / hot, 1) if hot else None,
        "note": "Multiply calls by your per-1,000 SKU rate for a money figure - "
                "the tier depends on which fields each call requested.",
    }


@router.get("/follow-ups/today")
def follow_ups_today():
    return query(
        """SELECT id, business_name, city, phone, email, whatsapp, status, lead_score,
                  lead_priority, last_contacted_date, follow_up_date, total_touchpoints
           FROM leads
           WHERE follow_up_date IS NOT NULL AND follow_up_date <= ?
             AND status NOT IN ('WON','LOST')
           ORDER BY lead_score DESC""",
        (today(),),
    )


@router.get("/follow-ups/overdue")
def follow_ups_overdue():
    return query(
        """SELECT id, business_name, city, phone, email, status, lead_score, lead_priority,
                  last_contacted_date, follow_up_date
           FROM leads
           WHERE follow_up_date IS NOT NULL AND follow_up_date < ?
             AND status NOT IN ('WON','LOST')
           ORDER BY follow_up_date ASC""",
        (today(),),
    )


@router.get("/follow-ups/upcoming")
def follow_ups_upcoming(days: int = 7):
    horizon = (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()
    return query(
        """SELECT id, business_name, city, phone, email, status, lead_score, lead_priority,
                  follow_up_date
           FROM leads
           WHERE follow_up_date > ? AND follow_up_date <= ? AND status NOT IN ('WON','LOST')
           ORDER BY follow_up_date ASC""",
        (today(), horizon),
    )


@router.get("/follow-ups/suggested")
def follow_ups_suggested():
    """Leads the Day 3 / 7 / 14 cadence says are due, even without an explicit
    follow-up date set (Part 2.2)."""
    rows = query(
        """SELECT id, business_name, city, phone, email, whatsapp, status, lead_score,
                  lead_priority, last_contacted_date, total_touchpoints
           FROM leads
           WHERE status = 'CONTACTED' AND last_contacted_date IS NOT NULL
           ORDER BY last_contacted_date ASC"""
    )
    now = datetime.now(timezone.utc)
    out = []
    for row in rows:
        try:
            last = datetime.fromisoformat(row["last_contacted_date"])
        except (ValueError, TypeError):
            continue
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        days = (now - last).days
        step = None
        for threshold, label, action in (
            (21, "Day 21", "Mark Lost - No Response"),
            (14, "Day 14", "Final follow-up email"),
            (7, "Day 7", "Call them"),
            (3, "Day 3", "Follow up on WhatsApp"),
        ):
            if days >= threshold:
                step, suggested = label, action
                break
        if step:
            out.append({**row, "days_since_contact": days, "step": step, "action": suggested})
    return out
