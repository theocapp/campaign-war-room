"""Tests for the auto-trigger that schedules a debounced rematch after
narrative-frame CRUD events.
"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services import scheduler as sched_mod


def test_schedule_noop_when_scheduler_not_running(monkeypatch):
    """If APScheduler isn't running (e.g. tests, early boot), the helper
    must not raise — the daily rematch_recent catches drift instead.
    """
    monkeypatch.setattr(sched_mod, "_scheduler", None)
    # Must not raise
    sched_mod.schedule_rematch_after_frame_edit()


def test_schedule_invokes_add_job_with_replace_existing(monkeypatch):
    """When scheduler is running, the helper should call add_job with
    replace_existing=True so rapid edits debounce naturally.
    """
    mock_sched = MagicMock()
    mock_sched.running = True
    monkeypatch.setattr(sched_mod, "_scheduler", mock_sched)

    sched_mod.schedule_rematch_after_frame_edit(debounce_seconds=30)

    assert mock_sched.add_job.called
    call_kwargs = mock_sched.add_job.call_args.kwargs
    assert call_kwargs["trigger"] == "date"
    assert call_kwargs["id"] == "rematch_after_frame_edit"
    assert call_kwargs["replace_existing"] is True
    assert call_kwargs["max_instances"] == 1
    # run_date should be ~30s in the future
    delta = (call_kwargs["run_date"] - datetime.utcnow()).total_seconds()
    assert 25 < delta < 35


def test_schedule_replaces_existing_on_rapid_edits(monkeypatch):
    """5 edits in a row should result in 5 add_job calls — all with the
    same job id and replace_existing=True, so only the last one fires.
    APScheduler handles the actual replacement; we just verify the contract.
    """
    mock_sched = MagicMock()
    mock_sched.running = True
    monkeypatch.setattr(sched_mod, "_scheduler", mock_sched)

    for _ in range(5):
        sched_mod.schedule_rematch_after_frame_edit()
    assert mock_sched.add_job.call_count == 5
    for call in mock_sched.add_job.call_args_list:
        assert call.kwargs["id"] == "rematch_after_frame_edit"
        assert call.kwargs["replace_existing"] is True


def test_schedule_handles_scheduler_not_yet_started(monkeypatch):
    """If _scheduler object exists but isn't running yet, still no-op."""
    fake_sched = SimpleNamespace(running=False, add_job=MagicMock())
    monkeypatch.setattr(sched_mod, "_scheduler", fake_sched)
    sched_mod.schedule_rematch_after_frame_edit()
    assert not fake_sched.add_job.called
