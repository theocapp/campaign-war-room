"""
Tests for the cross-provider cache-isolation invariant in
services/embeddings.py.

Background
----------
Gemini and OpenAI produce embedding vectors in different semantic spaces.
Even when configured to the same dimension, cosine similarity between a
Gemini vector and an OpenAI vector is essentially noise. Mixing them in a
single cache poisons downstream similarity comparisons.

The fix (added 2026-05-24): cache key includes the provider name. Each
embed_texts() call picks a SINGLE provider and never mixes within a call.

These tests pin the invariant so a future "refactor" that drops the
provider tag breaks them loudly.
"""
import pytest

from app.services.embeddings import (
    _PROVIDER_GEMINI,
    _PROVIDER_OPENAI,
    _cache_get,
    _cache_put,
    _cache_key,
    clear_cache,
)


@pytest.fixture(autouse=True)
def _clean_cache_between_tests():
    """Ensure a fresh cache for each test — keep tests isolated."""
    clear_cache()
    yield
    clear_cache()


# ─────────────────────────────────────────────────────────────────────────
# Cache-key invariant: provider is part of the key.
# ─────────────────────────────────────────────────────────────────────────

def test_cache_key_includes_provider():
    """The key tuple must be 3-element (hash, task_type, provider).
    Anything else and the isolation invariant breaks."""
    key_gemini = _cache_key("hello", "SEMANTIC_SIMILARITY", _PROVIDER_GEMINI)
    key_openai = _cache_key("hello", "SEMANTIC_SIMILARITY", _PROVIDER_OPENAI)
    assert key_gemini != key_openai, (
        "Same text + task_type but different provider must produce DIFFERENT keys"
    )
    assert len(key_gemini) == 3
    assert len(key_openai) == 3


def test_gemini_and_openai_vectors_stored_separately():
    """If both providers happen to embed the same text, both vectors must
    be retrievable independently. Neither should overwrite the other."""
    fake_gemini_vec = [0.1] * 3072
    fake_openai_vec = [0.9] * 3072

    _cache_put("test text", "SEMANTIC_SIMILARITY", _PROVIDER_GEMINI, fake_gemini_vec)
    _cache_put("test text", "SEMANTIC_SIMILARITY", _PROVIDER_OPENAI, fake_openai_vec)

    got_gemini = _cache_get("test text", "SEMANTIC_SIMILARITY", _PROVIDER_GEMINI)
    got_openai = _cache_get("test text", "SEMANTIC_SIMILARITY", _PROVIDER_OPENAI)

    assert got_gemini == fake_gemini_vec
    assert got_openai == fake_openai_vec
    assert got_gemini != got_openai


def test_cache_miss_when_only_other_provider_cached():
    """The smoking-gun case: text X has a Gemini vector cached, caller asks
    for OpenAI vector — should be a MISS, not return the Gemini vector
    masquerading as OpenAI's."""
    _cache_put("Bresnahan cuts funding", "SEMANTIC_SIMILARITY", _PROVIDER_GEMINI, [0.5] * 3072)

    got = _cache_get("Bresnahan cuts funding", "SEMANTIC_SIMILARITY", _PROVIDER_OPENAI)
    assert got is None, (
        "OpenAI-scoped lookup must NOT return a Gemini-cached vector — this is "
        "the bug that caused live API count=4 vs fresh-process count=19. If this "
        "assertion fails, the cache isolation is broken again."
    )


def test_clear_cache_clears_both_providers():
    """clear_cache must empty everything, including provider-tagged rows."""
    _cache_put("a", "SEMANTIC_SIMILARITY", _PROVIDER_GEMINI, [1.0] * 3072)
    _cache_put("a", "SEMANTIC_SIMILARITY", _PROVIDER_OPENAI, [2.0] * 3072)
    _cache_put("b", "SEMANTIC_SIMILARITY", _PROVIDER_GEMINI, [3.0] * 3072)

    n_cleared = clear_cache()
    assert n_cleared == 3

    assert _cache_get("a", "SEMANTIC_SIMILARITY", _PROVIDER_GEMINI) is None
    assert _cache_get("a", "SEMANTIC_SIMILARITY", _PROVIDER_OPENAI) is None
    assert _cache_get("b", "SEMANTIC_SIMILARITY", _PROVIDER_GEMINI) is None


def test_task_type_still_separates_keys():
    """Provider tag is ADDITIONAL to task_type, not a replacement.
    SEMANTIC_SIMILARITY and RETRIEVAL_DOCUMENT vectors must stay separate too."""
    _cache_put("a", "SEMANTIC_SIMILARITY", _PROVIDER_GEMINI, [1.0] * 3072)
    _cache_put("a", "RETRIEVAL_DOCUMENT",  _PROVIDER_GEMINI, [2.0] * 3072)

    sem = _cache_get("a", "SEMANTIC_SIMILARITY", _PROVIDER_GEMINI)
    ret = _cache_get("a", "RETRIEVAL_DOCUMENT",  _PROVIDER_GEMINI)

    assert sem == [1.0] * 3072
    assert ret == [2.0] * 3072


def test_known_provider_constants_are_strings():
    """Defensive — provider tag must be a string constant we control,
    not an enum value or object that could compare unexpectedly."""
    assert isinstance(_PROVIDER_GEMINI, str)
    assert isinstance(_PROVIDER_OPENAI, str)
    assert _PROVIDER_GEMINI != _PROVIDER_OPENAI
    # Sanity: the constants themselves are meaningful
    assert "gemini" in _PROVIDER_GEMINI.lower()
    assert "openai" in _PROVIDER_OPENAI.lower()
