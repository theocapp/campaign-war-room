"""Auth endpoints for the friend-share access-code gate.

Two endpoints:
  POST /api/auth/verify  — body {"code": "..."} → returns user info or 401.
                            Used by the login page to validate before saving
                            the code to localStorage.
  GET  /api/auth/me      — reads X-Access-Code header → returns user info
                            or 401. Used by the frontend AuthContext on app
                            load to restore session.

Neither endpoint sets a cookie. The frontend stores the code in localStorage
and sends it back as `X-Access-Code` on every request. The middleware in
main.py enforces the header on all other /api/* routes.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from app.services.access_codes import AccessUser, is_auth_configured, lookup_code

router = APIRouter()


class VerifyBody(BaseModel):
    code: str


class UserOut(BaseModel):
    name: str
    initials: str
    color: str
    is_admin: bool = False
    # We deliberately do NOT echo the code back. The frontend already has it
    # in localStorage; bouncing it through the API just gives one more place
    # to leak it.


def _to_out(user: AccessUser) -> UserOut:
    return UserOut(
        name=user.name,
        initials=user.initials,
        color=user.color,
        is_admin=user.is_admin,
    )


@router.post("/auth/verify", response_model=UserOut)
def verify(body: VerifyBody) -> UserOut:
    user = lookup_code(body.code)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid code")
    return _to_out(user)


@router.get("/auth/me", response_model=UserOut)
def me(
    request: Request,
    x_access_code: str | None = Header(default=None),
) -> UserOut:
    # If no codes are configured at all, this endpoint should not be the
    # thing telling the user "you're logged out" — the middleware already
    # lets requests through in that mode. Return a generic guest so the
    # frontend can render without forcing a login it can't satisfy. Guest
    # is admin in this mode because no-auth = local dev = the operator.
    if not is_auth_configured():
        return UserOut(name="Guest", initials="G", color="#6b7280", is_admin=True)
    user = lookup_code(x_access_code)
    if user is not None:
        return _to_out(user)
    # Localhost bypass — when the request started on the user's own machine
    # (Vite's proxy forwards the browser-visible host header), grant a dev
    # session so the user and Claude Code's preview don't need a code for
    # local iteration. Tunnel traffic carries the public hostname here and
    # falls through to the 401 below. Local dev = the operator, so admin.
    forwarded_host = (request.headers.get("x-forwarded-host") or "").lower()
    if forwarded_host.startswith("localhost") or forwarded_host.startswith("127.0.0.1"):
        return UserOut(name="Local Dev", initials="LD", color="#6b7280", is_admin=True)
    raise HTTPException(status_code=401, detail="invalid code")
