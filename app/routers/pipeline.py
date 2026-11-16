"""Pipeline configuration (admins) and moving a lead between stages (everyone)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import pipeline
from ..db import current_ws, execute, query, query_one, scalar
from ..security import require_role
from .leads import _own_lead

router = APIRouter(prefix="/api", tags=["pipeline"])
admin = require_role("superadmin", "admin")


class StageIn(BaseModel):
    label: str = Field(min_length=1, max_length=40)
    color: str = Field("#64748b", pattern="^#[0-9a-fA-F]{6}$")
    auto_follow_up_days: int = Field(0, ge=0, le=90)


class StagePatch(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=40)
    color: str | None = Field(None, pattern="^#[0-9a-fA-F]{6}$")
    auto_follow_up_days: int | None = Field(None, ge=0, le=90)


class Reorder(BaseModel):
    ids: list[int]


class Move(BaseModel):
    stage: str
    note: str = Field("", max_length=500)


def _stage(stage_id: int) -> dict:
    row = query_one("SELECT * FROM pipeline_stages WHERE id = ? AND workspace_id = ?", (stage_id, current_ws()))
    if not row:
        raise HTTPException(404, "Stage not found")
    return row


@router.get("/pipeline/stages")
def list_stages(_: dict = Depends(admin)):
    counts = {r["status"]: r["n"] for r in query(
        "SELECT status, COUNT(*) AS n FROM leads WHERE workspace_id = ? GROUP BY status", (current_ws(),))}
    return {"stages": [{**s, "count": counts.get(s["name"], 0)} for s in pipeline.stages()]}


@router.post("/pipeline/stages")
def add_stage(p: StageIn, _: dict = Depends(admin)):
    name = pipeline.slug(p.label)
    if not name:
        raise HTTPException(400, "Give the stage a name with letters or numbers")
    if query_one("SELECT 1 AS x FROM pipeline_stages WHERE workspace_id = ? AND name = ?", (current_ws(), name)):
        raise HTTPException(409, "A stage with that name already exists")
    # Insert just before WON so "won" and "lost" stay at the end of the board.
    won = query_one("SELECT position FROM pipeline_stages WHERE workspace_id = ? AND name = 'WON'", (current_ws(),))
    pos = won["position"] if won else (scalar("SELECT MAX(position) FROM pipeline_stages WHERE workspace_id = ?",
                                              (current_ws(),), 0) or 0) + 1
    execute("UPDATE pipeline_stages SET position = position + 1 WHERE workspace_id = ? AND position >= ?",
            (current_ws(), pos))
    sid = execute("INSERT INTO pipeline_stages(workspace_id, name, label, position, color, auto_follow_up_days, "
                  "is_core) VALUES(?,?,?,?,?,?,0)",
                  (current_ws(), name, p.label.strip(), pos, p.color, p.auto_follow_up_days))
    return _stage(sid)


@router.patch("/pipeline/stages/{stage_id}")
def edit_stage(stage_id: int, p: StagePatch, _: dict = Depends(admin)):
    row = _stage(stage_id)
    execute("UPDATE pipeline_stages SET label = ?, color = ?, auto_follow_up_days = ? "
            "WHERE id = ? AND workspace_id = ?",
            (p.label.strip() if p.label else row["label"], p.color or row["color"],
             row["auto_follow_up_days"] if p.auto_follow_up_days is None else p.auto_follow_up_days,
             stage_id, current_ws()))
    return _stage(stage_id)


@router.post("/pipeline/stages/reorder")
def reorder(p: Reorder, _: dict = Depends(admin)):
    existing = {s["id"] for s in pipeline.stages()}
    if set(p.ids) != existing or len(p.ids) != len(existing):
        raise HTTPException(400, "Send every stage id exactly once")
    for pos, sid in enumerate(p.ids, 1):
        execute("UPDATE pipeline_stages SET position = ? WHERE id = ? AND workspace_id = ?", (pos, sid, current_ws()))
    return {"stages": pipeline.stages()}


@router.delete("/pipeline/stages/{stage_id}")
def delete_stage(stage_id: int, move_to: str | None = None, _: dict = Depends(admin)):
    row = _stage(stage_id)
    if row["is_core"]:
        raise HTTPException(400, "Core stages can't be deleted - other features depend on them")
    n = scalar("SELECT COUNT(*) FROM leads WHERE workspace_id = ? AND status = ?", (current_ws(), row["name"]), 0)
    if n:
        if not move_to or move_to == row["name"] or move_to not in pipeline.stage_names():
            raise HTTPException(409, f"{n} lead(s) are in this stage - pass move_to=<another stage> to relocate them")
        execute("UPDATE leads SET status = ? WHERE workspace_id = ? AND status = ?",
                (move_to, current_ws(), row["name"]))
    execute("DELETE FROM pipeline_stages WHERE id = ? AND workspace_id = ?", (stage_id, current_ws()))
    return {"ok": True, "moved": n}


@router.post("/leads/{lead_id}/stage")
def move_lead(lead_id: int, p: Move):
    _own_lead(lead_id, "id")
    stage = p.stage.strip().upper()
    if stage not in pipeline.stage_names():
        raise HTTPException(400, "Unknown stage for this workspace")
    applied = pipeline.set_lead_status(lead_id, stage, p.note)
    return {"lead_id": lead_id, "status": stage, "changed": applied is not None}
