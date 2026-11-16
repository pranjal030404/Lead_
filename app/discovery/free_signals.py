"""Free, key-less buying-intent engines.

hiring   - does the business advertise digital/marketing roles? Reads the company's own
           careers page (works for small local firms that never post on job boards) and
           two open job feeds (Arbeitnow, Remotive - no key).
ads      - does it pay for traffic? Detects advertising pixels/tags on its own pages
           (Google Ads, Meta, LinkedIn, TikTok, Bing, X, Pinterest). A site running paid
           ads but with a weak page or no tracking is wasting spend - a strong pitch.

Parsers are pure; only `hiring_*_live` / `fetch_page` touch the network (via netsafe).
"""

from __future__ import annotations

import html as htmllib
import re

from .. import http, netsafe
from ..contactparse import ANCHOR_RE, TAG_RE, canonical_domain
from urllib.parse import urljoin

DIGITAL_ROLES = re.compile(
    r"\b(web ?(site )?(developer|designer)|front[- ]?end|full[- ]?stack|wordpress|shopify|ui/?ux|graphic designer|"
    r"digital marketing|seo( specialist| executive| manager)?|social media (manager|executive|marketer)|"
    r"performance marketer|content (writer|marketer)|e-?commerce (manager|executive)|growth marketer|"
    r"marketing (executive|manager|associate|intern))\b", re.I)
CAREER_HINTS = ("career", "jobs", "job-", "hiring", "we-are-hiring", "join-us", "join-our", "work-with-us",
                "vacanc", "openings", "recruit", "karir", "lowongan")


def careers_links(page_html: str, base_url: str, limit: int = 3) -> list[str]:
    base = canonical_domain(base_url)
    found: list[str] = []
    for href, inner in ANCHOR_RE.findall(page_html or ""):
        absolute = urljoin(base_url, htmllib.unescape(href).split("#")[0])
        label = absolute.lower() + " " + TAG_RE.sub("", inner).lower()
        if canonical_domain(absolute) == base and any(h in label for h in CAREER_HINTS) \
                and absolute not in found and absolute.startswith(("http://", "https://")):
            found.append(absolute)
    return found[:limit]


def digital_roles_in(text: str) -> list[str]:
    plain = TAG_RE.sub(" ", htmllib.unescape(text or ""))
    return list(dict.fromkeys(m.group(0).lower() for m in DIGITAL_ROLES.finditer(plain)))[:8]


def match_company_jobs(jobs: list[dict], company: str, name_key: str, title_key: str) -> list[dict]:
    """Jobs from a feed whose company matches `company` and whose title is a digital role."""
    target = re.sub(r"[^a-z0-9]", "", (company or "").lower())
    if len(target) < 3:
        return []
    out = []
    for j in jobs:
        cname = re.sub(r"[^a-z0-9]", "", str(j.get(name_key) or "").lower())
        if cname and (cname == target or (len(target) >= 5 and (target in cname or cname in target))) \
                and DIGITAL_ROLES.search(str(j.get(title_key) or "")):
            out.append({"title": j.get(title_key), "source": None, "url": j.get("url")})
    return out


def fetch_page(url: str, timeout: int = 7) -> str:
    try:
        r = netsafe.safe_get(url, breaker="website_check", retries=1, timeout=timeout)
        return r.text if r.status_code < 400 else ""
    except Exception:  # noqa: BLE001 - a page that won't load is simply "no evidence"
        return ""


def hiring(company: str, website: str | None = None) -> dict:
    evidence, checked = [], []
    if website:
        base = website if "//" in website else "https://" + website
        home = fetch_page(base)
        checked.append("own careers page")
        for url in careers_links(home, base):
            roles = digital_roles_in(fetch_page(url))
            if roles:
                evidence.append({"source": "company careers page", "roles": roles, "url": url})
                break
    if company:
        for label, url, params, listkey, nk, tk in (
            ("Arbeitnow", "https://www.arbeitnow.com/api/job-board-api", {}, "data", "company_name", "title"),
            ("Remotive", "https://remotive.com/api/remote-jobs", {"search": company}, "jobs", "company_name", "title"),
        ):
            checked.append(label)
            try:
                r = http.get(url, breaker=label.lower(), params=params, retries=1, timeout=10)
                if r.status_code < 400:
                    hits = match_company_jobs(r.json().get(listkey, []), company, nk, tk)
                    if hits:
                        evidence.append({"source": label, "roles": [h["title"] for h in hits[:5]],
                                         "url": hits[0].get("url")})
            except Exception:  # noqa: BLE001
                continue
    return {"hiring_digital": bool(evidence), "evidence": evidence, "checked": checked,
            "note": "Absence is not proof - small firms often recruit by word of mouth."}


# ------------------------------------------------------------------ ad footprint --
AD_SIGNATURES = [  # (platform, regex, what it proves)
    ("Google Ads", r"googleadservices\.com|AW-\d{6,}|google_conversion|gtag\(\s*['\"]config['\"]\s*,\s*['\"]AW-|doubleclick\.net", "Google Ads conversion tracking"),
    ("Meta (Facebook/Instagram) Ads", r"connect\.facebook\.net/[^'\"]*fbevents\.js|fbq\(\s*['\"]init|facebook\.com/tr\?", "Meta Pixel - retargeting/ads"),
    ("LinkedIn Ads", r"snap\.licdn\.com|_linkedin_partner_id|linkedin\.com/px", "LinkedIn Insight Tag"),
    ("TikTok Ads", r"analytics\.tiktok\.com|ttq\.load", "TikTok Pixel"),
    ("Microsoft/Bing Ads", r"bat\.bing\.com|uetq", "Microsoft Advertising UET tag"),
    ("X (Twitter) Ads", r"static\.ads-twitter\.com|twq\(", "X conversion pixel"),
    ("Pinterest Ads", r"s\.pinimg\.com/ct/core\.js|pintrk\(", "Pinterest Tag"),
]
_AD_RX = [(n, re.compile(p, re.I), w) for n, p, w in AD_SIGNATURES]


def ad_footprint(page_html: str) -> dict:
    """Pure. Which ad platforms does this page report to? Strong evidence of paid activity
    only for conversion tags; a bare analytics tag is not counted."""
    platforms = [{"platform": n, "evidence": w} for n, rx, w in _AD_RX if rx.search(page_html or "")]
    analytics = bool(re.search(r"google-analytics\.com|googletagmanager\.com|gtag\(", page_html or "", re.I))
    gaps = []
    if platforms and not analytics:
        gaps.append("runs ad pixels but has no analytics - cannot measure ROI")
    if platforms and not re.search(r"<meta[^>]+viewport", page_html or "", re.I):
        gaps.append("pays for traffic to a page that isn't mobile-friendly")
    if platforms and not re.search(r"(tel:|wa\.me|api\.whatsapp|<form)", page_html or "", re.I):
        gaps.append("ad traffic lands on a page with no phone link, WhatsApp link or form")
    return {"advertising": bool(platforms), "platforms": platforms, "wasted_spend_signals": gaps,
            "confidence": "high" if len(platforms) >= 2 else "medium" if platforms else "none"}
