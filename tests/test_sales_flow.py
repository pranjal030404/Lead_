"""Pipelines (configurable stages), meetings (+ics) and proposals (+public accept page)."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import campaigns, proposals as psvc
from app.config import settings
from app.db import execute, query, query_one, utcnow, workspace_context
from app.main import app
from app.outreach import suppress
from app.security import SESSION_COOKIE, issue_token

WS = None


@pytest.fixture()
def api():
    global WS
    c = TestClient(app)
    c.cookies.set(SESSION_COOKIE, issue_token("admin"))
    WS = query_one("SELECT workspace_id FROM workspace_members ORDER BY workspace_id LIMIT 1")["workspace_id"]
    return c


def _lead(name="Flow Cafe", email="flow@shop.example", status="NEW", **extra):
    cols = {"workspace_id": WS, "business_name": name, "city": "Pune", "niche": "cafes", "lead_score": 6,
            "lead_priority": "WARM", "status": status, "email": email, "email_valid": 1,
            "created_at": utcnow(), "updated_at": utcnow(), **extra}
    return execute(f"INSERT INTO leads({', '.join(cols)}) VALUES({', '.join('?' for _ in cols)})", tuple(cols.values()))


def _status(lid):
    return query_one("SELECT status FROM leads WHERE id = ?", (lid,))["status"]


def _future(days=2, hours=0):
    return (datetime.now(timezone.utc) + timedelta(days=days, hours=hours)).isoformat(timespec="seconds")


# ================================================================ pipeline ===
def test_core_stages_seeded_and_custom_stage_lifecycle(api):
    names = [s["name"] for s in api.get("/api/pipeline/stages").json()["stages"]]
    assert names[:2] == ["NEW", "CONTACTED"] and "WON" in names and "LOST" in names
    r = api.post("/api/pipeline/stages", json={"label": "Demo Booked", "color": "#112233"})
    assert r.status_code == 200, r.text
    stage = r.json()
    assert stage["name"] == "DEMO_BOOKED" and not stage["is_core"]
    order = [s["name"] for s in api.get("/api/pipeline/stages").json()["stages"]]
    assert order.index("DEMO_BOOKED") < order.index("WON")                 # new stages sit before WON/LOST
    assert api.post("/api/pipeline/stages", json={"label": "demo booked"}).status_code == 409
    assert api.patch(f"/api/pipeline/stages/{stage['id']}", json={"color": "#445566", "label": "Demo"}).json()["color"] == "#445566"

    lid = _lead()
    assert api.post(f"/api/leads/{lid}/stage", json={"stage": "demo_booked"}).json()["changed"] is True
    assert _status(lid) == "DEMO_BOOKED"
    assert api.delete(f"/api/pipeline/stages/{stage['id']}").status_code == 409          # has a lead in it
    assert api.delete(f"/api/pipeline/stages/{stage['id']}", params={"move_to": "CONTACTED"}).json()["moved"] == 1
    assert _status(lid) == "CONTACTED"
    core = query_one("SELECT id FROM pipeline_stages WHERE workspace_id = ? AND name = 'WON'", (WS,))
    assert api.delete(f"/api/pipeline/stages/{core['id']}").status_code == 400
    assert api.post(f"/api/leads/{lid}/stage", json={"stage": "NOPE"}).status_code == 400


def test_reorder_requires_every_stage(api):
    ids = [s["id"] for s in api.get("/api/pipeline/stages").json()["stages"]]
    assert api.post("/api/pipeline/stages/reorder", json={"ids": ids[:-1]}).status_code == 400
    assert api.post("/api/pipeline/stages/reorder", json={"ids": list(reversed(ids))}).status_code == 200
    assert api.post("/api/pipeline/stages/reorder", json={"ids": ids}).status_code == 200


def test_stages_are_per_workspace(api):
    from app.tenancy import create_workspace
    api.post("/api/pipeline/stages", json={"label": "Only Here"})
    other = create_workspace("Other Co")
    with workspace_context(other):
        from app import pipeline
        assert "ONLY_HERE" not in pipeline.stage_names() and "WON" in pipeline.stage_names()
    api.delete(f"/api/pipeline/stages/{query_one('SELECT id FROM pipeline_stages WHERE workspace_id = ? AND name = ?', (WS, 'ONLY_HERE'))['id']}")


def test_board_endpoint_uses_workspace_stages(api):
    board = api.get("/api/pipeline").json()
    assert {"NEW", "WON"} <= {s["name"] for s in board} and all("label" in s for s in board)


# ================================================================ meetings ===
def test_meeting_books_pipeline_task_conflict_and_ics(api):
    lid = _lead("Meet Cafe", status="CONTACTED")
    start = _future(2)
    r = api.post("/api/meetings", json={"lead_id": lid, "title": "Intro call", "starts_at": start,
                                         "duration_min": 30, "location": "https://meet.example/x", "notes": "bring; ideas, ok"})
    assert r.status_code == 200, r.text
    m = r.json()
    assert _status(lid) == "MEETING_SET"
    assert query_one("SELECT 1 AS x FROM tasks WHERE lead_id = ? AND title LIKE ?", (lid, "Prepare for meeting%"))
    assert m["conflicts"] == []
    clash = api.post("/api/meetings", json={"lead_id": lid, "title": "Overlap", "starts_at": start, "duration_min": 15}).json()
    assert [c["id"] for c in clash["conflicts"]] == [m["id"]]

    ics = api.get(f"/api/meetings/{m['id']}/ics")
    assert ics.status_code == 200 and ics.headers["content-type"].startswith("text/calendar")
    assert "BEGIN:VEVENT" in ics.text and "SUMMARY:Intro call" in ics.text and "bring\; ideas\\, ok" in ics.text
    assert "DTSTART:" in ics.text and ics.text.endswith("\r\n")

    assert api.get("/api/meetings").json()["count"] >= 1
    done = api.patch(f"/api/meetings/{m['id']}", json={"status": "COMPLETED"}).json()
    assert done["status"] == "COMPLETED"
    assert query_one("SELECT 1 AS x FROM tasks WHERE lead_id = ? AND title LIKE ?", (lid, "Send proposal%"))
    assert m["id"] not in [x["id"] for x in api.get("/api/meetings").json()["meetings"]]           # no longer upcoming
    assert m["id"] in [x["id"] for x in api.get("/api/meetings", params={"when": "past"}).json()["meetings"]]


def test_meeting_validation_and_pipeline_never_moves_backwards(api):
    lid = _lead("Late Stage", status="NEGOTIATION")
    assert api.post("/api/meetings", json={"lead_id": lid, "title": "x", "starts_at": "garbage"}).status_code == 400
    assert api.post("/api/meetings", json={"lead_id": 99999999, "title": "x", "starts_at": _future()}).status_code == 404
    api.post("/api/meetings", json={"lead_id": lid, "title": "Check-in", "starts_at": _future(3)})
    assert _status(lid) == "NEGOTIATION"                                            # forward-only


def test_meeting_stops_campaign_sequence(api):
    c = api.post("/api/campaigns", json={"name": "m", "steps": [{"template": "cold_email_first"}]}).json()
    lid = _lead("Seq Meet", "seqmeet@shop.example")
    api.post(f"/api/campaigns/{c['id']}/enroll", json={"lead_ids": [lid]})
    api.post("/api/meetings", json={"lead_id": lid, "title": "Call", "starts_at": _future()})
    e = query_one("SELECT status, stop_reason FROM campaign_enrollments WHERE lead_id = ?", (lid,))
    assert e["status"] == "STOPPED" and "meeting" in e["stop_reason"]


# =============================================================== proposals ===
def test_money_math_is_exact():
    calc = psvc.compute([{"description": "a", "qty": 3, "unit_price": 33.33}, {"description": "b", "qty": 1, "unit_price": 0.10}], 10, 18)
    assert calc["subtotal"] == 100.09 and calc["discount"] == 10.01
    assert calc["taxable"] == 90.08 and calc["tax"] == 16.21 and calc["total"] == 106.29
    assert psvc.compute([{"description": "x", "qty": 1, "unit_price": 0.005}], 0, 0)["total"] == 0.01      # half-up, not banker's


def _proposal(api, lid, **kw):
    body = {"lead_id": lid, "title": "Website rebuild", "currency": "inr", "tax_pct": 18,
            "items": [{"description": "Design", "qty": 1, "unit_price": 40000}, {"description": "Dev", "qty": 2, "unit_price": 30000}], **kw}
    r = api.post("/api/proposals", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_proposal_draft_edit_send_and_lock(api):
    lid = _lead("Prop Cafe", status="CONTACTED")
    p = _proposal(api, lid)
    assert p["status"] == "DRAFT" and p["number"].startswith("P-") and p["total"] == 118000.0 and p["currency"] == "INR"
    assert _proposal(api, lid)["number"] != p["number"]
    upd = api.patch(f"/api/proposals/{p['id']}", json={"discount_pct": 10}).json()
    assert upd["discount"] == 10000.0 and upd["total"] == 106200.0
    assert api.get(f"/p/{p['share_token']}").status_code == 404                     # drafts are private
    sent = api.post(f"/api/proposals/{p['id']}/send", json={}).json()
    assert sent["status"] == "SENT" and sent["valid_until"] and _status(lid) == "PROPOSAL_SENT"
    assert api.patch(f"/api/proposals/{p['id']}", json={"title": "x"}).status_code == 409
    assert api.delete(f"/api/proposals/{p['id']}").status_code == 409
    assert api.post(f"/api/proposals/{p['id']}/send", json={}).status_code == 409


def test_client_accepts_via_public_page(api):
    lid = _lead("Accept Cafe", status="CONTACTED")
    p = _proposal(api, lid, title="<script>alert(1)</script> Plan",
                  items=[{"description": "<img src=x onerror=alert(1)>", "qty": 1, "unit_price": 5000}])
    api.post(f"/api/proposals/{p['id']}/send", json={})
    anon = TestClient(app)                                                          # no login
    page = anon.get(f"/p/{p['share_token']}")
    assert page.status_code == 200 and "Accept proposal" in page.text
    assert "<script>alert(1)</script>" not in page.text and "<img src=x" not in page.text      # escaped
    assert "&lt;script&gt;" in page.text
    assert anon.post(f"/p/{p['share_token']}/decision", json={"accepted": True}).status_code == 200
    assert _status(lid) == "WON"
    assert api.get(f"/api/proposals/{p['id']}").json()["status"] == "ACCEPTED"
    assert anon.post(f"/p/{p['share_token']}/decision", json={"accepted": False}).status_code == 409     # final
    assert "Accepted" in anon.get(f"/p/{p['share_token']}").text
    rev = api.get("/api/proposals/revenue").json()
    assert rev["won_total"] >= 5900.0 and rev["accepted"] >= 1 and rev["by_month"]
    assert anon.get("/p/not-a-real-token").status_code == 404


def test_decline_creates_followup_and_expired_cannot_be_accepted(api):
    lid = _lead("Decline Cafe", status="CONTACTED")
    p = _proposal(api, lid)
    api.post(f"/api/proposals/{p['id']}/send", json={})
    assert TestClient(app).post(f"/p/{p['share_token']}/decision", json={"accepted": False}).status_code == 200
    assert _status(lid) == "NEGOTIATION"
    assert query_one("SELECT 1 AS x FROM tasks WHERE lead_id = ? AND title LIKE ?", (lid, "Follow up: proposal%"))

    lid2 = _lead("Expired Cafe", status="CONTACTED")
    p2 = _proposal(api, lid2)
    api.post(f"/api/proposals/{p2['id']}/send", json={})
    execute("UPDATE proposals SET valid_until = '2001-01-01T00:00:00+00:00' WHERE id = ?", (p2["id"],))
    assert api.get(f"/api/proposals/{p2['id']}").json()["effective_status"] == "EXPIRED"
    assert TestClient(app).post(f"/p/{p2['share_token']}/decision", json={"accepted": True}).status_code == 409
    assert _status(lid2) == "PROPOSAL_SENT"


def test_send_with_email_is_approval_gated(api, monkeypatch):
    lid = _lead("Mail Prop", "mailprop@shop.example", status="CONTACTED")
    p = _proposal(api, lid)
    assert api.post(f"/api/proposals/{p['id']}/send", json={"queue_email": True}).status_code == 400     # no PUBLIC_URL
    monkeypatch.setattr(settings, "public_url", "https://leads.example.com")
    sent = api.post(f"/api/proposals/{p['id']}/send", json={"queue_email": True})
    assert sent.status_code == 200, sent.text
    q = query_one("SELECT * FROM approval_queue WHERE id = ?", (sent.json()["queued_email_id"],))
    assert q["status"] == "PENDING" and f"https://leads.example.com/p/{p['share_token']}" in q["body"]

    lid2 = _lead("Opted Out", "optout@shop.example", status="CONTACTED")
    p2 = _proposal(api, lid2)
    with workspace_context(WS):
        suppress("optout@shop.example", "email", "test")
    assert api.post(f"/api/proposals/{p2['id']}/send", json={"queue_email": True}).status_code == 400
    assert api.get(f"/api/proposals/{p2['id']}").json()["status"] == "DRAFT"        # failed send left it a draft


def test_validation(api):
    lid = _lead()
    bad = [{"items": []}, {"items": [{"description": "x", "qty": 0, "unit_price": 1}]},
           {"items": [{"description": "x", "qty": 1, "unit_price": -5}]}, {"discount_pct": 120}, {"currency": "RUPEES"}]
    for patch in bad:
        body = {"lead_id": lid, "title": "t", "items": [{"description": "x", "qty": 1, "unit_price": 1}], **patch}
        assert api.post("/api/proposals", json=body).status_code == 422, patch
    assert api.post("/api/proposals", json={"lead_id": 99999999, "title": "t", "items": [{"description": "x", "qty": 1, "unit_price": 1}]}).status_code == 404
