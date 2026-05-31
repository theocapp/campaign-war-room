"""Provenance safety net: never SILENTLY archive an item that a monitor named
for a *specific* race participant pulled when its body is too thin to judge.

The failure mode (diagnosed 2026-05-31): a Google-News search monitor named for
the candidate returns an opaque redirect URL; the article body is never fetched,
so it sits < 300 chars; the LLM race-mention gate sees no race token in the thin
text and returns "irrelevant"; ingestion archives it. Genuinely-relevant local
news (a WVIA PA-08 primary piece, an NYT PA-08 polling piece) vanished this way.

The precision catch (also 2026-05-31): a BARE surname in the monitor label is
too noisy to rescue on — it collides with homonyms (the novelist Paolo Cognetti,
the ballplayer Roger Bresnahan, the voice actress Alyssa Bresnahan). So the
rescue fires only when the label names a specific participant: surname PLUS a
disambiguator (first name, district id, or a second race surname).

These tests pin that decision AND prove the rescued state actually surfaces in
the review queue — i.e. the queue's own filters + keyword gate don't silently
re-exclude what we just rescued. Everything is in-memory SQLite; no network, no
LLM, no live DB.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CampaignConfig, Opponent, SourceItem
from app.routes import review_queue
from app.services import ingestion
from app.services.relevance_gate import build_keyword_pattern, passes_gate


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    # Real campaign values + a third opponent ("Sam Ross") whose surname "ross"
    # is a substring of unrelated words — used to prove whole-word matching.
    session.add(CampaignConfig(
        candidate_name="Paige Cognetti",
        district="PA-08",
        location="Scranton/Wilkes-Barre, PA-08",
    ))
    session.add(Opponent(name="Rob Bresnahan"))
    session.add(Opponent(name="Sam Ross"))
    session.commit()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _archived_item(**overrides) -> SourceItem:
    """A SourceItem in the state ingestion leaves after the race-mention gate
    archives it: archived, score 0, irrelevant. Overridable per test."""
    fields = dict(
        title="Lackluster primary paves path to a hotly contested midterm",
        source_type="news",
        source_name="Paige Cognetti - Google News",
        source_url="https://news.google.com/rss/articles/CBMiabc123",
        raw_text="",                       # body never fetched
        archived_as_irrelevant=True,
        race_relevance_score=0,
        race_relevance_label="irrelevant",
        content_category="irrelevant",
        actionability_label="ignore",
        reviewed=False,
        dismissed=False,
    )
    fields.update(overrides)
    return SourceItem(**fields)


# --------------------------------------------------------------------------
# config derivation
# --------------------------------------------------------------------------

def test_campaign_people_are_first_last_display(db):
    assert ingestion._campaign_people(db) == [
        ("paige", "cognetti", "Paige Cognetti"),
        ("rob", "bresnahan", "Rob Bresnahan"),
        ("sam", "ross", "Sam Ross"),
    ]


def test_district_label_variants(db):
    assert ingestion._district_label_variants(db) == {"pa-08", "pa 08", "pa08"}


# --------------------------------------------------------------------------
# _provenance_rescue_label — the qualifying decision
# --------------------------------------------------------------------------

def test_rescue_qualifies_full_name_monitor(db):
    # thin body + monitor named with the candidate's FULL name.
    assert ingestion._provenance_rescue_label(db, _archived_item()) == "Paige Cognetti"


def test_rescue_qualifies_comma_reversed_full_name(db):
    # "COGNETTI, PAIGE" — both tokens present, order/punctuation irrelevant.
    item = _archived_item(source_name="Google News: COGNETTI, PAIGE")
    assert ingestion._provenance_rescue_label(db, item) == "Paige Cognetti"


def test_rescue_qualifies_opponent_full_name(db):
    item = _archived_item(source_name="Google News: Rob Bresnahan")
    assert ingestion._provenance_rescue_label(db, item) == "Rob Bresnahan"


def test_rescue_qualifies_surname_plus_district(db):
    # No first name, but the district id disambiguates from homonyms.
    item = _archived_item(source_name="Google News — PA-08 Cognetti coverage")
    assert ingestion._provenance_rescue_label(db, item) == "Paige Cognetti"


def test_rescue_qualifies_two_surnames_together(db):
    # Two race surnames named together → clearly THIS race, no first name needed.
    item = _archived_item(source_name="Cognetti vs Bresnahan tracker")
    assert ingestion._provenance_rescue_label(db, item) == "Paige Cognetti"


def test_rescue_rejects_bare_surname_homonym(db):
    # The precision boundary: a bare surname collides with the novelist Paolo
    # Cognetti / ballplayer Roger Bresnahan. With no disambiguator, do NOT
    # rescue — these flooded the surname-only set with noise.
    assert ingestion._provenance_rescue_label(
        db, _archived_item(source_name="Mastodon #Cognetti via mastodon.social")
    ) is None
    assert ingestion._provenance_rescue_label(
        db, _archived_item(source_name="YouTube: Bresnahan")
    ) is None


def test_rescue_skips_when_body_is_full(db):
    # A real body means the "irrelevant" verdict was a genuine judgment, not a
    # fetch failure — respect it, don't rescue.
    item = _archived_item(raw_text="x" * ingestion._PROVENANCE_RESCUE_MAX_BODY_CHARS)
    assert ingestion._provenance_rescue_label(db, item) is None


def test_rescue_skips_when_monitor_not_named_for_race(db):
    item = _archived_item(source_name="WVIA Public Media")
    assert ingestion._provenance_rescue_label(db, item) is None


def test_rescue_surname_match_is_whole_word_not_substring(db):
    # "ross" (opponent Sam Ross) must NOT fire inside "Crossroads".
    assert ingestion._provenance_rescue_label(
        db, _archived_item(source_name="Crossroads Gazette")
    ) is None
    # ...but the real whole-word full-name label still matches.
    assert ingestion._provenance_rescue_label(
        db, _archived_item(source_name="Sam Ross Watch")
    ) == "Sam Ross"


def test_rescue_threshold_is_env_configurable(db, monkeypatch):
    # 250-char body is thin under the default 300 but full under a lowered
    # threshold — proves the constant is the knob, no code change needed.
    item = _archived_item(raw_text="y" * 250)
    assert ingestion._provenance_rescue_label(db, item) == "Paige Cognetti"
    monkeypatch.setattr(ingestion, "_PROVENANCE_RESCUE_MAX_BODY_CHARS", 200)
    assert ingestion._provenance_rescue_label(db, item) is None


# --------------------------------------------------------------------------
# _apply_provenance_rescue — the mutation
# --------------------------------------------------------------------------

def test_apply_rescue_flips_archive_into_review(db):
    item = _archived_item()
    assert ingestion._apply_provenance_rescue(db, item) is True
    assert item.archived_as_irrelevant is False
    assert item.reviewed is False
    assert item.content_category == "campaign"
    assert item.actionability_label == "review"
    assert item.urgency == "medium"
    reasons = json.loads(item.relevance_reasons)
    assert "Provenance safety net" in reasons[0]
    assert "Paige Cognetti" in reasons[0]


def test_apply_rescue_leaves_text_verdict_unchanged(db):
    # We override the ARCHIVE action, not the text verdict: the score/label the
    # thin text earned stay put (honest "unscored"), category+actionability
    # carry the provenance override.
    item = _archived_item()
    ingestion._apply_provenance_rescue(db, item)
    assert item.race_relevance_score == 0
    assert item.race_relevance_label == "irrelevant"


def test_apply_rescue_is_noop_when_not_archived(db):
    item = _archived_item(archived_as_irrelevant=False, content_category="campaign")
    assert ingestion._apply_provenance_rescue(db, item) is False
    assert item.actionability_label == "ignore"  # unchanged


def test_apply_rescue_is_noop_for_bare_surname_homonym(db):
    item = _archived_item(source_name="YouTube: Bresnahan")
    assert ingestion._apply_provenance_rescue(db, item) is False
    assert item.archived_as_irrelevant is True


# --------------------------------------------------------------------------
# Integration: a rescued item actually surfaces in the review queue.
# This is the load-bearing proof — the queue's SQL filters + keyword gate must
# not silently re-exclude what the safety net rescued.
# --------------------------------------------------------------------------

def test_rescued_item_surfaces_in_review_queue(db):
    item = _archived_item()
    ingestion._apply_provenance_rescue(db, item)
    db.add(item)
    db.commit()

    queued = review_queue._review_queue_query(db).all()
    assert item.id in {i.id for i in queued}, "rescued item missing from queue SQL"

    # And it clears the keyword partition gate (actionability='review' bypass),
    # so it lands in the MAIN queue, not the filtered-out spillover.
    assert passes_gate(item, build_keyword_pattern(db)) is True


def test_unrescued_homonym_stays_out_of_queue(db):
    # Control: a bare-surname homonym item gets no rescue, stays archived, and
    # therefore never reaches the queue.
    item = _archived_item(source_name="Mastodon #Cognetti via mastodon.social")
    assert ingestion._apply_provenance_rescue(db, item) is False
    db.add(item)
    db.commit()

    queued = review_queue._review_queue_query(db).all()
    assert item.id not in {i.id for i in queued}


# --------------------------------------------------------------------------
# _is_non_article_landing_page — a directory/scorecard/bio page or a bare social
# ACCOUNT ROOT names a participant without being an article, so it must stay OUT
# of both promotion paths. A real post/video/news article (content-path URL) is
# narrative signal and must pass through. Pure URL structure — no DB, no length.
# --------------------------------------------------------------------------

_LANDING_URLS = [
    "https://ballotpedia.org/Rob_Bresnahan_Jr.",
    "https://www.congressweb.com/ABC/legislators/info/mbr_id/408",
    "https://heritageaction.com/scorecard/members/B001327/119",
    "https://www.leadershipnowproject.org/candidate-bio-paige-cognetti",
    "http://bresnahan.house.gov/contact",
    "https://www.congress.gov/member/robert-bresnahan/B001327",
    "https://www.facebook.com/RepBresnahan/",       # account root, trailing slash
    "https://www.facebook.com/RepBresnahan",        # account root, no slash
    "https://www.facebook.com/PaigeForPA",
    "https://www.instagram.com/repbresnahan?hl=en",  # root + query, no /p/
    "https://www.instagram.com/mayorpaigecognetti",
    "https://x.com/PaigeGCognetti",
    "https://www.linkedin.com/in/robert-bresnahan-jr-89481225",  # profile root
    "https://www.youtube.com/@RepBresnahan",         # channel root
]

_ARTICLE_URLS = [
    "https://www.facebook.com/RepBresnahan/posts/im-proud-to-join-repscholten",
    "https://www.facebook.com/wilkradio/posts/congressman-rob-bresnahan-spoke",
    "https://www.instagram.com/p/DYnBQnaGu4C",
    "https://www.instagram.com/reel/DWCQTN8iD1I",
    "https://www.youtube.com/watch?v=98cgzf3eKa4",
    "https://www.youtube.com/shorts/ms6GjLd6Czs",    # Shorts ARE video content
    "https://www.linkedin.com/posts/robert-bresnahan-jr_pa08-activity-7302489435",
    "https://www.poconorecord.com/picture-gallery/news/politics/2026/04/15/rob-bresnahan-cognetti",
    "https://www.wvia.org/news/2026-05-19/pa-08-primary-preview",
    "https://www.nytimes.com/2026/05/01/us/politics/pa-08-poll.html",
]


@pytest.mark.parametrize("url", _LANDING_URLS)
def test_landing_page_detected(url):
    assert ingestion._is_non_article_landing_page(SourceItem(source_url=url)) is True


@pytest.mark.parametrize("url", _ARTICLE_URLS)
def test_real_article_not_flagged_as_landing(url):
    assert ingestion._is_non_article_landing_page(SourceItem(source_url=url)) is False


def test_landing_page_none_url_is_false():
    assert ingestion._is_non_article_landing_page(SourceItem(source_url=None)) is False


# --------------------------------------------------------------------------
# The guard fires INSIDE both promotion paths — a thin landing page whose
# headline/monitor names the race is refused, while a thin REAL post with the
# same race-naming headline still gets promoted (the guard must not eat signal).
# --------------------------------------------------------------------------

def test_headline_promotion_skips_landing_page(db):
    # Ballotpedia title carries the candidate's full name, so the homonym-safe
    # headline gate WOULD fire — but it's a directory page, not an article.
    item = _archived_item(
        title="Rob Bresnahan Jr. - Ballotpedia",
        source_name="ballotpedia.org",
        source_url="https://ballotpedia.org/Rob_Bresnahan_Jr.",
        raw_text="Rob Bresnahan Jr. is a member of the U.S. House.",  # thin
    )
    assert ingestion._headline_names_race_disambiguated(db, item) is True  # would fire
    assert ingestion._apply_headline_feed_promotion(db, item) is False     # guard blocks
    assert item.archived_as_irrelevant is True       # untouched
    assert item.content_category == "irrelevant"     # untouched


def test_headline_promotion_still_fires_on_real_post(db):
    # Control: a thin REAL post (content-path URL) with the same race-naming
    # headline IS promoted — the guard must not over-block social signal.
    item = _archived_item(
        title="I'm proud to join... - Congressman Rob Bresnahan Jr.",
        source_name="www.facebook.com",
        source_url="https://www.facebook.com/RepBresnahan/posts/im-proud-to-join",
        raw_text="I'm proud to join RepScholten to introduce the Housing Act.",  # thin
    )
    assert ingestion._is_non_article_landing_page(item) is False
    assert ingestion._apply_headline_feed_promotion(db, item) is True
    assert item.archived_as_irrelevant is False


def test_provenance_rescue_skips_landing_page(db):
    # A Ballotpedia page pulled by a candidate-named monitor is provenance-strong
    # AND thin — the guard must still leave the directory page archived.
    item = _archived_item(
        title="Rob Bresnahan Jr. - Ballotpedia",
        source_name="Google News: Rob Bresnahan",
        source_url="https://ballotpedia.org/Rob_Bresnahan_Jr.",
        raw_text="",
    )
    assert ingestion._provenance_rescue_label(db, item) == "Rob Bresnahan"  # would qualify
    assert ingestion._apply_provenance_rescue(db, item) is False            # guard blocks
    assert item.archived_as_irrelevant is True
