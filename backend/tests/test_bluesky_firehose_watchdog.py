"""Bluesky firehose self-healing watchdog + keyword broadening.

Two failure modes the watchdog covers, neither of which the websockets
client raises on:
  - dead:   the long-running asyncio task crashed or never started
  - wedged: the socket stays "connected" but stops delivering frames, so
            events_seen goes flat while we still believe we're connected

get_health() classifies these from module state; restart() revives the task.
Plus: the keyword set the firehose filters on must include the district's
cities (the whole point of "broaden Bluesky"), with env escape hatches.

Everything network/asyncio/clock is stubbed — these tests never open a
socket, never touch a real event loop's wall time, and never hit the DB
except via in-memory SQLite.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CampaignConfig, Opponent
from app.services import bluesky_firehose as fh


# --------------------------------------------------------------------------
# get_health()
# --------------------------------------------------------------------------

class _FakeTask:
    """Stand-in for the asyncio.Task; only .done() is read by get_health."""
    def __init__(self, done: bool):
        self._done = done

    def done(self) -> bool:
        return self._done


@pytest.fixture
def reset_state(monkeypatch):
    """Give each health test a clean, isolated copy of the module globals.

    monkeypatch.setattr restores the originals afterward, so a test can't
    leak a fake task or a frozen clock into the next one (or into the live
    process if these ever run in-process)."""
    monkeypatch.setattr(fh, "_task", None)
    monkeypatch.setattr(fh, "_running", False)
    monkeypatch.setattr(fh, "_last_event_monotonic", None)
    monkeypatch.setattr(fh, "_stats", {
        "started_at": None,
        "events_seen": 0,
        "events_matched": 0,
        "events_written": 0,
        "reconnects": 0,
        "last_error": None,
        "last_match_at": None,
    })
    return monkeypatch


def test_health_disabled_when_never_started(reset_state):
    # No task, never ran, not running → firehose was simply turned off.
    assert fh.get_health(now=1000.0)["state"] == "disabled"


def test_health_dead_when_task_finished(reset_state):
    reset_state.setattr(fh, "_task", _FakeTask(done=True))
    reset_state.setattr(fh, "_running", True)
    fh._stats["started_at"] = "2026-05-31T00:00:00"
    assert fh.get_health(now=1000.0)["state"] == "dead"


def test_health_dead_when_task_vanished_mid_run(reset_state):
    # _task cleared but we still believe we should be running and have run
    # before (started_at set) → dead, not disabled.
    reset_state.setattr(fh, "_running", True)
    fh._stats["started_at"] = "2026-05-31T00:00:00"
    assert fh.get_health(now=1000.0)["state"] == "dead"


def test_health_ok_when_freshly_connected_no_events_yet(reset_state):
    # Connected, task alive, but no frame has arrived (last is None). Must
    # NOT be judged wedged — there's no silence to measure yet.
    reset_state.setattr(fh, "_task", _FakeTask(done=False))
    reset_state.setattr(fh, "_running", True)
    reset_state.setattr(fh, "_last_event_monotonic", None)
    health = fh.get_health(now=1_000_000.0)
    assert health["state"] == "ok"
    assert health["silent_for_s"] is None


def test_health_ok_when_recently_delivering(reset_state):
    reset_state.setattr(fh, "_task", _FakeTask(done=False))
    reset_state.setattr(fh, "_running", True)
    reset_state.setattr(fh, "_last_event_monotonic", 1000.0)
    # 10s of silence — well under the stall threshold.
    assert fh.get_health(now=1010.0)["state"] == "ok"


def test_health_wedged_when_silent_past_threshold(reset_state):
    reset_state.setattr(fh, "_task", _FakeTask(done=False))
    reset_state.setattr(fh, "_running", True)
    reset_state.setattr(fh, "_last_event_monotonic", 1000.0)
    now = 1000.0 + fh._WEDGE_STALL_S + 1.0
    health = fh.get_health(now=now)
    assert health["state"] == "wedged"
    assert health["silent_for_s"] == pytest.approx(fh._WEDGE_STALL_S + 1.0)


def test_health_threshold_is_exclusive(reset_state):
    # Exactly _WEDGE_STALL_S of silence is still "ok"; one tick past is wedged.
    reset_state.setattr(fh, "_task", _FakeTask(done=False))
    reset_state.setattr(fh, "_running", True)
    reset_state.setattr(fh, "_last_event_monotonic", 1000.0)
    assert fh.get_health(now=1000.0 + fh._WEDGE_STALL_S)["state"] == "ok"
    assert fh.get_health(now=1000.0 + fh._WEDGE_STALL_S + 0.01)["state"] == "wedged"


# --------------------------------------------------------------------------
# restart()
# --------------------------------------------------------------------------

def test_restart_stops_then_starts_and_resets_wedge_clock(monkeypatch):
    """restart() must: stop, reset the wedge clock, THEN start — in that order.

    The reset-before-start matters: the fresh connection stamps its own clock,
    and we must not let the watchdog judge the new task against the dead task's
    stale silence."""
    calls: list[str] = []
    monkeypatch.setattr(fh, "_RESTART_GRACE_S", 0.0)  # keep the test instant
    monkeypatch.setattr(fh, "_last_event_monotonic", 12345.0)

    def fake_stop():
        calls.append("stop")

    def fake_start():
        # Capture the clock value AS START SEES IT — must already be reset.
        calls.append(f"start(last={fh._last_event_monotonic})")

    monkeypatch.setattr(fh, "stop_firehose", fake_stop)
    monkeypatch.setattr(fh, "start_firehose", fake_start)

    asyncio.run(fh.restart(reason="unit-test"))

    assert calls == ["stop", "start(last=None)"]


# --------------------------------------------------------------------------
# _build_keyword_set() — broadening
# --------------------------------------------------------------------------

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    # Real campaign values (verified against the live config 2026-05-31).
    session.add(CampaignConfig(
        candidate_name="Paige Cognetti",
        district="PA-08",
        location="Scranton/Wilkes-Barre, PA-08",
    ))
    session.add(Opponent(name="Rob Bresnahan"))
    session.commit()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def test_keyword_set_includes_names_and_district(db):
    kws = fh._build_keyword_set(db)
    # Existing behavior preserved.
    assert "cognetti" in kws
    assert "paige cognetti" in kws
    assert "bresnahan" in kws
    assert "pa-08" in kws
    assert "pa 08" in kws


def test_keyword_set_adds_compact_district_form(db):
    # New: "PA-08" → "pa08" (the hashtag/handle form people actually type).
    assert "pa08" in fh._build_keyword_set(db)


def test_keyword_set_harvests_city_tokens_from_location(db):
    # New: the district's cities — the heart of "broaden Bluesky". "scranton"
    # is this module's own canonical example keyword.
    kws = fh._build_keyword_set(db)
    assert "scranton" in kws
    assert "wilkes" in kws
    assert "barre" in kws


def test_keyword_set_does_not_include_verbatim_location(db):
    # We broadened to the *tokens*, not the unique compound string, which
    # never appears in real posts.
    assert "scranton/wilkes-barre, pa-08" not in fh._build_keyword_set(db)


def test_block_keywords_env_removes_noisy_token(db, monkeypatch):
    # "barre" is the noise-risk token (ballet barre, Barre VT). The block
    # hatch must drop it without a code change.
    monkeypatch.setenv("BLUESKY_BLOCK_KEYWORDS", "barre")
    kws = fh._build_keyword_set(db)
    assert "barre" not in kws
    assert "scranton" in kws  # other cities untouched


def test_extra_keywords_env_adds_terms_bypassing_guard(db, monkeypatch):
    # Extras are user-supplied, so they're trusted even if short / generic.
    monkeypatch.setenv("BLUESKY_EXTRA_KEYWORDS", "#pa08, lackawanna , nepa")
    kws = fh._build_keyword_set(db)
    assert "#pa08" in kws
    assert "lackawanna" in kws
    assert "nepa" in kws  # 4 chars, would pass anyway, but proves additive path


def test_block_wins_over_derived_and_extra(db, monkeypatch):
    # A term that is both auto-derived AND explicitly blocked stays out.
    monkeypatch.setenv("BLUESKY_EXTRA_KEYWORDS", "scranton")
    monkeypatch.setenv("BLUESKY_BLOCK_KEYWORDS", "scranton")
    assert "scranton" not in fh._build_keyword_set(db)
