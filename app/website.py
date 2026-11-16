"""Website analysis (Part 1.3 Layer 2, Part 9.3).

A plain HTTP request plus HTML parsing - no paid API. Gives reachability, SSL,
mobile-friendliness, speed, platform, staleness, contact routes, plus any
social links and mailto: addresses on the page (free email discovery).
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from . import http, netsafe, people
from .config import settings

PLATFORM_SIGNATURES = [
    ("WordPress", (r"wp-content", r"wp-includes", r'name="generator" content="WordPress')),
    ("Wix", (r"wix\.com", r"wixstatic", r"_wixCssImports")),
    ("Squarespace", (r"squarespace", r"static1\.squarespace\.com")),
    ("Shopify", (r"cdn\.shopify\.com", r"Shopify\.theme")),
    ("Webflow", (r"webflow\.com", r"data-wf-page")),
    ("Google Business Site", (r"business\.site", r"gstatic\.com/business")),
    ("GoDaddy Builder", (r"godaddysites\.com", r"img1\.wsimg\.com")),
    ("Weebly", (r"weebly\.com", r"weeblysite")),
    ("Next.js", (r"__NEXT_DATA__", r"/_next/static")),
    ("React", (r"react(-dom)?(\.production)?\.min\.js", r"data-reactroot")),
]

SOCIAL_PATTERNS = {
    "instagram_url": r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9_.]+",
    "facebook_url": r"https?://(?:www\.)?facebook\.com/[A-Za-z0-9_.\-/]+",
    "linkedin_url": r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in)/[A-Za-z0-9_.\-]+",
}

from . import contactparse  # noqa: E402

MAILTO_RE = re.compile(r"mailto:([^\"'?>\s]+)", re.I)
EMAIL_IN_TEXT_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
YEAR_RE = re.compile(r"(?:©|&copy;|copyright)[^0-9]{0,20}((?:19|20)\d{2})", re.I)
VIEWPORT_RE = re.compile(r'<meta[^>]+name=["\']viewport["\']', re.I)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)

CONTACT_PATHS = ("/contact", "/contact-us", "/about", "/about-us")


def _normalise_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _detect_platform(html: str) -> str | None:
    for platform, patterns in PLATFORM_SIGNATURES:
        for pattern in patterns:
            if re.search(pattern, html, re.I):
                return platform
    return None


def _find_socials(html: str) -> dict:
    found = {}
    for field, pattern in SOCIAL_PATTERNS.items():
        match = re.search(pattern, html, re.I)
        if match:
            url = match.group(0).rstrip("/\"'")
            if not re.search(r"/(sharer|share|plugins|tr\?|login)", url, re.I):
                found[field] = url
    return found


def _find_emails(html: str) -> list[str]:
    return contactparse.extract_emails(html)[:5]


def check_website(url: str | None) -> dict:
    """Analyse a site. Never raises - a dead site is a result, not an error."""
    result = {
        "website_exists": 0,
        "website_ssl": 0,
        "website_mobile_friendly": 0,
        "website_speed_ms": None,
        "website_platform": None,
        "website_last_updated": None,
        "website_score": 0,
        "website_issues": [],
        "socials": {},
        "emails": [],
        "people": [],
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if not url:
        result["website_issues"] = ["No website at all"]
        return result

    target = _normalise_url(url)
    started = time.perf_counter()
    try:
        response = netsafe.safe_get(target, breaker="website_check", retries=2,
                                    timeout=settings.http_timeout)
    except netsafe.UnsafeURL:
        result["website_issues"] = ["Website address is not a public internet address"]
        return result
    except Exception:  # noqa: BLE001 - unreachable is a finding, not a crash
        # Phrased to complete the sentence "your site ..." in outreach copy.
        result["website_issues"] = ["Doesn't load at all - the domain isn't responding"]
        return result

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    result["website_speed_ms"] = elapsed_ms

    if response.status_code >= 400:
        result["website_issues"] = [
            f"Returns an HTTP {response.status_code} error instead of a page"
        ]
        return result

    html = response.text or ""
    final_url = str(response.url)
    result["website_exists"] = 1
    result["website_ssl"] = 1 if urlparse(final_url).scheme == "https" else 0
    result["website_mobile_friendly"] = 1 if VIEWPORT_RE.search(html) else 0
    result["website_platform"] = _detect_platform(html)
    result["socials"] = _find_socials(html)
    result["emails"] = _find_emails(html)
    result["people"] = people.extract_people(html)

    years = [int(y) for y in YEAR_RE.findall(html)]
    if years:
        result["website_last_updated"] = str(max(years))

    issues: list[str] = []
    score = 4  # baseline for a site that loads at all

    if result["website_ssl"]:
        score += 2
    else:
        issues.append("Has no HTTPS - browsers show it as 'Not secure'")

    if result["website_mobile_friendly"]:
        score += 2
    else:
        issues.append("Is not mobile-friendly - no viewport tag, so it won't scale on a phone")

    if elapsed_ms < 1500:
        score += 2
    elif elapsed_ms < 3500:
        score += 1
    else:
        issues.append(f"Takes {elapsed_ms / 1000:.1f}s to load - most visitors leave before that")

    current_year = datetime.now(timezone.utc).year
    if result["website_last_updated"]:
        age = current_year - int(result["website_last_updated"])
        if age >= 3:
            score -= 2
            issues.append(f"Shows a {result['website_last_updated']} copyright - looks abandoned")
        elif age == 2:
            score -= 1
            issues.append(f"Copyright still says {result['website_last_updated']}")

    if result["website_platform"] in ("Wix", "GoDaddy Builder", "Weebly", "Google Business Site"):
        score -= 1
        issues.append(f"Built on {result['website_platform']} - template site, limited and slow")

    if "<form" not in html.lower() and not result["emails"]:
        issues.append("Has no contact form or visible email address")

    if len(html) < 2000:
        issues.append("Is close to empty - almost no real content")
        score -= 1

    title = TITLE_RE.search(html)
    if not title or not title.group(1).strip():
        issues.append("Has no page title - invisible to search engines")
        score -= 1

    result["website_score"] = max(0, min(score, 10))
    result["website_issues"] = issues
    return result


def find_contact_email(website_url: str | None) -> list[str]:
    """Free email discovery (Part 9.3): home page, then the contact/about pages the
    site itself links to, then the fixed fallback paths. Own-domain addresses rank first."""
    if not website_url:
        return []
    base = _normalise_url(website_url).rstrip("/")
    found: list[str] = []

    def fetch(url: str) -> str:
        try:
            response = netsafe.safe_get(url, breaker="website_check", retries=1, timeout=6)
        except Exception:  # noqa: BLE001
            return ""
        return (response.text or "") if response.status_code < 400 else ""

    home = fetch(base)
    found += contactparse.extract_emails(home)
    targets = contactparse.discover_contact_links(home, base + "/") if home else []
    targets += [base + p for p in CONTACT_PATHS if base + p not in targets]
    for url in targets[:6]:
        if len(found) >= 3:
            break
        found += contactparse.extract_emails(fetch(url))
    unique = list(dict.fromkeys(found))
    return contactparse.rank_emails(unique, base)


def find_socials_deep(website_url: str | None, have: tuple[str, ...] = ()) -> dict:
    """Free social-profile discovery beyond the home page: the contact/about pages the site
    links to (footers and "follow us" blocks often live there). Only looks for columns not
    already in `have`; stops as soon as every wanted one is found. No search engine involved."""
    if not website_url:
        return {}
    wanted = [f for f in SOCIAL_PATTERNS if f not in have]
    if not wanted:
        return {}
    base = _normalise_url(website_url).rstrip("/")

    def fetch(url: str) -> str:
        try:
            response = netsafe.safe_get(url, breaker="website_check", retries=1, timeout=6)
        except Exception:  # noqa: BLE001
            return ""
        return (response.text or "") if response.status_code < 400 else ""

    found: dict = {}
    home = fetch(base)
    pages = [home]
    targets = contactparse.discover_contact_links(home, base + "/") if home else []
    targets += [base + p for p in CONTACT_PATHS if base + p not in targets]
    queue = iter(targets[:5])
    while True:
        for field, url in _find_socials(pages[-1]).items():
            if field in wanted and field not in found:
                found[field] = url
        if len(found) == len(wanted):
            break
        nxt = next(queue, None)
        if nxt is None:
            break
        pages.append(fetch(nxt))
    return found


def to_db_fields(result: dict) -> dict:
    """Map a check_website result onto lead columns."""
    return {
        "has_website": result["website_exists"],
        "website_ssl": result["website_ssl"],
        "website_mobile_friendly": result["website_mobile_friendly"],
        "website_speed_ms": result["website_speed_ms"],
        "website_platform": result["website_platform"],
        "website_last_updated": result["website_last_updated"],
        "website_score": result["website_score"],
        "website_issues": json.dumps(result["website_issues"]),
        "website_checked_at": result["checked_at"],
    }
