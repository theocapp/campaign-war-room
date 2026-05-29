"""
Regression test for the promote_cluster() bug where promoted frames
appeared with zero activity until the periodic rematch caught up.

Bug:    candidate_frames.source_item_id points at the article that
        triggered each suggestion. promote_cluster() created the frame
        but never wrote FCM/NFM rows linking those articles to the
        frame, so dashboards showed 0 articles / 0 outlets immediately
        after promotion.

Fix:    _backfill_evidence_for_promoted_frame() walks the candidate_frames'
        source_item_ids, writes one FCM per distinct story_cluster_id and
        one NFM per distinct source_item_id.

These tests use a real in-memory SQLite DB so the unique-constraint and
ON CONFLICT IGNORE behavior get exercised end-to-end.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base, CandidateFrame, FrameClusterMatch, NarrativeFrame,
    NarrativeFrameMention, SourceItem, StoryCluster,
)
from app.services.candidate_frame_promoter import (
    _backfill_evidence_for_promoted_frame, promote_cluster,
)


@pytest.fixture()
def db():
    """Fresh in-memory SQLite session for each test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def _make_cluster_and_item(db, *, item_id: int, cluster_suffix: str, title: str, published_at: datetime):
    """Create a SourceItem in its own story_cluster — simulates ingestion."""
    cluster_id = f"source-{cluster_suffix}"
    item = SourceItem(
        id=item_id,
        title=title,
        source_type="news",
        story_cluster_id=cluster_id,
        published_at=published_at,
        created_at=published_at,
    )
    db.add(item)
    cluster = StoryCluster(
        id=cluster_id,
        seed_source_item_id=item_id,
        representative_source_item_id=item_id,
        title_representative=title,
        first_seen_at=published_at,
        last_seen_at=published_at,
        article_count=1,
        outlet_count=0,
        source_diversity_score=0.0,
    )
    db.add(cluster)
    db.flush()
    return item, cluster


def _make_candidate(db, *, cf_id: int, source_item_id: int, suggested_name: str, evidence: str):
    """Stage one candidate_frame row (what the LLM would have written during scoring)."""
    cf = CandidateFrame(
        id=cf_id,
        source_item_id=source_item_id,
        suggested_name=suggested_name,
        owner_type_hint="candidate",
        evidence_quote=evidence,
        reasoning="test reason",
        created_at=datetime.utcnow(),
    )
    db.add(cf)
    db.flush()
    return cf


# ─────────────────────────────────────────────────────────────────────────
# Core behavior
# ─────────────────────────────────────────────────────────────────────────

def test_promote_cluster_writes_fcm_per_source_article(db):
    """Each candidate_frame's source_item should become a FCM row on the
    new frame. Three distinct articles → three FCM rows."""
    now = datetime.utcnow()
    _make_cluster_and_item(db, item_id=1, cluster_suffix="A", title="Article A", published_at=now)
    _make_cluster_and_item(db, item_id=2, cluster_suffix="B", title="Article B", published_at=now)
    _make_cluster_and_item(db, item_id=3, cluster_suffix="C", title="Article C", published_at=now)
    _make_candidate(db, cf_id=10, source_item_id=1, suggested_name="Test Frame", evidence="quote A")
    _make_candidate(db, cf_id=11, source_item_id=2, suggested_name="Test Frame", evidence="quote B")
    _make_candidate(db, cf_id=12, source_item_id=3, suggested_name="Test Frame", evidence="quote C")
    db.commit()

    frame = promote_cluster(
        db,
        suggested_name="Test Frame",
        suggested_description="desc",
        owner_type="candidate",
        candidate_frame_ids=[10, 11, 12],
    )

    fcm = db.query(FrameClusterMatch).filter(FrameClusterMatch.frame_id == frame.id).all()
    assert len(fcm) == 3, f"expected 3 FCM rows (one per source article), got {len(fcm)}"
    cluster_ids = {m.story_cluster_id for m in fcm}
    assert cluster_ids == {"source-A", "source-B", "source-C"}


def test_promote_cluster_writes_nfm_per_source_article(db):
    """Each candidate_frame's evidence_quote should land as NFM
    extracted_text — that's what powers the detail page's quotes."""
    now = datetime.utcnow()
    _make_cluster_and_item(db, item_id=1, cluster_suffix="A", title="A", published_at=now)
    _make_cluster_and_item(db, item_id=2, cluster_suffix="B", title="B", published_at=now)
    _make_candidate(db, cf_id=10, source_item_id=1, suggested_name="F", evidence="VERBATIM QUOTE A")
    _make_candidate(db, cf_id=11, source_item_id=2, suggested_name="F", evidence="VERBATIM QUOTE B")
    db.commit()

    frame = promote_cluster(
        db,
        suggested_name="F",
        suggested_description="",
        owner_type="candidate",
        candidate_frame_ids=[10, 11],
    )

    nfm = db.query(NarrativeFrameMention).filter(NarrativeFrameMention.frame_id == frame.id).all()
    assert len(nfm) == 2
    quotes = {m.extracted_text for m in nfm}
    assert quotes == {"VERBATIM QUOTE A", "VERBATIM QUOTE B"}
    # All NFM should be confidence=85 (the promoter's high-confidence default)
    assert all(m.confidence == 85 for m in nfm)
    # matched_by is the provenance tag — distinct from runtime matcher
    assert all(m.matched_by == "promoted_from_candidate" for m in nfm)


def test_multiple_candidates_same_cluster_make_one_fcm(db):
    """Two candidate_frames pointing at articles in the SAME cluster should
    produce ONE FCM row, not two. (Articles in the same cluster are one
    story event; FCM is keyed (frame_id, cluster_id) uniquely.)"""
    now = datetime.utcnow()
    item1, cluster = _make_cluster_and_item(db, item_id=1, cluster_suffix="shared", title="A1", published_at=now)
    # Manually create a second SourceItem in the SAME cluster (no helper for that)
    item2 = SourceItem(
        id=2, title="A2", source_type="news",
        story_cluster_id="source-shared",  # same cluster
        published_at=now, created_at=now,
    )
    db.add(item2)
    _make_candidate(db, cf_id=10, source_item_id=1, suggested_name="F", evidence="q1")
    _make_candidate(db, cf_id=11, source_item_id=2, suggested_name="F", evidence="q2")
    db.commit()

    frame = promote_cluster(db, suggested_name="F", suggested_description="", owner_type="candidate", candidate_frame_ids=[10, 11])

    fcm = db.query(FrameClusterMatch).filter(FrameClusterMatch.frame_id == frame.id).all()
    assert len(fcm) == 1, "two candidates in same cluster should produce one FCM"
    # But both NFM rows still exist — one per source article
    nfm = db.query(NarrativeFrameMention).filter(NarrativeFrameMention.frame_id == frame.id).all()
    assert len(nfm) == 2


def test_candidate_with_missing_source_item_doesnt_crash(db):
    """If a candidate_frame.source_item_id points at a deleted source_item,
    skip it gracefully — don't bring down the whole promotion."""
    now = datetime.utcnow()
    # Valid candidate
    _make_cluster_and_item(db, item_id=1, cluster_suffix="A", title="A", published_at=now)
    _make_candidate(db, cf_id=10, source_item_id=1, suggested_name="F", evidence="q")
    # Orphan candidate — source_item_id 999 doesn't exist
    _make_candidate(db, cf_id=11, source_item_id=999, suggested_name="F", evidence="missing")
    db.commit()

    frame = promote_cluster(db, suggested_name="F", suggested_description="", owner_type="candidate", candidate_frame_ids=[10, 11])

    # The valid one should land; the orphan should silently skip
    fcm = db.query(FrameClusterMatch).filter(FrameClusterMatch.frame_id == frame.id).all()
    assert len(fcm) == 1
    assert fcm[0].story_cluster_id == "source-A"


def test_candidate_with_unclustered_source_item_skipped(db):
    """If a source_item exists but story_cluster_id is NULL, skip FCM
    (no cluster to point at) but still write the NFM (uses source_item_id)."""
    now = datetime.utcnow()
    # Item without a cluster
    item = SourceItem(id=1, title="Unclustered", source_type="news",
                      story_cluster_id=None, published_at=now, created_at=now)
    db.add(item)
    _make_candidate(db, cf_id=10, source_item_id=1, suggested_name="F", evidence="q")
    db.commit()

    frame = promote_cluster(db, suggested_name="F", suggested_description="", owner_type="candidate", candidate_frame_ids=[10])

    fcm = db.query(FrameClusterMatch).filter(FrameClusterMatch.frame_id == frame.id).all()
    nfm = db.query(NarrativeFrameMention).filter(NarrativeFrameMention.frame_id == frame.id).all()
    assert len(fcm) == 0, "no cluster → no FCM"
    assert len(nfm) == 1, "NFM still keyed on source_item_id, so it lands"


def test_candidate_frames_marked_resolved_after_promotion(db):
    """The existing 'mark as resolved' behavior must keep working after
    backfill added — it runs after the FCM/NFM writes."""
    now = datetime.utcnow()
    _make_cluster_and_item(db, item_id=1, cluster_suffix="A", title="A", published_at=now)
    _make_candidate(db, cf_id=10, source_item_id=1, suggested_name="F", evidence="q")
    db.commit()

    frame = promote_cluster(db, suggested_name="F", suggested_description="", owner_type="candidate", candidate_frame_ids=[10])

    cf = db.query(CandidateFrame).filter_by(id=10).first()
    assert cf.resolved_to_frame_id == frame.id
    assert cf.resolved_at is not None


def test_backfill_idempotent_via_unique_constraints(db):
    """If somehow the same backfill is invoked twice (re-promotion, retry,
    test fixture issue), it shouldn't double-count. FCM upsert is
    idempotent; NFM uses INSERT OR IGNORE."""
    now = datetime.utcnow()
    _make_cluster_and_item(db, item_id=1, cluster_suffix="A", title="A", published_at=now)
    _make_candidate(db, cf_id=10, source_item_id=1, suggested_name="F", evidence="q")
    db.commit()

    frame = promote_cluster(db, suggested_name="F", suggested_description="", owner_type="candidate", candidate_frame_ids=[10])

    # Now re-run backfill against the same frame
    _backfill_evidence_for_promoted_frame(db, frame_id=frame.id, candidate_frame_ids=[10], ts=datetime.utcnow())
    db.commit()

    fcm = db.query(FrameClusterMatch).filter(FrameClusterMatch.frame_id == frame.id).all()
    nfm = db.query(NarrativeFrameMention).filter(NarrativeFrameMention.frame_id == frame.id).all()
    assert len(fcm) == 1, "FCM upsert should not duplicate"
    assert len(nfm) == 1, "NFM unique constraint should prevent duplicate"
