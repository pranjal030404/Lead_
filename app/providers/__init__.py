"""Pluggable business-discovery providers."""

from __future__ import annotations

from ..config import settings
from .base import Provider
from .google_places import GooglePlacesProvider
from .mock import MockProvider
from .foursquare import FoursquareProvider
from .nominatim import NominatimProvider
from .overture import OvertureProvider
from .osm_new import NewOpeningsProvider
from .companies_house import CompaniesHouseProvider
from .opencorporates import OpenCorporatesProvider
from .india_mca import IndiaMcaProvider
from .india_csv import IndiaCsvProvider
from .uk_bulk import UkBulkProvider
from .combined import CombinedProvider
from .osm import OverpassProvider

_REGISTRY: dict[str, type[Provider]] = {
    "google": GooglePlacesProvider,
    "osm": OverpassProvider,
    "mock": MockProvider,
    "nominatim": NominatimProvider,
    "overture": OvertureProvider,
    "osm_new": NewOpeningsProvider,
    "foursquare": FoursquareProvider,
    "companies_house": CompaniesHouseProvider,
    "opencorporates": OpenCorporatesProvider,
    "india_mca": IndiaMcaProvider,
    "india_csv": IndiaCsvProvider,
    "uk_bulk": UkBulkProvider,
}

_KEYS = {"google": "google_api_key", "foursquare": "foursquare_api_key",
         "companies_house": "companies_house_api_key",
         "opencorporates": "opencorporates_api_key",
         "india_mca": "data_gov_in_api_key"}


def get_provider(name: str | None = None) -> Provider:
    """`name` may carry a "+grid" suffix (e.g. "osm+grid") to sweep a grid of cells."""
    key = (name or settings.provider or "osm").lower()
    sweep = key.endswith("+grid")
    key = key.removesuffix("+grid")
    if key in ("uk_all", "india_all"):
        return _combined(key)
    if key not in _REGISTRY:
        raise RuntimeError(f"Unknown provider '{key}'. Options: {', '.join(_REGISTRY)}")
    attr = _KEYS.get(key)
    if attr and not getattr(settings, attr):
        raise RuntimeError(f"PROVIDER={key} but {attr.upper()} is not set. Add it to .env, "
                           "or use PROVIDER=osm (free) / PROVIDER=mock (offline).")
    inner = _REGISTRY[key]()
    if sweep:
        from ..discovery.sweep import SweepProvider
        return SweepProvider(inner, settings.sweep_cell_radius_m, settings.sweep_max_cells)
    return inner


def _combined(key: str) -> Provider:
    """Keyed source (when its key is set) + keyless source, merged."""
    if key == "uk_all":
        members = ([CompaniesHouseProvider()] if settings.companies_house_api_key else []) + [UkBulkProvider()]
    else:
        members = ([IndiaMcaProvider()] if settings.data_gov_in_api_key
                   and settings.data_gov_in_mca_resources else []) + [IndiaCsvProvider()]
    return CombinedProvider(key, members)


def _has_duckdb() -> bool:
    try:
        import duckdb  # noqa: F401
        return True
    except ImportError:
        return False


def available_providers() -> list[dict]:
    rows = [
        {"key": "osm", "label": "OpenStreetMap (free, no key)", "ready": True},
        {"key": "osm+grid", "label": "OpenStreetMap - grid sweep (more results)", "ready": True},
        {"key": "google", "label": "Google Places API (New)", "ready": bool(settings.google_api_key)},
        {"key": "google+grid", "label": "Google Places - grid sweep", "ready": bool(settings.google_api_key)},
        {"key": "nominatim", "label": "OpenStreetMap text search (free, 2nd source)", "ready": True},
        {"key": "overture", "label": "Overture Maps - open data with phones & emails (slower)",
         "ready": _has_duckdb()},
        {"key": "osm_new", "label": "New openings (recently mapped, free)", "ready": True},
        {"key": "foursquare", "label": "Foursquare Places (optional, key)", "ready": bool(settings.foursquare_api_key)},
        {"key": "companies_house", "label": "UK Companies House (optional, key)",
         "ready": bool(settings.companies_house_api_key)},
        {"key": "opencorporates", "label": "OpenCorporates (optional, key)",
         "ready": bool(settings.opencorporates_api_key)},
        {"key": "india_mca", "label": "India MCA new companies (free, data.gov.in key)",
         "ready": bool(settings.data_gov_in_api_key and settings.data_gov_in_mca_resources)},
        {"key": "uk_bulk", "label": "UK new companies - bulk file (free, no key, big first download)", "ready": True},
        {"key": "india_csv", "label": "India new companies - MCA CSVs in data/india_mca (free, no key)", "ready": True},
        {"key": "uk_all", "label": "UK new companies - API key + free bulk file together", "ready": True},
        {"key": "india_all", "label": "India new companies - data.gov.in key + MCA CSVs together", "ready": True},
        {"key": "mock", "label": "Mock data (offline demo)", "ready": True},
    ]
    return rows
