from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CampaignConfig, SourceItem


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    session.add(CampaignConfig(
        candidate_name="Alex Rivera",
        office="Assembly",
        district="Queens Assembly District 30",
        location="Queens",
    ))
    session.commit()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def test_noisy_wrapper_text_gets_low_extraction_quality():
    from app.services.ingestion import _clean_html_with_quality

    html = """
    <html><head><title>Queens candidate launches housing plan</title></head>
    <body>
      <main class="content">
        <p>Return to Homepage</p>
        <p>Top Stories: Who is Dr. Nicole Saphier?</p>
        <p>Latest News</p>
        <p>Trending: celebrity restaurant weather lottery</p>
        <p>Alex Rivera announced a Queens housing plan for Assembly District 30 voters.</p>
        <p>Subscribe to our newsletter</p>
      </main>
    </body></html>
    """
    title, body, score, label, reasons = _clean_html_with_quality(html)

    assert title == "Queens candidate launches housing plan"
    assert "Return to Homepage" not in body
    assert label in {"poor", "mixed"}
    assert score < 75
    assert reasons


def test_clean_article_text_scores_good():
    from app.services.ingestion import _clean_html_with_quality

    html = """
    <article>
      <p>Alex Rivera announced a housing affordability plan in Queens on Tuesday.</p>
      <p>The campaign said the proposal focuses on tenant protections, rent pressure, and local affordability in Assembly District 30.</p>
      <p>Rivera framed the plan as a response to cost-of-living concerns raised by working families.</p>
    </article>
    """
    _title, body, score, label, _reasons = _clean_html_with_quality(html)

    assert "housing affordability plan" in body
    assert label == "good"
    assert score >= 75


def test_poor_extraction_downgrades_evidence_and_respond_confidence(db):
    from app.services.race_relevance import apply_relevance
    from app.services.scoring import compute_evidence_score, compute_credibility_score

    source = SourceItem(
        title="Jordan Lee attacks Alex Rivera in Queens",
        raw_text="Return to Homepage Top Stories Latest News Jordan Lee says Alex Rivera failed tenants in Queens Assembly District 30.",
        source_name="Wrapped News",
        source_url="https://example.com/story",
        source_type="news",
        published_at=datetime.utcnow(),
        extraction_quality_score=20,
        extraction_quality_label="poor",
    )
    db.add(source)
    db.flush()
    apply_relevance(db, source)
    source.evidence_score = compute_evidence_score(source)
    source.credibility_score = compute_credibility_score(source)

    assert source.actionability_label != "respond"
    assert source.evidence_score < 60
    assert source.credibility_score < 60


def test_poor_extraction_snapshot_is_cautious(db):
    from app.services.snapshots import build_source_snapshot

    source = SourceItem(
        title="Queens candidate launches housing plan",
        raw_text="Return to Homepage Top Stories Who is Dr. Nicole Saphier? Subscribe now.",
        summary="Return to Homepage Top Stories Who is Dr. Nicole Saphier?",
        source_name="Wrapped News",
        source_url="https://example.com/story",
        source_type="news",
        published_at=datetime.utcnow(),
        race_relevance_score=65,
        race_relevance_label="high",
        actionability_label="review",
        evidence_score=35,
        credibility_score=35,
        content_category="campaign",
        extraction_quality_score=20,
        extraction_quality_label="poor",
    )
    db.add(source)
    db.commit()

    snapshot = build_source_snapshot(db, source)

    assert "Extraction quality is weak" in snapshot.why_it_matters
    assert snapshot.what_happened == source.title
    assert snapshot.evidence_summary == "weak"
    assert snapshot.key_claim_or_quote is None


def test_poor_extraction_uses_fallback_summary(db):
    from app.services.snapshots import build_source_summary

    source = SourceItem(
        title="Queens candidate launches housing plan",
        raw_text="Return to Homepage Top Stories Latest News Jordan Lee says Alex Rivera failed tenants in Queens.",
        summary="Return to Homepage Top Stories Latest News Jordan Lee says Alex Rivera failed tenants in Queens.",
        source_name="Wrapped News",
        source_url="https://example.com/story",
        source_type="news",
        published_at=datetime.utcnow(),
        extraction_quality_score=20,
        extraction_quality_label="poor",
    )

    summary = build_source_summary(source)

    assert "Return to Homepage" not in summary
    assert summary.startswith("Queens candidate launches housing plan.")
    assert "Clean summary unavailable because article extraction quality is poor" in summary


def test_good_extraction_keeps_normal_summary(db):
    from app.services.snapshots import build_source_summary

    source = SourceItem(
        title="Queens candidate launches housing plan",
        raw_text="Alex Rivera announced a housing affordability plan in Queens on Tuesday.",
        summary="Alex Rivera announced a housing affordability plan in Queens on Tuesday.",
        source_name="Local News",
        source_url="https://example.com/story",
        source_type="news",
        published_at=datetime.utcnow(),
        extraction_quality_score=90,
        extraction_quality_label="good",
    )

    summary = build_source_summary(source)

    assert summary == "Alex Rivera announced a housing affordability plan in Queens on Tuesday."


def test_geography_is_cautious_with_poor_extraction_but_uses_title_metadata(db):
    from app.services.snapshots import build_source_snapshot

    source = SourceItem(
        title="Queens Assembly District 30 ballot challenge",
        raw_text="Return to Homepage Top Stories Latest celebrity news.",
        source_name="Wrapped News",
        source_url="https://example.com/story",
        source_type="news",
        published_at=datetime.utcnow(),
        race_relevance_score=60,
        race_relevance_label="high",
        actionability_label="review",
        evidence_score=30,
        credibility_score=30,
        content_category="campaign",
        geo_relevance="none",
        extraction_quality_score=20,
        extraction_quality_label="poor",
    )
    db.add(source)
    db.commit()

    snapshot = build_source_snapshot(db, source)

    assert "title or source metadata" in snapshot.geography_summary


def test_committee_chrome_page_is_pruned_and_downgraded():
    from app.services.ingestion import _clean_html_with_quality

    html = """
    <html>
      <head><title>NRCC launches attack on Alex Rivera</title></head>
      <body>
        <main>
          <p>NRCC launches attack on Alex Rivera</p>
          <p>Donate now. Get involved. Text VICTORY to 12345.</p>
          <p>Paid for by the National Republican Congressional Committee. Official campaign headquarters.</p>
          <p>The NRCC says Alex Rivera failed Queens families and must be stopped.</p>
          <p>Sign up now. Learn more. Join the team.</p>
        </main>
      </body>
    </html>
    """

    title, body, score, label, reasons = _clean_html_with_quality(html)

    assert title == "NRCC launches attack on Alex Rivera"
    assert "Donate now" not in body
    assert "Text VICTORY" not in body
    assert "The NRCC says Alex Rivera failed Queens families and must be stopped." in body
    assert label in {"poor", "mixed"}
    assert score < 75
    assert reasons


def test_poor_committee_snapshot_uses_owner_aware_generic_language(db):
    from app.services.snapshots import build_source_snapshot

    source = SourceItem(
        title="NRCC launches attack on Alex Rivera",
        raw_text="NRCC launches attack on Alex Rivera NRCC launches attack on Alex Rivera",
        summary="NRCC launches attack on Alex Rivera NRCC launches attack on Alex Rivera",
        source_name="National Republican Congressional Committee",
        source_url="https://nrcc.org/press/attack",
        source_type="news",
        source_owner_type="party_committee_statement",
        source_owner_confidence="high",
        published_at=datetime.utcnow(),
        race_relevance_score=70,
        race_relevance_label="high",
        actionability_label="respond",
        evidence_score=35,
        credibility_score=35,
        content_category="campaign",
        extraction_quality_score=20,
        extraction_quality_label="poor",
    )
    db.add(source)
    db.commit()

    snapshot = build_source_snapshot(db, source)

    assert snapshot.what_happened == "Party committee statement targeting Alex Rivera."
    assert snapshot.key_claim_or_quote is None
