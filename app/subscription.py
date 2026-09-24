"""Subscription service: what a user is entitled to, and search accounting.

The model is deliberately simple - one active plan per user. Any purchase or
assignment expires the user's previous active subscription, so `active_subscription`
never needs to rank competing plans.

Search gating (Part of the paid-plans feature):
  - superadmin is never gated
  - everyone else must hold an *active* subscription with searches left
  - each COMPLETED search counts one search against the plan's search_limit,
    so the user always has the full picture of what they have left
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import settings
from .db import execute, get_setting, query_one

ROLES_BY_POWER = {"user": 0, "admin": 1, "superadmin": 2}


def active_subscription(user_id: int) -> dict | None:
    """The user's current plan, or None. Ordering/expiry handled in SQL."""
    return query_one(
        """SELECT us.id, us.user_id, us.plan_id, us.status, us.period_start,
                  us.period_end, us.searches_used, us.channel, us.payment_status,
                  us.payment_ref, p.name AS plan_name, p.description, p.price,
                  p.currency, p.period_days, p.search_limit,
                  p.max_results_per_search
           FROM user_subscriptions us
           JOIN plans p ON p.id = us.plan_id
           WHERE us.user_id = ? AND us.status = 'active'
             AND us.period_end >= ?
           ORDER BY us.id DESC LIMIT 1""",
        (user_id, today()),
    )


def latest_subscription(user_id: int) -> dict | None:
    """The most recent row regardless of state (for "My Plan" history/notice)."""
    return query_one(
        "SELECT * FROM user_subscriptions WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
    )


def payment_enabled() -> bool:
    return get_setting("payment_enabled", "off") == "on"


def set_payment_enabled(enabled: bool) -> None:
    from .db import set_setting
    set_setting("payment_enabled", "on" if enabled else "off")


def check_search_entitlement(user: dict) -> dict:
    """Gate one manual search. Returns ok + the subscription that counted."""
    if user.get("role") == "superadmin":
        return {"ok": True, "superadmin": True, "subscription": None}

    sub = active_subscription(user["id"])
    if not sub:
        return {"ok": False, "reason": "no_active_subscription", "subscription": None}
    if sub["searches_used"] >= sub["search_limit"]:
        return {"ok": False, "reason": "search_limit_reached", "subscription": sub}
    return {"ok": True, "superadmin": False, "subscription": sub}


def max_results_for(user: dict, requested: int) -> int:
    """Cap a search's result count for gated users by their plan."""

    role_cap = settings.max_results_per_search
    if user.get("role") == "superadmin":
        return min(requested, role_cap)
    sub = active_subscription(user["id"])
    plan_cap = sub["max_results_per_search"] if sub else role_cap
    if not plan_cap:
        return min(requested, role_cap)
    return min(requested, plan_cap, role_cap)


def count_search(user_id: int | None) -> None:
    """Charge one search against the user's active subscription."""
    if not user_id:
        return
    execute(
        """UPDATE user_subscriptions us
           JOIN plans p ON p.id = us.plan_id
           JOIN users u ON u.id = us.user_id
           SET us.searches_used = us.searches_used + 1
           WHERE us.user_id = ? AND us.status = 'active' AND us.period_end >= ?""",
        (user_id, today()),
    )


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def period_end_for(period_days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=period_days)).date().isoformat()


def expire_previous_active(user_id: int, keep_sub_id: int | None = None) -> None:
    """Roll any other active subscriptions off (one plan per user)."""
    if keep_sub_id:
        execute(
            "UPDATE user_subscriptions SET status = 'expired', updated_at = ? "
            "WHERE user_id = ? AND status = 'active' AND id <> ?",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"),
             user_id, keep_sub_id),
        )
    else:
        execute(
            "UPDATE user_subscriptions SET status = 'expired', updated_at = ? "
            "WHERE user_id = ? AND status = 'active'",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), user_id),
        )