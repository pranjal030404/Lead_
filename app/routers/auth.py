from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from ..config import settings
from .. import mfa, passwordreset
from ..security import (SESSION_COOKIE, account_status, authenticate_user, client_ip,
                        issue_token, normalize_username, rate_limit, revoke_sessions)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginPayload(BaseModel):
    username: str
    password: str
    code: str | None = None          # authenticator code, required once MFA is enabled


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=settings.session_hours * 3600,
        httponly=True, samesite="lax",
        # Set COOKIE_SECURE=true once you're behind HTTPS (Part 7.5). Off by
        # default so local http development can still sign in - a secure cookie
        # is silently dropped over plain http, which looks like a broken login.
        secure=settings.cookie_secure,
    )


def _login_with_role_floor(payload: LoginPayload, request: Request,
                           response: Response, role_floor: str | None,
                           block_admin: bool = False):
    """Shared login path. `role_floor` upgrades the endpoint to an admin-only
    door: the same credentials are validated the same way, but any account
    below the floor is rejected outright. 'admin' means admin OR superadmin.

    `block_admin` is the mirror image: the public sign-in door turns admins and
    superadmins away and points them at /admin, so the two consoles stay
    separate."""
    client = client_ip(request)
    if not rate_limit(f"login:{client}", limit=settings.login_rate_limit,
                      window_seconds=settings.login_rate_window):
        raise HTTPException(
            429,
            f"Too many login attempts. Wait {settings.login_rate_window // 60} minutes.",
        )

    username = normalize_username(payload.username)       # a username OR an email
    user = authenticate_user(username, payload.password)
    if not user:
        # A wrong password/deactivated account gets the same 401 as an unknown
        # user, so neither door lets an attacker enumerate accounts. The public
        # sign-in door is allowed to be helpful about a deactivated account: it
        # is the usual reason a just-created login appears to "not work", and the
        # generic message sends people chasing a password they never got wrong.
        if role_floor is None and account_status(username) == "inactive":
            raise HTTPException(
                403,
                "This account has been deactivated. Ask an administrator to re-enable it.",
            )
        raise HTTPException(401, "Invalid username/email or password")

    mfa_state = mfa.state(user["id"])
    if mfa_state["enabled"] and not mfa.verify_code(mfa_state["secret"], payload.code):
        # Password was right, second factor missing/wrong. Same 401 shape either way.
        raise HTTPException(401, "Authenticator code required" if not payload.code
                            else "Invalid authenticator code",
                            headers={"X-MFA-Required": "1"})

    if block_admin and user.get("role") in ("admin", "superadmin"):
        # Correct credentials, but the wrong door. Admins use /admin; telling
        # the account's own owner this leaks nothing (same reasoning as below).
        raise HTTPException(
            403,
            "Administrators sign in at /admin.",
        )

    if role_floor:
        level = {"user": 0, "admin": 1, "superadmin": 2}.get(user.get("role"), 0)
        floor = {"admin": 1, "superadmin": 2}.get(role_floor, 0)
        if level < floor:
            # The credentials are correct - this is the account's own owner - so
            # telling them which door to use leaks nothing an attacker could not
            # already learn by guessing a password. Without it, a plain user
            # handed the /admin URL sees "Invalid username or password" and
            # concludes their freshly created account doesn't work.
            raise HTTPException(
                403,
                "This page is for administrators. Please sign in at /login.",
            )

    _set_session_cookie(response, issue_token(user["username"]))
    return {"ok": True, "user": user}


@router.post("/login")
def login(payload: LoginPayload, request: Request, response: Response):
    """Standard sign-in for regular users. Admins and superadmins are told to
    use /admin instead - the two consoles are deliberately separate doors."""
    return _login_with_role_floor(payload, request, response, role_floor=None,
                                  block_admin=True)


@router.post("/admin/login")
def admin_login(payload: LoginPayload, request: Request, response: Response):
    """Sign-in for the /admin console. Accepts admin and superadmin accounts
    only. Wrong credentials get the same 401 as anywhere else; a regular user's
    *correct* credentials get a 403 pointing them at /login, because telling the
    account's own owner which door to use leaks nothing."""
    return _login_with_role_floor(payload, request, response, role_floor="admin")


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.post("/logout-all")
def logout_all(request: Request, response: Response):
    """Sign out everywhere: every session cookie issued so far stops working."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "Not authenticated")
    revoke_sessions(user["id"])
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


class ResetRequest(BaseModel):
    identifier: str


class ResetConfirm(BaseModel):
    token: str
    password: str


@router.post("/password-reset/request")
def password_reset_request(payload: ResetRequest, request: Request):
    """Always answers the same, so it can't be used to find which accounts exist."""
    if not rate_limit(f"reset:{client_ip(request)}", limit=5, window_seconds=900):
        raise HTTPException(429, "Too many reset requests. Try again later.")
    passwordreset.request_reset(payload.identifier)
    return {"ok": True, "message": "If that account exists and has an email, a reset link was sent."}


@router.post("/password-reset/confirm")
def password_reset_confirm(payload: ResetConfirm, request: Request):
    if not rate_limit(f"resetc:{client_ip(request)}", limit=10, window_seconds=900):
        raise HTTPException(429, "Too many attempts. Try again later.")
    try:
        ok = passwordreset.confirm_reset(payload.token, payload.password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not ok:
        raise HTTPException(400, "This reset link is invalid or has expired.")
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    user = getattr(request.state, "user", None)
    subscription = None
    if user:
        from ..subscription import active_subscription
        subscription = active_subscription(user["id"])
    return {"user": user, "subscription": subscription, "free_mode": settings.free_mode}
