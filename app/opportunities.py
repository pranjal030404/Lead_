"""Opportunity engine: findings -> things you can sell.

A technical problem is not yet a sales lead; this module translates audit
findings (and a few facts already on the lead) into opportunities, each with the
evidence behind it, the business impact, the recommended solution and an
estimated project value.

Honesty rules:
  * Evidence is copied from the audit's checks or the lead's own recorded data.
    Nothing is asserted that the data does not show.
  * `confidence` says how sure the *finding* is (0.9 for a measured audit fact,
    lower for "absent from a provider listing" or "tag not detected" claims that
    could be a data gap).
  * `business_impact` is qualitative and factual - no invented percentages.
  * `est_value_min/max` come from an editable price table (defaults below are
    starting points, not market data); each workspace can override them.
  * A site the SSRF guard blocked, or an audit that did not run, yields no
    audit-based opportunities - that is absence of evidence, not a finding.
"""

from __future__ import annotations

import json

from .db import current_ws, execute, get_ws_setting, query, query_one, set_ws_setting, utcnow

# Default project-value ranges, INR. Editable per workspace (see get_pricing).
DEFAULT_PRICING = {
    "NO_WEBSITE": (25000, 90000),
    "WEBSITE_UNREACHABLE": (25000, 90000),
    "WEBSITE_REDESIGN": (40000, 150000),
    "MOBILE_REDESIGN": (30000, 80000),
    "SECURITY_HTTPS": (3000, 10000),
    "PERFORMANCE_OPTIMIZATION": (10000, 35000),
    "SEO_OPTIMIZATION": (15000, 45000),
    "BOOKING_SYSTEM": (20000, 60000),
    "ONLINE_ORDERING": (25000, 80000),
    "ECOMMERCE": (60000, 200000),
    "WHATSAPP_CONVERSION": (4000, 15000),
    "LEAD_CAPTURE": (8000, 25000),
    "ANALYTICS_TRACKING": (4000, 12000),
    "REVIEWS_SHOWCASE": (5000, 18000),
    "CONTENT_REFRESH": (8000, 30000),
}

COPY = {
    "NO_WEBSITE": (
        "Customers who look this business up online find no website to learn more or get in "
        "touch from.",
        "A fast, mobile-friendly website with services, contact details, a map and a clear "
        "call/WhatsApp button."),
    "WEBSITE_UNREACHABLE": (
        "The website address listed for the business does not load, so anyone clicking it from "
        "a listing or search result hits a dead end.",
        "Restore or rebuild the site (or fix the domain/hosting), then redirect any old "
        "addresses."),
    "WEBSITE_REDESIGN": (
        "The site scores poorly across technical, SEO and conversion checks, so it is unlikely "
        "to be doing much work for the business.",
        "A redesign built on current standards: responsive layout, fast hosting, on-page SEO "
        "basics and visible calls to action."),
    "MOBILE_REDESIGN": (
        "The page has no mobile viewport setting, so phones show a shrunken desktop layout. "
        "Google indexes the mobile version of a site first.",
        "Make the site responsive: viewport meta tag, flexible layout and tap-sized controls."),
    "SECURITY_HTTPS": (
        "Browsers label sites without a valid HTTPS certificate as 'Not secure', which puts off "
        "visitors and weakens search trust signals.",
        "Install/renew a TLS certificate, redirect HTTP to HTTPS and fix any mixed content."),
    "PERFORMANCE_OPTIMIZATION": (
        "Slow pages lose visitors before they read anything, and page speed is a search "
        "ranking input.",
        "Optimise hosting/caching, compress and lazy-load images, defer render-blocking "
        "scripts and styles."),
    "SEO_OPTIMIZATION": (
        "Basic on-page SEO gaps make it harder for people searching for this kind of business "
        "in this area to find the site.",
        "Fix titles, meta descriptions, headings and schema markup; publish a sitemap; repair "
        "broken links."),
    "BOOKING_SYSTEM": (
        "Customers in this kind of business usually want to book; without online booking they "
        "have to call during opening hours.",
        "Add online appointment/table booking with confirmations and reminders."),
    "ONLINE_ORDERING": (
        "There is no way to order from the website, so every order depends on a phone call or "
        "a third-party app.",
        "Add direct online ordering (menu, cart, payments or pay-on-delivery)."),
    "ECOMMERCE": (
        "Products cannot be bought on the site; sales depend on visits and calls.",
        "Set up an online store with catalogue, cart, payments and order notifications."),
    "WHATSAPP_CONVERSION": (
        "Visitors cannot start a WhatsApp chat from the site, even though a WhatsApp-capable "
        "number is on record.",
        "Add a click-to-chat WhatsApp button with a pre-filled message."),
    "LEAD_CAPTURE": (
        "The site gives visitors no way to leave their details, so interested people who are "
        "not ready to call are lost.",
        "Add an enquiry form (and optional newsletter signup) wired to email/CRM."),
    "ANALYTICS_TRACKING": (
        "No analytics tag was detected, so the business cannot see where visitors come from or "
        "what they do.",
        "Install Google Analytics / Tag Manager with conversion events for calls and forms."),
    "REVIEWS_SHOWCASE": (
        "The business has strong public reviews, but the website does not show any social "
        "proof.",
        "Add a testimonials/reviews section with review schema markup."),
    "CONTENT_REFRESH": (
        "The content is thin or looks unmaintained, which weakens both trust and search "
        "relevance.",
        "Refresh copy for services and location, update the footer and add fresh content."),
}

BOOKING_NICHES = ("dentist", "dental", "clinic", "doctor", "salon", "spa", "barber", "gym",
                  "fitness", "yoga", "physio", "vet", "lawyer", "advocate", "consult",
                  "repair", "coaching", "tutor", "hotel", "resort", "photograph", "beauty",
                  "wellness", "therapy", "diagnostic")
ORDERING_NICHES = ("restaurant", "cafe", "café", "bakery", "caterer", "catering", "food",
                   "pizza", "sweet", "dhaba", "bistro", "kitchen", "juice", "ice cream")
ECOMMERCE_NICHES = ("store", "shop", "boutique", "retail", "jewel", "furniture", "garment",
                    "clothing", "apparel", "florist", "gift", "electronics", "footwear",
                    "cosmetic", "stationery", "toys", "handicraft")
TEMPLATE_BUILDERS = ("Wix", "GoDaddy Builder", "Weebly", "Squarespace")
SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def get_pricing() -> dict:
    """Defaults overlaid with the workspace's own price table ({type: [min,max]})."""
    pricing = {k: list(v) for k, v in DEFAULT_PRICING.items()}
    raw = get_ws_setting("opportunity_pricing")
    if raw:
        try:
            for k, v in json.loads(raw).items():
                if k in pricing and isinstance(v, list) and len(v) == 2 \
                        and all(isinstance(x, int) and x >= 0 for x in v) and v[0] <= v[1]:
                    pricing[k] = v
        except (ValueError, TypeError, AttributeError):
            pass
    return pricing


def set_pricing(table: dict) -> dict:
    clean = {}
    for k, v in table.items():
        if k not in DEFAULT_PRICING:
            raise ValueError(f"Unknown opportunity type: {k}")
        if not (isinstance(v, (list, tuple)) and len(v) == 2
                and all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in v)
                and v[0] <= v[1]):
            raise ValueError(f"{k}: expected [min, max] whole numbers with min <= max")
        clean[k] = list(v)
    set_ws_setting("opportunity_pricing", json.dumps(clean))
    return get_pricing()


# -------------------------------------------------------------------- rules --

def _niche_in(lead: dict, words: tuple) -> bool:
    n = (lead.get("niche") or "").lower()
    return any(w in n for w in words)


def detect(lead: dict, audit: dict | None) -> tuple[dict, set]:
    """-> ({type: opportunity}, evaluated_types).

    `evaluated_types` is what this call had enough evidence to judge; only those
    may be marked resolved when absent from the result."""
    found: dict[str, dict] = {}
    evaluated: set[str] = {"NO_WEBSITE"}
    f = lambda t, sev, conf, ev, audit_id=None: found.__setitem__(t, {
        "type": t, "severity": sev, "confidence": conf, "evidence": ev, "audit_id": audit_id})

    if not (lead.get("website_url") or "").strip():
        src = lead.get("source") or "the discovery provider"
        f("NO_WEBSITE", "HIGH", 0.6, [
            f"No website is listed for this business in {src} data.",
            "Provider listings can be incomplete - confirm before pitching."])
        return found, evaluated

    status = (audit or {}).get("status")
    if lead.get("has_website") == 0 and lead.get("website_checked_at") and status != "OK":
        # The basic website check (run on every lead) already found it dead.
        evaluated.add("WEBSITE_UNREACHABLE")
        issues = lead.get("website_issues")
        try:
            issues = json.loads(issues) if isinstance(issues, str) else (issues or [])
        except ValueError:
            issues = []
        f("WEBSITE_UNREACHABLE", "HIGH", 0.7,
          [f"The website check on {lead['website_checked_at'][:10]} found: "
           f"{(issues or ['the site did not load'])[0]}.",
           "Could be a temporary outage - re-check before pitching."])
        return found, evaluated
    if audit is None or status not in ("OK", "UNREACHABLE", "ERROR_PAGE"):
        return found, evaluated          # no usable audit: judge nothing from it
    audit_types = set(DEFAULT_PRICING) - {"NO_WEBSITE"}
    evaluated |= audit_types
    aid = audit["id"]
    if status == "UNREACHABLE":
        f("WEBSITE_UNREACHABLE", "HIGH", 0.8,
          [f"The audit could not load {audit['url']}: {audit.get('error') or 'no response'}.",
           "Retried before giving up; a temporary outage is possible."], aid)
        return found, evaluated
    if status == "ERROR_PAGE":
        f("WEBSITE_UNREACHABLE", "HIGH", 0.85,
          [f"{audit['url']} responds with an HTTP error instead of a page "
           f"({audit.get('error')})."], aid)
        return found, evaluated

    fd = audit["findings"] or {}
    checks = {c["id"]: c for c in fd.get("checks", [])}
    feats = fd.get("features", {})
    techs = {t["name"]: t for t in fd.get("technologies", [])}
    cats = {"technical": audit["technical_score"], "seo": audit["seo_score"]}

    def bad(*ids, levels=("FAIL",)):
        return [checks[i] for i in ids if i in checks and checks[i]["status"] in levels]

    ev = lambda cs: [f"{c['title']}: {c['detail']}" for c in cs]

    https_bad = bad("https", "tls_valid")
    if https_bad:
        f("SECURITY_HTTPS", "HIGH", 0.9, ev(https_bad), aid)
    if bad("mobile_viewport"):
        f("MOBILE_REDESIGN", "HIGH", 0.9, ev(bad("mobile_viewport")), aid)

    perf_ids = ("response_time", "lcp", "tbt", "render_blocking", "html_size", "script_count")
    pf, pw = bad(*perf_ids), bad(*perf_ids, levels=("WARN",))
    if pf:
        sev = "HIGH" if any(c["id"] in ("response_time", "lcp") for c in pf) else "MEDIUM"
        f("PERFORMANCE_OPTIMIZATION", sev, 0.85, ev(pf + pw), aid)
    elif len(pw) >= 2:
        f("PERFORMANCE_OPTIMIZATION", "LOW", 0.7, ev(pw), aid)

    seo_ids = ("title", "meta_description", "h1", "canonical", "structured_data", "indexability",
               "sitemap", "broken_links", "heading_structure", "image_alt", "open_graph")
    seo_bad = bad(*seo_ids, levels=("FAIL", "WARN"))
    hard = bad("title", "indexability")
    if hard:
        f("SEO_OPTIMIZATION", "HIGH", 0.9, ev(hard + [c for c in seo_bad if c not in hard]), aid)
    elif (cats["seo"] is not None and cats["seo"] < 50) and seo_bad:
        f("SEO_OPTIMIZATION", "MEDIUM", 0.85, ev(seo_bad), aid)
    elif (cats["seo"] is not None and cats["seo"] < 70) and len(seo_bad) >= 2:
        f("SEO_OPTIMIZATION", "LOW", 0.75, ev(seo_bad), aid)

    if _niche_in(lead, BOOKING_NICHES) and "booking" in feats and not feats["booking"]:
        f("BOOKING_SYSTEM", "MEDIUM", 0.65,
          [f"{lead.get('niche')} businesses typically take bookings; none detected on the site.",
           checks["booking"]["detail"]], aid)
    if _niche_in(lead, ORDERING_NICHES) and "online_ordering" in feats \
            and not feats["online_ordering"]:
        f("ONLINE_ORDERING", "MEDIUM", 0.65,
          [f"{lead.get('niche')} business with no online ordering detected."], aid)
    if _niche_in(lead, ECOMMERCE_NICHES) and "ecommerce" in feats and not feats["ecommerce"]:
        f("ECOMMERCE", "MEDIUM", 0.6,
          [f"{lead.get('niche')} business with no cart or shop detected."], aid)

    if "whatsapp_cta" in checks and checks["whatsapp_cta"]["status"] != "PASS":
        has_number = bool(lead.get("whatsapp"))
        f("WHATSAPP_CONVERSION", "MEDIUM" if has_number else "LOW", 0.8 if has_number else 0.6,
          [checks["whatsapp_cta"]["detail"]]
          + ([f"A WhatsApp-capable number is on record ({lead.get('whatsapp')})."]
             if has_number else []), aid)

    if bad("lead_capture"):
        no_call = bad("phone_cta")
        f("LEAD_CAPTURE", "HIGH" if (no_call and bad("contact_form")) else "MEDIUM", 0.85,
          ev(bad("lead_capture") + no_call), aid)

    if not any(t["category"] == "analytics" for t in techs.values()):
        f("ANALYTICS_TRACKING", "LOW", 0.55,
          ["No Google Analytics, Tag Manager or Hotjar tag was detected in the page "
           "(a tag injected later by script would not show here)."], aid)

    rating, reviews = lead.get("google_rating"), lead.get("google_reviews")
    if checks.get("social_proof", {}).get("status") == "WARN" and rating and reviews \
            and reviews >= 20 and rating >= 4.0:
        f("REVIEWS_SHOWCASE", "MEDIUM", 0.8,
          [f"Public listing: {rating} stars from {reviews} reviews.",
           checks["social_proof"]["detail"]], aid)

    overall = audit.get("overall_score")
    old_copy = bad("copyright_year")
    platform = next((n for n in TEMPLATE_BUILDERS if n in techs), None)
    if overall is not None and (overall < 45 or (overall < 60 and (old_copy or platform))):
        reasons = [f"Overall site audit score is {overall}/100 "
                   f"(technical {audit['technical_score']}, SEO {audit['seo_score']}, "
                   f"conversion {audit['conversion_score']})."]
        reasons += ev(old_copy)
        if platform:
            reasons.append(f"Built on {platform}, a template builder with limited flexibility.")
        f("WEBSITE_REDESIGN", "HIGH" if overall < 45 else "MEDIUM", 0.8, reasons, aid)
    elif bad("content_depth") or (old_copy and "WEBSITE_REDESIGN" not in found):
        f("CONTENT_REFRESH", "LOW", 0.7, ev(bad("content_depth") + old_copy), aid)
    return found, evaluated


# -------------------------------------------------------------- persistence --

def refresh_for_lead(lead_id: int) -> list[dict]:
    """Re-detect for one lead and reconcile the stored opportunities:
    new -> OPEN; still present -> updated (a DISMISSED/WON status is kept);
    previously found but now fixed -> RESOLVED (and re-opened if it returns)."""
    from . import audit as audit_mod

    lead = query_one("SELECT * FROM leads WHERE id = ? AND workspace_id = ?",
                     (lead_id, current_ws()))
    if not lead:
        raise LookupError("lead not found")
    found, evaluated = detect(lead, audit_mod.latest_audit(lead_id))
    pricing = get_pricing()
    now = utcnow()
    for t, o in found.items():
        lo, hi = pricing[t]
        impact, solution = COPY[t]
        execute(
            """INSERT INTO lead_opportunities
               (workspace_id, lead_id, audit_id, opportunity_type, severity, confidence,
                evidence, business_impact, recommended_solution, est_value_min, est_value_max,
                currency, status, detected_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,'INR','OPEN',?,?)
               ON DUPLICATE KEY UPDATE audit_id = VALUES(audit_id), severity = VALUES(severity),
                 confidence = VALUES(confidence), evidence = VALUES(evidence),
                 business_impact = VALUES(business_impact),
                 recommended_solution = VALUES(recommended_solution),
                 est_value_min = VALUES(est_value_min), est_value_max = VALUES(est_value_max),
                 resolved_at = IF(status = 'RESOLVED', NULL, resolved_at),
                 status = IF(status = 'RESOLVED', 'OPEN', status),
                 updated_at = VALUES(updated_at)""",
            (current_ws(), lead_id, o["audit_id"], t, o["severity"], o["confidence"],
             json.dumps(o["evidence"]), impact, solution, lo, hi, now, now),
        )
    stale = [t for t in evaluated if t not in found]
    if stale:
        marks = ",".join("?" for _ in stale)
        execute(
            f"UPDATE lead_opportunities SET status = 'RESOLVED', resolved_at = ?, updated_at = ? "
            f"WHERE workspace_id = ? AND lead_id = ? AND status = 'OPEN' "
            f"AND opportunity_type IN ({marks})",
            (now, now, current_ws(), lead_id, *stale))
    _update_lead_summary(lead_id)
    return list_for_lead(lead_id)


def _update_lead_summary(lead_id: int) -> None:
    row = query_one(
        "SELECT COUNT(*) AS n, COALESCE(SUM((est_value_min + est_value_max) / 2), 0) AS v "
        "FROM lead_opportunities WHERE workspace_id = ? AND lead_id = ? AND status = 'OPEN'",
        (current_ws(), lead_id))
    execute("UPDATE leads SET opportunity_count = ?, opportunity_value_est = ? "
            "WHERE id = ? AND workspace_id = ?",
            (row["n"], int(row["v"]), lead_id, current_ws()))


def _decode(row: dict) -> dict:
    try:
        row["evidence"] = json.loads(row["evidence"]) if row.get("evidence") else []
    except ValueError:
        row["evidence"] = []
    return row


def list_for_lead(lead_id: int, include_closed: bool = True) -> list[dict]:
    where = "" if include_closed else " AND status = 'OPEN'"
    rows = query(f"SELECT * FROM lead_opportunities WHERE workspace_id = ? AND lead_id = ?{where}",
                 (current_ws(), lead_id))
    rows.sort(key=lambda r: (r["status"] != "OPEN", SEVERITY_ORDER.get(r["severity"], 9),
                             -(r["est_value_max"] or 0)))
    return [_decode(r) for r in rows]


def set_status(opportunity_id: int, status: str) -> dict | None:
    if status not in ("OPEN", "DISMISSED", "WON"):
        raise ValueError("status must be OPEN, DISMISSED or WON")
    row = query_one("SELECT * FROM lead_opportunities WHERE id = ? AND workspace_id = ?",
                    (opportunity_id, current_ws()))
    if not row:
        return None
    execute("UPDATE lead_opportunities SET status = ?, updated_at = ? WHERE id = ? "
            "AND workspace_id = ?", (status, utcnow(), opportunity_id, current_ws()))
    _update_lead_summary(row["lead_id"])
    return _decode(query_one("SELECT * FROM lead_opportunities WHERE id = ?", (opportunity_id,)))
