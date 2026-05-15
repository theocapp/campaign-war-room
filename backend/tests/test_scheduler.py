"""
Tests for the scheduler and shared RSS ingestion lock.

Kept deliberately lightweight — no live event loop, no real DB, no network.
The critical invariant being tested: if the lock is already held, a concurrent
trigger must skip rather than deadlock or double-ingest.
"""
import threading


# ── Lock tests (sync, no async needed) ───────────────────────────────────────

class TestIngestLock:
    def test_skips_when_lock_held(self):
        from app.services.rss_ingestion import ingest_lock, try_ingest_all_rss

        ingest_lock.acquire()
        try:
            result = try_ingest_all_rss(skip_if_locked=True)
            assert result is None, "Should return None when lock is already held"
        finally:
            ingest_lock.release()

    def test_lock_released_after_skip(self):
        """Lock must not be consumed by the skipped call."""
        from app.services.rss_ingestion import ingest_lock, try_ingest_all_rss

        ingest_lock.acquire()
        try:
            try_ingest_all_rss(skip_if_locked=True)
        finally:
            ingest_lock.release()

        # Lock should be free again — acquirable immediately.
        acquired = ingest_lock.acquire(blocking=False)
        assert acquired, "Lock should be free after the skipped call"
        ingest_lock.release()

    def test_concurrent_thread_skips(self):
        """A second thread hitting try_ingest_all_rss while the first holds the lock gets None."""
        from app.services.rss_ingestion import ingest_lock, try_ingest_all_rss

        results: list = []

        def second_thread():
            results.append(try_ingest_all_rss(skip_if_locked=True))

        ingest_lock.acquire()
        try:
            t = threading.Thread(target=second_thread)
            t.start()
            t.join(timeout=2)
        finally:
            ingest_lock.release()

        assert results == [None]


# ── Scheduler config tests (no event loop needed) ────────────────────────────

class TestSchedulerConfig:
    def test_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("RSS_AUTO_INGEST_ENABLED", raising=False)
        from app.services import scheduler
        assert scheduler._is_enabled() is True

    def test_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("RSS_AUTO_INGEST_ENABLED", "false")
        from app.services import scheduler
        assert scheduler._is_enabled() is False

    def test_disabled_via_zero(self, monkeypatch):
        monkeypatch.setenv("RSS_AUTO_INGEST_ENABLED", "0")
        from app.services import scheduler
        assert scheduler._is_enabled() is False

    def test_default_interval(self, monkeypatch):
        monkeypatch.delenv("RSS_AUTO_INGEST_INTERVAL_MINUTES", raising=False)
        from app.services import scheduler
        assert scheduler._interval_minutes() == 30  # default is 30 min

    def test_custom_interval(self, monkeypatch):
        monkeypatch.setenv("RSS_AUTO_INGEST_INTERVAL_MINUTES", "30")
        from app.services import scheduler
        assert scheduler._interval_minutes() == 30

    def test_invalid_interval_falls_back_to_60(self, monkeypatch):
        monkeypatch.setenv("RSS_AUTO_INGEST_INTERVAL_MINUTES", "not-a-number")
        from app.services import scheduler
        assert scheduler._interval_minutes() == 60

    def test_interval_minimum_is_one(self, monkeypatch):
        monkeypatch.setenv("RSS_AUTO_INGEST_INTERVAL_MINUTES", "0")
        from app.services import scheduler
        assert scheduler._interval_minutes() == 1


class TestSchedulerJobConfig:
    def test_job_is_added_with_correct_id(self):
        """Verify the job can be added to a scheduler instance without crashing."""
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from app.services.scheduler import _scheduled_ingest_job

        sched = AsyncIOScheduler()
        sched.add_job(
            _scheduled_ingest_job,
            trigger="interval",
            minutes=60,
            id="rss_auto_ingest",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        jobs = sched.get_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == "rss_auto_ingest"

    def test_start_scheduler_disabled(self, monkeypatch):
        """start_scheduler() with disabled flag must leave _scheduler as None."""
        monkeypatch.setenv("RSS_AUTO_INGEST_ENABLED", "false")
        import app.services.scheduler as sched_mod

        original = sched_mod._scheduler
        sched_mod._scheduler = None
        try:
            sched_mod.start_scheduler()
            assert sched_mod._scheduler is None
        finally:
            sched_mod._scheduler = original
