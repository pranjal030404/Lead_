"""External buying-intent probes: domain age (RDAP), hiring (Adzuna), ads (Meta)."""

from __future__ import annotations

from datetime import datetime, timezone

from .. import http
from ..config import settings
from ..netsafe import UnsafeURL  # noqa: F401  (re-export for callers)


def _dt(v):
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_rdap(data: dict, now: datetime | None = None) -> dict:
    """Pure: RDAP JSON -> {registered, expires, age_days, days_to_expiry, flags}."""
    now = now or datetime.now(timezone.utc)
    ev = {e.get("eventAction"): _dt(e.get("eventDate")) for e in data.get("events", [])}
    reg, exp = ev.get("registration"), ev.get("expiration")
    out = {"registered": reg and reg.isoformat(), "expires": exp and exp.isoformat(),
           "age_days": (now - reg).days if reg else None,
           "days_to_expiry": (exp - now).days if exp else None, "flags": []}
    if out["age_days"] is not None and out["age_days"] < 120:
        out["flags"].append("NEW_DOMAIN")
    if out["days_to_expiry"] is not None and out["days_to_expiry"] < 45:
        out["flags"].append("DOMAIN_EXPIRING")
    return out


def domain_intel(domain: str) -> dict:
    r = http.get(f"https://rdap.org/domain/{domain}", breaker="rdap", retries=1, timeout=8)
    if r.status_code >= 400:
        return {"error": f"RDAP lookup failed ({r.status_code})", "flags": []}
    return parse_rdap(r.json())


def hiring(company: str, country: str = "gb", terms=("web developer", "digital marketing")) -> dict:
    """Is this company advertising digital roles? Needs ADZUNA_APP_ID/KEY."""
    if not (settings.adzuna_app_id and settings.adzuna_app_key):
        return {"available": False, "reason": "ADZUNA_APP_ID / ADZUNA_APP_KEY not set"}
    found = []
    for term in terms:
        r = http.get(f"https://api.adzuna.com/v1/api/jobs/{country.lower()}/search/1", breaker="adzuna",
                     params={"app_id": settings.adzuna_app_id, "app_key": settings.adzuna_app_key,
                             "what": term, "company": company, "results_per_page": 5}, retries=1)
        if r.status_code < 400:
            found += [{"title": j.get("title"), "created": j.get("created")}
                      for j in r.json().get("results", [])]
    return {"available": True, "hiring_digital": bool(found), "jobs": found[:5]}


def running_ads(page_name: str, country: str = "GB") -> dict:
    """Active Meta ads for a page name. Needs META_AD_LIBRARY_TOKEN."""
    if not settings.meta_ad_library_token:
        return {"available": False, "reason": "META_AD_LIBRARY_TOKEN not set"}
    r = http.get("https://graph.facebook.com/v19.0/ads_archive", breaker="meta_ads", retries=1,
                 params={"access_token": settings.meta_ad_library_token, "search_terms": page_name,
                         "ad_reached_countries": f'["{country.upper()}"]', "ad_active_status": "ACTIVE",
                         "fields": "page_name,ad_delivery_start_time", "limit": 5})
    if r.status_code >= 400:
        return {"available": True, "error": f"Meta API error {r.status_code}"}
    ads = [a for a in r.json().get("data", []) if page_name.lower() in (a.get("page_name") or "").lower()]
    return {"available": True, "running_ads": bool(ads), "ads": ads[:5]}
