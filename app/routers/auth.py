from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from ..config import settings
from ..security import (SESSION_COOKIE, check_credentials, client_ip, issue_token,
                        rate_limit)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginPayload(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(payload: LoginPayload, request: Request, response: Response):
    client = client_ip(request)
    if not rate_limit(f"login:{client}", limit=settings.login_rate_limit,
                      window_seconds=settings.login_rate_window):
        raise HTTPException(
            429,
            f"Too many login attempts. Wait {settings.login_rate_window // 60} minutes.",
        )

    if not check_credentials(payload.username, payload.password):
        raise HTTPException(401, "Invalid username or password")

    token = issue_token(payload.username)
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=settings.session_hours * 3600,
        httponly=True, samesite="lax",
        # Set COOKIE_SECURE=true once you're behind HTTPS (Part 7.5). Off by
        # default so local http development can still sign in - a secure cookie
        # is silently dropped over plain http, which looks like a broken login.
        secure=settings.cookie_secure,
    )
    return {"ok": True, "user": payload.username}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    return {"user": getattr(request.state, "user", None)}
