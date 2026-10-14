"""Competitor intelligence.

Competitors are *other businesses already in this workspace's lead database*
(collected through the configured providers): same niche, in the same city or
within COMPETITOR_RADIUS_KM. Nothing is scraped to find them, and one workspace
never sees another's leads. A user can also pin a specific competitor.

The output is comparative *evidence*, not a verdict: audit scores, ratings and
review counts side by side, and a factual gap statement - only when at least
MIN_COMPARABLE competitors have a completed audit. With fewer, the result says
"insufficient data" and how many could be audited on demand.
"""

from __future__ import annotations

import math
import statistics

from .db import current_ws, execute, query, query_one, utcnow

COMPETITOR_RADIUS_KM = 15.0
MIN_COMPARABLE = 2
MAX_CANDIDATES = 8


def haversine_km(lat1, lng1, lat2, lng2) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


_SELECT = """
    SELECT l.id, l.business_name, l.city, l.niche, l.lat, l.lng, l.website_url,
           l.google_rating, l.google_reviews, l.lead_score, l.website_audit_score,
           l.instagram_url, l.facebook_url, l.linkedin_url,
           a.overall_score AS audit_score, a.findings
    FROM leads l
    LEFT JOIN website_audits a ON a.id = (
        SELECT MAX(id) FROM website_audits WHERE lead_id = l.id AND status = 'OK')
"""


def _features(findings_json: str | None) -> dict:
    if not findings_json:
        return {}
    import json
    try:
        return (json.loads(findings_json) or {}).get("features", {})
    except ValueError:
        return {}


def _shape(row: dict, origin: dict, source: str) -> dict:
    dist = None
    if None not in (origin.get("lat"), origin.get("lng"), row.get("lat"), row.get("lng")):
        dist = round(haversine_km(origin["lat"], origin["lng"], row["lat"], row["lng"]), 1)
    feats = _features(row.pop("findings", None))
    return {
        "lead_id": row["id"], "business_name": row["business_name"], "source": source,
        "distance_km": dist, "has_website": bool((row.get("website_url") or "").strip()),
        "audit_score": row.get("audit_score"), "rating": row.get("google_rating"),
        "reviews": row.get("google_reviews"),
        "social_profiles": sum(1 for c in ("instagram_url", "facebook_url", "linkedin_url")
                               if row.get(c)),
        "features": {k: feats[k] for k in ("booking", "ecommerce", "online_ordering",
                                          "whatsapp_cta", "lead_capture") if k in feats},
    }


def candidates(lead_id: int, limit: int = MAX_CANDIDATES) -> tuple[dict | None, list[dict]]:
    """-> (target lead row, competitor list). Pinned competitors first, then
    same-niche leads in the same city or within radius, nearest first."""
    ws = current_ws()
    target = query_one(_SELECT + " WHERE l.id = ? AND l.workspace_id = ?", (lead_id, ws))
    if not target:
        return None, []
    out, seen = [], {lead_id}
    pinned = query(_SELECT + " JOIN lead_competitors c ON c.competitor_lead_id = l.id "
                   "WHERE c.workspace_id = ? AND c.lead_id = ? AND l.workspace_id = ?",
                   (ws, lead_id, ws))
    for row in pinned:
        seen.add(row["id"])
        out.append(_shape(row, target, "pinned"))
    niche = (target.get("niche") or "").strip().lower()
    if niche:
        rows = query(_SELECT + " WHERE l.workspace_id = ? AND LOWER(l.niche) = ? AND l.id != ? "
                     "LIMIT 500", (ws, niche, lead_id))
        auto = []
        for row in rows:
            if row["id"] in seen:
                continue
            shaped = _shape(row, target, "auto")
            same_city = (row.get("city") or "").lower() == (target.get("city") or "").lower()
            near = shaped["distance_km"] is not None and shaped["distance_km"] <= COMPETITOR_RADIUS_KM
            if same_city or near:
                auto.append(shaped)
        auto.sort(key=lambda c: (c["distance_km"] is None, c["distance_km"] or 0,
                                 -(c["audit_score"] or -1)))
        out += auto
    return target, out[:limit]


def competitive_gap(lead_id: int) -> dict:
    target, comps = candidates(lead_id)
    if target is None:
        raise LookupError("lead not found")
    t_score = target.get("audit_score")
    has_site = bool((target.get("website_url") or "").strip())
    scored = [c for c in comps if c["audit_score"] is not None]
    unaudited = [c["lead_id"] for c in comps if c["has_website"] and c["audit_score"] is None]
    result = {
        "lead_id": lead_id, "business_name": target["business_name"],
        "target": {"audit_score": t_score, "has_website": has_site,
                   "rating": target.get("google_rating"), "reviews": target.get("google_reviews")},
        "competitors": comps, "comparable": len(scored), "unaudited_competitor_ids": unaudited,
        "gap": None, "statements": [], "sufficient_data": False,
    }
    if len(scored) < MIN_COMPARABLE:
        result["statements"].append(
            f"Insufficient data: {len(scored)} of {len(comps)} nearby competitors have a "
            f"completed website audit (need {MIN_COMPARABLE})."
            + (f" {len(unaudited)} could be audited on demand." if unaudited else ""))
        return result
    if t_score is None and has_site:
        result["statements"].append("The target's own website has not been audited yet.")
        return result
    effective = 0 if not has_site else t_score
    scores = sorted((c["audit_score"] for c in scored), reverse=True)
    median, best = statistics.median(scores), scores[0]
    rank = 1 + sum(1 for s in scores if s > effective)
    result["gap"] = {
        "target_score": effective, "best": best, "median": median, "rank": rank,
        "of": len(scores) + 1, "deficit_vs_best": best - effective,
        "deficit_vs_median": round(median - effective, 1),
    }
    result["sufficient_data"] = True
    named = ", ".join(f"{c['business_name']}: {c['audit_score']}" for c in
                      sorted(scored, key=lambda c: -c["audit_score"])[:3])
    result["statements"] = [
        (f"Website score {effective}" if has_site else "No website (scored 0)")
        + f"; nearby competitors: {named}.",
        f"Ranks {rank} of {len(scores) + 1} among audited local competitors "
        f"(median {median:g}, best {best}).",
    ]
    rated = [c for c in comps if c["rating"] and c["reviews"]]
    if target.get("google_rating") and target.get("google_reviews") and rated:
        avg_reviews = statistics.median(c["reviews"] for c in rated)
        result["statements"].append(
            f"Reviews: {target['google_reviews']} (rating {target['google_rating']}) vs a "
            f"competitor median of {avg_reviews:g} reviews.")
    return result


def pin(lead_id: int, competitor_id: int) -> bool:
    ws = current_ws()
    if lead_id == competitor_id or not query_one(
            "SELECT 1 AS x FROM leads WHERE id = ? AND workspace_id = ?", (competitor_id, ws)):
        return False
    execute("INSERT IGNORE INTO lead_competitors(workspace_id, lead_id, competitor_lead_id, "
            "created_at) VALUES(?,?,?,?)", (ws, lead_id, competitor_id, utcnow()))
    return True


def unpin(lead_id: int, competitor_id: int) -> bool:
    return bool(execute("DELETE FROM lead_competitors WHERE workspace_id = ? AND lead_id = ? "
                        "AND competitor_lead_id = ?", (current_ws(), lead_id, competitor_id)))
