"""Unit tests for ingestion-side helpers added during the 2026-05-29
session: junk-title predicate, YouTube URL parser, transcript proper-noun
corrector, and the canonical-names lookup that drives the corrector.

These functions had only inline `python -c` smoke tests when first
written. Pulling them into the suite so future refactors catch
regressions cheaply.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CampaignConfig, Opponent
from app.services.ingestion import (
    _campaign_canonical_names,
    _correct_transcript_proper_nouns,
    _is_junk_title,
    _youtube_video_id,
)


# ── _is_junk_title ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("title", [
    "Instagram", "Facebook", "Twitter", "LinkedIn",
    "instagram", "FACEBOOK",                       # case-insensitive
    "Untitled", "Latest Articles", "chevron-right",
    "BizToc", "Targeted News Service", "Home", "Menu", "404",
    "breeze 4.jpg", "image.png", "report.pdf",
    "all pages realtime.csv", "video.mp4",
    "idahostatejournal.com",                       # bare hostname, 2 segments
    "rockymounttelegram.com",
    "sub.example.org",                             # bare hostname, 3 segments
    "", "   ", None,                               # empty / whitespace / null
])
def test_is_junk_title_flags_known_artifacts(title):
    assert _is_junk_title(title) is True


@pytest.mark.parametrize("title", [
    "Heard on the Hill",                           # legit short title
    "Rob Bresnahan",                               # candidate name as title
    "Paige for Scranton",                          # campaign slogan
    "TODAY! Runoff in Texas",                      # legit headline with punctuation
    "Pope calls for AI regulation",                # legit news
    "r/Scranton - Reddit",                         # legit subreddit reference
    "CVS Crash 2",                                 # ambiguous but not junk-pattern
    "Politics - HITS FM",                          # legit show name
    "Kyle Busch died after severe pneumonia",
])
def test_is_junk_title_passes_legitimate_titles(title):
    assert _is_junk_title(title) is False


def test_is_junk_title_bare_hostname_must_have_dot():
    # Single-segment "hostname" without a dot is not a hostname; might be
    # a legitimate one-word title.
    assert _is_junk_title("Wirecutter") is False


# ── _youtube_video_id ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/watch?v=jNQXAC9IVRw", "jNQXAC9IVRw"),
    ("https://youtu.be/jNQXAC9IVRw", "jNQXAC9IVRw"),
    ("https://www.youtube.com/shorts/jNQXAC9IVRw", "jNQXAC9IVRw"),
    ("https://www.youtube.com/embed/jNQXAC9IVRw", "jNQXAC9IVRw"),
    ("https://m.youtube.com/v/jNQXAC9IVRw", "jNQXAC9IVRw"),
    # Channel URLs have no video id
    ("https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxx", None),
    # Non-YouTube
    ("https://www.cnn.com/article", None),
    ("https://example.com/watch?v=jNQXAC9IVRw", None),
    # Empty / null
    ("", None),
    (None, None),
])
def test_youtube_video_id_extraction(url, expected):
    assert _youtube_video_id(url) == expected


# ── canonical-names lookup ─────────────────────────────────────────────────────

@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def test_campaign_canonical_names_includes_candidate_and_opponents(db):
    db.add(CampaignConfig(candidate_name="Paige Cognetti"))
    db.add(Opponent(name="Rob Bresnahan"))
    db.add(Opponent(name="Other Candidate"))
    db.commit()

    names = _campaign_canonical_names(db)
    # First names < 4 chars (e.g. "Rob") get dropped, but multi-letter
    # names should appear.
    assert "Paige" in names
    assert "Cognetti" in names
    assert "Bresnahan" in names
    assert "Other" in names
    assert "Candidate" in names


def test_campaign_canonical_names_dedup_case_insensitive(db):
    db.add(CampaignConfig(candidate_name="John Smith"))
    db.add(Opponent(name="John Doe"))  # same first name
    db.commit()

    names = _campaign_canonical_names(db)
    lowered = [n.lower() for n in names]
    assert lowered.count("john") == 1


def test_campaign_canonical_names_drops_short_words(db):
    # "Rob" is 3 chars — below the 4-char floor. Should be dropped to avoid
    # over-matching against random words.
    db.add(CampaignConfig(candidate_name="Rob"))
    db.commit()

    names = _campaign_canonical_names(db)
    assert "Rob" not in names


def test_campaign_canonical_names_empty_db(db):
    assert _campaign_canonical_names(db) == []


# ── _correct_transcript_proper_nouns ───────────────────────────────────────────

CANONICAL = ["Paige", "Cognetti", "Bresnahan"]


@pytest.mark.parametrize("inp,expected", [
    # Single-word caption errors should get corrected to canonical form
    ("Mayor Connetty announced today", "Mayor Cognetti announced today"),
    ("connetty for congress",          "Cognetti for congress"),
    ("Representative Bresnan voted",   "Representative Bresnahan voted"),
    ("paige cognetti spoke",           "Paige Cognetti spoke"),  # case normalization
])
def test_correct_transcript_applies_known_fixes(inp, expected):
    assert _correct_transcript_proper_nouns(inp, CANONICAL) == expected


@pytest.mark.parametrize("inp", [
    # First-letter gate prevents false positives across unrelated names
    "Police arrested a man named Bryan",
    # Length-divergence cap prevents long-vs-short collisions
    "Connecticut had a primary today",
    # Below ratio threshold — too dissimilar
    "congress passed a bill",
    "something cosmetic",
    "Robert took the train",
    # Different first letter
    "wrestled with the decision",
])
def test_correct_transcript_leaves_unrelated_words_alone(inp):
    assert _correct_transcript_proper_nouns(inp, CANONICAL) == inp


def test_correct_transcript_empty_inputs():
    assert _correct_transcript_proper_nouns("", CANONICAL) == ""
    assert _correct_transcript_proper_nouns(None, CANONICAL) is None
    assert _correct_transcript_proper_nouns("anything", []) == "anything"


def test_correct_transcript_preserves_punctuation_and_spacing():
    out = _correct_transcript_proper_nouns(
        "Mayor Connetty, who was elected in 2022, said: \"thanks!\"",
        CANONICAL,
    )
    # Should fix the name but leave all the punctuation and whitespace alone
    assert "Cognetti" in out
    assert "," in out and "\"" in out and ":" in out and "!" in out


# ── Multi-word transcript fixes (pass 2 added 2026-05-29) ────────────────────

@pytest.mark.parametrize("inp,expected_word", [
    # Two-token caption splits — most common form of multi-word garbling
    ("Representative Bres nahan voted",   "Bresnahan"),
    ("Rep Bresn ahan announced",          "Bresnahan"),
    ("Mayor cog netti spoke",             "Cognetti"),
])
def test_correct_transcript_multi_word_fixes(inp, expected_word):
    out = _correct_transcript_proper_nouns(inp, CANONICAL)
    assert expected_word in out


@pytest.mark.parametrize("inp", [
    # Two consecutive short common words should NOT be substituted
    "press the button to vote",       # "press the" looks bit like Bresnahan but ratio low
    "the children laughed at me",     # nothing close to canonicals
    "congress passed a bill",
    "no one voted today",             # "no one" looks like "noon" — but we have no "Noone" canonical
])
def test_correct_transcript_multi_word_leaves_unrelated_alone(inp):
    assert _correct_transcript_proper_nouns(inp, CANONICAL) == inp


def test_correct_transcript_multi_word_skips_short_canonicals():
    """Names below 6 chars are excluded from multi-word matching — too
    risky in terms of false positives. So 'Pay G' won't be corrected
    to 'Paige' (5 chars)."""
    out = _correct_transcript_proper_nouns("Pay G announced", CANONICAL)
    assert "Paige" not in out  # Paige is 5 chars, excluded from multi-word pass
