"""Search orchestration: discovery -> dedup -> staged detail fetch -> enrichment.

Idempotent by design (Part 7.3): re-running a search that crashed halfway
creates no duplicates, because every candidate goes through the dedup check in
Part 1.2 before anything is inserted.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from .config import settings
from .db import (execute, get_conn, log_automation, log_failure, query, query_one,
                 utcnow)
from .enrich import enrich_many
from .providers import get_provider
from .quota import QuotaExceeded
from .scoring import score_lead
from .validation import normalise_phone, whatsapp_number

LEAD_COLUMNS = [
    "business_name", "owner_name", "phone", "phone_valid", "email", "whatsapp",
    "website_url", "address", "city", "area", "pincode", "state", "country",
    "lat", "lng", "place_id", "google_maps_url", "google_rating", "google_reviews",
    "instagram_url", "facebook_url", "linkedin_url", "business_status",
    "lead_score", "lead_priority", "data_quality_score", "score_reasons",
    "status", "source", "niche", "search_id", "found_date", "created_at", "updated_at",
]

_running: dict[int, bool] = {}


# ---------------------------------------------------------------- coverage --

def coverage_status(row: dict) -> str:
    if not row or not row.get("last_searched"):
        return "NEVER"
    if row.get("barren_runs", 0) >= 3:
        return "EXHAUSTED"
    try:
        last = datetime.fromisoformat(row["last_searched"])
    except (ValueError, TypeError):
        return "STALE"
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - last
    if age < timedelta(days=7):
        return "FRESH"
    if age < timedelta(days=30):
        return "STALE"
    return "STALE"


def get_coverage(niche: str, city: str, area: str = "", country: str = "") -> dict:
    row = query_one(
        "SELECT * FROM search_coverage WHERE city = ? AND area = ? AND niche = ? AND country = ?",
        (city, area or "", niche, country or ""),
    )
    if not row:
        return {"status": "NEVER", "times_searched": 0, "total_leads_found": 0,
                "last_searched": None}
    row["status"] = coverage_status(row)
    return row


def _update_coverage(niche, city, area, country, found, new_leads) -> None:
    key = (city, area or "", niche, country or "")
    existing = query_one(
        "SELECT * FROM search_coverage WHERE city = ? AND area = ? AND niche = ? AND country = ?",
        key,
    )
    now = utcnow()
    if existing:
        barren = existing["barren_runs"] + 1 if new_leads == 0 else 0
        execute(
            """UPDATE search_coverage SET last_searched = ?, times_searched = times_searched + 1,
               total_leads_found = total_leads_found + ?, new_leads_last_search = ?,
               barren_runs = ?, status = ?, updated_at = ?
               WHERE id = ?""",
            (now, new_leads, new_leads, barren,
             "EXHAUSTED" if barren >= 3 else "FRESH", now, existing["id"]),
        )
    else:
        execute(
            """INSERT INTO search_coverage
               (city, area, pincode, niche, country, last_searched, times_searched,
                total_leads_found, new_leads_last_search, barren_runs, status,
                created_at, updated_at)
               VALUES(?,?,?,?,?,?,1,?,?,?,?,?,?)""",
            (city, area or "", None, niche, country or "", now, new_leads, new_leads,
             0 if new_leads else 1, "FRESH", now, now),
        )


# ------------------------------------------------------------------ search --

def _insert_lead(conn, candidate: dict, niche: str, search_id: int) -> int:
    phone, phone_valid = normalise_phone(candidate.get("phone"), candidate.get("country"))
    candidate["phone"] = phone
    candidate["phone_valid"] = 1 if phone_valid else 0
    candidate["whatsapp"] = whatsapp_number(phone, candidate.get("country"))
    candidate.update(score_lead(candidate))

    now = utcnow()
    values = {col: candidate.get(col) for col in LEAD_COLUMNS}
    values.update(
        niche=niche, search_id=search_id, status="NEW",
        found_date=now, created_at=now, updated_at=now,
        source=candidate.get("source") or "Search",
    )
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    cur = conn.execute(
        f"INSERT INTO leads ({columns}) VALUES ({placeholders})", tuple(values.values())
    )
    return cur.lastrowid


def run_search(
    niche: str,
    city: str,
    area: str = "",
    country: str = "",
    max_results: int = 20,
    provider_name: str | None = None,
    triggered_by: str = "manual",
    search_id: int | None = None,
    enrich: bool = True,
    lat: float | None = None,
    lng: float | None = None,
    radius_m: int | None = None,
) -> dict:
    """Run one search end to end. Blocking - call via start_search for the API.

    Passing lat/lng centres the search on a point instead of a geocoded city
    name, which is what the "near me" button does.
    """
    from .dedup import find_duplicate, merge_into_existing

    max_results = min(max_results, settings.max_results_per_search)
    provider = get_provider(provider_name)

    if search_id is None:
        search_id = execute(
            """INSERT INTO searches
               (niche, city, area, country, provider, max_results, status, progress,
                triggered_by, started_at)
               VALUES(?,?,?,?,?,?,'RUNNING','Starting...',?,?)""",
            (niche, city, area or "", country or "", provider.name, max_results,
             triggered_by, utcnow()),
        )

    _running[search_id] = True
    stats = {"results_found": 0, "new_leads": 0, "duplicates_skipped": 0,
             "hot_count": 0, "warm_count": 0, "cold_count": 0, "api_calls": 0}
    new_ids: list[int] = []
    quota_hit = False

    def progress(message: str) -> None:
        execute("UPDATE searches SET progress = ? WHERE id = ?", (message, search_id))

    try:
        where = f"{radius_m or settings.nearby_radius_m}m around you" if lat is not None \
            else (area or city)
        progress(f"Searching {provider.name} for '{niche}' in {where}...")
        candidates = provider.search(niche, city, area, country, max_results,
                                     lat=lat, lng=lng, radius_m=radius_m)
        stats["results_found"] = len(candidates)
        progress(f"{len(candidates)} results - checking for duplicates")

        for index, candidate in enumerate(candidates, 1):
            if not _running.get(search_id, True):
                progress("Cancelled")
                break
            candidate.setdefault("niche", niche)
            candidate["city"] = candidate.get("city") or city
            candidate["country"] = candidate.get("country") or country or None

            with get_conn() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    existing, reason = find_duplicate(conn, candidate)
                    if existing:
                        filled = merge_into_existing(conn, existing, candidate)
                        conn.execute("COMMIT")
                        stats["duplicates_skipped"] += 1
                        if filled:
                            log_automation(
                                "search",
                                f"Duplicate ({reason}): merged {', '.join(filled)} into "
                                f"lead #{existing['id']} {existing['business_name']}",
                            )
                        continue
                    conn.execute("ROLLBACK")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise

            # Stage 2 - only now, for a place we definitely don't have yet.
            try:
                details = provider.fetch_details(candidate)
                if details:
                    stats["api_calls"] += 1
                    candidate.update({k: v for k, v in details.items() if v is not None})
            except QuotaExceeded:
                raise
            except Exception as exc:  # noqa: BLE001
                log_failure("search.details", str(exc), payload=candidate.get("place_id"))

            if (candidate.get("business_status") or "").upper() in (
                "CLOSED_PERMANENTLY", "PERMANENTLY_CLOSED"
            ):
                stats["duplicates_skipped"] += 0
                continue

            # Stage 3 - ratings, only if the lead already looks worth pitching.
            provisional = score_lead(candidate)
            if provisional["lead_score"] >= settings.enterprise_fetch_min_score:
                try:
                    ratings = provider.fetch_ratings(candidate)
                    if ratings:
                        stats["api_calls"] += 1
                        candidate.update({k: v for k, v in ratings.items() if v is not None})
                except QuotaExceeded:
                    raise
                except Exception as exc:  # noqa: BLE001
                    log_failure("search.ratings", str(exc), payload=candidate.get("place_id"))

            with get_conn() as conn:
                lead_id = _insert_lead(conn, candidate, niche, search_id)
            new_ids.append(lead_id)
            stats["new_leads"] += 1
            progress(f"{index}/{len(candidates)} checked - {stats['new_leads']} new leads")

        if enrich and new_ids:
            progress(f"Enriching {len(new_ids)} new leads...")

            def report(finished: int, total: int) -> None:
                if finished % 5 == 0 or finished == total:
                    progress(f"Enriched {finished}/{total}")

            enrich_many(new_ids, on_progress=report)

            # Part 7.6 - a search run by the scheduler at 6am has nobody watching
            # it. Deduplicated per lead, so a manual search you're already
            # watching can't double-alert on the same business later.
            from . import notify

            notify.alert_new_hot_leads(new_ids)

        if new_ids:
            placeholders = ",".join("?" for _ in new_ids)
            counts = query(
                f"SELECT lead_priority, COUNT(*) AS n FROM leads WHERE id IN ({placeholders}) "
                "GROUP BY lead_priority",
                tuple(new_ids),
            )
            for row in counts:
                key = f"{(row['lead_priority'] or 'cold').lower()}_count"
                if key in stats:
                    stats[key] = row["n"]

        status = "COMPLETED"
        error = None

    except QuotaExceeded as exc:
        quota_hit = True
        status, error = "STOPPED_QUOTA", str(exc)
        log_automation("search", f"Search #{search_id} stopped: {exc}", "warn")
    except Exception as exc:  # noqa: BLE001
        status, error = "FAILED", str(exc)
        log_failure("search.run", str(exc), search_id)
        log_automation("search", f"Search #{search_id} failed: {exc}", "error")
    finally:
        _running.pop(search_id, None)

    execute(
        """UPDATE searches SET results_found = ?, new_leads = ?, duplicates_skipped = ?,
           hot_count = ?, warm_count = ?, cold_count = ?, api_calls = ?, status = ?,
           progress = ?, error_message = ?, completed_at = ?
           WHERE id = ?""",
        (stats["results_found"], stats["new_leads"], stats["duplicates_skipped"],
         stats["hot_count"], stats["warm_count"], stats["cold_count"], stats["api_calls"],
         status, "Done" if status == "COMPLETED" else status, error, utcnow(), search_id),
    )
    _update_coverage(niche, city, area, country, stats["results_found"], stats["new_leads"])

    if status == "COMPLETED":
        log_automation(
            "search",
            f"Searched '{niche}' in {area or city}{', ' + country if country else ''} - "
            f"{stats['new_leads']} new leads, {stats['duplicates_skipped']} duplicates skipped",
        )

    return {"search_id": search_id, "status": status, "error": error,
            "quota_hit": quota_hit, **stats}


def start_search(**kwargs) -> int:
    """Kick a search off in a worker thread; returns the search id immediately."""
    search_id = execute(
        """INSERT INTO searches
           (niche, city, area, country, provider, max_results, status, progress,
            triggered_by, started_at)
           VALUES(?,?,?,?,?,?,'RUNNING','Queued...',?,?)""",
        (kwargs.get("niche"), kwargs.get("city"), kwargs.get("area") or "",
         kwargs.get("country") or "", kwargs.get("provider_name") or settings.provider,
         kwargs.get("max_results", 20), kwargs.get("triggered_by", "manual"), utcnow()),
    )
    thread = threading.Thread(
        target=run_search, kwargs={**kwargs, "search_id": search_id}, daemon=True
    )
    thread.start()
    return search_id


def cancel_search(search_id: int) -> bool:
    if search_id in _running:
        _running[search_id] = False
        return True
    return False
