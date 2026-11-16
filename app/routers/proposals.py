"""Proposals API plus the public client page (/p/<token>)."""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from .. import proposal_pdf, proposals as svc
from ..branding import get_branding
from ..config import settings
from ..db import (sees_all, current_ws, execute, query, query_one, utcnow, workspace_context)
from ..outreach import ensure_email_validated, is_suppressed
from ..security import client_ip, rate_limit
from .leads import _own_lead

router = APIRouter(prefix="/api/proposals", tags=["proposals"])
public = APIRouter(tags=["proposals-public"])


class Item(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    qty: float = Field(1, gt=0, le=10000)
    unit_price: float = Field(ge=0, le=1_000_000_000)


class ProposalIn(BaseModel):
    lead_id: int
    title: str = Field(min_length=1, max_length=200)
    items: list[Item] = Field(min_length=1, max_length=50)
    currency: str = Field("INR", pattern="^[A-Za-z]{3}$")
    discount_pct: float = Field(0, ge=0, le=100)
    tax_pct: float = Field(0, ge=0, le=100)
    valid_until: str | None = Field(None, max_length=40)
    notes: str = Field("", max_length=5000)


class ProposalPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    items: list[Item] | None = Field(None, min_length=1, max_length=50)
    currency: str | None = Field(None, pattern="^[A-Za-z]{3}$")
    discount_pct: float | None = Field(None, ge=0, le=100)
    tax_pct: float | None = Field(None, ge=0, le=100)
    valid_until: str | None = Field(None, max_length=40)
    notes: str | None = Field(None, max_length=5000)


class SendIn(BaseModel):
    queue_email: bool = False


class DecideIn(BaseModel):
    accepted: bool


def _is_admin(request: Request) -> bool:
    return sees_all(getattr(request.state, "user", None))


def _own(pid: int, request: Request) -> dict:
    row = query_one("SELECT p.*, l.business_name FROM proposals p JOIN leads l ON l.id = p.lead_id "
                    "WHERE p.id = ? AND p.workspace_id = ?", (pid, current_ws()))
    uid = (getattr(request.state, "user", None) or {}).get("id")
    if not row or (not _is_admin(request) and row["owner_user_id"] != uid):
        raise HTTPException(404, "Proposal not found")
    return row


def _valid_until(value: str | None) -> str | None:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(400, "valid_until must be an ISO date, e.g. 2026-12-31") from exc
    return (d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d).isoformat(timespec="seconds")


def _items_json(items: list[Item]) -> str:
    return json.dumps([i.model_dump() for i in items])


@router.get("")
def list_proposals(request: Request, status: str | None = Query(None, pattern="^(DRAFT|SENT|ACCEPTED|DECLINED|EXPIRED)$"),
                   lead_id: int | None = None, limit: int = Query(200, le=500)):
    clauses, args = ["p.workspace_id = ?"], [current_ws()]
    if not _is_admin(request):
        clauses.append("p.owner_user_id = ?"); args.append(request.state.user["id"])
    if lead_id is not None:
        clauses.append("p.lead_id = ?"); args.append(lead_id)
    rows = query("SELECT p.*, l.business_name FROM proposals p JOIN leads l ON l.id = p.lead_id "
                 f"WHERE {' AND '.join(clauses)} ORDER BY p.id DESC LIMIT ?", (*args, limit))
    out = [svc.hydrate(r) for r in rows]
    if status:
        out = [p for p in out if p["effective_status"] == status]
    return {"proposals": out, "count": len(out)}


@router.get("/export")
def export_csv(request: Request):
    """All proposals visible to you as a CSV (formula-injection safe)."""
    from .. import exportutil
    rows = [{"number": p["number"], "title": p["title"], "lead": p["business_name"], "status": p["effective_status"],
             "currency": p["currency"], "subtotal": p["subtotal"], "discount": p["discount"], "tax": p["tax"],
             "total": p["total"], "valid_until": p["valid_until"], "sent_at": p["sent_at"],
             "decided_at": p["decided_at"], "created_at": p["created_at"]} for p in list_proposals(request, None, None, 500)["proposals"]]
    return Response(exportutil.to_csv(rows) if rows else "no proposals\n", media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="proposals.csv"'})


@router.get("/revenue")
def revenue(request: Request):
    return svc.revenue(None if _is_admin(request) else request.state.user["id"])


@router.post("")
def create(p: ProposalIn, request: Request):
    _own_lead(p.lead_id, "id")
    uid = request.state.user["id"]
    for attempt in range(5):                      # two people creating at once can race for the same number
        try:
            pid = execute("INSERT INTO proposals(workspace_id, lead_id, owner_user_id, number, title, status, "
                          "currency, items, discount_pct, tax_pct, subtotal, total, valid_until, notes, share_token, "
                          "created_at, updated_at) VALUES(?,?,?,?,?, 'DRAFT', ?,?,?,?,?,?,?,?,?,?,?)",
                          (current_ws(), p.lead_id, uid, svc.next_number(), p.title.strip(), p.currency.upper(),
                           _items_json(p.items), p.discount_pct, p.tax_pct, 0, 0, _valid_until(p.valid_until),
                           p.notes, svc.new_token(), utcnow(), utcnow()))
            break
        except Exception as exc:  # noqa: BLE001
            if "Duplicate" in str(exc) and attempt < 4:
                continue
            raise
    calc = svc.compute([i.model_dump() for i in p.items], p.discount_pct, p.tax_pct)
    execute("UPDATE proposals SET subtotal = ?, total = ? WHERE id = ? AND workspace_id = ?",
            (calc["subtotal"], calc["total"], pid, current_ws()))
    return svc.hydrate(_own(pid, request))


@router.get("/{pid}")
def get(pid: int, request: Request):
    return svc.hydrate(_own(pid, request))


@router.patch("/{pid}")
def update(pid: int, p: ProposalPatch, request: Request):
    row = _own(pid, request)
    if row["status"] != "DRAFT":
        raise HTTPException(409, "Only drafts can be edited - a sent proposal is what the client saw")
    items = _items_json(p.items) if p.items is not None else row["items"]
    disc = row["discount_pct"] if p.discount_pct is None else p.discount_pct
    tax = row["tax_pct"] if p.tax_pct is None else p.tax_pct
    calc = svc.compute(json.loads(items), disc, tax)
    execute("UPDATE proposals SET title = ?, currency = ?, items = ?, discount_pct = ?, tax_pct = ?, subtotal = ?, "
            "total = ?, valid_until = ?, notes = ?, updated_at = ? WHERE id = ? AND workspace_id = ?",
            (p.title.strip() if p.title else row["title"], p.currency.upper() if p.currency else row["currency"],
             items, disc, tax, calc["subtotal"], calc["total"],
             _valid_until(p.valid_until) if p.valid_until is not None else row["valid_until"],
             row["notes"] if p.notes is None else p.notes, utcnow(), pid, current_ws()))
    return svc.hydrate(_own(pid, request))


@router.delete("/{pid}")
def delete(pid: int, request: Request):
    row = _own(pid, request)
    if row["status"] != "DRAFT":
        raise HTTPException(409, "Only drafts can be deleted")
    execute("DELETE FROM proposals WHERE id = ? AND workspace_id = ?", (pid, current_ws()))
    return {"ok": True}


def _public_link(token: str) -> str:
    return f"{(settings.public_url or '').rstrip('/')}/p/{token}"


def _pdf_response(row: dict) -> Response:
    data = proposal_pdf.render(svc.hydrate(row), get_branding()["brand_name"])
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{row["number"]}.pdf"', "Cache-Control": "no-store"})


@router.get("/{pid}/pdf")
def pdf(pid: int, request: Request):
    return _pdf_response(_own(pid, request))


@router.post("/{pid}/send")
def send(pid: int, p: SendIn, request: Request):
    row = _own(pid, request)
    if row["status"] != "DRAFT":
        raise HTTPException(409, "This proposal was already sent")
    h = svc.hydrate(row)
    if h["total"] <= 0:
        raise HTTPException(400, "A proposal needs a total above zero")
    queued = None
    if p.queue_email:
        if not settings.public_url:
            raise HTTPException(400, "Set PUBLIC_URL in .env so the emailed link works, or send without email and share the link yourself")
        lead = query_one("SELECT * FROM leads WHERE id = ? AND workspace_id = ?", (row["lead_id"], current_ws()))
        ensure_email_validated(lead)
        if not lead.get("email") or not lead.get("email_valid") or is_suppressed(lead["email"]):
            raise HTTPException(400, "This lead has no usable email (missing, invalid or opted out)")
        company = get_branding()["brand_name"]
        body = (f"Hi {lead.get('owner_name') or 'there'},\n\nAs discussed, here is our proposal \"{row['title']}\" "
                f"({row['number']}) for {lead['business_name']}: {h['currency']} {h['total']:,.2f}.\n\n"
                f"You can review it, and accept or decline, here:\n{_public_link(row['share_token'])}\n\n"
                f"Thanks,\n{settings.mail_from_name or company}")
        queued = execute("INSERT INTO approval_queue(workspace_id, lead_id, channel, template_name, step, to_address, "
                         "subject, body, status, created_at) VALUES(?,?,'email','proposal',?,?,?,?,'PENDING',?)",
                         (current_ws(), row["lead_id"], f"proposal{pid}", lead["email"],
                          f"Proposal {row['number']}: {row['title']}", body, utcnow()))
    svc.mark_sent(h | {"valid_until": row["valid_until"]})
    return {**svc.hydrate(_own(pid, request)), "link": _public_link(row["share_token"]), "queued_email_id": queued}


@router.post("/{pid}/decision")
def decision(pid: int, p: DecideIn, request: Request):
    """Record the client's answer yourself (they said yes on the phone)."""
    _own(pid, request)
    who = f"recorded by {request.state.user['username']}"
    if not svc.decide(pid, p.accepted, who):
        raise HTTPException(409, "Only a sent, unexpired proposal can be accepted or declined")
    return svc.hydrate(_own(pid, request))


# ----------------------------------------------------------- public page ----
def _page(row: dict) -> str:
    e = html.escape
    h = svc.hydrate(row)
    brand = e(get_branding()["brand_name"])
    sym = e(h["currency"])
    rows = "".join(f"<tr><td>{e(i['description'])}</td><td class='n'>{i['qty']:g}</td>"
                   f"<td class='n'>{i['unit_price']:,.2f}</td><td class='n'>{i['line_total']:,.2f}</td></tr>"
                   for i in h["items"])
    extra = ""
    if h["discount"]:
        extra += f"<tr><td colspan='3'>Discount ({h['discount_pct']:g}%)</td><td class='n'>-{h['discount']:,.2f}</td></tr>"
    if h["tax"]:
        extra += f"<tr><td colspan='3'>Tax ({h['tax_pct']:g}%)</td><td class='n'>{h['tax']:,.2f}</td></tr>"
    st = h["effective_status"]
    valid = f"<p class='muted'>Valid until {e(h['valid_until'][:10])}</p>" if h["valid_until"] else ""
    if st == "SENT":
        actions = ("<div class='actions'><button id='yes'>Accept proposal</button>"
                   "<button id='no' class='alt'>Decline</button></div><p id='msg' class='muted'></p>")
    else:
        label = {"ACCEPTED": "Accepted - thank you!", "DECLINED": "Declined", "EXPIRED": "This proposal has expired",
                 "DRAFT": "Draft"}.get(st, st)
        actions = f"<p class='status {e(st)}'>{e(label)}</p>"
    notes = f"<div class='notes'>{e(h['notes'] or '').replace(chr(10), '<br>')}</div>" if h["notes"] else ""
    token = e(row["share_token"])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{e(h['number'])} - {e(h['title'])}</title>
<style>:root{{--bg:#fff;--fg:#111827;--muted:#6b7280;--line:#e5e7eb;--accent:#2563eb}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0f172a;--fg:#e5e7eb;--muted:#94a3b8;--line:#334155;--accent:#60a5fa}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}}
main{{max-width:760px;margin:0 auto;padding:32px 16px}}h1{{margin:.2em 0}}.muted{{color:var(--muted)}}
table{{width:100%;border-collapse:collapse;margin:20px 0}}th,td{{padding:10px 8px;border-bottom:1px solid var(--line);text-align:left}}
.n{{text-align:right;white-space:nowrap}}tfoot td{{font-weight:600}}.total td{{font-size:1.15em;border-bottom:2px solid var(--fg)}}
.actions{{display:flex;gap:12px;margin:24px 0}}button{{font:inherit;padding:12px 20px;border-radius:8px;border:0;background:var(--accent);color:#fff;cursor:pointer}}
button.alt{{background:transparent;color:var(--fg);border:1px solid var(--line)}}.status{{font-weight:600;font-size:1.1em}}
.ACCEPTED{{color:#16a34a}}.DECLINED,.EXPIRED{{color:#dc2626}}.notes{{margin-top:16px;padding:14px;border:1px solid var(--line);border-radius:8px}}
@media print{{.actions,#msg{{display:none}}}}</style></head><body><main>
<div class="muted">{brand}</div><h1>{e(h['title'])}</h1>
<div class="muted">{e(h['number'])} · Prepared for {e(h['business_name'] or '')}</div>{valid}
<table><thead><tr><th>Item</th><th class="n">Qty</th><th class="n">Price</th><th class="n">Amount ({sym})</th></tr></thead>
<tbody>{rows}</tbody><tfoot><tr><td colspan="3">Subtotal</td><td class="n">{h['subtotal']:,.2f}</td></tr>{extra}
<tr class="total"><td colspan="3">Total ({sym})</td><td class="n">{h['total']:,.2f}</td></tr></tfoot></table>
{notes}{actions}<p><a href="/p/{token}/pdf" style="color:var(--accent)">Download PDF</a></p>
<script>
const t="{token}",m=document.getElementById('msg');
async function go(a){{document.querySelectorAll('button').forEach(b=>b.disabled=true);
 const r=await fetch('/p/'+t+'/decision',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{accepted:a}})}});
 if(r.ok){{location.reload()}}else{{const d=await r.json().catch(()=>({{}}));m.textContent=d.detail||'Something went wrong';document.querySelectorAll('button').forEach(b=>b.disabled=false)}}}}
document.getElementById('yes')?.addEventListener('click',()=>go(true));document.getElementById('no')?.addEventListener('click',()=>go(false));
</script></main></body></html>"""


def _by_token(token: str, request: Request) -> dict:
    if not rate_limit(f"prop:{client_ip(request)}", limit=60, window_seconds=60):
        raise HTTPException(429, "Too many requests")
    row = query_one("SELECT p.*, l.business_name FROM proposals p JOIN leads l ON l.id = p.lead_id "
                    "WHERE p.share_token = ?", (token,))
    if not row or row["status"] == "DRAFT":      # drafts are private until sent
        raise HTTPException(404, "Proposal not found")
    return row


@public.get("/p/{token}", response_class=HTMLResponse)
def view(token: str, request: Request):
    row = _by_token(token, request)
    with workspace_context(row["workspace_id"]):
        return HTMLResponse(_page(row), headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})


@public.get("/p/{token}/pdf")
def view_pdf(token: str, request: Request):
    row = _by_token(token, request)
    with workspace_context(row["workspace_id"]):
        return _pdf_response(row)


@public.post("/p/{token}/decision")
def client_decision(token: str, p: DecideIn, request: Request):
    row = _by_token(token, request)
    with workspace_context(row["workspace_id"]):
        if not svc.decide(row["id"], p.accepted, f"by the client via link from {client_ip(request)}"):
            raise HTTPException(409, "This proposal can no longer be accepted or declined")
    return {"ok": True}
