"""Decision-maker contacts.

People enter the system only from real sources: a user typing them in, the
business's own structured data (schema.org Person on its website), or an
enrichment provider's response. Nothing here invents a name, title or address.
`decision_maker_probability` is a transparent keyword heuristic on the title,
not a model's guess, and is labelled as such in the API.
"""

from __future__ import annotations

import json
import re

from .db import current_ws, execute, query, query_one, utcnow
from .lineage import SOURCE_CONFIDENCE

ROLE_CATEGORIES = ("OWNER", "FOUNDER", "CEO", "DIRECTOR", "PARTNER",
                   "MARKETING_MANAGER", "OPERATIONS_MANAGER", "IT_MANAGER", "OTHER")

# (regex on the lower-cased title, category, decision-maker probability) - first match wins.
_TITLE_RULES = [
    (r"\bco-?founder\b|\bfounder\b", "FOUNDER", 0.95),
    (r"\bowner\b|\bproprietor\b", "OWNER", 0.95),
    (r"\bceo\b|chief executive|managing director|\bmd\b|\bpresident\b", "CEO", 0.90),
    (r"\bpartner\b", "PARTNER", 0.80),
    (r"\bdirector\b|\bchairman\b|\bprincipal\b", "DIRECTOR", 0.75),
    (r"marketing|growth|brand|digital", "MARKETING_MANAGER", 0.55),
    (r"operations|\bops\b|general manager|\bgm\b|office manager", "OPERATIONS_MANAGER", 0.55),
    (r"\bit\b|technology|\bcto\b|engineering|developer|technical", "IT_MANAGER", 0.45),
    (r"manager|head of|\bhead\b|\blead\b", "OTHER", 0.30),
]


def classify_title(title: str | None) -> tuple[str, float]:
    low = (title or "").lower()
    for pattern, category, probability in _TITLE_RULES:
        if re.search(pattern, low):
            return category, probability
    return "OTHER", 0.10 if title else 0.05


def add_contact(lead_id: int, name: str, title: str | None = None, email: str | None = None,
                phone: str | None = None, linkedin_url: str | None = None,
                source: str = "manual", source_type: str = "user",
                confidence: float | None = None, notes: str | None = None) -> dict | None:
    """Create or update a contact. Re-seeing a person fills blanks but keeps the
    better-sourced values (higher confidence wins the provenance)."""
    name = re.sub(r"\s+", " ", (name or "")).strip()
    if len(name) < 2:
        return None
    category, probability = classify_title(title)
    conf = SOURCE_CONFIDENCE.get(source, 0.5) if confidence is None else confidence
    now = utcnow()
    execute(
        """INSERT INTO contacts
           (workspace_id, lead_id, name, title, role_category, email, phone, linkedin_url,
            source, source_type, confidence, decision_maker_probability, notes,
            created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON DUPLICATE KEY UPDATE
             role_category = IF(title IS NULL, VALUES(role_category), role_category),
             decision_maker_probability = IF(title IS NULL, VALUES(decision_maker_probability),
                                             decision_maker_probability),
             title = COALESCE(title, VALUES(title)),
             email = COALESCE(email, VALUES(email)),
             phone = COALESCE(phone, VALUES(phone)),
             linkedin_url = COALESCE(linkedin_url, VALUES(linkedin_url)),
             source = IF(VALUES(confidence) > confidence, VALUES(source), source),
             source_type = IF(VALUES(confidence) > confidence, VALUES(source_type), source_type),
             confidence = GREATEST(confidence, VALUES(confidence)),
             updated_at = VALUES(updated_at)""",
        (current_ws(), lead_id, name, title, category, email, phone, linkedin_url, source,
         source_type, conf, probability, notes, now, now),
    )
    contact = query_one(
        "SELECT * FROM contacts WHERE workspace_id = ? AND lead_id = ? AND name = ?",
        (current_ws(), lead_id, name))
    if contact and contact.get("email"):
        from .emailintel import register
        register(lead_id, contact["email"], source, source_type, confidence=conf,
                 contact_id=contact["id"])
    return contact


def extract_people(html: str) -> list[dict]:
    """People the page itself publishes as schema.org JSON-LD (Person nodes, plus
    `founder`/`employee`/`member` of an Organization). Returns [{name, title}].
    Reads structured data the site chose to expose; does no guessing."""
    people: dict[str, dict] = {}

    def walk(node, depth=0):
        if depth > 6:
            return
        if isinstance(node, list):
            for item in node:
                walk(item, depth + 1)
            return
        if not isinstance(node, dict):
            return
        kind = node.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if "Person" in kinds and isinstance(node.get("name"), str):
            people.setdefault(node["name"].strip(), {
                "name": node["name"].strip(),
                "title": node.get("jobTitle") if isinstance(node.get("jobTitle"), str) else None})
        for key, role in (("founder", "Founder"), ("founders", "Founder"),
                          ("employee", None), ("member", None), ("author", None)):
            if key == "author":
                continue  # blog authors are not decision makers
            child = node.get(key)
            for item in (child if isinstance(child, list) else [child]):
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    people.setdefault(item["name"].strip(), {
                        "name": item["name"].strip(),
                        "title": item.get("jobTitle") if isinstance(item.get("jobTitle"), str)
                        else role})
        for key, value in node.items():
            if key in ("@graph", "mainEntity", "about", "publisher", "provider"):
                walk(value, depth + 1)

    for block in re.findall(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html or "", re.I | re.S):
        try:
            walk(json.loads(block.strip()))
        except (ValueError, TypeError):
            continue
    return [p for p in people.values() if len(p["name"].split()) >= 2][:10]


def list_for_lead(lead_id: int) -> list[dict]:
    return query("SELECT * FROM contacts WHERE workspace_id = ? AND lead_id = ? "
                 "ORDER BY decision_maker_probability DESC, id", (current_ws(), lead_id))


def best_decision_maker(lead_id: int) -> dict | None:
    rows = list_for_lead(lead_id)
    return rows[0] if rows and rows[0]["decision_maker_probability"] >= 0.5 else None
