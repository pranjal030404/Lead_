"""India new companies with NO API key in the app: reads MCA "Company Master Data"
CSV exports that you download (free, data.gov.in login) into data/india_mca/.
Drop one CSV per state in that folder; every *.csv there is searched."""

from __future__ import annotations

import csv
import re
from datetime import date, timedelta

from ..config import settings
from .base import blank_candidate
from .india_mca import row_to_candidate


def folder():
    p = settings.data_dir / "india_mca"
    p.mkdir(parents=True, exist_ok=True)
    return p


class IndiaCsvProvider:
    name = "india_csv"
    billed = False
    since_days = 365

    def search(self, niche, city, area="", country="", max_results=20,
               lat=None, lng=None, radius_m=None):
        files = sorted(folder().glob("*.csv"))
        if not files:
            raise RuntimeError(f"No CSV files in {folder()} - download MCA Company Master Data "
                               "CSVs from data.gov.in and put them there")
        since = date.today() - timedelta(days=self.since_days)
        words = [w for w in re.split(r"\W+", (niche or "").lower()) if len(w) > 2]
        place = (city or "").lower()
        out = []
        for path in files:
            with open(path, encoding="utf-8-sig", newline="", errors="replace") as f:
                for row in csv.DictReader(f):
                    c = row_to_candidate(row)
                    if not c:
                        continue
                    reg, status = c.pop("_registered"), c.pop("_status")
                    text, addr = c.pop("_text"), c.pop("_addr")
                    if reg is None or reg < since or (status and "active" not in status):
                        continue
                    if words and not any(w in text for w in words):
                        continue
                    if place and place not in addr:
                        continue
                    c["city"] = city or None
                    out.append(c)
        out.sort(key=lambda c: c["source"].split("registered ")[-1], reverse=True)
        return out[:max_results]

    def fetch_details(self, candidate):
        return {}

    def fetch_ratings(self, candidate):
        return {}
