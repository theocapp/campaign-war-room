"""Friend-share access codes.

Tiny shared-secret auth so the user can hand 2-3 friends a personal code
each before tunnelling the app to the public internet. Not a real auth
system — codes are static strings in .env, no rotation, no rate limit.
Adequate for "show a small private group", not for anything else.

Config format in .env (semicolon-separated, each entry "code:Display Name"
with an optional ":admin" suffix that grants admin powers):

    ACCESS_CODES=alice-7m4q:Alice Smith;bob-9k2x:Bob Jones;theo-2x8p:Theo:admin

Initials are auto-derived from the display name (first letter of each
word, capped at 2 chars). The user color is a deterministic hash of the
code so each friend gets a stable, distinct profile bubble color.

Admin flag is currently used to gate the raw relevance bucket badges
(CRITICAL/HIGH/MEDIUM/LOW) — those eroded non-admin trust by inviting
scrutiny of a per-article confidence signal that's noisy at the row
level. Non-admins still see the ranking; just no label.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class AccessUser:
    code: str
    name: str
    initials: str
    color: str
    is_admin: bool = False


_PALETTE = [
    "#0059c2",  # candidate blue
    "#16a34a",  # green
    "#d97706",  # amber
    "#9333ea",  # purple
    "#0891b2",  # cyan
    "#dc2626",  # red
    "#0d9488",  # teal
    "#c026d3",  # fuchsia
]


def _initials(name: str) -> str:
    parts = [p for p in name.strip().split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _color_for(code: str) -> str:
    h = int(hashlib.sha1(code.encode("utf-8")).hexdigest(), 16)
    return _PALETTE[h % len(_PALETTE)]


@lru_cache(maxsize=1)
def _load_codes() -> dict[str, AccessUser]:
    raw = os.environ.get("ACCESS_CODES", "").strip()
    if not raw:
        return {}
    out: dict[str, AccessUser] = {}
    for entry in raw.split(";"):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        # Accept either "code:Name" or "code:Name:admin". The third segment
        # is opt-in; missing or anything other than the literal "admin"
        # token (case-insensitive) means non-admin. We split into at most
        # three parts so display names containing a colon survive intact
        # (last segment is the role flag, not part of the name).
        parts = entry.split(":", 2)
        code = parts[0].strip()
        name = parts[1].strip() if len(parts) >= 2 else ""
        role = parts[2].strip().lower() if len(parts) == 3 else ""
        if not code or not name:
            continue
        out[code] = AccessUser(
            code=code,
            name=name,
            initials=_initials(name),
            color=_color_for(code),
            is_admin=(role == "admin"),
        )
    return out


def lookup_code(code: str | None) -> AccessUser | None:
    if not code:
        return None
    return _load_codes().get(code.strip())


def is_auth_configured() -> bool:
    """Returns True if any ACCESS_CODES are configured.

    Used so the app fails open (no auth) when codes aren't set up — handy
    during local dev — but fails closed the moment the user adds any codes
    (i.e., right before they tunnel the app to the internet).
    """
    return len(_load_codes()) > 0


def reset_cache_for_tests() -> None:
    _load_codes.cache_clear()


# --- FastAPI dependency ------------------------------------------------------
#
# `require_admin` is the gate that protects endpoints which spend real money on
# LLM calls (rescore, briefing, frame suggestion, etc.). The middleware in
# main.py already sets `request.state.user` whenever a valid code is presented;
# here we just enforce that the user has the admin flag.
#
# Bypass rules — mirror /api/auth/me so behaviors stay consistent:
#   1. No ACCESS_CODES configured → local dev, fail open (every request is admin).
#   2. Request originated on localhost via Vite's proxy (X-Forwarded-Host) → admin.
# In both cases the middleware also let the request through without a code, so
# request.state.user may not be set — fall back to "allow".
#
# When codes ARE configured and the request is NOT local, require an actual
# admin user. Anything else → 403 (NOT 401, because the user IS authenticated,
# just not authorized for this action).

def require_admin(request):  # type: ignore[no-untyped-def]
    """FastAPI dependency that raises 403 unless the caller is admin.

    Usage:
        @router.post("/expensive", dependencies=[Depends(require_admin)])
        def expensive_thing(...): ...

    or, if you want the user object in your handler:

        def handler(..., user: AccessUser = Depends(require_admin)): ...

    Note: the `request` parameter is annotated below via a runtime cast to
    starlette.Request so FastAPI's dependency resolver recognises it as the
    request object rather than a query parameter. We do the annotation at
    runtime (rather than as a Python type hint) so this module stays free
    of an unconditional FastAPI import — keeps pure-services pytest fast.
    """
    # Import lazily so the services package doesn't carry a hard FastAPI dep
    # at module-import time (keeps `pytest` collection of pure services fast).
    from fastapi import HTTPException

    if not is_auth_configured():
        # Dev mode — no codes set, middleware lets everything through, admin
        # check is a no-op. Return a synthetic admin user so handlers that
        # want the object still get something.
        return AccessUser(code="", name="Guest", initials="G", color="#6b7280", is_admin=True)

    forwarded_host = (request.headers.get("x-forwarded-host") or "").lower()
    if forwarded_host.startswith("localhost") or forwarded_host.startswith("127.0.0.1"):
        return AccessUser(code="", name="Local Dev", initials="LD", color="#6b7280", is_admin=True)

    user = getattr(request.state, "user", None)
    if user is None or not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="admin only")
    return user


# Re-bind require_admin with a proper type annotation on the `request`
# parameter. FastAPI inspects parameter types to decide what to inject
# (Request → the live request; anything else → query/body/etc.), and
# without this the dependency resolver tries to look up `request` as a
# query param and 422s every gated route.
def _wire_require_admin() -> None:
    from starlette.requests import Request
    require_admin.__annotations__ = {"request": Request, "return": "AccessUser"}


_wire_require_admin()
