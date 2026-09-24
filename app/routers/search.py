from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from .. import geo
from ..config import settings
from ..db import execute, query, query_one, utcnow
from ..fastjson import rows_response
from ..providers import available_providers
from ..quota import usage_summary
from ..search_service import cancel_search, coverage_status, get_coverage, start_search
from ..subscription import check_search_entitlement, max_results_for

router = APIRouter(prefix="/api", tags=["search"])


class SearchPayload(BaseModel):
    niche: str
    city: str = ""
    area: str = ""
    country: str = ""
    max_results: int = 20
    provider: str | None = None
    enrich: bool = True
    # "Near me": the browser's coordinates. When present the city is filled in
    # by reverse geocoding rather than typed.
    lat: float | None = None
    lng: float | None = None
    radius_m: int | None = None


@router.post("/search")
def start(payload: SearchPayload, request: Request):
    if not payload.niche.strip():
        raise HTTPException(400, "niche is required")

    user = getattr(request.state, "user", None)
    entitlement = check_search_entitlement(user or {})
    if not entitlement["ok"]:
        reason = entitlement.get("reason")
        if reason == "search_limit_reached":
            raise HTTPException(403, "Plan search limit reached. Upgrade or wait for renewal.")
        raise HTTPException(403, "An active subscription is required to search leads.")

    city, area, country = payload.city.strip(), payload.area.strip(), payload.country.strip()
    radius = payload.radius_m

    if payload.lat is not None and payload.lng is not None:
        radius = min(radius or settings.nearby_radius_m, settings.max_nearby_radius_m)
        if not city:
            # Name the point so the lead, the search log and the coverage map
            # all read like a place instead of a pair of decimals.
            try:
                place = geo.reverse(payload.lat, payload.lng)
            except geo.GeocodeError as exc:
                raise HTTPException(400, str(exc)) from exc
            city = place["city"]
            area = area or place["area"] or ""
            country = country or place["country"] or ""
    elif not city:
        raise HTTPException(400, "city is required unless you search by location")
    usage = usage_summary()
    if usage["google"]["remaining"] <= 0 and (payload.provider or settings.provider) == "google":
        raise HTTPException(
            429,
            f"Daily API cap reached ({usage['google']['used_today']}/{usage['google']['cap']}). "
            "Raise MAX_GOOGLE_CALLS_PER_DAY or wait until tomorrow.",
        )
    search_id = start_search(
        niche=payload.niche.strip(), city=city, area=area, country=country,
        max_results=max_results_for(user, payload.max_results), provider_name=payload.provider,
        enrich=payload.enrich, triggered_by="manual",
        lat=payload.lat, lng=payload.lng, radius_m=radius,
        user_id=user["id"],
    )
    return {"search_id": search_id, "status": "RUNNING", "city": city,
            "area": area, "radius_m": radius}


@router.get("/geo/reverse")
def geo_reverse(lat: float, lng: float):
    """Name the point the browser handed us, for the 'near me' search."""
    try:
        return geo.reverse(lat, lng)
    except geo.GeocodeError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/geo/map")
def geo_map(limit: int = Query(1000, ge=1, le=5000), priority: str | None = None,
            city: str | None = None, status: str | None = None):
    """Every lead that has coordinates, trimmed to what a map marker needs.

    Deliberately not the full lead row - a thousand complete records is a lot of
    payload to hand a map that only draws a dot and a label.
    """
    where = ["lat IS NOT NULL", "lng IS NOT NULL"]
    params: list = []
    for column, value in (("lead_priority", priority), ("city", city), ("status", status)):
        if value:
            where.append(f"{column} = ?")
            params.append(value)
    rows = query(
        "SELECT id, business_name, lat, lng, lead_score, lead_priority, status, city, "
        "area, phone, has_website, website_url, niche "
        f"FROM leads WHERE {' AND '.join(where)} ORDER BY lead_score DESC LIMIT ?",
        (*params, limit),
    )
    return rows_response({"count": len(rows), "leads": rows})


@router.get("/search/history")
def history(limit: int = Query(30, le=200)):
    return query("SELECT * FROM searches ORDER BY id DESC LIMIT ?", (limit,))


@router.get("/search/preview")
def preview(niche: str, city: str, area: str = "", country: str = ""):
    """What the coverage tracker already knows about this combo (Part 1.1)."""
    coverage = get_coverage(niche, city, area, country)
    return {
        "coverage": coverage,
        "message": {
            "NEVER": "Never searched - highest priority.",
            "FRESH": f"Searched {coverage.get('times_searched', 0)}x, most recently "
                     f"{(coverage.get('last_searched') or '')[:10]}. Probably nothing new yet.",
            "STALE": f"Last searched {(coverage.get('last_searched') or '')[:10]} - "
                     "new businesses may have appeared.",
            "EXHAUSTED": "Searched 3+ times with no new leads. Try a different area.",
        }.get(coverage["status"], ""),
    }


@router.get("/search/{search_id}/status")
def status(search_id: int):
    row = query_one("SELECT * FROM searches WHERE id = ?", (search_id,))
    if not row:
        raise HTTPException(404, "Search not found")
    if row["status"] == "COMPLETED":
        row["leads"] = query(
            "SELECT id, business_name, phone, website_url, lead_score, lead_priority, city "
            "FROM leads WHERE search_id = ? ORDER BY lead_score DESC",
            (search_id,),
        )
    return row


@router.post("/search/{search_id}/cancel")
def cancel(search_id: int):
    return {"cancelled": cancel_search(search_id)}


@router.get("/coverage")
def coverage(country: str | None = None):
    where, params = ("", ())
    if country:
        where, params = " WHERE country = ?", (country,)
    rows = query(f"SELECT * FROM search_coverage{where} ORDER BY city, niche", params)
    for row in rows:
        row["status"] = coverage_status(row)
    cities = sorted({r["city"] for r in rows})
    niches = sorted({r["niche"] for r in rows})
    return {"rows": rows, "cities": cities, "niches": niches}


@router.get("/coverage/{city}")
def coverage_for_city(city: str):
    rows = query("SELECT * FROM search_coverage WHERE city = ? ORDER BY niche", (city,))
    for row in rows:
        row["status"] = coverage_status(row)
    return rows


@router.get("/providers")
def providers():
    return {"active": settings.provider, "available": available_providers()}


@router.get("/quota")
def quota():
    return usage_summary()


# ----------------------------------------------------------------- targets --

class TargetPayload(BaseModel):
    niche: str
    city: str
    area: str = ""
    country: str = ""
    max_results: int = 20
    auto_search: str = "weekly"


@router.get("/targets")
def list_targets():
    rows = query("SELECT * FROM targets ORDER BY is_active DESC, city, niche")
    for row in rows:
        row["coverage"] = get_coverage(row["niche"], row["city"], row["area"], row["country"])
    return rows


@router.post("/targets")
def add_target(payload: TargetPayload):
    if payload.auto_search not in ("daily", "weekly", "monthly", "manual"):
        raise HTTPException(400, "auto_search must be daily, weekly, monthly or manual")
    execute(
        """INSERT IGNORE INTO targets
           (niche, city, area, country, max_results, is_active, auto_search,
            next_search_date, created_at)
           VALUES(?,?,?,?,?,1,?,CURDATE(),?)""",
        (payload.niche.strip(), payload.city.strip(), payload.area.strip(),
         payload.country.strip(), payload.max_results, payload.auto_search, utcnow()),
    )
    return query_one(
        "SELECT * FROM targets WHERE niche = ? AND city = ? AND area = ? AND country = ?",
        (payload.niche.strip(), payload.city.strip(), payload.area.strip(),
         payload.country.strip()),
    )


@router.patch("/targets/{target_id}")
def toggle_target(target_id: int, is_active: bool):
    execute("UPDATE targets SET is_active = ? WHERE id = ?", (1 if is_active else 0, target_id))
    return query_one("SELECT * FROM targets WHERE id = ?", (target_id,))


@router.delete("/targets/{target_id}")
def delete_target(target_id: int):
    execute("DELETE FROM targets WHERE id = ?", (target_id,))
    return {"deleted": target_id}


@router.post("/targets/{target_id}/search-now")
def search_now(target_id: int, request: Request):
    target = query_one("SELECT * FROM targets WHERE id = ?", (target_id,))
    if not target:
        raise HTTPException(404, "Target not found")
    user = getattr(request.state, "user", None)
    entitlement = check_search_entitlement(user or {})
    if not entitlement["ok"]:
        if entitlement.get("reason") == "search_limit_reached":
            raise HTTPException(403, "Plan search limit reached. Upgrade or wait for renewal.")
        raise HTTPException(403, "An active subscription is required to search leads.")
    search_id = start_search(
        niche=target["niche"], city=target["city"], area=target["area"] or "",
        country=target["country"] or "", max_results=max_results_for(user, target["max_results"]),
        triggered_by="target", user_id=user["id"],
    )
    execute("UPDATE targets SET last_searched = ? WHERE id = ?", (utcnow(), target_id))
    return {"search_id": search_id}
