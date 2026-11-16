"""stage_social: free social discovery from the site's contact/about pages."""
from types import SimpleNamespace

from app import enrich, website


def _fake_get(pages):
    def get(url, **kw):
        for suffix, html in pages.items():
            if url.rstrip("/").endswith(suffix):
                return SimpleNamespace(status_code=200, text=html)
        return SimpleNamespace(status_code=404, text="")
    return get


def test_finds_social_on_contact_page(monkeypatch):
    pages = {"example.com": '<a href="/contact">Contact</a>',
             "/contact": '<a href="https://www.instagram.com/shop_pune">ig</a>'
                         '<a href="https://www.facebook.com/sharer/x">share</a>'}
    monkeypatch.setattr(website.netsafe, "safe_get", _fake_get(pages))
    out = enrich.stage_social({"website_url": "example.com", "has_website": 1})
    assert out["instagram_url"] == "https://www.instagram.com/shop_pune"
    assert "facebook_url" not in out            # share links are not profiles
    assert out["_source"] == ("website_scrape", "website")


def test_skips_when_nothing_to_find(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not fetch")
    monkeypatch.setattr(website.netsafe, "safe_get", boom)
    assert enrich.stage_social({"website_url": None}) == {}
    assert enrich.stage_social({"website_url": "x.com", "has_website": 0}) == {}
    full = {"website_url": "x.com", "has_website": 1, "instagram_url": "a",
            "facebook_url": "b", "linkedin_url": "c"}
    assert enrich.stage_social(full) == {}
