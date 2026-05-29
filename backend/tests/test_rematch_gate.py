"""Tests for the embedding-gated rematch shortlist.

The gate drops frames an article can't plausibly match before the LLM
sees them. These tests use synthetic embeddings and mock the embed call
so we can pin the shortlist behavior without hitting the network.
"""
from unittest.mock import patch
from types import SimpleNamespace

from app.services import narrative_frames as nf


def _frame(id: int, name: str, desc: str = ""):
    return SimpleNamespace(id=id, name=name, description=desc)


def _item(id: int = 1, title: str = "x", raw_text: str = "x", summary: str = ""):
    return SimpleNamespace(id=id, title=title, raw_text=raw_text, summary=summary)


def _clear_caches():
    nf._FRAME_EMBEDDING_CACHE.clear()
    nf._FRAME_THRESHOLDS_CACHE = None


def test_shortlist_keeps_frames_above_threshold(monkeypatch):
    _clear_caches()
    article_vec = [1.0, 0.0, 0.0]
    frame_vecs = [
        [1.0, 0.0, 0.0],   # cosine 1.0 → keep
        [0.0, 1.0, 0.0],   # cosine 0.0 → drop
        [0.7, 0.7, 0.0],   # cosine ~0.7 → keep
    ]
    frames = [_frame(10, "near"), _frame(11, "far"), _frame(12, "midway")]

    call_count = {"n": 0}

    def fake_embed(texts, **kwargs):
        # First call: article. Subsequent: frame batches.
        if call_count["n"] == 0:
            call_count["n"] += 1
            return [article_vec]
        # Frame embeddings — embed in the same order they were requested.
        call_count["n"] += 1
        return frame_vecs

    monkeypatch.setattr(nf, "_FRAME_THRESHOLDS_CACHE", {"10": 0.3, "11": 0.3, "12": 0.3})
    with patch("app.services.embeddings.embed_texts", side_effect=fake_embed):
        out = nf._shortlist_frames_for_article(_item(), frames)
    ids = {f.id for f in out}
    assert ids == {10, 12}, f"expected {{10, 12}}, got {ids}"


def test_shortlist_empty_when_nothing_matches(monkeypatch):
    _clear_caches()
    article_vec = [1.0, 0.0, 0.0]
    frames = [_frame(20, "a"), _frame(21, "b")]
    frame_vecs = [[0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]  # both orthogonal to article

    calls = {"n": 0}

    def fake_embed(texts, **kwargs):
        if calls["n"] == 0:
            calls["n"] += 1
            return [article_vec]
        calls["n"] += 1
        return frame_vecs

    monkeypatch.setattr(nf, "_FRAME_THRESHOLDS_CACHE", {"20": 0.5, "21": 0.5})
    with patch("app.services.embeddings.embed_texts", side_effect=fake_embed):
        out = nf._shortlist_frames_for_article(_item(), frames)
    assert out == []


def test_shortlist_falls_back_when_article_embed_fails(monkeypatch):
    _clear_caches()
    frames = [_frame(30, "a"), _frame(31, "b")]

    monkeypatch.setattr(nf, "_FRAME_THRESHOLDS_CACHE", {})
    with patch("app.services.embeddings.embed_texts", return_value=[None]):
        out = nf._shortlist_frames_for_article(_item(), frames)
    # On embed failure we return the full list — LLM will judge.
    assert {f.id for f in out} == {30, 31}


def test_shortlist_uses_per_frame_thresholds(monkeypatch):
    _clear_caches()
    article_vec = [1.0, 0.0, 0.0]
    # Same similarity (0.6) to both frames; different thresholds.
    frame_vecs = [[0.8, 0.6, 0.0], [0.8, 0.6, 0.0]]  # both ≈ same dir
    frames = [_frame(40, "a"), _frame(41, "b")]

    # Pre-populate cache so embed_texts is only called once (for the article).
    import hashlib
    for f, v in zip(frames, frame_vecs):
        h = hashlib.sha1(f"{f.name}|||{f.description or ''}".encode()).hexdigest()
        nf._FRAME_EMBEDDING_CACHE[f.id] = (h, v)

    # Frame 40 has loose threshold → keep. Frame 41 has tight threshold → drop.
    monkeypatch.setattr(nf, "_FRAME_THRESHOLDS_CACHE", {"40": 0.5, "41": 0.95})
    with patch("app.services.embeddings.embed_texts", return_value=[article_vec]):
        out = nf._shortlist_frames_for_article(_item(), frames)
    assert {f.id for f in out} == {40}


def test_shortlist_uses_global_floor_for_uncalibrated_frame(monkeypatch):
    """A newly-created frame won't be in the thresholds JSON. It should fall
    back to the global floor (0.30) rather than passing freely or dropping.
    """
    _clear_caches()
    article_vec = [1.0, 0.0, 0.0]
    frame_vec_below = [0.2, 0.98, 0.0]  # cosine ≈ 0.2, below 0.30
    frame_vec_above = [0.5, 0.87, 0.0]  # cosine ≈ 0.5, above 0.30
    frames = [_frame(99, "new1"), _frame(100, "new2")]

    import hashlib
    for f, v in zip(frames, [frame_vec_below, frame_vec_above]):
        h = hashlib.sha1(f"{f.name}|||{f.description or ''}".encode()).hexdigest()
        nf._FRAME_EMBEDDING_CACHE[f.id] = (h, v)

    # No per-frame thresholds — should use _GLOBAL_FLOOR_DEFAULT = 0.30
    monkeypatch.setattr(nf, "_FRAME_THRESHOLDS_CACHE", {})
    with patch("app.services.embeddings.embed_texts", return_value=[article_vec]):
        out = nf._shortlist_frames_for_article(_item(), frames)
    assert {f.id for f in out} == {100}


def test_article_cache_hit_skips_embed_call(monkeypatch):
    """If an article has frame_match_embedding cached with the current model,
    the gate should use it instead of calling embed_texts.
    """
    _clear_caches()
    import json as _json

    cached_vec = [1.0, 0.0, 0.0]
    item = SimpleNamespace(
        id=42, title="x", raw_text="x", summary="",
        frame_match_embedding=_json.dumps(cached_vec),
        frame_match_embedding_model="text-embedding-3-large",
    )
    frame = _frame(60, "match")
    # Pre-populate frame embedding so we don't call embed_texts for it either.
    import hashlib
    h = hashlib.sha1(f"{frame.name}|||{frame.description or ''}".encode()).hexdigest()
    nf._FRAME_EMBEDDING_CACHE[frame.id] = (h, [1.0, 0.0, 0.0])

    monkeypatch.setattr(nf, "_FRAME_THRESHOLDS_CACHE", {"60": 0.5})

    # Tell the gate the current model matches the cached one.
    monkeypatch.setattr(
        "app.services.embeddings.current_primary_model_name",
        lambda: "text-embedding-3-large",
    )

    embed_called = {"n": 0}
    def fake_embed(*args, **kwargs):
        embed_called["n"] += 1
        return [[1.0, 0.0, 0.0]]
    with patch("app.services.embeddings.embed_texts", side_effect=fake_embed):
        out = nf._shortlist_frames_for_article(item, [frame])
    assert {f.id for f in out} == {60}
    assert embed_called["n"] == 0, "embed_texts should NOT be called when cache hits"


def test_article_cache_miss_when_model_differs(monkeypatch):
    """If the article was cached with a different model, force re-embed."""
    _clear_caches()
    import json as _json

    item = SimpleNamespace(
        id=43, title="x", raw_text="x", summary="",
        frame_match_embedding=_json.dumps([0.0, 1.0, 0.0]),  # stale
        frame_match_embedding_model="old-model",
    )
    frame = _frame(70, "match")
    import hashlib
    h = hashlib.sha1(f"{frame.name}|||{frame.description or ''}".encode()).hexdigest()
    nf._FRAME_EMBEDDING_CACHE[frame.id] = (h, [1.0, 0.0, 0.0])

    monkeypatch.setattr(nf, "_FRAME_THRESHOLDS_CACHE", {"70": 0.5})
    monkeypatch.setattr(
        "app.services.embeddings.current_primary_model_name",
        lambda: "text-embedding-3-large",
    )

    embed_called = {"n": 0}
    def fake_embed(texts, **kwargs):
        embed_called["n"] += 1
        return [[1.0, 0.0, 0.0]]  # fresh re-embed
    with patch("app.services.embeddings.embed_texts", side_effect=fake_embed):
        out = nf._shortlist_frames_for_article(item, [frame])
    assert {f.id for f in out} == {70}
    assert embed_called["n"] == 1, "stale cache should trigger re-embed"
    # Cache should be updated with the new model + vector
    assert item.frame_match_embedding_model == "text-embedding-3-large"


def test_cache_invalidates_when_frame_content_changes(monkeypatch):
    """Editing a frame's name/description should invalidate its cached
    embedding — otherwise the gate uses stale vectors after edits.
    """
    _clear_caches()
    frame = _frame(50, "old name")

    embed_calls = []

    def fake_embed(texts, **kwargs):
        embed_calls.append(list(texts))
        return [[0.1, 0.2, 0.3]] * len(texts)

    with patch("app.services.embeddings.embed_texts", side_effect=fake_embed):
        nf._ensure_frame_embeddings([frame])
        nf._ensure_frame_embeddings([frame])  # second call — no new embed
        assert len(embed_calls) == 1

        # Edit the frame
        frame.name = "new name"
        nf._ensure_frame_embeddings([frame])
        assert len(embed_calls) == 2  # re-embedded because content hash changed
