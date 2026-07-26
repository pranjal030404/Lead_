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

from . import http
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
    emails = [m.lower() for m in MAILTO_RE.findall(html)]
    emails += [m.lower() for m in EMAIL_IN_TEXT_RE.findall(html)]
    skip = ("sentry.io", "wixpress.com", ".png", ".jpg", ".gif", ".webp", "example.com")
    seen, out = set(), []
    for email in emails:
        email = email.split("?")[0].strip(".,;")
        if email in seen or any(s in email for s in skip):
            continue
        seen.add(email)
        out.append(email)
    return out[:5]


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
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if not url:
        result["website_issues"] = ["No website at all"]
        return result

    target = _normalise_url(url)
    started = time.perf_counter()
    try:
        response = http.get(target, breaker="website_check", retries=2,
                            timeout=settings.http_timeout)
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
    """Free email discovery: check the site's contact/about pages (Part 9.3)."""
    if not website_url:
        return []
    base = _normalise_url(website_url).rstrip("/")
    found: list[str] = []
    for path in CONTACT_PATHS:
        try:
            response = http.get(base + path, breaker="website_check", retries=1, timeout=6)
        except Exception:  # noqa: BLE001
            continue
        if response.status_code < 400:
            found += _find_emails(response.text or "")
        if found:
            break
    seen, out = set(), []
    for email in found:
        if email not in seen:
            seen.add(email)
            out.append(email)
    return out


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
