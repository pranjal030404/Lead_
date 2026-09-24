from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..automation import (JOB_SWITCHES, build_digest, run_job_now, scheduled_jobs,
                          set_switch, switches)
from ..backup import list_backups, run_backup
from ..config import settings
from ..db import execute, query, scalar
from ..http import breaker_state
from ..quota import usage_summary

router = APIRouter(prefix="/api/automation", tags=["automation"])


class SwitchPayload(BaseModel):
    name: str
    enabled: bool


@router.get("/status")
def status():
    return {
        "switches": switches(),
        "jobs": scheduled_jobs(),
        "quota": usage_summary(),
        "dry_run": settings.dry_run,
        "provider": settings.provider,
        "today": {
            "searches": scalar(
                "SELECT COUNT(*) FROM searches WHERE LEFT(started_at, 10) = CURDATE()"
            ),
            "leads_found": scalar(
                "SELECT COUNT(*) FROM leads WHERE LEFT(created_at, 10) = CURDATE()"
            ),
            "enriched": scalar(
                "SELECT COUNT(*) FROM leads WHERE enrichment_status = 'complete' "
                "AND LEFT(updated_at, 10) = CURDATE()"
            ),
            "awaiting_approval": scalar(
                "SELECT COUNT(*) FROM approval_queue WHERE status = 'PENDING'"
            ),
            "sent": scalar(
                "SELECT COUNT(*) FROM approval_queue WHERE status = 'SENT' "
                "AND LEFT(sent_at, 10) = CURDATE()"
            ),
        },
        "circuit_breakers": breaker_state(),
        "unresolved_failures": scalar("SELECT COUNT(*) FROM failed_jobs WHERE resolved = 0"),
    }


@router.post("/switch")
def flip(payload: SwitchPayload):
    valid = {"automation_master", *JOB_SWITCHES.values()}
    if payload.name not in valid:
        raise HTTPException(400, f"Unknown switch. Valid: {sorted(valid)}")
    set_switch(payload.name, payload.enabled)
    return switches()


@router.post("/kill")
def kill_switch():
    """Master off. Always available, even if individual jobs are misbehaving."""
    set_switch("automation_master", False)
    return {"automation_master": False}


@router.get("/log")
def log(limit: int = Query(50, le=500), level: str | None = None):
    if level:
        return query(
            "SELECT * FROM automation_log WHERE level = ? ORDER BY id DESC LIMIT ?",
            (level, limit),
        )
    return query("SELECT * FROM automation_log ORDER BY id DESC LIMIT ?", (limit,))


@router.post("/run/{job_name}")
def run_now(job_name: str):
    try:
        return {"job": job_name, "result": run_job_now(job_name)}
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/failures")
def failures(resolved: bool = False):
    return query(
        "SELECT * FROM failed_jobs WHERE resolved = ? ORDER BY id DESC LIMIT 200",
        (1 if resolved else 0,),
    )


@router.post("/failures/{failure_id}/resolve")
def resolve_failure(failure_id: int):
    execute("UPDATE failed_jobs SET resolved = 1 WHERE id = ?", (failure_id,))
    return {"resolved": failure_id}


@router.post("/failures/resolve-all")
def resolve_all():
    count = scalar("SELECT COUNT(*) FROM failed_jobs WHERE resolved = 0")
    execute("UPDATE failed_jobs SET resolved = 1 WHERE resolved = 0")
    return {"resolved": count}


@router.get("/digest")
def digest():
    return {"digest": build_digest()}


@router.get("/backups")
def backups():
    return {"backups": list_backups(), "keep_days": settings.backup_keep_days}


@router.post("/backups")
def create_backup():
    return run_backup()
