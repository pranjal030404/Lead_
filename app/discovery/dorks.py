"""Search-query ("dork") generator for manual prospecting. Produces queries only -
the user runs them; we never scrape search engines."""

from __future__ import annotations

from urllib.parse import quote_plus


def generate(niche: str, city: str) -> list[dict]:
    n, c = niche.strip(), city.strip()
    if not n or not c:
        raise ValueError("niche and city are required")
    templates = [
        ("Instagram-only businesses", f'site:instagram.com "{n}" "{c}" "DM to book" OR "WhatsApp"'),
        ("Facebook pages without sites", f'site:facebook.com "{n}" "{c}" -website'),
        ("Sites under construction", f'"{n}" "{c}" "coming soon" OR "under construction" OR "site is being updated"'),
        ("Free-builder sites", f'"{n}" "{c}" (site:wixsite.com OR site:business.site OR site:weebly.com OR site:blogspot.com)'),
        ("Gmail-addressed businesses", f'"{n}" "{c}" "@gmail.com" contact'),
        ("Outdated copyright", f'"{n}" "{c}" "© 2015" OR "© 2016" OR "© 2017" OR "copyright 2014"'),
        ("New openings", f'"{n}" "{c}" "grand opening" OR "now open" OR "newly opened"'),
        ("Hiring (digital)", f'"{c}" "{n}" hiring "web developer" OR "digital marketing" OR "social media manager"'),
        ("Association members", f'"{n}" association members directory "{c}"'),
    ]
    return [{"label": l, "query": q, "url": "https://www.google.com/search?q=" + quote_plus(q)}
            for l, q in templates]
