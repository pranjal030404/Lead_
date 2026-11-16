"""Mine review text for direct, quotable pain related to web presence. Pure."""

from __future__ import annotations

import re

PATTERNS = {
    "no_website": r"(no|without|doesn'?t have( a)?|don'?t have( a)?|couldn'?t find( their)?|can'?t find( their)?)\s+(official\s+)?(web ?site|web page|online presence)",
    "no_online_booking": r"(no|can'?t|couldn'?t|unable to|wish (they|you) (had|offered?))\s+(online\s+)?(book|booking|appointment|reserv|order)",
    "hard_to_reach": r"(never|doesn'?t|don'?t|didn'?t)\s+(answer|pick up|respond|reply)|hard to (reach|contact|get through)|phone (is )?(always )?(busy|off)|unreachable",
    "whatsapp_only": r"only (on )?whatsapp|whatsapp (only|to book)",
    "outdated_info": r"(wrong|outdated|old|incorrect)\s+(hours|timings|address|number|info|menu)|(is|are) (now )?closed (on|at) .{0,20}(but|despite)",
    "wants_menu_prices": r"(no|can'?t find|couldn'?t find|where('s| is)).{0,15}(menu|price|prices|rates|catalog)",
}
_COMPILED = {k: re.compile(v, re.I) for k, v in PATTERNS.items()}


def analyse(reviews: list[str | dict]) -> dict:
    """reviews: strings or {'text','rating'}. -> {hits, quotes, pain_score}."""
    hits: dict[str, int] = {}
    quotes: list[dict] = []
    for r in reviews or []:
        text = (r.get("text") if isinstance(r, dict) else r) or ""
        for key, rx in _COMPILED.items():
            m = rx.search(text)
            if m:
                hits[key] = hits.get(key, 0) + 1
                if len(quotes) < 5:
                    s = max(0, m.start() - 60)
                    quotes.append({"signal": key, "quote": text[s:m.end() + 60].strip()})
    total = sum(hits.values())
    return {"hits": hits, "quotes": quotes, "pain_score": min(100, total * 25),
            "reviews_scanned": len(reviews or [])}


def review_velocity(snapshots: list[dict]) -> dict:
    """snapshots: [{'at': ISO date, 'reviews': int}] -> reviews/month and a flag."""
    from datetime import datetime
    pts = []
    for s in snapshots or []:
        try:
            pts.append((datetime.fromisoformat(str(s["at"]).replace("Z", "+00:00")), int(s["reviews"])))
        except (KeyError, ValueError, TypeError):
            continue
    pts.sort()
    if len(pts) < 2 or pts[-1][0] == pts[0][0]:
        return {"per_month": None, "rapid": False, "note": "needs two dated snapshots"}
    days = (pts[-1][0] - pts[0][0]).days or 1
    per_month = round((pts[-1][1] - pts[0][1]) / days * 30, 1)
    return {"per_month": per_month, "rapid": per_month >= 8, "note": None}
