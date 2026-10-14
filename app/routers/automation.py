from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..automation import (JOB_SWITCHES, SYSTEM_JOBS, build_digest, run_job_now,
                          scheduled_jobs, set_switch, switches)
from ..backup import list_backups, run_backup
from ..config import settings
from ..db import current_ws, execute, lead_scope, query, scalar, search_scope
from ..security import require_role
from ..http import breaker_state
from ..quota import usage_summary

router = APIRouter(prefix="/api/automation", tags=["automation"])


class SwitchPayload(BaseModel):
    name: str
    enabled: bool


@router.get("/status")
def status():
    ws = current_ws()
    lscope, lp = lead_scope()
    sscope, sp = search_scope()
    return {
        "switches": switches(),
        "jobs": scheduled_jobs(),
        "quota": usage_summary(),
        "dry_run": settings.dry_run,
        "provider": settings.provider,
        "today": {
            "searches": scalar(
                f"SELECT COUNT(*) FROM searches WHERE {sscope} "
                f"AND LEFT(started_at, 10) = CURDATE()", tuple(sp)
            ),
            "leads_found": scalar(
                f"SELECT COUNT(*) FROM leads WHERE {lscope} "
                f"AND LEFT(created_at, 10) = CURDATE()", tuple(lp)
            ),
            "enriched": scalar(
                f"SELECT COUNT(*) FROM leads WHERE {lscope} "
                f"AND enrichment_status = 'complete' AND LEFT(updated_at, 10) = CURDATE()",
                tuple(lp)
            ),
            "awaiting_approval": scalar(
                "SELECT COUNT(*) FROM approval_queue WHERE workspace_id = ? "
                "AND status = 'PENDING'", (ws,)
            ),
            "sent": scalar(
                "SELECT COUNT(*) FROM approval_queue WHERE workspace_id = ? "
                "AND status = 'SENT' AND LEFT(sent_at, 10) = CURDATE()", (ws,)
            ),
        },
        "circuit_breakers": breaker_state(),
        "unresolved_failures": scalar(
            "SELECT COUNT(*) FROM failed_jobs WHERE resolved = 0 AND workspace_id = ?", (ws,)),
    }


@router.post("/switch")
def flip(payload: SwitchPayload, _: dict = Depends(require_role("superadmin"))):
    """Install-wide switch - superadmin only (it affects every workspace)."""
    valid = {"automation_master", *JOB_SWITCHES.values()}
    if payload.name not in valid:
        raise HTTPException(400, f"Unknown switch. Valid: {sorted(valid)}")
    set_switch(payload.name, payload.enabled)
    return switches()


@router.post("/kill")
def kill_switch(_: dict = Depends(require_role("superadmin"))):
    """Master off. Always available, even if individual jobs are misbehaving."""
    set_switch("automation_master", False)
    return {"automation_master": False}


@router.get("/log")
def log(limit: int = Query(50, le=500), level: str | None = None):
    if level:
        return query(
            "SELECT * FROM automation_log WHERE workspace_id = ? AND level = ? "
            "ORDER BY id DESC LIMIT ?", (current_ws(), level, limit),
        )
    return query("SELECT * FROM automation_log WHERE workspace_id = ? ORDER BY id DESC LIMIT ?",
                 (current_ws(), limit))


@router.post("/run/{job_name}")
def run_now(job_name: str, request: Request):
    user = request.state.user
    if job_name in SYSTEM_JOBS and user["role"] != "superadmin":
        raise HTTPException(403, "Requires role: superadmin")
    try:
        return {"job": job_name, "result": run_job_now(job_name, workspace_id=current_ws())}
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/failures")
def failures(resolved: bool = False):
    return query(
        "SELECT * FROM failed_jobs WHERE workspace_id = ? AND resolved = ? "
        "ORDER BY id DESC LIMIT 200",
        (current_ws(), 1 if resolved else 0),
    )


@router.post("/failures/{failure_id}/resolve")
def resolve_failure(failure_id: int):
    execute("UPDATE failed_jobs SET resolved = 1 WHERE id = ? AND workspace_id = ?",
            (failure_id, current_ws()))
    return {"resolved": failure_id}


@router.post("/failures/resolve-all")
def resolve_all():
    count = scalar("SELECT COUNT(*) FROM failed_jobs WHERE resolved = 0 AND workspace_id = ?",
                   (current_ws(),))
    execute("UPDATE failed_jobs SET resolved = 1 WHERE resolved = 0 AND workspace_id = ?",
            (current_ws(),))
    return {"resolved": count}


@router.get("/digest")
def digest(_: dict = Depends(require_role("superadmin"))):
    return {"digest": build_digest()}


@router.get("/backups")
def backups(_: dict = Depends(require_role("superadmin"))):
    return {"backups": list_backups(), "keep_days": settings.backup_keep_days}


@router.post("/backups")
def create_backup(_: dict = Depends(require_role("superadmin"))):
    return run_backup()
