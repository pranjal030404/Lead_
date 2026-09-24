from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from pymysql.err import IntegrityError

from ..db import execute, query, query_one, utcnow
from ..security import require_role
from ..subscription import (active_subscription, expire_previous_active,
                            payment_enabled, period_end_for, set_payment_enabled)

router = APIRouter(prefix="/api", tags=["plans"])


class PlanPayload(BaseModel):
    name: str
    description: str = ""
    price: float = 0
    currency: str = "INR"
    period_days: int = 30
    search_limit: int = 50
    max_results_per_search: int = 20
    is_active: bool = True


class AssignPayload(BaseModel):
    plan_id: int
    period_days: int | None = None


class OrderPayload(BaseModel):
    plan_id: int
    payment_ref: str = ""


class StatusPayload(BaseModel):
    enabled: bool


def _get_plan(plan_id: int) -> dict:
    plan = query_one("SELECT * FROM plans WHERE id = ?", (plan_id,))
    if not plan:
        raise HTTPException(404, "Plan not found")
    return plan


# ------------------------------------------------------------------- plans --

@router.get("/plans")
def list_plans(request: Request):
    user = getattr(request.state, "user", None)
    if user and user.get("role") in ("superadmin", "admin"):
        return {"rows": query("SELECT * FROM plans ORDER BY price ASC")}
    return {"rows": query("SELECT * FROM plans WHERE is_active = 1 ORDER BY price ASC")}


@router.post("/plans")
def create_plan(payload: PlanPayload,
                _: dict = Depends(require_role("superadmin", "admin"))):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "name is required")
    if payload.period_days <= 0 or payload.search_limit < 0:
        raise HTTPException(400, "period_days must be positive and search_limit non-negative")
    now = utcnow()
    try:
        execute(
            """INSERT INTO plans(name, description, price, currency, period_days,
               search_limit, max_results_per_search, is_active, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (name, payload.description.strip(), payload.price, payload.currency,
             payload.period_days, payload.search_limit, payload.max_results_per_search,
             1 if payload.is_active else 0, now, now),
        )
    except IntegrityError as exc:
        raise HTTPException(409, "A plan with that name already exists") from exc
    return query_one("SELECT * FROM plans WHERE name = ?", (name,))


@router.patch("/plans/{plan_id}")
def update_plan(plan_id: int, payload: PlanPayload,
                _: dict = Depends(require_role("superadmin", "admin"))):
    plan = _get_plan(plan_id)
    if payload.period_days <= 0 or payload.search_limit < 0:
        raise HTTPException(400, "period_days must be positive and search_limit non-negative")
    try:
        execute(
            """UPDATE plans SET name = ?, description = ?, price = ?, currency = ?,
               period_days = ?, search_limit = ?, max_results_per_search = ?,
               is_active = ?, updated_at = ? WHERE id = ?""",
            (payload.name.strip(), payload.description.strip(), payload.price,
             payload.currency, payload.period_days, payload.search_limit,
             payload.max_results_per_search, 1 if payload.is_active else 0,
             utcnow(), plan_id),
        )
    except IntegrityError as exc:
        raise HTTPException(409, "A plan with that name already exists") from exc
    return query_one("SELECT * FROM plans WHERE id = ?", (plan_id,))


@router.delete("/plans/{plan_id}")
def delete_plan(plan_id: int, _: dict = Depends(require_role("superadmin", "admin"))):
    _get_plan(plan_id)
    execute("DELETE FROM plans WHERE id = ?", (plan_id,))
    return {"deleted": plan_id}


# ------------------------------------------------------------ subscriptions --

@router.get("/subscriptions")
def list_subscriptions(_: dict = Depends(require_role("superadmin", "admin"))):
    return query(
        """SELECT us.id, us.user_id, us.plan_id, us.status, us.period_start,
                  us.period_end, us.searches_used, us.channel, us.payment_status,
                  us.payment_ref, us.created_at, u.username, u.full_name,
                  p.name AS plan_name, p.price, p.currency
           FROM user_subscriptions us
           JOIN users u ON u.id = us.user_id
           JOIN plans p ON p.id = us.plan_id
           ORDER BY us.id DESC LIMIT 200"""
    )


@router.post("/users/{user_id}/subscriptions")
def assign_subscription(user_id: int, payload: AssignPayload,
                        actor: dict = Depends(require_role("superadmin", "admin"))):
    target = query_one("SELECT id, username FROM users WHERE id = ?", (user_id,))
    if not target:
        raise HTTPException(404, "User not found")
    plan = _get_plan(payload.plan_id)
    days = payload.period_days or plan["period_days"]
    now = utcnow()
    expire_previous_active(user_id)
    execute(
        """INSERT INTO user_subscriptions
           (user_id, plan_id, status, period_start, period_end, searches_used,
            channel, payment_status, created_at, updated_at)
           VALUES(?,?,'active',?,?,0,'admin','paid',?,?)""",
        (user_id, plan["id"], now[:10], period_end_for(days), now, now),
    )
    return query_one(
        "SELECT * FROM user_subscriptions WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
    )


@router.post("/me/subscriptions")
def order_subscription(payload: OrderPayload, request: Request):
    """Self-serve order. Only works while the payment toggle is on; the admin
    approves/marks-paid orders from the Plans page."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    if not payment_enabled():
        raise HTTPException(403, "Self-serve ordering is currently disabled. Contact an admin.")

    plan = _get_plan(payload.plan_id)
    if not plan["is_active"]:
        raise HTTPException(400, "This plan is no longer available")

    pending = query_one(
        "SELECT * FROM user_subscriptions WHERE user_id = ? AND plan_id = ? AND status = 'pending'",
        (user["id"], plan["id"]),
    )
    if pending:
        return pending

    now = utcnow()
    execute(
        """INSERT INTO user_subscriptions
           (user_id, plan_id, status, searches_used, channel, payment_status,
            payment_ref, created_at, updated_at)
           VALUES(?,?,'pending',0,'self','unpaid',?,?,?)""",
        (user["id"], plan["id"], payload.payment_ref.strip(), now, now),
    )
    return query_one(
        "SELECT * FROM user_subscriptions WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user["id"],),
    )


@router.post("/subscriptions/{sub_id}/approve")
def approve_subscription(sub_id: int,
                         _: dict = Depends(require_role("superadmin", "admin"))):
    row = query_one("SELECT * FROM user_subscriptions WHERE id = ?", (sub_id,))
    if not row:
        raise HTTPException(404, "Subscription not found")
    plan = _get_plan(row["plan_id"])
    expire_previous_active(row["user_id"], keep_sub_id=sub_id)
    now = utcnow()
    execute(
        """UPDATE user_subscriptions SET status = 'active', payment_status = 'paid',
           period_start = ?, period_end = ?, searches_used = 0, updated_at = ?
           WHERE id = ?""",
        (now[:10], period_end_for(plan["period_days"]), now, sub_id),
    )
    return query_one("SELECT * FROM user_subscriptions WHERE id = ?", (sub_id,))


@router.post("/subscriptions/{sub_id}/reject")
def reject_subscription(sub_id: int,
                        _: dict = Depends(require_role("superadmin", "admin"))):
    row = query_one("SELECT * FROM user_subscriptions WHERE id = ?", (sub_id,))
    if not row:
        raise HTTPException(404, "Subscription not found")
    execute(
        "UPDATE user_subscriptions SET status = 'rejected', updated_at = ? WHERE id = ?",
        (utcnow(), sub_id),
    )
    return {"rejected": sub_id}


@router.post("/subscriptions/{sub_id}/cancel")
def cancel_subscription(sub_id: int,
                        _: dict = Depends(require_role("superadmin", "admin"))):
    row = query_one("SELECT * FROM user_subscriptions WHERE id = ?", (sub_id,))
    if not row:
        raise HTTPException(404, "Subscription not found")
    execute(
        "UPDATE user_subscriptions SET status = 'cancelled', updated_at = ? WHERE id = ?",
        (utcnow(), sub_id),
    )
    return {"cancelled": sub_id}


@router.get("/me/subscription")
def my_subscription(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return {"subscription": active_subscription(user["id"])}


@router.get("/me/subscriptions")
def my_subscriptions(request: Request):
    """The user's own order history, for the "My Plan" page."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return query(
        """SELECT us.id, us.plan_id, us.status, us.period_start, us.period_end,
                  us.searches_used, us.channel, us.payment_status, us.payment_ref,
                  us.created_at, p.name AS plan_name, p.price, p.currency,
                  p.search_limit, p.max_results_per_search
           FROM user_subscriptions us
           JOIN plans p ON p.id = us.plan_id
           WHERE us.user_id = ?
           ORDER BY us.id DESC LIMIT 20""",
        (user["id"],),
    )


# ------------------------------------------------------------- payment mode --

@router.get("/settings/payment")
def payment_mode(request: Request):
    """Whether self-serve ordering is on. Authed users see it so the UI can
    hide or show the Buy button."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    enabled = payment_enabled()
    active_count = query_one(
        "SELECT COUNT(*) AS n FROM user_subscriptions WHERE status = 'active'"
    )["n"]
    pending_count = query_one(
        "SELECT COUNT(*) AS n FROM user_subscriptions WHERE status = 'pending'"
    )["n"]
    return {"enabled": enabled, "active_subscriptions": active_count,
            "pending_orders": pending_count}


@router.post("/settings/payment")
def set_payment_mode(payload: StatusPayload,
                     _: dict = Depends(require_role("superadmin", "admin"))):
    set_payment_enabled(payload.enabled)
    return {"enabled": bool(payload.enabled)}