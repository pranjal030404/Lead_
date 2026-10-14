"""Phase 5: multi-score model, intent signals, competitor comparison."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import competitors, emailintel, enrich, leadscore, opportunities, people, signals, website
from app.config import settings
from app.db import current_ws, execute, query, query_one, utcnow, workspace_context
from app.main import app
from app.outreach import suppress
from app.security import SESSION_COOKIE, hash_password, issue_token
from app.tenancy import add_member, create_workspace


def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def _lead(name="Score Cafe", ws=None, **extra):
    cols = {"workspace_id": ws or current_ws(), "business_name": name, "city": "Delhi",
            "niche": "dentists", "lead_score": 6, "lead_priority": "WARM", "status": "NEW",
            "data_quality_score": 6, "website_url": "https://score.in",
            "created_at": utcnow(), "updated_at": utcnow(), **extra}
    return execute(f"INSERT INTO leads({', '.join(cols)}) VALUES({', '.join('?' for _ in cols)})",
                   tuple(cols.values()))


def _audit(lid, overall=70, status="OK", ws=None, fingerprint="fp1", title="T", checks=None,
           techs=None, features=None, created=None, error=None, **cats):
    findings = {"checks": checks or [], "technologies": techs or [], "features": features or {},
                "fingerprint": fingerprint, "title": title} if status == "OK" else None
    return execute(
        """INSERT INTO website_audits(workspace_id, lead_id, url, status, audit_version,
           overall_score, technical_score, seo_score, performance_score, conversion_score,
           content_score, findings, error, created_at)
           VALUES(?,?,?,?,1,?,?,?,?,?,?,?,?,?)""",
        (ws or current_ws(), lid, "https://score.in", status, overall if status == "OK" else None,
         cats.get("technical", overall), cats.get("seo", overall), cats.get("performance", overall),
         cats.get("conversion", overall), cats.get("content", overall),
         json.dumps(findings) if findings else None, error, created or utcnow()))


def chk(cid, status, detail="d", title=None):
    return {"id": cid, "status": status, "detail": detail, "title": title or cid,
            "category": "x", "weight": 1, "evidence": {}}


def base_ctx(**over):
    ctx = {"lead": {"lead_score": 6, "lead_priority": "WARM", "data_quality_score": 6,
                    "website_url": "https://x.in", "phone": "9811111111", "phone_valid": 1},
           "audit": None, "features": {}, "emails": [], "suppressed": {}, "socials": [],
           "contacts": [], "open_opportunities": [], "opportunity_value": 0, "signals": [],
           "evaluated": True}
    ctx.update(over)
    return ctx


def sig(t="WEBSITE_BROKEN", conf=0.8, status="ACTIVE", detected=None):
    return {"signal_type": t, "confidence": conf, "status": status,
            "detected_at": detected or utcnow(), "label": t}


# ------------------------------------------------------------ components ----

def test_lead_quality_blends_rule_and_data_scores_and_keeps_rule_score_visible():
    c = leadscore.lead_quality(base_ctx())
    assert c["score"] == round(0.7 * 60 + 0.3 * 60) and c["inputs"]["rule_score"] == 6
    assert leadscore.lead_quality(base_ctx(lead={}))["available"] is False


def test_digital_gap_branches():
    gap = lambda **kw: leadscore.digital_gap(base_ctx(**kw))
    assert (gap(lead={"website_url": ""})["score"], gap(lead={"website_url": ""})["confidence"]) == (95, 0.6)
    assert gap(audit={"status": "UNREACHABLE", "error": "refused"})["score"] == 90
    ok = gap(audit={"status": "OK", "overall_score": 38, "technical_score": 80, "seo_score": 20,
                    "performance_score": 90, "conversion_score": 10, "content_score": 60})
    assert ok["score"] == 62 and ok["confidence"] == 0.9 and "conversion 10" in ok["reasons"][1]
    basic = gap(lead={"website_url": "https://x", "website_score": 7, "website_checked_at": "x"})
    assert basic["score"] == 30 and basic["confidence"] == 0.5
    assert gap(lead={"website_url": "https://x"})["available"] is False
    dead = gap(lead={"website_url": "https://x", "has_website": 0, "website_checked_at": "x"})
    assert dead["score"] == 88


def test_contactability_points_and_blockers():
    e = lambda addr, status="UNVERIFIED", typ="PERSONAL": {"email": addr, "status": status, "email_type": typ}
    full = base_ctx(lead={"phone": "9", "phone_valid": 1, "whatsapp": "91", "address": "x"},
                    emails=[e("a@b.in", "VERIFIED")], features={"contact_form": True},
                    socials=[{"platform": "instagram"}])
    assert leadscore.contactability(full)["score"] == 100
    pts = lambda **kw: leadscore.contactability(base_ctx(**kw))["score"]
    assert pts(lead={"phone": "9", "phone_valid": 1}) == 30
    assert pts(lead={"phone": "9", "phone_valid": 0}) == 10
    assert pts(lead={}, emails=[e("p@x.in")]) == 28
    assert pts(lead={}, emails=[e("m@x.in", typ="ROLE")]) == 22
    assert pts(lead={}, emails=[e("info@x.in", typ="GENERIC")]) == 20
    assert pts(lead={}, emails=[e("bad@x.in", "INVALID"), e("g@x.in", "INFERRED"),
                                e("d@x.in", "DISPOSABLE")]) == 0
    assert pts(lead={}, emails=[e("s@x.in")], suppressed={"s@x.in": True}) == 0
    assert pts(lead={"phone": "9", "phone_valid": 1, "whatsapp": "91"},
               suppressed={"phone": True}) == 0
    best = pts(lead={}, emails=[e("info@x.in", typ="GENERIC"), e("v@x.in", "VERIFIED")])
    assert best == 35, "only the single best email counts"


def test_business_value_curve_is_monotonic_and_rating_scales_it():
    bv = lambda reviews, rating=4.5, value=0: leadscore.business_value(base_ctx(
        lead={"google_reviews": reviews, "google_rating": rating}, opportunity_value=value))["score"]
    scores = [bv(r) for r in (5, 20, 100, 500, 2000)]
    assert scores == sorted(scores) and scores[0] < 40 and scores[-1] > 80
    assert bv(100, 5.0) > bv(100, 4.0) > bv(100, 3.0)
    assert bv(None, value=150_000) == 100 and bv(100, value=75_000) == round(0.6 * bv(100) + 0.4 * 50)
    assert leadscore.business_value(base_ctx(lead={}))["available"] is False


def test_buying_intent_combination_decay_and_not_evaluated():
    assert leadscore.buying_intent(base_ctx(evaluated=False))["available"] is False
    none = leadscore.buying_intent(base_ctx())
    assert none["score"] == 0 and "not proof" in none["reasons"][0]
    one = leadscore.buying_intent(base_ctx(signals=[sig("WEBSITE_BROKEN", 1.0)]))
    assert one["score"] == 90
    two = leadscore.buying_intent(base_ctx(signals=[sig("WEBSITE_BROKEN", 1.0), sig("RUNNING_ADS", 1.0)]))
    assert two["score"] == round(100 * (1 - 0.1 * 0.3)) and two["score"] > one["score"] <= 100
    inactive = leadscore.buying_intent(base_ctx(signals=[sig(status="INACTIVE")]))
    assert inactive["score"] == 0
    fresh = leadscore.buying_intent(base_ctx(signals=[sig("WEBSITE_CHANGED", 1.0, detected=utcnow())]))
    half = leadscore.buying_intent(base_ctx(signals=[sig("WEBSITE_CHANGED", 1.0, detected=_ago(22.5))]))
    gone = leadscore.buying_intent(base_ctx(signals=[sig("WEBSITE_CHANGED", 1.0, detected=_ago(60))]))
    assert fresh["score"] == 50 and 24 <= half["score"] <= 26 and gone["score"] == 0


def test_decision_maker_and_outreach_fit_rules():
    dm = lambda **kw: leadscore.decision_maker(base_ctx(**kw))
    c = {"name": "A B", "title": "Owner", "role_category": "OWNER", "confidence": 0.9,
         "decision_maker_probability": 0.95, "email": "a@b.in", "phone": None}
    assert dm(contacts=[c])["score"] == 100                 # 95 + 10 capped
    assert dm(contacts=[{**c, "email": None}])["score"] == 95
    assert dm(lead={"owner_name": "Raj"})["score"] == 35
    assert dm(lead={})["score"] == 0

    opp = {"opportunity_type": "NO_WEBSITE", "severity": "HIGH", "est_value_max": 9,
           "evidence": ["a", "b"]}
    chan = {"available": True, "score": 50}
    of = lambda **kw: leadscore.outreach_fit({**base_ctx(**kw), "contactability": kw.get("c", chan)})
    assert of(open_opportunities=[opp])["score"] == 50 + 10 + 40
    assert of(open_opportunities=[opp], c={"available": True, "score": 10})["score"] == 20
    assert of(open_opportunities=[])["score"] == 40
    assert of(open_opportunities=[opp], suppressed={"all": True})["score"] == 0


def test_combine_renormalises_over_available_components_and_reports_confidence():
    comps = {k: {"score": 80, "confidence": 0.9, "available": True} for k in leadscore.DEFAULT_WEIGHTS}
    full = leadscore.combine(comps, leadscore.DEFAULT_WEIGHTS, 90)
    assert full["overall"] == 80 and full["coverage"] == 1.0 and full["tier"] == "HOT"
    assert full["confidence"] == round(100 * (0.4 * 1 + 0.3 * 0.9 + 0.3 * 0.9))
    comps["digital_gap"] = {"score": None, "confidence": 0, "available": False}
    comps["buying_intent"] = {"score": None, "confidence": 0, "available": False}
    part = leadscore.combine(comps, leadscore.DEFAULT_WEIGHTS, None)
    assert part["overall"] == 80, "missing components are excluded, not scored zero"
    assert part["coverage"] == 0.6 and part["confidence"] < full["confidence"]
    assert abs(sum(part["contributions"].values()) - part["overall"]) < 1
    assert leadscore.combine({k: {"score": None, "confidence": 0, "available": False}
                              for k in comps}, leadscore.DEFAULT_WEIGHTS, None)["overall"] is None
    assert [leadscore.tier_for(x) for x in (100, 75, 74, 55, 54, 0, None)] == \
        ["HOT", "HOT", "WARM", "WARM", "COLD", "COLD", None]


def test_compute_explains_every_component_and_is_deterministic():
    ctx = base_ctx(lead={"lead_score": 8, "lead_priority": "HOT", "data_quality_score": 7,
                         "website_url": "", "phone": "9", "phone_valid": 1,
                         "data_confidence_score": 80, "score_reasons": '["a"]'})
    r1, r2 = leadscore.compute(ctx), leadscore.compute(ctx)
    assert r1 == r2
    assert set(r1["components"]) == set(leadscore.COMPONENTS)
    for name, c in r1["components"].items():
        assert c["reasons"], name
    assert r1["components"]["data_confidence"]["contribution"] is None   # informs confidence only
    assert r1["rule_score"] == {"score": 8, "priority": "HOT", "reasons": ["a"]}
    assert r1["components"]["digital_gap"]["score"] == 95
    assert "confidence_formula" in r1


def test_weights_validation_and_effect():
    assert leadscore.get_weights() == leadscore.DEFAULT_WEIGHTS
    w = leadscore.set_weights({"digital_gap": 1.0, "lead_quality": 0.0})
    assert w["digital_gap"] == 1.0 and w["lead_quality"] == 0.0 and w["buying_intent"] == 0.15
    for bad in ({"nope": 1}, {"digital_gap": -1}, {"digital_gap": "x"}, {"digital_gap": True}):
        with pytest.raises(ValueError):
            leadscore.set_weights(bad)
    with pytest.raises(ValueError):
        leadscore.set_weights({k: 0 for k in leadscore.DEFAULT_WEIGHTS})
    only_gap = leadscore.compute(base_ctx(lead={"website_url": ""}), {"digital_gap": 1.0})
    assert only_gap["overall"] == 95
    leadscore.set_weights({k: v for k, v in leadscore.DEFAULT_WEIGHTS.items()})   # restore


# ----------------------------------------------------------------- signals ---

def test_signal_detection_from_audits_and_lead_data():
    lid = _lead("Sig Co", google_rating=4.6, google_reviews=80)
    _audit(lid, 40, fingerprint="aaa", title="Old",
           checks=[chk("copyright_year", "FAIL", "Footer copyright says 2019."),
                   chk("mobile_viewport", "FAIL", "No viewport")],
           techs=[{"name": "Meta Pixel", "category": "advertising", "evidence": "html: fbq("}],
           created=_ago(30))
    _audit(lid, 40, fingerprint="bbb", title="New",
           checks=[chk("copyright_year", "FAIL", "Footer copyright says 2019."),
                   chk("mobile_viewport", "FAIL", "No viewport")],
           techs=[{"name": "Meta Pixel", "category": "advertising", "evidence": "html: fbq("}],
           created=_ago(2))
    found = signals.detect(lid)
    assert {"WEBSITE_OUTDATED", "RUNNING_ADS", "WEBSITE_CHANGED",
            "STRONG_REPUTATION_POOR_SITE"} <= set(found)
    assert any("Title changed" in e for e in found["WEBSITE_CHANGED"]["evidence"])
    assert found["WEBSITE_CHANGED"]["detected_at"] == query_one(
        "SELECT created_at FROM website_audits WHERE lead_id = ? ORDER BY id DESC LIMIT 1", (lid,))["created_at"]
    assert any("not that a campaign is live" in e for e in found["RUNNING_ADS"]["evidence"])
    assert "4.6 stars from 80" in found["STRONG_REPUTATION_POOR_SITE"]["evidence"][0]


def test_unchanged_content_and_broken_sites():
    lid = _lead("Same Co")
    _audit(lid, 80, fingerprint="same", created=_ago(20))
    _audit(lid, 80, fingerprint="same", created=_ago(1))
    assert "WEBSITE_CHANGED" not in signals.detect(lid)
    broken = _lead("Broken Co")
    _audit(broken, status="UNREACHABLE", error="refused")
    assert signals.detect(broken)["WEBSITE_BROKEN"]["confidence"] == 0.85
    dead = _lead("Dead Co", has_website=0, website_checked_at=utcnow())
    assert signals.detect(dead)["WEBSITE_BROKEN"]["confidence"] == 0.7
    assert signals.detect(_lead("Healthy", website_url="https://h.in")) == {}


def test_signal_lifecycle_state_keeps_first_seen_event_decays_and_deactivates():
    lid = _lead("Life Co")
    a = _audit(lid, status="UNREACHABLE", error="down")
    signals.refresh_for_lead(lid)
    first = {s["signal_type"]: s for s in signals.list_for_lead(lid)}["WEBSITE_BROKEN"]
    assert first["status"] == "ACTIVE" and first["expires_at"] is None
    execute("UPDATE lead_signals SET detected_at = ? WHERE id = ?", (_ago(10), first["id"]))
    signals.refresh_for_lead(lid)
    again = signals.list_for_lead(lid)[0]
    assert again["detected_at"][:10] == _ago(10)[:10], "a state signal keeps its first-seen time"
    execute("DELETE FROM website_audits WHERE id = ?", (a,))
    _audit(lid, 90)                                       # site is fixed
    signals.refresh_for_lead(lid)
    assert {s["signal_type"]: s["status"] for s in signals.list_for_lead(lid)}["WEBSITE_BROKEN"] == "INACTIVE"

    ev = _lead("Event Co")
    _audit(ev, 70, fingerprint="a", created=_ago(70))
    _audit(ev, 70, fingerprint="b", created=_ago(60))     # change happened 60 days ago (> 45d ttl)
    signals.refresh_for_lead(ev)
    (s,) = [x for x in signals.list_for_lead(ev) if x["signal_type"] == "WEBSITE_CHANGED"]
    assert s["status"] == "INACTIVE" and s["effective_strength"] == 0.0
    assert signals.UNAVAILABLE_SIGNALS["HIRING_DEVELOPER"]


def test_niche_gated_feature_signals_follow_open_opportunities():
    lid = _lead("Gated Co", niche="restaurants")
    _audit(lid, 80, features={"online_ordering": False},
           checks=[chk("online_ordering", "WARN", "No online ordering detected."),
                   chk("booking", "WARN", "No booking")])
    opportunities.refresh_for_lead(lid)
    found = signals.detect(lid)
    assert "NO_ONLINE_ORDERING" in found and "NO_BOOKING_SYSTEM" not in found


# -------------------------------------------------------------- competitors --

def _comp_set(n=3, niche="florists", city="Competown", scores=(80, 70, 60), **extra):
    ids = []
    for i in range(n):
        lid = _lead(f"{niche}-{i}-{city}", niche=niche, city=city, website_url=f"https://c{i}.in",
                    google_rating=4.0 + i / 10, google_reviews=50 * (i + 1), **extra)
        if scores[i] is not None:
            _audit(lid, scores[i])
        ids.append(lid)
    return ids


def test_competitor_selection_scope_distance_and_pinning():
    comp = _comp_set(2, "optometrists", "Nearby", scores=(70, 60), lat=28.60, lng=77.20)
    target = _lead("Target Opt", niche="optometrists", city="Elsewhere", lat=28.61, lng=77.21)
    far = _lead("Far Opt", niche="optometrists", city="Other", lat=19.07, lng=72.87)   # Mumbai
    other_niche = _lead("Not Opt", niche="bakers", city="Elsewhere", lat=28.61, lng=77.21)
    _, found = competitors.candidates(target)
    ids = [c["lead_id"] for c in found]
    assert set(comp) <= set(ids) and far not in ids and other_niche not in ids and target not in ids
    assert all(c["distance_km"] is not None and c["distance_km"] < 5 for c in found)
    assert found[0]["distance_km"] <= found[-1]["distance_km"]
    assert competitors.pin(target, far) is True
    _, again = competitors.candidates(target)
    assert again[0]["lead_id"] == far and again[0]["source"] == "pinned"
    assert competitors.pin(target, target) is False and competitors.pin(target, 999999) is False
    assert competitors.unpin(target, far) and not competitors.unpin(target, far)


def test_competitive_gap_statements_and_insufficient_data():
    comps = _comp_set(3, "tailors", "Gapville", scores=(81, 76, 69))
    target = _lead("Weak Tailor", niche="tailors", city="Gapville", google_rating=4.4, google_reviews=12)
    assert competitors.competitive_gap(target)["statements"][0].startswith("The target's own website")
    _audit(target, 42)
    gap = competitors.competitive_gap(target)
    assert gap["sufficient_data"] and gap["gap"] == {
        "target_score": 42, "best": 81, "median": 76, "rank": 4, "of": 4,
        "deficit_vs_best": 39, "deficit_vs_median": 34}
    assert "Website score 42" in gap["statements"][0] and "tailors-0-Gapville: 81" in gap["statements"][0]
    assert "Ranks 4 of 4" in gap["statements"][1] and "12 (rating 4.4)" in gap["statements"][2]

    lone = _lead("Lone Mover", niche="movers", city="Alone")
    _comp_set(1, "movers", "Alone", scores=(60,))
    ins = competitors.competitive_gap(lone)
    assert not ins["sufficient_data"] and "Insufficient data" in ins["statements"][0] and ins["gap"] is None


def test_no_website_target_is_scored_zero_against_competitors():
    _comp_set(2, "chemists", "Zeroville", scores=(60, 50))
    target = _lead("No Site Chemist", niche="chemists", city="Zeroville", website_url=None)
    gap = competitors.competitive_gap(target)
    assert gap["gap"]["target_score"] == 0 and "No website (scored 0)" in gap["statements"][0]


def test_competitor_advantage_signal_needs_real_deficit_and_enough_data():
    _comp_set(3, "jewellers", "Sigtown", scores=(85, 80, 75))
    weak = _lead("Weak Jeweller", niche="jewellers", city="Sigtown")
    _audit(weak, 40)
    assert signals.detect(weak)["COMPETITOR_ADVANTAGE"]["confidence"] == 0.8
    close = _lead("Close Jeweller", niche="jewellers", city="Sigtown")
    _audit(close, 78)
    assert "COMPETITOR_ADVANTAGE" not in signals.detect(close)


# ------------------------------------------------------------- integration ---

def test_recompute_persists_scores_explanation_history_and_leaves_rule_score_alone():
    lid = _lead("Persist Co", website_url=None, phone="9811111111", phone_valid=1, lead_score=7)
    result = leadscore.recompute(lid)
    assert result["overall"] is not None and result["tier"] in ("HOT", "WARM", "COLD")
    ex = result["explanation"]
    assert ex["components"]["digital_gap"]["score"] == 95 and ex["rule_score"]["score"] == 7
    assert result["digital_gap"] == 95 and result["rule_score"] == 7
    lead = query_one("SELECT lead_score, overall_score, opportunity_tier, score_confidence, "
                     "scored_at FROM leads WHERE id = ?", (lid,))
    assert lead["lead_score"] == 7, "the deterministic rule score is never modified"
    assert (lead["overall_score"], lead["opportunity_tier"]) == (result["overall"], result["tier"])
    assert sum(v["contribution"] or 0 for v in ex["components"].values()) == pytest.approx(
        result["overall"], abs=1.0)
    n = len(leadscore.history(lid))
    leadscore.recompute(lid)
    assert len(leadscore.history(lid)) == n, "history grows only when the score changes"
    execute("UPDATE leads SET phone = NULL, phone_valid = 0 WHERE id = ?", (lid,))
    leadscore.recompute(lid)
    assert len(leadscore.history(lid)) == n + 1


def test_suppression_zeroes_contactability_and_outreach_fit():
    lid = _lead("Suppressed Co", website_url=None, phone=None, email="only@sup.in")
    before = leadscore.recompute(lid)["explanation"]["components"]
    assert before["contactability"]["score"] > 0
    suppress("only@sup.in", "email", "opt-out")
    after = leadscore.recompute(lid)["explanation"]["components"]
    assert after["contactability"]["score"] == 0 and after["outreach_fit"]["score"] == 0
    assert "suppressed" in " ".join(after["outreach_fit"]["reasons"]).lower()


def test_enrichment_pipeline_ends_with_scores_and_signals(monkeypatch):
    monkeypatch.setattr(website, "check_website", lambda url: {
        "website_exists": 1, "website_ssl": 1, "website_mobile_friendly": 1, "website_speed_ms": 400,
        "website_platform": None, "website_last_updated": "2026", "website_score": 8,
        "website_issues": [], "socials": {}, "emails": ["owner.one@pipe.in"],
        "people": [{"name": "Owen One", "title": "Owner"}], "checked_at": utcnow()})
    lid = _lead("Pipeline Score Co", website_url="https://pipe.in", has_website=1, lead_score=3)
    enrich.enrich_lead(lid)
    row = leadscore.get_scores(lid)
    assert row and row["overall"] is not None and row["decision_maker"] >= 90
    assert row["contactability"] > 0 and row["data_confidence"] is not None
    assert query_one("SELECT overall_score FROM leads WHERE id = ?", (lid,))["overall_score"] == row["overall"]


def test_scheduled_sweep_scores_unscored_leads():
    lid = _lead("Sweep Co", website_url=None)
    assert query_one("SELECT overall_score FROM leads WHERE id = ?", (lid,))["overall_score"] is None
    from app import automation
    automation.job_scoring(force=True, workspace_id=current_ws())
    assert query_one("SELECT overall_score FROM leads WHERE id = ?", (lid,))["overall_score"] is not None


# ---------------------------------------------------------------- HTTP API ---

@pytest.fixture(scope="module")
def tenants():
    wa, wb = create_workspace("Score A"), create_workspace("Score B")
    clients = {}
    for name, ws, role in (("sc_a", wa, "admin"), ("sc_b", wb, "admin"), ("sc_user", wa, "user")):
        execute("REPLACE INTO users(username, password_hash, role, is_active, created_at) "
                "VALUES(?,?,?,1,?)", (name, hash_password("x"), role, utcnow()))
        uid = query_one("SELECT id FROM users WHERE username = ?", (name,))["id"]
        add_member(ws, uid, "owner" if role == "admin" else "member")
        c = TestClient(app)
        c.cookies.set(SESSION_COOKIE, issue_token(name))
        clients[name] = c
    return {"a": wa, "b": wb, "ca": clients["sc_a"], "cb": clients["sc_b"], "cu": clients["sc_user"]}


def test_scores_signals_competitors_api_and_isolation(tenants):
    ca, cb = tenants["ca"], tenants["cb"]
    lid = _lead("API Score", ws=tenants["a"], website_url=None, phone="9811111111", phone_valid=1)
    assert ca.get(f"/api/leads/{lid}/scores").status_code == 404
    r = ca.post(f"/api/leads/{lid}/scores/recompute")
    assert r.status_code == 200 and r.json()["explanation"]["components"]
    assert ca.get(f"/api/leads/{lid}/scores").json()["overall"] == r.json()["overall"]
    assert ca.get(f"/api/leads/{lid}/score-history").json()
    sig = ca.get(f"/api/leads/{lid}/signals").json()
    assert "HIRING_DEVELOPER" in sig["unavailable_signals"]
    assert ca.get(f"/api/leads/{lid}/competitors").json()["sufficient_data"] is False
    other = _lead("Pin Me", ws=tenants["a"])
    pinned = ca.post(f"/api/leads/{lid}/competitors", json={"competitor_lead_id": other})
    assert pinned.status_code == 200 and pinned.json()["competitors"][0]["source"] == "pinned"
    assert ca.delete(f"/api/leads/{lid}/competitors/{other}").status_code == 200
    assert ca.delete(f"/api/leads/{lid}/competitors/{other}").status_code == 404
    foreign = _lead("Foreign", ws=tenants["b"])
    assert ca.post(f"/api/leads/{lid}/competitors", json={"competitor_lead_id": foreign}).status_code == 404

    for method, path in (("get", f"/api/leads/{lid}/scores"), ("post", f"/api/leads/{lid}/scores/recompute"),
                         ("get", f"/api/leads/{lid}/score-history"), ("get", f"/api/leads/{lid}/signals"),
                         ("post", f"/api/leads/{lid}/signals/refresh"), ("get", f"/api/leads/{lid}/competitors"),
                         ("post", f"/api/leads/{lid}/competitors/audit"),
                         ("delete", f"/api/leads/{lid}/competitors/{other}")):
        assert getattr(cb, method)(path).status_code == 404, path
    assert cb.post(f"/api/leads/{lid}/competitors", json={"competitor_lead_id": other}).status_code == 404
    assert cb.get("/api/scoring/summary").json()["top_leads"] == []


def test_competitor_audit_endpoint_runs_audits_and_respects_limits(tenants, monkeypatch):
    from app import audit as audit_mod
    ca = tenants["ca"]
    ids = [_lead(f"Rival {i}", ws=tenants["a"], niche="rivals", city="Rivalton",
                 website_url=f"https://rival{i}.in") for i in range(5)]
    target = _lead("Rival Target", ws=tenants["a"], niche="rivals", city="Rivalton")
    _audit(target, 30, ws=tenants["a"])
    called = []

    def fake_run(cid, use_psi=True):
        called.append((cid, use_psi))
        _audit(cid, 70, ws=tenants["a"])
        return {}
    monkeypatch.setattr(audit_mod, "run_audit", fake_run)
    from app import security
    security._hits.clear()
    r = ca.post(f"/api/leads/{target}/competitors/audit?max_audits=3")
    assert r.status_code == 200 and len(r.json()["audited"]) == 3 and all(not p for _, p in called)
    assert r.json()["comparison"]["comparable"] == 3
    # 2 rivals remain; with a limit of 1 audit per window the second is refused
    monkeypatch.setattr(settings, "audit_rate_limit", 1)
    security._hits.clear()
    limited = ca.post(f"/api/leads/{target}/competitors/audit?max_audits=2")
    assert limited.status_code == 429 and "after 1 audit" in limited.json()["detail"]
    assert len(called) == 4, "exactly one more audit ran before the limit hit"
    security._hits.clear()


def test_leads_list_sorts_and_filters_by_the_new_scores(tenants):
    ca = tenants["ca"]
    hi = _lead("Zed Hot", ws=tenants["a"], niche="zz-rank", overall_score=90, opportunity_tier="HOT", intent_score=80)
    lo = _lead("Zed Cold", ws=tenants["a"], niche="zz-rank", overall_score=20, opportunity_tier="COLD", intent_score=5)
    rows = ca.get("/api/leads", params={"niche": "zz-rank", "sort": "overall_score", "direction": "desc"}).json()["leads"]
    assert [r["id"] for r in rows] == [hi, lo] and rows[0]["overall_score"] == 90
    assert "opportunity_tier" in rows[0] and "intent_score" in rows[0]
    q = lambda **p: [r["id"] for r in ca.get("/api/leads", params={"niche": "zz-rank", **p}).json()["leads"]]
    assert q(min_overall=50) == [hi] and q(tier="cold") == [lo] and q(min_intent=50) == [hi]
    assert ca.get("/api/leads", params={"min_overall": 101}).status_code == 422


def test_weights_and_summary_endpoints(tenants):
    ca, cb, cu = tenants["ca"], tenants["cb"], tenants["cu"]
    assert cu.put("/api/scoring/weights", json={"digital_gap": 0.5}).status_code == 403
    assert ca.put("/api/scoring/weights", json={"digital_gap": 0.5}).json()["weights"]["digital_gap"] == 0.5
    assert cb.get("/api/scoring/weights").json()["weights"]["digital_gap"] == 0.25
    assert ca.put("/api/scoring/weights", json={"digital_gap": -1}).status_code == 400
    assert ca.put("/api/scoring/weights", json={"digital_gap": 0.25}).status_code == 200
    s = ca.get("/api/scoring/summary").json()
    assert {"tiers", "top_leads", "leads_with_active_signals"} <= set(s)
    assert all(t["tier"] for t in s["tiers"])


def test_patching_a_lead_rescores_it(tenants):
    ca = tenants["ca"]
    lid = _lead("Patch Score", ws=tenants["a"], website_url=None)
    ca.post(f"/api/leads/{lid}/scores/recompute")
    before = ca.get(f"/api/leads/{lid}/scores").json()["contactability"]
    assert ca.patch(f"/api/leads/{lid}", json={"phone": "9811112222", "email": "x.y@patch.in"}).status_code == 200
    assert ca.get(f"/api/leads/{lid}/scores").json()["contactability"] > before


def test_deleting_a_lead_removes_scores_signals_and_competitor_links(tenants):
    ca = tenants["ca"]
    lid = _lead("Del Score", ws=tenants["a"], website_url=None)
    other = _lead("Del Rival", ws=tenants["a"])
    ca.post(f"/api/leads/{lid}/scores/recompute")
    ca.post(f"/api/leads/{lid}/competitors", json={"competitor_lead_id": other})
    assert ca.delete(f"/api/leads/{other}").status_code == 200
    after = ca.get(f"/api/leads/{lid}/competitors").json()["competitors"]
    assert not [c for c in after if c["source"] == "pinned" or c["lead_id"] == other]
    assert query_one("SELECT 1 x FROM lead_competitors WHERE competitor_lead_id = ?", (other,)) is None
    assert ca.delete(f"/api/leads/{lid}").status_code == 200
    for t in ("lead_scores", "lead_score_history", "lead_signals", "lead_competitors"):
        assert query_one(f"SELECT 1 x FROM {t} WHERE lead_id = ?", (lid,)) is None, t
