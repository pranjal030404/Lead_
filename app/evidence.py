"""Evidence-confidence grading - "how sure are we this lead is real and reachable?"

Separate from the opportunity score (how *good* a prospect is). A flashy lead
built on thin or stale data should not look as trustworthy as a modest lead
backed by verified facts. Five dimensions, each 0-5, weighted:

  pain        25%  evidence the business has a problem we solve (site gap, signals)
  fit         25%  matches the target niche/geography, business is operating
  timing      20%  how fresh the evidence is
  reach       15%  a verified, non-blocked way to contact them
  provenance  15%  facts came from inspected sources, not guesses

Hard caps stop a high score hiding missing proof:
  * unknown evidence date          -> timing <= 2
  * no verified contact route      -> reach  <= 1
  * permanently closed             -> fit    == 0
  * only inferred (guessed) data   -> provenance <= 2

The result lists `gaps` - concrete missing evidence - so a user knows what to
check before outreach, instead of just seeing a number. Pure function: takes a
lead dict (plus optional extras), returns a dict. No DB access.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

WEIGHTS = {"pain": 0.25, "fit": 0.25, "timing": 0.20, "reach": 0.15, "provenance": 0.15}
GRADES = ((80, "A"), (60, "B"), (40, "C"), (0, "D"))
TRUSTED_SOURCES = {"google_places", "osm", "website", "website_scrape", "hunter",
                   "manual", "verified"}


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _age_days(value, now: datetime) -> int | None:
    dt = _parse_dt(value)
    return None if dt is None else max(0, (now - dt).days)


def _issues(lead: dict) -> list:
    raw = lead.get("website_issues")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = []
    return raw or []


def grade_evidence(lead: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    gaps: list[str] = []
    notes: dict[str, list[str]] = {k: [] for k in WEIGHTS}

    # ---- pain ---------------------------------------------------------------
    listed = (lead.get("website_url") or "").strip()
    wscore = lead.get("website_score")
    if not listed and lead.get("website_checked_at"):
        pain = 5; notes["pain"].append("checked: no website")
    elif not listed:
        pain = 3; notes["pain"].append("no website listed (not independently checked)")
        gaps.append("Confirm the business really has no website (search the name).")
    elif lead.get("has_website") == 0:
        pain = 5; notes["pain"].append("listed website does not load")
    elif wscore is not None:
        pain = 5 if wscore < 4 else 3 if wscore < 7 else 1
        notes["pain"].append(f"website audited at {wscore}/10")
    else:
        pain = 1; notes["pain"].append("website not analysed yet")
        gaps.append("Run enrichment to analyse the website.")
    if _issues(lead) and pain < 5:
        pain += 1; notes["pain"].append(f"{len(_issues(lead))} concrete issue(s) recorded")
    pain = min(pain, 5)

    # ---- fit ----------------------------------------------------------------
    status = (lead.get("business_status") or "OPERATIONAL").upper()
    fit = 3
    if status in ("CLOSED_PERMANENTLY", "PERMANENTLY_CLOSED"):
        fit = 0; notes["fit"].append("permanently closed")
    else:
        if lead.get("niche"):
            fit += 1; notes["fit"].append("niche known")
        else:
            gaps.append("Niche is missing - fit cannot be judged.")
        if lead.get("city") or (lead.get("lat") and lead.get("lng")):
            fit += 1; notes["fit"].append("location known")
        if status.endswith("TEMPORARILY"):
            fit -= 2; notes["fit"].append("temporarily closed")
    fit = max(0, min(fit, 5))

    # ---- timing -------------------------------------------------------------
    evidence_date = lead.get("website_checked_at") or lead.get("enriched_at") \
        or lead.get("last_enriched_at")
    age = _age_days(evidence_date, now)
    if age is None:
        timing = 2; notes["timing"].append("evidence date unknown (capped at 2)")
        gaps.append("No evidence date - re-run enrichment to timestamp it.")
    elif age <= 30:
        timing = 5; notes["timing"].append(f"checked {age}d ago")
    elif age <= 90:
        timing = 3; notes["timing"].append(f"checked {age}d ago")
    else:
        timing = 1; notes["timing"].append(f"checked {age}d ago - stale")
        gaps.append(f"Evidence is {age} days old - refresh before outreach.")

    # ---- reach --------------------------------------------------------------
    email_ok = bool(lead.get("email")) and lead.get("email_valid") not in (0, False)
    phone_ok = bool(lead.get("phone")) and bool(lead.get("phone_valid"))
    verified_route = (email_ok and lead.get("email_status") == "VERIFIED") or phone_ok
    reach = 0
    if phone_ok:
        reach += 3; notes["reach"].append("valid phone")
    if email_ok:
        reach += 2; notes["reach"].append("valid email")
    if lead.get("whatsapp"):
        reach += 1; notes["reach"].append("WhatsApp number")
    if not verified_route:
        reach = min(reach, 1); notes["reach"].append("no verified route (capped at 1)")
        gaps.append("No verified contact route - find a valid phone or verified email.")
    reach = min(reach, 5)

    # ---- provenance ---------------------------------------------------------
    source = (lead.get("source") or "").lower()
    prov = 3 if source in TRUSTED_SOURCES else 2
    if lead.get("place_id"):
        prov += 1; notes["provenance"].append("provider place id")
    if lead.get("website_checked_at"):
        prov += 1; notes["provenance"].append("website inspected directly")
    if lead.get("email_status") == "INFERRED":
        prov = min(prov, 2); notes["provenance"].append("email is inferred (capped at 2)")
        gaps.append("Email is a guess - verify before sending.")
    prov = min(prov, 5)

    dims = {"pain": pain, "fit": fit, "timing": timing, "reach": reach, "provenance": prov}
    total = round(sum(dims[k] / 5 * WEIGHTS[k] for k in WEIGHTS) * 100)
    if fit == 0:
        total = min(total, 20)
    grade = next(g for floor, g in GRADES if total >= floor)
    return {"evidence_score": total, "grade": grade, "dimensions": dims,
            "notes": notes, "gaps": gaps}
