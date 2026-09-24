"""Arthvex LeadGen - application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .automation import start_scheduler, stop_scheduler
from .config import settings
from .db import init_db, query_one
from .routers import auth, automation, leads, outreach, plans, search, stats, users
from .security import (SESSION_COOKIE, client_ip, load_user, rate_limit, verify_token)

PUBLIC_PATHS = {"/login", "/health", "/api/auth/login", "/favicon.ico"}
PUBLIC_PREFIXES = ("/static/",)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_runtime_secrets()
    init_db()
    start_scheduler()

    banner = [
        "",
        "  Arthvex LeadGen is running",
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
    title="Arthvex LeadGen",
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
        if path.startswith(PUBLIC_PREFIXES):
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
        return RedirectResponse("/login", status_code=302)

    request.state.user = user
    return await call_next(request)


app.include_router(auth.router)
app.include_router(leads.router)
app.include_router(search.router)
app.include_router(stats.router)
app.include_router(outreach.router)
app.include_router(automation.router)
app.include_router(users.router)
app.include_router(plans.router)

app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")


@app.get("/health")
def health():
    """Unauthenticated liveness probe - safe to point a uptime monitor at."""
    try:
        query_one("SELECT 1 AS ok")
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=503)


@app.get("/login")
def login_page():
    return FileResponse(settings.static_dir / "login.html")


@app.get("/")
def index():
    return FileResponse(settings.static_dir / "index.html")
