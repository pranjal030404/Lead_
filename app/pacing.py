"""Send pacing: quiet hours, daily/hourly caps and human-like jitter. Pure decisions.

Mailing in a burst at 3am is the fastest way to burn a sending domain. `send_decision`
answers "may we send now, and how many?" from plain inputs, so the rules are unit-testable
and `send_approved` only has to ask.
"""

from __future__ import annotations

import random
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _tz(name: str):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def in_quiet_hours(now: datetime, start_hour: int, end_hour: int, tz_name: str = "UTC") -> bool:
    """True when local time is inside [start, end). Handles windows that cross midnight
    (21 -> 8). start == end disables quiet hours."""
    if start_hour == end_hour:
        return False
    hour = now.astimezone(_tz(tz_name)).hour
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def send_decision(now: datetime, sent_last_hour: int, sent_today: int, *, hourly_cap: int,
                  daily_cap: int, quiet_start: int, quiet_end: int, tz_name: str = "UTC",
                  weekdays_only: bool = False) -> dict:
    """-> {allowed, budget, reason}. `budget` is how many may go out right now."""
    local = now.astimezone(_tz(tz_name))
    if weekdays_only and local.weekday() >= 5:
        return {"allowed": False, "budget": 0, "reason": "weekend - sending is weekdays only"}
    if in_quiet_hours(now, quiet_start, quiet_end, tz_name):
        return {"allowed": False, "budget": 0,
                "reason": f"quiet hours ({quiet_start:02d}:00-{quiet_end:02d}:00 {tz_name})"}
    hourly_left = max(0, hourly_cap - sent_last_hour)
    daily_left = max(0, daily_cap - sent_today) if daily_cap > 0 else hourly_left
    budget = min(hourly_left, daily_left) if hourly_cap > 0 else daily_left
    if budget <= 0:
        which = "daily" if daily_cap > 0 and daily_left == 0 else "hourly"
        used, cap = (sent_today, daily_cap) if which == "daily" else (sent_last_hour, hourly_cap)
        return {"allowed": False, "budget": 0, "reason": f"{which} cap reached ({used}/{cap})"}
    return {"allowed": True, "budget": budget, "reason": "ok"}


def jitter_seconds(min_s: int = 20, max_s: int = 90, rng: random.Random | None = None) -> float:
    """Random gap between consecutive sends so traffic doesn't look machine-regular."""
    if max_s < min_s:
        min_s, max_s = max_s, min_s
    return (rng or random).uniform(min_s, max_s)
