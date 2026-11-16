"""Application entry point. Fully white-label: see app/branding.py."""

from __future__ import annotations

import logging
from urllib.parse import urlparse
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import (FileResponse, JSONResponse, RedirectResponse,
                               Response)
from fastapi.staticfiles import StaticFiles

from . import apitokens, auditlog, branding, permissions
from .automation import start_scheduler, stop_scheduler
from .config import settings
from .db import ping as db_ping
from .db import (MIGRATIONS, NoWorkspace, _ws_var, init_db, pool_stats, query, scalar,
                 reset_current_user, set_current_user)
from . import mcp as mcp_server
from .routers import (startups as startups_router, campaigns as campaigns_router, crm, meetings as meetings_router, pipeline as pipeline_router, proposals as proposals_router, segments as segments_router, fit as fit_router, replies as replies_router, discovery, audits, auth, automation, intelligence, branding as branding_router, leads, outreach,
                      plans, profile, search, stats, users, workspaces)
from .tenancy import TenancyError, resolve_workspace
from .security import (SESSION_COOKIE, client_ip, load_user, rate_limit, verify_token)

PUBLIC_PATHS = {"/landing", "/login", "/admin", "/health", "/health/live", "/health/ready", "/api/auth/login",
                "/api/auth/admin/login", "/api/auth/password-reset/request",
                "/api/auth/password-reset/confirm", "/favicon.ico"}
# Self-service account actions a read-only viewer may still do.
VIEWER_WRITABLE = ("/api/auth/logout", "/api/auth/mfa", "/api/auth/password-reset")
PUBLIC_PREFIXES = ("/static/", "/api/uploads/", "/api/branding/theme.css", "/p/")


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


def _cross_site_write(request: Request) -> bool:
    """CSRF guard for the cookie session: a state-changing request whose Origin header names a
    different host than the one it was sent to is refused. Browsers always send Origin on
    cross-origin writes; requests without it (curl, scripts, bearer tokens) are not a CSRF
    vector, so they pass."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return False
    origin = request.headers.get("origin")
    if not origin:
        return False
    host = request.headers.get("x-forwarded-host") if settings.trust_proxy else None
    host = host or request.headers.get("host", "")
    return urlparse(origin).netloc.lower() != host.lower()


@app.middleware("http")
async def auth_and_rate_limit(request: Request, call_next):
    path = request.url.path

    if path.startswith("/api/") or path == "/mcp":
        client = client_ip(request)
        if not rate_limit(f"api:{client}", limit=settings.api_rate_limit,
                          window_seconds=settings.api_rate_window):
            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)

    if _cross_site_write(request):
        return JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)

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

    bearer = request.headers.get("authorization", "")
    token_ws = None
    if bearer[:11].lower() == "bearer lgk_":
        # Read-only API token (Claude / scripts): GET on an allow-list only, acts as its owner.
        if not apitokens.allowed(request.method, path):
            return JSONResponse({"detail": "API tokens are read-only and limited to lead data"}, status_code=403)
        found = apitokens.authenticate(bearer[7:].strip())
        user = load_user(found["username"]) if found else None
        token_ws = found["workspace_id"] if found else None
    else:
        user = verify_token(request.cookies.get(SESSION_COOKIE))
        if user:
            # Re-read the row so a deactivated or deleted account is locked out on
            # the very next request, not whenever the token happened to expire.
            user = load_user(user)
    if not user:
        if path.startswith("/api/") or path == "/mcp":
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        # The marketing landing page is the public front door; every other
        # page funnels to the sign-in form.
        return RedirectResponse("/landing" if path == "/" else "/login", status_code=302)

    # Tenant scope. A user with no active workspace gets no scope at all, so any
    # tenant-scoped query fails with NoWorkspace (-> 403) instead of seeing data.
    try:
        membership = resolve_workspace(user["id"], str(token_ws) if token_ws else request.headers.get("x-workspace-id"))
    except TenancyError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=403)
    if user.get("role") == "viewer" and request.method not in ("GET", "HEAD", "OPTIONS") \
            and not path.startswith(VIEWER_WRITABLE):
        return JSONResponse({"detail": "This account is read-only"}, status_code=403)
    feature = permissions.blocked_feature(user, path)
    if feature:
        return JSONResponse({"detail": f"You don't have access to {permissions.FEATURES[feature][0]}. "
                                       "Ask an administrator."}, status_code=403)
    user["workspace_id"] = membership["workspace_id"] if membership else None
    user["workspace_role"] = membership["member_role"] if membership else None
    request.state.user = user
    request.state.auth = "token" if token_ws else "cookie"
    wtoken = _ws_var.set(user["workspace_id"])
    utoken = set_current_user(user)
    try:
        response = await call_next(request)
        auditlog.record(user, request.method, path, response.status_code, client_ip(request))
    finally:
        _ws_var.reset(wtoken)
        reset_current_user(utoken)
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
app.include_router(discovery.router)
app.include_router(replies_router.router)
app.include_router(fit_router.router)
app.include_router(crm.router)
app.include_router(campaigns_router.router)
app.include_router(pipeline_router.router)
app.include_router(meetings_router.router)
app.include_router(proposals_router.router)
app.include_router(proposals_router.public)
app.include_router(segments_router.router)
app.include_router(startups_router.router)
app.include_router(mcp_server.router)

app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")


@app.get("/health/live")
async def health_live():
    """The process is up and its event loop is answering. Deliberately `async`
    and DB-free: it runs on the loop itself (not the worker thread pool), so it
    proves the interpreter is responsive and never flaps on a database blip."""
    return {"status": "ok"}


def _db_check() -> dict[str, str]:
    """Probe MySQL through the same connection manager every query uses (so a
    stale handle is healed, not just reported). Failure detail goes to the log
    only - the response never carries driver text, hosts or credentials."""
    checks: dict[str, str] = {"mysql": "error", "migrations": "unknown"}
    try:
        if scalar("SELECT 1") != 1:
            raise RuntimeError("SELECT 1 returned an unexpected value")
        checks["mysql"] = "ok"
        applied = {r["version"] for r in query("SELECT version FROM schema_migrations")}
        pending = [v for v, _, _ in MIGRATIONS if v not in applied]
        checks["migrations"] = "ok" if not pending else "pending"
        if pending:
            logging.getLogger("leadgen.health").error("migrations pending: %s", pending)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("leadgen.health").error(
            "readiness: database check failed: %s %r (pool=%s)",
            type(exc).__name__, exc.args[:2], pool_stats())
    return checks


@app.get("/health/ready")
def health_ready():
    """Ready to serve: DB reachable (via the shared connection manager) and
    all migrations applied. 200 when ready, 503 otherwise."""
    checks = _db_check()
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse({"status": "ok" if ok else "unavailable", "checks": checks},
                        status_code=200 if ok else 503)


@app.get("/health")
def health():
    """Unauthenticated probe for uptime monitors. Equivalent to readiness
    without the migration check."""
    if db_ping():
        return {"status": "ok"}
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


@app.get("/startups")
def startups_page():
    return RedirectResponse("/#/startups", status_code=302)


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
