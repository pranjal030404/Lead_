"""Lead scores, buying-intent signals and competitor comparison (workspace-scoped)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .. import audit, competitors, leadscore, signals
from ..config import settings
from ..db import current_ws, query, query_one
from ..security import rate_limit, require_role
from .leads import _own_lead

router = APIRouter(prefix="/api", tags=["intelligence"])


@router.get("/leads/{lead_id}/scores")
def lead_scores(lead_id: int):
    _own_lead(lead_id, "id")
    row = leadscore.get_scores(lead_id)
    if not row:
        raise HTTPException(404, "This lead has not been scored yet - POST .../scores/recompute")
    return row


@router.post("/leads/{lead_id}/scores/recompute")
def recompute(lead_id: int):
    _own_lead(lead_id, "id")
    return leadscore.recompute(lead_id)


@router.get("/leads/{lead_id}/score-history")
def score_history(lead_id: int, limit: int = Query(50, ge=1, le=500)):
    _own_lead(lead_id, "id")
    return leadscore.history(lead_id, limit)


@router.get("/leads/{lead_id}/signals")
def lead_signals(lead_id: int):
    _own_lead(lead_id, "id")
    return {"signals": signals.list_for_lead(lead_id),
            "unavailable_signals": signals.UNAVAILABLE_SIGNALS,
            "note": "Unavailable signals need external data feeds this installation does "
                    "not have; they are never estimated."}


@router.post("/leads/{lead_id}/signals/refresh")
def refresh_signals(lead_id: int):
    _own_lead(lead_id, "id")
    result = signals.refresh_for_lead(lead_id)
    leadscore.safe_recompute(lead_id)
    return result


# -------------------------------------------------------------- competitors --

@router.get("/leads/{lead_id}/competitors")
def lead_competitors(lead_id: int):
    _own_lead(lead_id, "id")
    return competitors.competitive_gap(lead_id)


class PinPayload(BaseModel):
    competitor_lead_id: int


@router.post("/leads/{lead_id}/competitors")
def pin_competitor(lead_id: int, payload: PinPayload):
    _own_lead(lead_id, "id")
    if not competitors.pin(lead_id, payload.competitor_lead_id):
        raise HTTPException(404, "Competitor lead not found in this workspace")
    return competitors.competitive_gap(lead_id)


@router.delete("/leads/{lead_id}/competitors/{competitor_id}")
def unpin_competitor(lead_id: int, competitor_id: int):
    _own_lead(lead_id, "id")
    if not competitors.unpin(lead_id, competitor_id):
        raise HTTPException(404, "That competitor is not pinned")
    return competitors.competitive_gap(lead_id)


@router.post("/leads/{lead_id}/competitors/audit")
def audit_competitors(lead_id: int, max_audits: int = Query(3, ge=1, le=5)):
    """Audit nearby competitors that have a website but no audit yet, so the
    comparison has data. Each audit makes outbound requests, so each one counts
    against the workspace's audit rate limit."""
    _own_lead(lead_id, "id")
    gap = competitors.competitive_gap(lead_id)
    done = []
    for cid in gap["unaudited_competitor_ids"][:max_audits]:
        if not rate_limit(f"audit:{current_ws()}", settings.audit_rate_limit,
                          settings.audit_rate_window):
            raise HTTPException(429, f"Audit rate limit reached after {len(done)} audit(s)")
        try:
            audit.run_audit(cid, use_psi=False)
            leadscore.safe_recompute(cid)
            done.append(cid)
        except (ValueError, LookupError):
            continue
    result = competitors.competitive_gap(lead_id)
    leadscore.safe_recompute(lead_id)
    return {"audited": done, "comparison": result}


# ----------------------------------------------------------------- weights --

@router.get("/scoring/weights")
def get_weights():
    return {"weights": leadscore.get_weights(), "defaults": leadscore.DEFAULT_WEIGHTS,
            "tiers": {"HOT": leadscore.TIER_HOT, "WARM": leadscore.TIER_WARM}}


@router.put("/scoring/weights")
def put_weights(table: dict, _: dict = Depends(require_role("superadmin", "admin"))):
    try:
        return {"weights": leadscore.set_weights(table)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/scoring/summary")
def scoring_summary():
    ws = current_ws()
    tiers = query("SELECT COALESCE(opportunity_tier, 'UNSCORED') AS tier, COUNT(*) AS n, "
                  "ROUND(AVG(overall_score)) AS avg_overall FROM leads WHERE workspace_id = ? "
                  "GROUP BY opportunity_tier", (ws,))
    top = query("SELECT id, business_name, city, niche, overall_score, opportunity_tier, "
                "intent_score, score_confidence FROM leads WHERE workspace_id = ? AND "
                "overall_score IS NOT NULL ORDER BY overall_score DESC, id LIMIT 10", (ws,))
    active = query_one("SELECT COUNT(DISTINCT lead_id) AS n FROM lead_signals WHERE "
                       "workspace_id = ? AND status = 'ACTIVE'", (ws,))["n"]
    return {"tiers": tiers, "top_leads": top, "leads_with_active_signals": active}
