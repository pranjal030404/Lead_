"""Turning coordinates into places, and places into coordinates.

Used by "search near me": the browser hands over a latitude and longitude, and
this names the area so the search, the coverage tracker and the lead records all
read like a human wrote them rather than storing bare decimals.

Two backends, cheapest first (Part 9.3):

  - **Nominatim** (OpenStreetMap) - free, no key, the default. Its usage policy
    asks for a real User-Agent and at most one request a second, both of which
    `http.py` and the caller respect.
  - **Google Geocoding** - used only when GOOGLE_API_KEY is set *and*
    GEOCODER=google. It bills per call, so it is never the default even when a
    key exists for Places.

Reverse geocoding one point per search is negligible either way; this exists so
the choice is explicit rather than accidental.
"""

from __future__ import annotations

from . import http
from .config import settings
from .quota import QuotaExceeded, check_and_reserve, record_free_call

NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"
GOOGLE_GEOCODE = "https://maps.googleapis.com/maps/api/geocode/json"

# Nominatim reports the settlement under whichever of these it has.
CITY_KEYS = ("city", "town", "village", "municipality", "county")
AREA_KEYS = ("suburb", "neighbourhood", "city_district", "quarter", "hamlet")


class GeocodeError(RuntimeError):
    pass


def _blank() -> dict:
    return {"city": None, "area": None, "state": None, "country": None,
            "pincode": None, "display": None, "source": None}


def _use_google() -> bool:
    return settings.geocoder == "google" and bool(settings.google_api_key)


# ------------------------------------------------------------ reverse --

def _reverse_nominatim(lat: float, lng: float) -> dict:
    record_free_call("nominatim")
    response = http.get(
        NOMINATIM_REVERSE, breaker="nominatim", timeout=15,
        params={"lat": lat, "lon": lng, "format": "jsonv2", "zoom": 16,
                "addressdetails": 1},
    )
    if response.status_code >= 400:
        raise GeocodeError(f"Nominatim reverse lookup failed ({response.status_code})")
    payload = response.json()
    address = payload.get("address") or {}
    result = _blank()
    result.update(
        city=next((address[k] for k in CITY_KEYS if address.get(k)), None),
        area=next((address[k] for k in AREA_KEYS if address.get(k)), None),
        state=address.get("state"),
        country=address.get("country"),
        pincode=address.get("postcode"),
        display=payload.get("display_name"),
        source="OpenStreetMap",
    )
    return result


def _reverse_google(lat: float, lng: float) -> dict:
    check_and_reserve("google_geocode", tier="geocoding")
    response = http.get(
        GOOGLE_GEOCODE, breaker="google_geocode", timeout=15,
        params={"latlng": f"{lat},{lng}", "key": settings.google_api_key},
    )
    payload = response.json() if response.status_code == 200 else {}
    if payload.get("status") not in ("OK", "ZERO_RESULTS"):
        raise GeocodeError(
            f"Google geocoding failed: {payload.get('status')} "
            f"{payload.get('error_message', '')}".strip()
        )
    results = payload.get("results") or []
    if not results:
        raise GeocodeError("Google returned no address for that point")

    components = results[0].get("address_components", [])

    def part(*types: str) -> str | None:
        for component in components:
            if any(t in component.get("types", []) for t in types):
                return component.get("long_name")
        return None

    result = _blank()
    result.update(
        city=part("locality", "postal_town", "administrative_area_level_2"),
        area=part("sublocality", "sublocality_level_1", "neighborhood"),
        state=part("administrative_area_level_1"),
        country=part("country"),
        pincode=part("postal_code"),
        display=results[0].get("formatted_address"),
        source="Google",
    )
    return result


def reverse(lat: float, lng: float) -> dict:
    """Name the place at a coordinate. Never raises for a missing name - an
    unnamed point still searches fine, it just reads as coordinates."""
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise GeocodeError(f"{lat},{lng} is not a valid coordinate")
    try:
        result = _reverse_google(lat, lng) if _use_google() else _reverse_nominatim(lat, lng)
    except QuotaExceeded:
        raise
    except GeocodeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise GeocodeError(f"Could not look up that location: {exc}") from exc

    if not result["city"]:
        result["city"] = result["area"] or f"{lat:.4f},{lng:.4f}"
    return result


# ------------------------------------------------------------ forward --

def forward(place: str, country: str = "") -> dict:
    """Coordinates for a place name, so a typed city can drive a radius search."""
    query = ", ".join(p for p in (place, country) if p)
    record_free_call("nominatim")
    response = http.get(
        NOMINATIM_SEARCH, breaker="nominatim", timeout=15,
        params={"q": query, "format": "json", "limit": 1},
    )
    data = response.json() if response.status_code == 200 else []
    if not data:
        raise GeocodeError(f"Could not find '{query}'. Check the spelling, or add a country.")
    return {"lat": float(data[0]["lat"]), "lng": float(data[0]["lon"]),
            "display": data[0].get("display_name")}
