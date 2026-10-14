"""Organizations, workspaces and membership.

A *workspace* is the tenant boundary: every business row (leads, searches,
templates, suppression list, approval queue, ...) carries a workspace_id and
every query is filtered by it. An *organization* groups workspaces for billing
and administration. A user may belong to several workspaces; the active one is
chosen per request (X-Workspace-Id header, validated against membership) and
defaults to their lowest-numbered membership.
"""

from __future__ import annotations

import re

from .db import execute, query, query_one, utcnow

MEMBER_ROLES = ("owner", "admin", "member")


class TenancyError(ValueError):
    pass


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug[:100] or "workspace"


def memberships(user_id: int) -> list[dict]:
    return query(
        """SELECT w.id AS workspace_id, w.name AS workspace_name, w.organization_id,
                  m.role AS member_role
           FROM workspace_members m JOIN workspaces w ON w.id = m.workspace_id
           JOIN organizations o ON o.id = w.organization_id
           WHERE m.user_id = ? AND w.is_active = 1 AND o.is_active = 1
           ORDER BY w.id""",
        (user_id,),
    )


def resolve_workspace(user_id: int, requested: str | None) -> dict | None:
    """The membership row for the workspace this request acts in.

    Returns None when the user belongs to no active workspace. Raises
    TenancyError when `requested` names a workspace the user is not a member of -
    never silently falling back, so a stale header can't act on the wrong tenant.
    """
    rows = memberships(user_id)
    if requested not in (None, ""):
        try:
            wanted = int(requested)
        except ValueError as exc:
            raise TenancyError("Invalid workspace id") from exc
        for row in rows:
            if row["workspace_id"] == wanted:
                return row
        raise TenancyError("You are not a member of that workspace")
    return rows[0] if rows else None


def create_workspace(name: str, organization_id: int | None = None,
                     organization_name: str | None = None) -> int:
    """Create a workspace (and its organization when none is given) and seed
    its default templates. Returns the workspace id."""
    from .db import workspace_context
    from .seed import seed_templates

    name = (name or "").strip()
    if not name:
        raise TenancyError("Workspace name is required")
    now = utcnow()
    if organization_id is None:
        org_name = (organization_name or name).strip()
        slug = _unique_org_slug(slugify(org_name))
        organization_id = execute(
            "INSERT INTO organizations(name, slug, created_at) VALUES(?,?,?)",
            (org_name, slug, now),
        )
    elif not query_one("SELECT id FROM organizations WHERE id = ?", (organization_id,)):
        raise TenancyError("Organization not found")

    base = slugify(name)
    slug, n = base, 1
    while query_one("SELECT 1 AS x FROM workspaces WHERE organization_id = ? AND slug = ?",
                    (organization_id, slug)):
        n += 1
        slug = f"{base}-{n}"
    workspace_id = execute(
        "INSERT INTO workspaces(organization_id, name, slug, created_at) VALUES(?,?,?,?)",
        (organization_id, name, slug, now),
    )
    with workspace_context(workspace_id):
        seed_templates()
    return workspace_id


def _unique_org_slug(base: str) -> str:
    slug, n = base, 1
    while query_one("SELECT 1 AS x FROM organizations WHERE slug = ?", (slug,)):
        n += 1
        slug = f"{base}-{n}"
    return slug


def add_member(workspace_id: int, user_id: int, role: str = "member") -> None:
    if role not in MEMBER_ROLES:
        raise TenancyError(f"role must be one of {', '.join(MEMBER_ROLES)}")
    execute(
        "INSERT INTO workspace_members(workspace_id, user_id, role, created_at) "
        "VALUES(?,?,?,?) ON DUPLICATE KEY UPDATE role = VALUES(role)",
        (workspace_id, user_id, role, utcnow()),
    )


def remove_member(workspace_id: int, user_id: int) -> bool:
    owners = query_one(
        "SELECT COUNT(*) AS n FROM workspace_members WHERE workspace_id = ? AND role = 'owner'",
        (workspace_id,),
    )["n"]
    target = query_one(
        "SELECT role FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
        (workspace_id, user_id),
    )
    if not target:
        return False
    if target["role"] == "owner" and owners <= 1:
        raise TenancyError("A workspace must keep at least one owner")
    execute("DELETE FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
            (workspace_id, user_id))
    return True


def can_manage_user(actor: dict, user_id: int) -> bool:
    """May `actor` administer `user_id`? The platform superadmin may; anyone else
    only for members of their own active workspace."""
    from .db import current_ws

    if actor.get("role") == "superadmin":
        return True
    return bool(query_one(
        "SELECT 1 AS x FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
        (current_ws(), user_id),
    ))


def default_workspace_id() -> int:
    return query_one("SELECT id FROM workspaces ORDER BY id LIMIT 1")["id"]
