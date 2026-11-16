"""Free email finder: network and DB are stubbed, so this runs anywhere."""
from app import finder


class _Resp:
    def __init__(self, text="", status=200):
        self.text, self.status_code = text, status


def test_domain_of():
    assert finder.domain_of({"website_url": "https://www.Acme.in/contact"}) == "acme.in"
    assert finder.domain_of({"website_url": None}) == ""


def test_deep_crawl_prefers_own_domain(monkeypatch):
    pages = {"https://acme.in/team": '<a href="mailto:jo@acme.in">x</a> mail spam@gmail.com'}
    monkeypatch.setattr(finder.netsafe, "safe_get",
                        lambda url, **kw: _Resp(pages.get(url, ""), 200 if url in pages else 404))
    got = finder.deep_crawl("https://acme.in")
    assert got and got[0] == "jo@acme.in"


def test_no_mail_server_means_no_guessing(monkeypatch):
    monkeypatch.setattr(finder, "has_mail_server", lambda d: False)
    called = []
    monkeypatch.setattr(finder, "guess", lambda *a: called.append(1) or 1)
    out = finder.find({"id": 1, "website_url": "https://dead.example"})
    assert out == {"email": None, "guessed": 0, "mail_server": False} and not called


def test_found_address_returned_without_guessing(monkeypatch):
    monkeypatch.setattr(finder, "has_mail_server", lambda d: True)
    monkeypatch.setattr(finder, "deep_crawl", lambda u: ["owner@acme.in"])
    monkeypatch.setattr(finder, "guess", lambda *a: (_ for _ in ()).throw(AssertionError))
    assert finder.find({"id": 1, "website_url": "https://acme.in"})["email"] == "owner@acme.in"


def test_foreign_domain_address_not_taken_as_lead_email(monkeypatch):
    monkeypatch.setattr(finder, "has_mail_server", lambda d: True)
    monkeypatch.setattr(finder, "deep_crawl", lambda u: ["x@agency.com"])
    monkeypatch.setattr(finder, "guess", lambda *a: 2)
    out = finder.find({"id": 1, "website_url": "https://acme.in"})
    assert out["email"] is None and out["guessed"] == 2
