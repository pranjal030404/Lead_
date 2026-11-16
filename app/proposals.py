"""Proposals / quotes: line items, exact money maths, a shareable client page, and deal tracking.

Lifecycle:  DRAFT -> SENT -> ACCEPTED | DECLINED (EXPIRED is computed from valid_until, never stored).
  * Sending moves the lead to PROPOSAL_SENT (forward only) and can queue an approval-gated email
    containing the client link. Nothing is emailed without approval, as everywhere else.
  * The client opens /p/<token> (unguessable, no login), reads it, and can Accept or Decline.
  * Accepting marks the lead WON, stops any sequence, and the amount counts as won revenue.
Money uses Decimal and is rounded half-up to 2 places so totals never drift by a paisa/cent.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from . import pipeline, webhooks
from .db import current_ws, execute, query, query_one, utcnow

CENT = Decimal("0.01")
DEFAULT_VALID_DAYS = 14


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def compute(items: list[dict], discount_pct: float, tax_pct: float) -> dict:
    """-> {items (with line totals), subtotal, discount, taxable, tax, total} as floats rounded to 2dp."""
    lines, subtotal = [], Decimal(0)
    for it in items:
        line = money(Decimal(str(it["qty"])) * Decimal(str(it["unit_price"])))
        lines.append({"description": it["description"], "qty": it["qty"], "unit_price": float(money(it["unit_price"])),
                      "line_total": float(line)})
        subtotal += line
    discount = money(subtotal * Decimal(str(discount_pct)) / 100)
    taxable = subtotal - discount
    tax = money(taxable * Decimal(str(tax_pct)) / 100)
    return {"items": lines, "subtotal": float(subtotal), "discount": float(discount),
            "taxable": float(taxable), "tax": float(tax), "total": float(taxable + tax)}


def next_number() -> str:
    year = datetime.now(timezone.utc).year
    prefix = f"P-{year}-"
    row = query_one("SELECT number FROM proposals WHERE workspace_id = ? AND number LIKE ? ORDER BY id DESC LIMIT 1",
                    (current_ws(), prefix + "%"))
    seq = int(row["number"].rsplit("-", 1)[1]) + 1 if row else 1
    return f"{prefix}{seq:04d}"


def new_token() -> str:
    return secrets.token_urlsafe(24)


def effective_status(row: dict) -> str:
    """SENT proposals past valid_until read as EXPIRED."""
    if row["status"] == "SENT" and row.get("valid_until"):
        try:
            until = datetime.fromisoformat(row["valid_until"])
            until = until.replace(tzinfo=timezone.utc) if until.tzinfo is None else until
            if until < datetime.now(timezone.utc):
                return "EXPIRED"
        except ValueError:
            pass
    return row["status"]


def hydrate(row: dict) -> dict:
    items = json.loads(row["items"] or "[]")
    calc = compute(items, row["discount_pct"], row["tax_pct"])
    return {**{k: row[k] for k in ("id", "lead_id", "number", "title", "status", "currency", "discount_pct",
                                    "tax_pct", "valid_until", "notes", "share_token", "created_at",
                                    "updated_at", "sent_at", "decided_at")},
            "effective_status": effective_status(row), **calc, "business_name": row.get("business_name")}


def mark_sent(proposal: dict) -> None:
    until = proposal["valid_until"] or (datetime.now(timezone.utc) + timedelta(days=DEFAULT_VALID_DAYS)).isoformat(timespec="seconds")
    execute("UPDATE proposals SET status = 'SENT', sent_at = COALESCE(sent_at, ?), valid_until = ?, updated_at = ? "
            "WHERE id = ? AND workspace_id = ?", (utcnow(), until, utcnow(), proposal["id"], current_ws()))
    pipeline.set_lead_status(proposal["lead_id"], "PROPOSAL_SENT", f"Proposal {proposal['number']} sent", forward_only=True)
    webhooks.emit("proposal.sent", {"proposal_id": proposal["id"], "number": proposal["number"],
                                    "lead_id": proposal["lead_id"], "total": proposal["total"]})


def decide(proposal_id: int, accepted: bool, by: str) -> bool:
    """Accept/decline a SENT, unexpired proposal. Returns False if it can't be decided."""
    row = query_one("SELECT * FROM proposals WHERE id = ? AND workspace_id = ?", (proposal_id, current_ws()))
    if not row or effective_status(row) != "SENT":
        return False
    status = "ACCEPTED" if accepted else "DECLINED"
    n = execute("UPDATE proposals SET status = ?, decided_at = ?, updated_at = ? WHERE id = ? AND workspace_id = ? "
                "AND status = 'SENT'", (status, utcnow(), utcnow(), proposal_id, current_ws()))
    if not n:
        return False
    h = hydrate(row)
    if accepted:
        pipeline.set_lead_status(row["lead_id"], "WON", f"Proposal {row['number']} accepted ({by})")
    else:
        pipeline.set_lead_status(row["lead_id"], "NEGOTIATION", f"Proposal {row['number']} declined ({by})", forward_only=True)
        execute("INSERT INTO tasks(workspace_id, lead_id, owner_user_id, title, due_at, status, created_at) "
                "VALUES(?,?,?,?,?, 'OPEN', ?)",
                (current_ws(), row["lead_id"], row["owner_user_id"], f"Follow up: proposal {row['number']} was declined",
                 (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(timespec="seconds"), utcnow()))
    webhooks.emit("proposal.accepted" if accepted else "proposal.declined",
                  {"proposal_id": proposal_id, "number": row["number"], "lead_id": row["lead_id"], "total": h["total"]})
    return True


def revenue(owner_user_id: int | None = None) -> dict:
    """Won revenue (by month and overall), open pipeline value and win rate."""
    sql = "SELECT * FROM proposals WHERE workspace_id = ?"
    args: list = [current_ws()]
    if owner_user_id is not None:
        sql += " AND owner_user_id = ?"; args.append(owner_user_id)
    rows = query(sql, tuple(args))
    won, open_value, by_month = Decimal(0), Decimal(0), {}
    accepted = declined = 0
    for r in rows:
        h = hydrate(r)
        if r["status"] == "ACCEPTED":
            accepted += 1
            won += money(h["total"])
            month = (r["decided_at"] or r["updated_at"])[:7]
            by_month[month] = by_month.get(month, Decimal(0)) + money(h["total"])
        elif r["status"] == "DECLINED":
            declined += 1
        elif h["effective_status"] == "SENT":
            open_value += money(h["total"])
    decided = accepted + declined
    return {"won_total": float(won), "open_value": float(open_value), "accepted": accepted, "declined": declined,
            "win_rate": round(accepted / decided * 100, 1) if decided else None,
            "by_month": [{"month": m, "won": float(v)} for m, v in sorted(by_month.items())]}
