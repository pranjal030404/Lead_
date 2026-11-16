"""Ideal-client profile, fit scoring, "Not a fit" feedback, and API tokens."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import aidraft, apitokens, feedback, icp, snapshots
from ..discovery import reviewmine
from ..db import (current_ws, execute, get_ws_setting, lead_scope, query, query_one, set_ws_setting,
                  utcnow)
from ..discovery import free_signals
from .leads import _own_lead

router = APIRouter(prefix="/api", tags=["fit"])


# ----------------------------------------------------------------- ICP --
def load_icp() -> dict | None:
    raw = get_ws_setting("icp")
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


class InferPayload(BaseModel):
    url: str = Field(min_length=3, max_length=300)


@router.post("/icp/infer")
def infer(p: InferPayload):
    """Read the operator's own website and propose a profile (not saved until PUT)."""
    url = p.url.strip()
    full = url if "//" in url else "https://" + url
    html = free_signals.fetch_page(full, timeout=10)
    if not html:
        raise HTTPException(502, "Could not load that website (blocked, down, or not public)")
    return icp.infer_icp(html, full)


@router.get("/icp")
def get_icp():
    return {"icp": load_icp()}


@router.put("/icp")
def save_icp(payload: dict):
    clean = icp.normalise_icp(payload)
    if not (clean["offerings"] or clean["target_niches"]):
        raise HTTPException(400, "Add at least one offering or target niche")
    set_ws_setting("icp", json.dumps(clean))
    return {"icp": clean}


# ---------------------------------------------------------- fit + feedback --
def _adjustments() -> dict:
    raw = get_ws_setting("fit_adjustments")
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def fit_for(lead: dict) -> dict:
    base = icp.fit_score(lead, load_icp())
    adj = feedback.adjust(base["score"], lead, _adjustments())
    return {"fit": base["score"], "adjusted_fit": adj["score"], "factor": adj["factor"],
            "reasons": base["reasons"], "learned": adj["reasons"], "configured": base["configured"]}


@router.get("/leads/{lead_id}/fit")
def lead_fit(lead_id: int):
    return fit_for(dict(_own_lead(lead_id)))


class FeedbackPayload(BaseModel):
    verdict: str
    reason: str = "other"
    note: str = Field("", max_length=500)
    mark_lost: bool = True


@router.post("/leads/{lead_id}/feedback")
def give_feedback(lead_id: int, p: FeedbackPayload):
    lead = _own_lead(lead_id)
    verdict = p.verdict.upper()
    if verdict not in feedback.VERDICTS:
        raise HTTPException(400, f"verdict must be one of {', '.join(feedback.VERDICTS)}")
    reason = p.reason.lower() if p.reason.lower() in feedback.NOT_FIT_REASONS else "other"
    now = utcnow()
    execute("""INSERT INTO interactions (workspace_id, lead_id, type, direction, date, subject, content,
               outcome, automated, created_at) VALUES(?,?,'note','out',?,?,?,?,0,?)""",
            (current_ws(), lead_id, now, "Fit feedback", p.note,
             f"FEEDBACK:{verdict}:{reason}" if verdict == "NOT_A_FIT" else "FEEDBACK:GOOD_FIT", now))
    if verdict == "NOT_A_FIT" and p.mark_lost and lead.get("status") not in ("WON", "LOST"):
        execute("UPDATE leads SET status = 'LOST', updated_at = ? WHERE id = ? AND workspace_id = ?",
                (now, lead_id, current_ws()))
    summary = relearn()
    return {"recorded": verdict, "learned_features": summary["features"], "fit": fit_for(dict(_own_lead(lead_id)))}


def relearn() -> dict:
    """Rebuild multipliers from every stored verdict in this workspace."""
    rows = query(
        "SELECT i.outcome, l.* FROM interactions i JOIN leads l ON l.id = i.lead_id "
        "WHERE i.workspace_id = ? AND i.outcome LIKE ?", (current_ws(), "FEEDBACK:%"))
    latest: dict[int, tuple[dict, bool]] = {}
    for r in rows:                                       # one vote per lead; the last wins
        latest[r["id"]] = (dict(r), r["outcome"].startswith("FEEDBACK:GOOD_FIT"))
    adj = feedback.learn(list(latest.values()))
    set_ws_setting("fit_adjustments", json.dumps(adj))
    good = sum(1 for _, g in latest.values() if g)
    return {"features": len(adj), "examples": len(latest), "good": good, "not_fit": len(latest) - good}


@router.get("/feedback/summary")
def feedback_summary():
    adj = _adjustments()
    items = sorted(({"feature": k, **v} for k, v in adj.items()), key=lambda x: abs(x["mult"] - 1), reverse=True)
    counts = query("SELECT i.outcome, COUNT(*) AS n FROM interactions i WHERE i.workspace_id = ? "
                   "AND i.outcome LIKE ? GROUP BY i.outcome ORDER BY n DESC", (current_ws(), "FEEDBACK:%"))
    return {"adjustments": items[:30], "verdicts": counts,
            "note": f"A feature needs {feedback.MIN_SAMPLES}+ verdicts before it changes anything."}


@router.post("/feedback/relearn")
def force_relearn():
    return relearn()


# --------------------------------------------------------------- API tokens --
class TokenPayload(BaseModel):
    name: str = Field("API token", max_length=80)


def _cookie_only(request: Request, user: dict) -> dict:
    if getattr(request.state, "auth", "cookie") != "cookie":
        raise HTTPException(403, "Tokens can only be managed from a signed-in browser session")
    return user


@router.get("/tokens")
def my_tokens(request: Request):
    user = _cookie_only(request, request.state.user)
    return {"tokens": apitokens.list_for(user["id"]),
            "allowed": list(apitokens.READ_PREFIXES),
            "usage": 'curl -H "Authorization: Bearer lgk_..." <this-site>/api/leads?limit=20'}


@router.post("/tokens")
def make_token(p: TokenPayload, request: Request):
    user = _cookie_only(request, request.state.user)
    try:
        return {**apitokens.create(user["id"], current_ws(), p.name),
                "warning": "Copy it now - it is shown only once."}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/tokens/{token_id}")
def revoke_token(token_id: int, request: Request):
    user = _cookie_only(request, request.state.user)
    if not apitokens.revoke(user["id"], token_id):
        raise HTTPException(404, "Token not found or already revoked")
    return {"revoked": token_id}


# ---------------------------------------------------------- review growth --
@router.get("/leads/{lead_id}/review-velocity")
def review_velocity(lead_id: int):
    _own_lead(lead_id, "id")
    hist = snapshots.history(lead_id)
    return {"snapshots": hist, **reviewmine.review_velocity(
        [{"at": h["at"], "reviews": h["reviews"]} for h in hist])}


# ----------------------------------------------------------- local AI draft --
@router.get("/ai/status")
def ai_status():
    return aidraft.status()


class DraftPayload(BaseModel):
    offer: str = Field(min_length=3, max_length=300)
    channel: str = Field("email", pattern="^(email|whatsapp)$")
    model: str | None = None


@router.post("/leads/{lead_id}/ai-draft")
def ai_draft(lead_id: int, p: DraftPayload, request: Request):
    lead = dict(_own_lead(lead_id))
    sender = (request.state.user.get("full_name") or "our studio")
    try:
        return aidraft.draft(lead, p.offer, sender, p.channel, p.model)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
