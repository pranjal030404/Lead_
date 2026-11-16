"""Rating/review snapshots over time -> review-growth signal (the missing RAPID_REVIEW_GROWTH)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .db import current_ws, execute, query, query_one, utcnow

MIN_GAP_DAYS = 6


def record(lead: dict) -> bool:
    """Store today's reviews/rating if we hold them and the last snapshot is old enough."""
    if lead.get("google_reviews") is None:
        return False
    last = query_one("SELECT at FROM rating_snapshots WHERE workspace_id = ? AND lead_id = ? ORDER BY id DESC LIMIT 1",
                     (current_ws(), lead["id"]))
    if last:
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(last["at"])
            if age < timedelta(days=MIN_GAP_DAYS):
                return False
        except ValueError:
            pass
    execute("INSERT INTO rating_snapshots(workspace_id, lead_id, at, reviews, rating) VALUES(?,?,?,?,?)",
            (current_ws(), lead["id"], utcnow(), lead["google_reviews"], lead.get("google_rating")))
    return True


def history(lead_id: int) -> list[dict]:
    return query("SELECT at, reviews, rating FROM rating_snapshots WHERE workspace_id = ? AND lead_id = ? "
                 "ORDER BY id", (current_ws(), lead_id))
