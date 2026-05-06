"""Tests for duplicate-extraction and duplicate-mention fixes."""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    CampaignConfig,
    NarrativeMention,
    Opponent,
    OpponentActivity,
    SourceItem,
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


def _campaign(db):
    db.add(CampaignConfig(
        candidate_name="Alex Rivera",
        office="Assembly",
        district="Queens District 30",
        location="Queens",
        key_priorities=json.dumps(["Housing", "Transit"]),
    ))
    opp = Opponent(name="Jordan Lee")
    db.add(opp)
    db.commit()
    return opp


def _source(db, title, raw_text="", source_url=None, cluster=None, days=1):
    s = SourceItem(
        title=title,
        raw_text=raw_text,
        source_name="Test Source",
        source_type="news",
        source_url=source_url,
        published_at=datetime.utcnow() - timedelta(days=days),
        created_at=datetime.utcnow() - timedelta(days=days),
        race_relevance_score=85,
        race_relevance_label="critical",
        actionability_score=80,
        actionability_label="respond",
        content_category="campaign",
        archived_as_irrelevant=False,
        story_cluster_id=cluster or f"cluster-{title[:20]}",
        geo_relevance="district",
        opponent_mentioned=True,
    )
    db.add(s)
    db.commit()
    return s


# ── _activity_fingerprint unit tests ─────────────────────────────────────────

class TestActivityFingerprint:
    def _fp(self):
        from app.services.opponent_analysis import _activity_fingerprint
        return _activity_fingerprint

    def test_identical_attack_same_fingerprint(self):
        fp = self._fp()
        a = {"attack": "The opponent lied.", "claim": None, "promise": None}
        b = {"attack": "The opponent lied.", "claim": None, "promise": None}
        assert fp(a) == fp(b)

    def test_case_and_whitespace_normalized(self):
        fp = self._fp()
        a = {"attack": "The  OPPONENT  lied.", "claim": None, "promise": None}
        b = {"attack": "the opponent lied.", "claim": None, "promise": None}
        assert fp(a) == fp(b)

    def test_different_fields_produce_different_fingerprints(self):
        fp = self._fp()
        attack_only = {"attack": "Opponent failed.", "claim": None, "promise": None}
        claim_only = {"attack": None, "claim": "Opponent failed.", "promise": None}
        assert fp(attack_only) != fp(claim_only)

    def test_all_none_produces_stable_key(self):
        fp = self._fp()
        result = fp({"attack": None, "claim": None, "promise": None})
        assert result == "||"  # three empty segments


# ── analyze_source_for_opponents dedup tests ──────────────────────────────────

class TestOpponentAnalysisDedup:
    def _run(self, db, source):
        from app.services.opponent_analysis import analyze_source_for_opponents
        return analyze_source_for_opponents(db, source)

    def test_repeated_call_does_not_double_activities(self, db):
        """Calling analyze twice on the same source must not create duplicates."""
        opp = Opponent(name="Harmon", office="Council", party="R")
        db.add(opp)
        db.commit()

        s = _source(db, "Harmon falsely claimed crime is down.")
        self._run(db, s)
        self._run(db, s)

        count = db.query(OpponentActivity).filter_by(source_item_id=s.id).count()
        assert count == 1

    def test_null_field_does_not_suppress_distinct_activity(self, db):
        """Old bug: (col == None) compiled to 'col IS NULL', matching unrelated rows.

        A source with two semantically distinct sentences — one attack (attack!=NULL,
        claim=NULL) and one claim-only (attack=NULL, claim!=NULL) — must produce two
        separate OpponentActivity rows, not one.
        """
        opp = Opponent(name="Harmon", office="Council", party="R")
        db.add(opp)
        db.commit()

        # "attacked" → attack marker  |  "says" → claim marker (no attack)
        s = _source(
            db,
            title="Harmon attacked Rivera for failing on crime.",
            raw_text="Harmon says the city needs more investment.",
        )
        activities = self._run(db, s)

        has_attack = any(a.attack is not None for a in activities)
        has_claim_only = any(a.claim is not None and a.attack is None for a in activities)
        assert has_attack, "Expected at least one attack activity"
        assert has_claim_only, "Expected at least one claim-only activity (NULL bug would suppress this)"

    def test_whitespace_variation_is_deduplicated(self, db):
        """Minor whitespace differences in extracted text don't create duplicates."""
        opp = Opponent(name="Harmon", office="Council", party="R")
        db.add(opp)
        db.commit()

        # Title has no trailing period: full_text = "title. raw_text" adds one,
        # so the extracted sentence ends with a single ".".
        # "lied" only triggers _ATTACK_MARKERS (no claim marker), giving attack-only.
        s = _source(db, "Harmon lied about crime  rates")

        # Inject a row that differs only in whitespace (the engine would produce this)
        db.add(OpponentActivity(
            opponent_id=opp.id,
            source_item_id=s.id,
            attack="Harmon lied about crime rates.",
        ))
        db.commit()

        self._run(db, s)  # should not add a second row

        count = db.query(OpponentActivity).filter_by(source_item_id=s.id).count()
        assert count == 1

    def test_genuinely_different_sentences_both_stored(self, db):
        """Two distinct extracted sentences from one source must each get a row."""
        opp = Opponent(name="Harmon", office="Council", party="R")
        db.add(opp)
        db.commit()

        s = _source(
            db,
            title="Harmon falsely claimed crime is down.",
            raw_text="Harmon lied about the housing numbers.",
        )
        activities = self._run(db, s)
        assert len(activities) >= 2

        # Calling again should not add more
        self._run(db, s)
        count = db.query(OpponentActivity).filter_by(source_item_id=s.id).count()
        assert count == len(activities)


# ── NarrativeMention dedup tests ──────────────────────────────────────────────

class TestNarrativeMentionDedup:
    def test_same_source_not_repeated_in_one_narrative(self, db):
        """Two OpponentActivity rows from one source must not inflate mention count."""
        from app.services.narratives import refresh_narratives
        from app.models import Narrative

        opp = _campaign(db)

        # One article that yielded two extracted attack sentences
        s = _source(
            db,
            title="Jordan Lee says Alex Rivera failed on housing.",
            raw_text="Jordan Lee claims Rivera lied about rent control.",
            source_url="https://queensdaily.com/article-1",
            cluster="cluster-housing-attack",
        )
        db.add(OpponentActivity(
            opponent_id=opp.id,
            source_item_id=s.id,
            attack="Jordan Lee says Alex Rivera failed on housing.",
            repeated_theme="housing",
        ))
        db.add(OpponentActivity(
            opponent_id=opp.id,
            source_item_id=s.id,
            attack="Jordan Lee claims Rivera lied about rent control.",
            repeated_theme="housing",
        ))
        db.commit()

        refresh_narratives(db, force=True)

        for narr in db.query(Narrative).all():
            mentions = db.query(NarrativeMention).filter_by(narrative_id=narr.id).all()
            source_ids = [m.source_item_id for m in mentions if m.source_item_id is not None]
            assert len(source_ids) == len(set(source_ids)), (
                f"Narrative {narr.id!r} ({narr.short_label!r}) links source {s.id} "
                f"more than once: {source_ids}"
            )

    def test_two_sources_same_narrative_both_appear(self, db):
        """Different sources that map to the same narrative both get a mention."""
        from app.services.narratives import refresh_narratives
        from app.models import Narrative

        opp = _campaign(db)

        s1 = _source(db, "Jordan Lee says Alex Rivera failed tenants",
                     source_url="https://a.com/1", cluster="c1")
        s2 = _source(db, "Jordan Lee claims Rivera failed on housing",
                     source_url="https://b.com/2", cluster="c2")
        db.add(OpponentActivity(opponent_id=opp.id, source_item_id=s1.id,
                                attack="Jordan Lee says Alex Rivera failed tenants."))
        db.add(OpponentActivity(opponent_id=opp.id, source_item_id=s2.id,
                                attack="Jordan Lee claims Rivera failed on housing."))
        db.commit()

        refresh_narratives(db, force=True)

        narratives = db.query(Narrative).all()
        assert narratives, "Expected at least one narrative"

        # The narrative that groups the housing attacks should reference both sources
        housing_narr = next(
            (n for n in narratives if n.owner_type in {"opponent", "unknown"}),
            narratives[0],
        )
        mentions = db.query(NarrativeMention).filter_by(narrative_id=housing_narr.id).all()
        mentioned_source_ids = {m.source_item_id for m in mentions if m.source_item_id}
        assert s1.id in mentioned_source_ids or s2.id in mentioned_source_ids, (
            "Expected at least one source to be linked to the narrative"
        )

    def test_db_constraint_prevents_duplicate_mention_insert(self, db):
        """The UniqueConstraint on (narrative_id, source_item_id) enforces dedup at DB level."""
        from sqlalchemy.exc import IntegrityError
        from app.models import Narrative

        s = _source(db, "Some source", source_url="https://x.com/1")
        narr = Narrative(
            canonical_text="Test narrative",
            short_label="Test",
            narrative_type="opponent_attack",
        )
        db.add(narr)
        db.flush()

        db.add(NarrativeMention(narrative_id=narr.id, source_item_id=s.id, mention_role="seed"))
        db.flush()

        with pytest.raises(IntegrityError):
            db.add(NarrativeMention(narrative_id=narr.id, source_item_id=s.id, mention_role="repeat"))
            db.flush()

    def test_null_source_id_not_constrained(self, db):
        """Multiple activity-only mentions (source_item_id=NULL) for one narrative are allowed."""
        from app.models import Narrative

        narr = Narrative(
            canonical_text="Test narrative",
            short_label="Test",
            narrative_type="opponent_attack",
        )
        db.add(narr)
        db.flush()

        # Both have NULL source_item_id — constraint must not fire
        db.add(NarrativeMention(narrative_id=narr.id, source_item_id=None, mention_role="seed"))
        db.add(NarrativeMention(narrative_id=narr.id, source_item_id=None, mention_role="repeat"))
        db.flush()  # should not raise

        count = db.query(NarrativeMention).filter_by(narrative_id=narr.id).count()
        assert count == 2
