"""Contact extraction from raw HTML - pure functions, no network, no DB.

Websites hide or disguise contact details in many ways; a plain `\\S+@\\S+`
regex misses most of them. This module recovers contacts from:

  * mailto: links and visible text addresses
  * HTML-entity encoded addresses  (&#109;&#97;...)
  * obfuscated text                (info [at] shop [dot] com, info(at)shop.com)
  * Cloudflare email protection    (data-cfemail="..." XOR-encoded)
  * tel: and wa.me / api.whatsapp.com links
  * schema.org JSON-LD             (telephone, email, sameAs, name)

and ranks what it finds so the address on the business's *own* domain beats a
web-agency footer credit. It also discovers which internal links are worth
crawling (contact / about / team pages) instead of guessing fixed paths.
"""

from __future__ import annotations

import html as htmllib
import json
import re
from urllib.parse import urljoin, urlparse

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
MAILTO_RE = re.compile(r"mailto:([^\"'?>\s]+)", re.I)
TEL_RE = re.compile(r"tel:([+\d\s().\-]{6,25})", re.I)
WA_RE = re.compile(r"(?:wa\.me/|api\.whatsapp\.com/send\?phone=)(\+?\d{8,15})", re.I)
CFEMAIL_RE = re.compile(r'data-cfemail=["\']([0-9a-fA-F]{6,})["\']')
JSONLD_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                       re.I | re.S)
ANCHOR_RE = re.compile(r'<a\b[^>]*?href=["\']([^"\'#][^"\']*)["\'][^>]*>(.*?)</a>', re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")

# "name [at] host [dot] com", "name (at) host.com", "name at host dot com"
_AT = r"\s*[\[\(\{<]\s*at\s*[\]\)\}>]\s*|\s+at\s+"
_DOT = r"\s*[\[\(\{<]\s*dot\s*[\]\)\}>]\s*|\s+dot\s+"
OBFUSCATED_RE = re.compile(
    rf"([A-Za-z0-9._%+\-]+)(?:{_AT})([A-Za-z0-9\-]+(?:(?:{_DOT}|\.)[A-Za-z0-9\-]+)*"
    rf"(?:{_DOT}|\.)[A-Za-z]{{2,}})", re.I)

JUNK_DOMAINS = ("sentry.io", "sentry-next.wixpress.com", "wixpress.com", "example.com",
                "domain.com", "email.com", "yourdomain.com", "mysite.com", "schema.org",
                "w3.org", "godaddy.com", "squarespace.com")
JUNK_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js", ".woff",
                 ".woff2", ".ico")
JUNK_LOCALS = {"you", "your", "name", "yourname", "email", "user", "username", "test",
               "someone", "example", "john.doe", "johndoe"}

CONTACT_HINTS = ("contact", "reach", "touch", "enquir", "inquir", "about", "team", "people",
                 "our-story", "who-we-are", "support", "location", "find-us", "visit",
                 "book", "reservation", "appointment")
SKIP_LINK_HINTS = ("login", "cart", "checkout", "privacy", "terms", "policy", "blog/",
                   "wp-admin", "javascript:", "mailto:", "tel:", ".pdf", ".jpg", ".png")


# ------------------------------------------------------------------ emails --

def decode_cfemail(hexstr: str) -> str | None:
    """Cloudflare's email obfuscation: first byte is the XOR key."""
    try:
        raw = bytes.fromhex(hexstr)
        key = raw[0]
        return bytes(b ^ key for b in raw[1:]).decode("utf-8", "ignore") or None
    except (ValueError, IndexError):
        return None


def _clean_email(value: str) -> str | None:
    email = htmllib.unescape(value or "").strip().strip(".,;:<>()[]\"'").lower()
    email = email.split("?")[0]
    if not EMAIL_RE.fullmatch(email):
        return None
    local, _, domain = email.partition("@")
    if local in JUNK_LOCALS or any(domain.endswith(d) for d in JUNK_DOMAINS):
        return None
    if email.endswith(JUNK_SUFFIXES) or re.search(r"@\d+x\.", email):  # logo@2x.png
        return None
    return email


def extract_emails(page_html: str) -> list[str]:
    """Every plausible email on the page, de-duplicated, in discovery order."""
    if not page_html:
        return []
    found: list[str] = []

    def add(raw: str | None) -> None:
        email = _clean_email(raw) if raw else None
        if email and email not in found:
            found.append(email)

    for m in MAILTO_RE.findall(page_html):
        add(m)
    for m in CFEMAIL_RE.findall(page_html):
        add(decode_cfemail(m))
    text = htmllib.unescape(page_html)           # resolves &#64; style encodings
    for m in JSONLD_RE.findall(text):
        for item in _walk_jsonld(m):
            add(item.get("email"))
    for m in EMAIL_RE.findall(TAG_RE.sub(" ", text) if "@" not in text else text):
        add(m)
    for local, host in OBFUSCATED_RE.findall(TAG_RE.sub(" ", text)):
        host = re.sub(r"\s+", "", re.sub(_DOT, ".", host, flags=re.I))
        add(f"{local}@{host}")
    return found


def rank_emails(emails: list[str], site_url: str | None) -> list[str]:
    """Best first: own-domain beats third-party; named person beats generic mailbox
    only when on the business's own domain; free-mail (gmail etc.) is acceptable
    for small shops but ranks below own-domain."""
    site = canonical_domain(site_url)
    free = {"gmail.com", "yahoo.com", "yahoo.in", "outlook.com", "hotmail.com", "rediffmail.com",
            "icloud.com", "proton.me", "protonmail.com"}

    def key(email: str):
        domain = email.rpartition("@")[2]
        own = bool(site) and (domain == site or domain.endswith("." + site)
                              or site.endswith("." + domain))
        tier = 0 if own else (1 if domain in free else 2)
        return (tier, emails.index(email))

    return sorted(emails, key=key)


# ------------------------------------------------------------------ phones --

def extract_phones(page_html: str) -> list[str]:
    """tel: and WhatsApp-link numbers plus JSON-LD telephone, raw (not normalised)."""
    if not page_html:
        return []
    out: list[str] = []
    for m in TEL_RE.findall(page_html):
        out.append(re.sub(r"[^\d+]", "", m))
    for m in WA_RE.findall(page_html):
        out.append(m if m.startswith("+") else "+" + m)
    for m in JSONLD_RE.findall(htmllib.unescape(page_html)):
        for item in _walk_jsonld(m):
            tel = item.get("telephone")
            if isinstance(tel, str):
                out.append(re.sub(r"[^\d+]", "", tel))
    seen, uniq = set(), []
    for p in out:
        if len(re.sub(r"\D", "", p)) >= 8 and p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


# ----------------------------------------------------------------- JSON-LD --

def _walk_jsonld(blob: str):
    """Yield every dict in a JSON-LD blob (handles @graph, lists, bad JSON)."""
    try:
        data = json.loads(blob.strip())
    except (ValueError, TypeError):
        return
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            yield node
            stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            stack.extend(node)


def extract_structured(page_html: str) -> dict:
    """Business facts a site declares about itself via schema.org markup."""
    result: dict = {"name": None, "telephone": None, "email": None, "same_as": [],
                    "address": None, "opening_hours": None, "price_range": None}
    for blob in JSONLD_RE.findall(htmllib.unescape(page_html or "")):
        for node in _walk_jsonld(blob):
            types = node.get("@type")
            types = [types] if isinstance(types, str) else (types or [])
            if not any(t in ("LocalBusiness", "Organization", "Restaurant", "Store",
                             "ProfessionalService", "MedicalBusiness", "HealthAndBeautyBusiness",
                             "Dentist", "Physician", "AutomotiveBusiness", "FoodEstablishment")
                       or str(t).endswith("Business") for t in types):
                continue
            result["name"] = result["name"] or node.get("name")
            result["telephone"] = result["telephone"] or node.get("telephone")
            result["email"] = result["email"] or node.get("email")
            result["price_range"] = result["price_range"] or node.get("priceRange")
            result["opening_hours"] = result["opening_hours"] or node.get("openingHours")
            addr = node.get("address")
            if isinstance(addr, dict) and not result["address"]:
                result["address"] = ", ".join(str(addr[k]) for k in
                                              ("streetAddress", "addressLocality", "addressRegion",
                                               "postalCode") if addr.get(k))
            elif isinstance(addr, str) and not result["address"]:
                result["address"] = addr
            same = node.get("sameAs")
            for s in ([same] if isinstance(same, str) else same or []):
                if isinstance(s, str) and s not in result["same_as"]:
                    result["same_as"].append(s)
    return result


# ------------------------------------------------------------------- links --

def canonical_domain(url: str | None) -> str:
    """'https://www.Shop.example.com/x' -> 'shop.example.com' ('' if none)."""
    if not url:
        return ""
    raw = url.strip()
    if "//" not in raw:
        raw = "//" + raw
    host = (urlparse(raw).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def discover_contact_links(page_html: str, base_url: str, limit: int = 4) -> list[str]:
    """Same-site links whose URL or anchor text suggests contact info, best first."""
    base_domain = canonical_domain(base_url)
    scored: dict[str, int] = {}
    for href, inner in ANCHOR_RE.findall(page_html or ""):
        href = htmllib.unescape(href).strip()
        if any(s in href.lower() for s in SKIP_LINK_HINTS):
            continue
        absolute = urljoin(base_url, href).split("#")[0]
        if urlparse(absolute).scheme not in ("http", "https"):
            continue
        if canonical_domain(absolute) != base_domain:
            continue
        label = (absolute.lower() + " " + TAG_RE.sub("", inner).lower())
        score = 0
        for i, hint in enumerate(CONTACT_HINTS):
            if hint in label:
                score = max(score, len(CONTACT_HINTS) - i)
        if score:
            scored[absolute] = max(score, scored.get(absolute, 0))
    ordered = sorted(scored, key=lambda u: -scored[u])
    return ordered[:limit]
