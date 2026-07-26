"""Lead scoring (Part 8.4).

Deterministic weighted rules, not a model - every score comes with the list of
rules that fired, so you can always explain why a lead is HOT.
"""

from __future__ import annotations

import json

from .validation import data_quality_score


def score_lead(lead: dict) -> dict:
    """Return {lead_score, lead_priority, data_quality_score, score_reasons}."""
    quality = data_quality_score(lead)

    status = (lead.get("business_status") or "OPERATIONAL").upper()
    if status in ("CLOSED_PERMANENTLY", "PERMANENTLY_CLOSED"):
        return {
            "lead_score": 0,
            "lead_priority": "EXCLUDED",
            "data_quality_score": quality,
            "score_reasons": json.dumps(["permanently closed - excluded"]),
        }

    score = 0
    reasons: list[str] = []

    has_website = lead.get("has_website")
    website_score = lead.get("website_score")
    has_social = bool(lead.get("instagram_url") or lead.get("facebook_url"))
    listed_url = lead.get("website_url")
    dead_website = bool(listed_url) and has_website == 0
    no_website = not listed_url and (has_website == 0 or has_website is None)
    reviews = lead.get("google_reviews")

    # "Is this a real business worth pitching?" Google reviews answer that
    # directly, but OpenStreetMap carries no review data at all - so when review
    # count is unknown, fall back to whether the listing is actually contactable.
    # Without this, HOT would be permanently unreachable on the free provider.
    if reviews is not None:
        established = reviews >= 20
        established_reason = f"{reviews} reviews - established business (+1)"
    else:
        established = bool(lead.get("phone_valid")) and bool(lead.get("address"))
        established_reason = "listed with a working phone and address - real business (+1)"

    if no_website:
        score += 4
        reasons.append("no website at all (+4)")
    elif dead_website:
        score += 4
        reasons.append("has a website listed but it doesn't load (+4)")
    elif website_score is not None and website_score < 4:
        score += 3
        reasons.append(f"website scores {website_score}/10 - outdated or broken (+3)")
    elif website_score is not None and website_score < 7:
        score += 1
        reasons.append(f"website scores {website_score}/10 - room to improve (+1)")

    if has_social and not listed_url:
        score += 2
        reasons.append("active on social but no website (+2)")

    if established:
        score += 1
        reasons.append(established_reason)

    # The ideal prospect for a web studio: proven real business, no working web
    # presence. Without this combination bonus that lead tops out at 6 and never
    # reaches the HOT band - and it can't earn the social bonus either, because
    # social discovery reads a business's own website, which is exactly what
    # this lead doesn't have.
    if (no_website or dead_website) and established:
        score += 2
        reasons.append("real business with no working website - ideal prospect (+2)")

    if status in ("CLOSED_TEMPORARILY", "TEMPORARILY_CLOSED"):
        score -= 2
        reasons.append("temporarily closed (-2)")

    if lead.get("phone_valid"):
        score += 1
        reasons.append("reachable phone number (+1)")

    score = max(0, min(score, 10))

    if quality < 3 and score > 5:
        score = 5
        reasons.append("capped at 5 - data quality below 3, not enough to act on")

    if score >= 8:
        priority = "HOT"
    elif score >= 5:
        priority = "WARM"
    else:
        priority = "COLD"

    return {
        "lead_score": score,
        "lead_priority": priority,
        "data_quality_score": quality,
        "score_reasons": json.dumps(reasons),
    }


def observation_for(lead: dict) -> str:
    """One human sentence about why this business needs you - used in templates."""
    issues = lead.get("website_issues")
    if isinstance(issues, str) and issues:
        try:
            issues = json.loads(issues)
        except (ValueError, TypeError):
            issues = []
    issues = issues or []

    if not lead.get("website_url"):
        if lead.get("instagram_url") or lead.get("facebook_url"):
            return "you're active on social but don't have a website yet"
        return "you don't have a website listed on your business profile"
    if issues:
        # Lowercase only the first letter, so "Has no HTTPS" reads correctly but
        # "Doesn't load at all" keeps its capitals intact.
        issue = issues[0][0].lower() + issues[0][1:]
        return f"your site {issue}"
    if lead.get("website_last_updated"):
        return f"your site looks like it was last updated in {lead['website_last_updated']}"
    return "a few things on your site that could be bringing in more enquiries"


def website_issue_for(lead: dict) -> str:
    issues = lead.get("website_issues")
    if isinstance(issues, str) and issues:
        try:
            issues = json.loads(issues)
        except (ValueError, TypeError):
            issues = []
    if issues:
        return issues[0]
    if not lead.get("website_url"):
        return "There's no website to send customers to"
    return "Page speed - it's slower than most visitors will wait for"
