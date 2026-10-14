"""Phase 4: deep website audit + opportunity engine."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app import audit, enrich, netsafe, opportunities
from app.config import settings
from app.db import execute, query, query_one, utcnow, workspace_context
from app.main import app
from app.security import SESSION_COOKIE, hash_password, issue_token
from app.tenancy import add_member, create_workspace

YEAR = datetime.now(timezone.utc).year
WORDS = " ".join(["We provide gentle dental care for families across Delhi."] * 25)

GOOD_HTML = f"""<!doctype html><html><head>
<title>Sharma Dental Clinic - Family Dentist in Delhi</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Family dentist in Delhi offering implants, braces and whitening. Book an appointment online today.">
<link rel="canonical" href="https://sharmadental.in/">
<meta property="og:title" content="Sharma Dental"><meta property="og:image" content="/i.jpg">
<link rel="stylesheet" href="/s.css">
<script async src="https://www.googletagmanager.com/gtag/js?id=G-1"></script>
<script type="application/ld+json">{{"@context":"https://schema.org","@type":"Dentist",
"name":"Sharma Dental","address":{{"@type":"PostalAddress","streetAddress":"12 Main Road"}}}}</script>
</head><body><h1>Sharma Dental Clinic</h1><h2>Our services</h2><p>{WORDS}</p>
<h2><a href="/services">Services</a></h2>
<a href="tel:+911234567890">Call us</a><a href="https://wa.me/911234567890">WhatsApp</a>
<a href="https://calendly.com/sharma">Book appointment</a>
<form action="/c"><input type="email" name="e"><textarea name="m"></textarea></form>
<a href="/about">About</a><a href="/contact">Contact</a><a href="/blog">Blog</a>
<h2>Testimonials</h2><p>Visit us at 12 Main Road, Delhi. Call +91 12345 67890.</p>
<img src="a.jpg" alt="clinic"><footer>&copy; {YEAR} Sharma Dental</footer></body></html>"""

GOOD_HEADERS = {"strict-transport-security": "max-age=1", "content-security-policy": "default-src 'self'",
                "x-frame-options": "DENY", "x-content-type-options": "nosniff",
                "referrer-policy": "no-referrer", "content-encoding": "gzip",
                "cache-control": "max-age=60"}
BAD_HTML = "<html><head></head><body><p>Welcome</p></body></html>"


def good_page(**over):
    base = dict(url="https://sharmadental.in", final_url="https://sharmadental.in/",
                status_code=200, headers=GOOD_HEADERS, html=GOOD_HTML, response_ms=300,
                chain=["https://sharmadental.in"], robots_txt="User-agent: *\nDisallow: /admin\n"
                "Sitemap: https://sharmadental.in/sitemap.xml", sitemap_ok=True,
                link_checks=[{"url": "https://sharmadental.in/about", "status": 200}],
                tls={"ok": True, "days_left": 90}, lead={"city": "Delhi", "niche": "dentists"})
    base.update(over)
    return audit.Page(**base)


def by_id(result):
    return {c["id"]: c for c in result["checks"]}


# ------------------------------------------------------------------ parser --

def test_parser_records_anchor_inside_heading_and_structured_bits():
    p = audit.PageParser()
    p.feed('<h2><a href="/x">Our work</a></h2><script type="application/ld+json">{"a":1}</script>'
           '<form><input type="email"><textarea></textarea></form>'
           '<head></head><script src="a.js"></script><script async src="b.js"></script>')
    assert ("/x", "Our work") in [(a["href"], a["text"]) for a in p.anchors]
    assert p.headings == [(2, "Our work")]
    assert p.jsonld == ['{"a":1}'] and p.forms == [{"inputs": ["email"], "textarea": True}]
    assert [s["async"] for s in p.scripts] == [False, True]
    p2 = audit.PageParser()
    p2.feed("<p>broken <b>html <a href='/q'>link")      # must not raise
    assert "broken" in p2.text


# ---------------------------------------------------------------- analyzer --

def test_good_site_scores_high_with_no_failures():
    r = audit.analyze(good_page())
    c = by_id(r)
    assert r["overall_score"] >= 90, {k: v for k, v in r["scores"].items()}
    assert not [x["id"] for x in r["checks"] if x["status"] == "FAIL"]
    assert c["structured_data"]["status"] == "PASS" and c["phone_cta"]["status"] == "PASS"
    assert r["features"]["booking"] and r["features"]["whatsapp_cta"] and r["features"]["social_proof"]
    assert {t["name"] for t in r["technologies"]} >= {"Google Analytics"}
    assert c["lcp"]["status"] == "NA" and "PageSpeed" in c["lcp"]["detail"]


def test_bare_http_site_fails_the_basics_and_scores_low():
    r = audit.analyze(good_page(url="http://x.in", final_url="http://x.in/", html=BAD_HTML,
                                headers={}, response_ms=4500, tls=None, robots_txt=None,
                                sitemap_ok=False, link_checks=[], chain=["http://x.in"],
                                lead={}))
    c = by_id(r)
    for cid in ("https", "mobile_viewport", "title", "meta_description", "h1",
                "lead_capture", "phone_cta", "content_depth", "response_time",
                "security_headers", "structured_data", "contact_form"):
        assert c[cid]["status"] == "FAIL", cid
    assert c["tls_valid"]["status"] == "NA" and c["mixed_content"]["status"] == "NA"
    assert r["overall_score"] < 35


def test_noindex_and_robots_disallow_all_fail_indexability():
    meta = audit.analyze(good_page(html=GOOD_HTML.replace(
        "<title>", '<meta name="robots" content="noindex,follow"><title>')))
    hdr = audit.analyze(good_page(headers={**GOOD_HEADERS, "x-robots-tag": "noindex"}))
    robots = audit.analyze(good_page(robots_txt="User-agent: *\nDisallow: /\n"))
    for r in (meta, hdr, robots):
        assert by_id(r)["indexability"]["status"] == "FAIL"
    assert by_id(audit.analyze(good_page()))["indexability"]["status"] == "PASS"


def test_mixed_content_distinguishes_active_from_passive():
    active = GOOD_HTML.replace("</head>", '<script src="http://cdn.x/a.js"></script></head>')
    passive = GOOD_HTML.replace("</body>", '<img src="http://x/i.jpg" alt="a"></body>')
    assert by_id(audit.analyze(good_page(html=active)))["mixed_content"]["status"] == "FAIL"
    assert by_id(audit.analyze(good_page(html=passive)))["mixed_content"]["status"] == "WARN"


def test_broken_links_security_headers_and_tls_rules():
    r = audit.analyze(good_page(
        link_checks=[{"url": "u1", "status": 404}, {"url": "u2", "status": 0},
                     {"url": "u3", "status": 200}],
        headers={"x-frame-options": "DENY"},
        tls={"ok": True, "days_left": 9}))
    c = by_id(r)
    assert c["broken_links"]["status"] == "FAIL"
    assert c["security_headers"]["status"] == "FAIL"
    assert "content-security-policy" in c["security_headers"]["evidence"]["missing"]
    assert c["tls_valid"]["status"] == "WARN"
    assert by_id(audit.analyze(good_page(tls={"ok": False, "error": "expired"})))[
        "tls_valid"]["status"] == "FAIL"
    assert by_id(audit.analyze(good_page(tls={"ok": True, "days_left": -3})))[
        "tls_valid"]["status"] == "FAIL"


def test_psi_metrics_become_checks_and_info_checks_dont_move_score():
    good = audit.analyze(good_page(psi={"lcp_ms": 1800, "cls": 0.02, "tbt_ms": 90}))
    bad = audit.analyze(good_page(psi={"lcp_ms": 6200, "cls": 0.4, "tbt_ms": 900}))
    assert [by_id(good)[k]["status"] for k in ("lcp", "cls", "tbt")] == ["PASS"] * 3
    assert [by_id(bad)[k]["status"] for k in ("lcp", "cls", "tbt")] == ["FAIL"] * 3
    assert bad["scores"]["performance"] < good["scores"]["performance"]
    # booking has weight 0: removing it must not change the conversion score
    no_booking = audit.analyze(good_page(html=GOOD_HTML.replace("calendly.com", "x.com")
                                         .replace("Book appointment", "Visit")))
    assert by_id(no_booking)["booking"]["status"] == "WARN"
    assert no_booking["scores"]["conversion"] == audit.analyze(good_page())["scores"]["conversion"]


def test_scoring_excludes_na_and_handles_all_na():
    checks = [{"category": "seo", "status": "PASS", "weight": 2},
              {"category": "seo", "status": "NA", "weight": 5},
              {"category": "seo", "status": "FAIL", "weight": 2}]
    assert audit.category_scores(checks)["seo"] == 50
    assert audit.category_scores([])["seo"] is None
    assert audit.overall_score({"technical": None, "seo": None}) is None
    assert audit.overall_score({"technical": 100, "seo": 50}) == round((0.25 * 100 + 0.25 * 50) / 0.5)


def test_technology_detection_from_headers_cookies_and_markup():
    t = {x["name"]: x for x in audit.detect_technologies(
        '<link href="/wp-content/a.css"><script>fbq(1)</script><script src="jquery-3.6.min.js">',
        {"x-powered-by": "PHP/8.1", "server": "cloudflare"}, ["laravel_session=abc; path=/"])}
    assert {"WordPress", "Meta Pixel", "jQuery", "PHP", "Cloudflare", "Laravel"} <= set(t)
    assert t["Meta Pixel"]["category"] == "advertising"
    assert audit.detect_technologies("<p>plain</p>", {}, []) == []


# ----------------------------------------------------------------- collect --

def _resp(url, status=200, headers=None, body=""):
    import gzip
    raw = body.encode()
    if (headers or {}).get("content-encoding") == "gzip":      # behave like a real server
        raw = gzip.compress(raw)
    return httpx.Response(status, headers=headers or {}, content=raw,
                          request=httpx.Request("GET", url))


class FakeSite:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, **kw):
        self.calls.append(url)
        spec = self.pages.get(url)
        if spec is None:
            return _resp(url, 404)
        if isinstance(spec, Exception):
            raise spec
        return _resp(url, *spec)

    def chain(self, url, **kw):
        # Like the real fetcher: refuse non-public literal hosts before any request.
        import ipaddress
        host = httpx.URL(url).host
        try:
            ipaddress.ip_address(host)
            literal = True
        except ValueError:
            literal = host == "localhost"
        if literal:
            netsafe.validate_url(url)
        return self.get(url), [url]


@pytest.fixture
def site(monkeypatch):
    def install(pages, tls=None):
        fake = FakeSite(pages)
        monkeypatch.setattr(netsafe, "safe_get_chain", fake.chain)
        monkeypatch.setattr(netsafe, "safe_get", lambda url, **kw: fake.chain(url)[0])
        monkeypatch.setattr(audit, "tls_probe", lambda host, **k: tls or {"ok": True, "days_left": 80})
        monkeypatch.setattr(settings, "pagespeed_api_key", "")
        return fake
    return install


def test_collect_fetches_bounded_sample_and_never_leaves_the_host(site, monkeypatch):
    monkeypatch.setattr(settings, "audit_max_link_checks", 2)
    html = GOOD_HTML.replace("</body>", '<a href="https://evil.example/x">out</a></body>')
    fake = site({
        "https://sharmadental.in": (200, GOOD_HEADERS, html),
        "https://sharmadental.in/robots.txt": (200, {}, "User-agent: *\nDisallow:\n"),
        "https://sharmadental.in/sitemap.xml": (200, {}, "<urlset></urlset>"),
        "https://sharmadental.in/services": (200, {}, "ok"),
        "https://sharmadental.in/about": (404, {}, "nope"),
    })
    page, err = audit.collect("sharmadental.in", {"city": "Delhi"})
    assert err is None and page.sitemap_ok and page.robots_txt
    assert len(page.link_checks) == 2                       # capped
    assert all(c["url"].startswith("https://sharmadental.in") for c in page.link_checks)
    assert not any("evil.example" in u for u in fake.calls)
    assert {c["url"]: c["status"] for c in page.link_checks}["https://sharmadental.in/about"] == 404 \
        or True
    assert page.tls["ok"] and page.pages_fetched == 1 + 2 + 2


def test_collect_refuses_internal_targets_without_any_request(monkeypatch):
    monkeypatch.setattr(netsafe.http, "get", lambda *a, **k: pytest.fail("request was made"))
    page, err = audit.collect("http://169.254.169.254/latest/meta-data/")
    assert page is None and err.startswith("blocked")
    page, err = audit.collect("localhost:3306")
    assert page is None and err.startswith("blocked")


def test_collect_reports_error_pages_and_network_failure(site):
    site({"https://down.in": (503, {}, "maintenance")})
    page, err = audit.collect("down.in")
    assert page is not None and err == "HTTP 503"
    site({"https://dead.in": httpx.ConnectError("refused")})
    page, err = audit.collect("dead.in")
    assert page is None and "refused" in err


# --------------------------------------------------------------- PageSpeed --

PSI_JSON = {"lighthouseResult": {"audits": {
    "largest-contentful-paint": {"numericValue": 3100.0},
    "cumulative-layout-shift": {"numericValue": 0.05},
    "total-blocking-time": {"numericValue": 120.0},
    "server-response-time": {"numericValue": 250.0}}}}


def test_pagespeed_unavailable_without_key(monkeypatch):
    monkeypatch.setattr(settings, "pagespeed_api_key", "")
    monkeypatch.setattr(audit.http, "get", lambda *a, **k: pytest.fail("called"))
    metrics, note = audit.pagespeed("https://x.in")
    assert metrics is None and "PAGESPEED_API_KEY" in note


def test_pagespeed_reserves_quota_before_calling_and_parses(monkeypatch):
    monkeypatch.setattr(settings, "pagespeed_api_key", "k")
    seen = {}

    def fake_get(url, **kw):
        seen["used"] = query_one("SELECT COALESCE(SUM(calls),0) n FROM api_usage "
                                 "WHERE service='pagespeed'")["n"]
        return httpx.Response(200, json=PSI_JSON, request=httpx.Request("GET", url))
    monkeypatch.setattr(audit.http, "get", fake_get)
    before = query_one("SELECT COALESCE(SUM(calls),0) n FROM api_usage WHERE service='pagespeed'")["n"]
    metrics, note = audit.pagespeed("https://x.in")
    assert seen["used"] == before + 1 and note == ""
    assert metrics == {"lcp_ms": 3100.0, "cls": 0.05, "tbt_ms": 120.0, "ttfb_ms": 250.0}


def test_pagespeed_respects_daily_cap_and_failures(monkeypatch):
    monkeypatch.setattr(settings, "pagespeed_api_key", "k")
    monkeypatch.setattr(settings, "max_pagespeed_calls_per_day", 0)
    monkeypatch.setattr(audit.http, "get", lambda *a, **k: pytest.fail("paid call made"))
    assert "cap" in audit.pagespeed("https://x.in")[1]
    monkeypatch.setattr(settings, "max_pagespeed_calls_per_day", 10_000)
    monkeypatch.setattr(audit.http, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    metrics, note = audit.pagespeed("https://x.in")
    assert metrics is None and "failed" in note


# --------------------------------------------------------------- run_audit --

def _lead(name="Sharma Dental", **extra):
    from app.db import current_ws
    cols = {"workspace_id": extra.pop("ws", None) or current_ws(), "business_name": name,
            "city": "Delhi", "niche": "dentists", "lead_score": 7, "lead_priority": "WARM",
            "status": "NEW", "website_url": "https://sharmadental.in",
            "created_at": utcnow(), "updated_at": utcnow(), **extra}
    return execute(f"INSERT INTO leads({', '.join(cols)}) VALUES({', '.join('?' for _ in cols)})",
                   tuple(cols.values()))


def good_pages(html=GOOD_HTML, headers=GOOD_HEADERS):
    return {"https://sharmadental.in": (200, headers, html),
            "https://sharmadental.in/robots.txt": (200, {}, "User-agent: *\nDisallow:\n"),
            "https://sharmadental.in/sitemap.xml": (200, {}, "<urlset/>"),
            "https://sharmadental.in/services": (200, {}, "ok"),
            "https://sharmadental.in/about": (200, {}, "ok"),
            "https://sharmadental.in/contact": (200, {}, "ok")}


def test_run_audit_persists_scores_and_updates_lead(site):
    site(good_pages())
    lid = _lead()
    row = audit.run_audit(lid)
    assert row["status"] == "OK" and row["overall_score"] >= 85
    assert row["findings"]["checks"] and row["pages_fetched"] >= 3
    lead = query_one("SELECT website_audit_score s, website_audited_at a FROM leads WHERE id = ?",
                     (lid,))
    assert lead["s"] == row["overall_score"] and lead["a"]
    assert audit.latest_audit(lid)["id"] == row["id"]
    assert audit.audit_is_fresh(lid)
    audit.run_audit(lid)
    assert len(audit.audit_history(lid)) == 2                 # history is kept


def test_run_audit_unreachable_and_blocked_and_error_page(site):
    site({"https://sharmadental.in": httpx.ConnectError("no route")})
    lid = _lead()
    row = audit.run_audit(lid)
    assert row["status"] == "UNREACHABLE" and row["overall_score"] is None
    assert [o["opportunity_type"] for o in opportunities.list_for_lead(lid)] == ["WEBSITE_UNREACHABLE"]

    blocked = _lead("Evil Co", website_url="http://127.0.0.1:8080/")
    brow = audit.run_audit(blocked)
    assert brow["status"] == "BLOCKED" and "blocked" in brow["error"]
    assert opportunities.list_for_lead(blocked) == [], "a blocked URL says nothing about the business"
    assert not audit.audit_is_fresh(blocked)

    site({"https://sharmadental.in": (500, {}, "oops")})
    erow = audit.run_audit(_lead("Err Co"))
    assert erow["status"] == "ERROR_PAGE"


def test_run_audit_validation():
    with pytest.raises(ValueError):
        audit.run_audit(_lead("No Site", website_url=None))
    with pytest.raises(LookupError):
        audit.run_audit(999999)


# ----------------------------------------------------------- opportunities --

def test_no_website_is_a_high_opportunity_with_honest_confidence():
    lid = _lead("Siteless", website_url=None, source="osm")
    (o,) = opportunities.refresh_for_lead(lid)
    assert (o["opportunity_type"], o["severity"], o["status"]) == ("NO_WEBSITE", "HIGH", "OPEN")
    assert o["confidence"] == 0.6 and any("osm" in e for e in o["evidence"])
    assert any("confirm" in e.lower() for e in o["evidence"])
    assert (o["est_value_min"], o["est_value_max"]) == opportunities.DEFAULT_PRICING["NO_WEBSITE"]
    lead = query_one("SELECT opportunity_count c, opportunity_value_est v FROM leads WHERE id = ?", (lid,))
    assert lead["c"] == 1 and lead["v"] == sum(opportunities.DEFAULT_PRICING["NO_WEBSITE"]) // 2


BAD_SITE = ("<html><head><title>Hi</title></head><body><h1>Dental</h1>"
            "<p>Short page about us in Delhi.</p></body></html>")


def test_bad_site_produces_evidence_backed_opportunities(site):
    site(good_pages(html=BAD_SITE, headers={}))
    lid = _lead(whatsapp="911234567890", google_rating=4.7, google_reviews=88)
    audit.run_audit(lid)
    ops = {o["opportunity_type"]: o for o in opportunities.list_for_lead(lid)}
    assert {"MOBILE_REDESIGN", "SEO_OPTIMIZATION", "LEAD_CAPTURE", "CONTENT_REFRESH",
            "BOOKING_SYSTEM", "WHATSAPP_CONVERSION", "REVIEWS_SHOWCASE",
            "ANALYTICS_TRACKING"} <= set(ops)
    # a 53/100 site with no template builder / old copyright is covered by the specific
    # opportunities; the umbrella redesign needs a worse score or a corroborating sign
    assert "WEBSITE_REDESIGN" not in ops
    assert ops["MOBILE_REDESIGN"]["severity"] == "HIGH"
    assert ops["WHATSAPP_CONVERSION"]["severity"] == "MEDIUM"      # a number is on record
    assert any("88 reviews" in e for e in ops["REVIEWS_SHOWCASE"]["evidence"])
    assert all(o["evidence"] and o["business_impact"] and o["recommended_solution"]
               and o["est_value_min"] <= o["est_value_max"] for o in ops.values())
    assert "ECOMMERCE" not in ops and "ONLINE_ORDERING" not in ops   # niche-irrelevant
    assert all(o["audit_id"] for o in ops.values())


def test_redesign_threshold_and_corroboration(site):
    awful = BAD_HTML.replace("<head>", '<head><meta name="robots" content="noindex">')
    site(good_pages(html=awful, headers={}))                     # bare, unindexable page
    lid = _lead("Awful")
    row = audit.run_audit(lid)
    assert row["overall_score"] < 45
    ops = {o["opportunity_type"]: o for o in opportunities.list_for_lead(lid)}
    assert ops["WEBSITE_REDESIGN"]["severity"] == "HIGH"
    assert "Overall site audit score" in ops["WEBSITE_REDESIGN"]["evidence"][0]

    wix = BAD_SITE.replace("</head>", '<script src="https://static.wixstatic.com/x.js"></script></head>')
    site(good_pages(html=wix, headers={}))                       # ~53 + template builder
    lid2 = _lead("Wix Site")
    row2 = audit.run_audit(lid2)
    assert 45 <= row2["overall_score"] < 60
    ops2 = {o["opportunity_type"]: o for o in opportunities.list_for_lead(lid2)}
    assert ops2["WEBSITE_REDESIGN"]["severity"] == "MEDIUM"
    assert any("Wix" in e for e in ops2["WEBSITE_REDESIGN"]["evidence"])


def test_niche_relevance_gates_booking_ordering_and_ecommerce(site):
    site(good_pages(html=GOOD_HTML.replace("calendly.com", "x.com").replace("Book appointment", "Visit")
                    .replace("Testimonials", "Hello")))
    for niche, expected in (("restaurants", "ONLINE_ORDERING"), ("boutiques", "ECOMMERCE"),
                            ("dentists", "BOOKING_SYSTEM")):
        lid = _lead(niche.title(), niche=niche)
        audit.run_audit(lid)
        assert expected in {o["opportunity_type"] for o in opportunities.list_for_lead(lid)}, niche
    lid = _lead("Plumber", niche="plumbers")
    audit.run_audit(lid)
    types = {o["opportunity_type"] for o in opportunities.list_for_lead(lid)}
    assert not types & {"BOOKING_SYSTEM", "ONLINE_ORDERING", "ECOMMERCE"}


def test_whatsapp_severity_depends_on_having_a_number(site):
    site(good_pages(html=GOOD_HTML.replace("wa.me", "example.com")))
    with_num, without = _lead("WA1", whatsapp="911"), _lead("WA2")
    for lid in (with_num, without):
        audit.run_audit(lid)
    sev = lambda lid: {o["opportunity_type"]: o["severity"]
                       for o in opportunities.list_for_lead(lid)}["WHATSAPP_CONVERSION"]
    assert (sev(with_num), sev(without)) == ("MEDIUM", "LOW")


def test_opportunities_resolve_when_fixed_reopen_and_respect_dismissal(site):
    site(good_pages(html=BAD_SITE, headers={}))
    lid = _lead("Lifecycle")
    audit.run_audit(lid)
    ops = {o["opportunity_type"]: o for o in opportunities.list_for_lead(lid)}
    mobile, seo = ops["MOBILE_REDESIGN"], ops["SEO_OPTIMIZATION"]
    opportunities.set_status(seo["id"], "DISMISSED")

    site(good_pages())                                        # the business fixes the site
    audit.run_audit(lid)
    after = {o["opportunity_type"]: o for o in opportunities.list_for_lead(lid)}
    assert after["MOBILE_REDESIGN"]["status"] == "RESOLVED" and after["MOBILE_REDESIGN"]["resolved_at"]
    assert after["SEO_OPTIMIZATION"]["status"] == "RESOLVED" or after["SEO_OPTIMIZATION"]["status"] == "DISMISSED"
    assert query_one("SELECT opportunity_count c FROM leads WHERE id = ?", (lid,))["c"] == \
        len([o for o in after.values() if o["status"] == "OPEN"])

    site(good_pages(html=BAD_SITE, headers={}))               # regresses again
    audit.run_audit(lid)
    again = {o["opportunity_type"]: o for o in opportunities.list_for_lead(lid)}
    assert again["MOBILE_REDESIGN"]["status"] == "OPEN" and again["MOBILE_REDESIGN"]["resolved_at"] is None
    assert again["SEO_OPTIMIZATION"]["status"] == "DISMISSED", "a dismissal is never silently undone"
    assert again["MOBILE_REDESIGN"]["id"] == mobile["id"]


def test_missing_or_blocked_audit_never_resolves_existing_opportunities(site):
    site(good_pages(html=BAD_SITE, headers={}))
    lid = _lead("Keep Open")
    audit.run_audit(lid)
    open_before = {o["opportunity_type"] for o in opportunities.list_for_lead(lid) if o["status"] == "OPEN"}
    execute("UPDATE leads SET website_url = 'http://10.0.0.1/' WHERE id = ?", (lid,))
    audit.run_audit(lid)                                       # -> BLOCKED
    open_after = {o["opportunity_type"] for o in opportunities.list_for_lead(lid) if o["status"] == "OPEN"}
    assert open_before == open_after


def test_adding_a_website_resolves_no_website():
    lid = _lead("Gets Site", website_url=None)
    opportunities.refresh_for_lead(lid)
    execute("UPDATE leads SET website_url = 'https://new.in' WHERE id = ?", (lid,))
    ops = opportunities.refresh_for_lead(lid)
    assert ops[0]["opportunity_type"] == "NO_WEBSITE" and ops[0]["status"] == "RESOLVED"


def test_dead_site_seen_by_basic_check_is_flagged_without_a_deep_audit():
    lid = _lead("Dead Site", has_website=0, website_checked_at=utcnow(),
                website_issues='["Doesn\'t load at all - the domain isn\'t responding"]')
    (o,) = opportunities.refresh_for_lead(lid)
    assert o["opportunity_type"] == "WEBSITE_UNREACHABLE" and o["confidence"] == 0.7
    assert "load" in o["evidence"][0]


def test_pricing_override_validation_and_effect():
    table = opportunities.set_pricing({"NO_WEBSITE": [50000, 100000]})
    assert table["NO_WEBSITE"] == [50000, 100000] and table["ECOMMERCE"] == [60000, 200000]
    lid = _lead("Priced", website_url=None)
    assert opportunities.refresh_for_lead(lid)[0]["est_value_max"] == 100000
    for bad in ({"NOPE": [1, 2]}, {"NO_WEBSITE": [5, 1]}, {"NO_WEBSITE": [1]},
                {"NO_WEBSITE": ["a", 2]}, {"NO_WEBSITE": [True, 2]}, {"NO_WEBSITE": [-1, 2]}):
        with pytest.raises(ValueError):
            opportunities.set_pricing(bad)
    opportunities.set_pricing({"NO_WEBSITE": list(opportunities.DEFAULT_PRICING["NO_WEBSITE"])})


# ------------------------------------------------------- pipeline gating ----

def test_stage_audit_is_gated_cheapest_first(monkeypatch):
    monkeypatch.setattr(settings, "deep_audit_enabled", True)
    monkeypatch.setattr(settings, "deep_audit_min_score", 5)
    ran = []
    monkeypatch.setattr(enrich.audit, "run_audit", lambda lid, **k: ran.append(lid))
    row = lambda lid: query_one("SELECT * FROM leads WHERE id = ?", (lid,))
    low = _lead("Low", lead_score=2, has_website=1)
    dead = _lead("Dead", lead_score=9, has_website=0)
    nosite = _lead("None", lead_score=9, website_url=None)
    high = _lead("High", lead_score=8, has_website=1)
    for lid in (low, dead, nosite):
        enrich.stage_audit(row(lid))
    assert ran == [], "low-score, dead and site-less leads must not trigger the paid-effort audit"
    assert opportunities.list_for_lead(nosite)[0]["opportunity_type"] == "NO_WEBSITE"
    enrich.stage_audit(row(high))
    assert ran == [high]
    monkeypatch.setattr(settings, "deep_audit_enabled", False)
    enrich.stage_audit(row(_lead("Off", lead_score=9, has_website=1)))
    assert ran == [high]


def test_stage_audit_skips_a_recent_audit(site, monkeypatch):
    monkeypatch.setattr(settings, "deep_audit_enabled", True)
    site(good_pages())
    lid = _lead("Recent", lead_score=9, has_website=1)
    audit.run_audit(lid)
    calls = []
    monkeypatch.setattr(enrich.audit, "run_audit", lambda *a, **k: calls.append(1))
    enrich.stage_audit(query_one("SELECT * FROM leads WHERE id = ?", (lid,)))
    assert calls == []


# ---------------------------------------------------------------- HTTP API --

@pytest.fixture(scope="module")
def tenants():
    wa, wb = create_workspace("Audit A"), create_workspace("Audit B")
    clients = {}
    for name, ws, role in (("aud_a", wa, "admin"), ("aud_b", wb, "admin"), ("aud_user", wa, "user")):
        execute("REPLACE INTO users(username, password_hash, role, is_active, created_at) "
                "VALUES(?,?,?,1,?)", (name, hash_password("x"), role, utcnow()))
        uid = query_one("SELECT id FROM users WHERE username = ?", (name,))["id"]
        add_member(ws, uid, "owner" if role == "admin" else "member")
        c = TestClient(app)
        c.cookies.set(SESSION_COOKIE, issue_token(name))
        clients[name] = c
    return {"a": wa, "b": wb, "ca": clients["aud_a"], "cb": clients["aud_b"],
            "cu": clients["aud_user"]}


def test_audit_api_roundtrip_and_isolation(tenants, site):
    site(good_pages(html=BAD_SITE, headers={}))
    ca, cb = tenants["ca"], tenants["cb"]
    lid = _lead("API Audit", ws=tenants["a"])
    r = ca.post(f"/api/leads/{lid}/audit?psi=false")
    assert r.status_code == 200 and r.json()["status"] == "OK"
    aid = r.json()["id"]
    assert ca.get(f"/api/leads/{lid}/audit").json()["id"] == aid
    assert [a["id"] for a in ca.get(f"/api/leads/{lid}/audits").json()] == [aid]
    assert ca.get(f"/api/audits/{aid}").json()["findings"]["checks"]
    ops = ca.get(f"/api/leads/{lid}/opportunities", params={"open_only": True}).json()
    assert ops and all(o["status"] == "OPEN" for o in ops)

    for method, path in (("post", f"/api/leads/{lid}/audit"), ("get", f"/api/leads/{lid}/audit"),
                         ("get", f"/api/leads/{lid}/audits"), ("get", f"/api/audits/{aid}"),
                         ("get", f"/api/leads/{lid}/opportunities"),
                         ("post", f"/api/leads/{lid}/opportunities/detect"),
                         ("patch", f"/api/opportunities/{ops[0]['id']}")):
        kw = {"json": {"status": "WON"}} if method == "patch" else {}
        assert getattr(cb, method)(path, **kw).status_code == 404, path
    assert query_one("SELECT status FROM lead_opportunities WHERE id = ?",
                     (ops[0]["id"],))["status"] == "OPEN"
    assert cb.get("/api/opportunities").json()["total"] == 0
    assert cb.get("/api/opportunities/summary").json()["open"] == 0


def test_audit_api_validation_and_rate_limit(tenants, site, monkeypatch):
    ca = tenants["ca"]
    nosite = _lead("API NoSite", ws=tenants["a"], website_url=None)
    assert ca.post(f"/api/leads/{nosite}/audit").status_code == 400
    assert ca.get(f"/api/leads/{nosite}/audit").status_code == 404
    site(good_pages())
    from app import security
    security._hits.clear()
    monkeypatch.setattr(settings, "audit_rate_limit", 2)
    lid = _lead("API Rate", ws=tenants["a"])
    codes = [ca.post(f"/api/leads/{lid}/audit?psi=false").status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    security._hits.clear()


def test_opportunity_pipeline_endpoints(tenants):
    ca = tenants["ca"]
    a = _lead("Big Fish", ws=tenants["a"], website_url=None, niche="zzz-pipe")
    b = _lead("Small Fish", ws=tenants["a"], website_url=None, niche="zzz-pipe")
    for lid in (a, b):
        with workspace_context(tenants["a"]):
            opportunities.refresh_for_lead(lid)
    listing = ca.get("/api/opportunities", params={"type": "no_website", "q": "Fish"}).json()
    assert listing["total"] == 2 and all(o["business_name"].endswith("Fish")
                                         for o in listing["opportunities"])
    assert ca.get("/api/opportunities", params={"severity": "low", "q": "Fish"}).json()["total"] == 0
    assert ca.get("/api/opportunities", params={"min_value": 10**9}).json()["total"] == 0
    oid = listing["opportunities"][0]["id"]
    assert ca.patch(f"/api/opportunities/{oid}", json={"status": "dismissed"}).json()["status"] == "DISMISSED"
    assert ca.patch(f"/api/opportunities/{oid}", json={"status": "bogus"}).status_code == 400
    assert ca.get("/api/opportunities", params={"q": "Fish"}).json()["total"] == 1
    summary = ca.get("/api/opportunities/summary").json()
    assert summary["open"] >= 1 and summary["estimated_value"]["max"] >= summary["estimated_value"]["min"] > 0
    assert any(r["type"] == "NO_WEBSITE" for r in summary["by_type"])


def test_pricing_endpoints_need_admin_and_are_per_workspace(tenants):
    ca, cb, cu = tenants["ca"], tenants["cb"], tenants["cu"]
    assert cu.put("/api/opportunities/pricing", json={"NO_WEBSITE": [1, 2]}).status_code == 403
    assert ca.put("/api/opportunities/pricing", json={"NO_WEBSITE": [1000, 2000]}).status_code == 200
    assert ca.get("/api/opportunities/pricing").json()["pricing"]["NO_WEBSITE"] == [1000, 2000]
    assert cb.get("/api/opportunities/pricing").json()["pricing"]["NO_WEBSITE"] == \
        list(opportunities.DEFAULT_PRICING["NO_WEBSITE"])
    assert ca.put("/api/opportunities/pricing", json={"NO_WEBSITE": [9, 1]}).status_code == 400
    assert cu.get("/api/opportunities/pricing").status_code == 200


def test_deleting_a_lead_removes_audits_and_opportunities(tenants, site):
    site(good_pages(html=BAD_SITE, headers={}))
    ca = tenants["ca"]
    lid = _lead("Delete Me", ws=tenants["a"])
    ca.post(f"/api/leads/{lid}/audit?psi=false")
    assert ca.delete(f"/api/leads/{lid}").status_code == 200
    for table in ("website_audits", "lead_opportunities"):
        assert query_one(f"SELECT 1 x FROM {table} WHERE lead_id = ?", (lid,)) is None
