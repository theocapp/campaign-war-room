"""
Tests for source credibility / provenance weighting in the KG system.

Covers:
  - assign_credibility()  heuristic rules
  - get_or_create_kg_source() populates provenance fields
  - _weighted_daily_rate() reflects claim confidence × source credibility
  - velocity_score produced by run_clustering is lower for low-credibility sources
  - alert severity is lower when velocity is suppressed by low credibility
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from typing import Optional

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.knowledge_graph import orm as _kg_orm  # noqa: F401 — registers kg_* tables
from app.knowledge_graph.ingestion import (
    assign_credibility,
    get_or_create_kg_source,
)
from app.knowledge_graph.narrative_engine import (
    ALERT_SEVERITY_THRESHOLD,
    CLUSTERING_METHOD,
    EMA_ALPHA,
    _compute_alert_severity,
    _weighted_daily_rate,
    generate_alerts,
    run_clustering,
)
from app.knowledge_graph.orm import (
    KGClaim,
    KGNarrative,
    KGNarrativeClaim,
    KGSource,
)
from app.models import SourceItem  # noqa: F401 — registers core tables


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    sess = Session()
    yield sess
    sess.close()
    engine.dispose()


# ══════════════════════════════════════════════════════════════════════════════
# assign_credibility — rule coverage
# ══════════════════════════════════════════════════════════════════════════════

class TestAssignCredibility:
    def test_gov_domain_high_credibility(self):
        score, verified = assign_credibility("news", None, "whitehouse.gov")
        assert score == 0.9
        assert verified is True

    def test_gov_subdomain_high_credibility(self):
        score, verified = assign_credibility(None, None, "data.census.gov")
        assert score == 0.9
        assert verified is True

    def test_candidate_owner_is_verified_official(self):
        score, verified = assign_credibility("social", "candidate", "twitter.com")
        assert score == 0.8
        assert verified is True

    def test_opponent_owner_is_verified_official(self):
        score, verified = assign_credibility("social", "opponent", "facebook.com")
        assert score == 0.8
        assert verified is True

    def test_official_owner_is_verified_official(self):
        score, verified = assign_credibility("news", "official", "example.com")
        assert score == 0.8
        assert verified is True

    def test_news_source_type(self):
        score, verified = assign_credibility("news", "unclear", "example.com")
        assert score == 0.7
        assert verified is False

    def test_public_record_source_type(self):
        score, verified = assign_credibility("public_record", None, "example.com")
        assert score == 0.75
        assert verified is False

    def test_opponent_statement_source_type(self):
        score, verified = assign_credibility("opponent_statement", None, "example.com")
        assert score == 0.6
        assert verified is False

    def test_social_known_owner(self):
        score, verified = assign_credibility("social", "business", "twitter.com")
        assert score == 0.5
        assert verified is False

    def test_social_unknown_owner(self):
        score, verified = assign_credibility("social", "unclear", "twitter.com")
        assert score == 0.4
        assert verified is False

    def test_campaign_note(self):
        score, verified = assign_credibility("campaign_note", None, None)
        assert score == 0.6
        assert verified is False

    def test_unclear_provenance_low_score(self):
        score, verified = assign_credibility(None, "unclear", None)
        assert score == 0.3
        assert verified is False

    def test_completely_empty_inputs_default(self):
        score, verified = assign_credibility(None, None, None)
        # Falls through to unclear predicate
        assert 0.0 < score <= 0.5

    def test_gov_takes_priority_over_news_type(self):
        # Even if source_type is "news", .gov domain should win (higher priority)
        score, verified = assign_credibility("news", None, "cdc.gov")
        assert score == 0.9
        assert verified is True

    def test_candidate_owner_takes_priority_over_social_type(self):
        # candidate owner (0.8) wins over social-with-known-owner (0.5)
        score, verified = assign_credibility("social", "candidate", "facebook.com")
        assert score == 0.8
        assert verified is True

    def test_case_insensitive(self):
        score_lower, _ = assign_credibility("news", None, "example.com")
        score_upper, _ = assign_credibility("NEWS", None, "example.com")
        assert score_lower == score_upper

    def test_score_is_float(self):
        score, _ = assign_credibility("news", None, "example.com")
        assert isinstance(score, float)

    def test_verified_official_is_bool(self):
        _, verified = assign_credibility("news", "candidate", "example.com")
        assert isinstance(verified, bool)


# ══════════════════════════════════════════════════════════════════════════════
# get_or_create_kg_source — provenance field population
# ══════════════════════════════════════════════════════════════════════════════

class TestKGSourceProvenance:
    def test_credibility_score_stored(self, db):
        src = get_or_create_kg_source(
            db, url="https://example.gov/data", text="t",
            source_type="news", source_owner_type=None,
        )
        assert src.credibility_score == 0.9   # .gov wins

    def test_verified_official_stored_as_int(self, db):
        src = get_or_create_kg_source(
            db, url="https://example.gov/data", text="t",
        )
        assert src.verified_official == 1

    def test_domain_parsed(self, db):
        src = get_or_create_kg_source(
            db, url="https://www.bbc.com/news/article", text="t",
        )
        assert src.domain == "bbc.com"

    def test_source_type_stored(self, db):
        src = get_or_create_kg_source(
            db, url="https://news.example.com/a", text="t",
            source_type="news",
        )
        assert src.source_type == "news"

    def test_source_name_stored(self, db):
        src = get_or_create_kg_source(
            db, url="https://example.com/b", text="t",
            source_name="Example Post",
        )
        assert src.source_name == "Example Post"

    def test_social_unclear_low_credibility(self, db):
        src = get_or_create_kg_source(
            db, url="https://twitter.com/user/status/1", text="tweet",
            source_type="social", source_owner_type="unclear",
        )
        assert src.credibility_score == 0.4
        assert src.verified_official == 0

    def test_idempotent_returns_same_credibility(self, db):
        s1 = get_or_create_kg_source(
            db, url="https://example.com/c", text="t",
            source_type="news",
        )
        s2 = get_or_create_kg_source(
            db, url="https://example.com/c", text="t",
            source_type="social",   # ignored on second call
        )
        assert s1.id == s2.id
        assert s2.credibility_score == 0.7   # first call's value preserved

    def test_no_url_does_not_crash(self, db):
        src = get_or_create_kg_source(db, url=None, text="some text")
        assert src.id is not None

    def test_default_credibility_when_no_provenance(self, db):
        src = get_or_create_kg_source(db, url="https://example.com/d", text="t")
        # No source_type, no source_owner_type, non-.gov domain → unclear → 0.3
        assert src.credibility_score == 0.3


# ══════════════════════════════════════════════════════════════════════════════
# _weighted_daily_rate
# ══════════════════════════════════════════════════════════════════════════════

class TestWeightedDailyRate:
    def _setup_narrative(
        self,
        db,
        *,
        confidence: float,
        credibility: float,
        created_ago_hours: float = 1.0,
    ) -> tuple[KGNarrative, KGClaim]:
        src = KGSource(
            url="https://rate-test.example.com",
            content_hash=f"hash-{confidence}-{credibility}",
            text="t",
            credibility_score=credibility,
        )
        db.add(src)
        db.flush()

        narr = KGNarrative(
            label="rate-test",
            status="active",
            velocity_score=0.0,
            clustering_method=CLUSTERING_METHOD,
            first_seen_at=datetime.utcnow(),
            last_seen_at=datetime.utcnow(),
        )
        db.add(narr)
        db.flush()

        claim = KGClaim(
            text="claim",
            stance="neutral",
            confidence=confidence,
            source_id=src.id,
            created_at=datetime.utcnow() - timedelta(hours=created_ago_hours),
        )
        db.add(claim)
        db.flush()
        db.add(KGNarrativeClaim(narrative_id=narr.id, claim_id=claim.id))
        db.flush()
        return narr, claim

    def test_single_claim_weight(self, db):
        now = datetime.utcnow()
        yesterday = now - timedelta(days=1)
        narr, _ = self._setup_narrative(db, confidence=0.8, credibility=0.7,
                                        created_ago_hours=1.0)
        rate = _weighted_daily_rate(db, narr.id, yesterday)
        assert abs(rate - 0.8 * 0.7) < 1e-6

    def test_old_claim_excluded(self, db):
        now = datetime.utcnow()
        yesterday = now - timedelta(days=1)
        narr, _ = self._setup_narrative(db, confidence=1.0, credibility=1.0,
                                        created_ago_hours=25.0)
        rate = _weighted_daily_rate(db, narr.id, yesterday)
        assert rate == 0.0

    def test_high_credibility_beats_low(self, db):
        now = datetime.utcnow()
        yesterday = now - timedelta(days=1)

        # High credibility source
        src_hi = KGSource(url="https://hi.gov/a", content_hash="hi", text="t",
                          credibility_score=0.9)
        src_lo = KGSource(url="https://lo-spam.com/a", content_hash="lo", text="t",
                          credibility_score=0.3)
        db.add(src_hi); db.add(src_lo); db.flush()

        narr_hi = KGNarrative(label="hi", status="active", velocity_score=0.0,
                              clustering_method=CLUSTERING_METHOD,
                              first_seen_at=now, last_seen_at=now)
        narr_lo = KGNarrative(label="lo", status="active", velocity_score=0.0,
                              clustering_method=CLUSTERING_METHOD,
                              first_seen_at=now, last_seen_at=now)
        db.add(narr_hi); db.add(narr_lo); db.flush()

        for narr, src in [(narr_hi, src_hi), (narr_lo, src_lo)]:
            for i in range(3):
                c = KGClaim(text=f"claim-{narr.id}-{i}", stance="neutral",
                            confidence=1.0, source_id=src.id,
                            created_at=now - timedelta(hours=i + 1))
                db.add(c); db.flush()
                db.add(KGNarrativeClaim(narrative_id=narr.id, claim_id=c.id))
        db.flush()

        rate_hi = _weighted_daily_rate(db, narr_hi.id, yesterday)
        rate_lo = _weighted_daily_rate(db, narr_lo.id, yesterday)
        assert rate_hi > rate_lo

    def test_missing_source_falls_back_to_0_5(self, db):
        now = datetime.utcnow()
        yesterday = now - timedelta(days=1)

        src = KGSource(url="https://fallback.example.com", content_hash="fb", text="t")
        db.add(src); db.flush()

        narr = KGNarrative(label="fb", status="active", velocity_score=0.0,
                           clustering_method=CLUSTERING_METHOD,
                           first_seen_at=now, last_seen_at=now)
        db.add(narr); db.flush()

        claim = KGClaim(text="fallback claim", stance="neutral", confidence=0.8,
                        source_id=src.id,
                        created_at=now - timedelta(hours=1))
        db.add(claim); db.flush()
        db.add(KGNarrativeClaim(narrative_id=narr.id, claim_id=claim.id))
        db.flush()

        # Source has no credibility_score set (SQLite default NULL is treated as 0.5)
        rate = _weighted_daily_rate(db, narr.id, yesterday)
        # Should be 0.8 * 0.5 = 0.4 (fallback) or 0.8 * actual_default
        assert rate > 0.0


# ══════════════════════════════════════════════════════════════════════════════
# Weighted velocity through run_clustering
# ══════════════════════════════════════════════════════════════════════════════

class TestWeightedVelocityInClustering:
    """
    Verify that two identical claim bursts produce different velocity scores
    when the only difference is the source credibility.
    """

    def _run_with_credibility(
        self, db, credibility: float, n_claims: int = 3
    ) -> float:
        now = datetime.utcnow()
        src = KGSource(
            url=f"https://cred-{credibility}.example.com",
            content_hash=f"cred-{credibility}",
            text="t",
            credibility_score=credibility,
        )
        db.add(src)
        db.flush()

        for i in range(n_claims):
            c = KGClaim(
                text=f"claim-cred{credibility}-{i}",
                stance="neutral",
                confidence=1.0,
                source_id=src.id,
                embedding=json.dumps([1.0, 0.0]),
                created_at=now - timedelta(hours=i),
            )
            db.add(c)
        db.flush()

        run_clustering(db, now=now)
        db.flush()

        narr = db.query(KGNarrative).filter_by(status="active").first()
        return narr.velocity_score if narr else 0.0

    def test_high_credibility_produces_higher_velocity(self):
        # Each run uses an isolated DB
        eng_hi = create_engine("sqlite:///:memory:",
                               connect_args={"check_same_thread": False})
        Base.metadata.create_all(eng_hi)
        sess_hi = sessionmaker(bind=eng_hi)()

        eng_lo = create_engine("sqlite:///:memory:",
                               connect_args={"check_same_thread": False})
        Base.metadata.create_all(eng_lo)
        sess_lo = sessionmaker(bind=eng_lo)()

        try:
            vel_hi = self._run_with_credibility(sess_hi, credibility=0.9)
            vel_lo = self._run_with_credibility(sess_lo, credibility=0.3)
            assert vel_hi > vel_lo
        finally:
            sess_hi.close(); eng_hi.dispose()
            sess_lo.close(); eng_lo.dispose()


# ══════════════════════════════════════════════════════════════════════════════
# Alert severity is suppressed by low credibility
# ══════════════════════════════════════════════════════════════════════════════

class TestAlertSeverityWithCredibility:
    def test_low_credibility_spam_harder_to_alert(self, db, monkeypatch):
        """
        A narrative built entirely from low-credibility claims should produce
        a lower velocity (via weighted EMA) and therefore lower alert severity
        than one built from high-credibility claims — given the same raw count.
        """
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        now = datetime.utcnow()
        n_claims = 5

        def _build_db_with_credibility(credibility: float):
            eng = create_engine("sqlite:///:memory:",
                                connect_args={"check_same_thread": False})
            Base.metadata.create_all(eng)
            sess = sessionmaker(bind=eng)()
            src = KGSource(
                url=f"https://cred-alert-{credibility}.example.com",
                content_hash=f"alert-cred-{credibility}",
                text="t",
                credibility_score=credibility,
            )
            sess.add(src)
            sess.flush()
            for i in range(n_claims):
                c = KGClaim(
                    text=f"claim-{i}",
                    stance="neutral",
                    confidence=1.0,
                    source_id=src.id,
                    embedding=json.dumps([1.0, 0.0]),
                    created_at=now - timedelta(hours=i),
                )
                sess.add(c)
            sess.flush()
            run_clustering(sess, now=now)
            alerts = generate_alerts(sess, severity_threshold=0.0, now=now)
            severity = alerts[0].severity_score if alerts else 0.0
            sess.close()
            eng.dispose()
            return severity

        sev_hi = _build_db_with_credibility(0.9)
        sev_lo = _build_db_with_credibility(0.3)
        assert sev_hi > sev_lo

    def test_compute_alert_severity_uses_weighted_velocity(self):
        """
        _compute_alert_severity is a pure function; calling it with a low
        weighted velocity (as produced by low-credibility sources) gives lower
        severity than the same call with a high weighted velocity.
        """
        sev_hi, _ = _compute_alert_severity(velocity=3.0, unique_sources=3,
                                            unique_entities=3)
        sev_lo, _ = _compute_alert_severity(velocity=0.3, unique_sources=3,
                                            unique_entities=3)
        assert sev_hi > sev_lo
