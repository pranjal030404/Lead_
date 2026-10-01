"""White-label branding.

Every customer-facing surface - product name, tagline, support address, accent
colours, logo - resolves through this module instead of being written into the
source. An install is rebranded by editing Settings (stored in `app_settings`)
or by setting BRAND_* variables in .env; the DB wins so the admin UI always
takes effect without a redeploy.

The three HTML pages ship as templates with `{{placeholders}}` and are rendered
on request. There is intentionally no build step and no template engine: the
placeholder syntax is plain `str.replace`, and `render_page` is strict about
unknown keys so a typo in a template fails loudly instead of shipping raw
`{{double_braces}}` to a customer.

Uploaded logos live in `data/uploads/` (inside the volume the backup job and
systemd ReadWritePaths already cover) and are served from the same process -
nothing about a rebrand should depend on a CDN or an image host.
"""

from __future__ import annotations

import html
import re
import uuid
from pathlib import Path

from .config import settings

# Keys that can be edited in the admin UI, with their env fallbacks and
# defaults. `branding_keys` (the DB keys) doubles as the allow-list for the
# settings API - never build an update query from raw user input.
BRAND_DEFAULTS: dict[str, str] = {
    "brand_name": settings.brand_name,
    "brand_short_name": settings.brand_short_name,
    "brand_tagline": settings.brand_tagline,
    "brand_landing_title": settings.brand_landing_title,
    "brand_landing_subtitle": settings.brand_landing_subtitle,
    "brand_footer": settings.brand_footer,
    "brand_support_email": settings.brand_support_email,
    "brand_company_name": settings.brand_company_name,
    "brand_accent": settings.brand_accent,
    "brand_accent_2": settings.brand_accent_2,
    "brand_background": settings.brand_background,
}

UPLOADS_DIRNAME = "uploads"


def uploads_dir() -> Path:
    """Where uploaded logos live, resolved per call.

    Deliberately not a module constant: `settings.data_dir` is reassigned by the
    test harness (and could be moved by an operator), and a constant would keep
    pointing at the value captured at import time.
    """
    path = settings.data_dir / UPLOADS_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

LOGO_KEY = "brand_logo"
FAVICON_KEY = "brand_favicon"
_UPLOAD_KEYS = {LOGO_KEY, FAVICON_KEY}


# ------------------------------------------------------------- resolution --


def get_branding() -> dict[str, str]:
    """Resolve every brand field: app_settings value, else env/DB default.

    One query, then fill from defaults - settings are read on every page render,
    so it must stay a single round-trip. DB and file values are validated: a
    hand-edited row with a bogus colour or a deleted logo file falls back to the
    default rather than breaking every page.
    """
    from .db import query

    rows = query("SELECT setting_key, value FROM app_settings WHERE setting_key IN "
                 "('brand_name','brand_short_name','brand_tagline','brand_landing_title',"
                 "'brand_landing_subtitle','brand_footer','brand_support_email',"
                 "'brand_company_name','brand_accent','brand_accent_2','brand_background',"
                 "'brand_logo','brand_favicon')")
    stored = {row["setting_key"]: (row["value"] or "") for row in rows}

    brand: dict[str, str] = {}
    for key, default in BRAND_DEFAULTS.items():
        value = (stored.get(key) or "").strip()
        brand[key] = value or default

    for key in ("brand_accent", "brand_accent_2", "brand_background"):
        if not HEX_RE.match(brand[key]):
            brand[key] = BRAND_DEFAULTS[key]

    for key in _UPLOAD_KEYS:
        if stored.get(key) and (uploads_dir() / stored[key]).is_file():
            brand[key] = stored[key]
        else:
            brand[key] = ""

    # The app name split used across the UI: wordmark = "Name <span>suffix</span>".
    name = brand["brand_name"].strip()
    short = brand["brand_short_name"].strip()
    if not short:
        parts = name.split()
        short = parts[-1] if len(parts) > 1 else name
        brand["brand_short_name"] = short
    # Strip the suffix only when it literally ends the name; a custom short
    # that isn't a substring ("Suite" -> "NA") must not slice mid-word.
    if name.endswith(short) and name != short:
        brand["brand_name_main"] = name[: -len(short)].strip()
    else:
        brand["brand_name_main"] = name
    return brand


def set_branding(values: dict[str, str]) -> None:
    """Upsert editable brand keys. Unknown keys raise; blank clears to default."""
    from .db import execute, utcnow

    for key, value in values.items():
        if key not in BRAND_DEFAULTS:
            raise ValueError(f"Unknown branding key: {key}")
        execute(
            "INSERT INTO app_settings(setting_key, value, updated_at) VALUES(?,?,?) "
            "ON DUPLICATE KEY UPDATE value = VALUES(value), updated_at = VALUES(updated_at)",
            (key, (value or "").strip(), utcnow()),
        )


def reset_branding() -> dict[str, str]:
    """Clear DB overrides AND uploaded files, restoring the .env / built-in look."""
    from .db import execute

    for key in (*BRAND_DEFAULTS, *_UPLOAD_KEYS):
        execute("DELETE FROM app_settings WHERE setting_key = ?", (key,))
    for key in _UPLOAD_KEYS:
        stored = _peek_setting(key)
        if stored:
            (uploads_dir() / stored).unlink(missing_ok=True)
    return get_branding()


def _peek_setting(key: str) -> str:
    from .db import query_one

    row = query_one("SELECT value FROM app_settings WHERE setting_key = ?", (key,))
    return (row["value"] if row else "") or ""


# ----------------------------------------------------------------- upload ---

ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/svg+xml"}
MAX_LOGO_BYTES = 1 * 1024 * 1024  # 1 MB is generous for a logo


def save_logo(data: bytes, content_type: str, favicon: bool = False) -> str:
    """Store an uploaded image and point the brand key at it. Returns filename."""
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise ValueError(f"Unsupported image type: {content_type or 'unknown'}")
    if not data:
        raise ValueError("Empty upload")
    if len(data) > MAX_LOGO_BYTES:
        raise ValueError("Image must be 1 MB or smaller")

    ext = {"image/png": ".png", "image/jpeg": ".jpg",
           "image/webp": ".webp", "image/svg+xml": ".svg"}[content_type]
    name = f"{'favicon' if favicon else 'logo'}-{uuid.uuid4().hex[:12]}{ext}"
    (uploads_dir() / name).write_bytes(data)

    from .db import set_setting

    set_setting(FAVICON_KEY if favicon else LOGO_KEY, name)

    # Best effort: drop the previous file so uploads don't accumulate. Reading
    # before the upsert would be a second query for cosmetic gain.
    stale = [
        value for key in _UPLOAD_KEYS
        if (value := _peek_setting(key)) and value != name
        and (uploads_dir() / value).is_file() and (key == LOGO_KEY) == (not favicon)
    ]
    for old in stale:
        (uploads_dir() / old).unlink(missing_ok=True)
    return name


# ------------------------------------------------------------------ theme ---


def _rgba(hex_color: str, alpha: float) -> str:
    value = hex_color.lstrip("#")
    r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


def theme_css(brand: dict[str, str]) -> str:
    """Override the design tokens the stylesheets are already written against.

    styles.css/landing.css keep their default palette; this file is loaded after
    them and re-points the custom properties, so a rebrand touches one small
    cached response instead of rewriting two stylesheets. Every accent-tinted
    literal in those files reads `rgb(var(--accent-rgb) / a)`, so setting the
    rgb triple here re-tints glows, borders and highlights automatically.
    """
    accent = brand["brand_accent"]
    accent2 = brand["brand_accent_2"]
    bg = brand["brand_background"]
    # Keep the dim-text ramp readable on a light background: a light bg with the
    # dark-theme text colours would be invisible, so flip the ramp instead.
    if _luma(bg) > 0.55:
        return theme_css_light(brand)
    return f""":root {{
  --bg: {bg};
  --bg-raise: {_mix(bg, '#ffffff', 0.055)};
  --bg-hover: {_mix(bg, '#ffffff', 0.10)};
  --bg-inset: {_mix(bg, '#000000', 0.16)};
  --line: {_mix(bg, '#ffffff', 0.135)};
  --line-soft: {_mix(bg, '#ffffff', 0.085)};
  --accent: {accent};
  --accent-2: {accent2};
  --accent-rgb: {_rgb_triple(accent)};
  --accent-2-rgb: {_rgb_triple(accent2)};
  --on-accent: {_readable_on(accent)};
  --accent-dim: {_rgba(accent, 0.14)};
  --grad: linear-gradient(135deg, {accent} 0%, {accent2} 100%);
  --glow: 0 0 0 1px {_rgba(accent, 0.35)}, 0 4px 24px {_rgba(accent, 0.22)};
}}
body::before {{
  background:
    radial-gradient(700px 420px at 85% -10%, {_rgba(accent, 0.09)}, transparent 60%),
    radial-gradient(640px 420px at -10% 110%, {_rgba(accent2, 0.07)}, transparent 60%);
}}
::selection {{ background: {_rgba(accent, 0.35)}; color: {_readable_on(accent)}; }}
"""


def theme_css_light(brand: dict[str, str]) -> str:
    """Light-background variant: flips the text ramp so the UI stays readable."""
    accent = brand["brand_accent"]
    accent2 = brand["brand_accent_2"]
    bg = brand["brand_background"]
    return f""":root {{
  --bg: {bg};
  --bg-raise: {_mix(bg, '#ffffff', 0.5)};
  --bg-hover: {_mix(bg, '#000000', 0.05)};
  --bg-inset: {_mix(bg, '#000000', 0.03)};
  --line: {_mix(bg, '#000000', 0.14)};
  --line-soft: {_mix(bg, '#000000', 0.09)};
  --text: {_mix(bg, '#000000', 0.87)};
  --text-dim: {_mix(bg, '#000000', 0.58)};
  --text-faint: {_mix(bg, '#000000', 0.4)};
  --accent: {accent};
  --accent-2: {accent2};
  --accent-rgb: {_rgb_triple(accent)};
  --accent-2-rgb: {_rgb_triple(accent2)};
  --on-accent: {_readable_on(accent)};
  --accent-dim: {_rgba(accent, 0.14)};
  --grad: linear-gradient(135deg, {accent} 0%, {accent2} 100%);
  --glow: 0 0 0 1px {_rgba(accent, 0.35)}, 0 4px 24px {_rgba(accent, 0.22)};
  color-scheme: light;
}}
body::before {{
  background:
    radial-gradient(700px 420px at 85% -10%, {_rgba(accent, 0.09)}, transparent 60%),
    radial-gradient(640px 420px at -10% 110%, {_rgba(accent2, 0.07)}, transparent 60%);
}}
::selection {{ background: {_rgba(accent, 0.35)}; color: {_readable_on(accent)}; }}
"""


def _rgb_triple(hex_color: str) -> str:
    """'#rrggbb' -> 'r g b' for `rgb(var(--accent-rgb) / alpha)` usage."""
    value = hex_color.lstrip("#")
    return f"{int(value[0:2], 16)} {int(value[2:4], 16)} {int(value[4:6], 16)}"


def _luma(hex_color: str) -> float:
    """Perceived brightness 0-1. Decides dark vs light token set."""
    value = hex_color.lstrip("#")
    r, g, b = (int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _readable_on(hex_color: str) -> str:
    """Text colour that stays legible on top of the accent (chips, selection)."""
    return "#1a1206" if _luma(hex_color) > 0.4 else "#ffffff"


def _mix(base: str, toward: str, amount: float) -> str:
    """Blend base toward another colour by amount (0-1). Derives the raised /
    line / text ramp from one background colour instead of hardcoding hexes."""
    b = base.lstrip("#")
    t = toward.lstrip("#")
    out = []
    for i in (0, 2, 4):
        cb = int(b[i:i + 2], 16)
        ct = int(t[i:i + 2], 16)
        out.append(round(cb + (ct - cb) * amount))
    return "#" + "".join(f"{v:02x}" for v in out)


# ------------------------------------------------------------------ pages ---


def logo_html(brand: dict[str, str], size: int = 17) -> str:
    """The logo box: uploaded image if set, else the built-in bolt mark."""
    if brand.get("brand_logo"):
        return (f'<span class="logo"><img src="/api/uploads/{html.escape(brand["brand_logo"])}" '
                f'alt="" width="{size}" height="{size}"></span>')
    return (f'<span class="logo"><svg width="{size}" height="{size}" viewBox="0 0 24 24" '
            'fill="none"><path d="M13 2 4.5 13.5H11L9.5 22 19 10h-6.5L13 2Z" '
            f'fill="{_readable_on(brand["brand_accent"])}"/></svg></span>')


def wordmark_html(brand: dict[str, str]) -> str:
    """Brand name with the suffix dimmed, matching the original lockups.

    Only splits when the short name literally ends the full name - a custom
    suffix like "NA" for "Northwind Analytics Suite" would otherwise render the
    tail twice. Single-word brands render unsplit for the same reason.
    """
    name = brand["brand_name"]
    short = brand["brand_short_name"]
    main = brand["brand_name_main"]
    if short and name != short and name.endswith(short):
        return f'{html.escape(main)} <span>{html.escape(short)}</span>'
    return html.escape(name)


_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def render_page(template_name: str, brand: dict[str, str] | None = None) -> str:
    """Render one of the three HTML pages with the current brand.

    Strict on purpose: an unrendered {{placeholder}} means someone added a key
    to a template without teaching get_branding() about it, and shipping that to
    a customer is worse than a 500 in development.
    """
    if brand is None:
        brand = get_branding()

    wordmark = wordmark_html(brand)
    footer_text = brand["brand_footer"] or f"© {brand['brand_company_name'] or brand['brand_name']}"
    favicon = (f'/api/uploads/{brand["brand_favicon"]}' if brand.get("brand_favicon")
               else "/static/favicon.svg")

    values = {
        "BRAND_NAME": html.escape(brand["brand_name"]),
        "BRAND_NAME_PLAIN": html.escape(brand["brand_name"]),
        "BRAND_NAME_MAIN": html.escape(brand["brand_name_main"]),
        "BRAND_SHORT_NAME": html.escape(brand["brand_short_name"]),
        "BRAND_TAGLINE": html.escape(brand["brand_tagline"]),
        "BRAND_LANDING_TITLE": html.escape(
            brand["brand_landing_title"] or "Find businesses that need a website"),
        "BRAND_LANDING_SUBTITLE": html.escape(brand["brand_landing_subtitle"] or (
            f'{brand["brand_name"]} finds local businesses that need a website, enriches '
            "them with phones, emails and scoring, and runs outreach you approve — all "
            "from your own server, for the price of a coffee a month.")),
        "BRAND_META_DESCRIPTION": html.escape(
            f'{brand["brand_name"]} - {brand["brand_tagline"]}'),
        "BRAND_FOOTER": html.escape(footer_text),
        "BRAND_SUPPORT_EMAIL": html.escape(brand["brand_support_email"]),
        # A mailto link to a blank address is broken; render plain text instead.
        "BRAND_SUPPORT_HTML": (
            f'<a href="mailto:{html.escape(brand["brand_support_email"])}">Contact us</a>'
            if brand["brand_support_email"] else "Ask your administrator"),
        "WORDMARK_HTML": wordmark,
        "LOGO_HTML": logo_html(brand, 19),
        "LOGO_HTML_17": logo_html(brand, 17),
        "LOGO_HTML_18": logo_html(brand, 18),
        "FAVICON_URL": html.escape(favicon),
        # Decorative slug for the browser-chrome mock on /landing.
        "BRAND_SHORT_NAMELower": re.sub(r"[^a-z0-9]", "", brand["brand_short_name"].lower()) or "app",
        "ACCENT_ON_COLOR": html.escape(_readable_on(brand["brand_accent"])),
        "BRAND_COMPANY_NAME": html.escape(brand["brand_company_name"] or brand["brand_name"]),
    }

    text = (settings.static_dir / template_name).read_text(encoding="utf-8")

    def sub(match: re.Match) -> str:
        key = match.group(1)
        if key not in values:
            raise ValueError(f"Unknown placeholder {{{{{key}}}}} in {template_name}")
        return values[key]

    rendered = _PLACEHOLDER_RE.sub(sub, text)
    leftover = _PLACEHOLDER_RE.search(rendered)
    if leftover:
        raise ValueError(f"Unrendered placeholder {leftover.group(0)} in {template_name}")
    return rendered
