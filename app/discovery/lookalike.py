"""Reverse prospecting: rank candidates by similarity to leads that converted. Pure."""

from __future__ import annotations

import math


def _vec(lead: dict) -> dict:
    return {f"niche:{(lead.get('niche') or '').lower()}": 2.0,
            f"city:{(lead.get('city') or '').lower()}": 1.0,
            f"web:{'yes' if lead.get('website_url') else 'no'}": 1.0,
            f"band:{int(math.log2((lead.get('google_reviews') or 0) + 1))}": 1.0,
            f"rating:{round(lead.get('google_rating') or 0)}": 0.5}


def _cos(a: dict, b: dict) -> float:
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    na, nb = math.sqrt(sum(v * v for v in a.values())), math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def rank(won: list[dict], candidates: list[dict], top: int = 25) -> list[dict]:
    if not won:
        return []
    centroid: dict = {}
    for w in won:
        for k, v in _vec(w).items():
            centroid[k] = centroid.get(k, 0) + v / len(won)
    scored = [{"id": c.get("id"), "business_name": c.get("business_name"),
               "similarity": round(_cos(centroid, _vec(c)) * 100)} for c in candidates]
    return sorted(scored, key=lambda r: -r["similarity"])[:top]
