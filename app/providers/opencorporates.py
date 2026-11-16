"""OpenCorporates - company registries worldwide. `country` is a jurisdiction code
prefix (e.g. "in", "us_ca", "gb"). Recently incorporated companies only."""

from __future__ import annotations

from datetime import date, timedelta

from .. import http
from ..config import settings
from .base import blank_candidate

URL = "https://api.opencorporates.com/v0.4/companies/search"


class OpenCorporatesProvider:
    name = "opencorporates"
    billed = False
    since_days = 180

    def search(self, niche, city, area="", country="", max_results=20,
               lat=None, lng=None, radius_m=None):
        since = (date.today() - timedelta(days=self.since_days)).isoformat()
        params = {"q": niche, "created_since": since, "current_status": "Active",
                  "per_page": min(max_results, 100), "api_token": settings.opencorporates_api_key}
        if country:
            params["jurisdiction_code"] = country.lower()
        r = http.get(URL, breaker="opencorporates", params=params)
        if r.status_code >= 400:
            raise RuntimeError(f"OpenCorporates error {r.status_code}: {r.text[:200]}")
        out = []
        for row in (r.json().get("results") or {}).get("companies", []):
            co = row.get("company") or {}
            c = blank_candidate()
            c.update(place_id=f"oc:{co.get('jurisdiction_code')}/{co.get('company_number')}",
                     business_name=(co.get("name") or "").title(),
                     address=co.get("registered_address_in_full"), city=city or None,
                     country=(co.get("jurisdiction_code") or "").upper() or None,
                     source=f"OpenCorporates (incorporated {co.get('incorporation_date')})")
            if c["business_name"]:
                out.append(c)
        return out

    def fetch_details(self, candidate):
        return {}

    def fetch_ratings(self, candidate):
        return {}
