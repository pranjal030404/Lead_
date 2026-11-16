"""Technology fingerprinting from HTML + response headers. Pure.

Outdated or locked-in stacks are redesign opportunities; each finding carries a
`why` so the pitch line is grounded in something observable.
"""

from __future__ import annotations

import re

SIGNATURES = [  # (tech, category, regex over html, why-it-matters or None)
    ("WordPress", "cms", r"wp-content|wp-includes|<meta name=\"generator\" content=\"WordPress", None),
    ("Wix", "builder", r"wixstatic\.com|wix\.com|_wixCIDX", "Wix sites are hard to speed up or extend"),
    ("Squarespace", "builder", r"squarespace\.com|static1\.squarespace", None),
    ("Weebly", "builder", r"weebly\.com|editmysite\.com", "Weebly is legacy and limited"),
    ("GoDaddy Builder", "builder", r"websitebuilder\.godaddy|img1\.wsimg\.com", "template builder, limited SEO"),
    ("Shopify", "ecommerce", r"cdn\.shopify\.com|Shopify\.theme", None),
    ("WooCommerce", "ecommerce", r"woocommerce", None),
    ("Joomla", "cms", r"/media/jui/|content=\"Joomla", "Joomla is largely end-of-life"),
    ("Drupal", "cms", r"Drupal\.settings|sites/default/files", None),
    ("Blogger", "builder", r"blogspot\.com|content='blogger'", "free Blogger site, not a business site"),
    ("jQuery (legacy)", "js", r"jquery[-.]?(?:1\.[0-9]|2\.[0-9])", "ships jQuery 1.x/2.x - unmaintained"),
    ("Flash", "legacy", r"\.swf\b|application/x-shockwave-flash", "Flash no longer runs in any browser"),
    ("FrontPage/Dreamweaver", "legacy", r"FrontPage|Dreamweaver|MM_preloadImages", "very old authoring tool"),
    ("Bootstrap 3", "css", r"bootstrap[/.-]3\.", "Bootstrap 3 is retired"),
    ("Google Analytics", "analytics", r"google-analytics\.com|googletagmanager\.com|gtag\(", None),
    ("Meta Pixel", "analytics", r"connect\.facebook\.net/.*/fbevents|fbq\(", None),
    ("Cloudflare", "cdn", r"cloudflare", None),
]
HEADER_HINTS = {"x-powered-by": {"php/5": "PHP 5 is end-of-life", "php/7.0": "PHP 7.0 is end-of-life",
                                  "php/7.1": "PHP 7.1 is end-of-life", "php/7.2": "PHP 7.2 is end-of-life"}}


def fingerprint(html: str, headers: dict | None = None, https: bool | None = None) -> dict:
    html = html or ""
    stack, issues = [], []
    for tech, cat, pattern, why in SIGNATURES:
        if re.search(pattern, html, re.I):
            stack.append({"tech": tech, "category": cat})
            if why:
                issues.append(f"{tech}: {why}")
    for h, bad in HEADER_HINTS.items():
        val = ((headers or {}).get(h) or "").lower()
        for needle, why in bad.items():
            if needle in val:
                issues.append(why)
    cats = {s["category"] for s in stack}
    if "analytics" not in cats and html:
        issues.append("no analytics installed - they cannot measure enquiries")
    if re.search(r"<meta[^>]+viewport", html, re.I) is None and html:
        issues.append("no mobile viewport tag - not mobile friendly")
    if https is False:
        issues.append("served over plain HTTP")
    legacy = sum(1 for s in stack if s["category"] == "legacy") + \
        sum(1 for i in issues if "end-of-life" in i or "retired" in i or "unmaintained" in i)
    return {"stack": stack, "issues": issues, "modernisation_score": min(100, legacy * 30 + len(issues) * 10)}
