"""Tests for the require_admin FastAPI dependency.

Verifies the three branches of access_codes.require_admin:

  1. No ACCESS_CODES configured (dev mode) → bypass, returns synthetic admin.
  2. Codes configured, localhost X-Forwarded-Host → bypass.
  3. Codes configured, non-localhost host → require an admin code (403 on
     non-admin, 200 on admin).

The dependency is the gate that protects every LLM-cost endpoint, so a
single regression here would silently expose money-burning routes to
non-admin users. The cost of one focused test is much cheaper than
discovering the regression in a bill.
"""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.services.access_codes import require_admin, reset_cache_for_tests


@pytest.fixture
def app_with_gated_route(monkeypatch):
    """Build a tiny app whose one route is gated by require_admin.

    Kept separate from `from app.main import app` because the production
    app eagerly loads ~40 routes; this fixture isolates the dependency
    under test from incidental import-time effects.
    """
    # Mirror main.py's middleware so the gate sees request.state.user set.
    from fastapi import Request
    from fastapi.responses import JSONResponse
    from app.services.access_codes import is_auth_configured, lookup_code

    app = FastAPI()

    @app.middleware("http")
    async def code_guard(request: Request, call_next):
        if not is_auth_configured():
            return await call_next(request)
        forwarded = (request.headers.get("x-forwarded-host") or "").lower()
        if forwarded.startswith("localhost") or forwarded.startswith("127.0.0.1"):
            return await call_next(request)
        code = request.headers.get("x-access-code")
        user = lookup_code(code)
        if user is None:
            return JSONResponse(status_code=401, content={"detail": "no code"})
        request.state.user = user
        return await call_next(request)

    @app.post("/gated", dependencies=[Depends(require_admin)])
    def gated() -> dict:
        return {"ok": True}

    return app


def test_no_codes_configured_bypasses_admin_check(app_with_gated_route, monkeypatch):
    """Dev-mode contract — when ACCESS_CODES is empty, every caller is
    treated as admin so local dev doesn't need a code juggle."""
    monkeypatch.setenv("ACCESS_CODES", "")
    reset_cache_for_tests()
    c = TestClient(app_with_gated_route)
    r = c.post("/gated")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_localhost_forwarded_host_bypasses_admin_check(app_with_gated_route, monkeypatch):
    """When codes ARE configured, requests originating on the user's own
    machine (Vite proxy → backend) skip the gate so local iteration works
    without juggling codes."""
    monkeypatch.setenv("ACCESS_CODES", "friend-7m4q:Friend Smith;boss-9k2x:Boss Jones:admin")
    reset_cache_for_tests()
    c = TestClient(app_with_gated_route)
    r = c.post("/gated", headers={"x-forwarded-host": "localhost:5174"})
    assert r.status_code == 200


def test_non_admin_code_gets_403(app_with_gated_route, monkeypatch):
    """The actual gate — a logged-in non-admin user calling an LLM-cost
    endpoint must get 403, NOT 401 (they ARE authenticated, just not
    authorized for this action)."""
    monkeypatch.setenv("ACCESS_CODES", "friend-7m4q:Friend Smith;boss-9k2x:Boss Jones:admin")
    reset_cache_for_tests()
    c = TestClient(app_with_gated_route)
    r = c.post(
        "/gated",
        headers={"x-forwarded-host": "noctua.example.com", "x-access-code": "friend-7m4q"},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "admin only"


def test_admin_code_gets_through(app_with_gated_route, monkeypatch):
    """An admin code presented from a non-localhost host should pass —
    this is the production-tunnel path."""
    monkeypatch.setenv("ACCESS_CODES", "friend-7m4q:Friend Smith;boss-9k2x:Boss Jones:admin")
    reset_cache_for_tests()
    c = TestClient(app_with_gated_route)
    r = c.post(
        "/gated",
        headers={"x-forwarded-host": "noctua.example.com", "x-access-code": "boss-9k2x"},
    )
    assert r.status_code == 200


def test_missing_code_gets_401_not_403(app_with_gated_route, monkeypatch):
    """When no code is presented at all, the middleware 401s before the
    require_admin gate runs. This is the right ordering: 401 = "no
    identity," 403 = "identity insufficient.\""""
    monkeypatch.setenv("ACCESS_CODES", "boss-9k2x:Boss Jones:admin")
    reset_cache_for_tests()
    c = TestClient(app_with_gated_route)
    r = c.post("/gated", headers={"x-forwarded-host": "noctua.example.com"})
    assert r.status_code == 401
