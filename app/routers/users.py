from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from pymysql.err import IntegrityError

from ..db import execute, query, query_one, utcnow
from ..security import hash_password, require_role

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
def list_users(_: dict = Depends(require_role("superadmin", "admin")),
               q: str | None = None, page: int = Query(0, ge=0), per_page: int = Query(100, le=500)):
    where = ""
    params: tuple = ()
    if q:
        where = "WHERE username LIKE ? OR full_name LIKE ?"
        like = f"%{q}%"
        params = (like, like)
    rows = query(
        "SELECT id, username, full_name, email, role, is_active, created_at, updated_at "
        f"FROM users {where} ORDER BY id DESC LIMIT ? OFFSET ?",
        (*params, per_page, page * per_page),
    )
    return {"rows": rows, "page": page, "per_page": per_page,
            "total": query_one("SELECT COUNT(*) AS n FROM users" + (f" {where}" if where else ""), params)["n"]}


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
    try:
        execute(
            """INSERT INTO users(username, password_hash, full_name, email, role,
               is_active, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (username, hash_password(payload.password), payload.full_name.strip(),
             payload.email.strip(), role, 1 if payload.is_active else 0, now, now),
        )
    except IntegrityError as exc:
        raise HTTPException(409, "Username already taken") from exc
    return _safe(query_one("SELECT * FROM users WHERE username = ?", (username,)))


@router.patch("/users/{user_id}")
def update_user(user_id: int, payload: UserPayload,
                actor: dict = Depends(require_role("superadmin", "admin"))):
    row = query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not row:
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
    if not row:
        raise HTTPException(404, "User not found")
    if row["role"] == "superadmin" and query_one(
        "SELECT COUNT(*) AS n FROM users WHERE role = 'superadmin' AND is_active = 1"
    )["n"] <= 1:
        raise HTTPException(400, "Every system needs at least one active superadmin")
    execute("DELETE FROM users WHERE id = ?", (user_id,))
    return {"deleted": user_id}