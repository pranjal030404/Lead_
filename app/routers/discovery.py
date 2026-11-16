"""Lead-discovery toolkit endpoints."""

from __future__ import annotations

import re

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from .. import netsafe
from ..config import settings
from ..db import current_ws, lead_scope, query
from ..discovery import intent_feed, free_signals, csvimport, dorks, grid, importer, lookalike, reviewmine, signals_ext, techstack
from ..providers import available_providers
from .leads import _own_lead

router = APIRouter(prefix="/api/discovery", tags=["discovery"])
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")


@router.get("/sources")
def sources():
    """Every way the app can find leads, and which are ready to use."""
    ready = {p["key"]: p["ready"] for p in available_providers()}
    catalogue = [
        {"id": "osm", "kind": "map", "name": "OpenStreetMap search", "ready": True, "free": True},
        {"id": "osm+grid", "kind": "map", "name": "OpenStreetMap grid sweep", "ready": ready["osm+grid"], "free": True},
        {"id": "nominatim", "kind": "map", "name": "OpenStreetMap text search (2nd source)", "ready": True, "free": True},
        {"id": "osm_new", "kind": "registry", "name": "New openings (newly mapped businesses)", "ready": True, "free": True},
        {"id": "csv", "kind": "import", "name": "CSV / sheet import (directories, CRMs, association lists)",
         "ready": True, "free": True, "endpoint": "POST /api/discovery/import"},
        {"id": "dorks", "kind": "manual", "name": "Search-query generator", "ready": True, "free": True},
        {"id": "lookalike", "kind": "reverse", "name": "Lookalikes of won customers", "ready": True, "free": True},
        {"id": "hiring", "kind": "signal", "name": "Hiring signal (careers page + open job feeds)", "ready": True, "free": True},
        {"id": "ads", "kind": "signal", "name": "Paid-ads footprint (pixel detection)", "ready": True, "free": True},
        {"id": "techstack", "kind": "signal", "name": "Tech-stack fingerprint", "ready": True, "free": True},
        {"id": "domain", "kind": "signal", "name": "Domain age / expiry (RDAP)", "ready": True, "free": True},
        {"id": "reviews", "kind": "signal", "name": "Review-text pain mining", "ready": True, "free": True},
        {"id": "google+grid", "kind": "map", "name": "Google Places grid sweep (optional)", "ready": ready["google+grid"],
         "needs": "GOOGLE_API_KEY", "optional": True},
        {"id": "foursquare", "kind": "map", "name": "Foursquare Places (optional)", "ready": ready["foursquare"],
         "needs": "FOURSQUARE_API_KEY", "optional": True},
        {"id": "companies_house", "kind": "registry", "name": "UK Companies House (optional)",
         "ready": ready["companies_house"], "needs": "COMPANIES_HOUSE_API_KEY", "optional": True},
        {"id": "opencorporates", "kind": "registry", "name": "OpenCorporates (optional)",
         "ready": ready["opencorporates"], "needs": "OPENCORPORATES_API_KEY", "optional": True},
    ]
    # Open-source build: only show optional keyed sources once the operator has switched them on.
    catalogue = [c for c in catalogue if not c.get("optional") or c["ready"]]
    catalogue.insert(3, {"id": "overture", "kind": "map", "name": "Overture Maps (open data, phones & emails)",
                         "ready": ready.get("overture", False), "free": True})
    return {"sources": catalogue}


class GridPayload(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    radius_m: int = Field(8000, ge=500, le=50000)
    cell_radius_m: int = Field(1500, ge=300, le=10000)


@router.post("/grid-preview")
def grid_preview(p: GridPayload):
    cells = grid.grid_cells(p.lat, p.lng, p.radius_m, p.cell_radius_m, settings.sweep_max_cells)
    return {"cells": cells, "count": len(cells), "max_cells": settings.sweep_max_cells}


@router.get("/dorks")
def get_dorks(niche: str, city: str):
    try:
        return {"queries": dorks.generate(niche, city)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/import")
async def import_csv(request: Request, file: UploadFile = File(...), niche: str = Form(""),
                     enrich: bool = Form(True)):
    raw = await file.read(csvimport.MAX_BYTES + 1)
    try:
        parsed = csvimport.parse_csv(raw.decode("utf-8-sig", "replace"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    user = getattr(request.state, "user", None)
    result = importer.import_candidates(parsed["candidates"], niche.strip(),
                                        user["id"] if user else None, enrich)
    return {**result, "mapped_columns": parsed["mapped_columns"],
            "ignored_columns": parsed["ignored_columns"], "skipped": parsed["skipped"][:50]}


@router.get("/leads/{lead_id}/techstack")
def lead_techstack(lead_id: int):
    lead = _own_lead(lead_id, "id, website_url")
    if not lead.get("website_url"):
        raise HTTPException(400, "This lead has no website to inspect")
    url = lead["website_url"] if "//" in lead["website_url"] else "https://" + lead["website_url"]
    try:
        resp = netsafe.safe_get(url, breaker="website_check", retries=1, timeout=8)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Could not fetch the site: {exc}") from exc
    return techstack.fingerprint(resp.text or "", dict(resp.headers), str(resp.url).startswith("https"))


@router.get("/domain")
def domain(name: str):
    name = name.strip().lower().removeprefix("www.")
    if not DOMAIN_RE.match(name):
        raise HTTPException(400, "Not a valid domain name")
    return signals_ext.domain_intel(name)


@router.get("/hiring")
def hiring(company: str = "", website: str = "", country: str = "gb"):
    """Free engine (careers page + open job feeds). Adzuna is merged in when its keys exist."""
    if not company.strip() and not website.strip():
        raise HTTPException(400, "Give a company name and/or a website")
    result = free_signals.hiring(company.strip(), website.strip() or None)
    if settings.adzuna_app_id and settings.adzuna_app_key and company.strip():
        extra = signals_ext.hiring(company.strip(), country if re.fullmatch(r"[a-zA-Z]{2}", country) else "gb")
        if extra.get("jobs"):
            result["evidence"].append({"source": "Adzuna", "roles": [j["title"] for j in extra["jobs"]]})
            result["hiring_digital"] = True
    return result


@router.get("/ads")
def ads(url: str = "", lead_id: int | None = None, page: str = "", country: str = "GB"):
    """Free engine: detect ad pixels on the site. Meta Ad Library is added when a token exists."""
    target = url.strip()
    if lead_id is not None:
        target = (_own_lead(lead_id, "id, website_url").get("website_url") or "")
    if not target and not page.strip():
        raise HTTPException(400, "Give a website URL, a lead id, or a page name")
    out: dict = {"advertising": False, "platforms": [], "wasted_spend_signals": [], "confidence": "none"}
    if target:
        full = target if "//" in target else "https://" + target
        html = free_signals.fetch_page(full)
        if not html:
            raise HTTPException(502, "Could not load that website")
        out = free_signals.ad_footprint(html)
    if settings.meta_ad_library_token and page.strip():
        out["meta_ad_library"] = signals_ext.running_ads(
            page.strip(), country if re.fullmatch(r"[a-zA-Z]{2}", country) else "GB")
    return out


class ReviewsPayload(BaseModel):
    reviews: list[str | dict] = Field(max_length=500)


@router.post("/reviews/analyse")
def analyse_reviews(p: ReviewsPayload):
    return reviewmine.analyse(p.reviews)


@router.get("/lookalikes")
def lookalikes(limit: int = 25):
    scope, params = lead_scope()
    cols = "id, business_name, niche, city, website_url, google_rating, google_reviews"
    won = query(f"SELECT {cols} FROM leads WHERE status = 'WON' AND {scope}", tuple(params))
    if not won:
        return {"lookalikes": [], "note": "Mark some leads WON first - lookalikes are modelled on them."}
    pool = query(f"SELECT {cols} FROM leads WHERE status IN ('NEW','CONTACTED') AND {scope} "
                 "ORDER BY id DESC LIMIT 2000", tuple(params))
    return {"lookalikes": lookalike.rank(won, pool, max(1, min(limit, 100))), "based_on": len(won)}


@router.get("/intent-feed")
def get_intent_feed(offer: str = "", days: int = 30):
    """Public posts where people ask for what you sell. Offers default to your saved profile."""
    offers = [o.strip() for o in offer.split(",") if o.strip()]
    if not offers:
        from .fit import load_icp
        offers = (load_icp() or {}).get("offerings") or []
    if not offers:
        raise HTTPException(400, "Say what you sell (e.g. 'web design') or save an ideal-client profile first")
    return {**intent_feed.search(offers[:3], max(1, min(days, 365))), "offers": offers[:3]}


class IntentLeadPayload(BaseModel):
    author: str = Field(min_length=1, max_length=80)
    url: str = Field(max_length=300)
    snippet: str = Field("", max_length=400)
    offer: str = Field("", max_length=80)
    source: str = Field("Hacker News", pattern="^(Hacker News|Mastodon)$")


@router.post("/intent-feed/lead")
def intent_to_lead(p: IntentLeadPayload, request: Request):
    """Turn a public request-for-help post into a lead (deduped by author)."""
    if not p.url.startswith("https://") or (p.source == "Hacker News" and not p.url.startswith("https://news.ycombinator.com/")):
        raise HTTPException(400, "That doesn't look like a link to the original public post")
    user = getattr(request.state, "user", None)
    cand = {"business_name": f"{p.author} ({p.source})", "niche": p.offer or "intent post",
            "source": "Intent post", "business_status": "OPERATIONAL"}
    result = importer.import_candidates([cand], p.offer or "intent post", user["id"] if user else None, enrich=False)
    from ..db import execute
    note = f"Asked for help publicly: {p.snippet}\n{p.url}"
    for lead_id in result.get("lead_ids", []):
        execute("UPDATE leads SET notes = ? WHERE id = ? AND workspace_id = ?", (note, lead_id, current_ws()))
    return result
