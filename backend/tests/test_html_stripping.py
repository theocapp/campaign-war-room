"""Regression tests for the Phase 0 HTML / entity leakage bugs.

- Bug 1: build_source_summary used to leak `<a href="...">...</a>` markup into
  user-facing summaries.
- Bug 2: opponent activity quotes used to retain `&#x2019;` and similar
  entities instead of decoding them to apostrophes / quotes.
"""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CampaignConfig, Opponent, OpponentActivity, SourceItem
from app.services.opponent_analysis import _extract_activities
from app.services.snapshots import build_source_summary
from app.services.text_utils import strip_html_to_text


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


# ── strip_html_to_text ────────────────────────────────────────────────────────


def test_strip_html_to_text_removes_anchor_tags():
    text = 'Cognetti said <a href="https://example.com">the bill is misleading</a>.'
    assert strip_html_to_text(text) == "Cognetti said the bill is misleading ."


def test_strip_html_to_text_decodes_hex_entities():
    # &#x2019; is the right single quote, &#8217; is its decimal equivalent
    text = "Bresnahan&#x2019;s response was &#8220;dismissive&#8221;."
    out = strip_html_to_text(text)
    assert "&#x2019;" not in out
    assert "&#8220;" not in out
    assert "’" in out  # right single quote present
    assert "“" in out  # left double quote present


def test_strip_html_to_text_decodes_named_entities():
    assert strip_html_to_text("AT&amp;T &mdash; the company") == "AT&T — the company"


def test_strip_html_to_text_collapses_whitespace_and_handles_none():
    assert strip_html_to_text(None) == ""
    assert strip_html_to_text("") == ""
    assert strip_html_to_text("  hello\n\n  world  ") == "hello world"


def test_strip_html_to_text_safe_on_clean_text():
    # No tags, no entities — should be a no-op (modulo whitespace).
    assert strip_html_to_text("Plain sentence about the race.") == "Plain sentence about the race."


# ── build_source_summary (bug 1) ──────────────────────────────────────────────


def _summary_item(summary: str) -> SourceItem:
    return SourceItem(
        title="A Headline",
        raw_text="raw text",
        summary=summary,
        source_name="Example",
        source_type="news",
        published_at=datetime.utcnow(),
        race_relevance_score=60,
        archived_as_irrelevant=False,
        extraction_quality_label="good",
        extraction_quality_score=80,
    )


def test_build_source_summary_strips_anchor_tags():
    item = _summary_item('Read more <a href="https://x.com/post/1">on X</a> about the rally.')
    out = build_source_summary(item)
    assert "<a" not in out
    assert "href" not in out
    assert "on X" in out
    assert "rally" in out


def test_build_source_summary_decodes_entities():
    item = _summary_item("Bresnahan&#x2019;s union &amp; labor endorsement.")
    out = build_source_summary(item)
    assert "&#x2019;" not in out
    assert "&amp;" not in out
    assert "’" in out
    assert " & " in out  # ampersand entity decoded to a literal &


# ── opponent activity entity decoding (bug 2) ─────────────────────────────────


def test_opponent_activity_quote_decodes_entities(db):
    """Sentences that include `&#x2019;` should land in storage with the
    actual character, not the raw entity."""
    db.add(CampaignConfig(candidate_name="Paige Cognetti", district="PA-08"))
    opponent = Opponent(name="Rob Bresnahan")
    db.add(opponent)
    db.commit()

    # Sentence where the opponent is the actor, contains both an entity and an anchor tag.
    full_text = (
        "Local news roundup. "
        "Bresnahan claims the district&#x2019;s priorities are "
        '<a href="https://example.com">badly misrepresented</a>.'
    )
    activities = _extract_activities(full_text, opponent.name, "Paige Cognetti", llm=None)
    assert activities, "expected at least one classified sentence"
    text_fields = [a.get("attack") or a.get("claim") or a.get("promise") or "" for a in activities]
    joined = " ".join(text_fields)
    assert "&#x2019;" not in joined
    assert "<a" not in joined and "href" not in joined
    assert "’" in joined
