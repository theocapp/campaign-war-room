"""Tests for app.services.article_quadrant — per-article (owner, subject)
resolution for the 4-quadrant Timeline color scheme.

Coverage:
  - highest-confidence frame supplies OWNER axis
  - SELF-AXIS WINS for partisan-owned articles when a self-promo frame
    matches alongside an attack frame (mixed posts default to self-promo)
  - pure attack post (only opponent-subject frame match) → other-axis
  - NULL subject_type filled by classifier
  - media-classifier output upgraded to owner when owner is partisan
  - no-frame fallback uses source_owner_type
  - ultimate fallback to perspective
  - default to media
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    CampaignConfig,
    NarrativeFrame,
    NarrativeFrameMention,
    Opponent,
    SourceItem,
)
from app.services.article_quadrant import quadrants_for_articles


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    # Every test needs a campaign + opponent so the subject_classifier knows
    # which name tokens to look for in frame names.
    session.add(CampaignConfig(candidate_name="Paige Cognetti", party="Democrat"))
    session.add(Opponent(name="Rob Bresnahan", party="Republican"))
    session.commit()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


# ── Helpers ────────────────────────────────────────────────────────────────

def _article(db, **overrides) -> SourceItem:
    defaults = dict(
        title="t",
        source_name="src",
        source_url="https://x.com/a",
        source_type="news",
        published_at=datetime.utcnow(),
    )
    defaults.update(overrides)
    a = SourceItem(**defaults)
    db.add(a)
    db.flush()
    return a


def _frame(db, name, owner_type, subject_type=None) -> NarrativeFrame:
    f = NarrativeFrame(
        name=name,
        owner_type=owner_type,
        subject_type=subject_type,
        active=True,
    )
    db.add(f)
    db.flush()
    return f


def _match(db, article: SourceItem, frame: NarrativeFrame, confidence: int) -> None:
    db.add(NarrativeFrameMention(
        frame_id=frame.id,
        source_item_id=article.id,
        confidence=confidence,
        matched_by="llm",
    ))
    db.flush()


# ── Cascade tests ──────────────────────────────────────────────────────────

def test_self_axis_wins_for_mixed_partisan_post(db):
    """The Pennanurses-case regression: a Cognetti tweet that leads with
    self-promotion AND attacks Bresnahan matches both a self-promo frame
    and a higher-confidence attack frame. Self-axis wins — the article is
    primarily self-promotion, so it should bucket as our_defense."""
    a = _article(db)
    f_attack = _frame(db, "Bresnahan's Stock Trades", "candidate", "opponent")
    f_self   = _frame(db, "Cognetti Healthcare Plan", "candidate", "candidate")
    _match(db, a, f_attack, confidence=90)  # higher confidence
    _match(db, a, f_self,   confidence=70)  # lower — but it IS a self match

    result = quadrants_for_articles([a], db)
    # Self-axis wins: subject defaults to owner (candidate) because at least
    # one match treats this article as self-promotion, even though the
    # highest-confidence match was an attack frame.
    assert result[a.id] == ("candidate", "candidate")


def test_pure_attack_post_routes_to_other_axis(db):
    """A Cognetti post matching ONLY an opponent-subject frame (no self
    match anywhere) is correctly bucketed as an attack → our_offense.
    Confirms self-axis-wins doesn't accidentally swallow pure attacks."""
    a = _article(db)
    f_attack = _frame(db, "Bresnahan's Stock Trades", "candidate", "opponent")
    _match(db, a, f_attack, confidence=90)

    assert quadrants_for_articles([a], db)[a.id] == ("candidate", "opponent")


def test_highest_confidence_match_anchors_owner_axis(db):
    """Owner is taken from the top match; subject is then resolved via
    the self-axis rule. Article matches a candidate-owned frame at high
    confidence and a media-owned frame at low confidence — owner is
    candidate."""
    a = _article(db)
    f_partisan = _frame(db, "Bresnahan's Stock Trades", "candidate", "opponent")
    f_media    = _frame(db, "Healthcare policy debate", "media", "media")
    _match(db, a, f_partisan, confidence=90)
    _match(db, a, f_media,    confidence=50)

    # Owner=candidate from top match. No self-axis match → subject=opponent.
    assert quadrants_for_articles([a], db)[a.id] == ("candidate", "opponent")


def test_null_subject_filled_by_classifier(db):
    """Real frames often leave subject_type NULL. Classifier picks it up
    from the frame name."""
    a = _article(db)
    # subject_type intentionally NULL; classifier should see "Bresnahan"
    # in the frame name and return "opponent".
    f = _frame(db, "Bresnahan's Stock Trades", "candidate", subject_type=None)
    _match(db, a, f, confidence=85)

    assert quadrants_for_articles([a], db)[a.id] == ("candidate", "opponent")


def test_media_classifier_falls_back_to_owner_when_owner_is_partisan(db):
    """Frame name doesn't mention either candidate ('NEPA Support') →
    classifier returns 'media'. But the frame is candidate-owned, so the
    article is self-promotion: default subject to candidate, NOT gray."""
    a = _article(db)
    f = _frame(db, "NEPA Support", "candidate", subject_type=None)
    _match(db, a, f, confidence=75)

    # owner=candidate, subject upgraded from "media" to "candidate" → our_defense
    assert quadrants_for_articles([a], db)[a.id] == ("candidate", "candidate")


def test_no_frame_match_uses_source_owner_type_candidate_statement(db):
    a = _article(db, source_owner_type="candidate_statement")
    assert quadrants_for_articles([a], db)[a.id] == ("candidate", "candidate")


def test_no_frame_match_uses_source_owner_type_opponent_statement(db):
    a = _article(db, source_owner_type="opponent_statement")
    assert quadrants_for_articles([a], db)[a.id] == ("opponent", "opponent")


def test_no_frame_match_uses_source_owner_type_media(db):
    a = _article(db, source_owner_type="media")
    assert quadrants_for_articles([a], db)[a.id] == ("media", "media")


def test_uninformative_source_owner_falls_through_to_perspective(db):
    """source_owner_type='unclear' is the default — should ignore it and
    use perspective. This is the article-4662 case (the Cognetti tweet
    that triggered the bug)."""
    a = _article(db, source_owner_type="unclear", perspective="pro_candidate")
    assert quadrants_for_articles([a], db)[a.id] == ("candidate", "candidate")


def test_perspective_pro_opponent_maps_to_opponent_pair(db):
    a = _article(db, perspective="pro_opponent")
    assert quadrants_for_articles([a], db)[a.id] == ("opponent", "opponent")


def test_perspective_neutral_maps_to_media(db):
    a = _article(db, perspective="neutral")
    assert quadrants_for_articles([a], db)[a.id] == ("media", "media")


def test_no_signal_at_all_defaults_to_media(db):
    """Bare article — no frame, no source_owner_type, no perspective."""
    a = _article(db)
    assert quadrants_for_articles([a], db)[a.id] == ("media", "media")


def test_batch_query_returns_one_row_per_article(db):
    """Caller should get a result for every input article — even those
    with no frame match."""
    a_attack = _article(db)  # has frame match
    a_bare   = _article(db)  # no signal
    f = _frame(db, "Bresnahan's Stock Trades", "candidate", "opponent")
    _match(db, a_attack, f, confidence=90)

    result = quadrants_for_articles([a_attack, a_bare], db)
    assert result[a_attack.id] == ("candidate", "opponent")
    assert result[a_bare.id]   == ("media", "media")


def test_empty_input_returns_empty_dict(db):
    assert quadrants_for_articles([], db) == {}


def test_frame_with_media_owner_stays_media(db):
    """Article matched to a media-owned frame (e.g. local-news topic
    cluster) — owner stays media, subject stays whatever classifier returns."""
    a = _article(db)
    f = _frame(db, "Local healthcare debate", "media", subject_type=None)
    _match(db, a, f, confidence=80)
    # owner=media → media-quadrant regardless of subject. Even if classifier
    # bumps subject to owner (which it does for partisan owners), here owner
    # is "media" so the bump rule doesn't apply.
    owner, subject = quadrants_for_articles([a], db)[a.id]
    assert owner == "media"
    # Subject = whatever classifier returned; here "media" since neither
    # name token appears in "Local healthcare debate".
    assert subject == "media"
