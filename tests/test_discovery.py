"""Pure-logic tests for the discovery toolkit (no database)."""
import math
from datetime import datetime, timezone

import pytest

from app.discovery import csvimport, dorks, grid, lookalike, reviewmine, signals_ext, techstack
from app.discovery.sweep import SweepProvider


def _dist(a, b):
    return math.hypot((a[0]-b[0])*111_000, (a[1]-b[1])*111_000*math.cos(math.radians(a[0])))


def test_grid_covers_disc_and_centre_first():
    cells = grid.grid_cells(28.6, 77.2, 5000, 1500, max_cells=200)
    assert cells[0]["distance_m"] == 0 and len(cells) > 7
    for px, py in [(28.6+0.03, 77.2), (28.6, 77.2-0.03), (28.6-0.02, 77.2+0.02)]:
        assert min(_dist((px, py), (c["lat"], c["lng"])) for c in cells) <= 1500
    assert len(grid.grid_cells(28.6, 77.2, 20000, 500, max_cells=10)) == 10
    with pytest.raises(ValueError):
        grid.grid_cells(0, 0, 0)


def test_sweep_dedupes_and_survives_failing_cell():
    class Inner:
        name = "x"; billed = False; calls = 0
        def search(self, niche, city, area="", country="", max_results=20, lat=None, lng=None, radius_m=None):
            Inner.calls += 1
            if Inner.calls == 2:
                raise RuntimeError("boom")
            return [{"place_id": "a", "business_name": "A"}, {"place_id": f"p{Inner.calls}", "business_name": "B"}]
        def fetch_details(self, c): return {}
        def fetch_ratings(self, c): return {}
    out = SweepProvider(Inner(), 1500, 5).search("cafe", "Delhi", lat=28.6, lng=77.2, radius_m=4000, max_results=50)
    assert len([c for c in out if c["place_id"] == "a"]) == 1 and len(out) >= 3


def test_csv_import_maps_synonyms_and_skips_bad_rows():
    text = "Company Name;Mobile;E-mail;Category;Rating\nAcme Dental;98765 43210;a@acme.in;dentist;4.5\n;1;x@y.com;d;1\n"
    r = csvimport.parse_csv(text)
    assert len(r["candidates"]) == 1 and r["candidates"][0]["phone"] == "98765 43210"
    assert r["candidates"][0]["google_rating"] == 4.5 and r["skipped"][0]["reason"] == "missing business name"
    with pytest.raises(ValueError):
        csvimport.parse_csv("foo,bar\n1,2\n")


def test_techstack():
    f = techstack.fingerprint('<html><script src="jquery-1.9.js"></script><embed src="a.swf"> wixstatic.com',
                              {"x-powered-by": "PHP/5.4"}, https=False)
    names = {s["tech"] for s in f["stack"]}
    assert {"Wix", "Flash", "jQuery (legacy)"} <= names
    assert any("PHP 5" in i for i in f["issues"]) and any("HTTP" in i for i in f["issues"])
    assert f["modernisation_score"] > 40


def test_review_mining_and_velocity():
    r = reviewmine.analyse(["Great food but they never answer the phone", "no website, couldn't find their menu",
                            {"text": "lovely", "rating": 5}])
    assert r["hits"].get("hard_to_reach") == 1 and r["hits"].get("no_website") == 1 and r["quotes"]
    v = reviewmine.review_velocity([{"at": "2026-01-01", "reviews": 10}, {"at": "2026-03-02", "reviews": 50}])
    assert v["rapid"] and v["per_month"] > 15
    assert reviewmine.review_velocity([])["per_month"] is None


def test_rdap_parse_flags():
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    r = signals_ext.parse_rdap({"events": [{"eventAction": "registration", "eventDate": "2026-04-01T00:00:00Z"},
                                           {"eventAction": "expiration", "eventDate": "2026-06-20T00:00:00Z"}]}, now)
    assert set(r["flags"]) == {"NEW_DOMAIN", "DOMAIN_EXPIRING"}


def test_dorks_and_lookalike():
    qs = dorks.generate("dentist", "Pune")
    assert len(qs) >= 8 and all("Pune" in q["query"] and q["url"].startswith("https://www.google.com/search?q=") for q in qs)
    with pytest.raises(ValueError):
        dorks.generate("", "x")
    won = [{"niche": "dentist", "city": "Pune", "website_url": None, "google_reviews": 40}]
    ranked = lookalike.rank(won, [{"id": 1, "niche": "dentist", "city": "Pune", "google_reviews": 38},
                                  {"id": 2, "niche": "bar", "city": "Goa", "website_url": "x", "google_reviews": 900}])
    assert ranked[0]["id"] == 1 and ranked[0]["similarity"] > ranked[1]["similarity"]
    assert lookalike.rank([], [{"id": 1}]) == []
