"""India MCA registry via data.gov.in - recently registered Indian companies.

Free: needs a data.gov.in API key (DATA_GOV_IN_API_KEY) and the resource id(s)
of the MCA "Company Master Data" dataset(s) you want (DATA_GOV_IN_MCA_RESOURCES,
comma separated; MCA publishes one dataset per state/UT - copy the id from the
dataset's API tab on data.gov.in).

The API can't sort or filter by date, so rows are paged and filtered here:
registered within `since_days`, status Active, plus niche / city matching.
Column names differ a little between datasets, so they are looked up loosely.
Registry data has no phone/email - enrichment finds those from the website.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from .. import http
from ..config import settings
from .base import blank_candidate

BASE = "https://api.data.gov.in/resource/"
PAGE = 500


def _norm(k: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(k).lower())


def _pick(row: dict, *names: str):
    flat = {_norm(k): v for k, v in row.items()}
    for n in names:
        v = flat.get(_norm(n))
        if v not in (None, "", "NA", "N/A"):
            return str(v).strip()
    return None


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%Y/%m/%d", "%d-%m-%y", "%d/%m/%y", "%d-%b-%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def row_to_candidate(row: dict) -> dict | None:
    name = _pick(row, "company_name", "name", "companyname")
    cin = _pick(row, "cin", "corporate_identification_number", "corporate_identification_no")
    if not name:
        return None
    reg = parse_date(_pick(row, "date_of_registration", "dateofincorporation", "registration_date"))
    addr = _pick(row, "registered_office_address", "registered_address", "address")
    state = _pick(row, "registered_state", "state", "company_state_code")
    activity = _pick(row, "principal_business_activity_as_per_cin",
                     "principal_business_activity", "industrial_class", "activity")
    pin = re.search(r"\b(\d{6})\b", addr or "")
    c = blank_candidate()
    c.update(place_id=f"mca:{cin or name}", business_name=name.title(), address=addr,
             state=state, pincode=pin.group(1) if pin else None, country="India",
             source=f"India MCA (CIN {cin or 'n/a'}, registered {reg.isoformat() if reg else 'n/a'}"
                    f"{', ' + activity if activity else ''})")
    c["_registered"] = reg
    c["_status"] = (_pick(row, "company_status", "status") or "").lower()
    c["_text"] = f"{name} {activity or ''}".lower()
    c["_addr"] = f"{addr or ''} {state or ''}".lower()
    return c


class IndiaMcaProvider:
    name = "india_mca"
    billed = False
    since_days = 365
    max_pages = 10

    def search(self, niche, city, area="", country="", max_results=20,
               lat=None, lng=None, radius_m=None):
        resources = [r.strip() for r in settings.data_gov_in_mca_resources.split(",") if r.strip()]
        if not resources:
            raise RuntimeError("DATA_GOV_IN_MCA_RESOURCES is not set - add the MCA dataset "
                               "resource id(s) from data.gov.in to .env")
        since = date.today() - timedelta(days=self.since_days)
        words = [w for w in re.split(r"\W+", (niche or "").lower()) if len(w) > 2]
        place = (city or "").lower()
        out: list[dict] = []
        for rid in resources:
            for page in range(self.max_pages):
                r = http.get(BASE + rid, breaker="data_gov_in", params={
                    "api-key": settings.data_gov_in_api_key, "format": "json",
                    "limit": PAGE, "offset": page * PAGE})
                if r.status_code >= 400:
                    raise RuntimeError(f"data.gov.in error {r.status_code}: {r.text[:200]}")
                rows = r.json().get("records") or []
                for row in rows:
                    c = row_to_candidate(row)
                    if not c:
                        continue
                    reg, status = c.pop("_registered"), c.pop("_status")
                    text, addr = c.pop("_text"), c.pop("_addr")
                    if reg is None or reg < since:
                        continue
                    if status and "active" not in status:
                        continue
                    if words and not any(w in text for w in words):
                        continue
                    if place and place not in addr:
                        continue
                    c["city"] = city or None
                    out.append(c)
                    if len(out) >= max_results:
                        return out
                if len(rows) < PAGE:
                    break
        return out

    def fetch_details(self, candidate):
        return {}

    def fetch_ratings(self, candidate):
        return {}
