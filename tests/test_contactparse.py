"""Pure-logic tests (no database needed): run with `pytest tests/test_contactparse.py`."""
import importlib.util, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

def _load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent.parent / "app" / f"{name}.py")
    m = importlib.util.module_from_spec(spec); sys.modules[name] = m; spec.loader.exec_module(m); return m

cp, ev, ex = _load("contactparse"), _load("evidence"), _load("exportutil")


def test_obfuscated_and_entity_emails():
    html = 'Mail: sales [at] acme [dot] co [dot] in or &#105;nfo&#64;acme.in <a href="mailto:Hi@Acme.in?subject=x">'
    got = cp.extract_emails(html)
    assert "hi@acme.in" in got and "info@acme.in" in got and "sales@acme.co.in" in got


def test_cloudflare_email():
    key = 0x42
    enc = bytes([key]) + bytes(b ^ key for b in b"owner@shop.com")
    assert cp.extract_emails(f'<a data-cfemail="{enc.hex()}">') == ["owner@shop.com"]


def test_junk_filtered_and_ranking():
    html = "logo@2x.png you@example.com agency@webdesign.io owner@shop.com me@gmail.com"
    got = cp.extract_emails(html)
    assert "logo@2x.png" not in got and "you@example.com" not in got
    assert cp.rank_emails(got, "https://www.shop.com")[0] == "owner@shop.com"


def test_phones_and_structured():
    html = ('<a href="tel:+91 98765-43210">x</a><a href="https://wa.me/919876543210">'
            '<script type="application/ld+json">{"@type":"Dentist","name":"Smile","telephone":"011 4000 1234",'
            '"sameAs":["https://instagram.com/smile"],"address":{"streetAddress":"1 Rd","addressLocality":"Delhi"}}</script>')
    assert "+919876543210" in cp.extract_phones(html)
    s = cp.extract_structured(html)
    assert s["name"] == "Smile" and s["same_as"] == ["https://instagram.com/smile"] and "Delhi" in s["address"]


def test_contact_link_discovery_same_site_only():
    html = '<a href="/contact-us">Contact</a><a href="https://other.com/contact">x</a><a href="/privacy">p</a><a href="/about">About</a>'
    links = cp.discover_contact_links(html, "https://shop.com/")
    assert links[0] == "https://shop.com/contact-us" and all("other.com" not in l for l in links)


def test_canonical_domain():
    assert cp.canonical_domain("HTTPS://www.Shop.com/x") == "shop.com"
    assert cp.canonical_domain("shop.com") == "shop.com" and cp.canonical_domain(None) == ""


def test_evidence_caps_and_gaps():
    now = datetime.now(timezone.utc)
    weak = ev.grade_evidence({"business_name": "A", "website_url": "", "niche": "dentist", "city": "Delhi"}, now)
    assert weak["dimensions"]["timing"] <= 2 and weak["dimensions"]["reach"] <= 1 and weak["gaps"]
    strong = ev.grade_evidence({"website_url": "", "website_checked_at": (now - timedelta(days=3)).isoformat(),
        "niche": "dentist", "city": "Delhi", "phone": "9876543210", "phone_valid": 1, "place_id": "p", "source": "google_places"}, now)
    assert strong["grade"] in ("A", "B") and strong["evidence_score"] > weak["evidence_score"]
    closed = ev.grade_evidence({"business_status": "CLOSED_PERMANENTLY", "niche": "x", "city": "y"}, now)
    assert closed["evidence_score"] <= 20


def test_csv_formula_injection_neutralised():
    out = ex.to_csv([{"business_name": "=HYPERLINK(\"http://evil\")", "phone": "+91 1"}])
    assert "'=HYPERLINK" in out and "'+91" in out
