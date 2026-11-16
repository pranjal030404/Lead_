"""Learn from "Good fit" / "Not a fit" marks.

Every verdict is stored as an interaction (so it is auditable and shows in the timeline).
`learn()` turns the history into multipliers per lead *feature* (niche, city, website state,
review band, rule-priority): a feature that is mostly marked "not a fit" is dampened, one that
is mostly "good" is boosted. Small samples are shrunk toward neutral and multipliers are clamped
to 0.5-1.5, so three unlucky marks can never zero out a niche.
"""

from __future__ import annotations

import json
import math

MIN_SAMPLES = 3
MULT_MIN, MULT_MAX = 0.5, 1.5
SMOOTHING = 2.0                       # pseudo-counts pulling small samples to the base rate
VERDICTS = ("GOOD_FIT", "NOT_A_FIT")
NOT_FIT_REASONS = ("wrong_industry", "too_small", "too_big", "already_has_solution", "no_budget",
                   "wrong_location", "bad_data", "other")


def features(lead: dict) -> list[str]:
    ws, listed = lead.get("website_score"), bool((lead.get("website_url") or "").strip())
    web = "no_site" if (not listed or lead.get("has_website") == 0) else \
          "weak_site" if (ws is not None and ws < 7) else "ok_site"
    rev = lead.get("google_reviews")
    band = "reviews_unknown" if rev is None else f"reviews_{0 if rev == 0 else int(math.log2(rev + 1))}"
    out = [f"web:{web}", f"band:{band}"]
    if lead.get("niche"):
        out.append(f"niche:{str(lead['niche']).strip().lower()}")
    if lead.get("city"):
        out.append(f"city:{str(lead['city']).strip().lower()}")
    if lead.get("lead_priority"):
        out.append(f"priority:{lead['lead_priority']}")
    return out


def learn(examples: list[tuple[dict, bool]]) -> dict:
    """examples: [(lead_dict, is_good)] -> {feature: {"mult", "good", "bad"}}. Pure."""
    if not examples:
        return {}
    base = sum(1 for _, g in examples if g) / len(examples)
    tally: dict[str, list[int]] = {}
    for lead, good in examples:
        for f in features(lead):
            t = tally.setdefault(f, [0, 0])
            t[0 if good else 1] += 1
    out = {}
    for f, (good, bad) in tally.items():
        n = good + bad
        if n < MIN_SAMPLES:
            continue
        rate = (good + SMOOTHING * base) / (n + SMOOTHING)         # shrunk toward the base rate
        mult = 1.0 + (rate - base) * 2.0
        out[f] = {"mult": round(max(MULT_MIN, min(MULT_MAX, mult)), 3), "good": good, "bad": bad}
    return out


def adjust(score: int | None, lead: dict, adjustments: dict) -> dict:
    """Apply learned multipliers to a 0-100 score. Geometric mean keeps one feature from dominating."""
    if score is None:
        return {"score": None, "factor": 1.0, "reasons": []}
    used = [(f, adjustments[f]) for f in features(lead) if f in adjustments]
    if not used:
        return {"score": score, "factor": 1.0, "reasons": []}
    factor = math.exp(sum(math.log(a["mult"]) for _, a in used) / len(used))
    reasons = [f"{f.split(':', 1)[1]}: {a['good']} good / {a['bad']} not-fit -> x{a['mult']}"
               for f, a in sorted(used, key=lambda t: abs(t[1]["mult"] - 1), reverse=True)[:4]
               if abs(a["mult"] - 1) >= 0.05]
    return {"score": max(0, min(100, round(score * factor))), "factor": round(factor, 3), "reasons": reasons}
