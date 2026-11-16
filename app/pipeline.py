"""Configurable pipeline: per-workspace stages, and the one place a lead's stage changes.

Eight core stages are seeded for every workspace (NEW .. LOST). Core stages can be recoloured,
relabelled and retimed but not deleted or renamed, because automations key off them (replies,
campaigns, proposals, meetings). Workspaces can add custom stages and arrange the board freely.

`set_lead_status` is the single writer for stage changes made by meetings, proposals and the
board: it records history, and stops campaign sequences once a lead has moved beyond outreach.
"""

from __future__ import annotations

import re

from .db import current_ws, execute, query, query_one, utcnow
from .db import PIPELINE_STAGES

CORE = {name for name, *_ in PIPELINE_STAGES}
TERMINAL = ("WON", "LOST")


def seed_stages(workspace_id: int) -> None:
    for name, pos, color, days in PIPELINE_STAGES:
        execute("INSERT IGNORE INTO pipeline_stages(workspace_id, name, position, color, "
                "auto_follow_up_days, is_core) VALUES(?,?,?,?,?,1)", (workspace_id, name, pos, color, days))


def stages() -> list[dict]:
    return query("SELECT id, name, COALESCE(label, REPLACE(name, '_', ' ')) AS label, position, color, "
                 "auto_follow_up_days, is_core FROM pipeline_stages WHERE workspace_id = ? "
                 "ORDER BY position, id", (current_ws(),))


def stage_names() -> set[str]:
    return {s["name"] for s in stages()}


def slug(label: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", (label or "").upper()).strip("_")[:32]


def set_lead_status(lead_id: int, new_status: str, note: str | None = None, *, forward_only: bool = False) -> str | None:
    """Move a lead (current workspace) to `new_status`. Returns the status applied, or None if
    unchanged. `forward_only` applies the same rule replies use (never moves a lead backwards)."""
    from . import campaigns, replies

    lead = query_one("SELECT status FROM leads WHERE id = ? AND workspace_id = ?", (lead_id, current_ws()))
    if not lead or lead["status"] == new_status:
        return None
    target = replies.next_status(lead["status"], new_status) if forward_only else new_status
    if not target or target == lead["status"]:
        return None
    now = utcnow()
    execute("UPDATE leads SET status = ?, updated_at = ? WHERE id = ? AND workspace_id = ?",
            (target, now, lead_id, current_ws()))
    execute("INSERT INTO interactions(workspace_id, lead_id, type, direction, date, subject, content, "
            "outcome, automated, created_at) VALUES(?,?,'note','out',?,?,?,?,0,?)",
            (current_ws(), lead_id, now, f"Stage: {lead['status']} -> {target}", note or "", f"STAGE:{target}", now))
    if target not in ("NEW", "CONTACTED"):
        campaigns.stop_for_lead(lead_id, f"lead moved to {target}")
    return target
