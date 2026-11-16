"""CRM extras: follow-up tasks, webhooks, audit log, and MFA management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import auditlog, mfa, netsafe, webhooks
from ..branding import get_branding
from ..db import current_ws, execute, query, query_one, utcnow
from ..security import require_role
from .leads import _own_lead

router = APIRouter(prefix="/api", tags=["crm"])


# ------------------------------------------------------------------ tasks ---
class TaskPayload(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    lead_id: int | None = None
    due_at: str | None = Field(None, max_length=40)       # ISO date or datetime
    notes: str = Field("", max_length=2000)


class TaskPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    due_at: str | None = None
    notes: str | None = None
    status: str | None = Field(None, pattern="^(OPEN|DONE)$")


def _own_task(task_id: int) -> dict:
    row = query_one("SELECT * FROM tasks WHERE id = ? AND workspace_id = ?", (task_id, current_ws()))
    if not row:
        raise HTTPException(404, "Task not found")
    return row


@router.get("/tasks")
def list_tasks(status: str = Query("OPEN", pattern="^(OPEN|DONE|ALL)$"), lead_id: int | None = None,
               overdue: bool = False, limit: int = Query(200, le=500)):
    clauses, params = ["workspace_id = ?"], [current_ws()]
    if status != "ALL":
        clauses.append("status = ?"); params.append(status)
    if lead_id is not None:
        clauses.append("lead_id = ?"); params.append(lead_id)
    if overdue:
        clauses.append("status = 'OPEN' AND due_at IS NOT NULL AND due_at < ?"); params.append(utcnow())
    rows = query(f"SELECT * FROM tasks WHERE {' AND '.join(clauses)} "
                 f"ORDER BY due_at IS NULL, due_at, id LIMIT ?", (*params, limit))
    return {"tasks": rows, "count": len(rows)}


@router.get("/tasks/export")
def export_tasks():
    from fastapi.responses import Response
    from .. import exportutil
    rows = [{"title": t["title"], "status": t["status"], "due": t["due_at"], "lead_id": t["lead_id"],
             "notes": t["notes"], "created_at": t["created_at"], "completed_at": t["completed_at"]}
            for t in list_tasks("ALL", None, False, 500)["tasks"]]
    return Response(exportutil.to_csv(rows) if rows else "no tasks\n", media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="tasks.csv"'})


@router.post("/tasks")
def create_task(p: TaskPayload, request: Request):
    if p.lead_id is not None:
        _own_lead(p.lead_id, "id")
    user = getattr(request.state, "user", None) or {}
    task_id = execute("INSERT INTO tasks(workspace_id, lead_id, owner_user_id, title, notes, due_at, "
                      "status, created_at) VALUES(?,?,?,?,?,?, 'OPEN', ?)",
                      (current_ws(), p.lead_id, user.get("id"), p.title.strip(), p.notes, p.due_at, utcnow()))
    return _own_task(task_id)


@router.patch("/tasks/{task_id}")
def update_task(task_id: int, p: TaskPatch):
    row = _own_task(task_id)
    new = {**row, **{k: v for k, v in p.model_dump().items() if v is not None}}
    done_at = row["completed_at"]
    if p.status == "DONE" and row["status"] != "DONE":
        done_at = utcnow()
    elif p.status == "OPEN":
        done_at = None
    execute("UPDATE tasks SET title = ?, notes = ?, due_at = ?, status = ?, completed_at = ? "
            "WHERE id = ? AND workspace_id = ?",
            (new["title"], new["notes"], new["due_at"], new["status"], done_at, task_id, current_ws()))
    return _own_task(task_id)


@router.delete("/tasks/{task_id}")
def delete_task(task_id: int):
    _own_task(task_id)
    execute("DELETE FROM tasks WHERE id = ? AND workspace_id = ?", (task_id, current_ws()))
    return {"ok": True}


# --------------------------------------------------------------- webhooks ---
class HookPayload(BaseModel):
    url: str = Field(max_length=500)
    events: list[str] = Field(default_factory=lambda: list(webhooks.EVENTS))


def _hook_out(row: dict, with_secret: bool = False) -> dict:
    out = {k: row[k] for k in ("id", "url", "events", "is_active", "created_at")}
    out["events"] = (row["events"] or "").split(",")
    if with_secret:
        out["secret"] = row["secret"]
    return out


@router.get("/webhooks")
def list_hooks(_: dict = Depends(require_role("superadmin", "admin"))):
    return {"webhooks": [_hook_out(r) for r in
                         query("SELECT * FROM webhooks WHERE workspace_id = ? ORDER BY id", (current_ws(),))],
            "events": list(webhooks.EVENTS)}


@router.post("/webhooks")
def create_hook(p: HookPayload, _: dict = Depends(require_role("superadmin", "admin"))):
    try:
        netsafe.validate_url(p.url)
    except netsafe.UnsafeURL as exc:
        raise HTTPException(400, f"URL rejected: {exc}") from exc
    bad = [e for e in p.events if e not in webhooks.EVENTS]
    if bad or not p.events:
        raise HTTPException(400, f"events must be from: {', '.join(webhooks.EVENTS)}")
    hid = execute("INSERT INTO webhooks(workspace_id, url, secret, events, is_active, created_at) "
                  "VALUES(?,?,?,?,1,?)", (current_ws(), p.url, webhooks.new_secret(), ",".join(p.events), utcnow()))
    # The secret is shown once, here, so the receiver can verify signatures.
    return _hook_out(query_one("SELECT * FROM webhooks WHERE id = ?", (hid,)), with_secret=True)


@router.delete("/webhooks/{hook_id}")
def delete_hook(hook_id: int, _: dict = Depends(require_role("superadmin", "admin"))):
    if not query_one("SELECT 1 AS x FROM webhooks WHERE id = ? AND workspace_id = ?", (hook_id, current_ws())):
        raise HTTPException(404, "Webhook not found")
    execute("DELETE FROM webhooks WHERE id = ? AND workspace_id = ?", (hook_id, current_ws()))
    return {"ok": True}


@router.post("/webhooks/{hook_id}/test")
def test_hook(hook_id: int, _: dict = Depends(require_role("superadmin", "admin"))):
    hook = query_one("SELECT * FROM webhooks WHERE id = ? AND workspace_id = ?", (hook_id, current_ws()))
    if not hook:
        raise HTTPException(404, "Webhook not found")
    import json
    body = json.dumps({"event": "ping", "at": utcnow(), "data": {"hello": "world"}}).encode()
    return webhooks._deliver(current_ws(), hook, "ping", body)


@router.get("/webhooks/deliveries")
def deliveries(limit: int = Query(50, le=200), _: dict = Depends(require_role("superadmin", "admin"))):
    return {"deliveries": query("SELECT * FROM webhook_deliveries WHERE workspace_id = ? "
                                "ORDER BY id DESC LIMIT ?", (current_ws(), limit))}


# -------------------------------------------------------------- audit log ---
@router.get("/audit-log")
def audit_log(limit: int = Query(100, le=500), _: dict = Depends(require_role("superadmin", "admin"))):
    return {"entries": auditlog.recent(limit, current_ws())}


# -------------------------------------------------------------------- MFA ---
class CodePayload(BaseModel):
    code: str


@router.get("/auth/mfa")
def mfa_status(request: Request):
    return {"enabled": mfa.state(request.state.user["id"])["enabled"]}


@router.post("/auth/mfa/setup")
def mfa_setup(request: Request):
    user = request.state.user
    secret = mfa.begin_setup(user["id"])
    return {"secret": secret, "otpauth_uri": mfa.provisioning_uri(secret, user["username"],
                                                                  get_branding()["brand_name"]),
            "next": "Add it to an authenticator app, then POST /api/auth/mfa/enable with a code."}


@router.post("/auth/mfa/enable")
def mfa_enable(p: CodePayload, request: Request):
    if not mfa.enable(request.state.user["id"], p.code):
        raise HTTPException(400, "That code is not valid. Check your authenticator app's clock.")
    return {"enabled": True}


@router.post("/auth/mfa/disable")
def mfa_disable(p: CodePayload, request: Request):
    st = mfa.state(request.state.user["id"])
    if not st["enabled"] or not mfa.verify_code(st["secret"], p.code):
        raise HTTPException(400, "A valid code is required to turn MFA off.")
    mfa.disable(request.state.user["id"])
    return {"enabled": False}


@router.post("/users/{user_id}/mfa-reset")
def mfa_reset(user_id: int, _: dict = Depends(require_role("superadmin"))):
    """Lock-out recovery: the superadmin clears someone's MFA so they can sign in and re-enrol."""
    mfa.disable(user_id)
    return {"ok": True}
