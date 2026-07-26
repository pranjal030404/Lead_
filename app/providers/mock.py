"""Offline generator - deterministic fake businesses.

Lets you exercise search, dedup, scoring, the pipeline and the whole automation
engine with no API key, no network and no billing. Same niche + city always
produces the same businesses, so re-running a search is a genuine duplicate
test (Part 7.7).
"""

from __future__ import annotations

import hashlib
import random

from .base import blank_candidate

PREFIXES = ["Sharma", "Gupta", "Royal", "Sunrise", "Elite", "Green", "Prime", "Nova",
            "Urban", "Classic", "Golden", "Silver", "Metro", "Star", "Apex", "Crown",
            "Blue Ridge", "Oakwood", "Riverside", "Summit"]
SUFFIXES = ["Hub", "House", "Point", "Corner", "Studio", "Works", "Centre", "Co",
            "& Sons", "Express", "Junction", "Place"]
AREAS = ["Sector 15", "Sector 21", "Old Town", "Main Market", "Civil Lines",
         "Model Town", "Industrial Area", "New Colony"]
PLATFORM_SITES = ["wixsite.com", "business.site", "wordpress.com", "squarespace.com"]


class MockProvider:
    name = "mock"
    billed = False

    def search(self, niche, city, area="", country="", max_results=20,
               lat=None, lng=None, radius_m=None):
        seed = int(hashlib.sha256(f"{niche}|{city}|{area}".encode()).hexdigest()[:12], 16)
        rng = random.Random(seed)
        # Scatter around the requested point when there is one, so the map view
        # has something plausible to draw in offline demos.
        base_lat = float(lat) if lat is not None else 28.4
        base_lng = float(lng) if lng is not None else 77.2
        spread = (radius_m or 3000) / 111_000
        singular = niche.rstrip("s").title()
        results = []

        for i in range(max_results):
            name = f"{rng.choice(PREFIXES)} {singular} {rng.choice(SUFFIXES)}"
            slug = name.lower().replace(" ", "").replace("&", "")[:18]
            has_site = rng.random() < 0.45
            candidate = blank_candidate()
            candidate.update(
                place_id=f"mock:{seed}:{i}",
                business_name=name,
                address=f"{rng.randint(1, 260)}, {rng.choice(AREAS)}, {city}",
                city=city,
                area=area or rng.choice(AREAS),
                pincode=str(rng.randint(110001, 121999)),
                country=country or "India",
                lat=round(base_lat + rng.uniform(-spread, spread), 6),
                lng=round(base_lng + rng.uniform(-spread, spread), 6),
                phone=f"9{rng.randint(100000000, 999999999)}",
                website_url=(
                    f"https://{slug}.{rng.choice(PLATFORM_SITES)}" if has_site else None
                ),
                business_status="CLOSED_PERMANENTLY" if rng.random() < 0.05 else "OPERATIONAL",
                google_rating=round(rng.uniform(3.2, 4.9), 1),
                google_reviews=rng.randint(0, 320),
                source="Mock",
            )
            results.append(candidate)
        return results

    def fetch_details(self, candidate: dict) -> dict:
        return {}

    def fetch_ratings(self, candidate: dict) -> dict:
        return {}
