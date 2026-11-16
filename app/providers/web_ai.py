"""Web "AI" finder for new startups - free, open source, no paid key.

1. Searches the open web (DuckDuckGo, keyless) with several "new <niche> startup" queries.
2. Optionally reads the top pages (listicles, launch posts, funding news).
3. Extracts companies:
   - with any OpenAI-compatible model if LLM_BASE_URL is set (Ollama, llama.cpp, vLLM, or a free
     tier such as Groq / OpenRouter free models): the model reads the results and returns
     structured startups (name, website, city, founders, what they do);
   - otherwise a built-in heuristic picks company homepages out of the results.
Each company becomes a lead with place_id "web:<domain>" so duplicates collapse."""

from __future__ import annotations

import html as htmllib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, unquote, urlparse

from .. import contactparse, http, netsafe, people as people_mod
from ..config import settings
from .base import blank_candidate

DDG = "https://html.duckduckgo.com/html/"
SKIP = {"linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com",
        "wikipedia.org", "reddit.com", "quora.com", "medium.com", "crunchbase.com", "tracxn.com",
        "producthunt.com", "yourstory.com", "inc42.com", "techcrunch.com", "forbes.com",
        "economictimes.indiatimes.com", "startupindia.gov.in", "github.com", "pinterest.com",
        "glassdoor.com", "indeed.com", "naukri.com", "amazon.com", "google.com", "bing.com",
        "duckduckgo.com", "entrackr.com", "dealroom.co", "f6s.com", "angel.co", "wellfound.com",
        "startupranking.com", "ycombinator.com", "tech.eu", "sifted.eu", "beauhurst.com",
        "gov.uk", "bbc.com", "theguardian.com", "linkedin.cn", "clutch.co", "goodfirms.co",
        "tiktok.com", "bloomberg.com", "reuters.com", "businessinsider.com", "wired.com"}
_TAG = re.compile(r"<[^>]+>")

MIN_BATCH, MAX_BATCH = 10, 20          # one run returns 10-20 startups, never more
MAX_DOMAIN_AGE_DAYS = 3 * 365          # "new": the domain was registered in the last 3 years
_SENTENCE = re.compile(r"\b(is|are|was|were|how|why|what|with|your|you|our|we|to|for|and|the|of|a|an|in|on|"
                       r"get|new|launch|launches|launched|built|build|help|helps|find|make|makes)\b", re.I)
_META = re.compile(r'<meta[^>]+(?:name|property)=["\'](og:site_name|og:description|description)["\'][^>]*>', re.I)
_META_VAL = re.compile(r'content=["\']([^"\']*)["\']', re.I)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def clamp_batch(n: int | None) -> int:
    """Requested result count forced into the 10-20 window."""
    try:
        n = int(n or MAX_BATCH)
    except (TypeError, ValueError):
        n = MAX_BATCH
    return max(MIN_BATCH, min(n, MAX_BATCH))


def good_name(name: str | None) -> bool:
    """A company name, not a headline: short, no sentence words."""
    n = (name or "").strip()
    words = n.split()
    if not (2 <= len(n) <= 40) or len(words) > 4:
        return False
    if len(words) >= 3 and _SENTENCE.search(n):
        return False
    return not re.search(r"[.!?]$", n)


def rdap_created(domain: str) -> str | None:
    """Domain registration date (YYYY-MM-DD) from RDAP - free, open, keyless. It is the best
    public proof of when a web startup was 'born'."""
    try:
        r = http.get(f"https://rdap.org/domain/{domain}", breaker="rdap", retries=1, timeout=8,
                     follow_redirects=True)
        if r.status_code >= 400:
            return None
        for ev in r.json().get("events", []):
            if ev.get("eventAction") == "registration" and ev.get("eventDate"):
                return ev["eventDate"][:10]
    except Exception:  # noqa: BLE001
        pass
    return None


def profile(site: str) -> dict | None:
    """Verify a startup's own site is alive and read every public detail off it:
    name, description, emails, phones, address, founders. None when the site is dead."""
    try:
        r = netsafe.safe_get(site, breaker="webpage", retries=1, timeout=8)
    except Exception:  # noqa: BLE001
        return None
    if r.status_code >= 400 or "html" not in r.headers.get("content-type", "html"):
        return None
    base = str(r.url).rstrip("/")
    pages = [r.text or ""]
    for link in contactparse.discover_contact_links(pages[0], base + "/", limit=2):
        try:
            x = netsafe.safe_get(link, breaker="webpage", retries=1, timeout=6)
            if x.status_code < 400:
                pages.append(x.text or "")
        except Exception:  # noqa: BLE001
            continue
    meta = {}
    for m in _META.finditer(pages[0]):
        v = _META_VAL.search(m.group(0))
        if v:
            meta.setdefault(m.group(1).lower(), htmllib.unescape(v.group(1)).strip())
    title = _text(_TITLE.search(pages[0]).group(1)) if _TITLE.search(pages[0]) else ""
    struct: dict = {}
    emails: list[str] = []
    phones: list[str] = []
    founders: list[dict] = []
    for pg in pages:
        st = contactparse.extract_structured(pg)
        for k, v in st.items():
            if v and not struct.get(k):
                struct[k] = v
        emails += contactparse.extract_emails(pg)
        phones += contactparse.extract_phones(pg)
        founders += people_mod.extract_people(pg)
    emails = contactparse.rank_emails(list(dict.fromkeys(emails)), base)
    phones = list(dict.fromkeys(phones))
    if struct.get("telephone"):
        phones.insert(0, re.sub(r"[^\d+]", "", struct["telephone"]))
    return {"site_name": meta.get("og:site_name") or struct.get("name"),
            "title": title, "about": meta.get("og:description") or meta.get("description") or "",
            "email": emails[0] if emails else struct.get("email"),
            "phone": phones[0] if phones else None, "address": struct.get("address"),
            "founders": [f["name"] for f in founders if f.get("name")][:3]}


def _age_ok(created: str | None) -> bool:
    if not created:
        return True          # RDAP gave nothing (some TLDs): don't punish
    try:
        d = datetime.fromisoformat(created).replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - d).days <= MAX_DOMAIN_AGE_DAYS


def complete(f: dict) -> dict | None:
    """Verify one found startup and fill in everything public about it. None = reject."""
    site = (f.get("website") or "").strip()
    dom = _domain(site if site.startswith("http") else "https://" + site) if site else ""
    if not dom or _skip(dom):
        return None
    prof = profile(f"https://{dom}")
    if prof is None:
        return None
    created = rdap_created(dom)
    if not _age_ok(created):
        return None
    name = f.get("name") if good_name(f.get("name")) else None
    if not name:
        for cand in (prof["site_name"], re.split(r"\s[-|–—:·]\s|\s\|\s", prof["title"])[0]):
            if good_name(cand):
                name = cand.strip(); break
    if not name:
        name = dom.split(".")[0].replace("-", " ").title()
    return {**f, "name": name, "dom": dom, "created": created, "prof": prof,
            "about": (prof["about"] or f.get("about") or "")[:160]}


def _richness(c: dict) -> int:
    p = c["prof"]
    return (3 * bool(c["created"]) + 2 * bool(p["email"]) + 2 * bool(p["phone"]) + 2 * bool(p["address"])
            + 2 * bool(p["founders"] or c.get("founders")) + bool(c["about"]))


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(_TAG.sub(" ", s))).strip()


def _domain(url: str) -> str:
    host = (urlparse(url).netloc or "").lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _skip(domain: str) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in SKIP)


def ddg_search(query: str, limit: int = 10) -> list[dict]:
    r = http.post(DDG, breaker="ddg", data={"q": query}, retries=2,
                  headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/125.0"})
    if r.status_code >= 400:
        raise RuntimeError(f"web search error {r.status_code}")
    out = []
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
                         r'(?:.*?class="result__snippet"[^>]*>(.*?)</a>)?', r.text, re.S):
        href = htmllib.unescape(m.group(1))
        if "uddg=" in href:
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        if href.startswith("//"):
            href = "https:" + href
        out.append({"url": href, "title": _text(m.group(2)), "snippet": _text(m.group(3) or "")})
        if len(out) >= limit:
            break
    return out


def searx_search(query: str, limit: int = 10) -> list[dict]:
    """Self-hosted SearXNG (open source metasearch): set SEARXNG_URL, e.g. http://localhost:8888."""
    r = http.get(settings.searxng_url.rstrip("/") + "/search", breaker="searx", retries=2,
                 params={"q": query, "format": "json"})
    if r.status_code >= 400:
        raise RuntimeError(f"SearXNG error {r.status_code}")
    return [{"url": x["url"], "title": x.get("title", ""), "snippet": x.get("content", "")}
            for x in r.json().get("results", [])[:limit]]


def hn_launches(niche: str, place: str, limit: int = 30) -> list[dict]:
    """Keyless, never-blocked source: recent 'Show HN' product launches (hn.algolia.com)."""
    out = []
    for q in dict.fromkeys([f"{niche} {place}".strip(), niche]):
        r = http.get("https://hn.algolia.com/api/v1/search_by_date", breaker="hn", retries=2,
                     params={"query": q, "tags": "show_hn", "hitsPerPage": limit})
        if r.status_code >= 400:
            continue
        for h in r.json().get("hits", []):
            title = h.get("title") or ""
            url = h.get("url") or ""
            d = _domain(url)
            if not url or not d or _skip(d):
                continue
            body = re.sub(r"^Show HN:\s*", "", title)
            name = re.split(r"\s[-–—:|]\s|:\s", body)[0].strip()[:60]
            out.append({"name": name or d, "website": f"https://{d}", "city": None, "founders": None, "global": True,
                        "about": body, "founded": (h.get("created_at") or "")[:10]})
        if out:
            break
    return out


def web_search(query: str, limit: int = 10) -> list[dict]:
    fns = ([searx_search] if settings.searxng_url else []) + [ddg_search]
    for fn in fns:
        for attempt in range(2):
            try:
                res = fn(query, limit)
                if res:
                    return res
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2)
    return []


def _page_text(url: str, cap: int = 4000) -> str:
    try:
        r = http.get(url, breaker="webpage", retries=1, timeout=8, follow_redirects=True)
        if r.status_code >= 400 or "html" not in r.headers.get("content-type", ""):
            return ""
        body = re.sub(r"(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", r.text)
        return _text(body)[:cap]
    except Exception:  # noqa: BLE001
        return ""


def llm_extract(niche: str, place: str, material: str) -> list[dict]:
    """Ask an OpenAI-compatible model to pull startups out of the search material."""
    prompt = (
        f"From the web search results below, list NEWLY launched startups/companies (founded or "
        f"launched in the last 2 years) in the niche '{niche}' located in {place}. Only real "
        f"companies that appear in the text. Return ONLY a JSON array; each item: "
        f'{{"name": str, "website": str|null, "city": str|null, "founders": str|null, '
        f'"about": str, "founded": str|null}}. No prose.\n\n{material[:14000]}')
    headers = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"
    r = http.post(settings.llm_base_url.rstrip("/") + "/chat/completions", breaker="llm",
                  retries=2, timeout=120, headers=headers,
                  json={"model": settings.llm_model, "temperature": 0.1,
                        "messages": [{"role": "user", "content": prompt}]})
    if r.status_code >= 400:
        raise RuntimeError(f"LLM error {r.status_code}: {r.text[:200]}")
    content = r.json()["choices"][0]["message"]["content"]
    m = re.search(r"\[.*\]", content, re.S)
    data = json.loads(m.group(0)) if m else []
    return [d for d in data if isinstance(d, dict) and d.get("name")]


_ARTICLE = re.compile(r"\b(top|best|list|\d+|ways|guide|how|why|trends|report|news|ranking)\b", re.I)
_BAD_HEAD = re.compile(r"(startup|conclusion|faq|related|share|comment|newsletter|subscribe|contact|about us|"
                       r"privacy|read more|table of contents|introduction|why|how|what|latest|trending|"
                       r"navigation|thoughts|about|final|summary|overview|home|key |takeaway|verdict|pricing|"
                       r"popular|follow|join|sign|menu|search|categories|tags|advertis)", re.I)


def headings(url: str) -> list[dict]:
    """Company-looking h2/h3 headings from a listicle page, e.g. '3. Zepto - quick commerce'."""
    try:
        r = http.get(url, breaker="webpage", retries=1, timeout=8, follow_redirects=True)
        if r.status_code >= 400:
            return []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for m in re.finditer(r"<h([23])[^>]*>(.*?)</h\1>(.{0,1500})", r.text, re.S | re.I):
        t = _text(m.group(2))
        t = re.sub(r"^\s*(#?\d+[\).:\-]?\s*)", "", t)
        name = re.split(r"\s[-|–—:]\s|:\s|\s\(", t)[0].strip()
        if not (2 <= len(name) <= 40) or _BAD_HEAD.search(name) or len(name.split()) > 4:
            continue
        about = _text(m.group(3))[:200]
        site = None
        for lm in re.finditer(r'href="(https?://[^"]+)"', m.group(2) + m.group(3)):
            d = _domain(lm.group(1))
            if d and not _skip(d) and d != _domain(url):
                site = f"https://{d}"; break
        out.append({"name": name, "website": site, "city": None, "founders": None,
                    "about": about, "founded": None})
    return out


def heuristic_extract(results: list[dict]) -> list[dict]:
    out = []
    for r in [x for x in results if _ARTICLE.search(x["title"])][:5]:
        out += headings(r["url"])
    for r in results:
        if _ARTICLE.search(r["title"]):
            continue
        d = _domain(r["url"])
        path = urlparse(r["url"]).path.strip("/")
        if not d or _skip(d) or path.count("/") > 1:
            continue
        name = re.split(r"\s[-|–—:·]\s|\s\|\s", r["title"])[0].strip()
        if not name or len(name) > 60:
            name = d.split(".")[0].title()
        out.append({"name": name, "website": f"https://{d}", "city": None, "founders": None,
                    "about": r["snippet"], "founded": None})
    return out


class WebAiProvider:
    name = "web_ai"
    billed = False

    def search(self, niche, city, area="", country="", max_results=20,
               lat=None, lng=None, radius_m=None):
        place = ", ".join(x for x in (city, country) if x) or "anywhere"
        yr = date.today().year
        base = niche or "tech"
        queries = [f"new {base} startup launched {yr} {place}",
                   f"{base} startups founded {yr} {place}",
                   f"recently launched {base} startup {place} founders",
                   f"{base} startup seed funding {yr} {place}"]
        results, seen, errors = [], set(), []
        for q in queries:
            time.sleep(1.5)
            try:
                for r in web_search(q):
                    if r["url"] not in seen:
                        seen.add(r["url"]); results.append(r)
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
        found: list[dict] = []
        if not results:       # search engines blocked this IP: fall back to recent product launches
            found = hn_launches(base, city or country)
            if not found:
                raise RuntimeError("Web search returned nothing - the search engine is rate-limiting this "
                                   "server. Retry in a few minutes or set SEARXNG_URL (self-hosted, free).")
        if settings.llm_base_url and results:
            material = "\n".join(f"- {r['title']} ({r['url']}): {r['snippet']}" for r in results)
            for r in results[:6]:        # read the top pages (listicles / launch posts)
                t = _page_text(r["url"])
                if t:
                    material += f"\n\n## {r['title']}\n{t}"
            found = llm_extract(base, place, material)
        if not found and results:
            found = heuristic_extract(results)
        limit = clamp_batch(max_results)
        pool, keys = [], set()
        for f in found:
            name = str(f.get("name") or "").strip()
            site = (f.get("website") or "").strip()
            dom = _domain(site if site.startswith("http") else "https://" + site) if site else ""
            k = dom or name.lower()
            if not name or not dom or k in keys or _skip(dom):
                continue
            keys.add(k)
            pool.append(f)
        # verify + complete each (parallel), keep the richest `limit`
        with ThreadPoolExecutor(max_workers=6) as ex:
            done = [c for c in ex.map(complete, pool[:limit * 3]) if c]
        done.sort(key=_richness, reverse=True)
        out = []
        for c in done[:limit]:
            p = c["prof"]
            cand = blank_candidate()
            owner = p["founders"] or ([c["founders"]] if c.get("founders") else [])
            cand.update(
                place_id=f"web:{c['dom']}", business_name=c["name"][:200],
                country=(None if c.get("global") else country or None),
                city=(c.get("city") or city or None), address=(p["address"] or None),
                phone=p["phone"], email=p["email"], website_url=f"https://{c['dom']}",
                owner_name=(", ".join(owner)[:200] if owner else None),
                source=(f"Web AI search ({'LLM' if settings.llm_base_url else 'heuristic'}; "
                        f"registered {c['created'] or c.get('founded') or 'n/a'}; {c['about']})")[:250])
            out.append(cand)
        return out

    def fetch_details(self, candidate):
        return {}

    def fetch_ratings(self, candidate):
        return {}
