"""Nominatim place search - a free, key-less second map source.

Where the Overpass provider asks "give me everything tagged X near here", this asks the
OpenStreetMap geocoder to rank places for a text query ("dentist Koregaon Park Pune"), which
surfaces businesses Overpass tag-matching misses (unusual niches, name-only matches).
IDs use the same `osm:<type>/<id>` form as the Overpass provider, so the same business found
by both is merged by dedup instead of duplicated.

Usage policy: <= 1 request/second, identifying User-Agent (http.py adds one), no bulk
harvesting. We make one request per search.
"""

from __future__ import annotations

import math
import time

from .. import http
from ..quota import record_free_call
from .base import blank_candidate

URL = "https://nominatim.openstreetmap.org/search"


def viewbox(lat: float, lng: float, radius_m: int) -> str:
    """left,top,right,bottom box around a point (what Nominatim's `viewbox` expects)."""
    dlat = math.degrees(radius_m / 6_371_000)
    dlng = math.degrees(radius_m / (6_371_000 * max(0.01, math.cos(math.radians(lat)))))
    return f"{lng - dlng:.6f},{lat + dlat:.6f},{lng + dlng:.6f},{lat - dlat:.6f}"


def parse_result(item: dict, city: str, country: str) -> dict | None:
    """Pure: one Nominatim JSON record -> candidate (None if it has no usable name)."""
    name = item.get("name") or (item.get("namedetails") or {}).get("name")
    if not name:
        return None
    tags, addr = item.get("extratags") or {}, item.get("address") or {}
    c = blank_candidate()
    c.update(
        place_id=f"osm:{item.get('osm_type')}/{item.get('osm_id')}",
        business_name=name, address=item.get("display_name"),
        city=addr.get("city") or addr.get("town") or addr.get("village") or addr.get("suburb") or city,
        area=addr.get("suburb") or addr.get("neighbourhood"), pincode=addr.get("postcode"),
        state=addr.get("state"), country=addr.get("country") or country or None,
        lat=float(item["lat"]) if item.get("lat") else None,
        lng=float(item["lon"]) if item.get("lon") else None,
        phone=tags.get("phone") or tags.get("contact:phone") or tags.get("contact:mobile"),
        email=tags.get("email") or tags.get("contact:email"),
        website_url=tags.get("website") or tags.get("contact:website") or tags.get("url"),
        source="OpenStreetMap (Nominatim)")
    if tags.get("contact:instagram"):
        c["instagram_url"] = tags["contact:instagram"]
    if tags.get("contact:facebook"):
        c["facebook_url"] = tags["contact:facebook"]
    if c["lat"] and c["lng"]:
        c["google_maps_url"] = f"https://www.google.com/maps/search/?api=1&query={c['lat']},{c['lng']}"
    return c


class NominatimProvider:
    name = "nominatim"
    billed = False

    def search(self, niche, city, area="", country="", max_results=20,
               lat=None, lng=None, radius_m=None):
        params = {"format": "jsonv2", "limit": min(max(max_results, 1), 40), "extratags": 1,
                  "addressdetails": 1, "namedetails": 1, "dedupe": 1}
        if lat is not None and lng is not None:
            params["q"] = niche
            params["viewbox"] = viewbox(float(lat), float(lng), radius_m or 4000)
            params["bounded"] = 1
        else:
            params["q"] = " ".join(p for p in (niche, area, city, country) if p)
        record_free_call("nominatim")
        r = http.get(URL, breaker="nominatim", params=params, timeout=20)
        if r.status_code >= 400:
            raise RuntimeError(f"Nominatim error {r.status_code}: {r.text[:200]}")
        time.sleep(1.0)                      # usage policy: max 1 request/second
        out = []
        for item in r.json():
            # Only businesses: skip streets, districts, admin areas.
            if item.get("category") not in ("amenity", "shop", "office", "craft", "tourism",
                                            "leisure", "healthcare", "man_made", "building"):
                continue
            cand = parse_result(item, city, country)
            if cand:
                out.append(cand)
        return out

    def fetch_details(self, candidate):
        return {}

    def fetch_ratings(self, candidate):
        return {}
