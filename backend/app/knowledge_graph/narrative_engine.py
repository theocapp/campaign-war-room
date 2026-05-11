"""
Narrative detection engine.

Clusters kg_claims into evolving kg_narratives using cosine similarity
between claim embedding vectors.  No LLM calls occur in the clustering loop
itself — embeddings are computed once per claim and cached in
kg_claims.embedding (JSON text array).

Public API
──────────
embed_claim(text)          → list[float]   unit-norm embedding vector
run_clustering(db, ...)    → ClusteringReport
get_emerging_narratives(db, ...) → list[EmergingNarrative]

Embedding providers
───────────────────
• OpenAI text-embedding-3-small  — when OPENAI_API_KEY is set
• Hash-based pseudo-vector       — deterministic fallback used in tests

The hash fallback is NOT semantically meaningful (dissimilar texts can be
close in hash space). Tests that need controlled similarity must set
claim.embedding directly rather than relying on embed_claim.

Clustering algorithm  (cosine_threshold_v1)
──────────────────────────────────────────
For each unlinked claim (created in last `days` days):
  1. Compute cosine similarity to every existing narrative centroid.
  2. If best_sim >= threshold  → attach to that narrative.
  3. Else                      → seed a new narrative from this claim.
After all assignments, recompute each touched narrative's centroid as the
mean of all its member claim embeddings, then update velocity_score via EMA.

Lifecycle management
────────────────────
merge_narratives(db, ...):
  Compare active narrative centroids pairwise; when cosine similarity >=
  MERGE_THRESHOLD (0.90) merge the smaller narrative into the larger one —
  moving all KGNarrativeClaim links, recomputing the surviving centroid and
  velocity, and marking the absorbed narrative status="merged".

apply_inactivity_decay(db, ...):
  Any active narrative whose last_seen_at is older than INACTIVE_DAYS (14)
  is marked status="inactive".

Alerts
──────
generate_alerts(db, now=None) → list[KGAlert]:
  For each active narrative compute an alert severity from velocity_score,
  source diversity, and entity spread.  When severity exceeds
  ALERT_SEVERITY_THRESHOLD and no unresolved alert was created for the same
  narrative within the last 24 hours, a new KGAlert row is inserted.

get_active_alerts(db, limit=50) → list[KGAlert]:
  Return unresolved alerts ordered newest-first.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import struct
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.knowledge_graph.orm import (
    KGAlert,
    KGClaim,
    KGClaimEntity,
    KGNarrative,
    KGNarrativeClaim,
    KGSource,
)

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

SIMILARITY_THRESHOLD: float = 0.62
MERGE_THRESHOLD:      float = 0.90  # centroid similarity required to merge two narratives
EMA_ALPHA:            float = 0.3   # smoothing factor for velocity EMA
HASH_EMBED_DIM:       int   = 64    # dimension of the hash fallback vector
CLUSTERING_METHOD:    str   = "cosine_threshold_v1"
INACTIVE_DAYS:        int   = 14    # days of silence before a narrative is marked inactive

# Alert thresholds
ALERT_SEVERITY_THRESHOLD: float = 0.30  # minimum severity to fire an alert
ALERT_COOLDOWN_HOURS:     int   = 24    # minimum hours between alerts per narrative

# Alert type labels (open-ended; callers may extend)
ALERT_VELOCITY_SPIKE:  str = "velocity_spike"
ALERT_ENTITY_SURGE:    str = "entity_surge"
ALERT_SOURCE_SURGE:    str = "source_surge"
ALERT_NEW_NARRATIVE:   str = "new_narrative"
ALERT_OPPONENT_ATTACK: str = "opponent_attack"


# ── Pure vector math (no SQLAlchemy dependency) ───────────────────────────────

def _normalize(vec: list[float]) -> list[float]:
    mag = math.sqrt(sum(x * x for x in vec))
    if mag < 1e-10:
        return list(vec)
    return [x / mag for x in vec]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """
    Dot product of two pre-normalised vectors clamped to [-1, 1].
    Assumes both are already unit-norm; skips re-normalisation for speed.
    Returns 0.0 when dimensions differ or either vector is empty.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    return max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b))))


def vec_mean(vectors: list[list[float]]) -> list[float]:
    """Element-wise mean of a non-empty list of same-length vectors."""
    if not vectors:
        return []
    dim = len(vectors[0])
    return [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]


# ── Embedding computation ─────────────────────────────────────────────────────

def embed_claim(text: str) -> list[float]:
    """
    Return a unit-norm float vector for *text*.

    Uses OpenAI text-embedding-3-small when OPENAI_API_KEY is present in the
    environment, falling back to the deterministic hash pseudo-vector otherwise.
    """
    import os
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if api_key:
        try:
            return _openai_embed(text, api_key)
        except Exception as exc:
            log.warning("OpenAI embed failed, using hash fallback: %s", exc)
    return _hash_embed(text)


def _openai_embed(text: str, api_key: str,
                  model: str = "text-embedding-3-small") -> list[float]:
    try:
        import openai
    except ImportError as exc:
        raise RuntimeError("openai package not installed") from exc
    client = openai.OpenAI(api_key=api_key)
    response = client.embeddings.create(input=text[:8000], model=model)
    return _normalize(response.data[0].embedding)


def _hash_embed(text: str, dim: int = HASH_EMBED_DIM) -> list[float]:
    """
    Single-text fallback embedding — used when the batch has fewer than 2
    claims (making corpus IDF meaningless) or when sklearn is unavailable.

    Design: each unique word in *text* is mapped to a fixed bucket in [0, dim)
    via SHA-256 of the word itself.  The bucket's value is accumulated with
    that word's relative term frequency (TF weight).  Because the word→bucket
    mapping derives from SHA-256(word) — not SHA-256(full-text + i) as in the
    original implementation — the same word always lands in the same bucket
    across every call.  Two texts that share words therefore accumulate
    non-zero weight in the same dimensions, producing a positive cosine
    similarity proportional to their word overlap.

    This satisfies all four test contracts:
      • Deterministic   — SHA-256(word) is stable.
      • Unit-norm       — result is L2-normalised by _normalize().
      • Distinct output — distinct words land in (overwhelmingly) distinct
                          buckets, so different texts produce different vectors.
      • Fixed dimension — always returns exactly `dim` floats.
    """
    words = re.findall(r'\b\w+\b', text.lower())
    if not words:
        # Deterministic non-zero vector for empty input so unit-norm test passes.
        seed = text.encode("utf-8") or b"__empty__"
        result: list[float] = []
        for i in range(dim):
            digest = hashlib.sha256(seed + i.to_bytes(4, "little")).digest()
            raw = struct.unpack_from(">H", digest[:2])[0]
            result.append((raw / 32767.5) - 1.0)
        return _normalize(result)

    from collections import Counter
    counts = Counter(words)
    total = len(words)
    vec = [0.0] * dim
    for word, count in counts.items():
        # Stable word → bucket mapping: SHA-256 of the word string itself
        digest = hashlib.sha256(word.encode()).digest()
        bucket = struct.unpack_from(">H", digest[:2])[0] % dim
        vec[bucket] += count / total  # normalised TF weight
    return _normalize(vec)


def _batch_tfidf_embed(
    claims: list[KGClaim],
    max_features: int = HASH_EMBED_DIM,
) -> dict[int, list[float]]:
    """
    Fit a TF-IDF vectorizer on the current clustering batch and return one
    normalized dense vector per claim.

    All vectors share the same dimension (max_features), so cosine similarity
    is well-defined across every pair returned by a single call.  The vectorizer
    is fitted on *claims* only — not on historical data — so the vocabulary
    reflects the political discourse present in this clustering window.

    Parameters
    ----------
    claims       : The full set of claims being processed this run.
    max_features : Vocabulary cap; also fixes the output vector dimension.
                   Defaults to HASH_EMBED_DIM so stored centroids remain
                   dimension-compatible across runs.

    Returns
    -------
    dict mapping claim.id → unit-norm float list of length max_features.
    Returns {} when the batch is too small (<2 docs) or sklearn is absent.
    """
    if len(claims) < 2:
        return {}
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
    except ImportError:
        log.warning(
            "narrative_engine: sklearn not installed — "
            "falling back to single-text TF embedding via _hash_embed()"
        )
        return {}

    texts = [c.text for c in claims]
    vectorizer = TfidfVectorizer(
        max_features=max_features,
        sublinear_tf=True,   # log(1 + tf)
        norm="l2",           # output rows are unit-norm — cosine sim = dot product
        min_df=1,
        stop_words="english",
        ngram_range=(1, 2),  # unigrams + bigrams capture political phrases
    )
    try:
        matrix = vectorizer.fit_transform(texts)
    except ValueError as exc:
        log.warning("narrative_engine: TF-IDF fit failed — %s", exc)
        return {}

    dense = matrix.toarray()
    result: dict[int, list[float]] = {}
    for i, claim in enumerate(claims):
        row = dense[i].tolist()
        # Pad to max_features when vocabulary is smaller than the cap
        if len(row) < max_features:
            row = row + [0.0] * (max_features - len(row))
        result[claim.id] = row[:max_features]
    return result


# ── Embedding I/O on KGClaim ──────────────────────────────────────────────────

def load_embedding(claim: KGClaim) -> Optional[list[float]]:
    """
    Read the stored embedding from a KGClaim row.
    Prefers the `embedding` column; falls back to `semantic_id` for DBs
    where the migration has not yet run.
    """
    raw: Optional[str] = None
    if hasattr(claim, "embedding") and claim.embedding:
        raw = claim.embedding
    elif claim.semantic_id:
        raw = claim.semantic_id
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else None
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def store_embedding(claim: KGClaim, vec: list[float]) -> None:
    """Write *vec* to the best-available column on *claim*."""
    encoded = json.dumps(vec)
    if hasattr(claim, "embedding"):
        claim.embedding = encoded
    else:
        claim.semantic_id = encoded


# ── Narrative label helper ────────────────────────────────────────────────────

def _narrative_label(text: str, max_words: int = 12, max_chars: int = 80) -> str:
    """
    Short human-readable label derived from the seed claim text.
    Takes the first `max_words` words, truncated to `max_chars` characters.
    """
    words = text.split()
    truncated = " ".join(words[:max_words])
    if len(truncated) > max_chars:
        truncated = truncated[:max_chars - 1] + "…"
    elif len(words) > max_words:
        truncated += "…"
    return truncated


# ── Clustering report ─────────────────────────────────────────────────────────

@dataclass
class ClusteringReport:
    claims_processed:    int = 0
    claims_embedded:     int = 0   # embeddings computed this run (not cached)
    narratives_created:  int = 0
    narratives_updated:  int = 0   # existing narratives that gained a new claim
    links_added:         int = 0
    narratives_merged:   int = 0   # narratives absorbed into a larger one
    narratives_decayed:  int = 0   # narratives marked inactive due to silence
    alerts_created:      int = 0   # new KGAlert rows created this run
    errors:              list[str] = field(default_factory=list)


# ── Main clustering job ───────────────────────────────────────────────────────

def run_clustering(
    db: Session,
    *,
    days: int = 7,
    threshold: float = SIMILARITY_THRESHOLD,
    now: Optional[datetime] = None,
) -> ClusteringReport:
    """
    Assign recent unlinked claims to narratives or seed new ones.

    Parameters
    ----------
    db        : SQLAlchemy session (caller owns commit).
    days      : Look-back window for "recent" claims.
    threshold : Cosine similarity required to attach a claim to an existing
                narrative.  Default 0.82.
    now       : Override for "current time" (useful in tests).

    Returns a ClusteringReport with counts of created/updated rows.
    The session is flushed at the end but NOT committed — caller commits.
    """
    report = ClusteringReport()
    now = now or datetime.utcnow()
    since = now - timedelta(days=days)

    # ── 1. Load candidate claims ───────────────────────────────────────────
    recent_claims: list[KGClaim] = (
        db.query(KGClaim)
        .filter(KGClaim.created_at >= since)
        .all()
    )
    report.claims_processed = len(recent_claims)

    # ── 2–6: embedding + assignment (skip when no new claims) ─────────────
    if recent_claims:
        claim_vecs: dict[int, list[float]] = {}

        # ── 2a. Batch TF-IDF pre-embedding ────────────────────────────────
        # When no OpenAI key is present, fit a single TfidfVectorizer on all
        # claims that lack a cached embedding.  Fitting on the full batch
        # (rather than one claim at a time) enables real IDF weighting, so
        # terms rare across this batch receive higher weight than ubiquitous
        # ones — giving same-topic claims higher cosine similarity.
        #
        # The OpenAI path is intentionally bypassed here; embed_claim() still
        # handles OpenAI per-claim (including its retry/fallback logic).
        _uncached = [c for c in recent_claims if load_embedding(c) is None]
        _tfidf_batch: dict[int, list[float]] = (
            _batch_tfidf_embed(_uncached)
            if _uncached and not os.environ.get("OPENAI_API_KEY")
            else {}
        )

        # ── 2b. Per-claim embedding ───────────────────────────────────────
        for claim in recent_claims:
            vec = load_embedding(claim)
            if vec is None:
                if claim.id in _tfidf_batch:
                    # Use the batch-fitted TF-IDF vector already computed above
                    vec = _tfidf_batch[claim.id]
                    store_embedding(claim, vec)
                    report.claims_embedded += 1
                else:
                    # OpenAI path (or single-claim _hash_embed fallback)
                    try:
                        vec = embed_claim(claim.text)
                        store_embedding(claim, vec)
                        report.claims_embedded += 1
                    except Exception as exc:
                        msg = f"embed failed for claim {claim.id}: {exc}"
                        log.error("narrative_engine: %s", msg)
                        report.errors.append(msg)
                        continue
            claim_vecs[claim.id] = vec

        # ── 3. Load existing narrative centroids ──────────────────────────
        all_narratives: list[KGNarrative] = db.query(KGNarrative).all()
        narrative_centroids: dict[int, list[float]] = {}
        for narr in all_narratives:
            if narr.embedding:
                try:
                    narrative_centroids[narr.id] = json.loads(narr.embedding)
                except (json.JSONDecodeError, TypeError):
                    pass

        narrative_map: dict[int, KGNarrative] = {n.id: n for n in all_narratives}

        # ── 4. Assign claims to narratives ────────────────────────────────
        touched: set[int] = set()

        for claim in recent_claims:
            if claim.id not in claim_vecs:
                continue

            if db.query(KGNarrativeClaim).filter_by(claim_id=claim.id).first():
                continue

            vec = claim_vecs[claim.id]
            best_id:  Optional[int] = None
            best_sim: float = threshold

            for narr_id, centroid in narrative_centroids.items():
                if len(centroid) != len(vec):
                    continue
                sim = cosine_similarity(vec, centroid)
                if sim >= best_sim:
                    best_sim = sim
                    best_id  = narr_id

            if best_id is not None:
                log.debug(
                    "cluster_decision: claim_id=%d best_match=narrative_id=%d similarity=%.4f",
                    claim.id, best_id, best_sim,
                )
                db.add(KGNarrativeClaim(narrative_id=best_id, claim_id=claim.id))
                touched.add(best_id)
                report.links_added        += 1
                report.narratives_updated += 1
            else:
                log.debug(
                    "cluster_decision: claim_id=%d no_match best_sim=%.4f threshold=%.4f seed_new_narrative",
                    claim.id, best_sim, threshold,
                )
                claim_ts = claim.created_at or now
                new_narr = KGNarrative(
                    label=_narrative_label(claim.text),
                    clustering_method=CLUSTERING_METHOD,
                    first_seen_at=claim_ts,
                    last_seen_at=claim_ts,
                    velocity_score=0.0,
                    embedding=json.dumps(vec),
                )
                db.add(new_narr)
                db.flush()

                db.add(KGNarrativeClaim(narrative_id=new_narr.id, claim_id=claim.id))
                narrative_centroids[new_narr.id] = vec
                narrative_map[new_narr.id]       = new_narr
                touched.add(new_narr.id)
                report.narratives_created += 1
                report.links_added        += 1

        # ── 5. Recompute centroids for touched narratives ──────────────────
        for narr_id in touched:
            member_ids: list[int] = [
                row.claim_id
                for row in db.query(KGNarrativeClaim)
                .filter_by(narrative_id=narr_id)
                .all()
            ]
            member_vecs: list[list[float]] = []
            for cid in member_ids:
                v = claim_vecs.get(cid)
                if v is None:
                    c = db.get(KGClaim, cid)
                    if c:
                        v = load_embedding(c)
                if v is not None:
                    member_vecs.append(v)

            if member_vecs:
                new_centroid = _normalize(vec_mean(member_vecs))
                narrative_centroids[narr_id] = new_centroid
                narrative_map[narr_id].embedding         = json.dumps(new_centroid)
                narrative_map[narr_id].clustering_method = CLUSTERING_METHOD

        # ── 6. Update timestamps and velocity scores ───────────────────────
        for narr_id in touched:
            _update_narrative_meta(db, narrative_map[narr_id], now)

    # ── 7. Merge near-duplicate narratives (always runs) ─────────────────
    report.narratives_merged = merge_narratives(db, now=now)

    # ── 8. Decay stale narratives (always runs) ───────────────────────────
    report.narratives_decayed = apply_inactivity_decay(db, now=now)

    # ── 9. Generate alerts for active narratives (always runs) ────────────
    report.alerts_created = len(generate_alerts(db, now=now))

    db.flush()

    # Debug summary: narrative diversity after clustering
    active_narratives = db.query(KGNarrative).filter(KGNarrative.status == "active").all()
    if active_narratives:
        source_counts = []
        velocities = []
        for narr in active_narratives:
            src_ids = (
                db.query(KGClaim.source_id)
                .join(KGNarrativeClaim, KGNarrativeClaim.claim_id == KGClaim.id)
                .filter(KGNarrativeClaim.narrative_id == narr.id, KGClaim.source_id.isnot(None))
                .distinct()
                .count()
            )
            source_counts.append(src_ids)
            velocities.append(narr.velocity_score or 0.0)
        avg_sources = sum(source_counts) / len(source_counts)
        log.info(
            "clustering summary: narratives=%d  avg_unique_sources=%.2f  "
            "velocity_min=%.4f  velocity_max=%.4f",
            len(active_narratives),
            avg_sources,
            min(velocities),
            max(velocities),
        )
    else:
        log.info("clustering summary: no active narratives after run")

    return report


def _weighted_daily_rate(db: Session, narrative_id: int, yesterday: datetime) -> float:
    """
    Sum of (claim.confidence * source.credibility_score) for all claims in
    *narrative_id* whose created_at >= *yesterday*.

    Falls back to claim.confidence * 0.5 when the source row is missing or
    lacks a credibility_score, so the function is always safe to call.
    """
    rows = (
        db.query(KGClaim.confidence, KGSource.credibility_score)
        .join(KGNarrativeClaim, KGNarrativeClaim.claim_id == KGClaim.id)
        .outerjoin(KGSource, KGSource.id == KGClaim.source_id)
        .filter(
            KGNarrativeClaim.narrative_id == narrative_id,
            KGClaim.created_at >= yesterday,
        )
        .all()
    )
    total = 0.0
    for confidence, credibility in rows:
        c = confidence if confidence is not None else 0.0
        s = credibility if credibility is not None else 0.5
        total += c * s
    return total


def _update_narrative_meta(
    db: Session,
    narr: KGNarrative,
    now: datetime,
) -> None:
    """
    Recompute velocity_score (EMA of weighted claims/day) and update timestamps.

    daily_rate = Σ (claim.confidence × source.credibility_score)
                 for all member claims created in the last 24 hours.
    velocity   = EMA_ALPHA × daily_rate + (1 − EMA_ALPHA) × previous_velocity

    Using weighted arrivals means a burst of low-confidence claims from
    unreliable sources raises velocity less than the same number of
    high-confidence, high-credibility claims.
    """
    yesterday = now - timedelta(days=1)
    daily_rate = _weighted_daily_rate(db, narr.id, yesterday)
    narr.velocity_score = (
        EMA_ALPHA * daily_rate
        + (1.0 - EMA_ALPHA) * (narr.velocity_score or 0.0)
    )
    narr.last_seen_at = now
    if narr.first_seen_at is None:
        narr.first_seen_at = now


# ── Narrative merge ───────────────────────────────────────────────────────────

def merge_narratives(
    db: Session,
    *,
    threshold: float = MERGE_THRESHOLD,
    now: Optional[datetime] = None,
) -> int:
    """
    Compare active narrative centroids pairwise; merge pairs whose cosine
    similarity >= *threshold*.

    For each mergeable pair, the narrative with *fewer* member claims is
    absorbed into the one with *more* members:
      • All KGNarrativeClaim rows are re-pointed to the survivor.
      • The survivor's centroid is recomputed as the normalised mean of all
        its member embeddings.
      • The survivor's velocity_score is updated via EMA.
      • The absorbed narrative's status is set to "merged" and
        merged_into_id is set to the survivor's id.

    Returns the number of narratives merged (absorbed) this call.
    The session is flushed but NOT committed — caller commits.
    """
    now = now or datetime.utcnow()

    active: list[KGNarrative] = (
        db.query(KGNarrative)
        .filter(KGNarrative.status == "active")
        .all()
    )

    # Build centroid cache; skip narratives without an embedding
    centroids: dict[int, list[float]] = {}
    for narr in active:
        if narr.embedding:
            try:
                centroids[narr.id] = json.loads(narr.embedding)
            except (json.JSONDecodeError, TypeError):
                pass

    # Member-count cache
    member_counts: dict[int, int] = {}
    for narr in active:
        member_counts[narr.id] = (
            db.query(KGNarrativeClaim)
            .filter_by(narrative_id=narr.id)
            .count()
        )

    narr_map: dict[int, KGNarrative] = {n.id: n for n in active}
    merged_ids: set[int] = set()  # absorbed narrative IDs this call
    ids = [n.id for n in active if n.id in centroids]

    for i in range(len(ids)):
        a_id = ids[i]
        if a_id in merged_ids:
            continue
        for j in range(i + 1, len(ids)):
            b_id = ids[j]
            if b_id in merged_ids:
                continue

            ca, cb = centroids[a_id], centroids[b_id]
            if len(ca) != len(cb):
                continue
            if cosine_similarity(ca, cb) < threshold:
                continue

            # Decide survivor (larger by claim count) vs absorbed (smaller)
            if member_counts.get(a_id, 0) >= member_counts.get(b_id, 0):
                survivor_id, absorbed_id = a_id, b_id
            else:
                survivor_id, absorbed_id = b_id, a_id

            _absorb_narrative(db, survivor_id=survivor_id,
                               absorbed_id=absorbed_id,
                               narr_map=narr_map,
                               centroids=centroids,
                               now=now)
            merged_ids.add(absorbed_id)
            # Refresh survivor count so later iterations use updated value
            member_counts[survivor_id] = (
                db.query(KGNarrativeClaim)
                .filter_by(narrative_id=survivor_id)
                .count()
            )

    if merged_ids:
        db.flush()
    return len(merged_ids)


def _absorb_narrative(
    db: Session,
    *,
    survivor_id: int,
    absorbed_id: int,
    narr_map: dict[int, KGNarrative],
    centroids: dict[int, list[float]],
    now: datetime,
) -> None:
    """
    Move all claims from *absorbed* to *survivor*, recompute centroid and
    velocity for the survivor, then mark *absorbed* as merged.
    """
    # Re-point every KGNarrativeClaim row from absorbed → survivor,
    # skipping any claim already linked to the survivor (dedup).
    survivor_claim_ids: set[int] = {
        row.claim_id
        for row in db.query(KGNarrativeClaim)
        .filter_by(narrative_id=survivor_id)
        .all()
    }
    absorbed_links = (
        db.query(KGNarrativeClaim)
        .filter_by(narrative_id=absorbed_id)
        .all()
    )
    for link in absorbed_links:
        if link.claim_id in survivor_claim_ids:
            db.delete(link)
        else:
            link.narrative_id = survivor_id

    # Recompute survivor centroid from all its member embeddings
    all_claim_ids: list[int] = [
        row.claim_id
        for row in db.query(KGNarrativeClaim)
        .filter_by(narrative_id=survivor_id)
        .all()
    ]
    member_vecs: list[list[float]] = []
    for cid in all_claim_ids:
        c = db.get(KGClaim, cid)
        if c:
            v = load_embedding(c)
            if v is not None:
                member_vecs.append(v)

    if member_vecs:
        new_centroid = _normalize(vec_mean(member_vecs))
        centroids[survivor_id] = new_centroid
        narr_map[survivor_id].embedding = json.dumps(new_centroid)

    # Update survivor velocity and timestamps
    _update_narrative_meta(db, narr_map[survivor_id], now)

    # Mark absorbed narrative as merged
    absorbed = narr_map[absorbed_id]
    absorbed.status        = "merged"
    absorbed.merged_into_id = survivor_id
    absorbed.last_seen_at  = now


# ── Inactivity decay ──────────────────────────────────────────────────────────

def apply_inactivity_decay(
    db: Session,
    *,
    inactive_days: int = INACTIVE_DAYS,
    now: Optional[datetime] = None,
) -> int:
    """
    Mark active narratives as "inactive" when last_seen_at is older than
    *inactive_days* days.

    Returns the number of narratives decayed this call.
    The session is flushed but NOT committed — caller commits.
    """
    now = now or datetime.utcnow()
    cutoff = now - timedelta(days=inactive_days)

    stale: list[KGNarrative] = (
        db.query(KGNarrative)
        .filter(
            KGNarrative.status == "active",
            KGNarrative.last_seen_at < cutoff,
        )
        .all()
    )
    for narr in stale:
        narr.status = "inactive"

    if stale:
        db.flush()
    return len(stale)


# ── Emerging narrative detection ──────────────────────────────────────────────

@dataclass
class EmergingNarrative:
    narrative:       KGNarrative
    score:           float
    unique_sources:  int
    unique_entities: int


def get_emerging_narratives(
    db: Session,
    limit: int = 10,
) -> list[EmergingNarrative]:
    """
    Return the top *limit* narratives ranked by emerging-narrative score:

        score = velocity_score × log(1 + unique_sources) × log(1 + unique_entities)

    Narratives with velocity_score == 0 are excluded (no signal yet).
    """
    narratives: list[KGNarrative] = (
        db.query(KGNarrative)
        .filter(
            KGNarrative.velocity_score > 0,
            KGNarrative.status == "active",
        )
        .all()
    )

    results: list[EmergingNarrative] = []
    for narr in narratives:
        claim_ids: list[int] = [
            row.claim_id
            for row in db.query(KGNarrativeClaim)
            .filter_by(narrative_id=narr.id)
            .all()
        ]
        if not claim_ids:
            continue

        unique_sources: int = (
            db.query(KGClaim.source_id)
            .filter(KGClaim.id.in_(claim_ids))
            .distinct()
            .count()
        )
        unique_entities: int = (
            db.query(KGClaimEntity.entity_id)
            .filter(KGClaimEntity.claim_id.in_(claim_ids))
            .distinct()
            .count()
        )

        score = (
            (narr.velocity_score or 0.0)
            * math.log1p(unique_sources)
            * math.log1p(unique_entities)
        )
        results.append(EmergingNarrative(
            narrative=narr,
            score=score,
            unique_sources=unique_sources,
            unique_entities=unique_entities,
        ))

    results.sort(key=lambda x: x.score, reverse=True)
    return results[:limit]


# ── Alert severity computation ────────────────────────────────────────────────

def _compute_alert_severity(
    velocity: float,
    unique_sources: int,
    unique_entities: int,
) -> tuple[float, str]:
    """
    Return (severity_score, alert_type) in [0, 1].

    *velocity* is the narrative's velocity_score, which is now the EMA of
    Σ(claim.confidence × source.credibility_score) per day.  Low-credibility
    claim bursts therefore produce a lower velocity and thus lower severity
    than equally-sized bursts from high-credibility sources.

    Severity combines three normalised signals:

        velocity_component  = tanh(velocity / 3.0)
        source_component    = tanh(unique_sources / 5.0)
        entity_component    = tanh(unique_entities / 5.0)

        severity = (2 * velocity_component + source_component + entity_component) / 4

    tanh keeps values in (0, 1) and provides natural saturation.  The weight
    of 2 on velocity makes it the dominant signal, matching the operational
    priority of fast-moving narratives.

    The dominant component also selects the alert type:
      • velocity_component largest → velocity_spike
      • source_component   largest → source_surge
      • entity_component   largest → entity_surge
    """
    v_comp = math.tanh(velocity / 3.0)
    s_comp = math.tanh(unique_sources / 5.0)
    e_comp = math.tanh(unique_entities / 5.0)

    severity = (2.0 * v_comp + s_comp + e_comp) / 4.0

    if v_comp >= s_comp and v_comp >= e_comp:
        alert_type = ALERT_VELOCITY_SPIKE
    elif s_comp >= e_comp:
        alert_type = ALERT_SOURCE_SURGE
    else:
        alert_type = ALERT_ENTITY_SURGE

    return severity, alert_type


def _recent_alert_exists(
    db: Session,
    narrative_id: int,
    cutoff: datetime,
) -> bool:
    """True if an unresolved alert for this narrative was created after *cutoff*."""
    return (
        db.query(KGAlert)
        .filter(
            KGAlert.narrative_id == narrative_id,
            KGAlert.resolved_at.is_(None),
            KGAlert.created_at >= cutoff,
        )
        .first()
        is not None
    )


# ── Public alert API ──────────────────────────────────────────────────────────

def generate_alerts(
    db: Session,
    *,
    severity_threshold: float = ALERT_SEVERITY_THRESHOLD,
    cooldown_hours: int = ALERT_COOLDOWN_HOURS,
    now: Optional[datetime] = None,
) -> list[KGAlert]:
    """
    Inspect every active narrative and create a KGAlert when:
      1. The computed severity_score >= *severity_threshold*, AND
      2. No unresolved alert for that narrative exists within *cooldown_hours*.

    Returns the list of newly created KGAlert rows.
    The session is flushed but NOT committed — caller commits.
    """
    now = now or datetime.utcnow()
    cooldown_cutoff = now - timedelta(hours=cooldown_hours)

    active_narratives: list[KGNarrative] = (
        db.query(KGNarrative)
        .filter(KGNarrative.status == "active")
        .all()
    )

    new_alerts: list[KGAlert] = []

    for narr in active_narratives:
        claim_ids: list[int] = [
            row.claim_id
            for row in db.query(KGNarrativeClaim)
            .filter_by(narrative_id=narr.id)
            .all()
        ]
        if not claim_ids:
            continue

        unique_sources: int = (
            db.query(KGClaim.source_id)
            .filter(KGClaim.id.in_(claim_ids))
            .distinct()
            .count()
        )
        unique_entities: int = (
            db.query(KGClaimEntity.entity_id)
            .filter(KGClaimEntity.claim_id.in_(claim_ids))
            .distinct()
            .count()
        )

        severity, alert_type = _compute_alert_severity(
            velocity=narr.velocity_score or 0.0,
            unique_sources=unique_sources,
            unique_entities=unique_entities,
        )

        if severity < severity_threshold:
            continue

        if _recent_alert_exists(db, narr.id, cooldown_cutoff):
            continue

        message = (
            f"Narrative '{narr.label}' triggered {alert_type}: "
            f"velocity={narr.velocity_score:.2f}, "
            f"sources={unique_sources}, entities={unique_entities}, "
            f"severity={severity:.3f}"
        )
        alert = KGAlert(
            narrative_id=narr.id,
            alert_type=alert_type,
            severity_score=severity,
            message=message,
            created_at=now,
        )
        db.add(alert)
        new_alerts.append(alert)

    if new_alerts:
        db.flush()
    return new_alerts


def get_active_alerts(
    db: Session,
    limit: int = 50,
) -> list[KGAlert]:
    """Return unresolved alerts ordered newest-first."""
    return (
        db.query(KGAlert)
        .filter(KGAlert.resolved_at.is_(None))
        .order_by(KGAlert.created_at.desc())
        .limit(limit)
        .all()
    )
