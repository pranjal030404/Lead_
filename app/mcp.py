"""Minimal MCP server (Model Context Protocol, Streamable HTTP, JSON-RPC 2.0) for your leads.

Connect Claude or any MCP client to  POST /mcp  with an API token (Discover -> API access):

    claude mcp add --transport http leads http://127.0.0.1:8000/mcp --header "Authorization: Bearer lgk_..."

Tools are READ-ONLY and run as the token's owner inside their workspace: search leads, open one,
explain its fit and evidence, and see pipeline stats. Nothing here can edit, delete or send.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .db import lead_scope, query, query_one, scalar

router = APIRouter(tags=["mcp"])
PROTOCOL = "2025-03-26"
LEAD_COLS = ("id, business_name, niche, city, status, lead_priority, lead_score, phone, email, website_url, "
             "website_score, google_rating, google_reviews, source, created_at")

TOOLS = [
    {"name": "search_leads", "description": "Search your leads. Filter by text, status, priority, niche or city.",
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string"}, "status": {"type": "string", "description": "NEW, CONTACTED, REPLIED, MEETING_SET, WON, LOST"},
         "priority": {"type": "string", "enum": ["HOT", "WARM", "COLD"]}, "niche": {"type": "string"},
         "city": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}}},
    {"name": "get_lead", "description": "Full detail for one lead, including score reasons, website issues and recent interactions.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
    {"name": "explain_fit", "description": "Why a lead matches (or doesn't match) your ideal-client profile, plus an evidence-confidence grade.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
    {"name": "pipeline_stats", "description": "Counts of leads by status and priority, and follow-ups due.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def _search(a: dict) -> dict:
    scope, params = lead_scope()
    where, p = [scope], list(params)
    for col, key in (("status", "status"), ("lead_priority", "priority")):
        if a.get(key):
            where.append(f"{col} = ?"); p.append(str(a[key]).upper())
    for col in ("niche", "city"):
        if a.get(col):
            where.append(f"LOWER({col}) LIKE ?"); p.append("%" + str(a[col]).lower()[:60] + "%")
    if a.get("text"):
        where.append("(LOWER(business_name) LIKE ? OR LOWER(COALESCE(notes,'')) LIKE ?)")
        p += ["%" + str(a["text"]).lower()[:80] + "%"] * 2
    limit = max(1, min(int(a.get("limit") or 20), 50))
    rows = query(f"SELECT {LEAD_COLS} FROM leads WHERE {' AND '.join(where)} ORDER BY lead_score DESC, id DESC LIMIT ?",
                 (*p, limit))
    return {"count": len(rows), "leads": rows}


def _lead(lead_id: int) -> dict | None:
    scope, params = lead_scope()
    return query_one(f"SELECT * FROM leads WHERE id = ? AND {scope}", (lead_id, *params))


def _get_lead(a: dict) -> dict:
    lead = _lead(int(a.get("id", 0)))
    if not lead:
        return {"error": "Lead not found"}
    for k in ("password", "raw_json"):
        lead.pop(k, None)
    lead["recent_interactions"] = query(
        "SELECT type, direction, date, subject, outcome FROM interactions WHERE lead_id = ? ORDER BY id DESC LIMIT 8",
        (lead["id"],))
    return lead


def _explain(a: dict) -> dict:
    from . import evidence
    from .routers.fit import fit_for
    lead = _lead(int(a.get("id", 0)))
    if not lead:
        return {"error": "Lead not found"}
    return {"business": lead["business_name"], "fit": fit_for(dict(lead)), "evidence": evidence.grade_evidence(dict(lead))}


def _stats(_: dict) -> dict:
    scope, params = lead_scope()
    by = lambda col: {r[col] or "none": r["n"] for r in query(
        f"SELECT {col}, COUNT(*) AS n FROM leads WHERE {scope} GROUP BY {col}", tuple(params))}
    return {"total": scalar(f"SELECT COUNT(*) FROM leads WHERE {scope}", tuple(params)),
            "by_status": by("status"), "by_priority": by("lead_priority")}


HANDLERS = {"search_leads": _search, "get_lead": _get_lead, "explain_fit": _explain, "pipeline_stats": _stats}


def handle(msg: dict) -> dict | None:
    """One JSON-RPC message -> response dict (None for notifications). Pure of HTTP."""
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    ok = lambda result: {"jsonrpc": "2.0", "id": mid, "result": result}
    err = lambda code, text: {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": text}}
    if mid is None:                                     # notification: no reply
        return None
    if method == "initialize":
        return ok({"protocolVersion": PROTOCOL, "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": "leadgen-readonly", "version": "1.0"},
                   "instructions": "Read-only access to the user's lead pipeline."})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        fn = HANDLERS.get(params.get("name"))
        if not fn:
            return err(-32602, f"Unknown tool: {params.get('name')}")
        try:
            data = fn(params.get("arguments") or {})
        except (TypeError, ValueError) as exc:
            return ok({"content": [{"type": "text", "text": f"Bad arguments: {exc}"}], "isError": True})
        return ok({"content": [{"type": "text", "text": json.dumps(data, default=str, ensure_ascii=False)}],
                   "isError": "error" in data})
    return err(-32601, f"Method not found: {method}")


@router.post("/mcp")
async def mcp_endpoint(request: Request):
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, 400)
    batch = body if isinstance(body, list) else [body]
    if not all(isinstance(m, dict) for m in batch) or len(batch) > 20:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}, 400)
    out = [r for r in (handle(m) for m in batch) if r is not None]
    if not out:
        return Response(status_code=202)               # only notifications
    return JSONResponse(out if isinstance(body, list) else out[0])


@router.get("/mcp")
def mcp_get():
    return JSONResponse({"detail": "MCP endpoint - use POST (Streamable HTTP, JSON responses)."}, 405)
