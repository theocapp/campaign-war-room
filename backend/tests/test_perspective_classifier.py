"""Unit tests for V13.22 perspective-classifier fixes.

Each test corresponds to a specific failure pattern surfaced by the
audit of 2,350 article classifications. Tests assert the fix works
without breaking the cases the classifier was already getting right.

Run with:
    cd backend && .venv/bin/python -m pytest tests/test_perspective_classifier.py -v
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from app.services.article_perspective import (
    PerspectiveResult,
    get_classifier,
)


# ── Fixtures ───────────────────────────────────────────────────────────────

@dataclass
class FakeCampaignConfig:
    candidate_name: str = "Paige Cognetti"
    party: str = "Democrat"
    district: str = "PA-08"
    district_number: int = 8
    location: str = "Scranton/Wilkes-Barre, PA-08"
    geography_keywords: str = (
        '["Scranton", "Wilkes-Barre", "Lackawanna", "Luzerne", "NEPA", '
        '"northeastern Pennsylvania", "Pocono"]'
    )
    race: str = "PA-08 U.S. House"


@dataclass
class FakeOpponent:
    name: str = "Rob Bresnahan"
    party: str = "Republican"


@dataclass
class FakeSourceItem:
    id: int = 1
    title: str = ""
    summary: str = ""
    raw_text: str = ""
    source_name: str = ""
    source_url: str = ""
    publisher_domain: Optional[str] = None
    source_owner_type: Optional[str] = None


class FakeDB:
    """Minimal stand-in for SQLAlchemy Session that returns our fakes."""

    def __init__(self, cfg=None, opponents=None):
        self.cfg = cfg or FakeCampaignConfig()
        self.opponents = opponents or [FakeOpponent()]

    def query(self, model):
        return _FakeQuery(self, model)


class _FakeQuery:
    def __init__(self, db: FakeDB, model):
        self._db = db
        self._model = model

    def first(self):
        if self._model.__name__ == "CampaignConfig":
            return self._db.cfg
        return None

    def all(self):
        if self._model.__name__ == "Opponent":
            return self._db.opponents
        return []


@pytest.fixture
def classify():
    db = FakeDB()
    return get_classifier(db)


# ── Fix #1: outlet_bias gated on race mention ──────────────────────────────

def test_outlet_bias_skips_when_article_does_not_mention_race(classify):
    """Audit case [5658]: 'Mayors want to keep handing out free cash' on
    Foxnews.com — fired outlet_bias=pro_opponent pre-fix, but article never
    mentions Cognetti, Bresnahan, or PA-08. Should fall through now."""
    item = FakeSourceItem(
        id=5658,
        title="Mayors want to keep handing out free cash after federal funds dried up",
        summary="Several US mayors have asked the federal government to extend cash assistance programs.",
        raw_text="Cities including Chicago, Stockton, and Denver have piloted guaranteed-income programs.",
        source_name="Foxnews",
        source_url="https://www.foxnews.com/politics/free-cash-mayors",
    )
    result = classify(item)
    assert result.method != "outlet_bias", (
        f"Expected outlet_bias to skip article without race mention, "
        f"got {result.method}/{result.perspective}: {result.reason}"
    )


def test_outlet_bias_still_fires_when_article_mentions_candidate(classify):
    """Outlet bias should still apply when the article actually touches the race."""
    item = FakeSourceItem(
        id=1,
        title="Bresnahan defends stock trades amid scrutiny",
        summary="Rep. Rob Bresnahan said new guardrails will prevent conflicts of interest.",
        raw_text="Bresnahan's office released a statement Tuesday.",
        source_name="Foxnews",
        source_url="https://www.foxnews.com/politics/bresnahan-stock-trades",
    )
    result = classify(item)
    assert result.method == "outlet_bias", (
        f"Expected outlet_bias to fire on Fox article mentioning Bresnahan, "
        f"got {result.method}: {result.reason}"
    )
    # Right-leaning outlet → favors Republican → in this race that's the opponent.
    assert result.perspective == "pro_opponent"


def test_outlet_bias_left_leaning_skips_when_no_race_mention(classify):
    """Audit case [4159]: Rawstory article about 'Republican extortionists'
    in Congress fired outlet_bias=pro_candidate pre-fix. Article doesn't
    mention Cognetti, Bresnahan, or PA-08 → should skip outlet_bias."""
    item = FakeSourceItem(
        id=4159,
        title="A small group of Republican 'extortionists' has usurped Mike Johnson's power",
        summary="Reports indicate House conservatives are flexing on the speaker.",
        raw_text="The Freedom Caucus has been pushing back against leadership compromises.",
        source_name="Rawstory",
        source_url="https://www.rawstory.com/republican-extortionists",
    )
    result = classify(item)
    assert result.method != "outlet_bias"


def test_outlet_bias_left_leaning_fires_with_race_mention(classify):
    """Confirms the gate is symmetric — left-leaning outlets still work
    when the article does touch the race."""
    item = FakeSourceItem(
        id=2,
        title="Cognetti to challenge Bresnahan in PA-08",
        summary="Scranton mayor Paige Cognetti announced her candidacy.",
        raw_text="",
        source_name="Rawstory",
        source_url="https://www.rawstory.com/cognetti-pa-08",
    )
    result = classify(item)
    assert result.method == "outlet_bias"
    assert result.perspective == "pro_candidate"


def test_outlet_bias_fires_on_geography_only_mention(classify):
    """Article from Foxnews mentioning Scranton (a campaign geography keyword)
    should still pass the gate even without candidate names."""
    item = FakeSourceItem(
        id=3,
        title="Scranton mayor faces budget challenges",
        summary="The city of Scranton is dealing with revenue shortfalls.",
        raw_text="",
        source_name="Foxnews",
        source_url="https://www.foxnews.com/local/scranton-budget",
    )
    result = classify(item)
    assert result.method == "outlet_bias"


# ── Fix #4: attribution polarity (drop ambiguous possessives) ──────────────

def test_attribution_does_not_misfire_on_protest_at_office(classify):
    """Audit case [4330]: 'NEPA residents deliver petition to Rep. Bresnahan's
    office over Medicaid' was tagged pro_opponent because the regex caught
    "Bresnahan's office". But the article ATTACKS him. Fix: drop 'office'
    from possessives — too ambiguous."""
    item = FakeSourceItem(
        id=4330,
        title="NEPA residents deliver petition to Rep. Bresnahan's office over Medicaid cuts",
        summary="Protesters gathered outside Rob Bresnahan's office to deliver a petition opposing his vote.",
        raw_text="The petition criticizes Bresnahan's support for proposed Medicaid cuts.",
        source_name="WBRE",
        source_url="https://fox56.com/news/local/nepa-medicaid-petition",
    )
    result = classify(item)
    assert result.method != "attribution" or result.perspective != "pro_opponent", (
        f"Attribution should not fire on 'Bresnahan's office' alone, "
        f"got {result.method}/{result.perspective}: {result.reason}"
    )


def test_attribution_fires_on_speaker_verb(classify):
    """Confirm attribution still works for unambiguous speaker patterns."""
    item = FakeSourceItem(
        id=4,
        title="Bresnahan said the bill will pass",
        summary="Rep. Bresnahan said Tuesday that the infrastructure bill has momentum.",
        raw_text="",
        source_name="WBRE",
        source_url="https://wbre.com/news/bresnahan-bill",
    )
    result = classify(item)
    assert result.method == "attribution"
    assert result.perspective == "pro_opponent"


def test_attribution_fires_on_spokesperson(classify):
    """Confirm the strict possessive list still catches clear cases."""
    item = FakeSourceItem(
        id=5,
        title="Bresnahan campaign responds",
        summary="Bresnahan's spokesperson released a statement Tuesday.",
        raw_text="",
        source_name="WBRE",
        source_url="https://wbre.com/news/bresnahan-statement",
    )
    result = classify(item)
    assert result.method == "attribution"
    assert result.perspective == "pro_opponent"


def test_attribution_does_not_fire_on_campaign_possessive(classify):
    """V13.22: 'Bresnahan's campaign' alone is too ambiguous —
    'Bresnahan's campaign is failing' is anti-Bresnahan. Need a more specific
    qualifier like 'campaign manager'."""
    item = FakeSourceItem(
        id=6,
        title="Bresnahan's campaign sputters as poll shows him trailing",
        summary="A new poll shows Rob Bresnahan's campaign is in trouble.",
        raw_text="",
        source_name="WBRE",
        source_url="https://wbre.com/news/bresnahan-poll",
    )
    result = classify(item)
    # Should not return pro_opponent via attribution
    if result.method == "attribution":
        # Allow it only if reason explicitly cites a speaker verb or campaign manager
        assert "manager" in result.reason.lower() or "said" in result.reason.lower(), (
            f"Attribution fired on ambiguous 'campaign' possessive: {result.reason}"
        )


# ── Phase 0: existing labels still work ────────────────────────────────────

def test_candidate_statement_label(classify):
    item = FakeSourceItem(
        id=7,
        title="Cognetti for Congress press release",
        source_name="Cognetti Campaign",
        source_url="https://cognetti.com/press",
        source_owner_type="candidate_statement",
    )
    result = classify(item)
    assert result.perspective == "pro_candidate"
    assert result.method == "existing"
    assert result.confidence == "high"


def test_news_outlet_overrides_mislabeled_opponent_statement(classify):
    """A news article that upstream mislabeled as opponent_statement
    should still fall through to other phases, not blindly take the label."""
    item = FakeSourceItem(
        id=8,
        title="Bresnahan stock trading scandal continues",
        summary="The scrutiny over Bresnahan's stock trades shows no sign of letting up.",
        source_name="NBC News",
        source_url="https://www.nbcnews.com/politics/bresnahan-stocks",
        source_owner_type="opponent_statement",
    )
    result = classify(item)
    assert result.method != "existing", (
        "News-outlet domain should override mislabeled opponent_statement"
    )


# ── Aggregator-aware domain resolution ─────────────────────────────────────

def test_google_news_aggregator_uses_publisher_domain(classify):
    """When source_url is news.google.com, the real publisher_domain
    should drive outlet_bias."""
    item = FakeSourceItem(
        id=9,
        title="Cognetti slams Bresnahan over stock trades",
        summary="Paige Cognetti said Bresnahan's stock trades are a 'public corruption.'",
        source_name="Foxnews via Google News",
        source_url="https://news.google.com/articles/abc123",
        publisher_domain="foxnews.com",
    )
    result = classify(item)
    # Should resolve to outlet_bias on foxnews.com (mentions both candidates → passes gate)
    assert result.method in ("outlet_bias", "attribution"), (
        f"Expected aggregator to resolve to publisher domain, got {result.method}"
    )
