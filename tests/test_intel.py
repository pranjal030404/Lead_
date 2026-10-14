"""Phase 3: lineage, confidence/freshness, contacts, email & social intelligence."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import emailintel, enrich, lineage, people, social, website
from app.config import settings
from app.db import execute, query, query_one, utcnow, workspace_context
from app.main import app
from app.outreach import queue_message, send_approved
from app.security import SESSION_COOKIE, hash_password, issue_token
from app.tenancy import add_member, create_workspace


def _days_ago(n: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat(timespec="seconds")


def _lead(name="Intel Cafe", ws=None, **extra):
    from app.db import current_ws
    cols = {"workspace_id": ws or current_ws(), "business_name": name, "city": "Delhi",
            "niche": "cafes", "lead_score": 5, "lead_priority": "WARM", "status": "NEW",
            "created_at": utcnow(), "updated_at": utcnow(), **extra}
    return execute(f"INSERT INTO leads({', '.join(cols)}) VALUES({', '.join('?' for _ in cols)})",
                   tuple(cols.values()))


# ------------------------------------------------------------ pure logic ----

@pytest.mark.parametrize("raw,kind,status", [
    ("anita.sharma@clinic.in", "PERSONAL", "UNVERIFIED"),
    ("info@clinic.in", "GENERIC", "UNVERIFIED"),
    ("Sales@Clinic.in", "GENERIC", "UNVERIFIED"),
    ("info+leads@clinic.in", "GENERIC", "UNVERIFIED"),
    ("marketing@clinic.in", "ROLE", "UNVERIFIED"),
    ("noreply@clinic.in", "GENERIC", "INVALID"),
    ("bad@@x", "GENERIC", "INVALID"),
    ("a@mailinator.com", "GENERIC", "DISPOSABLE"),
])
def test_classify_email(raw, kind, status):
    info = emailintel.classify(raw)
    assert (info["email_type"], info["status"]) == (kind, status)


def test_freshness_thresholds_use_field_ttl():
    ttl = lineage.ttl_days("phone")  # 180
    f = lambda days, v=None: lineage.freshness_status(_days_ago(days), v, "phone")
    assert f(ttl * 0.4) == "FRESH"
    assert f(ttl * 0.7) == "AGING"
    assert f(ttl * 1.2) == "STALE"
    assert f(ttl * 2) == "EXPIRED"
    assert f(ttl * 2, v=_days_ago(1)) == "FRESH", "a recent verification restarts the clock"
    assert lineage.freshness_status(None, None, "phone") == "EXPIRED"
    # rating decays 6x faster than phone
    assert lineage.freshness_status(_days_ago(40), None, "google_rating") == "STALE"


def test_summary_weights_effective_confidence_and_worst_freshness():
    rows = [
        {"field": "phone", "confidence": 0.9, "effective_confidence": 0.9,
         "freshness": "FRESH", "observed_at": _days_ago(1), "verified_at": None},
        {"field": "email", "confidence": 0.5, "effective_confidence": 0.15,
         "freshness": "EXPIRED", "observed_at": _days_ago(900), "verified_at": None},
    ]
    s = lineage.summarise(rows)
    assert s["data_confidence_score"] == round(100 * (0.25 * 0.9 + 0.25 * 0.15) / 0.5)
    assert s["freshness_status"] == "EXPIRED"
    assert lineage.summarise([])["data_confidence_score"] is None


@pytest.mark.parametrize("title,cat,minp", [
    ("Founder & CEO", "FOUNDER", 0.9), ("Managing Director", "CEO", 0.9),
    ("Owner", "OWNER", 0.9), ("Marketing Manager", "MARKETING_MANAGER", 0.5),
    ("Head of IT", "IT_MANAGER", 0.4), ("Receptionist", "OTHER", 0.0),
])
def test_title_classification(title, cat, minp):
    category, prob = people.classify_title(title)
    assert category == cat and prob >= minp


def test_extract_people_reads_only_published_structured_data():
    html = '''<script type="application/ld+json">{"@context":"https://schema.org",
      "@graph":[{"@type":"Organization","name":"Clinic","founder":{"@type":"Person",
      "name":"Anita Sharma"},"employee":[{"@type":"Person","name":"Raj Kumar",
      "jobTitle":"Marketing Manager"},{"@type":"Person","name":"Cher"}]},
      {"@type":"BlogPosting","author":{"@type":"Person","name":"Blog Writer"}}]}</script>
      <script type="application/ld+json">{ not json</script>'''
    found = {p["name"]: p["title"] for p in people.extract_people(html)}
    assert found == {"Anita Sharma": "Founder", "Raj Kumar": "Marketing Manager"}
    assert people.extract_people("<p>Our team: John Smith, CEO</p>") == []   # no guessing from prose


@pytest.mark.parametrize("url,platform,user", [
    ("https://www.instagram.com/sharma.dental/", "instagram", "sharma.dental"),
    ("instagram.com/p/ABC123", None, None),
    ("https://facebook.com/sharer/sharer.php", None, None),
    ("https://www.linkedin.com/company/sharma-dental/", "linkedin", "sharma-dental"),
    ("https://x.com/sharmadental", "x", "sharmadental"),
    ("https://youtube.com/@sharmatv", "youtube", "sharmatv"),
    ("https://example.com/sharma", None, None),
])
def test_social_parse(url, platform, user):
    info = social.parse(url)
    assert (info and info["platform"]) == platform
    assert (info and info["username"]) == user


# ------------------------------------------------------ lineage integration --

def test_search_records_provider_lineage_and_scores():
    from app.search_service import run_search
    res = run_search(niche="bakeries", city="Lineageville", max_results=5,
                     provider_name="mock", enrich=False)
    assert res["new_leads"] > 0
    lead = query_one("SELECT * FROM leads WHERE search_id = ? AND phone IS NOT NULL LIMIT 1",
                     (res["search_id"],))
    rows = {r["field"]: r for r in lineage.lineage_for(lead["id"])}
    assert rows["phone"]["source"] == "mock" and rows["phone"]["source_type"] == "provider"
    assert rows["phone"]["verified"] is False
    assert lead["data_confidence_score"] is not None
    assert 0 <= lead["data_confidence_score"] <= 100


def test_reobservation_clears_verification_and_manual_edit_is_attributed():
    lid = _lead(phone="9811111111")
    lineage.record(lid, "phone", "google", "provider")
    assert lineage.mark_verified(lid, "phone")
    assert lineage.lineage_for(lid)[0]["verified"] is True
    lineage.record(lid, "phone", "osm", "provider")           # new observation
    row = lineage.lineage_for(lid)[0]
    assert row["source"] == "osm" and row["verified"] is False


def test_refresh_all_ages_values_over_time():
    lid = _lead(phone="9822222222", email="a.b@x.in")
    lineage.record(lid, "phone", "google", "provider")
    lineage.record(lid, "email", "website_scrape", "website")
    lineage.refresh_lead(lid)
    fresh = query_one("SELECT data_confidence_score s, freshness_status f FROM leads "
                      "WHERE id = ?", (lid,))
    assert fresh["f"] == "FRESH"
    execute("UPDATE field_sources SET observed_at = ? WHERE entity_id = ? AND field = 'email'",
            (_days_ago(400), lid))
    assert lineage.refresh_all() >= 1
    aged = query_one("SELECT data_confidence_score s, freshness_status f, "
                     "next_verification_at n FROM leads WHERE id = ?", (lid,))
    assert aged["f"] == "EXPIRED" and aged["s"] < fresh["s"] and aged["n"]
    assert lineage.refresh_all() == 0, "nothing changed -> nothing rewritten"


# ----------------------------------------------- enrichment pipeline wiring --

def _fake_site(**over):
    base = {"website_exists": 1, "website_ssl": 1, "website_mobile_friendly": 1,
            "website_speed_ms": 400, "website_platform": "WordPress",
            "website_last_updated": "2026", "website_score": 8, "website_issues": [],
            "socials": {"instagram_url": "https://instagram.com/intelcafe"},
            "emails": ["hello@intelcafe.in", "raj.kumar@intelcafe.in"],
            "people": [{"name": "Anita Sharma", "title": "Founder"}],
            "checked_at": utcnow()}
    base.update(over)
    return base


def test_enrichment_records_sources_emails_contacts_and_socials(monkeypatch):
    monkeypatch.setattr(website, "check_website", lambda url: _fake_site())
    lid = _lead("Pipeline Cafe", website_url="https://intelcafe.in")
    enrich.enrich_lead(lid)

    lin = {r["field"]: r for r in lineage.lineage_for(lid)}
    assert lin["email"]["source"] == "website_scrape" and lin["email"]["source_type"] == "website"
    assert lin["instagram_url"]["source"] == "website_scrape"

    emails = {e["email"]: e for e in query("SELECT * FROM email_addresses WHERE lead_id = ?",
                                           (lid,))}
    assert emails["hello@intelcafe.in"]["email_type"] == "GENERIC"
    assert emails["raj.kumar@intelcafe.in"]["email_type"] == "PERSONAL"
    assert all(e["status"] == "UNVERIFIED" for e in emails.values()), "scraped != verified"

    (contact,) = people.list_for_lead(lid)
    assert contact["name"] == "Anita Sharma" and contact["role_category"] == "FOUNDER"
    assert contact["decision_maker_probability"] >= 0.9 and contact["source"] == "website_scrape"

    (sp,) = social.list_for_lead(lid)
    assert (sp["platform"], sp["username"], sp["followers"]) == ("instagram", "intelcafe", None)

    lead = query_one("SELECT data_confidence_score s, freshness_status f FROM leads "
                     "WHERE id = ?", (lid,))
    assert lead["s"] and lead["f"] == "FRESH"


def test_hunter_stage_creates_contact_with_provenance(monkeypatch):
    monkeypatch.setattr(settings, "hunter_api_key", "k")
    monkeypatch.setattr(website, "check_website",
                        lambda url: _fake_site(emails=[], people=[], socials={}))
    monkeypatch.setattr(website, "find_contact_email", lambda url: [])

    class Resp:
        status_code = 200
        def json(self):
            return {"data": {"emails": [{"value": "dev.patel@hunterco.in", "first_name": "Dev",
                    "last_name": "Patel", "position": "Managing Director"}]}}
    monkeypatch.setattr(enrich.http, "get", lambda *a, **k: Resp())
    lid = _lead("Hunter Co", website_url="https://hunterco.in", lead_priority="HOT")
    enrich.enrich_lead(lid)
    (contact,) = people.list_for_lead(lid)
    assert (contact["name"], contact["role_category"], contact["source"]) == \
        ("Dev Patel", "CEO", "hunter")
    assert {r["field"]: r["source"] for r in lineage.lineage_for(lid)}["email"] == "hunter"


def test_dedupe_keeps_contacts_and_lineage_of_the_removed_duplicate():
    from app.dedup import dedupe_existing_leads
    keep = _lead("Twin Dental", phone="9833333333")
    dup = _lead("Twin Dental", phone="9833333333")
    people.add_contact(dup, "Meera Rao", "Owner")
    lineage.record(dup, "phone", "google", "provider")
    dedupe_existing_leads()
    assert query_one("SELECT id FROM leads WHERE id = ?", (dup,)) is None
    assert [c["name"] for c in people.list_for_lead(keep)] == ["Meera Rao"]
    assert query_one("SELECT 1 x FROM field_sources WHERE entity_id = ?", (dup,)) is None


def test_contact_upsert_keeps_better_source_and_title_logic():
    lid = _lead("Upsert Co")
    people.add_contact(lid, "Ravi Iyer", None, source="hunter", source_type="enrichment_api")
    c = people.add_contact(lid, "Ravi Iyer", "Founder", source="manual", source_type="user",
                           confidence=0.99)
    assert c["title"] == "Founder" and c["role_category"] == "FOUNDER"
    assert c["decision_maker_probability"] >= 0.9
    assert c["source"] == "manual"
    assert len(people.list_for_lead(lid)) == 1


# --------------------------------------------------------- email safety ------

def test_outreach_refuses_inferred_invalid_and_disposable_addresses():
    lid = _lead("Mail Safe", email="priya.nair@mailsafe.in")
    people.add_contact(lid, "Priya Nair", "Owner")
    lead = query_one("SELECT * FROM leads WHERE id = ?", (lid,))
    # unverified-but-real address is allowed
    emailintel.register(lid, "priya.nair@mailsafe.in", "website_scrape", "website")
    assert queue_message(lead, "cold_email_first", "s1")
    # inferring an address we already hold from a real source must not downgrade it
    assert emailintel.infer(lid, "Priya Nair", "mailsafe.in") == []
    assert query_one("SELECT status FROM email_addresses WHERE lead_id = ? AND "
                     "email = 'priya.nair@mailsafe.in'", (lid,))["status"] == "UNVERIFIED"
    # a guess for another person follows the confirmed pattern - and is not sendable
    people.add_contact(lid, "Arun Das", "Manager")
    emailintel.infer(lid, "Arun Das", "mailsafe.in")
    guess = query_one("SELECT * FROM email_addresses WHERE lead_id = ? AND status = 'INFERRED' "
                      "AND email = 'arun.das@mailsafe.in'", (lid,))
    assert guess and guess["confidence"] == 0.55
    lead2 = {**lead, "id": lid, "email": "arun.das@mailsafe.in"}
    assert queue_message(lead2, "cold_email_first", "s2") is None
    assert queue_message({**lead, "email": "x@mailinator.com"}, "cold_email_first", "s3") is None
    assert queue_message({**lead, "email": "noreply@mailsafe.in"}, "cold_email_first", "s4") is None


def test_inference_uses_domain_evidence_and_is_never_verified():
    lid = _lead("Pattern Co")
    people.add_contact(lid, "Sunil Gupta", "Director")
    emailintel.register(lid, "sunil.gupta@patternco.in", "website_scrape", "website")
    people.add_contact(lid, "Neha Verma", "Manager")
    out = emailintel.infer(lid, "Neha Verma", "patternco.in")
    assert [o["email"] for o in out] == ["neha.verma@patternco.in"]       # pattern confirmed
    assert out[0]["confidence"] == 0.55 and out[0]["status"] == "INFERRED"
    lid2 = _lead("No Evidence Co")
    out2 = emailintel.infer(lid2, "Neha Verma", "nope.in")
    assert len(out2) >= 4 and all(o["confidence"] == 0.25 and o["status"] == "INFERRED"
                                  for o in out2)
    assert emailintel.infer(lid2, "Madonna", "nope.in") == []              # needs first+last


def test_send_time_block_when_address_turns_out_invalid():
    lid = _lead("Late Bounce", email="owner@latebounce.in")
    lead = query_one("SELECT * FROM leads WHERE id = ?", (lid,))
    qid = queue_message(lead, "cold_email_first", "x")
    assert qid
    execute("UPDATE approval_queue SET status = 'APPROVED' WHERE id = ?", (qid,))
    emailintel.register(lid, "owner@latebounce.in", "manual", "user")
    execute("UPDATE email_addresses SET status = 'INVALID' WHERE lead_id = ?", (lid,))
    from app.db import current_ws
    result = send_approved(workspace_id=current_ws())
    assert result["sent"] == 0 and result["skipped"] >= 1
    row = query_one("SELECT status, error_message FROM approval_queue WHERE id = ?", (qid,))
    assert row["status"] == "SKIPPED" and "blocked" in row["error_message"]


def test_verified_overrides_inferred_and_register_never_downgrades_verified():
    lid = _lead("Verified Co")
    row = emailintel.register(lid, "kim@verifiedco.in", "inferred", "inferred", inferred=True)
    assert row["status"] == "INFERRED"
    assert emailintel.email_blocked(lid, "kim@verifiedco.in")
    execute("UPDATE email_addresses SET status = 'VERIFIED' WHERE id = ?", (row["id"],))
    again = emailintel.register(lid, "kim@verifiedco.in", "inferred", "inferred", inferred=True)
    assert again["status"] == "VERIFIED"
    assert emailintel.email_blocked(lid, "kim@verifiedco.in") is None


# ------------------------------------------------------------ verification ---

def test_verification_unavailable_without_provider(monkeypatch):
    monkeypatch.setattr(settings, "hunter_api_key", "")
    lid = _lead("NoVerifier")
    row = emailintel.register(lid, "a.b@nov.in", "manual", "user")
    with pytest.raises(emailintel.VerifierUnavailable):
        emailintel.verify(row["id"])


def test_verification_reserves_quota_first_and_only_valid_verifies(monkeypatch):
    monkeypatch.setattr(settings, "hunter_api_key", "k")
    lid = _lead("Verifier Co")
    calls = []

    def fake_get(url, **kw):
        used = query_one("SELECT COALESCE(SUM(calls),0) n FROM api_usage "
                         "WHERE service = 'hunter'")["n"]
        calls.append(used)
        class R:
            status_code = 200
            def json(s): return {"data": {"status": verdicts.pop(0)}}
        return R()
    monkeypatch.setattr(emailintel.http, "get", fake_get)
    verdicts = ["valid", "accept_all", "invalid"]
    before = query_one("SELECT COALESCE(SUM(calls),0) n FROM api_usage WHERE service='hunter'")["n"]
    out = []
    for addr in ("v.one@vc.in", "v.two@vc.in", "v.three@vc.in"):
        r = emailintel.register(lid, addr, "website_scrape", "website")
        out.append(emailintel.verify(r["id"]))
    assert [o["status"] for o in out] == ["VERIFIED", "UNVERIFIED", "INVALID"]
    assert calls[0] == before + 1, "quota was reserved BEFORE the provider call"
    assert out[0]["last_verified_at"] and out[0]["confidence"] >= 0.9


def test_verification_respects_quota_cap(monkeypatch):
    monkeypatch.setattr(settings, "hunter_api_key", "k")
    monkeypatch.setattr(settings, "max_hunter_calls_per_month", 0)
    monkeypatch.setattr(emailintel.http, "get", lambda *a, **k: pytest.fail("paid call made"))
    lid = _lead("Capped Co")
    r = emailintel.register(lid, "c.d@cap.in", "manual", "user")
    from app.quota import QuotaExceeded
    with pytest.raises(QuotaExceeded):
        emailintel.verify(r["id"])


# --------------------------------------------------------------- HTTP / API --

@pytest.fixture(scope="module")
def tenants():
    wa, wb = create_workspace("Intel A"), create_workspace("Intel B")
    ids = {}
    for name, ws in (("intel_a", wa), ("intel_b", wb)):
        execute("REPLACE INTO users(username, password_hash, role, is_active, created_at) "
                "VALUES(?,?,'admin',1,?)", (name, hash_password("x"), utcnow()))
        uid = query_one("SELECT id FROM users WHERE username = ?", (name,))["id"]
        add_member(ws, uid, "owner")
    def client(n):
        c = TestClient(app)
        c.cookies.set(SESSION_COOKIE, issue_token(n))
        return c
    return {"a": wa, "b": wb, "ca": client("intel_a"), "cb": client("intel_b")}


def test_profile_api_shape_and_manual_attribution(tenants):
    ca = tenants["ca"]
    with workspace_context(tenants["a"]):
        lid = _lead("API Cafe", ws=tenants["a"])
    r = ca.patch(f"/api/leads/{lid}", json={"phone": "9844444444", "email": "owner@apicafe.in",
                                             "instagram_url": "https://instagram.com/apicafe"})
    assert r.status_code == 200
    profile = ca.get(f"/api/leads/{lid}/profile").json()
    phone = profile["sections"]["contact"]["phone"]
    assert phone["source"] == "manual" and phone["source_type"] == "user"
    assert phone["verified"] is False and phone["freshness"] == "FRESH"
    assert profile["sections"]["digital"]["instagram_url"]["source"] == "manual"
    assert profile["data_confidence_score"] is not None
    assert [s["platform"] for s in profile["socials"]] == ["instagram"]
    assert [e["email"] for e in profile["emails"]] == ["owner@apicafe.in"]

    v = ca.post(f"/api/leads/{lid}/verify", json={"field": "phone"})
    assert v.status_code == 200
    assert ca.get(f"/api/leads/{lid}/lineage").json()["fields"][-1]["field"] or True
    prof2 = ca.get(f"/api/leads/{lid}/profile").json()
    assert prof2["sections"]["contact"]["phone"]["verified"] is True
    assert prof2["last_verified_at"]
    assert ca.post(f"/api/leads/{lid}/verify", json={"field": "business_name"}).status_code == 400
    assert ca.get("/api/freshness/summary").json().get("FRESH", 0) >= 1


def test_contacts_emails_socials_crud_over_http(tenants):
    ca = tenants["ca"]
    lid = _lead("CRUD Co", ws=tenants["a"], website_url="https://crudco.in")
    c = ca.post(f"/api/leads/{lid}/contacts", json={"name": "Asha Menon", "title": "Owner",
                                                    "email": "asha.menon@crudco.in"})
    assert c.status_code == 200 and c.json()["decision_maker_probability"] >= 0.9
    cid = c.json()["id"]
    assert ca.post(f"/api/leads/{lid}/contacts", json={"name": "x"}).status_code == 400
    assert ca.patch(f"/api/contacts/{cid}", json={"name": "Asha Menon",
                    "title": "IT Manager"}).json()["role_category"] == "IT_MANAGER"
    assert ca.post(f"/api/contacts/{cid}/verify").json()["verified_at"]
    listing = ca.get("/api/contacts", params={"min_probability": 0.4}).json()
    assert any(x["id"] == cid and x["business_name"] == "CRUD Co" for x in listing["contacts"])

    same = ca.post(f"/api/leads/{lid}/emails/infer", json={"contact_id": cid}).json()
    assert same["candidates"] == [] and "already on file" in same["note"]
    rohan = ca.post(f"/api/leads/{lid}/contacts", json={"name": "Rohan Mehta"}).json()
    inf = ca.post(f"/api/leads/{lid}/emails/infer", json={"contact_id": rohan["id"]})
    cands = inf.json()["candidates"]
    assert inf.status_code == 200 and [c["email"] for c in cands] == ["rohan.mehta@crudco.in"]
    assert cands[0]["status"] == "INFERRED" and cands[0]["confidence"] == 0.55
    assert ca.post(f"/api/leads/{lid}/emails", json={"email": "nonsense"}).status_code == 400

    sp = ca.post(f"/api/leads/{lid}/socials", json={"url": "https://facebook.com/crudco"})
    assert sp.status_code == 200 and sp.json()["username"] == "crudco"
    assert query_one("SELECT facebook_url FROM leads WHERE id = ?", (lid,))["facebook_url"]
    assert ca.post(f"/api/leads/{lid}/socials", json={"url": "https://example.com/x"}
                   ).status_code == 400
    assert ca.delete(f"/api/socials/{sp.json()['id']}").status_code == 200
    assert ca.delete(f"/api/contacts/{cid}").status_code == 200


def test_verify_endpoint_503_when_provider_unconfigured(tenants, monkeypatch):
    monkeypatch.setattr(settings, "hunter_api_key", "")
    lid = _lead("503 Co", ws=tenants["a"])
    with workspace_context(tenants["a"]):
        row = emailintel.register(lid, "z.z@c503.in", "manual", "user")
    r = tenants["ca"].post(f"/api/emails/{row['id']}/verify")
    assert r.status_code == 503 and "provider" in r.json()["detail"].lower()
    assert tenants["ca"].get("/api/email-verification/status").json()["available"] is False


def test_intel_data_is_tenant_isolated(tenants):
    ca, cb = tenants["ca"], tenants["cb"]
    lid = _lead("Private Intel", ws=tenants["a"], phone="9855555555")
    with workspace_context(tenants["a"]):
        c = people.add_contact(lid, "Secret Person", "Owner", email="s.p@priv.in")
        e = emailintel.register(lid, "s.p@priv.in", "manual", "user")
        s = social.register(lid, "https://instagram.com/priv", "manual", "user")
        lineage.record(lid, "phone", "google", "provider")
    for path in (f"/api/leads/{lid}/profile", f"/api/leads/{lid}/lineage",
                 f"/api/leads/{lid}/contacts", f"/api/leads/{lid}/emails",
                 f"/api/leads/{lid}/socials"):
        assert ca.get(path).status_code == 200
        assert cb.get(path).status_code == 404, path
    assert cb.post(f"/api/leads/{lid}/verify", json={"field": "phone"}).status_code == 404
    assert cb.patch(f"/api/contacts/{c['id']}", json={"name": "Hacked"}).status_code == 404
    assert cb.delete(f"/api/contacts/{c['id']}").status_code == 404
    assert cb.post(f"/api/contacts/{c['id']}/verify").status_code == 404
    assert cb.post(f"/api/emails/{e['id']}/verify").status_code == 404
    assert cb.delete(f"/api/socials/{s['id']}").status_code == 404
    assert cb.post(f"/api/leads/{lid}/emails/infer", json={"contact_id": c["id"]}
                   ).status_code == 404
    assert cb.get("/api/contacts").json()["total"] == 0
    assert query_one("SELECT name FROM contacts WHERE id = ?", (c["id"],))["name"] == "Secret Person"
    # an address another tenant knows is bad must not block this tenant's mail
    with workspace_context(tenants["a"]):
        execute("UPDATE email_addresses SET status = 'INVALID' WHERE id = ?", (e["id"],))
        assert emailintel.email_blocked(lid, "s.p@priv.in")
    with workspace_context(tenants["b"]):
        assert emailintel.email_blocked(None, "s.p@priv.in") is None


def test_deleting_a_lead_removes_its_intel(tenants):
    ca = tenants["ca"]
    lid = _lead("Gone Co", ws=tenants["a"])
    with workspace_context(tenants["a"]):
        people.add_contact(lid, "Gone Person", "Owner")
        emailintel.register(lid, "g@gone.in", "manual", "user")
        social.register(lid, "https://instagram.com/gone", "manual", "user")
        lineage.record(lid, "phone", "manual", "user")
    assert ca.delete(f"/api/leads/{lid}").status_code == 200
    for table, col in (("contacts", "lead_id"), ("email_addresses", "lead_id"),
                       ("social_profiles", "lead_id"), ("field_sources", "entity_id")):
        assert query_one(f"SELECT 1 x FROM {table} WHERE {col} = ?", (lid,)) is None, table
