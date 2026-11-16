"""CombinedProvider: runs several providers for one country and merges the results.

Used for "keyed + keyless together" (uk_all = Companies House API + bulk file,
india_all = data.gov.in API + local MCA CSVs). Companies are keyed by the same
place_id in both sources, so duplicates collapse. One source failing (missing
key, empty folder, API down) never loses the others' results; it only raises if
every source failed."""

from __future__ import annotations


class CombinedProvider:
    billed = False

    def __init__(self, name: str, members: list):
        self.name = name
        self.members = members

    def search(self, niche, city, area="", country="", max_results=20,
               lat=None, lng=None, radius_m=None):
        seen: dict[str, dict] = {}
        errors: list[str] = []
        for p in self.members:
            try:
                for c in p.search(niche, city, area, country, max_results, lat, lng, radius_m):
                    seen.setdefault(c.get("place_id") or c["business_name"], c)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{p.name}: {exc}")
        if not seen and errors and len(errors) == len(self.members):
            raise RuntimeError("; ".join(errors))
        return list(seen.values())[:max_results]

    def fetch_details(self, candidate):
        return {}

    def fetch_ratings(self, candidate):
        return {}
