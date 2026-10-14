"""Multi-score lead model and the Overall Opportunity Score.

The original deterministic 0-10 *rule score* (app/scoring.py) is untouched and
stays in `leads.lead_score` / `lead_priority`. This module adds independent
0-100 component scores and combines them:

  lead_quality     rule score + data completeness
  digital_gap      how weak the business's web presence is (opportunity size)
  contactability   how many usable, non-blocked ways exist to reach them
  business_value   reputation proxy + estimated project value
  buying_intent    active evidence-backed intent signals (see signals.py)
  decision_maker   how likely we know/can reach someone who decides
  outreach_fit     can we make a specific, grounded pitch through a usable channel now
  data_confidence  trust in the underlying data (lineage) - informs *confidence*,
                   not the overall score

Overall = weight-normalised mean of the *available* components. A component
whose inputs do not exist (None) is excluded and reported as unavailable - never
silently scored 0 - and lowers the stated confidence through `coverage`.

Every score carries its reasons and inputs, and the full breakdown is stored, so
"why is this lead an 86?" always has an answer.

    confidence = 0.4 * coverage + 0.3 * mean(component confidence)
               + 0.3 * (data_confidence / 100, or 0.5 when unknown)
"""

from __future__ import annotations

import json
import math

from . import emailintel, lineage, opportunities, signals
from .db import (current_ws, execute, get_ws_setting, log_failure, query, query_one,
                 set_ws_setting, utcnow)
from .outreach import is_suppressed

SCORING_VERSION = 1
DEFAULT_WEIGHTS = {
    "digital_gap": 0.25, "buying_intent": 0.15, "business_value": 0.15,
    "contactability": 0.15, "decision_maker": 0.10, "outreach_fit": 0.10,
    "lead_quality": 0.10,
}
COMPONENTS = ("lead_quality", "digital_gap", "contactability", "business_value",
              "buying_intent", "decision_maker", "outreach_fit", "data_confidence")
TIER_HOT, TIER_WARM = 75, 55
SEVERITY_POINTS = {"HIGH": 50, "MEDIUM": 35, "LOW": 20}


def get_weights() -> dict:
    weights = dict(DEFAULT_WEIGHTS)
    raw = get_ws_setting("score_weights")
    if raw:
        try:
            for k, v in json.loads(raw).items():
                if k in weights and isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
                    weights[k] = float(v)
        except (ValueError, TypeError, AttributeError):
            pass
    return weights if sum(weights.values()) > 0 else dict(DEFAULT_WEIGHTS)


def set_weights(table: dict) -> dict:
    clean = {}
    for k, v in table.items():
        if k not in DEFAULT_WEIGHTS:
            raise ValueError(f"Unknown component: {k} (weights apply to {sorted(DEFAULT_WEIGHTS)})")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 or v > 1000:
            raise ValueError(f"{k}: weight must be a number >= 0")
        clean[k] = float(v)
    merged = {**DEFAULT_WEIGHTS, **clean}
    if sum(merged.values()) <= 0:
        raise ValueError("At least one weight must be above zero")
    set_ws_setting("score_weights", json.dumps(clean))
    return get_weights()


def tier_for(overall: int | None) -> str | None:
    if overall is None:
        return None
    return "HOT" if overall >= TIER_HOT else "WARM" if overall >= TIER_WARM else "COLD"


def _comp(score, confidence, reasons, inputs=None) -> dict:
    return {"score": None if score is None else max(0, min(100, round(score))),
            "confidence": confidence, "reasons": reasons, "inputs": inputs or {},
            "available": score is not None}


# ------------------------------------------------------------ components --
# Each takes the gathered `ctx` dict and returns a component dict. Pure.

def lead_quality(ctx: dict) -> dict:
    lead = ctx["lead"]
    rule = lead.get("lead_score")
    dq = lead.get("data_quality_score")
    if rule is None and dq is None:
        return _comp(None, 0.9, ["No rule score or data-quality score yet."])
    score = 0.7 * (rule or 0) * 10 + 0.3 * (dq or 0) * 10
    return _comp(score, 0.9,
                 [f"Rule score {rule}/10 ({lead.get('lead_priority')}) carries 70%.",
                  f"Data completeness {dq}/10 carries 30%."],
                 {"rule_score": rule, "data_quality_score": dq})


def digital_gap(ctx: dict) -> dict:
    lead, audit = ctx["lead"], ctx["audit"]
    has_url = bool((lead.get("website_url") or "").strip())
    if not has_url:
        return _comp(95, 0.6, ["No website listed - the largest possible gap.",
                               "Confidence is moderate: provider listings can omit websites."],
                     {"website": None})
    if audit and audit["status"] in ("UNREACHABLE", "ERROR_PAGE"):
        return _comp(90, 0.85, [f"The website does not load ({audit.get('error') or 'error'})."],
                     {"audit_status": audit["status"]})
    if audit and audit["status"] == "OK" and audit.get("overall_score") is not None:
        o = audit["overall_score"]
        return _comp(100 - o, 0.9,
                     [f"Website audit score is {o}/100, so the gap is {100 - o}.",
                      f"Weakest areas: {_weakest(audit)}."],
                     {"audit_score": o})
    if lead.get("has_website") == 0 and lead.get("website_checked_at"):
        return _comp(88, 0.7, ["The basic website check could not load the site."])
    basic = lead.get("website_score")
    if basic is not None and lead.get("website_checked_at"):
        return _comp(100 - basic * 10, 0.5,
                     [f"Only the basic website check has run ({basic}/10); no deep audit yet."],
                     {"basic_website_score": basic})
    return _comp(None, 0.0, ["The website has not been checked yet."])


def _weakest(audit: dict) -> str:
    cats = {k: audit.get(f"{k}_score") for k in ("technical", "seo", "performance",
                                                  "conversion", "content")}
    cats = {k: v for k, v in cats.items() if v is not None}
    worst = sorted(cats, key=cats.get)[:2]
    return ", ".join(f"{k} {cats[k]}" for k in worst) or "n/a"


def contactability(ctx: dict) -> dict:
    lead, emails, ws_blocked = ctx["lead"], ctx["emails"], ctx["suppressed"]
    reasons, points = [], 0
    if lead.get("phone"):
        if ws_blocked.get("phone"):
            reasons.append("Phone is on the suppression list: 0.")
        elif lead.get("phone_valid"):
            points += 30
            reasons.append("Valid phone number: +30.")
        else:
            points += 10
            reasons.append("Phone present but not validated: +10.")
    else:
        reasons.append("No phone number.")
    if lead.get("whatsapp") and not ws_blocked.get("phone"):
        points += 15
        reasons.append("WhatsApp-capable number: +15.")
    best = None
    for e in emails:
        if e["status"] in emailintel.BLOCKING_STATUSES or ws_blocked.get(e["email"]):
            continue
        val = 35 if e["status"] == "VERIFIED" else {"PERSONAL": 28, "ROLE": 22}.get(e["email_type"], 20)
        if best is None or val > best[0]:
            best = (val, e)
    if best:
        points += best[0]
        reasons.append(f"Best email {best[1]['email']} ({best[1]['status']}, "
                       f"{best[1]['email_type']}): +{best[0]}.")
    elif emails:
        reasons.append("Every known email is invalid, disposable, inferred or suppressed: +0.")
    else:
        reasons.append("No email address known.")
    if ctx["features"].get("contact_form"):
        points += 10
        reasons.append("Website has an enquiry form: +10.")
    if ctx["socials"]:
        points += 5
        reasons.append(f"{len(ctx['socials'])} social profile(s): +5.")
    if lead.get("address"):
        points += 5
        reasons.append("Street address known: +5.")
    return _comp(points, 0.85, reasons, {"emails_known": len(emails)})


def business_value(ctx: dict) -> dict:
    lead = ctx["lead"]
    rating, reviews = lead.get("google_rating"), lead.get("google_reviews")
    rep = None
    reasons = []
    if reviews:
        base = min(95, 30 * math.log10(max(reviews, 1) + 1) + 5)    # 10->36, 100->65, 1000->95
        factor = 0.6 + 0.4 * max(0.0, min(1.0, ((rating or 3.0) - 3.0) / 2.0))
        rep = base * factor
        reasons.append(f"Reputation: {reviews} reviews at {rating or 'n/a'} stars -> {round(rep)}.")
    value = ctx["opportunity_value"]
    potential = None
    if value:
        potential = min(100, value / 150_000 * 100)
        reasons.append(f"Estimated open opportunity value ~INR {value:,} -> {round(potential)}.")
    if rep is None and potential is None:
        return _comp(None, 0.0, ["No reviews and no open opportunities to size the business."])
    score = rep if potential is None else potential if rep is None else 0.6 * rep + 0.4 * potential
    if rep is not None and potential is not None:
        reasons.append("Combined 60% reputation, 40% opportunity size.")
    return _comp(score, 0.6, reasons, {"reviews": reviews, "rating": rating,
                                       "opportunity_value": value})


def buying_intent(ctx: dict) -> dict:
    score, conf = signals.intent(ctx["signals"])
    active = [s for s in ctx["signals"] if s["status"] == "ACTIVE"
              and signals.effective_strength(s) > 0]
    if not ctx["evaluated"]:
        return _comp(None, 0.0, ["Intent has not been evaluated yet (no audit or signals)."])
    if not active:
        return _comp(0, 0.5, ["No intent signals detected (absence of detectable signals is "
                              "not proof of no intent)."], {"signals": 0})
    return _comp(score, conf or 0.5,
                 [f"{s['label']} (strength {signals.effective_strength(s):.2f}, "
                  f"confidence {s['confidence']:.2f})" for s in active],
                 {"signals": [s["signal_type"] for s in active]})


def decision_maker(ctx: dict) -> dict:
    contacts, lead = ctx["contacts"], ctx["lead"]
    if contacts:
        top = max(contacts, key=lambda c: c["decision_maker_probability"])
        score = 100 * top["decision_maker_probability"]
        reasons = [f"{top['name']} ({top['title'] or 'no title'}) - role {top['role_category']}, "
                   f"decision-maker likelihood {top['decision_maker_probability']:.0%} (title "
                   f"keyword heuristic)."]
        if top.get("email") or top.get("phone"):
            score += 10
            reasons.append("Direct email/phone on file: +10.")
        return _comp(score, top["confidence"], reasons, {"contacts": len(contacts)})
    if lead.get("owner_name"):
        return _comp(35, 0.5, [f"An owner name is recorded ({lead['owner_name']}) but no "
                               "contact record with a title."])
    return _comp(0, 0.7, ["No decision-maker identified."])


def outreach_fit(ctx: dict) -> dict:
    opps, c = ctx["open_opportunities"], ctx["contactability"]
    reasons, score = [], 0
    if not opps:
        reasons.append("No open opportunity to pitch.")
    else:
        top = min(opps, key=lambda o: (list(SEVERITY_POINTS).index(o["severity"]),
                                       -(o["est_value_max"] or 0)))
        score += SEVERITY_POINTS[top["severity"]]
        reasons.append(f"Lead opportunity: {top['opportunity_type']} ({top['severity']}): "
                       f"+{SEVERITY_POINTS[top['severity']]}.")
        if sum(len(o["evidence"]) for o in opps) >= 2:
            score += 10
            reasons.append("Enough evidence to personalise the message: +10.")
    channel = c["available"] and c["score"] >= 20
    if channel:
        score += 40
        reasons.append("A usable contact channel exists: +40.")
    else:
        score = min(score, 20)
        reasons.append("No usable contact channel - capped at 20.")
    if ctx["suppressed"].get("all"):
        return _comp(0, 0.9, ["Every contact route is suppressed - never contact."])
    return _comp(score, 0.8, reasons)


def data_confidence(ctx: dict) -> dict:
    dc = ctx["lead"].get("data_confidence_score")
    if dc is None:
        return _comp(None, 0.0, ["No data lineage recorded yet."])
    return _comp(dc, 0.9, [f"Weighted effective confidence of tracked fields is {dc}/100 "
                           f"(freshness: {ctx['lead'].get('freshness_status') or 'unknown'})."])


# --------------------------------------------------------------- combine --

def combine(components: dict, weights: dict, data_conf: int | None) -> dict:
    avail = {k: v for k, v in components.items() if k in weights and v["available"]
             and weights[k] > 0}
    total_w = sum(weights[k] for k in weights if weights[k] > 0)
    used_w = sum(weights[k] for k in avail)
    if not avail or total_w <= 0:
        return {"overall": None, "tier": None, "confidence": None, "coverage": 0.0,
                "contributions": {}}
    overall = sum(weights[k] * v["score"] for k, v in avail.items()) / used_w
    contributions = {k: round(weights[k] / used_w * v["score"], 1) for k, v in avail.items()}
    coverage = used_w / total_w
    mean_conf = sum(v["confidence"] for v in avail.values()) / len(avail)
    dc = (data_conf / 100) if data_conf is not None else 0.5
    confidence = round(100 * (0.4 * coverage + 0.3 * mean_conf + 0.3 * dc))
    return {"overall": round(overall), "tier": tier_for(round(overall)),
            "confidence": confidence, "coverage": round(coverage, 2),
            "contributions": contributions,
            "normalised_weights": {k: round(weights[k] / used_w, 3) for k in avail}}


def compute(ctx: dict, weights: dict | None = None) -> dict:
    """Pure: gathered context -> full scoring result with explanation."""
    weights = weights or DEFAULT_WEIGHTS
    comps = {}
    comps["lead_quality"] = lead_quality(ctx)
    comps["digital_gap"] = digital_gap(ctx)
    comps["contactability"] = contactability(ctx)
    ctx = {**ctx, "contactability": comps["contactability"]}
    comps["business_value"] = business_value(ctx)
    comps["buying_intent"] = buying_intent(ctx)
    comps["decision_maker"] = decision_maker(ctx)
    comps["outreach_fit"] = outreach_fit(ctx)
    comps["data_confidence"] = data_confidence(ctx)
    result = combine(comps, weights, comps["data_confidence"]["score"])
    for k, v in comps.items():
        v["weight"] = weights.get(k, 0.0)
        v["contribution"] = result["contributions"].get(k)
    return {
        "version": SCORING_VERSION, "components": comps, "weights": weights, **result,
        "rule_score": {"score": ctx["lead"].get("lead_score"),
                       "priority": ctx["lead"].get("lead_priority"),
                       "reasons": _parse_list(ctx["lead"].get("score_reasons"))},
        "tiers": {"HOT": f">= {TIER_HOT}", "WARM": f">= {TIER_WARM}", "COLD": "below"},
        "confidence_formula": "0.4*coverage + 0.3*mean(component confidence) + "
                              "0.3*(data_confidence/100, 0.5 if unknown)",
        "note": "Components marked unavailable are excluded, not scored 0; "
                "data_confidence informs confidence and is not part of the overall.",
    }


def _parse_list(raw):
    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw) if raw else []
    except (ValueError, TypeError):
        return []


# ----------------------------------------------------------- persistence --

def gather(lead_id: int) -> dict:
    from . import audit as audit_mod

    ws = current_ws()
    lead = query_one("SELECT * FROM leads WHERE id = ? AND workspace_id = ?", (lead_id, ws))
    if not lead:
        raise LookupError("lead not found")
    aud = audit_mod.latest_audit(lead_id)
    findings = (aud or {}).get("findings") or {}
    emails = query("SELECT email, email_type, status FROM email_addresses WHERE workspace_id = ? "
                   "AND lead_id = ?", (ws, lead_id))
    if lead.get("email") and not any(e["email"] == lead["email"].lower() for e in emails):
        info = emailintel.classify(lead["email"])
        emails.append({"email": info["email"], "email_type": info["email_type"],
                       "status": info["status"]})
    suppressed = {e["email"]: True for e in emails if is_suppressed(e["email"])}
    phone_supp = bool(lead.get("phone") and is_suppressed(lead["phone"], "phone"))
    if phone_supp:
        suppressed["phone"] = True
    reachable_routes = [e for e in emails if not suppressed.get(e["email"])] \
        or (lead.get("phone") and not phone_supp)
    if (emails or lead.get("phone")) and not reachable_routes:
        suppressed["all"] = True
    opps = opportunities.list_for_lead(lead_id, include_closed=False)
    sigs = signals.list_for_lead(lead_id)
    return {
        "lead": lead, "audit": aud, "features": findings.get("features", {}),
        "emails": emails, "suppressed": suppressed,
        "socials": query("SELECT platform FROM social_profiles WHERE workspace_id = ? "
                         "AND lead_id = ?", (ws, lead_id)),
        "contacts": query("SELECT * FROM contacts WHERE workspace_id = ? AND lead_id = ?",
                          (ws, lead_id)),
        "open_opportunities": opps,
        "opportunity_value": int(sum((o["est_value_min"] + o["est_value_max"]) / 2 for o in opps)),
        "signals": sigs,
        "evaluated": bool(aud) or bool(sigs) or not (lead.get("website_url") or "").strip(),
    }


def recompute(lead_id: int, refresh_signals: bool = True) -> dict:
    """Refresh signals, score the lead, store scores + explanation (+ history)."""
    if refresh_signals:
        signals.refresh_for_lead(lead_id)
    ctx = gather(lead_id)
    result = compute(ctx, get_weights())
    c = result["components"]
    sc = {k: c[k]["score"] for k in COMPONENTS}
    now = utcnow()
    ws = current_ws()
    prev = query_one("SELECT overall, tier FROM lead_scores WHERE workspace_id = ? AND lead_id = ?",
                     (ws, lead_id))
    execute(
        """INSERT INTO lead_scores
           (workspace_id, lead_id, scoring_version, rule_score, lead_quality, digital_gap,
            contactability, business_value, buying_intent, decision_maker, outreach_fit,
            data_confidence, overall, tier, confidence, explanation, computed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON DUPLICATE KEY UPDATE scoring_version = VALUES(scoring_version),
             rule_score = VALUES(rule_score), lead_quality = VALUES(lead_quality),
             digital_gap = VALUES(digital_gap), contactability = VALUES(contactability),
             business_value = VALUES(business_value), buying_intent = VALUES(buying_intent),
             decision_maker = VALUES(decision_maker), outreach_fit = VALUES(outreach_fit),
             data_confidence = VALUES(data_confidence), overall = VALUES(overall),
             tier = VALUES(tier), confidence = VALUES(confidence),
             explanation = VALUES(explanation), computed_at = VALUES(computed_at)""",
        (ws, lead_id, SCORING_VERSION, ctx["lead"].get("lead_score"), sc["lead_quality"],
         sc["digital_gap"], sc["contactability"], sc["business_value"], sc["buying_intent"],
         sc["decision_maker"], sc["outreach_fit"], sc["data_confidence"], result["overall"],
         result["tier"], result["confidence"], json.dumps(result), now))
    execute("UPDATE leads SET overall_score = ?, opportunity_tier = ?, score_confidence = ?, "
            "intent_score = ?, scored_at = ? WHERE id = ? AND workspace_id = ?",
            (result["overall"], result["tier"], result["confidence"], sc["buying_intent"], now,
             lead_id, ws))
    if prev is None or prev["overall"] != result["overall"] or prev["tier"] != result["tier"]:
        execute("INSERT INTO lead_score_history(workspace_id, lead_id, overall, tier, "
                "buying_intent, created_at) VALUES(?,?,?,?,?,?)",
                (ws, lead_id, result["overall"], result["tier"], sc["buying_intent"], now))
    return get_scores(lead_id)


def safe_recompute(lead_id: int) -> None:
    """Best-effort refresh after an edit; scoring is derived data and must never
    make the triggering write fail."""
    try:
        recompute(lead_id)
    except Exception as exc:  # noqa: BLE001
        log_failure("leadscore.recompute", str(exc), lead_id)


def get_scores(lead_id: int) -> dict | None:
    row = query_one("SELECT * FROM lead_scores WHERE workspace_id = ? AND lead_id = ?",
                    (current_ws(), lead_id))
    if not row:
        return None
    row["explanation"] = json.loads(row["explanation"]) if row["explanation"] else None
    return row


def history(lead_id: int, limit: int = 50) -> list[dict]:
    return query("SELECT overall, tier, buying_intent, created_at FROM lead_score_history "
                 "WHERE workspace_id = ? AND lead_id = ? ORDER BY id DESC LIMIT ?",
                 (current_ws(), lead_id, limit))


def recompute_workspace(only_updated_since: str | None = None) -> int:
    """Scheduled sweep. Isolated per lead: one failure never stops the rest."""
    where, params = "workspace_id = ?", [current_ws()]
    if only_updated_since:
        where += " AND (scored_at IS NULL OR updated_at >= ?)"
        params.append(only_updated_since)
    done = 0
    for row in query(f"SELECT id FROM leads WHERE {where} ORDER BY id", tuple(params)):
        try:
            recompute(row["id"])
            done += 1
        except Exception as exc:  # noqa: BLE001
            log_failure("leadscore.recompute", str(exc), row["id"])
    return done
