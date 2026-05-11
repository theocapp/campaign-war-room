"""
Tests for the narrative detection engine (app/knowledge_graph/narrative_engine.py).

All tests run against an in-memory SQLite database.  No LLM calls are made:
tests inject pre-built embedding vectors directly into claim.embedding so
the clustering logic can be verified without any randomness.

Embedding geometry used in the tests
─────────────────────────────────────
We work in 2-D so the angles (and thus cosine similarities) are exact.

    v(θ) = [cos(θ), sin(θ)]    cos_sim(v(0), v(θ)) = cos(θ)

THRESHOLD = 0.82 ≈ cos(35°)

  CLOSE:   θ = 15° → cos = 0.966  (well above threshold)
  CLOSE2:  θ = 25° → cos = 0.906  (above threshold)
  DISTANT: θ = 55° → cos = 0.574  (below threshold)
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
from app.knowledge_graph.narrative_engine import (
    ALERT_COOLDOWN_HOURS,
    ALERT_ENTITY_SURGE,
    ALERT_SEVERITY_THRESHOLD,
    ALERT_SOURCE_SURGE,
    ALERT_VELOCITY_SPIKE,
    CLUSTERING_METHOD,
    INACTIVE_DAYS,
    MERGE_THRESHOLD,
    SIMILARITY_THRESHOLD,
    ClusteringReport,
    EmergingNarrative,
    _compute_alert_severity,
    _hash_embed,
    _narrative_label,
    _normalize,
    _recent_alert_exists,
    apply_inactivity_decay,
    cosine_similarity,
    embed_claim,
    generate_alerts,
    get_active_alerts,
    get_emerging_narratives,
    load_embedding,
    merge_narratives,
    run_clustering,
    store_embedding,
    vec_mean,
)
from app.knowledge_graph.orm import (
    KGAlert,
    KGClaim,
    KGClaimEntity,
    KGEntity,
    KGNarrative,
    KGNarrativeClaim,
    KGSource,
)
from app.models import SourceItem  # noqa: F401 — registers core tables


# ── Helpers ───────────────────────────────────────────────────────────────────

def _v(angle_deg: float) -> list[float]:
    """2-D unit vector at *angle_deg* degrees from [1, 0]."""
    r = math.radians(angle_deg)
    return [math.cos(r), math.sin(r)]


CLOSE   = _v(15)   # cos(15°) ≈ 0.966 — above SIMILARITY_THRESHOLD (0.82)
CLOSE2  = _v(25)   # cos(25°) ≈ 0.906 — above SIMILARITY_THRESHOLD
DISTANT = _v(55)   # cos(55°) ≈ 0.574 — below SIMILARITY_THRESHOLD


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    sess = Session()
    yield sess
    sess.close()
    engine.dispose()


def _source(db, *, url: str = "https://example.com/a", text: str = "t") -> KGSource:
    src = KGSource(url=url, content_hash=url + text, text=text)
    db.add(src)
    db.flush()
    return src


def _claim(
    db,
    source: KGSource,
    text: str = "claim text",
    vec: Optional[list[float]] = None,
    created_at: Optional[datetime] = None,
) -> KGClaim:
    c = KGClaim(
        text=text,
        stance="neutral",
        confidence=0.8,
        source_id=source.id,
        created_at=created_at or datetime.utcnow(),
    )
    if vec is not None:
        c.embedding = json.dumps(vec)
    db.add(c)
    db.flush()
    return c


# ══════════════════════════════════════════════════════════════════════════════
# Pure math utilities
# ══════════════════════════════════════════════════════════════════════════════

class TestVectorMath:
    def test_normalize_produces_unit_vector(self):
        v = _normalize([3.0, 4.0])
        assert abs(math.sqrt(sum(x * x for x in v)) - 1.0) < 1e-9

    def test_normalize_zero_vector_safe(self):
        v = _normalize([0.0, 0.0, 0.0])
        assert v == [0.0, 0.0, 0.0]

    def test_cosine_identical_vectors(self):
        v = _v(30)
        assert abs(cosine_similarity(v, v) - 1.0) < 1e-9

    def test_cosine_orthogonal_vectors(self):
        assert abs(cosine_similarity(_v(0), _v(90))) < 1e-9

    def test_cosine_known_angle(self):
        assert abs(cosine_similarity(_v(0), CLOSE) - math.cos(math.radians(15))) < 1e-6

    def test_cosine_dimension_mismatch_returns_zero(self):
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0

    def test_cosine_empty_returns_zero(self):
        assert cosine_similarity([], []) == 0.0

    def test_vec_mean_single(self):
        v = [1.0, 2.0]
        assert vec_mean([v]) == v

    def test_vec_mean_two_vectors(self):
        result = vec_mean([[1.0, 0.0], [0.0, 1.0]])
        assert abs(result[0] - 0.5) < 1e-9
        assert abs(result[1] - 0.5) < 1e-9


# ══════════════════════════════════════════════════════════════════════════════
# Embedding utilities
# ══════════════════════════════════════════════════════════════════════════════

class TestHashEmbed:
    def test_deterministic(self):
        assert _hash_embed("hello world") == _hash_embed("hello world")

    def test_unit_norm(self):
        v = _hash_embed("some text")
        mag = math.sqrt(sum(x * x for x in v))
        assert abs(mag - 1.0) < 1e-6

    def test_different_texts_differ(self):
        assert _hash_embed("housing crisis") != _hash_embed("police reform")

    def test_expected_dimension(self):
        from app.knowledge_graph.narrative_engine import HASH_EMBED_DIM
        assert len(_hash_embed("x")) == HASH_EMBED_DIM


class TestEmbedClaim:
    def test_fallback_when_no_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        vec = embed_claim("housing affordability crisis")
        assert len(vec) > 0
        mag = math.sqrt(sum(x * x for x in vec))
        assert abs(mag - 1.0) < 1e-6


class TestEmbeddingIO:
    def test_store_and_load_roundtrip(self, db):
        src = _source(db)
        c = _claim(db, src)
        vec = [0.6, 0.8]
        store_embedding(c, vec)
        assert load_embedding(c) == pytest.approx(vec)

    def test_load_returns_none_when_empty(self, db):
        src = _source(db)
        c = _claim(db, src)
        assert load_embedding(c) is None

    def test_load_ignores_corrupt_json(self, db):
        src = _source(db)
        c = _claim(db, src)
        c.embedding = "not-valid-json"
        assert load_embedding(c) is None


# ══════════════════════════════════════════════════════════════════════════════
# Label helper
# ══════════════════════════════════════════════════════════════════════════════

class TestNarrativeLabel:
    def test_short_text_unchanged(self):
        assert _narrative_label("short text") == "short text"

    def test_truncates_to_max_words(self):
        words = ["word"] * 20
        label = _narrative_label(" ".join(words))
        assert label.count("word") <= 12

    def test_adds_ellipsis_when_truncated(self):
        words = ["word"] * 20
        label = _narrative_label(" ".join(words))
        assert label.endswith("…")


# ══════════════════════════════════════════════════════════════════════════════
# Clustering: basic behaviour
# ══════════════════════════════════════════════════════════════════════════════

class TestRunClusteringBasic:
    def test_no_claims_returns_empty_report(self, db):
        report = run_clustering(db)
        assert report.claims_processed == 0
        assert report.narratives_created == 0

    def test_single_claim_creates_one_narrative(self, db):
        src = _source(db)
        _claim(db, src, vec=CLOSE)
        report = run_clustering(db)
        assert report.claims_processed == 1
        assert report.narratives_created == 1
        assert report.links_added == 1
        assert db.query(KGNarrative).count() == 1
        assert db.query(KGNarrativeClaim).count() == 1

    def test_narrative_has_correct_metadata(self, db):
        src = _source(db)
        _claim(db, src, text="The mayor raised taxes on housing.", vec=CLOSE)
        run_clustering(db)
        narr = db.query(KGNarrative).first()
        assert narr.clustering_method == CLUSTERING_METHOD
        assert narr.first_seen_at is not None
        assert narr.last_seen_at is not None
        assert narr.embedding is not None
        centroid = json.loads(narr.embedding)
        assert len(centroid) == 2

    def test_already_linked_claim_is_not_reprocessed(self, db):
        src = _source(db)
        c = _claim(db, src, vec=CLOSE)
        # First run: creates narrative and links claim
        run_clustering(db)
        # Second run: claim already linked → no new narrative, no new link
        report2 = run_clustering(db)
        assert report2.narratives_created == 0
        assert report2.links_added == 0
        assert db.query(KGNarrative).count() == 1
        assert db.query(KGNarrativeClaim).count() == 1

    def test_old_claims_outside_window_ignored(self, db):
        now = datetime.utcnow()
        src = _source(db)
        # Claim created 10 days ago, window is 7 days
        _claim(db, src, vec=CLOSE, created_at=now - timedelta(days=10))
        report = run_clustering(db, days=7, now=now)
        assert report.claims_processed == 0
        assert db.query(KGNarrative).count() == 0


# ══════════════════════════════════════════════════════════════════════════════
# Clustering: similarity-based assignment
# ══════════════════════════════════════════════════════════════════════════════

class TestClusteringSimilarity:
    def test_close_claims_merge_into_one_narrative(self, db):
        """
        Two claims with cosine similarity > threshold must share one narrative.
        CLOSE = v(15°), CLOSE2 = v(25°); cos_sim ≈ 0.906 > 0.82.
        """
        src = _source(db)
        _claim(db, src, text="claim A", vec=CLOSE)
        _claim(db, src, text="claim B", vec=CLOSE2)
        report = run_clustering(db)
        assert db.query(KGNarrative).count() == 1
        assert db.query(KGNarrativeClaim).count() == 2
        # One narrative was seeded (created), the other was attached (updated)
        assert report.narratives_created == 1
        assert report.narratives_updated == 1

    def test_distant_claims_form_separate_narratives(self, db):
        """
        Two claims with cosine similarity < threshold must produce separate narratives.
        CLOSE = v(0° effectively after normalization), DISTANT = v(55°); cos≈0.574.
        """
        src = _source(db)
        _claim(db, src, text="claim A", vec=_v(0))
        _claim(db, src, text="claim B", vec=DISTANT)
        report = run_clustering(db)
        assert db.query(KGNarrative).count() == 2
        assert report.narratives_created == 2

    def test_third_claim_joins_closest_narrative(self, db):
        """
        With two existing narratives (at 0° and 90°), a new claim at 10°
        should join the 0° narrative (cos 10° ≈ 0.985 > 0.82).
        """
        src = _source(db)
        c1 = _claim(db, src, text="claim at 0deg",  vec=_v(0))
        c2 = _claim(db, src, text="claim at 90deg", vec=_v(90))
        # Seed the two narratives first
        now = datetime.utcnow()
        run_clustering(db, now=now)
        assert db.query(KGNarrative).count() == 2

        # Add a new claim at 10° — should join the 0° narrative
        c3 = _claim(db, src, text="claim at 10deg", vec=_v(10),
                    created_at=now + timedelta(minutes=1))
        report = run_clustering(db, now=now + timedelta(minutes=1))

        assert report.narratives_created == 0   # no new narrative
        assert report.narratives_updated == 1   # joined an existing one
        assert db.query(KGNarrative).count() == 2
        assert db.query(KGNarrativeClaim).count() == 3

        # The link for c3 must point to the narrative seeded by c1 (at 0°)
        c1_narrative_id = (
            db.query(KGNarrativeClaim)
            .filter_by(claim_id=c1.id)
            .first()
            .narrative_id
        )
        c3_narrative_id = (
            db.query(KGNarrativeClaim)
            .filter_by(claim_id=c3.id)
            .first()
            .narrative_id
        )
        assert c3_narrative_id == c1_narrative_id

    def test_threshold_is_inclusive(self, db):
        """
        A claim exactly at the threshold angle should be attached (>= not >).
        cos⁻¹(0.82) ≈ 34.9°
        """
        src = _source(db)
        _claim(db, src, text="seed", vec=_v(0))
        run_clustering(db)

        # Compute the exact threshold angle
        threshold_angle = math.degrees(math.acos(SIMILARITY_THRESHOLD))
        exact_vec = _v(threshold_angle)
        now = datetime.utcnow()
        c2 = _claim(db, src, text="at threshold", vec=exact_vec,
                    created_at=now + timedelta(seconds=1))
        run_clustering(db, now=now + timedelta(seconds=1))

        assert db.query(KGNarrative).count() == 1  # merged, not separate
        assert db.query(KGNarrativeClaim).count() == 2


# ══════════════════════════════════════════════════════════════════════════════
# Centroid recomputation
# ══════════════════════════════════════════════════════════════════════════════

class TestCentroidUpdate:
    def test_centroid_is_mean_of_member_embeddings(self, db):
        """
        After clustering two claims [1,0] and [0,1], the centroid should be
        the normalised mean ≈ [0.707, 0.707].
        """
        src = _source(db)
        _claim(db, src, text="A", vec=[1.0, 0.0])
        _claim(db, src, text="B", vec=[0.0, 1.0])
        # Force them to cluster together: same direction within threshold
        # Use a very low threshold so they merge
        run_clustering(db, threshold=0.0)

        narr = db.query(KGNarrative).one()
        centroid = json.loads(narr.embedding)
        expected = _normalize([0.5, 0.5])
        assert abs(centroid[0] - expected[0]) < 1e-6
        assert abs(centroid[1] - expected[1]) < 1e-6

    def test_centroid_updates_when_third_claim_joins(self, db):
        src = _source(db)
        _claim(db, src, text="seed", vec=_v(0))
        _claim(db, src, text="close", vec=CLOSE)
        run_clustering(db, threshold=0.0)  # force into one narrative

        centroid_before = json.loads(db.query(KGNarrative).one().embedding)

        # Add a third claim at a different angle in the same narrative
        now = datetime.utcnow()
        _claim(db, src, text="third", vec=CLOSE2,
               created_at=now + timedelta(seconds=1))
        run_clustering(db, threshold=0.0, now=now + timedelta(seconds=1))

        centroid_after = json.loads(db.query(KGNarrative).one().embedding)
        # Centroid must have shifted
        assert centroid_before != centroid_after


# ══════════════════════════════════════════════════════════════════════════════
# Velocity score
# ══════════════════════════════════════════════════════════════════════════════

class TestVelocityScore:
    def test_velocity_nonzero_for_recent_claims(self, db):
        now = datetime.utcnow()
        src = _source(db)
        # Claim created 12 hours ago → within yesterday window
        _claim(db, src, vec=CLOSE, created_at=now - timedelta(hours=12))
        run_clustering(db, now=now)

        narr = db.query(KGNarrative).one()
        assert narr.velocity_score > 0.0

    def test_velocity_zero_for_old_only_claims(self, db):
        now = datetime.utcnow()
        src = _source(db)
        # All claims older than 24 h — no daily_rate contribution
        _claim(db, src, vec=CLOSE, created_at=now - timedelta(hours=36))
        run_clustering(db, now=now, days=30)  # wide window to pick up the claim

        narr = db.query(KGNarrative).one()
        # daily_rate=0; EMA: 0.3*0 + 0.7*0.0 = 0.0
        assert narr.velocity_score == 0.0

    def test_velocity_increases_with_more_recent_claims(self, db):
        now = datetime.utcnow()
        src = _source(db)

        # First run: 1 claim in last 24 h
        _claim(db, src, text="c1", vec=CLOSE, created_at=now - timedelta(hours=6))
        run_clustering(db, now=now)
        v1 = db.query(KGNarrative).one().velocity_score

        # Second run: 2 more recent claims join the same narrative
        _claim(db, src, text="c2", vec=CLOSE2,
               created_at=now + timedelta(minutes=1))
        _claim(db, src, text="c3", vec=CLOSE2,
               created_at=now + timedelta(minutes=2))
        run_clustering(db, now=now + timedelta(minutes=3))
        v2 = db.query(KGNarrative).one().velocity_score

        assert v2 > v1

    def test_velocity_ema_formula(self, db):
        """Exact EMA calculation: alpha * daily_rate + (1-alpha) * old_velocity."""
        from app.knowledge_graph.narrative_engine import EMA_ALPHA
        now = datetime.utcnow()

        # Use a source with known credibility_score=1.0 so
        # daily_rate = claim.confidence(0.8) * credibility(1.0) = 0.8 exactly.
        src = KGSource(url="https://ema-test.example.com", content_hash="ema-vc",
                       text="t", credibility_score=1.0)
        db.add(src); db.flush()

        _claim(db, src, text="seed", vec=CLOSE, created_at=now - timedelta(hours=6))
        run_clustering(db, now=now)
        narr = db.query(KGNarrative).one()
        # daily_rate = 0.8 * 1.0 = 0.8
        # velocity = EMA_ALPHA * 0.8 + (1-EMA_ALPHA) * 0 = EMA_ALPHA * 0.8
        expected = EMA_ALPHA * 0.8
        assert abs(narr.velocity_score - expected) < 1e-9

    def test_velocity_timestamps_updated(self, db):
        now = datetime(2026, 1, 1, 12, 0, 0)
        src = _source(db)
        _claim(db, src, vec=CLOSE, created_at=now - timedelta(hours=1))
        run_clustering(db, now=now)

        narr = db.query(KGNarrative).one()
        assert narr.last_seen_at == now
        assert narr.first_seen_at is not None


# ══════════════════════════════════════════════════════════════════════════════
# Emerging narrative detection
# ══════════════════════════════════════════════════════════════════════════════

class TestGetEmergingNarratives:
    def _seed_narrative(
        self,
        db,
        velocity: float,
        n_sources: int,
        n_entities: int,
        *,
        label: str = "test narrative",
    ) -> KGNarrative:
        narr = KGNarrative(
            label=label,
            velocity_score=velocity,
            clustering_method=CLUSTERING_METHOD,
            first_seen_at=datetime.utcnow(),
            last_seen_at=datetime.utcnow(),
        )
        db.add(narr)
        db.flush()

        for i in range(n_sources):
            url = f"https://source{narr.id}-{i}.example.com"
            src = KGSource(url=url, content_hash=url, text="t")
            db.add(src)
            db.flush()
            claim = KGClaim(
                text=f"claim for {narr.id} src {i}",
                stance="neutral",
                confidence=0.7,
                source_id=src.id,
            )
            db.add(claim)
            db.flush()
            db.add(KGNarrativeClaim(narrative_id=narr.id, claim_id=claim.id))

            # Attach unique entities to the first claim
            if i < n_entities:
                ent = KGEntity(entity_type="PERSON", name=f"entity-{narr.id}-{i}")
                db.add(ent)
                db.flush()
                from app.knowledge_graph.orm import KGClaimEntity
                db.add(KGClaimEntity(claim_id=claim.id, entity_id=ent.id))

        db.flush()
        return narr

    def test_excludes_zero_velocity_narratives(self, db):
        self._seed_narrative(db, velocity=0.0, n_sources=2, n_entities=2)
        results = get_emerging_narratives(db)
        assert results == []

    def test_returns_narrative_with_positive_velocity(self, db):
        self._seed_narrative(db, velocity=1.0, n_sources=2, n_entities=1)
        results = get_emerging_narratives(db)
        assert len(results) == 1

    def test_score_formula(self, db):
        """score = velocity * log(1 + sources) * log(1 + entities)"""
        # n_entities capped at n_sources in _seed_narrative; use equal values
        narr = self._seed_narrative(db, velocity=2.0, n_sources=3, n_entities=3)
        results = get_emerging_narratives(db)
        assert len(results) == 1
        expected = 2.0 * math.log1p(3) * math.log1p(3)
        assert abs(results[0].score - expected) < 1e-6

    def test_ranking_by_score(self, db):
        """Higher-scoring narrative should rank first."""
        # Narrative A: velocity=1, sources=1, entities=1 → low score
        self._seed_narrative(db, velocity=1.0, n_sources=1, n_entities=1,
                             label="low")
        # Narrative B: velocity=5, sources=4, entities=3 → high score
        self._seed_narrative(db, velocity=5.0, n_sources=4, n_entities=3,
                             label="high")
        results = get_emerging_narratives(db)
        assert results[0].narrative.label == "high"
        assert results[1].narrative.label == "low"

    def test_limit_respected(self, db):
        for i in range(5):
            self._seed_narrative(db, velocity=float(i + 1),
                                 n_sources=1, n_entities=1,
                                 label=f"n{i}")
        results = get_emerging_narratives(db, limit=3)
        assert len(results) == 3

    def test_unique_sources_and_entities_counted(self, db):
        """Verify the returned counts match what was seeded."""
        self._seed_narrative(db, velocity=1.0, n_sources=3, n_entities=2)
        result = get_emerging_narratives(db)[0]
        assert result.unique_sources == 3
        # Entities attached are min(n_sources, n_entities) = 2
        assert result.unique_entities == 2


# ══════════════════════════════════════════════════════════════════════════════
# End-to-end: embed → cluster → detect
# ══════════════════════════════════════════════════════════════════════════════

class TestEndToEnd:
    def test_full_pipeline_housing_cluster(self, db, monkeypatch):
        """
        Three 'housing' claims (controlled to be close in embedding space) and
        one 'police' claim (distant) should form exactly two narratives.
        The housing narrative should be the emerging one (more claims).
        """
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        now = datetime.utcnow()

        housing_vec  = _v(5)    # tight cluster: 5°, 10°, 20°
        housing_vec2 = _v(10)
        housing_vec3 = _v(20)
        police_vec   = _v(80)   # cos(80°-5°) = cos(75°) ≈ 0.259 < 0.82

        src_a = KGSource(url="https://a.com", content_hash="a", text="t")
        src_b = KGSource(url="https://b.com", content_hash="b", text="t")
        db.add(src_a)
        db.add(src_b)
        db.flush()

        for i, (src, vec, txt) in enumerate([
            (src_a, housing_vec,  "Rents are rising due to housing shortage"),
            (src_a, housing_vec2, "Affordable housing units disappearing"),
            (src_b, housing_vec3, "Mayor refuses housing policy reform"),
            (src_b, police_vec,   "Police budget cuts spark safety debate"),
        ]):
            c = KGClaim(text=txt, stance="neutral", confidence=0.8,
                        source_id=src.id,
                        created_at=now - timedelta(hours=i))
            c.embedding = json.dumps(vec)
            db.add(c)
        db.flush()

        report = run_clustering(db, now=now)
        db.commit()

        assert db.query(KGNarrative).count() == 2
        # 3 housing claims → 1 housing narrative (created + 2 attached)
        assert report.narratives_created == 2
        assert report.links_added == 4

        # The narrative with 3 claims should have higher velocity
        counts = {
            narr.id: db.query(KGNarrativeClaim)
                       .filter_by(narrative_id=narr.id)
                       .count()
            for narr in db.query(KGNarrative).all()
        }
        assert max(counts.values()) == 3
        assert min(counts.values()) == 1


# ══════════════════════════════════════════════════════════════════════════════
# Narrative merge
# ══════════════════════════════════════════════════════════════════════════════

# Merge geometry:
#   MERGE_THRESHOLD = 0.90 ≈ cos(26°)
#   NEAR:  θ = 5°   → cos(5°-0°)  = cos(5°)  ≈ 0.996   (above merge threshold)
#   FAR:   θ = 60°  → cos(60°-0°) = cos(60°) = 0.500   (below merge threshold)

_NEAR = _v(5)   # close enough to _v(0) to trigger a merge
_FAR  = _v(60)  # too distant to merge with _v(0)


def _narrative_with_claims(
    db,
    vec: list[float],
    n_claims: int,
    *,
    src: "KGSource | None" = None,
    label: str = "test",
) -> "KGNarrative":
    """
    Create a KGNarrative whose centroid is *vec* and attach *n_claims* dummy
    claims (all with the same embedding so the centroid stays at *vec*).
    """
    if src is None:
        url = f"https://src-{label}.example.com"
        src = KGSource(url=url, content_hash=url, text="t")
        db.add(src)
        db.flush()

    narr = KGNarrative(
        label=label,
        status="active",
        velocity_score=1.0,
        clustering_method=CLUSTERING_METHOD,
        first_seen_at=datetime.utcnow(),
        last_seen_at=datetime.utcnow(),
        embedding=json.dumps(vec),
    )
    db.add(narr)
    db.flush()

    for i in range(n_claims):
        c = KGClaim(
            text=f"claim-{label}-{i}",
            stance="neutral",
            confidence=0.8,
            source_id=src.id,
            embedding=json.dumps(vec),
        )
        db.add(c)
        db.flush()
        db.add(KGNarrativeClaim(narrative_id=narr.id, claim_id=c.id))

    db.flush()
    return narr


class TestMergeNarratives:
    def test_no_narratives_returns_zero(self, db):
        assert merge_narratives(db) == 0

    def test_single_narrative_no_merge(self, db):
        _narrative_with_claims(db, _v(0), 2)
        assert merge_narratives(db) == 0

    def test_distant_narratives_not_merged(self, db):
        _narrative_with_claims(db, _v(0),  2, label="a")
        _narrative_with_claims(db, _FAR,   2, label="b")
        assert merge_narratives(db) == 0
        assert db.query(KGNarrative).filter_by(status="active").count() == 2

    def test_near_narratives_are_merged(self, db):
        _narrative_with_claims(db, _v(0), 3, label="big")
        _narrative_with_claims(db, _NEAR, 1, label="small")
        merged_count = merge_narratives(db)
        assert merged_count == 1

    def test_smaller_absorbed_into_larger(self, db):
        big   = _narrative_with_claims(db, _v(0),  3, label="big")
        small = _narrative_with_claims(db, _NEAR,  1, label="small")
        merge_narratives(db)
        db.refresh(big)
        db.refresh(small)
        assert small.status        == "merged"
        assert small.merged_into_id == big.id
        assert big.status          == "active"

    def test_merged_narrative_claims_moved_to_survivor(self, db):
        big   = _narrative_with_claims(db, _v(0), 3, label="big")
        small = _narrative_with_claims(db, _NEAR, 2, label="small")
        merge_narratives(db)
        # Survivor gets all 5 claims
        survivor_count = (
            db.query(KGNarrativeClaim).filter_by(narrative_id=big.id).count()
        )
        assert survivor_count == 5

    def test_merged_narrative_has_no_remaining_claims(self, db):
        big   = _narrative_with_claims(db, _v(0), 3, label="big")
        small = _narrative_with_claims(db, _NEAR, 2, label="small")
        merge_narratives(db)
        absorbed_count = (
            db.query(KGNarrativeClaim).filter_by(narrative_id=small.id).count()
        )
        assert absorbed_count == 0

    def test_duplicate_claims_not_double_linked(self, db):
        """
        If a claim already belongs to both narratives before merge, the merge
        must not create a duplicate KGNarrativeClaim row for the survivor.
        """
        url = "https://shared.example.com"
        src = KGSource(url=url, content_hash=url, text="t")
        db.add(src)
        db.flush()

        shared_claim = KGClaim(
            text="shared claim",
            stance="neutral",
            confidence=0.8,
            source_id=src.id,
            embedding=json.dumps(_v(0)),
        )
        db.add(shared_claim)
        db.flush()

        big   = _narrative_with_claims(db, _v(0), 2, src=src, label="big")
        small = _narrative_with_claims(db, _NEAR, 1, src=src, label="small")

        # Manually link shared_claim to both narratives
        db.add(KGNarrativeClaim(narrative_id=big.id,   claim_id=shared_claim.id))
        db.add(KGNarrativeClaim(narrative_id=small.id, claim_id=shared_claim.id))
        db.flush()

        merge_narratives(db)

        # Only one link for shared_claim under the survivor
        links = (
            db.query(KGNarrativeClaim)
            .filter_by(narrative_id=big.id, claim_id=shared_claim.id)
            .count()
        )
        assert links == 1

    def test_survivor_centroid_updated(self, db):
        big   = _narrative_with_claims(db, _v(0),  2, label="big")
        small = _narrative_with_claims(db, _NEAR,  2, label="small")
        merge_narratives(db)
        db.refresh(big)
        new_centroid = json.loads(big.embedding)
        # Centroid should be unit-norm
        mag = math.sqrt(sum(x * x for x in new_centroid))
        assert abs(mag - 1.0) < 1e-6
        # Centroid should have shifted toward _NEAR (between 0° and 5°)
        # i.e. slightly above the x-axis; y-component > 0
        assert new_centroid[1] > 0

    def test_merged_status_excluded_from_get_emerging(self, db):
        big   = _narrative_with_claims(db, _v(0), 3, label="big")
        small = _narrative_with_claims(db, _NEAR, 1, label="small")
        merge_narratives(db)
        results = get_emerging_narratives(db)
        result_ids = {r.narrative.id for r in results}
        db.refresh(small)
        assert small.id not in result_ids

    def test_report_counts_merged_narratives(self, db, monkeypatch):
        """run_clustering report should include narratives_merged count."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        now = datetime.utcnow()

        # Pre-seed two near-identical active narratives so clustering will merge
        _narrative_with_claims(db, _v(0),  2, label="big")
        _narrative_with_claims(db, _NEAR,  1, label="small")

        # run_clustering with no new claims still runs merge+decay steps
        report = run_clustering(db, now=now)
        assert report.narratives_merged == 1

    def test_three_way_merge_chain(self, db):
        """Three mutually close narratives should reduce to one survivor."""
        a = _narrative_with_claims(db, _v(0), 3, label="a")
        b = _narrative_with_claims(db, _v(3), 2, label="b")
        c = _narrative_with_claims(db, _v(6), 1, label="c")
        merge_narratives(db)
        active = db.query(KGNarrative).filter_by(status="active").count()
        # At minimum two of three are absorbed; depending on traversal order
        # it could be 1 or 2 survivors — but must be fewer than 3
        assert active < 3


# ══════════════════════════════════════════════════════════════════════════════
# Inactivity decay
# ══════════════════════════════════════════════════════════════════════════════

class TestInactivityDecay:
    def _active_narrative(
        self, db, *, last_seen_days_ago: float, label: str = "n"
    ) -> KGNarrative:
        ts = datetime.utcnow() - timedelta(days=last_seen_days_ago)
        narr = KGNarrative(
            label=label,
            status="active",
            velocity_score=1.0,
            clustering_method=CLUSTERING_METHOD,
            first_seen_at=ts,
            last_seen_at=ts,
        )
        db.add(narr)
        db.flush()
        return narr

    def test_no_narratives_returns_zero(self, db):
        assert apply_inactivity_decay(db) == 0

    def test_recent_narrative_not_decayed(self, db):
        self._active_narrative(db, last_seen_days_ago=1, label="recent")
        assert apply_inactivity_decay(db) == 0
        narr = db.query(KGNarrative).first()
        assert narr.status == "active"

    def test_stale_narrative_marked_inactive(self, db):
        self._active_narrative(db, last_seen_days_ago=INACTIVE_DAYS + 1, label="stale")
        count = apply_inactivity_decay(db)
        assert count == 1
        narr = db.query(KGNarrative).first()
        assert narr.status == "inactive"

    def test_boundary_exactly_inactive_days_not_decayed(self, db):
        """A narrative last seen exactly INACTIVE_DAYS ago is NOT yet stale (strict <)."""
        now = datetime.utcnow()
        cutoff = now - timedelta(days=INACTIVE_DAYS)
        # last_seen_at == cutoff exactly: should NOT be decayed (cutoff is not < cutoff)
        narr = KGNarrative(
            label="boundary",
            status="active",
            velocity_score=1.0,
            clustering_method=CLUSTERING_METHOD,
            first_seen_at=cutoff,
            last_seen_at=cutoff,
        )
        db.add(narr)
        db.flush()
        assert apply_inactivity_decay(db, now=now) == 0

    def test_only_active_narratives_decayed(self, db):
        """Already-merged or inactive narratives are not touched."""
        stale = self._active_narrative(db, last_seen_days_ago=INACTIVE_DAYS + 5,
                                       label="stale")
        already_inactive = KGNarrative(
            label="already_inactive",
            status="inactive",
            velocity_score=0.0,
            clustering_method=CLUSTERING_METHOD,
            first_seen_at=datetime.utcnow() - timedelta(days=30),
            last_seen_at=datetime.utcnow() - timedelta(days=30),
        )
        db.add(already_inactive)
        db.flush()
        count = apply_inactivity_decay(db)
        assert count == 1  # only the stale active one

    def test_inactive_excluded_from_get_emerging(self, db):
        stale = self._active_narrative(db, last_seen_days_ago=INACTIVE_DAYS + 1,
                                       label="stale")
        apply_inactivity_decay(db)
        results = get_emerging_narratives(db)
        assert all(r.narrative.id != stale.id for r in results)

    def test_report_counts_decayed_narratives(self, db, monkeypatch):
        """run_clustering report should include narratives_decayed count."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        now = datetime.utcnow()
        stale = KGNarrative(
            label="stale",
            status="active",
            velocity_score=1.0,
            clustering_method=CLUSTERING_METHOD,
            first_seen_at=now - timedelta(days=INACTIVE_DAYS + 5),
            last_seen_at=now - timedelta(days=INACTIVE_DAYS + 5),
        )
        db.add(stale)
        db.flush()
        report = run_clustering(db, now=now)
        assert report.narratives_decayed == 1


# ══════════════════════════════════════════════════════════════════════════════
# Alert severity computation
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeAlertSeverity:
    def test_zero_inputs_zero_severity(self):
        sev, _ = _compute_alert_severity(0.0, 0, 0)
        assert sev == 0.0

    def test_severity_in_unit_range(self):
        sev, _ = _compute_alert_severity(10.0, 20, 20)
        assert 0.0 <= sev <= 1.0

    def test_velocity_spike_type_when_velocity_dominant(self):
        # High velocity, low sources/entities → velocity_spike
        _, alert_type = _compute_alert_severity(10.0, 1, 1)
        assert alert_type == ALERT_VELOCITY_SPIKE

    def test_source_surge_type_when_sources_dominant(self):
        # Low velocity, many sources, few entities → source_surge
        _, alert_type = _compute_alert_severity(0.1, 20, 1)
        assert alert_type == ALERT_SOURCE_SURGE

    def test_entity_surge_type_when_entities_dominant(self):
        # Low velocity, few sources, many entities → entity_surge
        _, alert_type = _compute_alert_severity(0.1, 1, 20)
        assert alert_type == ALERT_ENTITY_SURGE

    def test_severity_increases_with_velocity(self):
        sev_low,  _ = _compute_alert_severity(1.0, 2, 2)
        sev_high, _ = _compute_alert_severity(5.0, 2, 2)
        assert sev_high > sev_low

    def test_severity_increases_with_sources(self):
        sev_low,  _ = _compute_alert_severity(1.0, 1, 2)
        sev_high, _ = _compute_alert_severity(1.0, 10, 2)
        assert sev_high > sev_low

    def test_severity_increases_with_entities(self):
        sev_low,  _ = _compute_alert_severity(1.0, 2, 1)
        sev_high, _ = _compute_alert_severity(1.0, 2, 10)
        assert sev_high > sev_low


# ══════════════════════════════════════════════════════════════════════════════
# generate_alerts
# ══════════════════════════════════════════════════════════════════════════════

def _active_narrative_with_signal(
    db,
    *,
    velocity: float = 5.0,
    n_sources: int = 3,
    n_entities: int = 3,
    label: str = "alert-test",
    status: str = "active",
) -> KGNarrative:
    """
    Build a KGNarrative with enough velocity / sources / entities to fire an
    alert when generate_alerts is called with default thresholds.
    """
    narr = KGNarrative(
        label=label,
        status=status,
        velocity_score=velocity,
        clustering_method=CLUSTERING_METHOD,
        first_seen_at=datetime.utcnow(),
        last_seen_at=datetime.utcnow(),
        embedding=json.dumps(_v(0)),
    )
    db.add(narr)
    db.flush()

    for i in range(n_sources):
        url = f"https://src-{label}-{i}.example.com"
        src = KGSource(url=url, content_hash=url, text="t")
        db.add(src)
        db.flush()
        claim = KGClaim(
            text=f"claim-{label}-{i}",
            stance="neutral",
            confidence=0.8,
            source_id=src.id,
            embedding=json.dumps(_v(0)),
        )
        db.add(claim)
        db.flush()
        db.add(KGNarrativeClaim(narrative_id=narr.id, claim_id=claim.id))

        if i < n_entities:
            ent = KGEntity(entity_type="PERSON", name=f"ent-{label}-{i}")
            db.add(ent)
            db.flush()
            db.add(KGClaimEntity(claim_id=claim.id, entity_id=ent.id))

    db.flush()
    return narr


class TestGenerateAlerts:
    def test_no_narratives_returns_empty(self, db):
        assert generate_alerts(db) == []

    def test_alert_created_for_high_severity_narrative(self, db):
        _active_narrative_with_signal(db)
        alerts = generate_alerts(db, severity_threshold=0.0)
        assert len(alerts) == 1

    def test_alert_not_created_below_threshold(self, db):
        _active_narrative_with_signal(db, velocity=0.0, n_sources=0, n_entities=0)
        # With all-zero inputs severity == 0.0; threshold > 0 → no alert
        alerts = generate_alerts(db, severity_threshold=0.1)
        assert alerts == []

    def test_alert_stored_in_db(self, db):
        _active_narrative_with_signal(db)
        generate_alerts(db, severity_threshold=0.0)
        assert db.query(KGAlert).count() == 1

    def test_alert_fields_populated(self, db):
        narr = _active_narrative_with_signal(db)
        now = datetime.utcnow()
        alerts = generate_alerts(db, severity_threshold=0.0, now=now)
        a = alerts[0]
        assert a.narrative_id == narr.id
        assert a.alert_type in (ALERT_VELOCITY_SPIKE, ALERT_SOURCE_SURGE, ALERT_ENTITY_SURGE)
        assert 0.0 < a.severity_score <= 1.0
        assert narr.label in a.message
        assert a.created_at == now
        assert a.resolved_at is None

    def test_cooldown_prevents_second_alert_within_24h(self, db):
        """Second call within cooldown window must not create a duplicate."""
        narr = _active_narrative_with_signal(db)
        now = datetime.utcnow()
        first  = generate_alerts(db, severity_threshold=0.0, now=now)
        second = generate_alerts(db, severity_threshold=0.0, now=now)
        assert len(first)  == 1
        assert len(second) == 0
        assert db.query(KGAlert).count() == 1

    def test_new_alert_fires_after_cooldown_window(self, db):
        """An alert created >24h ago does not block a new one."""
        narr = _active_narrative_with_signal(db)
        old_time = datetime.utcnow() - timedelta(hours=ALERT_COOLDOWN_HOURS + 1)
        new_time = datetime.utcnow()
        first  = generate_alerts(db, severity_threshold=0.0, now=old_time)
        second = generate_alerts(db, severity_threshold=0.0, now=new_time)
        assert len(first)  == 1
        assert len(second) == 1
        assert db.query(KGAlert).count() == 2

    def test_resolved_alert_does_not_block_new_one(self, db):
        """A resolved alert within the cooldown window should not block a new alert."""
        narr = _active_narrative_with_signal(db)
        now = datetime.utcnow()
        first = generate_alerts(db, severity_threshold=0.0, now=now)
        # Mark first alert as resolved
        first[0].resolved_at = now
        db.flush()
        # Second call should fire a new alert because the existing one is resolved
        second = generate_alerts(db, severity_threshold=0.0, now=now)
        assert len(second) == 1

    def test_inactive_narrative_skipped(self, db):
        _active_narrative_with_signal(db, status="inactive")
        alerts = generate_alerts(db, severity_threshold=0.0)
        assert alerts == []

    def test_merged_narrative_skipped(self, db):
        _active_narrative_with_signal(db, status="merged")
        alerts = generate_alerts(db, severity_threshold=0.0)
        assert alerts == []

    def test_narrative_without_claims_skipped(self, db):
        narr = KGNarrative(
            label="empty",
            status="active",
            velocity_score=10.0,
            clustering_method=CLUSTERING_METHOD,
            first_seen_at=datetime.utcnow(),
            last_seen_at=datetime.utcnow(),
        )
        db.add(narr)
        db.flush()
        assert generate_alerts(db, severity_threshold=0.0) == []

    def test_multiple_narratives_each_get_alert(self, db):
        _active_narrative_with_signal(db, label="n1")
        _active_narrative_with_signal(db, label="n2")
        alerts = generate_alerts(db, severity_threshold=0.0)
        assert len(alerts) == 2

    def test_cooldown_is_per_narrative(self, db):
        """Cooldown on narrative A must not block an alert for narrative B."""
        n1 = _active_narrative_with_signal(db, label="n1")
        n2 = _active_narrative_with_signal(db, label="n2")
        now = datetime.utcnow()
        first = generate_alerts(db, severity_threshold=0.0, now=now)
        assert len(first) == 2  # both fire
        # Second call: both are blocked by cooldown
        second = generate_alerts(db, severity_threshold=0.0, now=now)
        assert len(second) == 0

    def test_report_counts_alerts_created(self, db, monkeypatch):
        """run_clustering report.alerts_created should reflect new alerts."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        _active_narrative_with_signal(db)
        now = datetime.utcnow()
        report = run_clustering(db, now=now, threshold=SIMILARITY_THRESHOLD)
        assert report.alerts_created >= 1


# ══════════════════════════════════════════════════════════════════════════════
# get_active_alerts
# ══════════════════════════════════════════════════════════════════════════════

class TestGetActiveAlerts:
    def _alert(self, db, narr: KGNarrative, *, resolved: bool = False,
               created_ago_hours: float = 0.0) -> KGAlert:
        ts = datetime.utcnow() - timedelta(hours=created_ago_hours)
        a = KGAlert(
            narrative_id=narr.id,
            alert_type=ALERT_VELOCITY_SPIKE,
            severity_score=0.5,
            message="test alert",
            created_at=ts,
            resolved_at=ts if resolved else None,
        )
        db.add(a)
        db.flush()
        return a

    def test_empty_when_no_alerts(self, db):
        assert get_active_alerts(db) == []

    def test_returns_unresolved_alerts(self, db):
        narr = _active_narrative_with_signal(db, velocity=0.0, n_sources=1,
                                             n_entities=0, label="g")
        self._alert(db, narr)
        assert len(get_active_alerts(db)) == 1

    def test_excludes_resolved_alerts(self, db):
        narr = _active_narrative_with_signal(db, velocity=0.0, n_sources=1,
                                             n_entities=0, label="g")
        self._alert(db, narr, resolved=True)
        assert get_active_alerts(db) == []

    def test_ordered_newest_first(self, db):
        narr = _active_narrative_with_signal(db, velocity=0.0, n_sources=1,
                                             n_entities=0, label="g")
        old = self._alert(db, narr, created_ago_hours=5.0)
        new = self._alert(db, narr, created_ago_hours=1.0)
        results = get_active_alerts(db)
        assert results[0].id == new.id
        assert results[1].id == old.id

    def test_limit_respected(self, db):
        narr = _active_narrative_with_signal(db, velocity=0.0, n_sources=1,
                                             n_entities=0, label="g")
        for i in range(5):
            self._alert(db, narr, created_ago_hours=float(i))
        assert len(get_active_alerts(db, limit=3)) == 3

    def test_mixed_resolved_unresolved(self, db):
        narr = _active_narrative_with_signal(db, velocity=0.0, n_sources=1,
                                             n_entities=0, label="g")
        self._alert(db, narr, resolved=True)
        self._alert(db, narr, resolved=False)
        self._alert(db, narr, resolved=True)
        self._alert(db, narr, resolved=False)
        assert len(get_active_alerts(db)) == 2
