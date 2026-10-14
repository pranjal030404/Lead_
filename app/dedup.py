"""Duplicate detection and merging (Part 1.2).

Checks run cheapest and most certain first:
  1. place_id      - exact, 100% the same business
  2. phone         - same phone = same business even if the name differs
  3. name + city   - exact match after normalisation
  4. fuzzy name    - >= 0.80 similarity within the same city
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .db import current_ws, get_conn, lead_scope, utcnow

FUZZY_THRESHOLD = 0.80

# Words that carry no signal when comparing business names.
_NOISE = {
    "the", "a", "an", "and", "co", "company", "pvt", "private", "ltd", "limited",
    "llp", "inc", "llc", "shop", "store", "centre", "center", "services", "service",
    "solutions", "&",
}


def normalise_name(name: str | None) -> str:
    if not name:
        return ""
    lowered = name.lower()
    # Drop possessives first: "sharma's" must become "sharma", not "sharma s" -
    # a stray one-letter token badly skews the token-containment score below.
    lowered = re.sub(r"'s\b|’s\b", "", lowered)
    cleaned = re.sub(r"[^\w\s]", " ", lowered)
    words = [w for w in cleaned.split() if len(w) > 1 and w not in _NOISE]
    return " ".join(words)


def similarity(a: str | None, b: str | None) -> float:
    """Blend of character similarity and token containment.

    Character similarity alone is too strict for the case the spec cares about:
    "Sharma's Dental" vs "Sharma Dental Clinic" only scores 0.74, because the
    second name is simply longer. Token containment catches that (every word of
    the shorter name appears in the longer one), and blending the two keeps
    unrelated names well below the threshold.
    """
    na, nb = normalise_name(a), normalise_name(b)
    if not na or not nb:
        return 0.0

    sequence = SequenceMatcher(None, na, nb).ratio()
    tokens_a, tokens_b = set(na.split()), set(nb.split())
    containment = len(tokens_a & tokens_b) / min(len(tokens_a), len(tokens_b))
    return (sequence + containment) / 2


def find_duplicate(conn, candidate: dict,
                   owner_user_id: int | None = None) -> tuple[dict | None, str | None]:
    """Return (existing_lead, reason) or (None, None).

    Lookups are scoped to the owner who is searching, so a regular user always
    gets their own copy of a business rather than merging into somebody else's
    lead. `owner_user_id=None` (system/automated runs) matches the shared,
    unowned leads, which are stored under the sentinel owner 0.
    """
    owner = owner_user_id or 0
    place_id = candidate.get("place_id")
    if place_id:
        row = conn.execute(
            "SELECT * FROM leads WHERE workspace_id = ? AND owner_user_id = ? AND place_id = ?",
            (current_ws(), owner, place_id)).fetchone()
        if row:
            return dict(row), "place_id"

    phone = candidate.get("phone")
    if phone:
        row = conn.execute(
            "SELECT * FROM leads WHERE workspace_id = ? AND owner_user_id = ? "
            "AND phone = ? AND phone != ''",
            (current_ws(), owner, phone)
        ).fetchone()
        if row:
            return dict(row), "phone"

    name, city = candidate.get("business_name"), candidate.get("city")
    if not name:
        return None, None

    rows = conn.execute(
        "SELECT * FROM leads WHERE workspace_id = ? AND owner_user_id = ? "
        "AND LOWER(COALESCE(city,'')) = LOWER(COALESCE(?,''))",
        (current_ws(), owner, city),
    ).fetchall()
    norm_candidate = normalise_name(name)
    best, best_score = None, 0.0
    for row in rows:
        existing = dict(row)
        if normalise_name(existing["business_name"]) == norm_candidate and norm_candidate:
            return existing, "name+city"
        # Two known-different phone numbers means two different businesses, even
        # if the names match - this is what stops chain branches ("Domino's
        # Sector 15" vs "Domino's Sector 21") being merged into one lead.
        if phone and existing.get("phone") and phone != existing["phone"]:
            continue
        score = similarity(name, existing["business_name"])
        if score > best_score:
            best, best_score = existing, score
    if best and best_score >= FUZZY_THRESHOLD:
        return best, f"fuzzy name ({best_score:.0%})"
    return None, None


# Fields a later search is allowed to fill in on an existing lead. Only ever
# fills blanks - a re-search never overwrites data you already have (or edited).
MERGEABLE = [
    "phone", "email", "website_url", "address", "area", "pincode", "state",
    "country", "lat", "lng", "place_id", "google_maps_url", "google_rating",
    "google_reviews", "instagram_url", "facebook_url", "linkedin_url",
    "owner_name", "whatsapp", "niche",
]


def merge_into_existing(conn, existing: dict, candidate: dict) -> list[str]:
    """Fill blank fields on `existing` from `candidate`. Returns filled field names."""
    updates: dict[str, object] = {}
    for field in MERGEABLE:
        new_value = candidate.get(field)
        if new_value in (None, "", 0) and field not in ("google_rating", "google_reviews"):
            continue
        if new_value in (None, ""):
            continue
        current = existing.get(field)
        if current in (None, "", 0):
            updates[field] = new_value
    if not updates:
        return []
    updates["updated_at"] = utcnow()
    assignments = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE leads SET {assignments} WHERE id = ?",
        (*updates.values(), existing["id"]),
    )
    return [k for k in updates if k != "updated_at"]


def dedupe_existing_leads() -> list[dict]:
    """Sweep the current workspace for duplicates that slipped in. Returns merge report.

    Only leads the caller can see are considered, and a merge never crosses
    owners - an admin sweeping the whole workspace keeps each person's leads
    separate."""
    merged = []
    scope, scope_params = lead_scope()
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT * FROM leads WHERE {scope} ORDER BY id", tuple(scope_params)).fetchall()]
        seen: list[dict] = []
        for lead in rows:
            match = None
            for kept in seen:
                if lead.get("owner_user_id") != kept.get("owner_user_id"):
                    continue  # never merge one person's lead into another's
                if lead.get("place_id") and lead["place_id"] == kept.get("place_id"):
                    match = kept
                    break
                if lead.get("phone") and lead["phone"] == kept.get("phone"):
                    match = kept
                    break
                same_city = (lead.get("city") or "").lower() == (kept.get("city") or "").lower()
                both_phoned = lead.get("phone") and kept.get("phone")
                if both_phoned and lead["phone"] != kept["phone"]:
                    continue  # different phones = different businesses (chain branches)
                if same_city and similarity(lead["business_name"], kept["business_name"]) >= FUZZY_THRESHOLD:
                    match = kept
                    break
            if match:
                filled = merge_into_existing(conn, match, lead)
                conn.execute("UPDATE interactions SET lead_id = ? WHERE lead_id = ?",
                             (match["id"], lead["id"]))
                # Keep what we learned about the duplicate; IGNORE skips rows the
                # survivor already has (the leftovers cascade away with the lead).
                for table in ("contacts", "email_addresses", "social_profiles"):
                    conn.execute(f"UPDATE IGNORE {table} SET lead_id = ? WHERE lead_id = ?",
                                 (match["id"], lead["id"]))
                conn.execute("UPDATE IGNORE field_sources SET entity_id = ? "
                             "WHERE entity_type = 'lead' AND entity_id = ?",
                             (match["id"], lead["id"]))
                conn.execute("DELETE FROM field_sources WHERE entity_type = 'lead' "
                             "AND entity_id = ?", (lead["id"],))
                conn.execute("DELETE FROM leads WHERE id = ?", (lead["id"],))
                merged.append({
                    "removed_id": lead["id"],
                    "removed_name": lead["business_name"],
                    "kept_id": match["id"],
                    "fields_merged": filled,
                })
            else:
                seen.append(lead)
    return merged
