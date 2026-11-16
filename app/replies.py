"""Inbound reply understanding: classify what a prospect said and act on it safely.

`classify()` is pure (rule-based, English + Hinglish + Bahasa Indonesia phrases, no
API call per reply, fully explainable - it returns the phrases that matched).
`apply_reply()` records the reply and moves the lead, with these guardrails:

  * STOP / do-not-contact -> permanent suppression (email + phone), lead closed.
  * Any real reply cancels pending automated follow-ups for that lead.
  * Auto-replies / out-of-office never count as engagement.
  * A lead only ever moves *forward* through the pipeline; WON is never set here
    (closing a deal stays a manual decision).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

INTENTS = ("STOP", "AUTO_REPLY", "REFERRAL", "NOT_INTERESTED", "NOT_NOW",
           "WANTS_MEETING", "ASKS_PRICE", "INTERESTED", "UNCLEAR")

# Checked in this order; first intent with a match wins (so "not interested" is
# seen before "interested", and "stop" before everything).
RULES: list[tuple[str, list[str]]] = [
    ("STOP", [r"\bstop\b(?! by| at| in)", r"unsubscribe", r"remove (me|my)", r"do not (contact|email|call|message)",
              r"don'?t (contact|email|call|message|text)", r"never (contact|email|message)",
              r"take me off", r"opt.?out", r"mat (bhejo|karo)", r"jangan (hubungi|kirim)", r"berhenti"]),
    ("AUTO_REPLY", [r"out of (the )?office", r"automatic reply", r"auto.?reply", r"on (annual )?leave",
                    r"i am (currently )?away", r"will (be )?(back|return)", r"delivery (status )?notification",
                    r"mailbox (is )?full", r"undeliverable"]),
    ("REFERRAL", [r"(contact|speak to|talk to|reach out to|ask for|email) (our |my |the )?\w+",
                  r"(he|she|they) (handles|manages|looks after)", r"forwarded? (this|your)", r"wrong person",
                  r"not the right person", r"(owner|manager|director) (is|will)", r"hubungi (bapak|ibu|pak|bu)"]),
    ("NOT_INTERESTED", [r"not interested", r"no thanks?", r"no,? thank", r"not (looking|required|needed)",
                        r"already (have|got|made|using)", r"we (are|re) (good|fine|set)", r"don'?t need",
                        r"nahi chahiye", r"zarurat nahi", r"tidak tertarik", r"tidak butuh", r"sudah punya"]),
    ("NOT_NOW", [r"not (right )?now", r"maybe later", r"(next|in a) (week|month|quarter|year)", r"busy (right now|at the moment|this)",
                 r"get back to you", r"reach (out )?(again )?(later|after)", r"after (diwali|festival|season|holidays?|eid|ramadan)",
                 r"baad (mein|me)", r"abhi nahi", r"nanti (dulu|saja)", r"lain kali", r"bulan depan"]),
    ("WANTS_MEETING", [r"\b(call|meet|meeting|demo|zoom|google meet|visit)\b.{0,40}\b(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|morning|evening|afternoon|\d{1,2}\s?(am|pm))",
                       r"(let'?s|can we|shall we|happy to|free for) (a )?(call|meet|talk|chat|discuss)", r"schedule (a )?(call|meeting|demo)",
                       r"book (a )?(call|slot|meeting)", r"when (are|can) you (free|available)", r"call me",
                       r"kab (milte|baat)", r"bisa (bertemu|telepon|zoom)", r"mari (kita )?(bertemu|diskusi)"]),
    ("ASKS_PRICE", [r"how much", r"\b(price|prices|pricing|cost|costs|charges?|rates?|quote|quotation|budget|fees?|package|packages)\b",
                    r"kitna (lagega|paisa|charge)", r"rate kya", r"berapa (harga|biaya)", r"\bharga\b", r"\bbiaya\b"]),
    ("INTERESTED", [r"interested", r"sounds (good|great|interesting)", r"tell me more", r"more (info|details|information)",
                    r"send (me )?(the )?(details|portfolio|samples?|examples?|proposal|info)", r"\byes\b", r"please (do|proceed|share|send)",
                    r"go ahead", r"let me know more", r"i('d| would) like", r"haan", r"zaroor", r"tertarik", r"boleh", r"silakan", r"kirim(kan)? (detail|info|contoh)"]),
]
_COMPILED = [(i, [re.compile(p, re.I) for p in pats]) for i, pats in RULES]

# What each intent does to the lead. status None = leave unchanged.
ACTIONS = {
    "STOP":           {"status": "LOST", "suppress": True,  "cancel": True,  "next_step": "Do not contact again"},
    "AUTO_REPLY":     {"status": None,   "suppress": False, "cancel": False, "next_step": "Auto-reply - wait for a human answer"},
    "REFERRAL":       {"status": "REPLIED", "suppress": False, "cancel": True, "next_step": "Contact the person they referred"},
    "NOT_INTERESTED": {"status": "LOST", "suppress": False, "cancel": True,  "next_step": "Closed - not interested"},
    "NOT_NOW":        {"status": None,   "suppress": False, "cancel": True,  "next_step": "Re-contact later"},
    "WANTS_MEETING":  {"status": "MEETING_SET", "suppress": False, "cancel": True, "next_step": "Confirm a time for the call"},
    "ASKS_PRICE":     {"status": "REPLIED", "suppress": False, "cancel": True, "next_step": "Send a quote / price range"},
    "INTERESTED":     {"status": "REPLIED", "suppress": False, "cancel": True, "next_step": "Send details and propose a call"},
    "UNCLEAR":        {"status": "REPLIED", "suppress": False, "cancel": True, "next_step": "Read the reply and respond personally"},
}
STAGE_ORDER = ["NEW", "CONTACTED", "REPLIED", "MEETING_SET", "PROPOSAL_SENT", "NEGOTIATION"]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<![\d])(\+?\d[\d\s().\-]{7,16}\d)(?![\d])")
NAME_RE = re.compile(r"(?:contact|speak to|talk to|reach out to|ask for|call|email|hubungi)\s+(?:our\s+|my\s+|the\s+)?"
                     r"((?:Mr\.?|Mrs\.?|Ms\.?|Dr\.?|Pak|Bu|Bapak|Ibu)?\s*[A-Z][a-z]+(?:\s[A-Z][a-z]+)?)")


def strip_quoted(text: str) -> str:
    """Drop quoted history so the prospect's *new* words are what we classify."""
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        if re.match(r"^on .{5,80} wrote:?$", s, re.I) or re.match(r"^-{2,}\s*(original|forwarded) message", s, re.I):
            break                                  # everything below is history
        if s.startswith(">"):
            continue
        out.append(line)
    return "\n".join(out).strip()


def defer_days(text: str) -> int:
    t = (text or "").lower()
    if re.search(r"next week|agle hafte|minggu depan", t):
        return 7
    if re.search(r"next quarter|3 months|three months", t):
        return 90
    if re.search(r"next year|saal", t):
        return 180
    if re.search(r"diwali|eid|ramadan|festival|season|holiday", t):
        return 45
    return 30


def extract_referral(text: str) -> dict:
    emails = list(dict.fromkeys(e.lower() for e in EMAIL_RE.findall(text or "")))
    phones = list(dict.fromkeys(re.sub(r"[^\d+]", "", p) for p in PHONE_RE.findall(text or "")
                                if len(re.sub(r"\D", "", p)) >= 8))
    m = NAME_RE.search(text or "")
    return {"name": m.group(1).strip() if m else None, "emails": emails[:3], "phones": phones[:3]}


def classify(text: str) -> dict:
    """-> {intent, confidence, matched, actions, referral?, defer_days?}. Pure."""
    body = strip_quoted(text or "")
    if not body:
        return {"intent": "UNCLEAR", "confidence": 0.0, "matched": [], "action": ACTIONS["UNCLEAR"]}
    for intent, patterns in _COMPILED:
        hits = [m.group(0) for p in patterns for m in [p.search(body)] if m]
        if hits:
            # Referral needs real contact detail to be worth acting on.
            if intent == "REFERRAL":
                ref = extract_referral(body)
                if not (ref["emails"] or ref["phones"] or ref["name"]):
                    continue
            conf = min(0.95, 0.6 + 0.15 * len(hits))
            if len(body) > 600 and intent not in ("STOP", "AUTO_REPLY"):
                conf -= 0.15                       # long replies are ambiguous
            out = {"intent": intent, "confidence": round(conf, 2), "matched": hits[:4],
                   "action": ACTIONS[intent]}
            if intent == "REFERRAL":
                out["referral"] = extract_referral(body)
            if intent == "NOT_NOW":
                out["defer_days"] = defer_days(body)
            return out
    return {"intent": "UNCLEAR", "confidence": 0.2, "matched": [], "action": ACTIONS["UNCLEAR"]}


def next_status(current: str | None, proposed: str | None) -> str | None:
    """Forward-only transition. LOST is allowed from anywhere except WON."""
    if not proposed or current == "WON":
        return None
    if proposed == "LOST":
        return None if current == "LOST" else "LOST"
    if current in ("LOST", None):
        return proposed                            # a lost lead that replies is revived
    if current in STAGE_ORDER and proposed in STAGE_ORDER:
        return proposed if STAGE_ORDER.index(proposed) > STAGE_ORDER.index(current) else None
    return None


# -------------------------------------------------------------- persistence --

def apply_reply(lead_id: int, text: str, channel: str = "email") -> dict:
    """Classify and record an inbound reply for a lead in the current scope."""
    from .db import current_ws, execute, lead_scope, log_automation, query, query_one, utcnow
    from .outreach import suppress

    scope, params = lead_scope()
    lead = query_one(f"SELECT * FROM leads WHERE id = ? AND {scope}", (lead_id, *params))
    if not lead:
        raise LookupError("Lead not found")
    verdict = classify(text)
    intent, action = verdict["intent"], verdict["action"]
    now = utcnow()

    execute(
        """INSERT INTO interactions (workspace_id, lead_id, type, direction, date, subject, content,
               outcome, next_step, follow_up_date, automated, created_at)
           VALUES(?,?,?,'in',?,?,?,?,?,?,0,?)""",
        (current_ws(), lead_id, channel, now, f"Reply ({intent})", (text or "")[:5000], intent,
         action["next_step"], None, now))

    changes = {"suppressed": [], "cancelled_messages": 0, "status": None, "follow_up_date": None}
    if action["suppress"]:
        for value, kind in ((lead.get("email"), "email"), (lead.get("phone"), "phone"),
                            (lead.get("whatsapp"), "phone")):
            if value:
                suppress(value, kind, reason="reply: stop request")
                changes["suppressed"].append(value)
    if action["cancel"]:
        changes["cancelled_messages"] = execute(
            "UPDATE approval_queue SET status = 'SKIPPED', error_message = ? "
            "WHERE workspace_id = ? AND lead_id = ? AND status IN ('PENDING','APPROVED')",
            (f"cancelled: prospect replied ({intent})", current_ws(), lead_id)) or 0

    new_status = next_status(lead.get("status"), action["status"])
    follow = None
    if intent == "NOT_NOW":
        follow = (datetime.now(timezone.utc) + timedelta(days=verdict["defer_days"])).date().isoformat()
    if new_status or follow:
        execute("UPDATE leads SET status = COALESCE(?, status), follow_up_date = COALESCE(?, follow_up_date), "
                "updated_at = ? WHERE id = ? AND workspace_id = ?",
                (new_status, follow, now, lead_id, current_ws()))
    changes["status"], changes["follow_up_date"] = new_status, follow
    if intent != "AUTO_REPLY":
        execute("UPDATE leads SET last_contacted_date = COALESCE(last_contacted_date, ?) "
                "WHERE id = ? AND workspace_id = ?", (now, lead_id, current_ws()))
    if intent != "AUTO_REPLY":
        from . import campaigns
        changes["campaigns_stopped"] = campaigns.stop_for_lead(
            lead_id, "prospect asked to stop" if intent == "STOP" else f"replied ({intent})")
    log_automation("reply", f"Lead #{lead_id} replied: {intent} ({verdict['confidence']})")
    return {**verdict, "changes": changes}
