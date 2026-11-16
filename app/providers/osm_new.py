"""New-business detector built on OpenStreetMap edit metadata (free, no key).

A registry tells you who *incorporated*; most small shops never hit a registry we can read.
OSM tells us something different and just as useful: when a place was first *mapped*. A
business that appeared on the map in the last N days and has never been edited since
(version 1) is very likely newly opened - exactly when it needs a website.

Honest limits: "newly mapped" is a proxy for "newly opened" (a mapper may add an old shop
today), so results carry the date and are labelled that way. Explicit `start_date` /
`opening_date` tags, when present, are trusted over edit metadata.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .. import http
from ..quota import record_free_call
from .base import blank_candidate
from .osm import OVERPASS_URL, OverpassProvider, tags_for_niche


def _dt(value):
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def opened_signal(element: dict, now: datetime, window_days: int) -> dict | None:
    """Pure. Is this element plausibly new? -> {basis, date, confidence} or None."""
    tags = element.get("tags") or {}
    cutoff = now - timedelta(days=window_days)
    for key in ("opening_date", "start_date"):
        d = _dt(tags.get(key)) if tags.get(key) and len(str(tags[key])) >= 7 else None
        if d is None and tags.get(key) and len(str(tags[key])) == 4 and str(tags[key]).isdigit():
            d = datetime(int(tags[key]), 7, 1, tzinfo=timezone.utc)      # year-only: mid-year guess
        if d and cutoff <= d <= now + timedelta(days=60):
            return {"basis": f"{key} tag", "date": d.date().isoformat(), "confidence": 0.85}
    ts = _dt(element.get("timestamp"))
    if ts and ts >= cutoff and int(element.get("version") or 0) == 1:
        return {"basis": "first mapped, never edited", "date": ts.date().isoformat(), "confidence": 0.6}
    return None


class NewOpeningsProvider(OverpassProvider):
    name = "osm_new"
    billed = False
    window_days = 180

    def search(self, niche, city, area="", country="", max_results=20,
               lat=None, lng=None, radius_m=None):
        if lat is not None and lng is not None:
            lat, lon, radius = float(lat), float(lng), radius_m or 6000
        else:
            lat, lon = self._geocode(f"{area}, {city}" if area else city, country)
            radius = radius_m or (6000 if area else 15000)
        now = datetime.now(timezone.utc)
        since = (now - timedelta(days=self.window_days)).strftime("%Y-%m-%dT00:00:00Z")
        clauses = "".join(f'  nwr{f}(around:{radius},{lat},{lon})(newer:"{since}");\n'
                          for f in tags_for_niche(niche))
        oq = f"[out:json][timeout:60];\n(\n{clauses});\nout center tags meta {max_results * 4};"
        record_free_call("overpass")
        r = http.post(OVERPASS_URL, breaker="overpass", data={"data": oq}, timeout=60)
        if r.status_code >= 400:
            raise RuntimeError(f"Overpass query failed ({r.status_code}): {r.text[:200]}")
        out = []
        for el in r.json().get("elements", []):
            tags = el.get("tags") or {}
            sig = opened_signal(el, now, self.window_days)
            if not tags.get("name") or not sig:
                continue
            centre = el.get("center") or {}
            street = " ".join(p for p in (tags.get("addr:housenumber"), tags.get("addr:street")) if p)
            c = blank_candidate()
            c.update(
                place_id=f"osm:{el['type']}/{el['id']}", business_name=tags["name"],
                address=", ".join(p for p in (street, tags.get("addr:suburb"), tags.get("addr:city") or city,
                                              tags.get("addr:postcode")) if p) or None,
                city=tags.get("addr:city") or city, area=tags.get("addr:suburb") or area or None,
                pincode=tags.get("addr:postcode"), country=country or None,
                lat=el.get("lat") or centre.get("lat"), lng=el.get("lon") or centre.get("lon"),
                phone=tags.get("phone") or tags.get("contact:phone"),
                email=tags.get("email") or tags.get("contact:email"),
                website_url=tags.get("website") or tags.get("contact:website"),
                source=f"New opening ({sig['basis']}, {sig['date']}, {int(sig['confidence']*100)}% sure)")
            if c["lat"] and c["lng"]:
                c["google_maps_url"] = f"https://www.google.com/maps/search/?api=1&query={c['lat']},{c['lng']}"
            out.append((sig["confidence"], c))
        out.sort(key=lambda t: -t[0])
        return [c for _, c in out[:max_results]]
