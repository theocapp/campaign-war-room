"""Tests for CachedSearchProvider — the disk-backed wrapper around the
configured search provider that protects the Tavily free-tier quota from
dev iteration burn and re-clicks during user setup.

Five behaviors locked here:
  1. Miss → inner called, row persisted
  2. Hit → inner NOT called again
  3. Different `limit` → separate cache key (no silent truncation)
  4. Stale row (past TTL) → re-fetched and row updated in place
  5. SEARCH_CACHE_DISABLED=1 → cache fully bypassed

Transient inner-provider errors (message set + empty results) are NOT
cached. That property is covered in test_does_not_cache_transient_error.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.services import search_provider as sp_module
from app.services.search_provider import (
    CachedSearchProvider, SearchResponse, SearchResult,
)


@pytest.fixture
def db_engine(monkeypatch):
    """In-memory SQLite engine. Patch SessionLocal so CachedSearchProvider
    uses this database when it does its own lookups."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    import app.models  # noqa — register tables
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr("app.db.SessionLocal", TestSession)
    yield engine
    Base.metadata.drop_all(engine)


class _CountingProvider:
    """Stub inner provider that counts calls + returns deterministic results."""
    name = "stub"

    def __init__(self):
        self.calls = 0

    def search(self, query: str, limit: int = 10) -> SearchResponse:
        self.calls += 1
        return SearchResponse(
            provider=self.name,
            message=None,
            results=[
                SearchResult(
                    title=f"r{i}-{query}",
                    url=f"https://x.example/{query}/{i}",
                    snippet=f"snippet {i}",
                )
                for i in range(min(limit, 3))
            ],
        )


def test_cache_miss_calls_inner_and_persists(db_engine):
    inner = _CountingProvider()
    cached = CachedSearchProvider(inner, ttl_days=7)

    r = cached.search("paige", limit=4)
    assert inner.calls == 1
    assert len(r.results) == 3

    # Row persisted
    from app.models import SearchResultCache
    from app.db import SessionLocal
    with SessionLocal() as db:
        assert db.query(SearchResultCache).count() == 1


def test_cache_hit_skips_inner(db_engine):
    inner = _CountingProvider()
    cached = CachedSearchProvider(inner, ttl_days=7)

    cached.search("paige", limit=4)
    cached.search("paige", limit=4)
    cached.search("paige", limit=4)
    assert inner.calls == 1, "expected 1 inner call after 3 hits"


def test_different_limit_is_separate_cache_key(db_engine):
    """The same query at limit=4 vs limit=8 must NOT collapse — limit=8
    asks for more results than limit=4 has cached, so silently truncating
    would drop hits.
    """
    inner = _CountingProvider()
    cached = CachedSearchProvider(inner, ttl_days=7)

    cached.search("paige", limit=4)
    cached.search("paige", limit=8)
    assert inner.calls == 2


def test_stale_row_refetched(db_engine):
    inner = _CountingProvider()
    cached = CachedSearchProvider(inner, ttl_days=7)

    cached.search("paige", limit=4)
    assert inner.calls == 1

    # Backdate the row past the TTL
    from app.models import SearchResultCache
    from app.db import SessionLocal
    with SessionLocal() as db:
        row = db.query(SearchResultCache).first()
        row.cached_at = datetime.utcnow() - timedelta(days=8)
        db.commit()

    cached.search("paige", limit=4)
    assert inner.calls == 2

    # And the row was updated in place, not duplicated
    with SessionLocal() as db:
        assert db.query(SearchResultCache).count() == 1


def test_cache_bypass_env_var(db_engine, monkeypatch):
    monkeypatch.setenv("SEARCH_CACHE_DISABLED", "1")
    inner = _CountingProvider()
    cached = CachedSearchProvider(inner, ttl_days=7)

    cached.search("paige", limit=4)
    cached.search("paige", limit=4)
    cached.search("paige", limit=4)
    assert inner.calls == 3, "cache should be fully bypassed when disabled"


def test_does_not_cache_transient_error(db_engine):
    """When the inner provider returns an error message AND empty results
    (e.g. "all keys exhausted"), we must NOT cache it — otherwise we'd
    serve the error for a week after the keys refill.
    """
    class FlakyProvider:
        name = "flaky"
        def __init__(self):
            self.calls = 0
        def search(self, query, limit=10):
            self.calls += 1
            if self.calls == 1:
                return SearchResponse(
                    provider=self.name, message="all keys exhausted", results=[],
                )
            return SearchResponse(
                provider=self.name, message=None,
                results=[SearchResult(title="x", url="https://e.example/x")],
            )

    inner = FlakyProvider()
    cached = CachedSearchProvider(inner, ttl_days=7)

    r1 = cached.search("paige", limit=4)
    assert r1.message and not r1.results

    # Second call: cache should NOT serve the error — must call inner again
    r2 = cached.search("paige", limit=4)
    assert inner.calls == 2
    assert r2.message is None
    assert len(r2.results) == 1


def test_empty_results_with_no_message_are_cached(db_engine):
    """A legit "nothing found" answer (empty results, no error message)
    IS cached — no point in repeating a query that returns nothing.
    """
    class EmptyButOkProvider:
        name = "empty"
        def __init__(self):
            self.calls = 0
        def search(self, query, limit=10):
            self.calls += 1
            return SearchResponse(provider=self.name, message=None, results=[])

    inner = EmptyButOkProvider()
    cached = CachedSearchProvider(inner, ttl_days=7)

    cached.search("paige", limit=4)
    cached.search("paige", limit=4)
    assert inner.calls == 1


def test_name_includes_inner_name(db_engine):
    inner = _CountingProvider()
    cached = CachedSearchProvider(inner, ttl_days=7)
    assert cached.name == "cached:stub"
