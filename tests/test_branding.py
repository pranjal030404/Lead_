"""White-label branding tests.

Covers: DB-over-env resolution, short-name derivation, colour validation and
fallback, theme CSS generation (dark + light background), page rendering
(placeholders resolved, no raw braces shipped), upload validation, the public
theme endpoint, and that a rebrand actually reaches the served pages.

Environment (PROVIDER, MYSQL_*) and the isolated MySQL test database are set up
in tests/conftest.py, which runs before this module is imported.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402
from app.branding import (BRAND_DEFAULTS, get_branding, render_page,  # noqa: E402
                          reset_branding, save_logo, set_branding, theme_css)
from app.db import init_db, query_one, set_setting  # noqa: E402

init_db()


# ------------------------------------------------------------ resolution --


def _cleanup():
    reset_branding()


def test_brand_defaults_resolve_from_env_without_db_rows():
    _cleanup()
    brand = get_branding()
    assert brand["brand_name"] == config.settings.brand_name
    assert brand["brand_accent"] == config.settings.brand_accent


def test_db_override_wins_over_env():
    _cleanup()
    set_branding({"brand_name": "Acme Radar"})
    brand = get_branding()
    assert brand["brand_name"] == "Acme Radar"
    assert brand["brand_short_name"] == "Radar", "suffix should default to the last word"
    assert brand["brand_name_main"] == "Acme"


def test_short_name_derivation_single_word():
    _cleanup()
    set_branding({"brand_name": "Plumbify"})
    brand = get_branding()
    assert brand["brand_short_name"] == "Plumbify"
    assert brand["brand_name_main"] == "Plumbify"


def test_explicit_short_name_is_respected():
    """A custom suffix that isn't a literal tail of the name must not slice the
    name mid-word: the wordmark renders unsplit and main keeps the full name."""
    _cleanup()
    set_branding({"brand_name": "Northwind Analytics Suite", "brand_short_name": "NA"})
    brand = get_branding()
    assert brand["brand_name_main"] == "Northwind Analytics Suite"
    assert brand["brand_short_name"] == "NA"
    # And the landing wordmark stays on one piece.
    assert "Northwind Analytics Suite" in render_page("landing.html")
    assert "<span>NA</span>" not in render_page("landing.html")


def test_invalid_colour_falls_back_to_default():
    _cleanup()
    set_setting("brand_accent", "javascript:alert(1)")
    brand = get_branding()
    assert brand["brand_accent"] == BRAND_DEFAULTS["brand_accent"]
    assert brand["brand_accent"].startswith("#")


def test_missing_logo_file_falls_back_cleanly():
    _cleanup()
    set_setting("brand_logo", "logo-gone-does-not-exist.png")
    brand = get_branding()
    assert brand["brand_logo"] == ""


# --------------------------------------------------------------- theme ----


def test_theme_css_overrides_tokens():
    _cleanup()
    set_branding({"brand_accent": "#22cc88", "brand_background": "#101018"})
    css = theme_css(get_branding())
    assert "--accent: #22cc88" in css
    assert "--bg: #101018" in css
    assert "--accent-rgb: 34 204 136" in css
    assert "--grad" in css and "--on-accent" in css


def test_light_background_flips_the_text_ramp():
    _cleanup()
    set_branding({"brand_background": "#f5f4f0"})
    css = theme_css(get_branding())
    assert "color-scheme: light" in css
    assert "--text: " in css and "--text-dim: " in css


def test_theme_tokens_match_stylesheet_usage():
    """styles.css references --accent-rgb / --on-accent; the theme must emit them
    or every rebrand would silently keep the gold accent in half the places."""
    _cleanup()
    css = theme_css(get_branding())
    for token in ("--accent-rgb", "--accent-2-rgb", "--on-accent", "--grad",
                  "--bg-raise", "--line", "--accent-dim"):
        assert token in css, token


# ------------------------------------------------------------- rendering --


def test_rendered_pages_carry_the_brand_and_no_placeholders():
    _cleanup()
    set_branding({"brand_name": "Beacon Leads"})
    for template in ("index.html", "login.html", "landing.html"):
        html_out = render_page(template)
        assert "{{" not in html_out, f"raw placeholder leaked in {template}"
        assert "Beacon Leads" in html_out, template


def test_wordmark_splits_name_and_suffix():
    _cleanup()
    set_branding({"brand_name": "Northstar Leads"})
    landing = render_page("landing.html")
    assert "Northstar <span>Leads</span>" in landing


def test_unknown_placeholder_raises():
    """A template key nobody wired up must fail loudly, not ship braces."""
    _cleanup()
    import pytest
    from app import branding as b

    original = (config.settings.static_dir / "index.html").read_text(encoding="utf-8")
    try:
        (config.settings.static_dir / "index.html").write_text("x {{NOT_A_KEY}} y")
        with pytest.raises(ValueError):
            render_page("index.html")
    finally:
        (config.settings.static_dir / "index.html").write_text(original)


def test_footer_defaults_to_company_line():
    _cleanup()
    set_branding({"brand_company_name": "Beacon Studio"})
    html_out = render_page("login.html")
    assert "© Beacon Studio" in html_out


# --------------------------------------------------------------- uploads --


def test_logo_upload_validates_type_and_size():
    _cleanup()
    import pytest

    with pytest.raises(ValueError):
        save_logo(b"not an image", "application/zip")
    with pytest.raises(ValueError):
        save_logo(b"", "image/png")
    with pytest.raises(ValueError):
        save_logo(b"x" * (2 * 1024 * 1024), "image/png")


def test_logo_upload_round_trip():
    _cleanup()
    png = (b"\x89PNG\r\n\x1a\n" + b"0" * 64)  # minimal header; served as a file
    name = save_logo(png, "image/png")
    assert name.startswith("logo-") and name.endswith(".png")
    brand = get_branding()
    assert brand["brand_logo"] == name
    assert (config.settings.data_dir / "uploads" / name).is_file()


def test_favicon_and_logo_are_tracked_separately():
    _cleanup()
    png = b"\x89PNG\r\n\x1a\n" + b"1" * 32
    logo = save_logo(png, "image/png")
    fav = save_logo(png, "image/png", favicon=True)
    brand = get_branding()
    assert brand["brand_logo"] == logo
    assert brand["brand_favicon"] == fav
    assert logo != fav


# ----------------------------------------------------------------- HTTP ---


def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def test_theme_endpoint_is_public_and_reflects_the_brand():
    _cleanup()
    set_branding({"brand_accent": "#3366ff"})
    with _client() as client:
        response = client.get("/api/branding/theme.css")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/css")
        assert "#3366ff" in response.text


def test_branding_api_requires_admin():
    with _client() as client:
        assert client.get("/api/branding").status_code == 401


def test_branding_api_returns_brand_and_overridden_keys_for_admin():
    """The authenticated GET must return 200, not 500.

    Regression: the handler's `overridden` lookup runs a parameterless
    `LIKE 'brand_%'`. While the query helpers defaulted `params` to `()`, the
    DB driver tried to interpolate that literal `%` and raised
    `TypeError: not enough arguments for format string`, which reached the
    client as a 500 and broke the whole Settings page. The 401 test above never
    caught it: the role dependency rejects first, so the query never runs.
    """
    _cleanup()
    set_branding({"brand_name": "Beacon Leads"})
    with _client() as client:
        client.post("/api/auth/login", json={"username": config.settings.admin_user,
                                             "password": config.settings.admin_password})
        response = client.get("/api/branding")
        assert response.status_code == 200
        body = response.json()
        assert body["brand"]["brand_name"] == "Beacon Leads"
        # Only keys actually stored in app_settings count as overridden.
        assert "brand_name" in body["overridden"]
        assert "brand_accent_2" not in body["overridden"]
        assert body["uploads_dir"].endswith("uploads")


def test_pages_serve_the_brand_without_a_session():
    _cleanup()
    set_branding({"brand_name": "Rivet CRM"})
    with _client() as client:
        assert "Rivet CRM" in client.get("/login").text
        assert "Rivet CRM" in client.get("/landing").text
        assert "Rivet CRM" in client.get("/").text


def test_full_rebrand_flow_via_api():
    _cleanup()
    with _client() as client:
        client.post("/api/auth/login", json={"username": config.settings.admin_user,
                                             "password": config.settings.admin_password})
        response = client.put("/api/branding", json={
            "brand_name": "Hawkline", "brand_accent": "#00b8d4",
        })
        assert response.status_code == 200
        assert response.json()["brand"]["brand_name"] == "Hawkline"

        # Unknown keys are rejected rather than silently ignored.
        assert client.put("/api/branding", json={"nope": "x"}).status_code == 400
        assert client.put("/api/branding",
                          json={"brand_accent": "red"}).status_code == 400

    assert "Hawkline" in render_page("index.html")
    assert "#00b8d4" in theme_css(get_branding())


def test_reset_restores_env_defaults():
    _cleanup()
    set_branding({"brand_name": "Temporary Name"})
    brand = reset_branding()
    assert brand["brand_name"] == config.settings.brand_name


def test_favicon_route_serves_svg_default():
    with _client() as client:
        response = client.get("/favicon.ico")
        assert response.status_code == 200
        assert "svg" in response.headers.get("content-type", "")


def test_startup_title_uses_brand_setting():
    """The OpenAPI title is read from settings at import; env-level rebranding
    must flow through so API docs match the product name too."""
    from app.main import app

    assert app.title == config.settings.brand_name
