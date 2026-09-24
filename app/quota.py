"""API quota guard (Part 7.2).

This is the SECOND layer of protection. The first layer is a Requests-per-day
Quota limit set on the API in the Google Cloud Console - a Cloud Billing budget
alert only emails you, it does not stop spending. Set both.

`check_and_reserve` raises QuotaExceeded *before* the call goes out, so a runaway
loop stops rather than warns.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .config import settings
from .db import execute, get_conn, query, query_one, today


class QuotaExceeded(RuntimeError):
    def __init__(self, service: str, used: int, cap: int):
        self.service, self.used, self.cap = service, used, cap
        super().__init__(f"{service} daily cap reached: {used}/{cap} calls. Stopping.")


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def calls_today(service: str = "google_places") -> int:
    row = query_one(
        "SELECT COALESCE(SUM(calls), 0) AS n FROM api_usage WHERE day = ? AND service = ?",
        (today(), service),
    )
    return int(row["n"]) if row else 0


def calls_this_month(service: str) -> int:
    row = query_one(
        "SELECT COALESCE(SUM(calls), 0) AS n FROM api_usage WHERE day LIKE ? AND service = ?",
        (f"{_month()}-%", service),
    )
    return int(row["n"]) if row else 0


def cap_for(service: str) -> int:
    if service == "hunter":
        return settings.max_hunter_calls_per_month
    return settings.max_google_calls_per_day


def check_and_reserve(service: str = "google_places", tier: str = "default", n: int = 1) -> None:
    """Atomically reserve n calls, or raise QuotaExceeded without spending any."""
    cap = cap_for(service)
    monthly = service == "hunter"
    with get_conn() as conn:
        conn.execute("START TRANSACTION")
        try:
            if monthly:
                row = conn.execute(
                    "SELECT COALESCE(SUM(calls),0) AS n FROM api_usage "
                    "WHERE day LIKE ? AND service = ?",
                    (f"{_month()}-%", service),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COALESCE(SUM(calls),0) AS n FROM api_usage "
                    "WHERE day = ? AND service = ?",
                    (today(), service),
                ).fetchone()
            used = int(row["n"])
            if used + n > cap:
                conn.execute("ROLLBACK")
                raise QuotaExceeded(service, used, cap)
            conn.execute(
                "INSERT INTO api_usage(day, service, tier, calls) VALUES(?,?,?,?) "
                "ON DUPLICATE KEY UPDATE calls = calls + VALUES(calls)",
                (today(), service, tier, n),
            )
            conn.execute("COMMIT")
        except QuotaExceeded:
            raise
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # Part 7.6 - warn on the approach, not the wall. Outside the transaction so
    # a mail failure can never roll back a reservation we already committed.
    if not monthly:
        from . import notify

        notify.alert_quota(used + n, cap)


def usage_summary() -> dict:
    google_used = calls_today("google_places")
    google_cap = settings.max_google_calls_per_day
    hunter_used = calls_this_month("hunter")
    by_tier = query(
        "SELECT tier, SUM(calls) AS calls FROM api_usage "
        "WHERE day = ? AND service = 'google_places' GROUP BY tier",
        (today(),),
    )
    pct = round(google_used / google_cap * 100) if google_cap else 0
    return {
        "google": {
            "used_today": google_used,
            "cap": google_cap,
            "remaining": max(google_cap - google_used, 0),
            "pct": pct,
            "warning": pct >= settings.quota_warn_pct,
            "by_tier": {r["tier"]: r["calls"] for r in by_tier},
        },
        "hunter": {
            "used_this_month": hunter_used,
            "cap": settings.max_hunter_calls_per_month,
            "remaining": max(settings.max_hunter_calls_per_month - hunter_used, 0),
        },
        "provider": settings.provider,
    }


def record_free_call(service: str, tier: str = "default") -> None:
    """Log usage for a provider with no billing (OSM), for visibility only."""
    execute(
        "INSERT INTO api_usage(day, service, tier, calls) VALUES(?,?,?,1) "
        "ON DUPLICATE KEY UPDATE calls = calls + 1",
        (today(), service, tier),
    )
