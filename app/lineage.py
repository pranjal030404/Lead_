"""Data lineage, confidence and freshness.

Every important field on a lead can answer: where did this value come from, how
much do we trust it, and how old is it. One `field_sources` row per (lead,
field) holds the provenance of the *current* value.

Honesty rules (spec: "do not treat inferred information as verified"):
  * `verified_at` is set only by an explicit act - a user attesting, or a real
    verification provider. Scraping, a provider lookup or normalisation is an
    *observation*, never a verification.
  * Re-observing a field (new value, new source) clears `verified_at`.
  * `confidence` is the inherent trust in the source, not a claim of truth.
    Effective confidence additionally decays as the value ages.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .db import current_ws, execute, query, utcnow

# Fields we track provenance for, and how much each matters to "can I reach and
# trust this business" (weights sum to 1.0).
FIELD_WEIGHTS = {
    "phone": 0.25, "email": 0.25, "website_url": 0.15, "address": 0.10,
    "owner_name": 0.10, "instagram_url": 0.05, "facebook_url": 0.05,
    "linkedin_url": 0.05,
}
TRACKED_FIELDS = tuple(FIELD_WEIGHTS) + ("whatsapp", "google_rating", "google_reviews")

# How long a value stays trustworthy before it should be re-checked (days).
FIELD_TTL_DAYS = {
    "phone": 180, "whatsapp": 180, "email": 90, "website_url": 180, "address": 365,
    "owner_name": 365, "instagram_url": 180, "facebook_url": 180, "linkedin_url": 180,
    "google_rating": 30, "google_reviews": 30,
}
DEFAULT_TTL_DAYS = 180

# Inherent trust per source. Unknown sources get DEFAULT_CONFIDENCE.
SOURCE_CONFIDENCE = {
    "google": 0.95, "osm": 0.75, "website_scrape": 0.85, "hunter": 0.80,
    "manual": 0.90, "inferred": 0.30, "unknown": 0.50, "mock": 0.10,
}
DEFAULT_CONFIDENCE = 0.50

# Effective-confidence multiplier by freshness.
DECAY = {"FRESH": 1.0, "AGING": 0.85, "STALE": 0.60, "EXPIRED": 0.30}
_RANK = {"FRESH": 0, "AGING": 1, "STALE": 2, "EXPIRED": 3}

SOURCE_TYPES = ("provider", "website", "enrichment_api", "user", "inferred", "system", "legacy")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def ttl_days(field: str) -> int:
    return FIELD_TTL_DAYS.get(field, DEFAULT_TTL_DAYS)


def source_confidence(source: str) -> float:
    return SOURCE_CONFIDENCE.get(source, DEFAULT_CONFIDENCE)


def freshness_status(observed_at: str | None, verified_at: str | None, field: str,
                     now: datetime | None = None) -> str:
    """FRESH < 50% of the field's TTL, AGING < 100%, STALE < 150%, else EXPIRED.
    Age runs from the last verification if there is one, else the observation."""
    base = _parse(verified_at) or _parse(observed_at)
    if base is None:
        return "EXPIRED"
    ratio = ((now or _now()) - base) / timedelta(days=ttl_days(field))
    if ratio < 0.5:
        return "FRESH"
    if ratio < 1.0:
        return "AGING"
    if ratio < 1.5:
        return "STALE"
    return "EXPIRED"


def record(lead_id: int, field: str, source: str, source_type: str,
           confidence: float | None = None, verified: bool = False) -> None:
    """Record that `field` of `lead_id` now holds a value from `source`."""
    if field not in TRACKED_FIELDS:
        return
    now = utcnow()
    conf = source_confidence(source) if confidence is None else confidence
    conf = max(0.0, min(1.0, conf))
    base = _parse(now)
    expires = (base + timedelta(days=ttl_days(field))).isoformat(timespec="seconds")
    execute(
        """INSERT INTO field_sources
           (workspace_id, entity_type, entity_id, field, source, source_type,
            confidence, observed_at, verified_at, expires_at)
           VALUES(?,'lead',?,?,?,?,?,?,?,?)
           ON DUPLICATE KEY UPDATE source = VALUES(source), source_type = VALUES(source_type),
             confidence = VALUES(confidence), observed_at = VALUES(observed_at),
             verified_at = VALUES(verified_at), expires_at = VALUES(expires_at)""",
        (current_ws(), lead_id, field, source, source_type, conf, now,
         now if verified else None, expires),
    )


def record_many(lead_id: int, values: dict, source: str, source_type: str,
                confidence: float | None = None) -> list[str]:
    """Record provenance for each tracked field in `values` that holds a real
    value. Returns the fields recorded."""
    done = []
    for field, value in values.items():
        if field in TRACKED_FIELDS and value not in (None, "", 0):
            record(lead_id, field, source, source_type, confidence)
            done.append(field)
    return done


def mark_verified(lead_id: int, field: str) -> bool:
    """A human (or a verification provider) attests the current value is right."""
    now = utcnow()
    expires = (_parse(now) + timedelta(days=ttl_days(field))).isoformat(timespec="seconds")
    changed = execute(
        "UPDATE field_sources SET verified_at = ?, expires_at = ? WHERE workspace_id = ? "
        "AND entity_type = 'lead' AND entity_id = ? AND field = ?",
        (now, expires, current_ws(), lead_id, field),
    )
    return bool(changed)


def lineage_for(lead_id: int) -> list[dict]:
    """Annotated provenance rows for one lead, with live freshness."""
    rows = query(
        "SELECT field, source, source_type, confidence, observed_at, verified_at, expires_at "
        "FROM field_sources WHERE workspace_id = ? AND entity_type = 'lead' AND entity_id = ? "
        "ORDER BY field", (current_ws(), lead_id))
    now = _now()
    for row in rows:
        row["freshness"] = freshness_status(row["observed_at"], row["verified_at"],
                                            row["field"], now)
        row["effective_confidence"] = round(row["confidence"] * DECAY[row["freshness"]], 3)
        row["verified"] = bool(row["verified_at"])
    return rows


def summarise(rows: list[dict]) -> dict:
    """Roll field lineage up to a lead-level score.

    data_confidence_score (0-100) is the weight-averaged *effective* confidence of
    the tracked fields that have provenance - it measures trust in what we hold,
    not completeness (that is data_quality_score). Lead freshness is the worst
    freshness among the contact-critical fields present; next_verification_at is
    the earliest moment any tracked value crosses out of FRESH."""
    weighted = total_w = 0.0
    worst = None
    next_due: datetime | None = None
    last_verified: str | None = None
    for row in rows:
        w = FIELD_WEIGHTS.get(row["field"])
        if w is None:
            continue
        weighted += w * row["effective_confidence"]
        total_w += w
        if row["field"] in ("phone", "email", "website_url"):
            if worst is None or _RANK[row["freshness"]] > _RANK[worst]:
                worst = row["freshness"]
        base = _parse(row["verified_at"]) or _parse(row["observed_at"])
        if base:
            due = base + timedelta(days=ttl_days(row["field"]) * 0.5)
            if next_due is None or due < next_due:
                next_due = due
        if row["verified_at"] and (last_verified is None or row["verified_at"] > last_verified):
            last_verified = row["verified_at"]
    return {
        "data_confidence_score": round(100 * weighted / total_w) if total_w else None,
        "freshness_status": worst,
        "last_verified_at": last_verified,
        "next_verification_at": next_due.isoformat(timespec="seconds") if next_due else None,
    }


def refresh_lead(lead_id: int) -> dict:
    """Recompute and store the lead-level confidence/freshness columns."""
    summary = summarise(lineage_for(lead_id))
    execute(
        "UPDATE leads SET data_confidence_score = ?, freshness_status = ?, "
        "last_verified_at = ?, next_verification_at = ? WHERE id = ? AND workspace_id = ?",
        (summary["data_confidence_score"], summary["freshness_status"],
         summary["last_verified_at"], summary["next_verification_at"], lead_id, current_ws()),
    )
    return summary


def refresh_all(workspace_id: int | None = None) -> int:
    """Recompute every lead in the workspace (scheduled; ages values over time).
    One lineage query for the whole workspace, then a write per changed lead."""
    wid = workspace_id if workspace_id is not None else current_ws()
    grouped: dict[int, list[dict]] = {}
    now = _now()
    for row in query(
        "SELECT entity_id, field, source, source_type, confidence, observed_at, verified_at, "
        "expires_at FROM field_sources WHERE workspace_id = ? AND entity_type = 'lead'", (wid,)):
        row["freshness"] = freshness_status(row["observed_at"], row["verified_at"],
                                            row["field"], now)
        row["effective_confidence"] = row["confidence"] * DECAY[row["freshness"]]
        grouped.setdefault(row.pop("entity_id"), []).append(row)
    changed = 0
    current = {r["id"]: r for r in query(
        "SELECT id, data_confidence_score, freshness_status, next_verification_at, "
        "last_verified_at FROM leads WHERE workspace_id = ?", (wid,))}
    for lead_id, rows in grouped.items():
        before = current.get(lead_id)
        if before is None:
            continue
        s = summarise(rows)
        if all(before[k] == s[k] for k in s):
            continue
        execute(
            "UPDATE leads SET data_confidence_score = ?, freshness_status = ?, "
            "last_verified_at = ?, next_verification_at = ? WHERE id = ? AND workspace_id = ?",
            (s["data_confidence_score"], s["freshness_status"], s["last_verified_at"],
             s["next_verification_at"], lead_id, wid),
        )
        changed += 1
    return changed


def freshness_summary() -> dict:
    rows = query("SELECT COALESCE(freshness_status, 'UNKNOWN') AS status, COUNT(*) AS n "
                 "FROM leads WHERE workspace_id = ? GROUP BY freshness_status", (current_ws(),))
    return {r["status"]: r["n"] for r in rows}
