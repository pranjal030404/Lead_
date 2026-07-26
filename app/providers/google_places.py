"""Google Places API (New) with staged, tier-aware calling.

COST NOTE - read before scaling usage
-------------------------------------
The old flat $200/month credit was retired in March 2025. Billing is now a
per-SKU free monthly threshold, and *the fields you request decide the SKU* -
one Enterprise field makes the whole call bill at Enterprise rates.

The exact field-to-tier mapping moves around between Google's pricing updates.
As of writing, for Text Search, `displayName` / `formattedAddress` / `location`
are Pro-tier, while phone, website and rating are Enterprise-tier; for Place
Details, phone / website / hours / rating sit in Enterprise. The field masks
below are grouped so you can re-check them against Google's live pricing page
and move a field between stages without touching any other code.

What actually saves money here is the staging, not the exact labels: we never
fetch details for a place we already have, and never fetch ratings for a lead
that already looks cold.
"""

from __future__ import annotations

from .. import http
from ..config import settings
from ..quota import check_and_reserve
from .base import blank_candidate

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"

# Stage 1 - identity only: enough to dedupe against what we already have.
STAGE1_FIELDS = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.addressComponents",
    "nextPageToken",
])

# Stage 2 - the fields that make a lead actionable.
STAGE2_FIELDS = ",".join([
    "nationalPhoneNumber",
    "internationalPhoneNumber",
    "websiteUri",
    "businessStatus",
    "googleMapsUri",
    "primaryTypeDisplayName",
])

# Stage 3 - social proof. Only worth paying for on leads that already look good.
STAGE3_FIELDS = "rating,userRatingCount"

_COMPONENT_MAP = {
    "postal_code": "pincode",
    "administrative_area_level_1": "state",
    "country": "country",
    "locality": "city",
    "sublocality_level_1": "area",
    "sublocality": "area",
    "neighborhood": "area",
}


def _headers(field_mask: str) -> dict:
    return {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": settings.google_api_key,
        "X-Goog-FieldMask": field_mask,
    }


def _apply_components(candidate: dict, components: list[dict]) -> None:
    for comp in components or []:
        for kind in comp.get("types", []):
            field = _COMPONENT_MAP.get(kind)
            if field and not candidate.get(field):
                candidate[field] = comp.get("longText") or comp.get("shortText")


class GooglePlacesProvider:
    name = "google"
    billed = True

    def search(self, niche, city, area="", country="", max_results=20,
               lat=None, lng=None, radius_m=None):
        query_parts = [niche, "in"]
        if area:
            query_parts.append(area)
        query_parts.append(city)
        if country:
            query_parts.append(country)
        text_query = " ".join(p for p in query_parts if p)

        results: list[dict] = []
        page_token = None
        max_results = min(max_results, settings.max_results_per_search)

        while len(results) < max_results:
            payload = {
                "textQuery": text_query,
                "maxResultCount": min(20, max_results - len(results)),
            }
            if lat is not None and lng is not None:
                # locationBias, not locationRestriction: a hard restriction
                # silently returns nothing when the radius is tighter than the
                # nearest match, which reads as "no businesses here" rather than
                # "look wider". Bias ranks nearby first and still returns.
                payload["locationBias"] = {
                    "circle": {
                        "center": {"latitude": float(lat), "longitude": float(lng)},
                        "radius": float(radius_m or settings.nearby_radius_m),
                    }
                }
            if page_token:
                payload["pageToken"] = page_token

            check_and_reserve("google_places", tier="stage1_search")
            response = http.post(
                SEARCH_URL, breaker="google_places", headers=_headers(STAGE1_FIELDS), json=payload
            )
            if response.status_code >= 400:
                raise RuntimeError(f"Places search failed ({response.status_code}): {response.text[:300]}")
            data = response.json()

            for place in data.get("places", []):
                candidate = blank_candidate()
                candidate.update(
                    place_id=place.get("id"),
                    business_name=(place.get("displayName") or {}).get("text"),
                    address=place.get("formattedAddress"),
                    lat=(place.get("location") or {}).get("latitude"),
                    lng=(place.get("location") or {}).get("longitude"),
                    city=city,
                    area=area or None,
                    country=country or None,
                    source="Google Maps",
                )
                _apply_components(candidate, place.get("addressComponents", []))
                candidate["city"] = candidate.get("city") or city
                if candidate["business_name"]:
                    results.append(candidate)

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        return results[:max_results]

    def fetch_details(self, candidate: dict) -> dict:
        place_id = candidate.get("place_id")
        if not place_id:
            return {}
        check_and_reserve("google_places", tier="stage2_details")
        response = http.get(
            DETAILS_URL.format(place_id=place_id),
            breaker="google_places",
            headers=_headers(STAGE2_FIELDS),
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Place details failed ({response.status_code}): {response.text[:300]}")
        data = response.json()
        return {
            "phone": data.get("nationalPhoneNumber") or data.get("internationalPhoneNumber"),
            "website_url": data.get("websiteUri"),
            "business_status": data.get("businessStatus") or "OPERATIONAL",
            "google_maps_url": data.get("googleMapsUri"),
            "business_type": data.get("primaryTypeDisplayName", {}).get("text"),
        }

    def fetch_ratings(self, candidate: dict) -> dict:
        place_id = candidate.get("place_id")
        if not place_id:
            return {}
        check_and_reserve("google_places", tier="stage3_ratings")
        response = http.get(
            DETAILS_URL.format(place_id=place_id),
            breaker="google_places",
            headers=_headers(STAGE3_FIELDS),
        )
        if response.status_code >= 400:
            return {}
        data = response.json()
        return {
            "google_rating": data.get("rating"),
            "google_reviews": data.get("userRatingCount"),
        }
