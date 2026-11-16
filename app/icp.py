"""Ideal-client profile (ICP): learn what you sell and who buys it, then score fit.

`infer_icp()` reads *your own* website (no AI service, fully explainable) and proposes:
  offerings       - services you sell, matched against a service vocabulary
  target_niches   - business types your pages talk to ("websites for dentists")
  keywords        - your most distinctive words
  summary         - headline + description
The proposal is a starting point: the operator edits and saves it. `fit_score()` then rates
any lead 0-100 against the saved profile and says why.
"""

from __future__ import annotations

import html as htmllib
import json
import re
from collections import Counter

from .providers.osm import NICHE_TAGS

SERVICES = [  # (label, regex) - what a studio/agency might sell
    ("web design", r"web ?(site)? ?design|website design|ui/?ux|landing pages?"),
    ("web development", r"web ?(site)? ?development|web developers?|full[- ]?stack|react|wordpress|shopify|webflow|custom (software|web)"),
    ("e-commerce", r"e-?commerce|online store|online shop|woocommerce|shopify"),
    ("seo", r"\bseo\b|search engine optimi[sz]ation|local seo|google ranking"),
    ("digital marketing", r"digital marketing|performance marketing|google ads|facebook ads|meta ads|ppc|lead generation"),
    ("social media", r"social media (management|marketing)|instagram (growth|management)|content creation"),
    ("branding", r"branding|brand identity|logo design|graphic design"),
    ("app development", r"(mobile|android|ios) app|app development|flutter|react native"),
    ("automation / AI", r"automation|ai agents?|chatbots?|workflow automation|\bai\b"),
    ("video / photography", r"video production|photography|photo ?shoots?|reels"),
    ("consulting", r"consult(ing|ancy)|strategy|advisory"),
]
STOP = set("the a an and or of to in for on with your you our we is are be as at by it this that from will can all more "
           "get best new how what why who not no us about home contact services service solutions company team".split())
NICHE_WORDS = sorted({kw for kws, _ in NICHE_TAGS for kw in kws if len(kw) > 3})


def _text_of(page_html: str) -> dict:
    h = page_html or ""
    def grab(pattern):
        return [re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", m))).strip()
                for m in re.findall(pattern, h, re.I | re.S)]
    title = (grab(r"<title[^>]*>(.*?)</title>") or [""])[0]
    meta = re.findall(r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\']([^"\']+)', h, re.I)
    meta += re.findall(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\'](?:description|og:description)["\']', h, re.I)
    headings = grab(r"<h[1-3][^>]*>(.*?)</h[1-3]>")
    body = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", h, flags=re.I | re.S)
    body = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", body)))
    return {"title": title, "meta": htmllib.unescape(meta[0]).strip() if meta else "",
            "headings": [x for x in headings if 3 < len(x) < 160][:30], "body": body[:60000]}


def infer_icp(page_html: str, url: str = "") -> dict:
    t = _text_of(page_html)
    corpus = " ".join([t["title"], t["meta"], " ".join(t["headings"]), t["body"]]).lower()
    weighted = (" ".join([t["title"], t["meta"], " ".join(t["headings"])]).lower() + " ") * 3 + corpus

    offerings = []
    for label, rx in SERVICES:
        n = len(re.findall(rx, weighted, re.I))
        if n:
            offerings.append((n, label))
    offerings = [l for _, l in sorted(offerings, reverse=True)][:5]

    # Audiences: niche words appearing in "for <niche>" / "<niche> owners" / headings.
    hits: Counter = Counter()
    for w in NICHE_WORDS:
        for m in re.finditer(rf"(?:for|serving|helping|to)\s+(?:\w+\s+){{0,2}}{re.escape(w)}\w*|{re.escape(w)}\w*\s+(?:owners?|businesses|clinics?|brands?)", weighted):
            hits[w] += 3
        hits[w] += len(re.findall(rf"\b{re.escape(w)}\w*", corpus)) if w in corpus else 0
    niches = [w for w, c in hits.most_common(8) if c >= 2][:6]

    words = [w for w in re.findall(r"[a-z][a-z\-]{3,}", corpus) if w not in STOP]
    keywords = [w for w, _ in Counter(words).most_common(12)]
    evidence = [h for h in t["headings"] if any(l.split()[0] in h.lower() for l in offerings)][:4] or t["headings"][:3]
    conf = min(0.95, 0.25 + 0.15 * len(offerings) + 0.1 * len(niches) + (0.1 if t["meta"] else 0))
    return {"url": url, "offerings": offerings, "target_niches": niches, "target_cities": [],
            "keywords": keywords, "min_reviews": 0,
            "summary": (t["headings"][0] if t["headings"] else t["title"])[:200],
            "description": t["meta"][:300], "evidence": evidence,
            "confidence": round(conf, 2),
            "note": "Proposed from your site's wording - edit it before relying on it."}


def normalise_icp(raw: dict) -> dict:
    """Validate/clean operator-supplied ICP JSON."""
    def lst(v, n=12):
        return list(dict.fromkeys(str(x).strip().lower()[:60] for x in (v or []) if str(x).strip()))[:n]
    try:
        mr = max(0, min(int(raw.get("min_reviews") or 0), 100000))
    except (TypeError, ValueError):
        mr = 0
    return {"url": str(raw.get("url") or "")[:300], "offerings": lst(raw.get("offerings")),
            "target_niches": lst(raw.get("target_niches")), "target_cities": lst(raw.get("target_cities")),
            "keywords": lst(raw.get("keywords"), 20), "min_reviews": mr,
            "target_roles": lst(raw.get("target_roles")), "exclude_niches": lst(raw.get("exclude_niches")),
            "exclude_keywords": lst(raw.get("exclude_keywords")),
            "summary": str(raw.get("summary") or "")[:200], "description": str(raw.get("description") or "")[:300]}


WEB_OFFERS = {"web design", "web development", "e-commerce", "app development"}
GROWTH_OFFERS = {"seo", "digital marketing", "social media"}


def fit_score(lead: dict, icp: dict | None) -> dict:
    """0-100 fit of one lead against the saved ICP, with reasons. Pure."""
    if not icp or not (icp.get("offerings") or icp.get("target_niches")):
        return {"score": None, "reasons": ["No ideal-client profile saved yet."], "configured": False}
    score, reasons = 0, []
    niche = (lead.get("niche") or "").lower()
    name = (lead.get("business_name") or "").lower()
    for ex in icp.get("exclude_niches") or []:
        if ex and niche and (ex in niche or niche in ex):
            return {"score": 0, "reasons": [f"excluded: niche '{niche}' is on your exclude list"], "configured": True,
                    "excluded": True}
    for kw in icp.get("exclude_keywords") or []:
        if kw and kw in name:
            return {"score": 0, "reasons": [f"excluded: name contains '{kw}'"], "configured": True, "excluded": True}
    targets = icp.get("target_niches") or []
    if targets:
        if niche and any(t in niche or niche in t for t in targets):
            score += 45; reasons.append(f"niche '{niche}' is one you target (+45)")
        else:
            reasons.append(f"niche '{niche or 'unknown'}' isn't in your target list (+0)")
    else:
        score += 22; reasons.append("no target niches set - niche is neutral (+22)")

    cities = icp.get("target_cities") or []
    city = (lead.get("city") or "").lower()
    if cities:
        if city and any(c in city or city in c for c in cities):
            score += 15; reasons.append(f"in your target city {city} (+15)")
        else:
            reasons.append("outside your target cities (+0)")
    else:
        score += 8; reasons.append("no target cities set - location is neutral (+8)")

    offers = set(icp.get("offerings") or [])
    ws, listed = lead.get("website_score"), bool((lead.get("website_url") or "").strip())
    gap = (not listed) or lead.get("has_website") == 0 or (ws is not None and ws < 7)
    if offers & WEB_OFFERS:
        if gap:
            score += 30; reasons.append("they have a web gap and you sell web work (+30)")
        else:
            score += 6; reasons.append("their site looks fine - weaker web-work opportunity (+6)")
    elif offers & GROWTH_OFFERS:
        if listed:
            score += 22; reasons.append("they have a site to grow traffic for (+22)")
        else:
            score += 8; reasons.append("no site yet, so growth work comes second (+8)")
    elif offers:
        score += 14; reasons.append("offer is general - moderate fit (+14)")

    roles = icp.get("target_roles") or []
    if roles:
        owner = (lead.get("owner_name") or "").lower()
        if owner and any(r in owner for r in roles):
            score += 5; reasons.append(f"contact matches a target role (+5)")
    need = int(icp.get("min_reviews") or 0)
    reviews = lead.get("google_reviews")
    if need:
        if reviews is not None and reviews >= need:
            score += 10; reasons.append(f"{reviews} reviews meets your {need}+ minimum (+10)")
        else:
            reasons.append(f"fewer than {need} reviews (+0)")
    else:
        score += 5
    return {"score": max(0, min(100, score)), "reasons": reasons, "configured": True}
