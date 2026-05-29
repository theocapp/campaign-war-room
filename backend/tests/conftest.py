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
