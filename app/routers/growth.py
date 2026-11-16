"""Admin-only growth console: inbound form, free email finder, MQL/SQL split and
source report. Everything here is read/configured by admins only; the one public
surface (the /c/<slug> form) is switched on or off from this page."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..config import settings
from ..db import current_ws, get_setting, query, query_one, scalar, set_setting
from ..security import require_role

router = APIRouter(prefix="/api/growth", tags=["growth"],
                   dependencies=[Depends(require_role("admin", "superadmin"))])

DEFAULTS = {"growth_capture_enabled": "on", "growth_finder_enabled": "on",
            "growth_sql_threshold": "60", "growth_mql_threshold": "30"}


def setting(key: str) -> str:
    return get_setting(key, DEFAULTS[key]) or DEFAULTS[key]


def enabled(key: str) -> bool:
    return setting(key) == "on"


def thresholds() -> tuple[int, int]:
    def num(key: str) -> int:
        try:
            return int(setting(key))
        except ValueError:
            return int(DEFAULTS[key])
    return num("growth_mql_threshold"), num("growth_sql_threshold")


class GrowthSettings(BaseModel):
    capture_enabled: bool | None = None
    finder_enabled: bool | None = None
    mql_threshold: int | None = Field(None, ge=0, le=100)
    sql_threshold: int | None = Field(None, ge=0, le=100)


def _qualification(ws: int) -> dict:
    mql, sql = thresholds()
    rows = query(
        """SELECT CASE WHEN source = 'Inbound form' OR COALESCE(overall_score, lead_score * 10) >= ? THEN 'SQL'
                       WHEN COALESCE(overall_score, lead_score * 10) >= ? THEN 'MQL' ELSE 'UNQUALIFIED' END AS stage,
                  COUNT(*) AS n
           FROM leads WHERE workspace_id = ? AND status NOT IN ('WON','LOST') GROUP BY stage""",
        (sql, mql, ws))
    out = {"SQL": 0, "MQL": 0, "UNQUALIFIED": 0}
    out.update({r["stage"]: r["n"] for r in rows})
    return out


def _sources(ws: int) -> list[dict]:
    rows = query(
        """SELECT COALESCE(NULLIF(LEFT(source, 40), ''), 'Unknown') AS source,
                  COUNT(*) AS leads,
                  SUM(status <> 'NEW') AS contacted,
                  SUM(status = 'WON') AS won,
                  SUM(status = 'LOST') AS lost
           FROM leads WHERE workspace_id = ? GROUP BY 1 ORDER BY leads DESC LIMIT 30""", (ws,))
    for r in rows:
        r["leads"], r["contacted"] = int(r["leads"]), int(r["contacted"] or 0)
        r["won"], r["lost"] = int(r["won"] or 0), int(r["lost"] or 0)
        r["win_rate"] = round(100 * r["won"] / r["leads"], 1) if r["leads"] else 0.0
    return rows


@router.get("")
def overview():
    ws = current_ws()
    slug = (query_one("SELECT slug FROM workspaces WHERE id = ?", (ws,)) or {}).get("slug", "")
    base = (settings.public_url or "").rstrip("/")
    mql, sql = thresholds()
    return {
        "settings": {"capture_enabled": enabled("growth_capture_enabled"),
                     "finder_enabled": enabled("growth_finder_enabled"),
                     "mql_threshold": mql, "sql_threshold": sql},
        "capture": {"url": f"{base}/c/{slug}" if base else f"/c/{slug}", "slug": slug,
                    "public_url_set": bool(base),
                    "inbound_leads": scalar(
                        "SELECT COUNT(*) FROM leads WHERE workspace_id = ? AND source = 'Inbound form'", (ws,)),
                    "last_7_days": scalar(
                        "SELECT COUNT(*) FROM leads WHERE workspace_id = ? AND source = 'Inbound form' "
                        "AND created_at >= ?", (ws, _days_ago(7)))},
        "finder": {"guessed_addresses": scalar(
                       "SELECT COUNT(*) FROM email_addresses WHERE workspace_id = ? AND status = 'INFERRED'", (ws,)),
                   "leads_with_email": scalar(
                       "SELECT COUNT(*) FROM leads WHERE workspace_id = ? AND email IS NOT NULL AND email <> ''", (ws,)),
                   "total_leads": scalar("SELECT COUNT(*) FROM leads WHERE workspace_id = ?", (ws,))},
        "qualification": _qualification(ws),
        "sources": _sources(ws),
    }


def _days_ago(days: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


@router.put("/settings")
def update_settings(p: GrowthSettings):
    mql, sql = thresholds()
    new_mql = mql if p.mql_threshold is None else p.mql_threshold
    new_sql = sql if p.sql_threshold is None else p.sql_threshold
    if new_mql > new_sql:
        raise HTTPException(400, "The MQL threshold cannot be above the SQL threshold")
    if p.capture_enabled is not None:
        set_setting("growth_capture_enabled", "on" if p.capture_enabled else "off")
    if p.finder_enabled is not None:
        set_setting("growth_finder_enabled", "on" if p.finder_enabled else "off")
    set_setting("growth_mql_threshold", str(new_mql))
    set_setting("growth_sql_threshold", str(new_sql))
    return overview()
