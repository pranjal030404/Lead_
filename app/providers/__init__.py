"""Pluggable business-discovery providers."""

from __future__ import annotations

from ..config import settings
from .base import Provider
from .google_places import GooglePlacesProvider
from .mock import MockProvider
from .osm import OverpassProvider

_REGISTRY: dict[str, type[Provider]] = {
    "google": GooglePlacesProvider,
    "osm": OverpassProvider,
    "mock": MockProvider,
}


def get_provider(name: str | None = None) -> Provider:
    key = (name or settings.provider or "osm").lower()
    if key == "google" and not settings.google_api_key:
        raise RuntimeError(
            "PROVIDER=google but GOOGLE_API_KEY is not set. "
            "Add the key to .env, or use PROVIDER=osm (free) / PROVIDER=mock (offline)."
        )
    if key not in _REGISTRY:
        raise RuntimeError(f"Unknown provider '{key}'. Options: {', '.join(_REGISTRY)}")
    return _REGISTRY[key]()


def available_providers() -> list[dict]:
    return [
        {"key": "osm", "label": "OpenStreetMap (free, no key)", "ready": True},
        {"key": "google", "label": "Google Places API (New)", "ready": bool(settings.google_api_key)},
        {"key": "mock", "label": "Mock data (offline demo)", "ready": True},
    ]
