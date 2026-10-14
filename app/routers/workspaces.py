"""Workspace and membership administration.

Visibility rule: a user only ever sees workspaces they are a member of. The
superadmin (platform operator) may additionally list every workspace and create
new ones, but still reads a tenant's *data* only by being a member of it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..db import current_ws, query, query_one
from ..security import require_role
from ..tenancy import (TenancyError, add_member, create_workspace, memberships,
                       remove_member)

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


def _require_workspace_admin(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    if user["role"] != "superadmin" and user.get("workspace_role") not in ("owner", "admin"):
        raise HTTPException(403, "Requires workspace owner or admin")
    return user


@router.get("/mine")
def my_workspaces(request: Request):
    user = request.state.user
    return {"active": user.get("workspace_id"), "workspaces": memberships(user["id"])}


@router.get("")
def all_workspaces(_: dict = Depends(require_role("superadmin"))):
    return query(
        """SELECT w.id, w.name, w.slug, w.is_active, w.organization_id,
                  o.name AS organization_name,
                  (SELECT COUNT(*) FROM workspace_members m WHERE m.workspace_id = w.id)
                      AS members
           FROM workspaces w JOIN organizations o ON o.id = w.organization_id
           ORDER BY w.id"""
    )


class WorkspacePayload(BaseModel):
    name: str
    organization_id: int | None = None
    organization_name: str | None = None
    owner_user_id: int | None = None


@router.post("")
def create(payload: WorkspacePayload, _: dict = Depends(require_role("superadmin"))):
    try:
        workspace_id = create_workspace(payload.name, payload.organization_id,
                                        payload.organization_name)
    except TenancyError as exc:
        raise HTTPException(400, str(exc)) from exc
    if payload.owner_user_id:
        if not query_one("SELECT id FROM users WHERE id = ?", (payload.owner_user_id,)):
            raise HTTPException(404, "Owner user not found")
        add_member(workspace_id, payload.owner_user_id, "owner")
    return query_one("SELECT * FROM workspaces WHERE id = ?", (workspace_id,))


@router.get("/current/members")
def list_members(request: Request):
    _require_workspace_admin(request)
    return query(
        """SELECT u.id, u.username, u.full_name, u.email, u.is_active, m.role AS member_role
           FROM workspace_members m JOIN users u ON u.id = m.user_id
           WHERE m.workspace_id = ? ORDER BY u.id""",
        (current_ws(),),
    )


class MemberPayload(BaseModel):
    user_id: int
    role: str = "member"


@router.post("/current/members")
def put_member(payload: MemberPayload, request: Request):
    actor = _require_workspace_admin(request)
    if not query_one("SELECT id FROM users WHERE id = ?", (payload.user_id,)):
        raise HTTPException(404, "User not found")
    if payload.role == "owner" and actor["role"] != "superadmin" \
            and actor.get("workspace_role") != "owner":
        raise HTTPException(403, "Only an owner can grant ownership")
    try:
        add_member(current_ws(), payload.user_id, payload.role)
    except TenancyError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@router.delete("/current/members/{user_id}")
def delete_member(user_id: int, request: Request):
    _require_workspace_admin(request)
    try:
        removed = remove_member(current_ws(), user_id)
    except TenancyError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not removed:
        raise HTTPException(404, "Not a member of this workspace")
    return {"removed": user_id}
