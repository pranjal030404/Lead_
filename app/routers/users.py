from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from pymysql.err import IntegrityError

from ..db import current_ws, execute, query, query_one, utcnow
from .. import permissions
from ..security import hash_password, require_role, revoke_sessions
from ..tenancy import add_member, can_manage_user, remove_member

router = APIRouter(prefix="/api", tags=["users"])

ROLES = ("user", "viewer", "admin", "superadmin")      # viewer = read-only, sees the whole workspace


class UserPayload(BaseModel):
    username: str = ""
    password: str = ""
    full_name: str = ""
    email: str = ""
    role: str = "user"
    is_active: bool = True


def _safe(user: dict) -> dict:
    out = {k: v for k, v in user.items() if k not in ("password_hash", "mfa_secret")}
    if "denied_features" in out:
        out["denied_features"] = permissions.parse(out["denied_features"])
    return out


@router.get("/users")
def list_users(actor: dict = Depends(require_role("superadmin", "admin")),
               q: str | None = None, page: int = Query(0, ge=0), per_page: int = Query(100, le=500)):
    # The platform superadmin sees every account; a workspace admin only the
    # members of their own workspace.
    clauses: list[str] = []
    params: list = []
    if actor["role"] != "superadmin":
        clauses.append("id IN (SELECT user_id FROM workspace_members WHERE workspace_id = ?)")
        params.append(current_ws())
    if q:
        clauses.append("(username LIKE ? OR full_name LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = query(
        "SELECT id, username, full_name, email, role, is_active, denied_features, created_at, updated_at "
        f"FROM users {where} ORDER BY id DESC LIMIT ? OFFSET ?",
        (*params, per_page, page * per_page),
    )
    for row in rows:
        row["denied_features"] = permissions.parse(row.get("denied_features"))
    return {"rows": rows, "page": page, "per_page": per_page,
            "total": query_one(f"SELECT COUNT(*) AS n FROM users {where}", tuple(params))["n"]}


def _check_email(email: str, exclude_id: int | None = None) -> str:
    """Email is a login identifier, so it must be well-formed and unique (case-insensitive).
    Blank is allowed - the account then signs in by username only."""
    email = (email or "").strip()
    if not email:
        return ""
    if len(email) > 255 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]{2,}", email):
        raise HTTPException(400, "That email address doesn't look valid")
    clash = query_one("SELECT 1 AS x FROM users WHERE LOWER(email) = LOWER(?) AND id <> ?",
                      (email, exclude_id or 0))
    if clash:
        raise HTTPException(409, "That email is already used by another account")
    return email


@router.post("/users")
def create_user(payload: UserPayload,
                actor: dict = Depends(require_role("superadmin", "admin"))):
    username = payload.username.strip()
    if not username or not payload.password:
        raise HTTPException(400, "username and password are required")
    role = payload.role or "user"
    if role not in ROLES:
        raise HTTPException(400, f"role must be one of {', '.join(ROLES)}")
    if role != "user" and actor["role"] != "superadmin":
        raise HTTPException(403, "Only the superadmin can grant privileged roles")

    payload.email = _check_email(payload.email)
    now = utcnow()
    workspace_id = current_ws()  # fails loudly (403) if the actor has no workspace
    try:
        new_id = execute(
            """INSERT INTO users(username, password_hash, full_name, email, role,
               is_active, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (username, hash_password(payload.password), payload.full_name.strip(),
             payload.email.strip(), role, 1 if payload.is_active else 0, now, now),
        )
    except IntegrityError as exc:
        raise HTTPException(409, "Username already taken") from exc
    # New accounts join the creator's workspace.
    add_member(workspace_id, new_id, "admin" if role in ("admin", "superadmin") else "member")
    return _safe(query_one("SELECT * FROM users WHERE username = ?", (username,)))


@router.patch("/users/{user_id}")
def update_user(user_id: int, payload: UserPayload,
                actor: dict = Depends(require_role("superadmin", "admin"))):
    row = query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not row or not can_manage_user(actor, user_id):
        raise HTTPException(404, "User not found")

    role = (payload.role or row["role"])
    if role not in ROLES:
        raise HTTPException(400, f"role must be one of {', '.join(ROLES)}")
    if role != row["role"] and actor["role"] != "superadmin":
        raise HTTPException(403, "Only the superadmin can change roles")

    result = query_one("SELECT 1 AS x FROM users WHERE username = ? AND id <> ?",
                       (payload.username.strip(), user_id))
    if result:
        raise HTTPException(409, "Username already taken")
    payload.email = _check_email(payload.email, user_id)

    if payload.password:
        execute(
            "UPDATE users SET username = ?, password_hash = ?, full_name = ?, email = ?, "
            "role = ?, is_active = ?, updated_at = ? WHERE id = ?",
            (payload.username.strip(), hash_password(payload.password),
             payload.full_name.strip(), payload.email.strip(), role,
             1 if payload.is_active else 0, utcnow(), user_id),
        )
    else:
        execute(
            "UPDATE users SET username = ?, full_name = ?, email = ?, role = ?, "
            "is_active = ?, updated_at = ? WHERE id = ?",
            (payload.username.strip(), payload.full_name.strip(), payload.email.strip(),
             role, 1 if payload.is_active else 0, utcnow(), user_id),
        )
    if payload.password or not payload.is_active:
        revoke_sessions(user_id)       # a new password / a deactivation ends old sessions
    saved = query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    return _safe(saved)


@router.delete("/users/{user_id}")
def delete_user(user_id: int, actor: dict = Depends(require_role("superadmin", "admin"))):
    if actor["id"] == user_id:
        raise HTTPException(400, "You cannot delete your own account")
    row = query_one("SELECT id, role FROM users WHERE id = ?", (user_id,))
    if not row or not can_manage_user(actor, user_id):
        raise HTTPException(404, "User not found")
    if actor["role"] != "superadmin":
        # A workspace admin removes the person from their workspace; the account
        # itself is only deleted once it belongs to no workspace at all.
        if row["role"] == "superadmin":
            raise HTTPException(403, "Only the superadmin can remove a superadmin")
        try:
            remove_member(current_ws(), user_id)
        except Exception as exc:  # noqa: BLE001 - TenancyError: last owner
            raise HTTPException(400, str(exc)) from exc
        if not query_one("SELECT 1 AS x FROM workspace_members WHERE user_id = ?", (user_id,)):
            execute("DELETE FROM users WHERE id = ?", (user_id,))
        return {"deleted": user_id}
    if row["role"] == "superadmin" and query_one(
        "SELECT COUNT(*) AS n FROM users WHERE role = 'superadmin' AND is_active = 1"
    )["n"] <= 1:
        raise HTTPException(400, "Every system needs at least one active superadmin")
    execute("DELETE FROM users WHERE id = ?", (user_id,))
    return {"deleted": user_id}

# ------------------------------------------------------- feature permissions --

class PermissionsPayload(BaseModel):
    denied: list[str]


@router.get("/permissions/features")
def permission_features(_: dict = Depends(require_role("superadmin", "admin"))):
    return {"features": permissions.catalogue()}


@router.patch("/users/{user_id}/permissions")
def set_permissions(user_id: int, payload: PermissionsPayload,
                    actor: dict = Depends(require_role("superadmin", "admin"))):
    """Replace the set of features switched OFF for this user (empty list = full access)."""
    row = query_one("SELECT id, role FROM users WHERE id = ?", (user_id,))
    if not row or not can_manage_user(actor, user_id):
        raise HTTPException(404, "User not found")
    if row["role"] in ("admin", "superadmin"):
        raise HTTPException(400, "Admins always have full access - change their role first")
    unknown = [f for f in payload.denied if f not in permissions.FEATURES]
    if unknown:
        raise HTTPException(400, f"Unknown feature(s): {', '.join(unknown)}")
    denied = permissions.parse(payload.denied)
    execute("UPDATE users SET denied_features = ?, updated_at = ? WHERE id = ?",
            (json.dumps(denied) if denied else None, utcnow(), user_id))
    return {"id": user_id, "denied_features": denied}
