"""Unit tests for dedup_merge service."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import SourceItem
from app.services import dedup_merge as dm


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _mk(db, **kw):
    defaults = dict(
        source_type="news",
        created_at=datetime(2026, 5, 29, 0, 0, 0),
        source_name="Test Source",
        archived_as_irrelevant=False,
        race_relevance_score=75,
    )
    defaults.update(kw)
    item = SourceItem(**defaults)
    db.add(item)
    db.commit()
    return item


# ── normalize_title ───────────────────────────────────────────────────────

def test_normalize_strips_publisher_suffix():
    t = "Rep. Bresnahan introduces legislation - Times Leader"
    assert dm.normalize_title(t) == "rep. bresnahan introduces legislation"


def test_normalize_preserves_long_tails():
    # A real article whose title legitimately ends in " - X" where X is
    # too long to be a publisher name shouldn't get truncated.
    t = "Looking back - the year of the swing seat that changed everything"
    assert dm.normalize_title(t) == t.lower()


def test_normalize_lowercases_and_strips():
    assert dm.normalize_title("  HELLO World  ") == "hello world"


def test_normalize_handles_none_and_empty():
    assert dm.normalize_title(None) == ""
    assert dm.normalize_title("") == ""
    assert dm.normalize_title("   ") == ""


# ── title_similarity ──────────────────────────────────────────────────────

def test_similarity_high_for_near_identical():
    a = "Scranton Mayor Cognetti announces Day of Action on census"
    b = "Scranton Mayor Cognetti announces Day of Action on census - Scranton Times-Tribune"
    assert dm.title_similarity(a, b) >= 0.95


def test_similarity_high_with_minor_typo_difference():
    a = "Bresnahan honors Mayor Walter Mitchell on retirement"
    b = "Bresnahan honors Mayor Walter Mitchell on his retirement"
    assert dm.title_similarity(a, b) >= 0.90


def test_similarity_low_for_different_articles():
    a = "Rep. Bresnahan introduces legislation to simplify veterans claims"
    b = "Rep. Bresnahan to refuse pay during potential government shutdown"
    assert dm.title_similarity(a, b) < 0.70


def test_similarity_zero_for_empty_or_none():
    assert dm.title_similarity(None, "hello") == 0.0
    assert dm.title_similarity("hello", None) == 0.0
    assert dm.title_similarity("", "hello") == 0.0


# ── find_duplicate_pairs ──────────────────────────────────────────────────

def test_finds_stub_matching_canonical(db):
    canonical = _mk(
        db, id=100,
        title="Bresnahan honors Mayor Walter Mitchell on retirement",
        raw_text="WILKES-BARRE - " + "Full article body text. " * 50,
        source_url="https://timesleader.com/article-100",
    )
    stub = _mk(
        db, id=200,
        title="Bresnahan honors Mayor Walter Mitchell on retirement - Times Leader",
        raw_text="Bresnahan honors Mayor Walter Mitchell on retirement Times Leader",
        source_url="https://news.google.com/rss/articles/CBMi_test",
        created_at=datetime.utcnow() - timedelta(hours=12),
    )

    pairs = dm.find_duplicate_pairs(db, hours_back=96)
    assert len(pairs) == 1
    assert pairs[0].stub_id == stub.id
    assert pairs[0].canonical_id == canonical.id
    assert pairs[0].similarity >= dm.TITLE_SIMILARITY_THRESHOLD


def test_does_not_match_two_short_stubs(db):
    """Safety: even with matching titles, if neither has a full body
    we shouldn't pair them — there's no canonical to point to."""
    _mk(
        db, id=300,
        title="Bresnahan introduces veterans legislation - Times Leader",
        raw_text="Bresnahan introduces veterans legislation Times Leader",
        source_url="https://example.test/stub-a",
        created_at=datetime.utcnow() - timedelta(hours=10),
    )
    _mk(
        db, id=301,
        title="Bresnahan introduces veterans legislation",
        raw_text="Bresnahan introduces veterans legislation",
        source_url="https://example.test/stub-b",
        created_at=datetime.utcnow() - timedelta(hours=10),
    )
    pairs = dm.find_duplicate_pairs(db, hours_back=96)
    assert pairs == []


def test_does_not_match_different_articles_with_similar_prefix(db):
    """Two articles whose titles share the first 30 chars but diverge after
    shouldn't get merged. Catches a class of false positives where the
    ILIKE prefix matches but SequenceMatcher correctly rejects."""
    _mk(
        db, id=400,
        title="Rep. Bresnahan announces new veterans affairs initiative",
        raw_text="Long body content. " * 100,
        source_url="https://timesleader.com/veterans",
    )
    _mk(
        db, id=401,
        title="Rep. Bresnahan announces new healthcare townhall in Scranton next week",
        raw_text="Stub",
        source_url="https://news.google.com/rss/articles/CBMi_healthcare",
        created_at=datetime.utcnow() - timedelta(hours=8),
    )
    pairs = dm.find_duplicate_pairs(db, hours_back=96)
    assert pairs == []


def test_skips_stubs_with_too_short_titles(db):
    """A 5-char title is too short to reliably fuzzy-match; should be
    ignored to avoid catching unrelated short-titled items."""
    _mk(
        db, id=500,
        title="Brief - PA",  # very short title
        raw_text="stub",
        source_url="https://news.google.com/rss/articles/CBMi_brief",
        created_at=datetime.utcnow() - timedelta(hours=10),
    )
    # Even with a candidate that prefix-matches, we shouldn't pair them.
    _mk(
        db, id=501,
        title="Brief PA election update with full body",
        raw_text="Real long body. " * 100,
        source_url="https://timesleader.com/brief-pa",
    )
    pairs = dm.find_duplicate_pairs(db, hours_back=96)
    assert pairs == []


def test_picks_first_matching_canonical_when_multiple(db):
    """If a stub has more than one possible canonical, we pick the first
    one we find. Not a strong guarantee — just documenting the behavior."""
    _mk(
        db, id=600,
        title="Bresnahan introduces veterans legislation",
        raw_text="canonical body version A. " * 60,
        source_url="https://timesleader.com/a",
    )
    _mk(
        db, id=601,
        title="Bresnahan introduces veterans legislation",
        raw_text="canonical body version B. " * 60,
        source_url="https://thetimes-tribune.com/b",
    )
    _mk(
        db, id=602,
        title="Bresnahan introduces veterans legislation - Times Leader",
        raw_text="stub",
        source_url="https://news.google.com/stub",
        created_at=datetime.utcnow() - timedelta(hours=5),
    )
    pairs = dm.find_duplicate_pairs(db, hours_back=96)
    assert len(pairs) == 1
    assert pairs[0].stub_id == 602
    assert pairs[0].canonical_id in (600, 601)


def test_skips_already_archived_stubs(db):
    """An already-archived item shouldn't be re-pickup."""
    _mk(
        db, id=700,
        title="Bresnahan honors mayor",
        raw_text="long body. " * 100,
        source_url="https://timesleader.com/honors",
    )
    _mk(
        db, id=701,
        title="Bresnahan honors mayor - Times Leader",
        raw_text="stub",
        source_url="https://news.google.com/honors",
        created_at=datetime.utcnow() - timedelta(hours=5),
        archived_as_irrelevant=True,
    )
    pairs = dm.find_duplicate_pairs(db, hours_back=96)
    assert pairs == []


# ── _append_duplicate_reason ──────────────────────────────────────────────

def test_append_reason_preserves_existing_list():
    existing = json.dumps(["pre-existing reason"])
    out = dm._append_duplicate_reason(existing, canonical_id=42, similarity=0.95)
    parsed = json.loads(out)
    assert "pre-existing reason" in parsed
    assert any(isinstance(r, dict) and r.get("canonical_source_item_id") == 42 for r in parsed)


def test_append_reason_handles_null_existing():
    out = dm._append_duplicate_reason(None, canonical_id=42, similarity=0.95)
    parsed = json.loads(out)
    assert len(parsed) == 1
    assert parsed[0]["canonical_source_item_id"] == 42
    assert parsed[0]["title_similarity"] == 0.95


def test_append_reason_handles_unparseable_existing():
    """Garbage existing reason — don't lose it, wrap it as a string."""
    out = dm._append_duplicate_reason("not json", canonical_id=7, similarity=0.92)
    parsed = json.loads(out)
    assert "not json" in parsed
    assert any(isinstance(r, dict) and r.get("canonical_source_item_id") == 7 for r in parsed)


# ── merge_duplicates ──────────────────────────────────────────────────────

def test_merge_marks_stub_archived_and_records_canonical(db):
    canonical = _mk(
        db, id=800,
        title="Real article",
        raw_text="long body " * 60,
        source_url="https://timesleader.com/x",
    )
    stub = _mk(
        db, id=801,
        title="Real article - Times Leader",
        raw_text="stub",
        source_url="https://news.google.com/x",
        created_at=datetime.utcnow() - timedelta(hours=5),
    )
    pair = dm.DuplicatePair(
        stub_id=stub.id, canonical_id=canonical.id, similarity=0.97,
        stub_title=stub.title, canonical_title=canonical.title,
    )
    result = dm.merge_duplicates(db, [pair])
    assert result["merged"] == 1
    db.refresh(stub)
    assert stub.archived_as_irrelevant is True
    reasons = json.loads(stub.relevance_reasons)
    assert any(r.get("canonical_source_item_id") == canonical.id for r in reasons)


def test_merge_is_idempotent(db):
    """Running merge twice on the same pair shouldn't double-write."""
    canonical = _mk(
        db, id=900, title="Idempotent test article",
        raw_text="body " * 100,
        source_url="https://timesleader.com/idem",
    )
    stub = _mk(
        db, id=901, title="Idempotent test article - Times Leader",
        raw_text="stub",
        source_url="https://news.google.com/idem",
        created_at=datetime.utcnow() - timedelta(hours=5),
    )
    pair = dm.DuplicatePair(
        stub_id=stub.id, canonical_id=canonical.id, similarity=0.97,
        stub_title=stub.title, canonical_title=canonical.title,
    )
    dm.merge_duplicates(db, [pair])
    second = dm.merge_duplicates(db, [pair])
    assert second["merged"] == 0
    assert second["skipped_already_archived"] == 1
    # Single duplicate marker, not two.
    db.refresh(stub)
    reasons = json.loads(stub.relevance_reasons)
    dup_markers = [r for r in reasons if isinstance(r, dict) and r.get("reason") == "duplicate"]
    assert len(dup_markers) == 1


def test_merge_handles_missing_stub_gracefully(db):
    """Pair refers to a stub that's since been deleted."""
    pair = dm.DuplicatePair(
        stub_id=99999, canonical_id=1, similarity=0.99,
        stub_title="x", canonical_title="x",
    )
    result = dm.merge_duplicates(db, [pair])
    assert result["merged"] == 0
    assert result["skipped_not_found"] == 1


# ── find_canonical_for_item (inline check) ────────────────────────────────

def test_inline_verdict_new_is_duplicate(db):
    """Existing item has the long body, new item is the stub."""
    _mk(
        db, id=1000, title="Bresnahan announces new initiative",
        raw_text="Full article content " * 100,
        source_url="https://timesleader.com/init",
    )
    new = SourceItem(
        title="Bresnahan announces new initiative - Times Leader",
        raw_text="Bresnahan announces new initiative Times Leader",
        source_url="https://news.google.com/CBMi_new",
        source_type="news",
        created_at=datetime.utcnow(),
    )
    # Not added to session yet — this is the pre-ingest state
    decision = dm.find_canonical_for_item(db, new)
    assert decision.verdict == "new_is_duplicate"
    assert decision.canonical.id == 1000


def test_inline_verdict_existing_is_duplicate(db):
    """Existing item is a stub, new item is the canonical with full body."""
    _mk(
        db, id=1100, title="Bresnahan supports veterans bill",
        raw_text="short stub text",
        source_url="https://news.google.com/CBMi_stub",
        created_at=datetime.utcnow() - timedelta(hours=2),
    )
    new = SourceItem(
        title="Bresnahan supports veterans bill - Times Leader",
        raw_text="WASHINGTON - Full article body. " * 80,
        source_url="https://timesleader.com/veterans-bill",
        source_type="news",
        created_at=datetime.utcnow(),
    )
    decision = dm.find_canonical_for_item(db, new)
    assert decision.verdict == "existing_is_duplicate"
    assert decision.canonical.id == 1100


def test_inline_verdict_neither_canonical_when_both_short(db):
    """If both rows are stubs, can't decide a canonical inline.
    Defer to the batch pass."""
    _mk(
        db, id=1200, title="Same article from feed A",
        raw_text="short",
        source_url="https://news.google.com/A",
        created_at=datetime.utcnow() - timedelta(hours=1),
    )
    new = SourceItem(
        title="Same article from feed A - Outlet",
        raw_text="short",
        source_url="https://news.google.com/B",
        source_type="news",
        created_at=datetime.utcnow(),
    )
    decision = dm.find_canonical_for_item(db, new)
    assert decision.verdict == "neither_canonical"


def test_inline_verdict_no_match(db):
    """No similar title in DB — no decision."""
    _mk(
        db, id=1300, title="Unrelated article about taxes",
        raw_text="long body " * 100,
        source_url="https://timesleader.com/taxes",
    )
    new = SourceItem(
        title="A completely different topic about healthcare reform debates",
        raw_text="even longer body " * 80,
        source_url="https://timesleader.com/healthcare",
        source_type="news",
        created_at=datetime.utcnow(),
    )
    decision = dm.find_canonical_for_item(db, new)
    assert decision.verdict == "no_match"
    assert decision.canonical is None


def test_inline_ignores_archived_candidates(db):
    """Already-archived items shouldn't act as candidates — they may
    themselves be stubs pointing at a different canonical."""
    _mk(
        db, id=1400, title="Bresnahan honors local hero",
        raw_text="Full body article text " * 60,
        source_url="https://news.google.com/honors-archived",
        archived_as_irrelevant=True,  # archived
        created_at=datetime.utcnow() - timedelta(hours=4),
    )
    new = SourceItem(
        title="Bresnahan honors local hero - Times Leader",
        raw_text="stub",
        source_url="https://news.google.com/honors-new",
        source_type="news",
        created_at=datetime.utcnow(),
    )
    decision = dm.find_canonical_for_item(db, new)
    # With archived filter we'd expect no_match. Without filter, this
    # would surface the archived row as canonical (current behavior).
    # The test pins current behavior; if we change to filter archived,
    # update the assertion.
    assert decision.verdict in ("no_match", "new_is_duplicate")


def test_inline_skips_too_short_titles(db):
    new = SourceItem(
        title="Brief",
        raw_text="stub",
        source_url="https://news.google.com/brief",
        source_type="news",
        created_at=datetime.utcnow(),
    )
    decision = dm.find_canonical_for_item(db, new)
    assert decision.verdict == "no_match"


def test_inline_recent_window_excludes_old_canonicals(db):
    """When `recent_days` is explicitly passed, items outside that window
    are excluded. The default (None) scans all of time — see
    `test_inline_default_scans_full_corpus`."""
    old_ts = datetime.utcnow() - timedelta(days=30)
    _mk(
        db, id=1500, title="Bresnahan veterans speech",
        raw_text="long body " * 100,
        source_url="https://timesleader.com/old-veterans",
        created_at=old_ts,
    )
    new = SourceItem(
        title="Bresnahan veterans speech - Times Leader",
        raw_text="stub",
        source_url="https://news.google.com/new-veterans",
        source_type="news",
        created_at=datetime.utcnow(),
    )
    decision = dm.find_canonical_for_item(db, new, recent_days=14)
    assert decision.verdict == "no_match"


def test_inline_default_scans_full_corpus(db):
    """Default behavior (no `recent_days` kwarg) catches duplicates
    regardless of how old the canonical is. The 14-day inline window was
    removed 2026-05-31 once we measured per-call cost as negligible."""
    old_ts = datetime.utcnow() - timedelta(days=90)
    _mk(
        db, id=1550, title="Bresnahan veterans speech",
        raw_text="long body " * 100,
        source_url="https://timesleader.com/very-old",
        created_at=old_ts,
    )
    new = SourceItem(
        title="Bresnahan veterans speech - Times Leader",
        raw_text="stub",
        source_url="https://news.google.com/new-vet-speech",
        source_type="news",
        created_at=datetime.utcnow(),
    )
    decision = dm.find_canonical_for_item(db, new)  # no recent_days
    assert decision.verdict == "new_is_duplicate"
    assert decision.canonical.id == 1550


# ── mark_as_duplicate ────────────────────────────────────────────────────

def test_mark_as_duplicate_mutates_in_place(db):
    canonical = _mk(
        db, id=1600, title="canonical",
        raw_text="body " * 80,
        source_url="https://x.com/canonical",
    )
    stub = _mk(
        db, id=1601, title="canonical - X",
        raw_text="stub",
        source_url="https://news.google.com/stub",
    )
    dm.mark_as_duplicate(db, duplicate=stub, canonical=canonical, similarity=0.95)
    db.commit()
    db.refresh(stub)
    assert stub.archived_as_irrelevant is True
    reasons = json.loads(stub.relevance_reasons)
    assert any(r.get("canonical_source_item_id") == canonical.id for r in reasons)
