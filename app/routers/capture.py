"""Inbound lead capture: a free, self-hosted landing form (/c/<workspace-slug>).

This is the inbound half of the funnel. A visitor fills the form and becomes a
lead with source `Inbound form`, HOT priority (they asked to be contacted) and
the UTM / referrer details saved in the notes. Spam is handled with a honeypot
field, a per-IP rate limit and de-duplication on email/phone. The form posts to
the same origin, so the app's CSRF guard stays on. No third-party form tool.
"""

from __future__ import annotations

import html
import re

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ..branding import get_branding
from ..db import execute, log_failure, query_one, utcnow, workspace_context
from ..security import client_ip, rate_limit
from ..validation import normalise_phone, validate_email, whatsapp_number

public = APIRouter(tags=["capture-public"])

SLUG_RE = re.compile(r"^[a-z0-9-]{1,100}$")


class CaptureIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    business: str = Field("", max_length=200)
    email: str = Field("", max_length=255)
    phone: str = Field("", max_length=40)
    website: str = Field("", max_length=300)
    message: str = Field("", max_length=2000)
    utm_source: str = Field("", max_length=100)
    utm_campaign: str = Field("", max_length=100)
    referrer: str = Field("", max_length=300)
    company_url: str = Field("", max_length=300)     # honeypot: humans leave it empty


def _workspace(slug: str) -> dict:
    from .growth import enabled
    if not enabled("growth_capture_enabled"):
        raise HTTPException(404, "Form not found")
    row = SLUG_RE.match(slug or "") and query_one(
        "SELECT id, name FROM workspaces WHERE slug = ? AND is_active = 1 ORDER BY id LIMIT 1",
        (slug,))
    if not row:
        raise HTTPException(404, "Form not found")
    return row


def _enrich_later(lead_id: int, workspace_id: int) -> None:
    try:
        from ..enrich import enrich_lead
        with workspace_context(workspace_id):
            enrich_lead(lead_id)
    except Exception as exc:  # noqa: BLE001 - never lose an inbound lead over enrichment
        log_failure("capture.enrich", str(exc), lead_id)


def _existing(workspace_id: int, email: str | None, phone: str | None) -> dict | None:
    if email:
        row = query_one("SELECT id FROM leads WHERE workspace_id = ? AND email = ? LIMIT 1",
                        (workspace_id, email))
        if row:
            return row
    if phone:
        return query_one("SELECT id FROM leads WHERE workspace_id = ? AND phone = ? LIMIT 1",
                         (workspace_id, phone))
    return None


def capture(workspace_id: int, p: CaptureIn) -> tuple[int, bool]:
    """Create (or refresh) the lead. Returns (lead_id, created)."""
    email, email_ok, _ = validate_email(p.email)
    phone, phone_ok = normalise_phone(p.phone, "") if p.phone else (None, False)
    if not email_ok and not phone_ok:
        raise HTTPException(400, "Please give a valid email or phone number")
    now = utcnow()
    note = "Inbound enquiry"
    bits = [f"utm_source={p.utm_source}" if p.utm_source else "",
            f"utm_campaign={p.utm_campaign}" if p.utm_campaign else "",
            f"referrer={p.referrer}" if p.referrer else ""]
    note += " (" + ", ".join(b for b in bits if b) + ")" if any(bits) else ""
    if p.message.strip():
        note += f": {p.message.strip()}"
    with workspace_context(workspace_id):
        dup = _existing(workspace_id, email if email_ok else None, phone if phone_ok else None)
        if dup:
            execute("UPDATE leads SET notes = CONCAT(COALESCE(notes, ''), ?), updated_at = ?, "
                    "lead_priority = 'HOT' WHERE id = ? AND workspace_id = ?",
                    (f"\n[{now}] {note}", now, dup["id"], workspace_id))
            return dup["id"], False
        lead_id = execute(
            """INSERT INTO leads (workspace_id, owner_user_id, business_name, owner_name, email,
               email_valid, phone, phone_valid, whatsapp, website_url, status, source,
               lead_priority, lead_score, notes, found_date, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (workspace_id, 0, p.business.strip() or p.name.strip(), p.name.strip(),
             email if email_ok else None, 1 if email_ok else 0,
             phone if phone_ok else None, 1 if phone_ok else 0,
             whatsapp_number(phone, "") if phone_ok else None,
             p.website.strip() or None, "NEW", "Inbound form", "HOT", 8, note, now, now, now))
        return lead_id, True


@public.get("/c/{slug}", response_class=HTMLResponse)
def form(slug: str):
    _workspace(slug)
    brand = html.escape(get_branding()["brand_name"])
    return HTMLResponse(_PAGE.replace("{{BRAND}}", brand).replace("{{SLUG}}", html.escape(slug)),
                        headers={"Cache-Control": "no-store"})


@public.post("/c/{slug}")
def submit(slug: str, p: CaptureIn, request: Request, tasks: BackgroundTasks):
    ws = _workspace(slug)
    if p.company_url:                                   # honeypot tripped: pretend success
        return {"ok": True}
    if not rate_limit(f"capture:{client_ip(request)}", limit=5, window_seconds=600):
        raise HTTPException(429, "Too many submissions - try again later")
    lead_id, created = capture(ws["id"], p)
    if created:
        tasks.add_task(_enrich_later, lead_id, ws["id"])
    return {"ok": True}


_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>Get in touch - {{BRAND}}</title><link rel="stylesheet" href="/api/branding/theme.css">
<style>body{font-family:system-ui,sans-serif;background:var(--bg,#0b0b0d);color:var(--text,#eee);margin:0;padding:24px}
main{max-width:460px;margin:0 auto}h1{font-size:1.5rem}label{display:block;margin:12px 0 4px;font-size:.9rem}
input,textarea{width:100%;box-sizing:border-box;padding:10px;border-radius:8px;border:1px solid #444;background:#1a1a1d;color:inherit;font:inherit}
button{margin-top:16px;padding:12px 18px;border:0;border-radius:8px;background:var(--accent,#f0b429);color:#111;font-weight:600;cursor:pointer}
.hp{position:absolute;left:-9999px}#msg{margin-top:14px}</style></head><body><main>
<h1>Tell us about your project</h1><p>Leave your details and we will get back to you.</p>
<form id="f"><label>Your name *<input name="name" required maxlength="120"></label>
<label>Business<input name="business" maxlength="200"></label>
<label>Email<input name="email" type="email" maxlength="255"></label>
<label>Phone / WhatsApp<input name="phone" maxlength="40"></label>
<label>Website<input name="website" maxlength="300"></label>
<label>What do you need?<textarea name="message" rows="4" maxlength="2000"></textarea></label>
<div class="hp" aria-hidden="true"><input name="company_url" tabindex="-1" autocomplete="off"></div>
<button>Send</button><div id="msg" role="status"></div></form>
<script>
const q=new URLSearchParams(location.search),f=document.getElementById('f'),m=document.getElementById('msg');
f.addEventListener('submit',async e=>{e.preventDefault();const d=Object.fromEntries(new FormData(f));
d.utm_source=q.get('utm_source')||'';d.utm_campaign=q.get('utm_campaign')||'';d.referrer=document.referrer.slice(0,300);
const r=await fetch('/c/{{SLUG}}',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
if(r.ok){f.reset();m.textContent='Thank you - we will be in touch shortly.';}
else{const j=await r.json().catch(()=>({}));m.textContent=typeof j.detail==='string'?j.detail:'Please check the form and try again.';}});
</script></main></body></html>"""
