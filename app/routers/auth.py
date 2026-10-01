from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from ..config import settings
from ..security import (SESSION_COOKIE, authenticate_user, client_ip, issue_token,
                        rate_limit)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginPayload(BaseModel):
    username: str
    password: str


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
                           response: Response, role_floor: str | None):
    """Shared login path. `role_floor` upgrades the endpoint to an admin-only
    door: the same credentials are validated the same way, but any account
    below the floor is rejected outright. 'admin' means admin OR superadmin."""
    client = client_ip(request)
    if not rate_limit(f"login:{client}", limit=settings.login_rate_limit,
                      window_seconds=settings.login_rate_window):
        raise HTTPException(
            429,
            f"Too many login attempts. Wait {settings.login_rate_window // 60} minutes.",
        )

    user = authenticate_user(payload.username, payload.password)
    if not user:
        raise HTTPException(401, "Invalid username or password")

    if role_floor:
        level = {"user": 0, "admin": 1, "superadmin": 2}.get(user.get("role"), 0)
        floor = {"admin": 1, "superadmin": 2}.get(role_floor, 0)
        if level < floor:
            # Deliberately the same message as a bad password: the endpoint
            # must not reveal whether the account exists or what role it has.
            raise HTTPException(401, "Invalid username or password")

    _set_session_cookie(response, issue_token(user["username"]))
    return {"ok": True, "user": user}


@router.post("/login")
def login(payload: LoginPayload, request: Request, response: Response):
    """Standard sign-in for every role."""
    return _login_with_role_floor(payload, request, response, role_floor=None)


@router.post("/admin/login")
def admin_login(payload: LoginPayload, request: Request, response: Response):
    """Sign-in for the /admin console. Accepts admin and superadmin accounts
    only - a regular user's correct credentials are rejected with the same
    401 as a wrong password, so the endpoint leaks nothing."""
    return _login_with_role_floor(payload, request, response, role_floor="admin")


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    user = getattr(request.state, "user", None)
    subscription = None
    if user:
        from ..subscription import active_subscription
        subscription = active_subscription(user["id"])
    return {"user": user, "subscription": subscription}
