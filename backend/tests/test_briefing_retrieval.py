"""Tests for app.services.briefing_retrieval — top_claims + top_entities."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    ClaimRecord,
    ClaimRecordEntity,
    Entity,
    EntityMention,
    Outlet,
    SourceItem,
)
from app.services.briefing_retrieval import (
    MIN_QUOTE_LENGTH,
    overnight_changes,
    top_claims_for_briefing,
    top_entities_for_briefing,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


# ── Helpers to build fixtures ──────────────────────────────────────────────

def _make_outlet(db, name="The Test Outlet", domain="test.com", reliability=70):
    o = Outlet(name=name, domain=domain, outlet_type="local_news",
               authority_score=5, reliability_score=reliability, active=True)
    db.add(o); db.flush()
    return o


def _make_article(db, outlet=None, days_ago=1, relevance=80, archived=False,
                  title="An article", source_url="https://test.com/a"):
    si = SourceItem(
        title=title, source_name="src",
        source_url=source_url, source_type="news",
        published_at=datetime.utcnow() - timedelta(days=days_ago),
        race_relevance_score=relevance,
        archived_as_irrelevant=archived,
        outlet_id=outlet.id if outlet else None,
        raw_text="dummy body",
    )
    db.add(si); db.flush()
    return si


def _make_entity(db, canonical_id, name=None, type="person", affiliation="D", seeded=True):
    e = Entity(canonical_id=canonical_id, name=name or canonical_id,
               type=type, affiliation=affiliation, seeded=seeded,
               aliases="[]", description="", metadata_json="{}",
               mention_count=0)
    db.add(e); db.flush()
    return e


def _make_claim(db, article, label, quote="This is a long enough sample quote about politics",
                entities=()):
    cr = ClaimRecord(
        article_id=article.id, evidence_span=quote,
        evidence_start_char=0, evidence_end_char=len(quote),
        evidence_hash=str(hash(quote))[:40],
        label=label, confidence="high", extractor_version="v15.0",
    )
    db.add(cr); db.flush()
    for ent in entities:
        db.add(ClaimRecordEntity(claim_record_id=cr.id, entity_id=ent.id,
                                  surface_text=ent.name))
    db.flush()
    return cr


def _make_mention(db, article, entity):
    em = EntityMention(article_id=article.id, entity_id=entity.id,
                       surface_text=entity.name, confidence="high",
                       extraction_method="seed")
    db.add(em); db.flush()
    return em


# ── top_claims_for_briefing tests ──────────────────────────────────────────

class TestTopClaims:
    def test_empty_returns_empty_list(self, db):
        assert top_claims_for_briefing(db) == []

    def test_excludes_statement_and_announcement_labels(self, db):
        e = _make_entity(db, "person:cognetti")
        outlet = _make_outlet(db)
        art = _make_article(db, outlet=outlet)
        _make_claim(db, art, label="statement", entities=[e])
        _make_claim(db, art, label="announcement",
                    quote="A different long enough quote about politics today",
                    entities=[e])
        _make_claim(db, art, label="attack",
                    quote="A third long enough quote about an attack on policy",
                    entities=[e])
        out = top_claims_for_briefing(db)
        assert len(out) == 1
        assert out[0]["label"] == "attack"

    def test_excludes_short_quotes(self, db):
        e = _make_entity(db, "person:cognetti")
        outlet = _make_outlet(db)
        art = _make_article(db, outlet=outlet)
        _make_claim(db, art, label="attack", quote="Too short", entities=[e])
        _make_claim(db, art, label="attack",
                    quote="A" * (MIN_QUOTE_LENGTH + 5), entities=[e])
        out = top_claims_for_briefing(db)
        assert len(out) == 1

    def test_excludes_low_race_relevance_articles(self, db):
        e = _make_entity(db, "person:cognetti")
        outlet = _make_outlet(db)
        # Article with score 30 is filtered out (we want >= 50)
        art_low = _make_article(db, outlet=outlet, relevance=30,
                                source_url="https://test.com/low")
        _make_claim(db, art_low, label="attack",
                    quote="Quote on low-relevance article that's long enough here",
                    entities=[e])
        # Article with score 60 is included
        art_high = _make_article(db, outlet=outlet, relevance=60,
                                 source_url="https://test.com/high")
        _make_claim(db, art_high, label="attack",
                    quote="Quote on high-relevance article that's long enough here",
                    entities=[e])
        out = top_claims_for_briefing(db)
        assert len(out) == 1
        assert out[0]["article_id"] == art_high.id

    def test_excludes_archived_articles(self, db):
        e = _make_entity(db, "person:cognetti")
        outlet = _make_outlet(db)
        art = _make_article(db, outlet=outlet, archived=True)
        _make_claim(db, art, label="attack",
                    quote="A long enough archived quote that should not appear here",
                    entities=[e])
        assert top_claims_for_briefing(db) == []

    def test_excludes_old_articles_outside_window(self, db):
        e = _make_entity(db, "person:cognetti")
        outlet = _make_outlet(db)
        old = _make_article(db, outlet=outlet, days_ago=30)
        new = _make_article(db, outlet=outlet, days_ago=2,
                            source_url="https://test.com/new")
        _make_claim(db, old, label="attack",
                    quote="A long enough quote from 30 days ago that shouldn't appear",
                    entities=[e])
        _make_claim(db, new, label="attack",
                    quote="A long enough quote from 2 days ago that should appear",
                    entities=[e])
        out = top_claims_for_briefing(db, days=7)
        assert len(out) == 1
        assert out[0]["article_id"] == new.id

    def test_higher_reliability_outranks_lower(self, db):
        e = _make_entity(db, "person:cognetti")
        hi_outlet = _make_outlet(db, name="High", domain="hi.com", reliability=92)
        lo_outlet = _make_outlet(db, name="Low", domain="lo.com", reliability=20)
        # Same recency, same label — only outlet differs
        art_hi = _make_article(db, outlet=hi_outlet, days_ago=2,
                               source_url="https://hi.com/a")
        art_lo = _make_article(db, outlet=lo_outlet, days_ago=2,
                               source_url="https://lo.com/a")
        _make_claim(db, art_lo, label="attack",
                    quote="Quote from a low-reliability outlet, long enough",
                    entities=[e])
        _make_claim(db, art_hi, label="attack",
                    quote="Quote from a high-reliability outlet, long enough",
                    entities=[e])
        out = top_claims_for_briefing(db)
        assert len(out) == 2
        # High-reliability outlet's claim ranks first
        assert out[0]["outlet"] == "High"

    def test_aliases_are_resolved_in_entity_output(self, db):
        seed = _make_entity(db, "person:bresnahan", name="Rob Bresnahan", affiliation="R")
        alias = _make_entity(db, "person:auto:rob-bresnahan-jr",
                             name="Rob Bresnahan Jr.", affiliation="R", seeded=False)
        outlet = _make_outlet(db)
        art = _make_article(db, outlet=outlet)
        # Quote linked to the AUTO alias
        _make_claim(db, art, label="attack",
                    quote="A long enough quote attacking Bresnahan today here",
                    entities=[alias])
        out = top_claims_for_briefing(db)
        # The alias should resolve to the seeded canonical id
        assert out[0]["entities"][0]["id"] == "person:bresnahan"


# ── top_entities_for_briefing tests ────────────────────────────────────────

class TestTopEntities:
    def test_always_shows_cognetti_and_bresnahan_even_with_zero_activity(self, db):
        # No mentions in the DB at all
        _make_entity(db, "person:cognetti", name="Paige Cognetti", affiliation="D")
        _make_entity(db, "person:bresnahan", name="Rob Bresnahan", affiliation="R")
        out = top_entities_for_briefing(db)
        ids = [e["id"] for e in out]
        assert "person:cognetti" in ids
        assert "person:bresnahan" in ids

    def test_alias_mentions_are_merged_into_canonical(self, db):
        seed = _make_entity(db, "person:bresnahan", name="Rob Bresnahan")
        alias = _make_entity(db, "person:auto:rob-bresnahan-jr",
                             name="Rob Bresnahan Jr.", seeded=False)
        _make_entity(db, "person:cognetti", name="Paige Cognetti")
        outlet = _make_outlet(db)
        # 2 mentions via the seed, 1 via the alias, all this week
        for i in range(2):
            a = _make_article(db, outlet=outlet, days_ago=1,
                              source_url=f"https://test.com/seed-{i}")
            _make_mention(db, a, seed)
        a = _make_article(db, outlet=outlet, days_ago=1,
                          source_url="https://test.com/alias")
        _make_mention(db, a, alias)
        out = top_entities_for_briefing(db)
        bres = next(e for e in out if e["id"] == "person:bresnahan")
        assert bres["mentions_this_week"] == 3  # 2 seed + 1 alias

    def test_caps_to_six_entries(self, db):
        # Seed all the always-show + context entities, give them all some mentions
        cids = [
            "person:cognetti", "person:bresnahan", "person:trump", "person:shapiro",
            "person:cartwright", "org:dccc", "org:nrcc",
            "bill:stock-act", "bill:medicaid-cuts", "bill:tax-cuts", "bill:aca-subsidies",
        ]
        outlet = _make_outlet(db)
        for i, cid in enumerate(cids):
            e_type = cid.split(":")[0]
            ent = _make_entity(db, cid, name=cid, type=e_type)
            # Give each a distinct mention count via N unique articles
            for j in range(11 - i):  # cognetti gets 11, last bill gets 1
                a = _make_article(db, outlet=outlet, days_ago=1,
                                  source_url=f"https://test.com/{cid}-{j}")
                _make_mention(db, a, ent)
        out = top_entities_for_briefing(db)
        # Always-show (cognetti+bresnahan) + top 4 from context = 6 max
        assert len(out) == 6
        ids = [e["id"] for e in out]
        assert "person:cognetti" in ids
        assert "person:bresnahan" in ids

    def test_sample_recent_titles_returned(self, db):
        e = _make_entity(db, "person:cognetti", name="Paige Cognetti")
        _make_entity(db, "person:bresnahan", name="Rob Bresnahan")
        outlet = _make_outlet(db)
        for i in range(5):
            a = _make_article(db, outlet=outlet, days_ago=i,
                              title=f"Cognetti article number {i}",
                              source_url=f"https://test.com/c-{i}")
            _make_mention(db, a, e)
        out = top_entities_for_briefing(db)
        cog = next(x for x in out if x["id"] == "person:cognetti")
        assert len(cog["sample_recent_titles"]) == 3
        # Should be most-recent first
        assert "number 0" in cog["sample_recent_titles"][0]


# ── overnight_changes tests ────────────────────────────────────────────────

class TestOvernightChanges:
    def test_empty_returns_empty_list(self, db):
        # Even with cognetti seeded but no claims, should return empty list
        _make_entity(db, "person:cognetti", name="Paige Cognetti")
        _make_entity(db, "person:bresnahan", name="Rob Bresnahan")
        assert overnight_changes(db) == []

    def test_excludes_quotes_only_about_trump(self, db):
        # A claim that mentions ONLY Trump should NOT surface here — the
        # candidate-only gate prevents Jen-Kiggans-in-VA-style noise.
        _make_entity(db, "person:cognetti", name="Paige Cognetti")
        _make_entity(db, "person:bresnahan", name="Rob Bresnahan")
        trump = _make_entity(db, "person:trump", name="Donald Trump")
        outlet = _make_outlet(db)
        art = _make_article(db, outlet=outlet, days_ago=1)
        _make_claim(db, art, label="attack",
                    quote="A long enough quote about Trump and some other race",
                    entities=[trump])
        assert overnight_changes(db) == []

    def test_includes_quotes_about_cognetti_or_bresnahan(self, db):
        cog = _make_entity(db, "person:cognetti", name="Paige Cognetti")
        bres = _make_entity(db, "person:bresnahan", name="Rob Bresnahan")
        outlet = _make_outlet(db)
        a1 = _make_article(db, outlet=outlet, days_ago=1)
        _make_claim(db, a1, label="endorsement",
                    quote="A long enough quote endorsing Cognetti for Congress here",
                    entities=[cog])
        a2 = _make_article(db, outlet=outlet, days_ago=1,
                           source_url="https://test.com/b")
        _make_claim(db, a2, label="attack",
                    quote="A long enough quote attacking Bresnahan over something",
                    entities=[bres])
        out = overnight_changes(db)
        assert len(out) == 2

    def test_excludes_articles_outside_hours_window(self, db):
        cog = _make_entity(db, "person:cognetti", name="Paige Cognetti")
        outlet = _make_outlet(db)
        # 3 days ago — outside default 48h
        old = _make_article(db, outlet=outlet, days_ago=3)
        _make_claim(db, old, label="endorsement",
                    quote="A long enough quote from 3 days ago about Cognetti",
                    entities=[cog])
        # 1 day ago — inside default 48h
        new = _make_article(db, outlet=outlet, days_ago=1,
                            source_url="https://test.com/new")
        _make_claim(db, new, label="endorsement",
                    quote="A long enough quote from 1 day ago about Cognetti",
                    entities=[cog])
        out = overnight_changes(db, hours=48)
        assert len(out) == 1
        assert "1 day ago" in out[0]["quote"]

    def test_respects_label_allowlist(self, db):
        cog = _make_entity(db, "person:cognetti", name="Paige Cognetti")
        outlet = _make_outlet(db)
        a1 = _make_article(db, outlet=outlet, days_ago=1)
        _make_claim(db, a1, label="statement",
                    quote="A long enough statement-labeled quote about Cognetti",
                    entities=[cog])
        a2 = _make_article(db, outlet=outlet, days_ago=1,
                           source_url="https://test.com/b")
        _make_claim(db, a2, label="endorsement",
                    quote="A long enough endorsement-labeled quote about Cognetti",
                    entities=[cog])
        out = overnight_changes(db)
        # statement is NOT in the allowlist; endorsement is
        assert len(out) == 1
        assert out[0]["label"] == "endorsement"
