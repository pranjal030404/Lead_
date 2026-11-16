"""Foursquare Places (v3) - a second map source that finds businesses Google misses."""

from __future__ import annotations

from .. import http
from ..config import settings
from .base import blank_candidate

URL = "https://api.foursquare.com/v3/places/search"


class FoursquareProvider:
    name = "foursquare"
    billed = False

    def search(self, niche, city, area="", country="", max_results=20,
               lat=None, lng=None, radius_m=None):
        params = {"query": niche, "limit": min(max_results, 50),
                  "fields": "fsq_id,name,location,tel,website,email,geocodes,social_media,rating,stats,closed_bucket"}
        if lat is not None and lng is not None:
            params.update(ll=f"{lat},{lng}", radius=min(radius_m or 4000, 100000))
        else:
            params["near"] = ", ".join(p for p in (area, city, country) if p)
        r = http.get(URL, breaker="foursquare", params=params,
                     headers={"Authorization": settings.foursquare_api_key, "Accept": "application/json"})
        if r.status_code >= 400:
            raise RuntimeError(f"Foursquare error {r.status_code}: {r.text[:200]}")
        out = []
        for p in r.json().get("results", []):
            loc, geo = p.get("location") or {}, (p.get("geocodes") or {}).get("main") or {}
            social = p.get("social_media") or {}
            c = blank_candidate()
            c.update(place_id=f"fsq:{p.get('fsq_id')}", business_name=p.get("name"),
                     address=loc.get("formatted_address"), city=loc.get("locality") or city,
                     pincode=loc.get("postcode"), state=loc.get("region"),
                     country=loc.get("country") or country or None,
                     lat=geo.get("latitude"), lng=geo.get("longitude"),
                     phone=p.get("tel"), email=p.get("email"), website_url=p.get("website"),
                     google_rating=round(p["rating"] / 2, 1) if p.get("rating") else None,
                     google_reviews=(p.get("stats") or {}).get("total_ratings"),
                     business_status="CLOSED_PERMANENTLY" if p.get("closed_bucket") == "VeryLikelyClosed" else "OPERATIONAL",
                     source="Foursquare")
            if social.get("instagram"):
                c["instagram_url"] = f"https://instagram.com/{social['instagram']}"
            if social.get("facebook_id"):
                c["facebook_url"] = f"https://facebook.com/{social['facebook_id']}"
            if c["business_name"]:
                out.append(c)
        return out

    def fetch_details(self, candidate):
        return {}

    def fetch_ratings(self, candidate):
        return {}
