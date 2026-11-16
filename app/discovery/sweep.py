"""SweepProvider: wraps any provider and runs it over a grid of cells."""

from __future__ import annotations

from ..providers.base import Provider
from .grid import grid_cells


class SweepProvider:
    billed = False

    def __init__(self, inner: Provider, cell_radius_m: int = 1500, max_cells: int = 25):
        self.inner = inner
        self.name = inner.name          # lineage / quota keep attributing to the real source
        self.billed = getattr(inner, "billed", False)
        self.cell_radius_m = cell_radius_m
        self.max_cells = max_cells

    def search(self, niche, city, area="", country="", max_results=20,
               lat=None, lng=None, radius_m=None):
        if lat is None or lng is None:
            # Resolve the centre once through the inner provider's own geocoder.
            geocode = getattr(self.inner, "_geocode", None)
            if geocode is None:
                return self.inner.search(niche, city, area, country, max_results)
            lat, lng = geocode(f"{area}, {city}" if area else city, country)
        radius = radius_m or 8000
        seen: dict[str, dict] = {}
        for cell in grid_cells(lat, lng, radius, self.cell_radius_m, self.max_cells):
            if len(seen) >= max_results:
                break
            try:
                found = self.inner.search(niche, city, area, country, max_results,
                                          lat=cell["lat"], lng=cell["lng"],
                                          radius_m=cell["radius_m"])
            except Exception:  # noqa: BLE001 - one bad cell must not lose the sweep
                continue
            for cand in found:
                key = cand.get("place_id") or f'{cand.get("business_name")}|{cand.get("address")}'
                seen.setdefault(key, cand)
        return list(seen.values())[:max_results]

    def fetch_details(self, candidate):
        return self.inner.fetch_details(candidate)

    def fetch_ratings(self, candidate):
        return self.inner.fetch_ratings(candidate)
