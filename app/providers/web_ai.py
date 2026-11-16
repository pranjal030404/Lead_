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
from datetime import date
from urllib.parse import parse_qs, unquote, urlparse

from .. import http
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
        out, keys = [], set()
        for f in found:
            name = str(f.get("name") or "").strip()
            site = (f.get("website") or "").strip()
            dom = _domain(site if site.startswith("http") else "https://" + site) if site else ""
            key = f"web:{dom or name.lower()}"
            if not name or key in keys or (dom and _skip(dom)):
                continue
            keys.add(key)
            c = blank_candidate()
            c.update(place_id=key, business_name=name[:200], country=(None if f.get("global") else country or None),
                     city=(f.get("city") or city or None),
                     website_url=(f"https://{dom}" if dom else None),
                     owner_name=(str(f.get("founders"))[:200] if f.get("founders") else None),
                     source=(f"Web AI search ({'LLM' if settings.llm_base_url else 'heuristic'}; "
                             f"registered {f.get('founded') or 'n/a'}; {str(f.get('about') or '')[:120]})")[:250])
            out.append(c)
        return out[:max_results]

    def fetch_details(self, candidate):
        return {}

    def fetch_ratings(self, candidate):
        return {}
