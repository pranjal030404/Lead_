"""Buying-intent signals.

A signal is a *detected, evidence-backed* reason a business may be ready to buy.
Only signals derivable from data this system actually holds are implemented:

  WEBSITE_BROKEN, WEBSITE_OUTDATED, WEBSITE_CHANGED (content fingerprint differs
  between audits), RUNNING_ADS (ad/pixel tags detected), NO_BOOKING_SYSTEM,
  NO_ECOMMERCE, NO_ONLINE_ORDERING (niche-gated), STRONG_REPUTATION_POOR_SITE,
  COMPETITOR_ADVANTAGE.

NOT implemented - they need external feeds this installation does not have, and
are never faked: new/recently-opened business, hiring a developer or marketer,
new service/branch, social activity, review-velocity growth (needs a rating
refresh history). They appear in `UNAVAILABLE_SIGNALS` so the API can say so.

Event-like signals decay linearly to zero over their TTL; state-like signals
(a site that is down) persist while the state holds. The intent score combines
active signals as 100 * (1 - prod(1 - strength * decay * confidence)): many weak
signals add up, a single strong one still counts, and it can never exceed 100.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from . import competitors, opportunities
from .db import current_ws, execute, query, query_one, utcnow

# type: (base strength, ttl_days or None for state-like, label)
SIGNAL_DEFS = {
    "WEBSITE_BROKEN": (0.90, None, "Website is broken"),
    "WEBSITE_OUTDATED": (0.60, None, "Website looks outdated"),
    "WEBSITE_CHANGED": (0.50, 45, "Website content recently changed"),
    "RUNNING_ADS": (0.70, None, "Advertising tags detected"),
    "NO_BOOKING_SYSTEM": (0.50, None, "No online booking"),
    "NO_ECOMMERCE": (0.40, None, "No online store"),
    "NO_ONLINE_ORDERING": (0.50, None, "No online ordering"),
    "STRONG_REPUTATION_POOR_SITE": (0.70, None, "Strong reputation, weak website"),
    "COMPETITOR_ADVANTAGE": (0.70, None, "Competitors have a clearly better web presence"),
}
UNAVAILABLE_SIGNALS = {
    "NEW_BUSINESS": "needs a business-registry or listing-age feed",
    "RECENTLY_OPENED_LOCATION": "needs a listing-age feed",
    "HIRING_DEVELOPER": "needs a job-board integration",
    "HIRING_MARKETER": "needs a job-board integration",
    "HIGH_SOCIAL_ACTIVITY": "needs official social-platform APIs",
    "RAPID_REVIEW_GROWTH": "needs periodic rating refreshes (history) from a provider",
    "NEW_SERVICE_OR_BRANCH": "needs a content-change feed",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(v):
    try:
        d = datetime.fromisoformat(v)
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _recent_ok_audits(lead_id: int, n: int = 2) -> list[dict]:
    rows = query("SELECT id, created_at, findings, overall_score, status FROM website_audits "
                 "WHERE workspace_id = ? AND lead_id = ? AND status = 'OK' ORDER BY id DESC LIMIT ?",
                 (current_ws(), lead_id, n))
    for r in rows:
        r["findings"] = json.loads(r["findings"]) if r["findings"] else {}
    return rows


def detect(lead_id: int) -> dict[str, dict]:
    """-> {type: {confidence, evidence, detected_at?}} for currently-true signals."""
    lead = query_one("SELECT * FROM leads WHERE id = ? AND workspace_id = ?",
                     (lead_id, current_ws()))
    if not lead:
        raise LookupError("lead not found")
    found: dict[str, dict] = {}
    latest = query_one("SELECT * FROM website_audits WHERE workspace_id = ? AND lead_id = ? "
                       "ORDER BY id DESC LIMIT 1", (current_ws(), lead_id))
    ok = _recent_ok_audits(lead_id)
    open_ops = {o["opportunity_type"]: o for o in opportunities.list_for_lead(lead_id, True)
                if o["status"] == "OPEN"}

    # --- state: broken site -------------------------------------------------
    if latest and latest["status"] in ("UNREACHABLE", "ERROR_PAGE"):
        found["WEBSITE_BROKEN"] = {"confidence": 0.85, "evidence": [
            f"Audit on {latest['created_at'][:10]}: {latest.get('error') or 'site did not load'}."]}
    elif lead.get("has_website") == 0 and lead.get("website_url") and lead.get("website_checked_at"):
        found["WEBSITE_BROKEN"] = {"confidence": 0.7, "evidence": [
            f"Website check on {lead['website_checked_at'][:10]} could not load the site."]}

    # --- state: outdated ------------------------------------------------------
    if ok:
        fd = ok[0]["findings"]
        checks = {c["id"]: c for c in fd.get("checks", [])}
        reasons = []
        cy = checks.get("copyright_year")
        if cy and cy["status"] == "FAIL":
            reasons.append(cy["detail"])
        builders = [t["name"] for t in fd.get("technologies", []) if t["category"] == "builder"
                    and t["name"] in opportunities.TEMPLATE_BUILDERS]
        if builders and (ok[0]["overall_score"] or 100) < 70:
            reasons.append(f"Built on {builders[0]} with an audit score of {ok[0]['overall_score']}.")
        if checks.get("mobile_viewport", {}).get("status") == "FAIL":
            reasons.append(checks["mobile_viewport"]["detail"])
        if reasons:
            found["WEBSITE_OUTDATED"] = {"confidence": 0.75, "evidence": reasons}

        # --- running ads ------------------------------------------------------
        ads = [t for t in fd.get("technologies", []) if t["category"] == "advertising"]
        if ads:
            found["RUNNING_ADS"] = {"confidence": 0.7, "evidence": [
                f"{t['name']} detected on the site ({t['evidence']})." for t in ads]
                + ["A pixel/tag shows ad tracking is installed, not that a campaign is live."]}

        # --- niche-gated feature gaps ----------------------------------------
        for sig, opp in (("NO_BOOKING_SYSTEM", "BOOKING_SYSTEM"),
                         ("NO_ECOMMERCE", "ECOMMERCE"),
                         ("NO_ONLINE_ORDERING", "ONLINE_ORDERING")):
            if opp in open_ops:
                found[sig] = {"confidence": open_ops[opp]["confidence"],
                              "evidence": open_ops[opp]["evidence"]}

    # --- event: website changed ----------------------------------------------
    if len(ok) == 2:
        new, old = ok[0], ok[1]
        fn, fo = new["findings"].get("fingerprint"), old["findings"].get("fingerprint")
        if fn and fo and fn != fo:
            ev = [f"Page content differs between audits on {old['created_at'][:10]} and "
                  f"{new['created_at'][:10]}."]
            tn, to = new["findings"].get("title"), old["findings"].get("title")
            if tn and to and tn != to:
                ev.append(f"Title changed from \"{to}\" to \"{tn}\".")
            found["WEBSITE_CHANGED"] = {"confidence": 0.7, "evidence": ev,
                                        "detected_at": new["created_at"]}

    # --- reputation vs site ---------------------------------------------------
    rating, reviews = lead.get("google_rating"), lead.get("google_reviews")
    if rating and reviews and rating >= 4.2 and reviews >= 30:
        weak = None
        if not (lead.get("website_url") or "").strip():
            weak = "has no website listed"
        elif ok and (ok[0]["overall_score"] or 100) < 60:
            weak = f"has a website audit score of {ok[0]['overall_score']}/100"
        if weak:
            found["STRONG_REPUTATION_POOR_SITE"] = {
                "confidence": 0.8 if ok else 0.6,
                "evidence": [f"{rating} stars from {reviews} public reviews, but the business {weak}."]}

    # --- competitor advantage ---------------------------------------------------
    try:
        gap = competitors.competitive_gap(lead_id)
    except LookupError:
        gap = None
    if gap and gap["sufficient_data"] and gap["gap"]["deficit_vs_median"] >= 15:
        found["COMPETITOR_ADVANTAGE"] = {"confidence": 0.8, "evidence": gap["statements"]}
    return found


def effective_strength(sig: dict, now: datetime | None = None) -> float:
    base, ttl, _ = SIGNAL_DEFS[sig["signal_type"]]
    if ttl is None:
        return base
    age = ((now or _now()) - (_parse(sig["detected_at"]) or _now())).total_seconds() / 86400
    return base * max(0.0, 1 - age / ttl)


def intent(signals: list[dict]) -> tuple[int, float | None]:
    """-> (intent score 0-100, mean confidence of contributing signals or None)."""
    now = _now()
    miss, confs = 1.0, []
    for s in signals:
        if s["status"] != "ACTIVE":
            continue
        eff = effective_strength(s, now)
        if eff <= 0:
            continue
        miss *= 1 - eff * s["confidence"]
        confs.append(s["confidence"])
    return round(100 * (1 - miss)), (sum(confs) / len(confs) if confs else None)


def refresh_for_lead(lead_id: int) -> list[dict]:
    """Re-detect and reconcile: new -> ACTIVE; still true -> updated (event
    signals restart only on a *new* event); no longer true -> INACTIVE."""
    found = detect(lead_id)
    existing = {r["signal_type"]: r for r in query(
        "SELECT * FROM lead_signals WHERE workspace_id = ? AND lead_id = ?",
        (current_ws(), lead_id))}
    now = utcnow()
    for t, sig in found.items():
        strength, ttl, _ = SIGNAL_DEFS[t]
        old = existing.get(t)
        if sig.get("detected_at"):                      # event with its own timestamp
            detected = sig["detected_at"]
        elif old and old["status"] == "ACTIVE":
            detected = old["detected_at"]               # state signal: keep first-seen
        else:
            detected = now
        expires = ((_parse(detected) + timedelta(days=ttl)).isoformat(timespec="seconds")
                   if ttl else None)
        status = "ACTIVE"
        if ttl and expires and expires < now:
            status = "INACTIVE"                         # the event is too old to matter
        execute(
            """INSERT INTO lead_signals
               (workspace_id, lead_id, signal_type, strength, confidence, evidence, source,
                status, detected_at, expires_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON DUPLICATE KEY UPDATE strength = VALUES(strength),
                 confidence = VALUES(confidence), evidence = VALUES(evidence),
                 status = VALUES(status), detected_at = VALUES(detected_at),
                 expires_at = VALUES(expires_at), updated_at = VALUES(updated_at)""",
            (current_ws(), lead_id, t, strength, sig["confidence"], json.dumps(sig["evidence"]),
             "derived", status, detected, expires, now))
    stale = [t for t in existing if t not in found and existing[t]["status"] == "ACTIVE"]
    for t in stale:
        execute("UPDATE lead_signals SET status = 'INACTIVE', updated_at = ? WHERE "
                "workspace_id = ? AND lead_id = ? AND signal_type = ?",
                (now, current_ws(), lead_id, t))
    return list_for_lead(lead_id)


def list_for_lead(lead_id: int) -> list[dict]:
    rows = query("SELECT * FROM lead_signals WHERE workspace_id = ? AND lead_id = ? "
                 "ORDER BY status = 'ACTIVE' DESC, strength DESC", (current_ws(), lead_id))
    now = _now()
    for r in rows:
        try:
            r["evidence"] = json.loads(r["evidence"]) if r["evidence"] else []
        except ValueError:
            r["evidence"] = []
        r["label"] = SIGNAL_DEFS[r["signal_type"]][2]
        r["effective_strength"] = round(effective_strength(r, now), 3) if r["status"] == "ACTIVE" else 0.0
    return rows
