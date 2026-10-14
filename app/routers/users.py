from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from pymysql.err import IntegrityError

from ..db import current_ws, execute, query, query_one, utcnow
from ..security import hash_password, require_role
from ..tenancy import add_member, can_manage_user, remove_member

router = APIRouter(prefix="/api", tags=["users"])

ROLES = ("user", "admin", "superadmin")


class UserPayload(BaseModel):
    username: str = ""
    password: str = ""
    full_name: str = ""
    email: str = ""
    role: str = "user"
    is_active: bool = True


def _safe(user: dict) -> dict:
    return {k: v for k, v in user.items() if k != "password_hash"}


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
        "SELECT id, username, full_name, email, role, is_active, created_at, updated_at "
        f"FROM users {where} ORDER BY id DESC LIMIT ? OFFSET ?",
        (*params, per_page, page * per_page),
    )
    return {"rows": rows, "page": page, "per_page": per_page,
            "total": query_one(f"SELECT COUNT(*) AS n FROM users {where}", tuple(params))["n"]}


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