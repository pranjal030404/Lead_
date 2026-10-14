from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from ..db import current_ws, lead_scope, query, query_one, scalar, search_scope, today
from ..quota import usage_summary

router = APIRouter(prefix="/api", tags=["stats"])

STAGE_ORDER = ["NEW", "CONTACTED", "REPLIED", "MEETING_SET", "PROPOSAL_SENT",
               "NEGOTIATION", "WON"]
STAGE_RANK = {name: i for i, name in enumerate(STAGE_ORDER)}


def _days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


@router.get("/stats")
def dashboard_stats():
    ws = current_ws()
    scope, sp = lead_scope()

    def count(extra: str = "", extra_params: tuple = ()) -> int:
        return scalar(f"SELECT COUNT(*) FROM leads WHERE {scope}{extra}",
                      (*sp, *extra_params))

    total = count()
    hot = count(" AND lead_priority = 'HOT'")
    warm = count(" AND lead_priority = 'WARM'")
    # Fall back to the listed URL when the site hasn't been checked yet -
    # otherwise every un-enriched lead is miscounted as having no website.
    no_site = count(
        " AND COALESCE(has_website, CASE WHEN COALESCE(website_url,'') = '' THEN 0 ELSE 1 END) = 0")
    contacted = count(" AND total_touchpoints > 0")
    won = count(" AND status = 'WON'")
    won_this_month = count(" AND status = 'WON' AND updated_at >= ?",
                           (datetime.now(timezone.utc).replace(day=1).isoformat(timespec="seconds"),))
    due_today = count(" AND follow_up_date IS NOT NULL AND follow_up_date <= ? "
                      "AND status NOT IN ('WON','LOST')", (today(),))
    lscope, lp = lead_scope("l")
    pending_approval = scalar(
        "SELECT COUNT(*) FROM approval_queue q JOIN leads l ON l.id = q.lead_id "
        f"WHERE {lscope} AND q.status = 'PENDING'", tuple(lp))
    new_this_week = count(" AND created_at >= ?", (_days_ago(7),))
    pending_enrichment = count(" AND enrichment_status IN ('pending','partial')")
    unresolved_failures = scalar(
        "SELECT COUNT(*) FROM failed_jobs WHERE resolved = 0 AND workspace_id = ?", (ws,))

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
            f"SELECT lead_priority AS priority, COUNT(*) AS n FROM leads "
            f"WHERE {scope} GROUP BY lead_priority", tuple(sp)
        ),
        "by_status": query(f"SELECT status, COUNT(*) AS n FROM leads WHERE {scope} "
                           f"GROUP BY status", tuple(sp)),
        "by_niche": query(
            f"SELECT niche, COUNT(*) AS n, SUM(CASE WHEN lead_priority='HOT' THEN 1 ELSE 0 END) "
            f"AS hot FROM leads WHERE {scope} AND niche IS NOT NULL "
            f"GROUP BY niche ORDER BY n DESC LIMIT 12", tuple(sp)
        ),
        "by_city": query(
            f"SELECT city, COUNT(*) AS n, SUM(CASE WHEN lead_priority='HOT' THEN 1 ELSE 0 END) "
            f"AS hot FROM leads WHERE {scope} AND city IS NOT NULL "
            f"GROUP BY city ORDER BY n DESC LIMIT 12", tuple(sp)
        ),
        "quota": usage_summary(),
    }


@router.get("/pipeline")
def pipeline():
    stages = query("SELECT * FROM pipeline_stages ORDER BY position")
    scope, sp = lead_scope()
    counts = {r["status"]: r["n"] for r in
              query(f"SELECT status, COUNT(*) AS n FROM leads WHERE {scope} "
                    f"GROUP BY status", tuple(sp))}
    result = []
    for stage in stages:
        leads = query(
            f"SELECT id, business_name, city, lead_score, lead_priority, phone, email, "
            f"last_contacted_date, follow_up_date FROM leads WHERE {scope} "
            f"AND status = ? ORDER BY lead_score DESC LIMIT 25",
            (*sp, stage["name"]),
        )
        result.append({**stage, "count": counts.get(stage["name"], 0), "leads": leads})
    return result


@router.get("/stats/conversions")
def conversions():
    """Funnel rates. A lead's current status implies it passed every earlier
    stage; LOST leads count as contacted if they have any touchpoint logged."""
    scope, sp = lead_scope()
    leads = query(f"SELECT status, total_touchpoints FROM leads WHERE {scope}", tuple(sp))
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

    sscope, ssp = search_scope()
    searched = scalar(f"SELECT COALESCE(SUM(results_found), 0) FROM searches "
                      f"WHERE {sscope}", tuple(ssp))
    total_leads = scalar(f"SELECT COUNT(*) FROM leads WHERE {scope}", tuple(sp))
    return {
        "reached": reached,
        "steps": steps,
        "search_to_lead": round(total_leads / searched * 100, 1) if searched else 0.0,
        "search_to_won": round(reached["WON"] / searched * 100, 2) if searched else 0.0,
        "results_seen": searched,
    }


@router.get("/stats/weekly")
def weekly_report():
    ws = current_ws()
    scope, sp = lead_scope()
    sscope, ssp = search_scope()
    since = _days_ago(7)
    interactions = query(
        "SELECT type, COUNT(*) AS n FROM interactions WHERE workspace_id = ? AND date >= ? "
        "GROUP BY type", (ws, since)
    )
    return {
        "period_start": since[:10],
        "period_end": today(),
        "leads_found": scalar(f"SELECT COUNT(*) FROM leads WHERE {scope} AND created_at >= ?",
                              (*sp, since)),
        "hot_found": scalar(
            f"SELECT COUNT(*) FROM leads WHERE {scope} AND created_at >= ? "
            f"AND lead_priority = 'HOT'", (*sp, since)
        ),
        "searches_run": scalar(f"SELECT COUNT(*) FROM searches WHERE {sscope} "
                               f"AND started_at >= ?", (*ssp, since)),
        "outreach": {r["type"]: r["n"] for r in interactions},
        "total_touchpoints": sum(r["n"] for r in interactions),
        "meetings_set": scalar(
            f"SELECT COUNT(*) FROM leads WHERE {scope} AND status = 'MEETING_SET' "
            f"AND updated_at >= ?", (*sp, since)
        ),
        "deals_won": scalar(
            f"SELECT COUNT(*) FROM leads WHERE {scope} AND status = 'WON' "
            f"AND updated_at >= ?", (*sp, since)
        ),
        "by_niche": query(
            f"""SELECT niche,
                      COUNT(*) AS leads,
                      SUM(CASE WHEN status = 'WON' THEN 1 ELSE 0 END) AS won,
                      SUM(CASE WHEN total_touchpoints > 0 THEN 1 ELSE 0 END) AS contacted
               FROM leads WHERE {scope} AND niche IS NOT NULL
               GROUP BY niche ORDER BY leads DESC""", tuple(sp)
        ),
        "by_city": query(
            f"""SELECT city,
                      COUNT(*) AS leads,
                      SUM(CASE WHEN status = 'WON' THEN 1 ELSE 0 END) AS won
               FROM leads WHERE {scope} AND city IS NOT NULL
               GROUP BY city ORDER BY leads DESC""", tuple(sp)
        ),
        "cost_per_hot_lead": cost_per_hot_lead(),
    }


def cost_per_hot_lead() -> dict:
    """Part 9.7 - the one number that tells you if a change was really cheaper."""
    month_start = datetime.now(timezone.utc).replace(day=1).date().isoformat()
    scope, sp = lead_scope()
    calls = scalar(
        "SELECT COALESCE(SUM(calls),0) FROM api_usage WHERE service = 'google_places' "
        "AND day >= ?", (month_start,)
    )
    hot = scalar(
        f"SELECT COUNT(*) FROM leads WHERE {scope} AND lead_priority = 'HOT' "
        f"AND created_at >= ?",
        (*sp, month_start)
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
    scope, sp = lead_scope()
    return query(
        f"""SELECT id, business_name, city, phone, email, whatsapp, status, lead_score,
                  lead_priority, last_contacted_date, follow_up_date, total_touchpoints
           FROM leads
           WHERE {scope} AND follow_up_date IS NOT NULL AND follow_up_date <= ?
             AND status NOT IN ('WON','LOST')
           ORDER BY lead_score DESC""",
        (*sp, today()),
    )


@router.get("/follow-ups/overdue")
def follow_ups_overdue():
    scope, sp = lead_scope()
    return query(
        f"""SELECT id, business_name, city, phone, email, status, lead_score, lead_priority,
                  last_contacted_date, follow_up_date
           FROM leads
           WHERE {scope} AND follow_up_date IS NOT NULL AND follow_up_date < ?
             AND status NOT IN ('WON','LOST')
           ORDER BY follow_up_date ASC""",
        (*sp, today()),
    )


@router.get("/follow-ups/upcoming")
def follow_ups_upcoming(days: int = 7):
    horizon = (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()
    scope, sp = lead_scope()
    return query(
        f"""SELECT id, business_name, city, phone, email, status, lead_score, lead_priority,
                  follow_up_date
           FROM leads
           WHERE {scope} AND follow_up_date > ? AND follow_up_date <= ? AND status NOT IN ('WON','LOST')
           ORDER BY follow_up_date ASC""",
        (*sp, today(), horizon),
    )


@router.get("/follow-ups/suggested")
def follow_ups_suggested():
    """Leads the Day 3 / 7 / 14 cadence says are due, even without an explicit
    follow-up date set (Part 2.2)."""
    scope, sp = lead_scope()
    rows = query(
        f"""SELECT id, business_name, city, phone, email, whatsapp, status, lead_score,
                  lead_priority, last_contacted_date, total_touchpoints
           FROM leads
           WHERE {scope} AND status = 'CONTACTED' AND last_contacted_date IS NOT NULL
           ORDER BY last_contacted_date ASC""",
        tuple(sp),
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
