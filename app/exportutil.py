"""Safe export helpers.

A CSV cell starting with = + - @ (or tab/CR) is executed as a formula by Excel
and Google Sheets. Business names and scraped text are attacker-controlled, so
every exported cell is neutralised with a leading apostrophe.
"""

from __future__ import annotations

import csv
import io
import json

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value):
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def to_csv(rows: list[dict]) -> str:
    buffer = io.StringIO()
    if not rows:
        return "no leads matched\n"
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()),
                            extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: safe_cell(v) for k, v in row.items()})
    return buffer.getvalue()


def to_jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(r, default=str, ensure_ascii=False) + "\n" for r in rows)


def _vesc(v) -> str:
    return str(v or "").replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def to_vcard(rows: list[dict]) -> str:
    """vCard 3.0, one contact per lead - importable into phone/Google/Outlook contacts."""
    cards = []
    for r in rows:
        name = r.get("business_name")
        if not name:
            continue
        lines = ["BEGIN:VCARD", "VERSION:3.0", f"FN:{_vesc(name)}", f"ORG:{_vesc(name)}"]
        if r.get("owner_name"):
            lines.append(f"N:{_vesc(r['owner_name'])};;;;")
        if r.get("phone"):
            lines.append(f"TEL;TYPE=WORK,VOICE:{_vesc(r['phone'])}")
        if r.get("email"):
            lines.append(f"EMAIL;TYPE=INTERNET,WORK:{_vesc(r['email'])}")
        if r.get("website_url"):
            lines.append(f"URL:{_vesc(r['website_url'])}")
        addr = [r.get("address"), r.get("city"), r.get("state"), r.get("pincode"), r.get("country")]
        if any(addr):
            lines.append("ADR;TYPE=WORK:;;" + ";".join(_vesc(a) for a in (addr[0], addr[1], addr[2], addr[3], addr[4])))
        if r.get("niche"):
            lines.append(f"CATEGORIES:{_vesc(r['niche'])}")
        note = " | ".join(x for x in (f"Status: {r['status']}" if r.get("status") else "",
                                      f"Score: {r['lead_score']}/10" if r.get("lead_score") is not None else "") if x)
        if note:
            lines.append(f"NOTE:{_vesc(note)}")
        lines.append("END:VCARD")
        cards.append("\r\n".join(lines))
    return "\r\n".join(cards) + ("\r\n" if cards else "")
