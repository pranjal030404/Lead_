"""Website audit and opportunity endpoints (all workspace-scoped)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .. import audit, leadscore, opportunities
from ..config import settings
from ..db import current_ws, query, query_one
from ..security import rate_limit, require_role
from .leads import _own_lead

router = APIRouter(prefix="/api", tags=["audits"])


@router.post("/leads/{lead_id}/audit")
def run_audit(lead_id: int, psi: bool = True):
    """Audit the lead's website now (synchronous; usually 5-20 s). Rate limited
    per workspace because it makes outbound requests."""
    lead = _own_lead(lead_id, "id, website_url")
    if not (lead.get("website_url") or "").strip():
        raise HTTPException(400, "The lead has no website to audit")
    if not rate_limit(f"audit:{current_ws()}", settings.audit_rate_limit,
                      settings.audit_rate_window):
        raise HTTPException(429, "Too many audits - try again shortly")
    result = audit.run_audit(lead_id, use_psi=psi)
    leadscore.safe_recompute(lead_id)
    return result


@router.get("/leads/{lead_id}/audit")
def latest_audit(lead_id: int):
    _own_lead(lead_id, "id")
    found = audit.latest_audit(lead_id)
    if not found:
        raise HTTPException(404, "No audit yet for this lead")
    return found


@router.get("/leads/{lead_id}/audits")
def audit_history(lead_id: int, limit: int = Query(20, ge=1, le=100)):
    _own_lead(lead_id, "id")
    return audit.audit_history(lead_id, limit)


@router.get("/audits/{audit_id}")
def get_audit(audit_id: int):
    found = audit.get_audit(audit_id)
    if not found:
        raise HTTPException(404, "Audit not found")
    return found


@router.get("/leads/{lead_id}/opportunities")
def lead_opportunities(lead_id: int, open_only: bool = False):
    _own_lead(lead_id, "id")
    return opportunities.list_for_lead(lead_id, include_closed=not open_only)


@router.post("/leads/{lead_id}/opportunities/detect")
def redetect(lead_id: int):
    """Re-run detection from the latest audit and the lead's current data."""
    _own_lead(lead_id, "id")
    result = opportunities.refresh_for_lead(lead_id)
    leadscore.safe_recompute(lead_id)
    return result


class StatusPayload(BaseModel):
    status: str


@router.patch("/opportunities/{opportunity_id}")
def update_opportunity(opportunity_id: int, payload: StatusPayload):
    try:
        row = opportunities.set_status(opportunity_id, payload.status.upper())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not row:
        raise HTTPException(404, "Opportunity not found")
    leadscore.safe_recompute(row["lead_id"])
    return row


@router.get("/opportunities")
def list_opportunities(
    type: str | None = None, severity: str | None = None, status: str = "OPEN",
    min_value: int = Query(0, ge=0), q: str | None = None,
    limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
):
    """The workspace's opportunity pipeline, biggest first."""
    clauses, params = ["o.workspace_id = ?"], [current_ws()]
    for col, val in (("o.opportunity_type", type and type.upper()),
                     ("o.severity", severity and severity.upper()),
                     ("o.status", status and status.upper())):
        if val:
            clauses.append(f"{col} = ?")
            params.append(val)
    if min_value:
        clauses.append("o.est_value_max >= ?")
        params.append(min_value)
    if q:
        clauses.append("l.business_name LIKE ?")
        params.append(f"%{q}%")
    where = " AND ".join(clauses)
    total = query_one(f"SELECT COUNT(*) AS n FROM lead_opportunities o JOIN leads l "
                      f"ON l.id = o.lead_id WHERE {where}", tuple(params))["n"]
    rows = query(
        f"SELECT o.*, l.business_name, l.city, l.niche, l.lead_score FROM lead_opportunities o "
        f"JOIN leads l ON l.id = o.lead_id WHERE {where} "
        f"ORDER BY FIELD(o.severity,'HIGH','MEDIUM','LOW'), o.est_value_max DESC, o.id "
        f"LIMIT ? OFFSET ?", (*params, limit, offset))
    return {"total": total, "limit": limit, "offset": offset,
            "opportunities": [opportunities._decode(r) for r in rows]}


@router.get("/opportunities/summary")
def opportunity_summary():
    rows = query(
        "SELECT opportunity_type AS type, severity, COUNT(*) AS n, "
        "SUM(est_value_min) AS value_min, SUM(est_value_max) AS value_max "
        "FROM lead_opportunities WHERE workspace_id = ? AND status = 'OPEN' "
        "GROUP BY opportunity_type, severity ORDER BY value_max DESC", (current_ws(),))
    total = query_one(
        "SELECT COUNT(*) AS n, COALESCE(SUM(est_value_min),0) AS lo, "
        "COALESCE(SUM(est_value_max),0) AS hi FROM lead_opportunities "
        "WHERE workspace_id = ? AND status = 'OPEN'", (current_ws(),))
    return {"by_type": rows, "open": total["n"],
            "estimated_value": {"min": int(total["lo"]), "max": int(total["hi"]),
                                "currency": "INR"},
            "note": "Estimates come from the workspace price table, not market data."}


@router.get("/opportunities/pricing")
def get_pricing():
    return {"pricing": opportunities.get_pricing(), "currency": "INR"}


@router.put("/opportunities/pricing")
def put_pricing(table: dict, _: dict = Depends(require_role("superadmin", "admin"))):
    try:
        return {"pricing": opportunities.set_pricing(table)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
