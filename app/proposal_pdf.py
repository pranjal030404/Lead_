"""Lay a proposal out as a PDF (A4, items table, totals, notes, page numbers)."""

from __future__ import annotations

from .pdfsimple import A4, Pdf, wrap

LEFT, RIGHT, TOP, BOTTOM = 50.0, A4[0] - 50.0, A4[1] - 60.0, 70.0
COL_QTY, COL_PRICE, COL_AMT = 360.0, 450.0, RIGHT
DESC_W = 290.0


def _money(n: float) -> str:
    return f"{n:,.2f}"


def render(p: dict, brand: str) -> bytes:
    """`p` is proposals.hydrate() output (with business_name)."""
    pdf = Pdf()
    y = TOP

    def ensure(space: float) -> None:
        nonlocal y
        if y - space < BOTTOM:
            pdf.new_page()
            y = TOP

    pdf.text(LEFT, y, brand, 10, gray=0.45); y -= 26
    for line in wrap(p["title"], RIGHT - LEFT, 20, bold=True):
        pdf.text(LEFT, y, line, 20, bold=True); y -= 24
    y -= 2
    pdf.text(LEFT, y, f"{p['number']}  |  Prepared for {p.get('business_name') or ''}", 10, gray=0.35); y -= 14
    if p.get("valid_until"):
        pdf.text(LEFT, y, f"Valid until {p['valid_until'][:10]}", 10, gray=0.35); y -= 14
    status = p.get("effective_status")
    if status in ("ACCEPTED", "DECLINED", "EXPIRED"):
        pdf.text(LEFT, y, status.title(), 10, bold=True); y -= 14
    y -= 14

    def header() -> None:
        nonlocal y
        pdf.text(LEFT, y, "Item", 9, bold=True, gray=0.3)
        pdf.text_right(COL_QTY, y, "Qty", 9, bold=True, gray=0.3)
        pdf.text_right(COL_PRICE, y, "Price", 9, bold=True, gray=0.3)
        pdf.text_right(COL_AMT, y, f"Amount ({p['currency']})", 9, bold=True, gray=0.3)
        y -= 6; pdf.rule(LEFT, RIGHT, y, 0.8, 0.3); y -= 14

    header()
    for it in p["items"]:
        lines = wrap(it["description"], DESC_W, 10)
        ensure(14 * len(lines) + 8)
        if y == TOP:
            header()
        top = y
        for line in lines:
            pdf.text(LEFT, y, line, 10); y -= 13
        pdf.text_right(COL_QTY, top, f"{it['qty']:g}", 10)
        pdf.text_right(COL_PRICE, top, _money(it["unit_price"]), 10)
        pdf.text_right(COL_AMT, top, _money(it["line_total"]), 10)
        y -= 3; pdf.rule(LEFT, RIGHT, y, 0.4, 0.85); y -= 12

    ensure(90)
    y -= 4
    rows = [("Subtotal", _money(p["subtotal"]))]
    if p["discount"]:
        rows.append((f"Discount ({p['discount_pct']:g}%)", "-" + _money(p["discount"])))
    if p["tax"]:
        rows.append((f"Tax ({p['tax_pct']:g}%)", _money(p["tax"])))
    for label, value in rows:
        pdf.text_right(COL_PRICE, y, label, 10, gray=0.35); pdf.text_right(COL_AMT, y, value, 10); y -= 15
    pdf.rule(COL_PRICE - 70, RIGHT, y + 9, 0.8, 0.3)
    pdf.text_right(COL_PRICE, y - 4, f"Total ({p['currency']})", 12, bold=True)
    pdf.text_right(COL_AMT, y - 4, _money(p["total"]), 12, bold=True); y -= 38

    if p.get("notes"):
        ensure(40)
        pdf.text(LEFT, y, "Notes", 11, bold=True); y -= 16
        for line in wrap(p["notes"], RIGHT - LEFT, 10):
            ensure(14)
            pdf.text(LEFT, y, line, 10); y -= 13

    pdf.footer_all(lambda i, n: f"{p['number']}  -  Page {i} of {n}")
    return pdf.render()
