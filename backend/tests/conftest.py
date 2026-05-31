"""Session-wide pytest fixtures.

The most important one is `_mock_llm_provider_by_default` (autouse): it
replaces `llm_provider.get_ingestion_provider` with a `MockLLMProvider`
factory for the duration of the test session. Without this, any test that
exercises ingestion code (which calls `analyze_with_frames` → real OpenAI
client) would hang on socket timeouts when no API credentials are set.

`analyze_with_frames` already short-circuits to `_fallback_result()` when
it detects a MockLLMProvider, and `_create_and_analyze` then applies the
keyword-based `race_relevance.apply_relevance` as a stopgap. Tests that
exercise ingestion get deterministic keyword-based scoring instead of
hanging on a network call.

Tests that want real LLM behavior (or a specific stub response) can
override by patching `llm_provider.get_ingestion_provider` themselves —
`monkeypatch.setattr` later in a test wins over this autouse fixture.

This file lives at `backend/tests/conftest.py` so pytest auto-loads it
for every test in the `tests/` directory.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _mock_llm_provider_by_default(monkeypatch):
    """Default every test to a MockLLMProvider to prevent accidental
    network calls. Individual tests can override by re-patching after
    this fixture runs."""
    from app.services import llm_provider

    def _factory():
        return llm_provider.MockLLMProvider()

    # Patch every entry point that could resolve to a real provider.
    # `get_ingestion_provider` is the one ingestion uses; the others are
    # patched for completeness so judge/other code paths also stay offline.
    for name in (
        "get_ingestion_provider",
        "get_provider",
        "get_judge_provider",
    ):
        if hasattr(llm_provider, name):
            monkeypatch.setattr(llm_provider, name, _factory, raising=False)

    yield


@pytest.fixture(autouse=True)
def _offline_rss_fetch_by_default(monkeypatch):
    """Default every test to an offline RSS feed fetch.

    `ingest_rss` fetches the feed body via `_fetch_rss_content` (httpx.get),
    then runs inline body recovery on every entry — and `recover_body`
    makes its own live httpx.get/post calls to news.google.com to decode
    redirect URLs.

    The setup-path tests (campaign initialize/update, auto_setup_monitors,
    race selection) trigger this whole chain as a *side effect* of
    generating Google News RSS monitors and ingesting them immediately.
    They assert on monitor counts / election dates / search-path ingestion
    and do NOT care about RSS-fetched content, but the live Google News
    decode call hangs the suite — it blocks in a C-level SSL recv() that
    pytest's default thread-timeout can't interrupt.

    Returning None here exercises the real, already-handled "feed fetch
    failed → skip this feed" branch (ingestion.ingest_rss): no entries are
    processed, so `recover_body` is never reached. That closes BOTH network
    seams (feed fetch + per-entry decode) at a single choke point, while the
    function under test still runs end to end — the RSS path just
    contributes 0 items. We deliberately do NOT patch `recover_body` itself,
    so test_article_body_recovery.py keeps exercising the real recovery
    logic against its own HTTP mocks.

    Tests that need a populated feed can re-patch `_fetch_rss_content` after
    this fixture runs (monkeypatch override wins), same as the LLM fixture.
    """
    from app.services import ingestion

    monkeypatch.setattr(ingestion, "_fetch_rss_content", lambda *a, **k: None)

    yield


@pytest.fixture(autouse=True)
def _offline_search_provider_by_default(monkeypatch):
    """Default every test to the mock search provider.

    `get_search_provider()` reads SEARCH_PROVIDER from the environment /
    .env (via load_dotenv override=False). On a dev box where .env sets
    SEARCH_PROVIDER=tavily, any test that exercises a code path calling
    get_search_provider() WITHOUT first setting the env var hits the live
    Tavily API — a real network call that burns free-tier quota and adds
    seconds of latency. Observed in test_custom_campaign_update_still_works
    (~22s) and any other setup/monitor path that omits the env override.

    Forcing SEARCH_PROVIDER=mock here makes the offline default explicit.
    Tests that already do `monkeypatch.setenv("SEARCH_PROVIDER", "mock")`
    are unaffected (same value); a test that genuinely wants the Tavily
    selection path can re-set the env after this fixture runs (monkeypatch
    override wins), same as the LLM/RSS fixtures above. No test currently
    exercises the live-tavily branch, and test_search_cache.py constructs
    providers directly without reading the env, so this is collateral-free.
    """
    monkeypatch.setenv("SEARCH_PROVIDER", "mock")

    yield
