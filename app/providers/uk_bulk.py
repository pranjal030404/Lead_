"""UK new companies with NO API key: Companies House's free monthly bulk file
(https://download.companieshouse.gov.uk/en_output.html). Downloaded once, cached
in data/, refreshed after 30 days, then filtered locally for companies
incorporated within `since_days`. Big download (~450 MB zip) - first run is slow."""

from __future__ import annotations

import csv
import io
import re
import time
import zipfile
from datetime import date, datetime, timedelta

import httpx

from ..config import settings
from .base import blank_candidate

URL = "https://download.companieshouse.gov.uk/BasicCompanyDataAsOneFile-{y}-{m:02d}-01.zip"
MAX_AGE = 30 * 86400


def _cache_path():
    return settings.data_dir / "uk_companies_bulk.zip"


def ensure_bulk_file():
    path = _cache_path()
    if path.exists() and time.time() - path.stat().st_mtime < MAX_AGE:
        return path
    today = date.today()
    months = [(today.year, today.month),
              ((today.replace(day=1) - timedelta(days=1)).year, (today.replace(day=1) - timedelta(days=1)).month)]
    tmp = path.with_suffix(".part")
    for y, m in months:        # the current month's file may not be published yet
        with httpx.stream("GET", URL.format(y=y, m=m), timeout=120, follow_redirects=True) as r:
            if r.status_code == 404:
                continue
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
        tmp.replace(path)
        return path
    if path.exists():
        return path            # stale beats nothing
    raise RuntimeError("Companies House bulk file not available")


def _d(s):
    try:
        return datetime.strptime((s or "").strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def iter_rows(fileobj):
    reader = csv.DictReader(io.TextIOWrapper(fileobj, encoding="utf-8-sig", newline=""))
    reader.fieldnames = [(h or "").strip() for h in reader.fieldnames or []]
    yield from reader


def to_candidate(row):
    g = lambda k: (row.get(k) or "").strip()  # noqa: E731
    name = g("CompanyName")
    if not name:
        return None
    addr = ", ".join(x for x in (g("RegAddress.AddressLine1"), g("RegAddress.PostTown"),
                                 g("RegAddress.PostCode")) if x)
    c = blank_candidate()
    c.update(place_id=f"ch:{g('CompanyNumber')}", business_name=name.title(), address=addr or None,
             city=g("RegAddress.PostTown").title() or None, pincode=g("RegAddress.PostCode") or None,
             country="United Kingdom",
             source=f"Companies House bulk (incorporated {g('IncorporationDate')}, {g('SICCode.SicText_1')})")
    return c


class UkBulkProvider:
    name = "uk_bulk"
    billed = False
    since_days = 180

    def search(self, niche, city, area="", country="", max_results=20,
               lat=None, lng=None, radius_m=None):
        since = date.today() - timedelta(days=self.since_days)
        words = [w for w in re.split(r"\W+", (niche or "").lower()) if len(w) > 2]
        place = (city or "").lower()
        out = []
        with zipfile.ZipFile(ensure_bulk_file()) as z:
            with z.open(z.namelist()[0]) as f:
                for row in iter_rows(f):
                    inc = _d(row.get("IncorporationDate"))
                    if not inc or inc < since or (row.get("CompanyStatus") or "").strip() != "Active":
                        continue
                    text = f"{row.get('CompanyName', '')} {row.get('SICCode.SicText_1', '')}".lower()
                    if words and not any(w in text for w in words):
                        continue
                    if place and place not in f"{row.get('RegAddress.PostTown', '')} {row.get('RegAddress.County', '')}".lower():
                        continue
                    c = to_candidate(row)
                    if c:
                        out.append(c)
        out.sort(key=lambda c: c["source"], reverse=True)
        return out[:max_results]

    def fetch_details(self, candidate):
        return {}

    def fetch_ratings(self, candidate):
        return {}
