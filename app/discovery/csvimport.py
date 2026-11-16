"""CSV / spreadsheet import: bring your own list through the normal pipeline.

Header matching is forgiving (case, spaces, common synonyms), so exports from
Justdial-style sheets, CRMs, Google Takeout or hand-made lists all work.
"""

from __future__ import annotations

import csv
import io
import re

SYNONYMS = {
    "business_name": ("business_name", "name", "company", "company_name", "business", "title",
                      "organization", "organisation", "shop", "shop_name"),
    "phone": ("phone", "phone_number", "mobile", "contact", "contact_number", "tel", "telephone",
              "whatsapp"),
    "email": ("email", "email_address", "e-mail", "mail"),
    "website_url": ("website", "website_url", "url", "site", "web", "domain"),
    "address": ("address", "full_address", "street_address", "location"),
    "city": ("city", "town"),
    "area": ("area", "locality", "neighborhood", "neighbourhood", "suburb"),
    "state": ("state", "region", "province"),
    "country": ("country",),
    "pincode": ("pincode", "pin", "zip", "zipcode", "postal_code", "postcode"),
    "niche": ("niche", "category", "industry", "type"),
    "owner_name": ("owner", "owner_name", "contact_person", "contact_name"),
    "instagram_url": ("instagram", "instagram_url"),
    "facebook_url": ("facebook", "facebook_url"),
    "linkedin_url": ("linkedin", "linkedin_url"),
    "google_rating": ("rating", "google_rating", "stars"),
    "google_reviews": ("reviews", "review_count", "google_reviews", "user_ratings_total"),
    "lat": ("lat", "latitude"),
    "lng": ("lng", "lon", "long", "longitude"),
}
MAX_ROWS = 5000
MAX_BYTES = 5_000_000
_LOOKUP = {syn: field for field, syns in SYNONYMS.items() for syn in syns}


def _norm(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (header or "").strip().lower()).strip("_")


def map_headers(headers: list[str]) -> dict[str, str]:
    """{original header -> lead field}; first header wins for each field."""
    mapping, taken = {}, set()
    for h in headers:
        field = _LOOKUP.get(_norm(h))
        if field and field not in taken:
            mapping[h] = field
            taken.add(field)
    return mapping


def _num(value, cast):
    try:
        return cast(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def parse_csv(text: str) -> dict:
    """-> {candidates, mapped_columns, ignored_columns, skipped: [{row, reason}]}."""
    if len(text.encode("utf-8", "ignore")) > MAX_BYTES:
        raise ValueError(f"File too large (max {MAX_BYTES // 1_000_000} MB)")
    text = text.lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = reader.fieldnames or []
    mapping = map_headers(headers)
    if "business_name" not in mapping.values():
        raise ValueError("No business-name column found (expected e.g. 'name' or 'company')")
    candidates, skipped = [], []
    for i, row in enumerate(reader, start=2):
        if len(candidates) >= MAX_ROWS:
            skipped.append({"row": i, "reason": f"row limit {MAX_ROWS} reached"})
            break
        cand = {mapping[h]: (v or "").strip() or None for h, v in row.items()
                if h in mapping and isinstance(v, str)}
        if not cand.get("business_name"):
            skipped.append({"row": i, "reason": "missing business name"})
            continue
        for f, cast in (("google_rating", float), ("google_reviews", int),
                        ("lat", float), ("lng", float)):
            if f in cand:
                cand[f] = _num(cand[f], cast)
        cand["source"] = "CSV import"
        cand.setdefault("business_status", "OPERATIONAL")
        candidates.append(cand)
    return {"candidates": candidates, "mapped_columns": mapping,
            "ignored_columns": [h for h in headers if h not in mapping], "skipped": skipped}
