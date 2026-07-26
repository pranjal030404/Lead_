"""The enrichment pipeline (Part 8.3).

Stages run in order and each writes its result back immediately, so a lead that
fails at stage 3 keeps everything stages 1-2 found. `enrichment_status` tracks
pending -> partial -> complete so the dashboard shows progress rather than a
lead just sitting there looking unfinished.

Cheapest-first everywhere: free HTML scraping before Hunter.io, and Hunter only
for HOT leads once the free routes come up empty (Part 9.3).
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from . import http, website
from .config import settings
from .db import execute, log_automation, log_failure, query_one, utcnow
from .quota import QuotaExceeded, check_and_reserve
from .scoring import score_lead
from .validation import normalise_phone, validate_email, whatsapp_number

HUNTER_URL = "https://api.hunter.io/v2/domain-search"


def _update(lead_id: int, fields: dict) -> None:
    if not fields:
        return
    fields["updated_at"] = utcnow()
    assignments = ", ".join(f"{k} = ?" for k in fields)
    execute(f"UPDATE leads SET {assignments} WHERE id = ?", (*fields.values(), lead_id))


def _website_check_is_fresh(lead: dict) -> bool:
    checked = lead.get("website_checked_at")
    if not checked:
        return False
    try:
        when = datetime.fromisoformat(checked)
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - when < timedelta(days=settings.website_cache_days)


def stage_website(lead: dict) -> dict:
    """Stage 1 - reachability, SSL, mobile, platform, staleness. Also yields
    socials and mailto addresses for free."""
    if _website_check_is_fresh(lead):
        return {}
    result = website.check_website(lead.get("website_url"))
    fields = website.to_db_fields(result)
    for field, url in result["socials"].items():
        if not lead.get(field):
            fields[field] = url
    if result["emails"] and not lead.get("email"):
        email, valid, _ = validate_email(result["emails"][0])
        fields["email"] = email
        fields["email_valid"] = 1 if valid else 0
    return fields


def stage_social(lead: dict) -> dict:
    """Stage 2 - social profile discovery.

    Free path: links already found on the business's own website (stage 1).
    Anything beyond that needs a search API; rather than scrape a search engine
    (fragile and against most of their terms), this is left as a deliberate
    extension point - plug a search provider in here if you want it automated,
    otherwise fill Instagram/Facebook in by hand on the leads worth it.
    """
    return {}


def stage_email(lead: dict) -> dict:
    """Stage 3 - email discovery, cheapest route first."""
    if lead.get("email"):
        return {}

    # Free: the site's own contact / about page.
    for candidate in website.find_contact_email(lead.get("website_url")):
        email, valid, _ = validate_email(candidate)
        if valid:
            return {"email": email, "email_valid": 1}

    # Paid fallback, HOT leads only, hard-capped at the free tier.
    if not settings.hunter_api_key or (lead.get("lead_priority") != "HOT"):
        return {}
    domain = urlparse(website._normalise_url(lead.get("website_url") or "")).netloc
    if not domain:
        return {}
    try:
        check_and_reserve("hunter")
    except QuotaExceeded:
        log_automation("enrichment", "Hunter.io monthly cap reached - skipping email discovery",
                       "warn")
        return {}
    try:
        response = http.get(
            HUNTER_URL, breaker="hunter", retries=2,
            params={"domain": domain, "api_key": settings.hunter_api_key, "limit": 1},
        )
        if response.status_code >= 400:
            return {}
        emails = (response.json().get("data") or {}).get("emails") or []
        if not emails:
            return {}
        email, valid, _ = validate_email(emails[0].get("value"))
        fields = {"email": email, "email_valid": 1 if valid else 0}
        first = emails[0].get("first_name")
        last = emails[0].get("last_name")
        if first and not lead.get("owner_name"):
            fields["owner_name"] = " ".join(p for p in (first, last) if p)
        return fields
    except Exception as exc:  # noqa: BLE001
        log_failure("enrich.email", str(exc), lead["id"])
        return {}


def stage_validate(lead: dict) -> dict:
    """Stage 4 - normalise and flag the contact data."""
    fields: dict = {}
    phone, phone_valid = normalise_phone(lead.get("phone"), lead.get("country"))
    if phone != lead.get("phone") or bool(phone_valid) != bool(lead.get("phone_valid")):
        fields["phone"] = phone
        fields["phone_valid"] = 1 if phone_valid else 0
    # Always recompute from the normalised phone rather than only filling a
    # blank - otherwise a corrected number leaves a stale wa.me link behind.
    whatsapp = whatsapp_number(phone, lead.get("country"))
    if whatsapp != lead.get("whatsapp"):
        fields["whatsapp"] = whatsapp
    if lead.get("email"):
        email, valid, _ = validate_email(lead["email"])
        fields["email"] = email
        fields["email_valid"] = 1 if valid else 0
    return fields


def stage_score(lead: dict) -> dict:
    """Stage 5 - deterministic scoring, once everything else has landed."""
    return score_lead(lead)


def enrich_lead(lead_id: int) -> dict:
    """Run the full pipeline for one lead. Returns the final lead row."""
    lead = query_one("SELECT * FROM leads WHERE id = ?", (lead_id,))
    if not lead:
        raise ValueError(f"Lead {lead_id} not found")

    _update(lead_id, {"enrichment_status": "partial", "enrichment_error": None})
    errors: list[str] = []

    for name, stage in (
        ("website", stage_website),
        ("social", stage_social),
        ("email", stage_email),
        ("validate", stage_validate),
    ):
        try:
            fields = stage(lead)
            if fields:
                _update(lead_id, fields)
                lead.update(fields)
        except Exception as exc:  # noqa: BLE001 - one bad stage must not sink the lead
            errors.append(f"{name}: {exc}")
            log_failure(f"enrich.{name}", str(exc), lead_id)

    scored = stage_score(lead)
    _update(lead_id, scored)
    lead.update(scored)

    _update(lead_id, {
        "enrichment_status": "complete" if not errors else "partial",
        "enrichment_error": "; ".join(errors)[:500] or None,
    })
    return query_one("SELECT * FROM leads WHERE id = ?", (lead_id,))


def enrich_many(lead_ids: list[int], on_progress=None) -> dict:
    """Enrich a batch in parallel.

    Enrichment is almost entirely waiting on other people's web servers, and a
    dead site burns the full HTTP_TIMEOUT before giving up. Run sequentially,
    one slow batch of 60 leads is 60 timeouts end to end; a bounded pool
    overlaps that wait. The work per lead is unchanged and each lead still
    writes its own results, so a failure is still contained to one lead.

    Bounded on purpose: ENRICH_WORKERS caps both outbound connections and the
    number of threads contending for the SQLite writer.
    """
    done, failed = 0, 0
    hot_ids: list[int] = []
    if not lead_ids:
        return {"enriched": 0, "failed": 0, "hot_ids": []}

    workers = max(1, min(settings.enrich_workers, len(lead_ids)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="enrich") as pool:
        futures = {pool.submit(enrich_lead, lead_id): lead_id for lead_id in lead_ids}
        for finished, future in enumerate(as_completed(futures), 1):
            lead_id = futures[future]
            try:
                result = future.result()
                done += 1
                if result and result.get("lead_priority") == "HOT":
                    hot_ids.append(lead_id)
            except Exception as exc:  # noqa: BLE001 - one bad lead must not sink the batch
                failed += 1
                log_failure("enrich.lead", str(exc), lead_id)
            if on_progress:
                on_progress(finished, len(lead_ids))

    # Each worker thread holds its own SQLite handle; drop them rather than
    # leaving one per pool thread open after the pool is torn down.
    return {"enriched": done, "failed": failed, "hot_ids": hot_ids}


def enrich_pending(limit: int = 50) -> dict:
    """Enrich every lead that hasn't been through the pipeline yet."""
    rows = query_one(
        "SELECT COUNT(*) AS n FROM leads WHERE enrichment_status IN ('pending','partial')"
    )
    pending_total = rows["n"] if rows else 0
    from .db import query

    leads = query(
        "SELECT id FROM leads WHERE enrichment_status IN ('pending','partial') "
        "ORDER BY id LIMIT ?",
        (limit,),
    )
    result = enrich_many([row["id"] for row in leads])

    # Part 7.6 - tell the operator about the good ones rather than leaving them
    # sitting unopened. Deduplicated per lead, so this is safe to call every run.
    from . import notify

    notify.alert_new_hot_leads(result["hot_ids"])
    return {"enriched": result["enriched"], "failed": result["failed"],
            "new_hot": len(result["hot_ids"]), "pending_before": pending_total}


def rescore_all() -> int:
    """Recompute scores for every lead - use after changing scoring rules."""
    from .db import query

    count = 0
    for lead in query("SELECT * FROM leads"):
        _update(lead["id"], score_lead(lead))
        count += 1
    return count


def parse_issues(raw) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return []
