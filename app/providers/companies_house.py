"""UK Companies House registry - newly incorporated companies (the "new business,
no website yet" window). Free API key. `niche` is matched against company name; use
SIC-style words ("dental", "cafe") or a name fragment."""

from __future__ import annotations

from datetime import date, timedelta

from .. import http
from ..config import settings
from .base import blank_candidate

URL = "https://api.company-information.service.gov.uk/advanced-search/companies"


class CompaniesHouseProvider:
    name = "companies_house"
    billed = False
    since_days = 180

    def search(self, niche, city, area="", country="", max_results=20,
               lat=None, lng=None, radius_m=None):
        since = (date.today() - timedelta(days=self.since_days)).isoformat()
        params = {"company_name_includes": niche, "incorporated_from": since,
                  "company_status": "active", "size": min(max_results, 100)}
        if city:
            params["location"] = city
        r = http.get(URL, breaker="companies_house", params=params,
                     auth=(settings.companies_house_api_key, ""))
        if r.status_code >= 400:
            raise RuntimeError(f"Companies House error {r.status_code}: {r.text[:200]}")
        out = []
        for it in r.json().get("items", []):
            a = it.get("registered_office_address") or {}
            c = blank_candidate()
            c.update(place_id=f"ch:{it.get('company_number')}", business_name=(it.get("company_name") or "").title(),
                     address=", ".join(str(a[k]) for k in ("address_line_1", "locality", "postal_code") if a.get(k)) or None,
                     city=a.get("locality") or city, pincode=a.get("postal_code"), country="United Kingdom",
                     source=f"Companies House (incorporated {it.get('date_of_creation')})")
            out.append(c)
        return out

    def fetch_details(self, candidate):
        return {}

    def fetch_ratings(self, candidate):
        return {}
