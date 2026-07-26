"""Provider interface.

Discovery is deliberately split into three stages so the expensive fields are
only ever fetched for places that survived the cheap filters (Part 9.2):

    search()         cheap fields, enough to dedupe          - every result
    fetch_details()  phone / website / status                - new places only
    fetch_ratings()  rating / review count                   - promising leads only

Providers with flat-rate or free data implement the last two as no-ops.
"""

from __future__ import annotations

from typing import Protocol


class Provider(Protocol):
    name: str
    billed: bool

    def search(
        self,
        niche: str,
        city: str,
        area: str = "",
        country: str = "",
        max_results: int = 20,
        lat: float | None = None,
        lng: float | None = None,
        radius_m: int | None = None,
    ) -> list[dict]:
        """Stage 1. Return candidate dicts with at least business_name + place_id.

        When lat/lng are given the search is centred on that point rather than on
        a geocoded city name - that's what "search near me" uses.
        """

    def fetch_details(self, candidate: dict) -> dict:
        """Stage 2. Return extra fields to merge into the candidate."""

    def fetch_ratings(self, candidate: dict) -> dict:
        """Stage 3. Return rating fields to merge into the candidate."""


def blank_candidate() -> dict:
    return {
        "place_id": None,
        "business_name": None,
        "address": None,
        "city": None,
        "area": None,
        "pincode": None,
        "state": None,
        "country": None,
        "lat": None,
        "lng": None,
        "phone": None,
        "email": None,
        "website_url": None,
        "business_status": "OPERATIONAL",
        "google_maps_url": None,
        "google_rating": None,
        "google_reviews": None,
        "source": None,
    }
