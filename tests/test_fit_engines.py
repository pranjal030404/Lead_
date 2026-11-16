"""Pure-logic tests: ICP inference/fit, feedback learning, intent feed."""
from datetime import datetime, timedelta, timezone

from app import feedback, icp
from app.discovery import intent_feed as ifd

SITE = """<html><head><title>Pixel Forge - Websites for Dentists</title>
<meta name="description" content="We design and build fast websites and run local SEO for dentists and clinics."></head>
<body><h1>Websites for dentists that bring in patients</h1><h2>Web design, WordPress development and local SEO</h2>
<p>We help dental clinics and dentists grow with Google ranking improvements.</p></body></html>"""


def test_infer_icp():
    r = icp.infer_icp(SITE, "https://pixelforge.example")
    assert "web design" in r["offerings"] and "seo" in r["offerings"]
    assert "dentist" in r["target_niches"] or "dental" in r["target_niches"]
    assert r["confidence"] > 0.5 and r["summary"].startswith("Websites for dentists")
    empty = icp.infer_icp("<html></html>")
    assert empty["offerings"] == [] and empty["confidence"] < 0.5


def test_normalise_and_fit():
    prof = icp.normalise_icp({"offerings": ["Web Design", "web design", "SEO"], "target_niches": ["Dentist"],
                              "target_cities": ["Pune"], "min_reviews": "20"})
    assert prof["offerings"] == ["web design", "seo"] and prof["min_reviews"] == 20
    good = icp.fit_score({"niche": "dentist", "city": "Pune", "website_url": "", "google_reviews": 50}, prof)
    bad = icp.fit_score({"niche": "bar", "city": "Goa", "website_url": "x.com", "website_score": 9, "google_reviews": 2}, prof)
    assert good["score"] >= 85 and bad["score"] < 40 and good["reasons"]
    assert icp.fit_score({"niche": "x"}, None)["score"] is None
    assert icp.fit_score({"niche": "x"}, {"offerings": [], "target_niches": []})["configured"] is False


def _lead(niche, good_site=False):
    return {"niche": niche, "city": "Pune", "website_url": "x.com" if good_site else "", "website_score": 9 if good_site else None,
            "google_reviews": 30, "lead_priority": "HOT"}


def test_feedback_learns_and_is_bounded():
    ex = [(_lead("dentist"), True)] * 6 + [(_lead("bar"), False)] * 6 + [(_lead("cafe"), True), (_lead("cafe"), False)]
    adj = feedback.learn(ex)
    assert adj["niche:dentist"]["mult"] > 1.1 and adj["niche:bar"]["mult"] < 0.9
    assert all(feedback.MULT_MIN <= a["mult"] <= feedback.MULT_MAX for a in adj.values())
    assert feedback.learn([]) == {}
    # tiny samples never move anything
    assert "niche:rare" not in feedback.learn(ex + [(_lead("rare"), False), (_lead("rare"), False)])


def test_adjust_directions():
    adj = feedback.learn([(_lead("dentist"), True)] * 6 + [(_lead("bar"), False)] * 6)
    up = feedback.adjust(60, _lead("dentist"), adj); down = feedback.adjust(60, _lead("bar"), adj)
    assert up["score"] > 60 > down["score"] and down["reasons"]
    assert feedback.adjust(None, _lead("bar"), adj)["score"] is None
    assert feedback.adjust(60, _lead("bar"), {})["score"] == 60


NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)


def test_intent_scoring_and_provider_filter():
    fresh = (NOW - timedelta(days=2)).isoformat()
    hot = ifd.score_hit("Looking for a web designer for my restaurant, budget around $2k, need it asap", fresh, NOW)
    assert hot["score"] >= 60 and hot["signals"] and not hot["provider"]
    old = ifd.score_hit("Looking for a web designer", (NOW - timedelta(days=200)).isoformat(), NOW)
    assert old["score"] < hot["score"]
    prov = ifd.score_hit("I'm a freelance web designer, available for hire. Looking for clients.", fresh, NOW)
    assert prov["provider"] and prov["score"] <= 10


def test_queries_and_parse():
    qs = ifd.build_queries(["web design", "seo"])
    assert "looking for a web designer" in qs and all(len(q) < 60 for q in qs)
    payload = {"hits": [{"objectID": "42", "author": "pg", "created_at": (NOW - timedelta(days=1)).isoformat(),
                         "comment_text": "<p>Need a <b>web developer</b> for our shop &amp; quote please</p>"},
                        {"objectID": None, "comment_text": "x"}, {"objectID": "7", "comment_text": ""}]}
    items = ifd.parse_hits(payload, "need a web developer", NOW)
    assert len(items) == 1 and items[0]["url"].endswith("id=42") and "<" not in items[0]["snippet"] and "&amp;" not in items[0]["snippet"]


def test_is_request_precision():
    yes = ["We are looking for a web designer for our clinic, budget is flexible",
           "Can anyone recommend a good SEO agency in Pune?",
           "I need a web developer to rebuild my shop's site",
           "Does anyone know a freelance web designer who does WordPress?"]
    no = ["There's an analogy in woodworking -- rich people go to craftspeople. A web designer would hire a carpenter",
          "That falsely turns your experience into an imagined generality, hire web developers who agree",
          "Web designers are looking for new tools this year",
          "I'm a freelance web designer and I'd love to hire an assistant"]
    assert all(ifd.is_request(t, ["web design", "web development", "seo"]) for t in yes)
    assert not any(ifd.is_request(t, ["web design", "web development", "seo"]) for t in no)
    assert not ifd.is_request("anything", [])


def test_mastodon_parse_filters():
    posts = [{"id": "1", "url": "https://x.social/@a/1", "language": "en", "created_at": (NOW - timedelta(days=1)).isoformat(),
              "content": "<p>We need a web designer for our bakery - anyone to recommend?</p>", "account": {"acct": "bakery@x.social"}},
             {"id": "2", "url": "https://x.social/@b/2", "language": "fr", "content": "On cherche un web designer", "account": {"acct": "b"}},
             {"id": "3", "url": "https://x.social/@c/3", "language": "en", "reblog": {"id": "9"}, "content": "boost", "account": {"acct": "c"}},
             {"id": "4", "url": "https://x.social/@d/4", "language": "en", "created_at": (NOW - timedelta(days=1)).isoformat(),
              "content": "Nice web design trends this year", "account": {"acct": "d"}}]
    items = ifd.parse_mastodon(posts, "webdesign", NOW, ["web design"])
    by = {i["id"]: i for i in items}
    assert set(by) == {"m:1", "m:4"} and by["m:1"]["score"] >= 20 and by["m:4"]["score"] == 0
    assert by["m:1"]["source"] == "Mastodon" and "<" not in by["m:1"]["snippet"]


def test_icp_exclusions_and_roles():
    prof = icp.normalise_icp({"offerings": ["web design"], "target_niches": ["dentist", "bar"], "exclude_niches": ["Bar"],
                              "exclude_keywords": ["Franchise"], "target_roles": ["Owner"]})
    assert prof["exclude_niches"] == ["bar"] and prof["target_roles"] == ["owner"]
    ex = icp.fit_score({"niche": "bar", "city": "x", "website_url": ""}, prof)
    assert ex["score"] == 0 and ex["excluded"] and "exclude" in ex["reasons"][0]
    assert icp.fit_score({"niche": "dentist", "business_name": "Smile Franchise Outlet"}, prof)["score"] == 0
    a = icp.fit_score({"niche": "dentist", "owner_name": "Dr Rao, Owner", "website_url": ""}, prof)
    b = icp.fit_score({"niche": "dentist", "website_url": ""}, prof)
    assert a["score"] == b["score"] + 5
