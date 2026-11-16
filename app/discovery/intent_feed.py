"""Intent feed: public posts where someone is asking for what you sell.

Sources: Hacker News (open Algolia API) and Mastodon public tag timelines - no key, no scraping, no login. Queries are
built from your ICP offerings ("looking for a web designer", "can anyone recommend an SEO
agency"). Each hit is scored for buying intent and recency, and posts written by *providers*
("I'm a freelance designer, available for hire") are filtered out so you don't pitch competitors.
Parsing and scoring are pure; only `search()` touches the network.
"""

from __future__ import annotations

import html as htmllib
import re
from datetime import datetime, timedelta, timezone

from .. import http

API = "https://hn.algolia.com/api/v1/search_by_date"
ROLE_FOR = {"web design": "web designer", "web development": "web developer", "e-commerce": "ecommerce developer",
            "seo": "SEO help", "digital marketing": "digital marketing agency", "social media": "social media manager",
            "branding": "branding designer", "app development": "mobile app developer",
            "automation / ai": "automation consultant", "video / photography": "videographer",
            "consulting": "consultant"}
TEMPLATES = ('looking for a {r}', 'need a {r}', 'recommend a {r}', 'hiring a {r}')
INTENT = [(r"looking for|in need of|\bneed(ed)?\b|searching for|want to hire|hire (a|an)|can anyone recommend|"
           r"recommend(ations?)? (for|a)|anyone know (a|of)|who (do|should) (you|i) use", 3),
          (r"budget|quote|proposal|rfp|asap|urgent|this week|by (end of )?(the )?month", 2),
          (r"freelanc|contract|small business|my (shop|restaurant|clinic|store|startup)", 1)]
PROVIDER = re.compile(r"available for (hire|work|projects)|i('m| am) a (freelance|full[- ]?stack|web|seo)|"
                      r"we (offer|provide|specialize|build for)|our (agency|services|team) (can|will|offers)|"
                      r"seeking work|looking for (work|clients|gigs)|hire me|my services", re.I)


ROLE_RX = {
    "web design": r"web ?(site)? ?design(er|ers|ing)?|website (designer|builder)|ui/?ux designer",
    "web development": r"web ?(site)? ?developers?|full[- ]?stack developers?|wordpress (developer|expert|help)|front[- ]?end developers?|website (built|developer)",
    "e-commerce": r"e-?commerce (developer|expert|help)|shopify (developer|expert)|online store",
    "seo": r"\bseo\b( (help|expert|agency|consultant|specialist|freelancer))?",
    "digital marketing": r"(digital|performance|growth) marketing (agency|expert|consultant|help)|marketing agency|ppc (expert|agency)|ads? (manager|agency)",
    "social media": r"social media (manager|marketer|help|agency)",
    "branding": r"brand(ing)? (designer|agency|help)|logo designer",
    "app development": r"(mobile|ios|android|flutter|react native) (app )?developers?|app developers?",
    "automation / ai": r"automation (consultant|expert|developer)|ai (consultant|developer|agency)|chatbot (developer|builder)",
    "video / photography": r"videographers?|video (editor|producer)|photographers?",
    "consulting": r"consultants?",
}
REQUEST_VERB = (r"(looking for|in need of|\bneed(s|ed)?\b|searching for|want(ed)? to hire|\bhir(?:e|ing)\b|"
                r"recommend(ation)?s?( for)?|\bknow (?:a|an|of|any|someone|somebody)\b|suggest(?:ions?)?( for)?|"
                r"where (can|do) (i|we) find|how (do|can) (i|we) find)")


# A request is first-person or addressed to the crowd: "we need", "I'm looking for", "anyone recommend".
ASK_CUE = (r"(?:\b(?:i|we|i'm|we're|i am|we are|my|our|anyone|anybody|someone|please|does anyone|can anyone|"
           r"can someone|could someone|any)\b[\w' ,]{0,25}?)")


def is_request(text: str, offerings: list[str]) -> bool:
    """True only when a request phrase sits within ~70 chars of a term for what you sell."""
    low = (text or "").lower()
    for o in offerings:
        role = ROLE_RX.get(o.lower()) or re.escape(ROLE_FOR.get(o.lower(), o).lower())
        if re.search(rf"{ASK_CUE}{REQUEST_VERB}[^.!?\n]{{0,70}}?(?:{role})", low):
            return True
    return False


def build_queries(offerings: list[str], limit: int = 6) -> list[str]:
    roles = [ROLE_FOR.get(o.lower(), o) for o in offerings if o]
    qs = [t.format(r=r) for r in roles[:3] for t in TEMPLATES]
    return list(dict.fromkeys(qs))[:limit]


def clean(text: str, n: int = 320) -> str:
    t = re.sub(r"<[^>]+>", " ", htmllib.unescape(text or ""))
    return re.sub(r"\s+", " ", t).strip()[:n]


def score_hit(text: str, created_at: str | None, now: datetime | None = None) -> dict:
    """-> {score 0-100, signals[], provider(bool)}. Pure."""
    now = now or datetime.now(timezone.utc)
    low = (text or "").lower()
    provider = bool(PROVIDER.search(text or ""))
    pts, sig = 0, []
    for rx, w in INTENT:
        if re.search(rx, low):
            pts += w; sig.append(re.search(rx, low).group(0))
    score = min(100, pts * 14)
    try:
        d = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        age = max(0, (now - d).days)
        score = round(score * (1.0 if age <= 7 else 0.8 if age <= 30 else 0.55 if age <= 90 else 0.3))
    except (ValueError, TypeError):
        score = round(score * 0.5)
    if provider:
        score = min(score, 10)
    return {"score": score, "signals": sig[:3], "provider": provider}


def parse_hits(payload: dict, query: str, now: datetime | None = None,
               offerings: list[str] | None = None) -> list[dict]:
    out = []
    for h in payload.get("hits", []):
        text = clean(h.get("comment_text") or h.get("story_text") or h.get("title") or "", 600)
        if not text or h.get("objectID") is None:
            continue
        s = score_hit(text, h.get("created_at"), now)
        if offerings and not is_request(text, offerings):
            s = {"score": 0, "signals": [], "provider": s["provider"]}
        out.append({"id": str(h["objectID"]), "author": h.get("author"), "query": query,
                    "url": f"https://news.ycombinator.com/item?id={h['objectID']}",
                    "snippet": text[:320], "created_at": h.get("created_at"), "source": "Hacker News", **s})
    return out


ROLE_TAGS = {"web design": ["webdesign", "webdesigner"], "web development": ["webdev", "webdeveloper"],
             "e-commerce": ["ecommerce", "shopify"], "seo": ["seo"],
             "digital marketing": ["digitalmarketing"], "social media": ["socialmedia"],
             "branding": ["branding", "logodesign"], "app development": ["appdev", "mobiledev"],
             "automation / ai": ["automation"], "video / photography": ["videography", "photography"],
             "consulting": ["consulting"]}
MASTODON = "https://mastodon.social/api/v1/timelines/tag/{tag}"


def parse_mastodon(posts: list[dict], tag: str, now: datetime | None = None,
                   offerings: list[str] | None = None) -> list[dict]:
    """Pure: Mastodon status JSON -> intent items (English posts only)."""
    out = []
    for p in posts:
        if p.get("language") not in (None, "en") or p.get("reblog") or not p.get("url"):
            continue
        text = clean(p.get("content") or "", 600)
        if not text:
            continue
        s = score_hit(text, p.get("created_at"), now)
        if offerings and not is_request(text, offerings):
            s = {"score": 0, "signals": [], "provider": s["provider"]}
        acct = (p.get("account") or {}).get("acct") or "unknown"
        out.append({"id": f"m:{p.get('id')}", "author": acct, "query": f"#{tag}", "url": p["url"],
                    "snippet": text[:320], "created_at": p.get("created_at"), "source": "Mastodon", **s})
    return out


def _mastodon(offerings: list[str], days: int, results: dict, errors: list) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    tags = list(dict.fromkeys(t for o in offerings for t in ROLE_TAGS.get(o.lower(), [])))[:5] + ["hiring"]
    for tag in tags:
        max_id = None
        for _ in range(2):                                       # two pages of 40 per tag
            try:
                r = http.get(MASTODON.format(tag=tag), breaker="mastodon", retries=1, timeout=12,
                             params={"limit": 40, **({"max_id": max_id} if max_id else {})})
                if r.status_code >= 400:
                    errors.append(f"#{tag}: HTTP {r.status_code}"); break
                posts = r.json()
                for item in parse_mastodon(posts, tag, offerings=offerings):
                    results.setdefault(item["id"], item)
                if not posts:
                    break
                max_id = posts[-1].get("id")
                oldest = datetime.fromisoformat(posts[-1]["created_at"].replace("Z", "+00:00"))
                if oldest < cutoff:
                    break
            except Exception as exc:  # noqa: BLE001
                errors.append(f"#{tag}: {exc}"); break


def search(offerings: list[str], days: int = 30, per_query: int = 15) -> dict:
    since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    queries = build_queries(offerings)
    results, errors = {}, []
    for q in queries:
        try:
            r = http.get(API, breaker="hn_algolia", retries=1, timeout=12, params={
                "query": q, "tags": "(story,comment)", "hitsPerPage": per_query, "typoTolerance": "min",
                "numericFilters": f"created_at_i>{since}"})
            if r.status_code < 400:
                for item in parse_hits(r.json(), q, offerings=offerings):
                    results.setdefault(item["id"], item)
            else:
                errors.append(f"{q}: HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{q}: {exc}")
    _mastodon(offerings, days, results, errors)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    items = [i for i in results.values() if not i["provider"] and i["score"] >= 20
             and (i["created_at"] or "") >= cutoff[:10]]
    items.sort(key=lambda i: (-i["score"], i["created_at"] or ""))
    return {"queries": queries, "items": items[:40], "errors": errors, "scanned": len(results),
            "note": "Public posts from Hacker News and Mastodon. Read the post and reply helpfully - don't spam."}
