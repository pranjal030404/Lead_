"""A tiny dependency-free PDF writer: text, rules, Helvetica / Helvetica-Bold, A4, multi-page.

Just enough for documents like proposals. Text is encoded as cp1252 (unsupported characters
become '?'); string widths use the real Helvetica metrics so wrapping and right-alignment are
accurate. Output is a valid PDF 1.4 with a correct cross-reference table.
"""

from __future__ import annotations

# Helvetica advance widths (1/1000 em) for ASCII 32..126; Helvetica-Bold differs slightly, so bold
# text is measured with a 5% safety margin rather than a second table.
_W = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
      556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
      1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
      667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
      333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
      556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]

A4 = (595.28, 841.89)


def _clean(text: str) -> str:
    return (text or "").replace("₹", "Rs.").replace("–", "-").replace("—", "-") \
        .replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')


def width(text: str, size: float, bold: bool = False) -> float:
    total = sum(_W[ord(c) - 32] if 32 <= ord(c) <= 126 else 556 for c in _clean(text))
    return total * size / 1000 * (1.05 if bold else 1.0)


def wrap(text: str, max_width: float, size: float, bold: bool = False) -> list[str]:
    """Greedy word wrap that also breaks words longer than a line."""
    lines: list[str] = []
    for paragraph in _clean(text).split("\n"):
        current = ""
        for word in paragraph.split(" "):
            candidate = f"{current} {word}".strip() if current else word
            if width(candidate, size, bold) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
            while width(word, size, bold) > max_width and len(word) > 1:      # unbreakable long token
                cut = len(word)
                while cut > 1 and width(word[:cut], size, bold) > max_width:
                    cut -= 1
                lines.append(word[:cut]); word = word[cut:]
            current = word
        lines.append(current)
    return lines or [""]


def _esc(text: str) -> bytes:
    raw = _clean(text).encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


class Pdf:
    def __init__(self):
        self.pages: list[list[bytes]] = [[]]

    def new_page(self) -> None:
        self.pages.append([])

    def _add(self, chunk: bytes) -> None:
        self.pages[-1].append(chunk)

    def text(self, x: float, y: float, text: str, size: float = 10, bold: bool = False, gray: float = 0.0) -> None:
        font = b"/F2" if bold else b"/F1"
        self._add(b"BT %s %.2f Tf %.2f g %.2f %.2f Td (" % (font, size, gray, x, y) + _esc(text) + b") Tj ET")

    def text_right(self, right: float, y: float, text: str, size: float = 10, bold: bool = False, gray: float = 0.0) -> None:
        self.text(right - width(text, size, bold), y, text, size, bold, gray)

    def rule(self, x1: float, x2: float, y: float, thickness: float = 0.5, gray: float = 0.75) -> None:
        self._add(b"%.2f G %.2f w %.2f %.2f m %.2f %.2f l S" % (gray, thickness, x1, y, x2, y))

    def footer_all(self, make_text) -> None:
        """Stamp a centred footer on every page; make_text(page_no, page_count) -> str."""
        count = len(self.pages)
        for i, chunk in enumerate(self.pages, 1):
            label = make_text(i, count)
            chunk.append(b"BT /F1 8.00 Tf 0.50 g %.2f 30.00 Td (" % ((A4[0] - width(label, 8)) / 2) + _esc(label) + b") Tj ET")

    def render(self) -> bytes:
        objs: list[bytes] = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
                             b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
        kids = []
        for chunk in self.pages:
            stream = b"\n".join(chunk)
            objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
            content_no = len(objs)
            objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Contents %d 0 R "
                        b"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>" % (A4[0], A4[1], content_no))
            kids.append(len(objs))
        objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
        objs[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % k for k in kids), len(kids))
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for number, body in enumerate(objs, 1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
        for off in offsets:
            out += b"%010d 00000 n \n" % off
        out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
        return bytes(out)
