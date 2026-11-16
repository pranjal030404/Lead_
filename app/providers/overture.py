"""Overture Maps places - a large open dataset (CDLA-Permissive), free, no key.

Overture merges Meta, Microsoft and other open sources, and often carries what OSM lacks:
phone, email, website and social links. It is read straight from public Parquet files with
DuckDB (an open-source embedded database) - no account, no API, nothing sent to a third party
except anonymous reads of the public bucket.

Trade-off: a query scans remote files, so a search takes ~1 minute. Searches already run in the
background with progress, so this fits; results are cached by the app's search cache.
"""

from __future__ import annotations

import re
import threading
from urllib.parse import urlparse

from .. import http
from .base import blank_candidate
from .osm import OverpassProvider

STAC = "https://stac.overturemaps.org/catalog.json"
FALLBACK_RELEASE = "2026-10-21.0"
BUCKET = "s3://overturemaps-us-west-2/release/{rel}/theme=places/type=place/*"
_release_cache: dict = {}


def latest_release() -> str:
    if "v" in _release_cache:
        return _release_cache["v"]
    try:
        r = http.get(STAC, breaker="overture_stac", retries=1, timeout=10)
        rel = r.json().get("latest") if r.status_code < 400 else None
    except Exception:  # noqa: BLE001
        rel = None
    _release_cache["v"] = rel if isinstance(rel, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}\.\d", rel) else FALLBACK_RELEASE
    return _release_cache["v"]


def bbox_around(lat: float, lng: float, radius_m: int) -> tuple[float, float, float, float]:
    import math
    dlat = math.degrees(radius_m / 6_371_000)
    dlng = math.degrees(radius_m / (6_371_000 * max(0.01, math.cos(math.radians(lat)))))
    return lng - dlng, lng + dlng, lat - dlat, lat + dlat


def classify_links(website: str | None, socials: list | None) -> dict:
    """Pure: sort Overture's mixed `websites`/`socials` into website / whatsapp / social URLs."""
    out: dict = {"website_url": None, "whatsapp": None, "facebook_url": None, "instagram_url": None,
                 "linkedin_url": None}
    for url in [website] + list(socials or []):
        if not url or not isinstance(url, str):
            continue
        host = (urlparse(url if "//" in url else "//" + url).hostname or "").lower().removeprefix("www.")
        if host in ("wa.me", "api.whatsapp.com"):
            m = re.search(r"(\d{8,15})", url)
            out["whatsapp"] = out["whatsapp"] or (m.group(1) if m else None)
        elif host.endswith("facebook.com") or host == "fb.com":
            out["facebook_url"] = out["facebook_url"] or url
        elif host.endswith("instagram.com"):
            out["instagram_url"] = out["instagram_url"] or url
        elif host.endswith("linkedin.com"):
            out["linkedin_url"] = out["linkedin_url"] or url
        elif host and url == website and out["website_url"] is None:
            out["website_url"] = url
    return out


def to_candidate(row: dict, city: str, country: str) -> dict | None:
    if not row.get("name"):
        return None
    c = blank_candidate()
    links = classify_links(row.get("website"), [row.get("social")] if row.get("social") else [])
    closed = (row.get("operating_status") or "").lower() in ("closed", "permanently_closed")
    c.update(place_id=f"overture:{row['id']}", business_name=row["name"], address=row.get("addr"),
             city=row.get("city") or city, pincode=row.get("pin"), country=country or None,
             lat=row.get("lat"), lng=row.get("lng"), phone=row.get("phone"), email=row.get("email"),
             business_status="CLOSED_PERMANENTLY" if closed else "OPERATIONAL",
             source=f"Overture Maps ({int((row.get('confidence') or 0) * 100)}% confidence)", **links)
    if c["lat"] and c["lng"]:
        c["google_maps_url"] = f"https://www.google.com/maps/search/?api=1&query={c['lat']},{c['lng']}"
    return c


class OvertureProvider(OverpassProvider):
    """Reuses the OSM provider's free geocoder to locate the city."""
    name = "overture"
    billed = False
    min_confidence = 0.25
    timeout_s = 200

    def search(self, niche, city, area="", country="", max_results=20,
               lat=None, lng=None, radius_m=None):
        import duckdb
        if lat is not None and lng is not None:
            lat, lng, radius = float(lat), float(lng), radius_m or 5000
        else:
            lat, lng = self._geocode(f"{area}, {city}" if area else city, country)
            radius = radius_m or (2500 if area else 4000)
        xmin, xmax, ymin, ymax = bbox_around(lat, lng, radius)
        words = [w for w in re.findall(r"[a-z]{3,}", niche.lower()) if w not in ("shop", "store", "and", "the")]
        if not words:
            raise RuntimeError("Give a niche with at least one real word (e.g. 'dentist', 'cafe')")
        like = ["%" + w.rstrip("s") + "%" for w in words[:3]]
        cond = " OR ".join(['taxonomy."primary" ILIKE ?', 'basic_category ILIKE ?', 'names."primary" ILIKE ?'] * len(like))
        params = [x for p in like for x in (p, p, p)] + [self.min_confidence, xmin, xmax, ymin, ymax, max(max_results * 2, 12)]
        con = duckdb.connect()
        timer = threading.Timer(self.timeout_s, con.interrupt)   # hard stop for slow remote scans
        timer.start()
        try:
            con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2'; SET http_timeout=120000;")
            cur = con.execute(f"""
                SELECT names."primary" AS name, confidence, operating_status,
                       websites[1] AS website, phones[1] AS phone, emails[1] AS email, socials[1] AS social,
                       addresses[1].freeform AS addr, addresses[1].locality AS city,
                       addresses[1].postcode AS pin, bbox.xmin AS lng, bbox.ymin AS lat, id
                FROM read_parquet('{BUCKET.format(rel=latest_release())}', hive_partitioning=1)
                WHERE ({cond}) AND confidence >= ?
                  AND bbox.xmin BETWEEN ? AND ? AND bbox.ymin BETWEEN ? AND ?
                LIMIT ?""", params)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            rows.sort(key=lambda r: -(r.get("confidence") or 0))     # best-attested first (sorted here: DuckDB
                                                                      # trips on ORDER BY over remote STRUCT columns)
        except duckdb.InterruptException as exc:
            raise RuntimeError(f"Overture Maps took longer than {self.timeout_s}s - use a smaller area (a district, "
                               "not a whole city) or try the OpenStreetMap sources.") from exc
        finally:
            timer.cancel()
            con.close()
        out = [c for c in (to_candidate(r, city, country) for r in rows) if c]
        return out[:max_results]

    def fetch_details(self, candidate):
        return {}

    def fetch_ratings(self, candidate):
        return {}
