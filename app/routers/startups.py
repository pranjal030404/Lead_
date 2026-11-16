"""New startups: find them in the company registries, review, check and update."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from .. import registry_check
from ..db import current_ws, execute, lead_scope, query, query_one, utcnow
from ..providers.web_ai import clamp_batch
from ..search_service import start_search
from ..security import require_role
from ..subscription import check_search_entitlement, max_results_for
from .leads import _own_lead

router = APIRouter(prefix="/api/startups", tags=["startups"])
WRITE = Depends(require_role("superadmin", "admin", "user"))

_REG = re.compile(r"(?:incorporated|registered) (\d{4}-\d{2}-\d{2}|\d{2}[/-]\d{2}[/-]\d{4}|\d{4}-\d{2}|\d{4})")


_ABOUT = re.compile(r"registered [^;]*;\s*(.*)\)\s*$")
_FIELDS = ("owner_name", "email", "phone", "address", "website_url", "instagram_url", "facebook_url", "linkedin_url")


def _about(source: str | None) -> str:
    m = _ABOUT.search(source or "")
    return m.group(1).strip() if m else ""


def _registered(source: str | None) -> str | None:
    m = _REG.search(source or "")
    return m.group(1) if m else None


@router.get("")
def list_startups(country: str = "", q: str = "", closed: bool = False,
                  limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    """Registry leads (UK Companies House / India MCA) with owner and status."""
    scope, params = lead_scope()
    sql = (f"SELECT id, business_name, owner_name, address, city, state, pincode, country, website_url, "
           f"email, phone, instagram_url, facebook_url, linkedin_url, created_at, status, business_status, lead_score, source, place_id, updated_at FROM leads "
           f"WHERE {scope} AND (place_id LIKE 'ch:%%' OR place_id LIKE 'mca:%%' OR place_id LIKE 'web:%%')")
    if country:
        sql += " AND country = ?"; params.append(country)
    if q:
        sql += " AND (business_name LIKE ? OR address LIKE ? OR owner_name LIKE ?)"
        params += [f"%{q}%"] * 3
    if not closed:
        sql += " AND COALESCE(business_status,'') <> 'CLOSED_PERMANENTLY'"
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    rows = query(sql, (*params, limit, offset))
    for r in rows:
        r["registry"] = {"ch": "uk", "mca": "india"}.get(r["place_id"].split(":", 1)[0], "web")
        r["registration_number"] = r["place_id"].split(":", 1)[1] if r["registry"] != "web" else "web"
        r["registered_on"] = _registered(r["source"])
        r["about"] = _about(r["source"])
        have = sum(1 for k in _FIELDS if r.get(k)) + bool(r["registered_on"])
        r["completeness"] = round(100 * have / (len(_FIELDS) + 1))
    return {"count": len(rows), "startups": rows}


class FindPayload(BaseModel):
    niche: str = "tech"
    city: str = ""
    country: str = "both"          # any country name, uk, india, both or worldwide
    max_results: int = 15          # 10-20 per run, never more


@router.post("/find")
def find(payload: FindPayload, request: Request, _: dict = WRITE):
    """Search the registries for newly incorporated companies (background run)."""
    user = getattr(request.state, "user", None) or {}
    ent = check_search_entitlement(user)
    if not ent["ok"]:
        raise HTTPException(403, "Plan search limit reached or no active subscription.")
    c = payload.country.strip()
    low = c.lower()
    if low in ("", "both", "all", "worldwide", "global"):
        countries = [""] if low in ("all", "worldwide", "global") else ["United Kingdom", "India"]
    elif low == "uk":
        countries = ["United Kingdom"]
    elif low == "india":
        countries = ["India"]
    else:
        countries = [c[:100]]
    runs = [("web_ai", x) for x in countries]
    ids = [start_search(niche=payload.niche.strip() or "tech", city=payload.city.strip(), area="",
                        country=country, max_results=clamp_batch(max_results_for(user, payload.max_results)),
                        provider_name=prov, enrich=True, triggered_by="manual",
                        user_id=user.get("id")) for prov, country in runs]
    return {"search_ids": ids, "status": "RUNNING"}


@router.post("/check")
def check(dry_run: bool = True, limit: int = Query(100, ge=1, le=500), _: dict = WRITE):
    """Re-check registry leads: update addresses, retire dissolved companies, remove untouched
    dead ones. Defaults to a DRY RUN - pass dry_run=false to apply."""
    return {"dry_run": dry_run, **registry_check.check_workspace(limit, dry_run)}


@router.post("/{lead_id}/owners")
def owners(lead_id: int, _: dict = WRITE):
    """Fetch current directors (UK, needs the free Companies House key) and save the first as owner."""
    lead = _own_lead(lead_id, "id, place_id, owner_name")
    pid = lead["place_id"] or ""
    if pid.startswith("mca:"):
        raise HTTPException(400, "India's public MCA dataset has no director names; "
                                 "add the owner manually or via website enrichment")
    if not pid.startswith("ch:"):
        raise HTTPException(400, "Not a registry lead")
    try:
        people = registry_check.uk_officers(pid[3:])
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    if people and not lead["owner_name"]:
        execute("UPDATE leads SET owner_name = ?, updated_at = ? WHERE id = ? AND workspace_id = ?",
                (people[0]["name"], utcnow(), lead_id, current_ws()))
    return {"officers": people}


@router.post("/india-csv")
async def upload_india_csv(file: UploadFile = File(...), _: dict = Depends(require_role("superadmin", "admin"))):
    """Upload an MCA 'Company Master Data' CSV (data.gov.in) so India searches work."""
    from ..providers.india_csv import folder
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", file.filename or "mca.csv")
    if not name.lower().endswith(".csv"):
        raise HTTPException(400, "Upload a .csv file")
    data = await file.read()
    if len(data) > 200 * 1024 * 1024:
        raise HTTPException(413, "File too large (max 200 MB)")
    (folder() / name).write_bytes(data)
    return {"saved": name, "bytes": len(data), "files": len(list(folder().glob("*.csv")))}
