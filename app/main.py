"""Application entry point. Fully white-label: see app/branding.py."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import (FileResponse, JSONResponse, RedirectResponse,
                               Response)
from fastapi.staticfiles import StaticFiles

from . import branding
from .automation import start_scheduler, stop_scheduler
from .config import settings
from .db import (MIGRATIONS, NoWorkspace, _ws_var, init_db, query, query_one)
from .routers import (audits, auth, automation, intelligence, branding as branding_router, leads, outreach,
                      plans, profile, search, stats, users, workspaces)
from .tenancy import TenancyError, resolve_workspace
from .security import (SESSION_COOKIE, client_ip, load_user, rate_limit, verify_token)

PUBLIC_PATHS = {"/landing", "/login", "/admin", "/health", "/health/live", "/health/ready", "/api/auth/login",
                "/api/auth/admin/login", "/favicon.ico"}
PUBLIC_PREFIXES = ("/static/", "/api/uploads/", "/api/branding/theme.css")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_runtime_secrets()
    init_db()
    start_scheduler()

    brand = branding.get_branding()
    banner = [
        "",
        f"  {brand['brand_name']} is running",
        f"  ->  http://{settings.host}:{settings.port}",
        f"  provider: {settings.provider}   dry-run sending: {settings.dry_run}",
    ]
    if settings.generated_password:
        banner += [
            "",
            "  No ADMIN_PASSWORD was set, so one was generated for this run:",
            f"      user: {settings.admin_user}",
            f"      pass: {settings.generated_password}",
            "  Put it in .env to keep it across restarts.",
        ]
    if settings.generated_secret:
        banner += [
            "",
            "  WARNING: no SECRET_KEY set, so one was generated for this run.",
            "  You will be signed out every time the server restarts. Fix with:",
            '      python -c "import secrets;print(secrets.token_hex(32))"',
            "  and put the result in .env as SECRET_KEY=...",
        ]
    print("\n".join(banner) + "\n", flush=True)

    yield
    stop_scheduler()


app = FastAPI(
    title=settings.brand_name,
    description="Find businesses that need websites, enrich them, and work the pipeline.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def auth_and_rate_limit(request: Request, call_next):
    path = request.url.path

    if path.startswith("/api/"):
        client = client_ip(request)
        if not rate_limit(f"api:{client}", limit=settings.api_rate_limit,
                          window_seconds=settings.api_rate_window):
            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)

    if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
        response = await call_next(request)
        if path.startswith(("/static/", "/api/uploads/", "/api/branding/theme.css")):
            # Revalidate on every load. There's no build step and no hashed
            # filenames here, so without this the browser keeps running the
            # app.js it cached before a deploy until someone thinks to hard-
            # refresh. ETag turns the revalidation into a 304, so correctness
            # here costs one tiny conditional request, not a re-download.
            response.headers["Cache-Control"] = "no-cache"
        return response

    user = verify_token(request.cookies.get(SESSION_COOKIE))
    if user:
        # Re-read the row so a deactivated or deleted account is locked out on
        # the very next request, not whenever the token happened to expire.
        user = load_user(user)
    if not user:
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        # The marketing landing page is the public front door; every other
        # page funnels to the sign-in form.
        return RedirectResponse("/landing" if path == "/" else "/login", status_code=302)

    # Tenant scope. A user with no active workspace gets no scope at all, so any
    # tenant-scoped query fails with NoWorkspace (-> 403) instead of seeing data.
    try:
        membership = resolve_workspace(user["id"], request.headers.get("x-workspace-id"))
    except TenancyError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=403)
    user["workspace_id"] = membership["workspace_id"] if membership else None
    user["workspace_role"] = membership["member_role"] if membership else None
    request.state.user = user
    token = _ws_var.set(user["workspace_id"])
    try:
        response = await call_next(request)
    finally:
        _ws_var.reset(token)
    # The rendered HTML bakes in operator config - the Google Maps JS key lands
    # in a meta tag - and branding.render_page reads it per request. Without a
    # cache directive the browser heuristically caches the page, so a .env edit
    # plus restart still shows the old (empty) key until a hard refresh. Same
    # reasoning as the /static/ branch above, applied to the page itself.
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache"
    return response


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(self), camera=(), microphone=()",
}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Outermost middleware (registered last) so every response - including the
    401/429 early returns from the auth middleware - carries the headers.
    No CSP yet: the pages use inline scripts/styles and Google Maps, so a
    meaningful CSP needs those refactored first (tracked in KNOWN_LIMITATIONS)."""
    response = await call_next(request)
    for key, value in SECURITY_HEADERS.items():
        response.headers.setdefault(key, value)
    if settings.cookie_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


@app.exception_handler(NoWorkspace)
async def no_workspace_handler(request: Request, exc: NoWorkspace):
    return JSONResponse({"detail": "No workspace access - ask an administrator to add "
                                   "you to a workspace"}, status_code=403)


app.include_router(auth.router)
app.include_router(leads.router)
app.include_router(search.router)
app.include_router(stats.router)
app.include_router(outreach.router)
app.include_router(automation.router)
app.include_router(users.router)
app.include_router(plans.router)
app.include_router(branding_router.router)
app.include_router(workspaces.router)
app.include_router(profile.router)
app.include_router(audits.router)
app.include_router(intelligence.router)

app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")


@app.get("/health/live")
def health_live():
    """Process is up. Touches nothing external, so it never flaps on a DB blip."""
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready():
    """Ready to serve: DB reachable and all migrations applied."""
    checks: dict[str, str] = {}
    try:
        query_one("SELECT 1 AS ok")
        checks["mysql"] = "ok"
        applied = {r["version"] for r in query("SELECT version FROM schema_migrations")}
        pending = [v for v, _, _ in MIGRATIONS if v not in applied]
        checks["migrations"] = "ok" if not pending else f"pending: {pending}"
    except Exception:  # noqa: BLE001 - detail stays in logs, not the public response
        checks.setdefault("mysql", "error")
        checks.setdefault("migrations", "unknown")
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse({"status": "ok" if ok else "error", "checks": checks},
                        status_code=200 if ok else 503)


@app.get("/health")
def health():
    """Unauthenticated liveness probe - safe to point a uptime monitor at."""
    try:
        query_one("SELECT 1 AS ok")
        return {"status": "ok"}
    except Exception:  # noqa: BLE001 - don't leak driver errors publicly
        return JSONResponse({"status": "error"}, status_code=503)


@app.get("/login")
def login_page(request: Request):
    """User sign-in. Admins are pointed at their own console so each role has a
    single obvious door."""
    user = load_user(verify_token(request.cookies.get(SESSION_COOKIE)))
    if user:
        return RedirectResponse(
            "/admin" if user.get("role") in ("admin", "superadmin") else "/",
            status_code=302,
        )
    return Response(branding.render_page("login.html"), media_type="text/html")


@app.get("/admin")
def admin_login_page(request: Request):
    """Dedicated administrator door: its own page and its own role-gated login
    endpoint. A signed-in admin is sent straight to the app; a signed-in regular
    user is sent to the user sign-in page rather than the console they cannot
    use. Anonymous visitors get the sign-in form."""
    user = load_user(verify_token(request.cookies.get(SESSION_COOKIE)))
    if user:
        return RedirectResponse(
            "/" if user.get("role") in ("admin", "superadmin") else "/login",
            status_code=302,
        )
    return Response(branding.render_page("admin.html"), media_type="text/html")


@app.get("/landing")
def landing_page():
    return Response(branding.render_page("landing.html"), media_type="text/html")


@app.get("/")
def index():
    return Response(branding.render_page("index.html"), media_type="text/html")


@app.get("/favicon.ico")
def favicon():
    """Uploaded favicon if configured, else the vendored default."""
    brand = branding.get_branding()
    if brand.get("brand_favicon"):
        return RedirectResponse(f"/api/uploads/{brand['brand_favicon']}", status_code=302)
    return FileResponse(settings.static_dir / "favicon.svg", media_type="image/svg+xml")
