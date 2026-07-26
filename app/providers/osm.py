"""OpenStreetMap via Nominatim + Overpass (Part 9.4).

Completely free, no key, no billing. Data density varies a lot by region -
excellent in most of Europe and urban USA, patchier in parts of India - so
spot-check your target city before you rely on it. It has no ratings or
reviews, but it often does carry phone and website tags, which are exactly the
fields that cost the most on Google.

Both endpoints are community-run. We send a real User-Agent and keep volume
modest, per their usage policies.
"""

from __future__ import annotations

import time

from .. import http
from ..quota import record_free_call
from .base import blank_candidate

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# niche keyword -> OSM tag filters. First match on a substring wins.
NICHE_TAGS: list[tuple[tuple[str, ...], list[str]]] = [
    (("restaurant", "dhaba", "eatery", "diner"), ['["amenity"="restaurant"]']),
    (("cafe", "coffee"), ['["amenity"="cafe"]']),
    (("bakery",), ['["shop"="bakery"]']),
    (("bar", "pub"), ['["amenity"="bar"]', '["amenity"="pub"]']),
    (("hotel", "lodge"), ['["tourism"="hotel"]', '["tourism"="guest_house"]']),
    (("gym", "fitness"), ['["leisure"="fitness_centre"]']),
    (("yoga",), ['["leisure"="sports_centre"]["sport"="yoga"]']),
    (("spa",), ['["leisure"="spa"]', '["shop"="beauty"]']),
    (("salon", "hairdress", "barber", "parlour", "parlor"), ['["shop"="hairdresser"]', '["shop"="beauty"]']),
    (("dentist", "dental"), ['["amenity"="dentist"]', '["healthcare"="dentist"]']),
    (("clinic", "doctor", "medical"), ['["amenity"="clinic"]', '["amenity"="doctors"]']),
    (("hospital",), ['["amenity"="hospital"]']),
    (("pharmacy", "chemist", "medical store"), ['["amenity"="pharmacy"]']),
    (("vet",), ['["amenity"="veterinary"]']),
    (("lawyer", "legal", "advocate", "law firm"), ['["office"="lawyer"]']),
    (("accountant", "chartered"), ['["office"="accountant"]']),
    (("real estate", "realtor", "property"), ['["office"="estate_agent"]']),
    (("insurance",), ['["office"="insurance"]']),
    (("travel", "tour"), ['["shop"="travel_agency"]']),
    (("plumber",), ['["craft"="plumber"]']),
    (("electrician",), ['["craft"="electrician"]']),
    (("carpenter",), ['["craft"="carpenter"]']),
    (("builder", "contractor"), ['["craft"="builder"]']),
    (("car repair", "garage", "mechanic", "workshop"), ['["shop"="car_repair"]']),
    (("car deal", "automobile"), ['["shop"="car"]']),
    (("school", "coaching", "tuition", "institute"), ['["amenity"="school"]', '["amenity"="college"]']),
    (("driving school",), ['["amenity"="driving_school"]']),
    (("boutique", "clothing", "apparel", "garment", "fashion"), ['["shop"="clothes"]']),
    (("jewel", "jewellery", "jewelry"), ['["shop"="jewelry"]']),
    (("furniture",), ['["shop"="furniture"]']),
    (("hardware",), ['["shop"="hardware"]', '["shop"="doityourself"]']),
    (("florist", "flower"), ['["shop"="florist"]']),
    (("photo", "studio"), ['["shop"="photo"]', '["craft"="photographer"]']),
    (("optician", "eyewear", "spectacle"), ['["shop"="optician"]']),
    (("bank",), ['["amenity"="bank"]']),
    (("supermarket", "grocery", "kirana"), ['["shop"="supermarket"]', '["shop"="convenience"]']),
    (("bookshop", "bookstore", "stationery"), ['["shop"="books"]', '["shop"="stationery"]']),
    (("pet",), ['["shop"="pet"]']),
    (("laundry", "dry clean"), ['["shop"="laundry"]', '["shop"="dry_cleaning"]']),
]


def tags_for_niche(niche: str) -> list[str]:
    lowered = niche.lower()
    for keywords, filters in NICHE_TAGS:
        if any(word in lowered for word in keywords):
            return filters
    # Unknown niche: fall back to a name match over anything with a name.
    escaped = niche.replace('"', "").replace("\\", "")
    return [f'["name"~"{escaped}",i]']


class OverpassProvider:
    name = "osm"
    billed = False

    def _geocode(self, city: str, country: str = "") -> tuple[float, float]:
        query = ", ".join(p for p in (city, country) if p)
        record_free_call("nominatim")
        response = http.get(
            NOMINATIM_URL,
            breaker="nominatim",
            params={"q": query, "format": "json", "limit": 1},
            timeout=15,
        )
        data = response.json() if response.status_code == 200 else []
        if not data:
            raise RuntimeError(
                f"Could not locate '{query}' on OpenStreetMap. Check the city spelling, "
                "or add the country."
            )
        time.sleep(1.0)  # Nominatim usage policy: max 1 request/second
        return float(data[0]["lat"]), float(data[0]["lon"])

    def search(self, niche, city, area="", country="", max_results=20,
               lat=None, lng=None, radius_m=None):
        if lat is not None and lng is not None:
            # Searching from a known point (the operator's current location, or
            # a map pin) - no geocoding round trip needed at all.
            lat, lon = float(lat), float(lng)
            radius = radius_m or 4000
        else:
            lat, lon = self._geocode(f"{area}, {city}" if area else city, country)
            radius = radius_m or (4000 if area else 12000)
        filters = tags_for_niche(niche)
        clauses = "".join(f"  nwr{f}(around:{radius},{lat},{lon});\n" for f in filters)
        overpass_query = f"[out:json][timeout:60];\n(\n{clauses});\nout center tags {max_results * 2};"

        record_free_call("overpass")
        response = http.post(
            OVERPASS_URL, breaker="overpass", data={"data": overpass_query}, timeout=60,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Overpass query failed ({response.status_code}): {response.text[:200]}")

        results = []
        for element in response.json().get("elements", []):
            tags = element.get("tags", {})
            name = tags.get("name")
            if not name:
                continue

            street = " ".join(
                p for p in (tags.get("addr:housenumber"), tags.get("addr:street")) if p
            )
            address = ", ".join(
                p for p in (street, tags.get("addr:suburb"), tags.get("addr:city") or city,
                            tags.get("addr:postcode")) if p
            )
            centre = element.get("center") or {}
            candidate = blank_candidate()
            candidate.update(
                place_id=f"osm:{element['type']}/{element['id']}",
                business_name=name,
                address=address or None,
                city=tags.get("addr:city") or city,
                area=tags.get("addr:suburb") or area or None,
                pincode=tags.get("addr:postcode"),
                state=tags.get("addr:state"),
                country=country or None,
                lat=element.get("lat") or centre.get("lat"),
                lng=element.get("lon") or centre.get("lon"),
                phone=tags.get("phone") or tags.get("contact:phone") or tags.get("contact:mobile"),
                email=tags.get("email") or tags.get("contact:email"),
                website_url=tags.get("website") or tags.get("contact:website"),
                business_status="OPERATIONAL",
                source="OpenStreetMap",
            )
            if tags.get("contact:instagram"):
                candidate["instagram_url"] = tags["contact:instagram"]
            if tags.get("contact:facebook"):
                candidate["facebook_url"] = tags["contact:facebook"]
            if candidate["lat"] and candidate["lng"]:
                candidate["google_maps_url"] = (
                    f"https://www.google.com/maps/search/?api=1&query="
                    f"{candidate['lat']},{candidate['lng']}"
                )
            results.append(candidate)

        return results[:max_results]

    def fetch_details(self, candidate: dict) -> dict:
        return {}  # Overpass returns everything it has in one call.

    def fetch_ratings(self, candidate: dict) -> dict:
        return {}  # OSM has no ratings or reviews.
