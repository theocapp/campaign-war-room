"""Regression tests for the confirm-string guards on destructive endpoints.

Two endpoints are protected:

  * POST /admin/rescore-articles — a full rescore (only_unscored=false) runs
    the LLM scoring pipeline over the entire corpus (21k+ articles, ~2/min,
    ~5 days, real money). A single misclick must not start it.

  * DELETE /narrative-frames/{id} — cascades through FrameClusterMatch,
    FrameVariant, FrameStageHistory, and NarrativeFrameMention. One frame
    can carry hundreds of these rows.

The tests use FastAPI's TestClient against the real FastAPI app, but patch
the rescore service so the validation path is exercised without launching
a real LLM worker pool.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    # Patch the rescore service before importing the app so the route's
    # `rescore_svc.start_rescore` reference resolves to our stub. The stub
    # records what was called with so we can assert the body-decoded values
    # made it through.
    from app.services import rescore as rescore_svc

    calls: list[dict] = []

    def _stub_start(db, **kwargs):
        calls.append(kwargs)
        return {"started": True, "total": 0, "max_workers": 1, "estimated_minutes": 0.0}

    monkeypatch.setattr(rescore_svc, "start_rescore", _stub_start)

    from app.main import app
    c = TestClient(app)
    c.calls = calls  # type: ignore[attr-defined]  — exposed for assertions
    return c


# ── /admin/rescore-articles guard ─────────────────────────────────────────


def test_rescore_full_run_without_confirm_rejected(client):
    r = client.post("/api/admin/rescore-articles", json={"only_unscored": False})
    assert r.status_code == 400
    assert "RESCORE ALL ARTICLES" in r.json()["detail"]
    assert client.calls == []  # service was NOT called


def test_rescore_full_run_with_wrong_confirm_rejected(client):
    r = client.post(
        "/api/admin/rescore-articles",
        json={"only_unscored": False, "confirm": "yes please"},
    )
    assert r.status_code == 400
    assert client.calls == []


def test_rescore_full_run_with_correct_confirm_accepted(client):
    r = client.post(
        "/api/admin/rescore-articles",
        json={"only_unscored": False, "confirm": "RESCORE ALL ARTICLES"},
    )
    assert r.status_code == 200
    assert len(client.calls) == 1
    assert client.calls[0]["only_unscored"] is False


def test_rescore_resume_only_unscored_does_not_require_confirm(client):
    """`only_unscored=true` is the safe resume path — it only touches
    articles that have never been scored. No confirm required."""
    r = client.post(
        "/api/admin/rescore-articles",
        json={"only_unscored": True},
    )
    assert r.status_code == 200
    assert len(client.calls) == 1
    assert client.calls[0]["only_unscored"] is True


def test_rescore_passes_through_body_fields(client):
    r = client.post(
        "/api/admin/rescore-articles",
        json={
            "only_unscored": False,
            "confirm": "RESCORE ALL ARTICLES",
            "auto_rematch": True,
            "max_workers": 4,
        },
    )
    assert r.status_code == 200
    kwargs = client.calls[0]
    assert kwargs["auto_rematch"] is True
    assert kwargs["max_workers"] == 4


# ── DELETE /narrative-frames/{id} guard ────────────────────────────────────


@pytest.fixture
def frame_id():
    """Returns the id of a real NarrativeFrame in the live DB.

    These tests run against the live Postgres `noctua` — the data validation
    matters more than test isolation here, and the DELETE path is gated by
    the confirm string so no row is actually removed.
    """
    from app.db import SessionLocal
    from app.models import NarrativeFrame
    with SessionLocal() as db:
        row = db.query(NarrativeFrame).first()
        if not row:
            pytest.skip("no narrative_frames rows in DB to exercise the guard")
        return row.id


def test_delete_frame_without_confirm_rejected(client, frame_id):
    r = client.delete(f"/api/narrative-frames/{frame_id}")
    assert r.status_code == 400
    detail = r.json()["detail"]
    # The detail string carries the URL-encoded form for copy-paste.
    assert "DELETE+FRAME" in detail or "DELETE FRAME" in detail


def test_delete_frame_with_wrong_confirm_rejected(client, frame_id):
    r = client.delete(
        f"/api/narrative-frames/{frame_id}?confirm=yes"
    )
    assert r.status_code == 400


def test_delete_frame_dry_run_does_not_delete(client, frame_id):
    """`?dry_run=true` returns cascade counts without committing anything.
    Frame must still exist afterward."""
    r = client.delete(f"/api/narrative-frames/{frame_id}?dry_run=true")
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert body["frame_id"] == frame_id
    assert "frame_name" in body
    assert "would_delete" in body
    # The cascade preview names every dependent table — drift in the
    # safe_delete_frame implementation should force this test to break.
    expected_keys = {
        "frame_cluster_matches",
        "narrative_frame_mentions",
        "frame_variants",
        "frame_stage_history",
        "candidate_frame_refs_cleared",
        "narrative_frame",
    }
    assert set(body["would_delete"].keys()) == expected_keys

    # Confirm frame is still there.
    from app.db import SessionLocal
    from app.models import NarrativeFrame
    with SessionLocal() as db:
        assert db.query(NarrativeFrame).get(frame_id) is not None


def test_delete_frame_404_on_missing_id(client):
    r = client.delete("/api/narrative-frames/999999?dry_run=true")
    assert r.status_code == 404
