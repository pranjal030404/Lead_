"""Pure-logic tests for the free discovery engines (no network, no database)."""
from datetime import datetime, timedelta, timezone

from app.discovery import free_signals as fs
from app.providers import nominatim, osm_new

NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)


def test_opened_signal_prefers_tags_then_first_mapped():
    recent = (NOW - timedelta(days=20)).isoformat()
    old = (NOW - timedelta(days=400)).isoformat()
    assert osm_new.opened_signal({"timestamp": recent, "version": 1, "tags": {}}, NOW, 180)["basis"].startswith("first mapped")
    assert osm_new.opened_signal({"timestamp": recent, "version": 4, "tags": {}}, NOW, 180) is None   # edited => not new
    assert osm_new.opened_signal({"timestamp": old, "version": 1, "tags": {}}, NOW, 180) is None
    sig = osm_new.opened_signal({"timestamp": old, "version": 9, "tags": {"opening_date": "2026-10-01"}}, NOW, 180)
    assert sig["basis"] == "opening_date tag" and sig["confidence"] > 0.8
    assert osm_new.opened_signal({"tags": {"start_date": "1999-01-01"}}, NOW, 180) is None
    assert osm_new.opened_signal({"tags": {"start_date": "2026"}}, NOW, 400) is not None


def test_nominatim_parse_and_viewbox():
    item = {"osm_type": "node", "osm_id": 7, "name": "Smile Dental", "lat": "18.5", "lon": "73.8",
            "display_name": "Smile Dental, Pune", "category": "amenity",
            "extratags": {"phone": "+91 20 1234 5678", "website": "https://smile.in", "contact:instagram": "https://instagram.com/smile"},
            "address": {"suburb": "Koregaon Park", "city": "Pune", "postcode": "411001", "country": "India"}}
    c = nominatim.parse_result(item, "Pune", "")
    assert c["place_id"] == "osm:node/7" and c["website_url"] == "https://smile.in" and c["area"] == "Koregaon Park"
    assert c["instagram_url"].endswith("/smile") and nominatim.parse_result({"osm_type": "node", "osm_id": 1}, "x", "") is None
    left, top, right, bottom = map(float, nominatim.viewbox(18.5, 73.8, 2000).split(","))
    assert left < 73.8 < right and bottom < 18.5 < top


def test_careers_and_roles():
    html = '<a href="/careers">Careers</a><a href="https://other.com/jobs">x</a><a href="/about">About</a><a href="/join-us">Join us</a>'
    links = fs.careers_links(html, "https://shop.com/")
    assert "https://shop.com/careers" in links and all("other.com" not in l for l in links)
    roles = fs.digital_roles_in("<li>Hiring: Digital Marketing Executive</li><li>Shopify developer needed</li><li>Cook</li>")
    assert "digital marketing" in roles and "shopify" in roles and "cook" not in roles


def test_job_feed_matching():
    jobs = [{"company_name": "Acme Dental Ltd", "title": "SEO Specialist", "url": "u1"},
            {"company_name": "Acme Dental Ltd", "title": "Receptionist", "url": "u2"},
            {"company_name": "Other Co", "title": "Web Developer", "url": "u3"}]
    hits = fs.match_company_jobs(jobs, "Acme Dental", "company_name", "title")
    assert [h["title"] for h in hits] == ["SEO Specialist"]
    assert fs.match_company_jobs(jobs, "ab", "company_name", "title") == []      # too short to match safely


def test_ad_footprint_and_wasted_spend():
    page = ('<script src="https://connect.facebook.net/en_US/fbevents.js"></script><script>fbq("init","1")</script>'
            '<script async src="https://www.googletagmanager.com/gtag/js?id=AW-123456789"></script>')
    r = fs.ad_footprint(page)
    names = {p["platform"] for p in r["platforms"]}
    assert r["advertising"] and "Google Ads" in names and r["confidence"] == "high"
    assert any("mobile-friendly" in g for g in r["wasted_spend_signals"]) and any("no phone" in g for g in r["wasted_spend_signals"])
    clean = fs.ad_footprint('<meta name="viewport" content="x"><a href="tel:123">call</a><script src="google-analytics.com"></script>')
    assert not clean["advertising"] and clean["confidence"] == "none"
