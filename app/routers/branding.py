from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from .. import branding
from ..security import require_role

router = APIRouter(prefix="/api", tags=["branding"])

# Text keys an admin may set. Uploads go through their own endpoint - never
# accept a path-ish value from JSON.
_ALLOWED_KEYS = {
    "brand_name", "brand_short_name", "brand_tagline", "brand_landing_title",
    "brand_landing_subtitle", "brand_footer", "brand_support_email",
    "brand_company_name", "brand_accent", "brand_accent_2", "brand_background",
}


@router.get("/branding/theme.css")
def theme_css():
    """Public. Loaded by every page after the main stylesheets; overrides the
    design tokens with the configured brand colours."""
    css = branding.theme_css(branding.get_branding())
    return Response(
        content=css,
        media_type="text/css",
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/branding")
def get_branding(_: dict = Depends(require_role("admin", "superadmin"))):
    """Current brand config, plus which keys are overridden vs default."""
    brand = branding.get_branding()
    return {
        "brand": brand,
        "overridden": sorted(branding.BRAND_DEFAULTS.keys() & _overridden_keys()),
        "uploads_dir": str(branding.uploads_dir()),
    }


def _overridden_keys() -> set[str]:
    from ..db import query

    rows = query("SELECT setting_key FROM app_settings WHERE setting_key LIKE 'brand_%'")
    return {row["setting_key"] for row in rows}


@router.put("/branding")
def update_branding(payload: dict, _: dict = Depends(require_role("admin", "superadmin"))):
    values = payload.get("brand") if isinstance(payload.get("brand"), dict) else payload
    unknown = set(values) - _ALLOWED_KEYS
    if unknown:
        raise HTTPException(400, f"Unknown branding keys: {sorted(unknown)}")

    for key in ("brand_accent", "brand_accent_2", "brand_background"):
        if key in values and values[key] and not branding.HEX_RE.match(values[key].strip()):
            raise HTTPException(400, f"{key} must be a hex colour like #22cc88")

    clean = {k: str(v) for k, v in values.items() if k in _ALLOWED_KEYS}
    if clean:
        branding.set_branding(clean)
    return {"ok": True, "brand": branding.get_branding()}


@router.post("/branding/reset")
def reset_branding(_: dict = Depends(require_role("superadmin", "admin"))):
    return {"ok": True, "brand": branding.reset_branding()}


@router.post("/branding/logo")
def upload_logo(
    file: UploadFile = File(...),
    favicon: bool = False,
    _: dict = Depends(require_role("admin", "superadmin")),
):
    try:
        name = branding.save_logo(file.file.read(), file.content_type or "", favicon=favicon)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "file": name}


@router.get("/uploads/{filename}")
def uploaded_file(filename: str):
    """Serve an uploaded brand image. Public: logos appear on /login and
    /landing, which have no session. The filename is validated against the
    stored settings key, so only files the admin actually uploaded resolve."""
    safe = branding.uploads_dir() / filename
    stored = {
        branding._peek_setting(branding.LOGO_KEY),
        branding._peek_setting(branding.FAVICON_KEY),
    }
    if filename not in stored or not safe.is_file():
        raise HTTPException(404, "Not found")
    media = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".svg": "image/svg+xml",
    }.get(safe.suffix.lower(), "application/octet-stream")
    return FileResponse(safe, media_type=media,
                        headers={"Cache-Control": "public, max-age=86400"})
