"""Deep website audit.

Two halves, deliberately separate:

  collect()  does the network I/O (homepage, robots.txt, sitemap.xml, a bounded
             sample of same-host links, one TLS handshake, optional PageSpeed
             Insights). Every fetch goes through `netsafe`, so the audit can
             never be steered at internal addresses.
  analyze()  is pure: it turns the collected `Page` into structured checks. No
             I/O, so every rule is unit-testable on a string of HTML.

What an audit is and is not: it is *homepage-centric*. It reads one page in
full plus a small sample of its links - enough to find real, demonstrable gaps
- not a crawl of the whole site. Core Web Vitals (LCP/CLS/TBT) need a real
browser; they come from Google PageSpeed Insights when PAGESPEED_API_KEY is set
and are reported "unavailable" otherwise, never estimated.

Each check: {id, category, title, status, weight, detail, evidence}
  status  PASS | WARN | FAIL | NA   (NA = not applicable / not measurable)
  weight  0 = informational (reported, does not move the score)
Category score = 100 * sum(weight * {PASS:1, WARN:.5, FAIL:0}) / sum(weight)
over non-NA checks. Overall = weighted mean of the category scores.
"""

from __future__ import annotations

import hashlib
import json
import re
import socket
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from . import http, netsafe
from .config import settings
from .db import current_ws, execute, log_failure, query, query_one, utcnow
from .quota import QuotaExceeded, check_and_reserve

AUDIT_VERSION = 1
CATEGORY_WEIGHTS = {"technical": 0.25, "seo": 0.25, "performance": 0.15,
                    "conversion": 0.20, "content": 0.15}
STATUS_POINTS = {"PASS": 1.0, "WARN": 0.5, "FAIL": 0.0}
PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


# ------------------------------------------------------------------ parsing --

class PageParser(HTMLParser):
    """One-pass collector of everything the analyzers need. Tolerant of bad HTML.
    Title, heading, anchor and JSON-LD captures are independent, so an anchor
    inside a heading (very common) is recorded by both."""

    SKIP_TEXT = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.metas: list[dict] = []
        self.links: list[dict] = []          # <link>
        self.anchors: list[dict] = []        # <a href> {href, text}
        self.headings: list[tuple[int, str]] = []
        self.images: list[dict] = []
        self.scripts: list[dict] = []        # {src, async, defer, module, in_head}
        self.iframes: list[str] = []
        self.forms: list[dict] = []          # {inputs: [types], textarea: bool}
        self.buttons: list[str] = []         # <button> text and submit-input values
        self.jsonld: list[str] = []
        self.microdata_types: list[str] = []
        self.text_chunks: list[str] = []
        self._in_head = False
        self._skip_depth = 0
        self._title: list[str] | None = None
        self._ld: list[str] | None = None
        self._heading: tuple[int, list[str]] | None = None
        self._anchor: tuple[str, list[str]] | None = None
        self._form: dict | None = None
        self._button: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if "itemtype" in a:
            self.microdata_types.append(a["itemtype"].rsplit("/", 1)[-1])
        if tag == "head":
            self._in_head = True
        elif tag == "body":
            self._in_head = False
        elif tag == "title":
            self._title = []
        elif tag == "meta":
            self.metas.append(a)
        elif tag == "link":
            self.links.append(a)
        elif tag == "a" and "href" in a:
            self._anchor = (a["href"], [])
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._heading = (int(tag[1]), [])
        elif tag == "img":
            self.images.append(a)
        elif tag == "iframe" and a.get("src"):
            self.iframes.append(a["src"])
        elif tag == "script":
            if a.get("type", "").lower() == "application/ld+json":
                self._ld = []
            elif a.get("src"):
                self.scripts.append({
                    "src": a["src"], "async": "async" in a, "defer": "defer" in a,
                    "module": a.get("type", "").lower() == "module",
                    "in_head": self._in_head})
        elif tag == "form":
            self._form = {"inputs": [], "textarea": False}
        elif tag == "button":
            self._button = []
        elif tag == "input" and a.get("type", "").lower() in ("submit", "button") \
                and a.get("value"):
            self.buttons.append(a["value"])
        if tag == "input" and self._form is not None:
            self._form["inputs"].append((a.get("type") or "text").lower())
        elif tag == "textarea" and self._form is not None:
            self._form["textarea"] = True
        if tag in self.SKIP_TEXT:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP_TEXT and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title" and self._title is not None:
            self.title = re.sub(r"\s+", " ", "".join(self._title)).strip()
            self._title = None
        elif tag == "script" and self._ld is not None:
            self.jsonld.append("".join(self._ld))
            self._ld = None
        elif tag == "a" and self._anchor is not None:
            href, buf = self._anchor
            self.anchors.append({"href": href, "text": re.sub(r"\s+", " ", "".join(buf)).strip()})
            self._anchor = None
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self._heading is not None \
                and self._heading[0] == int(tag[1]):
            level, buf = self._heading
            self.headings.append((level, re.sub(r"\s+", " ", "".join(buf)).strip()))
            self._heading = None
        elif tag == "button" and self._button is not None:
            self.buttons.append(re.sub(r"\s+", " ", "".join(self._button)).strip())
            self._button = None
        elif tag == "form" and self._form is not None:
            self.forms.append(self._form)
            self._form = None
        elif tag == "head":
            self._in_head = False

    def handle_data(self, data):
        if self._title is not None:
            self._title.append(data)
        if self._ld is not None:
            self._ld.append(data)
        if self._skip_depth == 0:
            self.text_chunks.append(data)
            if self._heading is not None:
                self._heading[1].append(data)
            if self._anchor is not None:
                self._anchor[1].append(data)
            if self._button is not None:
                self._button.append(data)

    # convenience --------------------------------------------------------
    def meta(self, key: str, value: str) -> str | None:
        for m in self.metas:
            if m.get(key, "").lower() == value:
                return m.get("content", "")
        return None

    @property
    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.text_chunks)).strip()


@dataclass
class Page:
    """Everything `collect` learned; the sole input to `analyze`."""
    url: str
    final_url: str = ""
    status_code: int = 0
    headers: dict = field(default_factory=dict)         # lower-cased keys
    html: str = ""
    response_ms: int = 0
    chain: list = field(default_factory=list)
    set_cookies: list = field(default_factory=list)
    robots_txt: str | None = None
    sitemap_ok: bool | None = None
    link_checks: list = field(default_factory=list)     # [{url, status}]
    tls: dict | None = None                             # {ok, days_left, error}
    psi: dict | None = None                             # {lcp_ms, cls, tbt_ms, ttfb_ms}
    psi_note: str = ""
    pages_fetched: int = 1
    lead: dict = field(default_factory=dict)            # niche / city for content checks


def _chk(cid, category, title, status, weight, detail, evidence=None) -> dict:
    return {"id": cid, "category": category, "title": title, "status": status,
            "weight": weight, "detail": detail, "evidence": evidence or {}}


# ---------------------------------------------------------------- technology --

TECH_SIGNATURES = [
    # (name, category, [(where, regex)]) where = html | header:<name> | cookie
    ("WordPress", "cms", [("html", r"wp-content|wp-includes|name=\"generator\" content=\"WordPress")]),
    ("WooCommerce", "ecommerce", [("html", r"woocommerce|wc-ajax|wp-content/plugins/woocommerce")]),
    ("Shopify", "ecommerce", [("html", r"cdn\.shopify\.com|Shopify\.theme")]),
    ("Magento", "ecommerce", [("html", r"Magento_|mage/cookies|/static/frontend/Magento")]),
    ("Wix", "builder", [("html", r"wixstatic\.com|_wixCssImports|wix\.com")]),
    ("Squarespace", "builder", [("html", r"static1\.squarespace\.com|squarespace\.com")]),
    ("Webflow", "builder", [("html", r"data-wf-page|webflow\.com")]),
    ("GoDaddy Builder", "builder", [("html", r"godaddysites\.com|img1\.wsimg\.com")]),
    ("Weebly", "builder", [("html", r"weebly\.com|weeblysite")]),
    ("Drupal", "cms", [("html", r"Drupal\.settings|/sites/default/files"),
                       ("header:x-generator", r"Drupal")]),
    ("Joomla", "cms", [("html", r"/media/jui/|content=\"Joomla")]),
    ("Next.js", "framework", [("html", r"__NEXT_DATA__|/_next/static")]),
    ("React", "framework", [("html", r"data-reactroot|react(-dom)?(\.production)?\.min\.js")]),
    ("Vue.js", "framework", [("html", r"data-v-[0-9a-f]{6,}|vue(\.runtime)?(\.min)?\.js")]),
    ("Laravel", "framework", [("cookie", r"laravel_session|XSRF-TOKEN")]),
    ("PHP", "language", [("header:x-powered-by", r"PHP"), ("html", r"\.php[\"'?]")]),
    ("ASP.NET", "language", [("header:x-powered-by", r"ASP\.NET"),
                             ("html", r"__VIEWSTATE")]),
    ("jQuery", "library", [("html", r"jquery[-.\d]*(\.min)?\.js")]),
    ("Bootstrap", "library", [("html", r"bootstrap(\.min)?\.(css|js)")]),
    ("Google Analytics", "analytics", [("html", r"google-analytics\.com|gtag\(|googletagmanager\.com/gtag")]),
    ("Google Tag Manager", "analytics", [("html", r"googletagmanager\.com/gtm\.js")]),
    ("Meta Pixel", "advertising", [("html", r"connect\.facebook\.net/[^\"']+/fbevents\.js|fbq\(")]),
    ("Google Ads tag", "advertising", [("html", r"googleadservices\.com|AW-\d{6,}")]),
    ("Hotjar", "analytics", [("html", r"static\.hotjar\.com")]),
    ("Cloudflare", "cdn", [("header:server", r"cloudflare"), ("header:cf-ray", r".")]),
    ("nginx", "server", [("header:server", r"nginx")]),
    ("Apache", "server", [("header:server", r"Apache")]),
]


def detect_technologies(html: str, headers: dict, cookies: list[str]) -> list[dict]:
    found, seen = [], set()
    cookie_blob = "; ".join(cookies)
    for name, category, rules in TECH_SIGNATURES:
        for where, pattern in rules:
            if where == "html":
                blob = html
            elif where == "cookie":
                blob = cookie_blob
            else:
                blob = headers.get(where.split(":", 1)[1], "")
            m = re.search(pattern, blob, re.I)
            if m and name not in seen:
                seen.add(name)
                found.append({"name": name, "category": category,
                              "evidence": f"{where}: {m.group(0)[:60]}"})
                break
    return found


# ---------------------------------------------------------------- analyzers --

SECURITY_HEADERS = ("strict-transport-security", "content-security-policy",
                    "x-frame-options", "x-content-type-options", "referrer-policy")
# Platform / tool signatures are matched in the markup. Marketing *phrases* are
# matched only on interactive elements (link text, buttons): "book an appointment
# online" in a meta description or paragraph is a claim, not a booking system.
BOOKING_PLATFORM_RE = re.compile(
    r"calendly\.com|booksy\.com|setmore\.com|simplybook|acuityscheduling|zenoti|practo\.com|"
    r"fresha\.com|appointy|youcanbook\.me|opentable|resy\.com|bookingsplus|"
    r"mindbodyonline|vagaro\.com|squareup\.com/appointments|zoho\.com/bookings", re.I)
BOOKING_PHRASE_RE = re.compile(
    r"\b(book|schedule|reserve)\b.{0,20}\b(appointment|now|table|slot|online|visit|session|"
    r"consultation|a call)\b|\bbook(ing)?\b", re.I)
ORDERING_PLATFORM_RE = re.compile(
    r"zomato\.com/|swiggy\.com/|ubereats|doordash|dunzo|magicpin|petpooja|"
    r"toasttab|olo\.com|grubhub", re.I)
ORDERING_PHRASE_RE = re.compile(r"order\s*(online|now)|online\s*order|place\s*(an\s*)?order", re.I)
ECOMMERCE_MARKUP_RE = re.compile(
    r"add_to_cart|add-to-cart|woocommerce|cdn\.shopify\.com|href=[\"'][^\"']*/(cart|checkout)\b",
    re.I)
ECOMMERCE_PHRASE_RE = re.compile(r"add\s*to\s*(cart|bag|basket)|buy\s*now|shopping\s*cart", re.I)
PRICING_RE = re.compile(r"(?:₹|rs\.?\s?|inr\s?|\$|€|£)\s?\d[\d,]*|\bpricing\b|\bprice list\b|"
                        r"\bour packages\b|\brates\b", re.I)
TESTIMONIAL_RE = re.compile(r"testimonial|what (our )?(clients|customers|patients|guests) say|"
                            r"customer reviews|happy (clients|customers)|\bsuccess stories\b",
                            re.I)
TRUST_RE = re.compile(r"\bISO\s?\d{3,5}|\bcertified\b|\baward[- ]winning\b|\bnabh\b|\bfssai\b|"
                      r"\b(money[- ]back )?guarantee\b|\bsecure payment|\btrusted by\b|"
                      r"\bgoogle reviews?\b|\bverified\b", re.I)
NEWSLETTER_RE = re.compile(r"newsletter|subscribe|sign up for (our )?(updates|offers)|"
                           r"join our (mailing )?list", re.I)
WHATSAPP_RE = re.compile(r"wa\.me/|api\.whatsapp\.com|whatsapp://|web\.whatsapp\.com|"
                         r"chat\s*on\s*whatsapp", re.I)


def _same_host(a: str, b: str) -> bool:
    ha = (urlparse(a).hostname or "").removeprefix("www.")
    hb = (urlparse(b).hostname or "").removeprefix("www.")
    return ha == hb


def _internal_links(page: Page, p: PageParser) -> list[str]:
    base = page.final_url or page.url
    out, seen = [], set()
    for a in p.anchors:
        href = (a["href"] or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "sms:")):
            continue
        absolute = urljoin(base, href).split("#")[0]
        if urlparse(absolute).scheme in ("http", "https") and _same_host(absolute, base) \
                and absolute.rstrip("/") != base.rstrip("/") and absolute not in seen:
            seen.add(absolute)
            out.append(absolute)
    return out


def _jsonld_types(blocks: list[str]) -> set[str]:
    types: set[str] = set()

    def walk(node, depth=0):
        if depth > 6:
            return
        if isinstance(node, list):
            for i in node:
                walk(i, depth + 1)
        elif isinstance(node, dict):
            t = node.get("@type")
            for x in (t if isinstance(t, list) else [t]):
                if isinstance(x, str):
                    types.add(x)
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v, depth + 1)

    for b in blocks:
        try:
            walk(json.loads(b.strip()))
        except (ValueError, TypeError):
            continue
    return types


def analyze(page: Page) -> dict:
    """Pure: Page -> {checks, scores, overall_score, technologies, features, summary}."""
    p = PageParser()
    try:
        p.feed(page.html or "")
    except Exception:  # noqa: BLE001 - malformed markup must not fail the audit
        pass
    base = page.final_url or page.url
    https = urlparse(base).scheme == "https"
    h = page.headers
    html = page.html or ""
    checks: list[dict] = []
    add = checks.append
    internal = _internal_links(page, p)
    jsonld_types = _jsonld_types(p.jsonld)
    schema_types = jsonld_types | set(p.microdata_types)
    anchors_blob = " ".join(f"{a['href']} {a['text']}" for a in p.anchors).lower()
    visible = p.text
    words = len(re.findall(r"[A-Za-zऀ-ॿ]{2,}", visible))

    # ------------------------------------------------------------ technical --
    add(_chk("https", "technical", "Served over HTTPS", "PASS" if https else "FAIL", 3,
             "The site loads over HTTPS." if https else
             "The site loads over plain HTTP - browsers label it 'Not secure'.",
             {"final_url": base}))
    if page.tls is None:
        add(_chk("tls_valid", "technical", "TLS certificate", "NA", 3,
                 "Not an HTTPS site, or the handshake could not be performed."))
    elif not page.tls.get("ok"):
        add(_chk("tls_valid", "technical", "TLS certificate", "FAIL", 3,
                 f"Certificate problem: {page.tls.get('error', 'verification failed')}.",
                 page.tls))
    else:
        d = page.tls.get("days_left")
        st = "FAIL" if d is not None and d < 0 else "WARN" if d is not None and d < 30 else "PASS"
        add(_chk("tls_valid", "technical", "TLS certificate", st, 3,
                 f"Valid certificate, {d} day(s) until expiry." if st != "FAIL"
                 else "The certificate has expired.", page.tls))
    hops = max(len(page.chain) - 1, 0)
    add(_chk("redirect_chain", "technical", "Redirect chain",
             "PASS" if hops <= 2 else "WARN" if hops == 3 else "FAIL", 1,
             f"{hops} redirect(s) before the final page.", {"chain": page.chain}))
    present = [x for x in SECURITY_HEADERS if x in h and (x != "strict-transport-security" or https)]
    missing = [x for x in SECURITY_HEADERS
               if x not in present and (x != "strict-transport-security" or https)]
    need = len(SECURITY_HEADERS) if https else len(SECURITY_HEADERS) - 1
    st = "PASS" if len(present) >= need - 1 else "WARN" if len(present) >= 2 else "FAIL"
    add(_chk("security_headers", "technical", "Security headers", st, 2,
             f"{len(present)} of {need} recommended security headers set.",
             {"present": present, "missing": missing}))
    mixed_active, mixed_passive = [], []
    if https:
        for s in p.scripts:
            if s["src"].startswith("http://"):
                mixed_active.append(s["src"])
        for l in p.links:
            if l.get("href", "").startswith("http://") and "stylesheet" in l.get("rel", "").lower():
                mixed_active.append(l["href"])
        mixed_active += [i for i in p.iframes if i.startswith("http://")]
        mixed_passive = [i["src"] for i in p.images if i.get("src", "").startswith("http://")]
    if not https:
        add(_chk("mixed_content", "technical", "Mixed content", "NA", 2, "Not an HTTPS page."))
    else:
        st = "FAIL" if mixed_active else "WARN" if mixed_passive else "PASS"
        add(_chk("mixed_content", "technical", "Mixed content", st, 2,
                 "No insecure resources on the secure page." if st == "PASS" else
                 f"{len(mixed_active)} insecure script/style/frame and {len(mixed_passive)} "
                 "insecure image reference(s) on an HTTPS page.",
                 {"active": mixed_active[:5], "passive": mixed_passive[:5]}))
    rt = page.response_ms
    add(_chk("response_time", "technical", "Server response time",
             "PASS" if rt < 800 else "WARN" if rt < 2000 else "FAIL", 2,
             f"The page took {rt} ms to respond.", {"ms": rt}))
    enc = h.get("content-encoding", "")
    if len(html) < 10_000:
        add(_chk("compression", "technical", "Compression", "NA", 1,
                 "Page too small for compression to matter."))
    else:
        add(_chk("compression", "technical", "Compression",
                 "PASS" if enc in ("gzip", "br", "zstd", "deflate") else "WARN", 1,
                 f"Responses are compressed ({enc})." if enc else
                 "The HTML is not compressed (no Content-Encoding).", {"encoding": enc}))
    cached = any(k in h for k in ("cache-control", "etag", "last-modified", "expires"))
    add(_chk("caching", "technical", "Caching headers", "PASS" if cached else "WARN", 1,
             "Caching headers present." if cached else "No Cache-Control / ETag / Last-Modified."))
    if page.robots_txt is None:
        add(_chk("robots_txt", "technical", "robots.txt", "WARN", 1, "No robots.txt found."))
    else:
        add(_chk("robots_txt", "technical", "robots.txt", "PASS", 1, "robots.txt is present."))
    sitemap_in_robots = bool(page.robots_txt and re.search(r"^\s*sitemap\s*:", page.robots_txt, re.I | re.M))
    if page.sitemap_ok or sitemap_in_robots:
        add(_chk("sitemap", "technical", "XML sitemap", "PASS", 1, "A sitemap is published."))
    else:
        add(_chk("sitemap", "technical", "XML sitemap", "WARN", 1,
                 "No /sitemap.xml and none declared in robots.txt."))
    viewport = p.meta("name", "viewport")
    add(_chk("mobile_viewport", "technical", "Mobile viewport",
             "PASS" if viewport else "FAIL", 3,
             "A viewport meta tag is set." if viewport else
             "No viewport meta tag - the page will not scale on a phone.",
             {"viewport": viewport}))

    # ------------------------------------------------------------------ seo --
    tlen = len(p.title)
    add(_chk("title", "seo", "Page title",
             "FAIL" if not tlen else "PASS" if 10 <= tlen <= 70 else "WARN", 3,
             "No <title>." if not tlen else f"Title is {tlen} characters: \"{p.title[:90]}\".",
             {"title": p.title, "length": tlen}))
    desc = p.meta("name", "description") or ""
    dl = len(desc.strip())
    add(_chk("meta_description", "seo", "Meta description",
             "FAIL" if not dl else "PASS" if 50 <= dl <= 170 else "WARN", 2,
             "No meta description." if not dl else f"Meta description is {dl} characters.",
             {"length": dl}))
    canonical = next((l.get("href") for l in p.links if "canonical" in l.get("rel", "").lower()), None)
    add(_chk("canonical", "seo", "Canonical URL", "PASS" if canonical else "WARN", 1,
             "Canonical link set." if canonical else "No canonical link."))
    h1s = [t for lvl, t in p.headings if lvl == 1]
    add(_chk("h1", "seo", "Single H1", "PASS" if len(h1s) == 1 else "WARN" if len(h1s) > 1
             else "FAIL", 2, f"{len(h1s)} H1 heading(s).", {"h1": h1s[:3]}))
    levels = [lvl for lvl, _ in p.headings]
    skips = any(b - a > 1 for a, b in zip(levels, levels[1:])) or (levels and levels[0] > 1)
    if not levels:
        add(_chk("heading_structure", "seo", "Heading structure", "FAIL", 1, "No headings at all."))
    else:
        add(_chk("heading_structure", "seo", "Heading structure",
                 "WARN" if skips or 2 not in levels else "PASS", 1,
                 f"{len(levels)} headings; "
                 + ("levels are skipped or no H2 present." if skips or 2 not in levels
                    else "hierarchy looks sound."), {"levels": levels[:30]}))
    og = {k for k in ("og:title", "og:description", "og:image") if p.meta("property", k)}
    add(_chk("open_graph", "seo", "Open Graph tags", "PASS" if len(og) >= 2 else "WARN", 1,
             f"{len(og)} of 3 Open Graph tags set.", {"present": sorted(og)}))
    local_types = {"LocalBusiness", "Organization", "Restaurant", "Dentist", "MedicalBusiness",
                   "Store", "HealthAndBeautyBusiness", "ProfessionalService", "Hotel"}
    has_local = bool(schema_types & local_types) or any(t.endswith(("Business", "Store"))
                                                       for t in schema_types)
    add(_chk("structured_data", "seo", "Structured data (schema.org)",
             "PASS" if has_local else "WARN" if schema_types else "FAIL", 2,
             "LocalBusiness/Organization markup found." if has_local else
             f"Only {sorted(schema_types)[:4]} markup found." if schema_types else
             "No schema.org structured data.", {"types": sorted(schema_types)[:10]}))
    robots_meta = (p.meta("name", "robots") or "").lower()
    xrobots = h.get("x-robots-tag", "").lower()
    blocked_by_robots = bool(page.robots_txt and re.search(
        r"user-agent:\s*\*\s*(?:\r?\n\s*(?:#.*)?)*disallow:\s*/\s*$", page.robots_txt, re.I | re.M))
    noindex = "noindex" in robots_meta or "noindex" in xrobots
    add(_chk("indexability", "seo", "Indexable by search engines",
             "FAIL" if noindex or blocked_by_robots else "PASS", 3,
             "Search engines are told NOT to index this page." if noindex else
             "robots.txt disallows the whole site." if blocked_by_robots else
             "Nothing blocks indexing.",
             {"meta_robots": robots_meta, "x_robots_tag": xrobots,
              "robots_disallow_all": blocked_by_robots}))
    checked = page.link_checks
    broken = [c for c in checked if c["status"] == 0 or c["status"] >= 400]
    if not checked:
        add(_chk("broken_links", "seo", "Broken internal links", "NA", 2,
                 "No internal links were checked."))
    else:
        add(_chk("broken_links", "seo", "Broken internal links",
                 "PASS" if not broken else "WARN" if len(broken) == 1 else "FAIL", 2,
                 f"{len(broken)} of {len(checked)} sampled internal links are broken.",
                 {"broken": broken[:5], "sampled": len(checked)}))
    add(_chk("internal_links", "seo", "Internal linking", "PASS" if len(internal) >= 3 else "WARN",
             1, f"{len(internal)} internal link(s) on the homepage.", {"count": len(internal)}))
    imgs = p.images
    if imgs:
        with_alt = sum(1 for i in imgs if i.get("alt", "").strip())
        ratio = with_alt / len(imgs)
        add(_chk("image_alt", "seo", "Image alt text",
                 "PASS" if ratio >= 0.9 else "WARN" if ratio >= 0.5 else "FAIL", 1,
                 f"{with_alt} of {len(imgs)} images have alt text.", {"ratio": round(ratio, 2)}))
    else:
        add(_chk("image_alt", "seo", "Image alt text", "NA", 1, "No images found."))

    # ---------------------------------------------------------- performance --
    kb = len(html.encode("utf-8", "ignore")) / 1024
    add(_chk("html_size", "performance", "HTML size", "PASS" if kb < 100 else
             "WARN" if kb < 300 else "FAIL", 1, f"The HTML is {kb:.0f} KB.", {"kb": round(kb)}))
    blocking = [s["src"] for s in p.scripts
                if s["in_head"] and not (s["async"] or s["defer"] or s["module"])]
    css_in_head = [l.get("href") for l in p.links
                   if "stylesheet" in l.get("rel", "").lower()
                   and l.get("media", "all") in ("", "all", "screen")]
    rb = len(blocking) + len(css_in_head)
    add(_chk("render_blocking", "performance", "Render-blocking resources",
             "PASS" if rb <= 3 else "WARN" if rb <= 6 else "FAIL", 2,
             f"{len(blocking)} blocking script(s) and {len(css_in_head)} stylesheet(s) in <head>.",
             {"scripts": blocking[:5], "stylesheets": css_in_head[:5]}))
    ext = len(p.scripts)
    add(_chk("script_count", "performance", "External scripts",
             "PASS" if ext <= 10 else "WARN" if ext <= 20 else "FAIL", 1,
             f"{ext} external script file(s).", {"count": ext}))
    if len(imgs) >= 4:
        good = sum(1 for i in imgs if i.get("loading", "").lower() == "lazy"
                   or (i.get("width") and i.get("height")))
        add(_chk("image_optimization", "performance", "Image loading hints",
                 "PASS" if good / len(imgs) >= 0.6 else "WARN", 1,
                 f"{good} of {len(imgs)} images use lazy-loading or declare dimensions."))
    else:
        add(_chk("image_optimization", "performance", "Image loading hints", "NA", 1,
                 "Too few images to judge."))
    psi = page.psi
    if psi:
        lcp, cls, tbt = psi.get("lcp_ms"), psi.get("cls"), psi.get("tbt_ms")
        if lcp is not None:
            add(_chk("lcp", "performance", "Largest Contentful Paint (lab, mobile)",
                     "PASS" if lcp <= 2500 else "WARN" if lcp <= 4000 else "FAIL", 2,
                     f"LCP {lcp / 1000:.1f}s (good is under 2.5s).", {"ms": lcp}))
        if cls is not None:
            add(_chk("cls", "performance", "Cumulative Layout Shift (lab, mobile)",
                     "PASS" if cls <= 0.1 else "WARN" if cls <= 0.25 else "FAIL", 2,
                     f"CLS {cls:.2f} (good is under 0.1).", {"cls": cls}))
        if tbt is not None:
            add(_chk("tbt", "performance", "Total Blocking Time (lab, mobile; INP proxy)",
                     "PASS" if tbt <= 200 else "WARN" if tbt <= 600 else "FAIL", 2,
                     f"TBT {tbt:.0f} ms. Real INP needs field data, which lab runs lack.",
                     {"ms": tbt}))
    else:
        for cid, title in (("lcp", "Largest Contentful Paint"), ("cls", "Cumulative Layout Shift"),
                           ("tbt", "Total Blocking Time / INP")):
            add(_chk(cid, "performance", title, "NA", 2,
                     page.psi_note or "Unavailable: no PageSpeed Insights provider configured."))

    # ----------------------------------------------------------- conversion --
    tel = bool(re.search(r"href=[\"']tel:", html, re.I))
    add(_chk("phone_cta", "conversion", "Click-to-call phone link", "PASS" if tel else "FAIL", 2,
             "A tel: link is present." if tel else "No tap-to-call link - phone numbers "
             "(if shown) are plain text.", {}))
    wa = bool(WHATSAPP_RE.search(html))
    add(_chk("whatsapp_cta", "conversion", "WhatsApp contact option", "PASS" if wa else "WARN", 1,
             "A WhatsApp link is present." if wa else "No WhatsApp link or chat button."))
    contact_form = any(
        ("email" in f["inputs"] or "tel" in f["inputs"] or f["textarea"]) for f in p.forms)
    has_mailto = "mailto:" in html.lower()
    add(_chk("contact_form", "conversion", "Enquiry / contact form",
             "PASS" if contact_form else "WARN" if has_mailto else "FAIL", 2,
             "A contact/enquiry form is present." if contact_form else
             "Only a mailto: link - no form." if has_mailto else "No form and no email link."))
    news = bool(NEWSLETTER_RE.search(visible)) and any("email" in f["inputs"] for f in p.forms)
    lead_capture = contact_form or news
    add(_chk("lead_capture", "conversion", "Lead capture", "PASS" if lead_capture else "FAIL", 2,
             "Visitors have a way to leave their details." if lead_capture else
             "Nothing captures a visitor's details.", {"newsletter": news}))
    interactive = " ".join([a["text"] for a in p.anchors] + p.buttons)
    booking = bool(BOOKING_PLATFORM_RE.search(html) or BOOKING_PHRASE_RE.search(interactive))
    add(_chk("booking", "conversion", "Online booking", "PASS" if booking else "WARN", 0,
             "Online booking / appointment option detected." if booking else
             "No online booking detected.", {}))
    ordering = bool(ORDERING_PLATFORM_RE.search(html) or ORDERING_PHRASE_RE.search(interactive))
    add(_chk("online_ordering", "conversion", "Online ordering", "PASS" if ordering else "WARN", 0,
             "Online ordering detected." if ordering else "No online ordering detected."))
    techs = detect_technologies(html, h, page.set_cookies)
    ecommerce = bool(ECOMMERCE_MARKUP_RE.search(html) or ECOMMERCE_PHRASE_RE.search(interactive)) \
        or any(t["category"] == "ecommerce" for t in techs)
    add(_chk("ecommerce", "conversion", "E-commerce", "PASS" if ecommerce else "WARN", 0,
             "A shop/cart was detected." if ecommerce else "No cart or shop detected."))
    pricing = bool(PRICING_RE.search(visible))
    add(_chk("pricing", "conversion", "Pricing information", "PASS" if pricing else "WARN", 0,
             "Prices or a pricing section appear." if pricing else "No prices shown."))
    proof_review = bool(TESTIMONIAL_RE.search(visible)) or bool(
        {"Review", "AggregateRating"} & schema_types)
    trust = bool(TRUST_RE.search(visible))
    add(_chk("social_proof", "conversion", "Testimonials / reviews",
             "PASS" if proof_review else "WARN", 1,
             "Testimonials or review markup found." if proof_review else
             "No testimonials or reviews on the page."))
    add(_chk("trust_signals", "conversion", "Trust signals (heuristic)",
             "PASS" if trust else "WARN", 0,
             "Certification / guarantee / trust wording found." if trust else
             "No trust wording found (keyword heuristic)."))

    # -------------------------------------------------------------- content --
    add(_chk("content_depth", "content", "Content depth",
             "PASS" if words >= 150 else "WARN" if words >= 60 else "FAIL", 2,
             f"About {words} words of visible text.", {"words": words}))
    phone_vis = bool(re.search(r"(?:\+?\d[\d\s\-().]{8,}\d)", visible))
    email_vis = bool(re.search(r"[\w.+-]+@[\w-]+\.[\w.]{2,}", visible)) or has_mailto
    addr_vis = "PostalAddress" in schema_types or bool(
        re.search(r"\b(address|located at|visit us|find us|road|street|nagar|sector|floor)\b",
                  visible, re.I))
    add(_chk("contact_info", "content", "Contact details visible",
             "PASS" if (phone_vis or email_vis) and addr_vis else
             "WARN" if (phone_vis or email_vis or addr_vis) else "FAIL", 2,
             "Phone/email and an address are visible." if (phone_vis or email_vis) and addr_vis
             else "Contact details are incomplete.",
             {"phone": phone_vis, "email": email_vis, "address": addr_vis}))
    city = (page.lead.get("city") or "").strip().lower()
    if city:
        add(_chk("location_mention", "content", "Location mentioned",
                 "PASS" if city in visible.lower() else "WARN", 1,
                 f"\"{page.lead.get('city')}\" appears on the page." if city in visible.lower()
                 else f"The page never mentions \"{page.lead.get('city')}\"."))
    about = bool(re.search(r"about", anchors_blob))
    add(_chk("about_page", "content", "About page", "PASS" if about else "WARN", 1,
             "An About link exists." if about else "No About link."))
    team = bool(re.search(r"\b(team|our doctors|our staff|meet us|leadership)\b", anchors_blob))
    blog = bool(re.search(r"\b(blog|news|articles|insights)\b", anchors_blob))
    faq = "FAQPage" in schema_types or bool(re.search(r"\bfaq|frequently asked", anchors_blob + " " + visible.lower()))
    for cid, title, ok in (("team_page", "Team page", team), ("blog", "Blog / news", blog),
                           ("faq", "FAQ", faq)):
        add(_chk(cid, "content", title, "PASS" if ok else "WARN", 0,
                 f"{title} detected." if ok else f"No {title.lower()} detected."))
    years = [int(y) for y in re.findall(r"(?:©|&copy;|copyright)[^0-9]{0,20}((?:19|20)\d{2})", html, re.I)]
    if years:
        age = datetime.now(timezone.utc).year - max(years)
        add(_chk("copyright_year", "content", "Content freshness (copyright year)",
                 "PASS" if age <= 1 else "WARN" if age == 2 else "FAIL", 1,
                 f"Footer copyright says {max(years)}.", {"year": max(years)}))
    else:
        add(_chk("copyright_year", "content", "Content freshness (copyright year)", "NA", 1,
                 "No copyright year found."))

    scores = category_scores(checks)
    overall = overall_score(scores)
    # Digits are masked so dates, counters and prices don't read as "the site changed".
    fingerprint = hashlib.sha1(re.sub(r"\d+", "#", visible)[:20000].encode(
        "utf-8", "ignore")).hexdigest()
    features = {c["id"]: c["status"] == "PASS" for c in checks if c["id"] in (
        "booking", "online_ordering", "ecommerce", "pricing", "social_proof", "trust_signals",
        "whatsapp_cta", "phone_cta", "contact_form", "lead_capture", "team_page", "blog", "faq")}
    return {
        "checks": checks, "scores": scores, "overall_score": overall,
        "technologies": techs, "features": features,
        "fingerprint": fingerprint, "title": p.title,
        "summary": {"checks": len(checks),
                    "failed": sum(c["status"] == "FAIL" for c in checks),
                    "warnings": sum(c["status"] == "WARN" for c in checks),
                    "not_measured": sum(c["status"] == "NA" for c in checks)},
    }


def category_scores(checks: list[dict]) -> dict:
    out = {}
    for category in CATEGORY_WEIGHTS:
        rows = [c for c in checks if c["category"] == category
                and c["status"] in STATUS_POINTS and c["weight"] > 0]
        total = sum(c["weight"] for c in rows)
        out[category] = round(100 * sum(c["weight"] * STATUS_POINTS[c["status"]] for c in rows)
                              / total) if total else None
    return out


def overall_score(scores: dict) -> int | None:
    used = {k: v for k, v in scores.items() if v is not None}
    if not used:
        return None
    w = sum(CATEGORY_WEIGHTS[k] for k in used)
    return round(sum(CATEGORY_WEIGHTS[k] * v for k, v in used.items()) / w)


# --------------------------------------------------------------- collection --

def _fetch(url: str, retries: int = 1, timeout: int = 8):
    """(response|None, error|None). Never raises; UnsafeURL is reported, not retried."""
    try:
        return netsafe.safe_get(url, breaker="website_audit", retries=retries,
                                timeout=timeout), None
    except netsafe.UnsafeURL as exc:
        return None, f"blocked: {exc}"
    except Exception as exc:  # noqa: BLE001
        resp = getattr(exc, "response", None)
        if resp is not None:
            return resp, None
        return None, str(exc)[:200]


def tls_probe(host: str, port: int = 443, timeout: float = 6.0) -> dict:
    """Handshake with full verification; report expiry. Caller has already
    validated the host is public (the page was fetched through netsafe)."""
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as tls:
                cert = tls.getpeercert()
        expires = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(
            tzinfo=timezone.utc)
        return {"ok": True, "days_left": (expires - datetime.now(timezone.utc)).days,
                "expires": expires.date().isoformat()}
    except ssl.SSLCertVerificationError as exc:
        return {"ok": False, "error": exc.verify_message or str(exc)}
    except (OSError, ssl.SSLError, KeyError, ValueError) as exc:
        return {"ok": None, "error": str(exc)[:120]}


def pagespeed(url: str) -> tuple[dict | None, str]:
    """PageSpeed Insights mobile lab data. (metrics|None, note)."""
    if not settings.pagespeed_api_key:
        return None, "Unavailable: no PageSpeed Insights provider configured (PAGESPEED_API_KEY)."
    try:
        check_and_reserve("pagespeed")       # reserve BEFORE the call
    except QuotaExceeded:
        return None, "Unavailable: PageSpeed daily cap reached."
    try:
        r = http.get(PSI_URL, breaker="pagespeed", retries=1, timeout=45,
                     params={"url": url, "strategy": "mobile", "category": "performance",
                             "key": settings.pagespeed_api_key})
        if r.status_code >= 400:
            return None, f"Unavailable: PageSpeed returned {r.status_code}."
        audits = r.json()["lighthouseResult"]["audits"]

        def num(k):
            v = audits.get(k, {}).get("numericValue")
            return float(v) if v is not None else None
        return {"lcp_ms": num("largest-contentful-paint"), "cls": num("cumulative-layout-shift"),
                "tbt_ms": num("total-blocking-time"),
                "ttfb_ms": num("server-response-time")}, ""
    except Exception as exc:  # noqa: BLE001
        return None, f"Unavailable: PageSpeed call failed ({str(exc)[:80]})."


def collect(url: str, lead: dict | None = None, use_psi: bool = True) -> tuple[Page | None, str | None]:
    """Fetch what the audit needs. -> (Page, None) or (None, error)."""
    target = url.strip()
    if not target.startswith(("http://", "https://")):
        target = "https://" + target
    started = time.perf_counter()
    try:
        response, chain = netsafe.safe_get_chain(target, breaker="website_audit", retries=2,
                                                 timeout=settings.http_timeout)
    except netsafe.UnsafeURL as exc:
        return None, f"blocked: {exc}"
    except Exception as exc:  # noqa: BLE001
        resp = getattr(exc, "response", None)
        if resp is None:
            return None, str(exc)[:200]
        response, chain = resp, [target]
    try:   # the final response's own round-trip, not redirects/retries before it
        elapsed = int(response.elapsed.total_seconds() * 1000)
    except (RuntimeError, AttributeError):
        elapsed = int((time.perf_counter() - started) * 1000)
    final = chain[-1] if chain else str(response.url)
    page = Page(url=target, final_url=final, status_code=response.status_code,
                headers={k.lower(): v for k, v in response.headers.items()},
                html=response.text or "", response_ms=elapsed, chain=chain,
                set_cookies=[c for c in response.headers.get_list("set-cookie")]
                if hasattr(response.headers, "get_list") else [],
                lead=lead or {})
    if response.status_code >= 400:
        return page, f"HTTP {response.status_code}"

    parsed = urlparse(final)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    robots, _ = _fetch(origin + "/robots.txt")
    if robots is not None and robots.status_code == 200 and "<html" not in robots.text[:200].lower():
        page.robots_txt = robots.text[:20_000]
    page.pages_fetched += 1
    sm, _ = _fetch(origin + "/sitemap.xml")
    page.pages_fetched += 1
    page.sitemap_ok = bool(sm is not None and sm.status_code == 200
                           and re.search(r"<(urlset|sitemapindex)", sm.text[:2000]))

    p = PageParser()
    try:
        p.feed(page.html)
    except Exception:  # noqa: BLE001
        pass
    for link in _internal_links(page, p)[:max(0, settings.audit_max_link_checks)]:
        r, err = _fetch(link, retries=1, timeout=6)
        page.link_checks.append({"url": link, "status": r.status_code if r is not None else 0})
        page.pages_fetched += 1

    if parsed.scheme == "https" and parsed.hostname:
        tls = tls_probe(parsed.hostname)
        page.tls = tls if tls.get("ok") is not None else None
    if use_psi:
        page.psi, page.psi_note = pagespeed(final)
    return page, None


# ---------------------------------------------------------------- persistence --

def run_audit(lead_id: int, use_psi: bool = True) -> dict:
    """Audit one lead's website, store the result, refresh its opportunities.

    Returns the stored audit row (findings decoded). A site that can't be reached
    is a *result* (status UNREACHABLE), not an error; a URL the SSRF guard refuses
    is recorded as BLOCKED and produces no opportunities (that is not a fact about
    the business)."""
    from . import opportunities

    lead = query_one("SELECT * FROM leads WHERE id = ? AND workspace_id = ?",
                     (lead_id, current_ws()))
    if not lead:
        raise LookupError("lead not found")
    url = (lead.get("website_url") or "").strip()
    if not url:
        raise ValueError("The lead has no website to audit")

    started = time.perf_counter()
    page, error = collect(url, lead, use_psi=use_psi)
    findings: dict | None = None
    status, final_url, pages = "OK", None, 0
    scores = {k: None for k in ("overall", *CATEGORY_WEIGHTS)}
    if page is None:
        status = "BLOCKED" if (error or "").startswith("blocked") else "UNREACHABLE"
    else:
        final_url, pages = page.final_url, page.pages_fetched
        if error:                      # HTTP >= 400: reachable, but serving an error
            status = "ERROR_PAGE"
            findings = {"checks": [], "http_status": page.status_code,
                        "technologies": [], "features": {}, "summary": {}}
        else:
            findings = analyze(page)
            scores = {"overall": findings["overall_score"], **findings["scores"]}
            findings["response_ms"] = page.response_ms
            findings["redirect_chain"] = page.chain
            findings["psi"] = page.psi
    duration = int((time.perf_counter() - started) * 1000)
    audit_id = execute(
        """INSERT INTO website_audits
           (workspace_id, lead_id, url, final_url, status, audit_version, overall_score,
            technical_score, seo_score, performance_score, conversion_score, content_score,
            findings, pages_fetched, duration_ms, error, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (current_ws(), lead_id, url[:512], (final_url or "")[:512] or None, status,
         AUDIT_VERSION, scores["overall"], scores["technical"], scores["seo"],
         scores["performance"], scores["conversion"], scores["content"],
         json.dumps(findings) if findings else None, pages, duration,
         error if status != "OK" else None, utcnow()),
    )
    execute("UPDATE leads SET website_audit_score = ?, website_audited_at = ? "
            "WHERE id = ? AND workspace_id = ?",
            (scores["overall"], utcnow(), lead_id, current_ws()))
    try:
        opportunities.refresh_for_lead(lead_id)
    except Exception as exc:  # noqa: BLE001 - never lose a stored audit over this
        log_failure("audit.opportunities", str(exc), lead_id)
    return get_audit(audit_id)


def _decode(row: dict | None) -> dict | None:
    if not row:
        return None
    raw = row.pop("findings", None)
    row["findings"] = json.loads(raw) if raw else None
    return row


def get_audit(audit_id: int) -> dict | None:
    return _decode(query_one("SELECT * FROM website_audits WHERE id = ? AND workspace_id = ?",
                             (audit_id, current_ws())))


def latest_audit(lead_id: int) -> dict | None:
    return _decode(query_one(
        "SELECT * FROM website_audits WHERE workspace_id = ? AND lead_id = ? "
        "ORDER BY id DESC LIMIT 1", (current_ws(), lead_id)))


def audit_history(lead_id: int, limit: int = 20) -> list[dict]:
    return query(
        "SELECT id, url, final_url, status, overall_score, technical_score, seo_score, "
        "performance_score, conversion_score, content_score, pages_fetched, duration_ms, "
        "error, created_at FROM website_audits WHERE workspace_id = ? AND lead_id = ? "
        "ORDER BY id DESC LIMIT ?", (current_ws(), lead_id, limit))


def audit_is_fresh(lead_id: int) -> bool:
    from datetime import timedelta
    row = query_one("SELECT created_at FROM website_audits WHERE workspace_id = ? AND "
                    "lead_id = ? AND status IN ('OK','UNREACHABLE','ERROR_PAGE') "
                    "ORDER BY id DESC LIMIT 1", (current_ws(), lead_id))
    if not row:
        return False
    try:
        when = datetime.fromisoformat(row["created_at"])
    except ValueError:
        return False
    return datetime.now(timezone.utc) - when < timedelta(days=settings.website_cache_days)
