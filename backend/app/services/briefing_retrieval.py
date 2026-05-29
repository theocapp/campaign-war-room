"""briefing_retrieval.py — structured intermediate for the morning briefing.

This module produces the new structured layer that sits between
the raw DB and the briefing memo. Two helpers:

  * `top_claims_for_briefing(db, days=7, limit=20)` — labeled, race-relevant
    ClaimRecords selected for memo grounding. Filtered by label allowlist
    + minimum quote length + race relevance. Ranked by a composite
    score of (label priority × outlet reliability × recency).

  * `top_entities_for_briefing(db, days=7)` — ~6 race-allowlist entities
    with mention deltas (this period vs prior period) and a few sample
    article titles. Always shows the candidate (Cognetti) and opponent
    (Bresnahan), plus the top 4 movers from a curated context allowlist.

Why these two? They are the structured retrieval outputs the briefing
endpoint will surface to:
  - the memo prompt (so the LLM has grounded quotes to cite, not just
    article summaries), and
  - the frontend's "Sources Used" / "Activity this week" cards.

What this module deliberately does NOT do:
  - Semantic narrative clustering (the failure mode that retired the
    previous KG pipeline — see CLAUDE.md "KG pivot").
  - Speaker attribution (no `speaker_entity_id` exists on ClaimRecord
    yet — the entities linked to a quote are who's NAMED in it, not
    necessarily who SAID it).
  - Cross-source semantic dedup (we dedup by `evidence_hash` only,
    which is syntactic — AP wire + 4 local pickups = 5 rows for one
    underlying story). File for Phase 2.

The structured intermediate this feeds into is documented in the
locked schema (see /briefing/morning endpoint).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    ClaimRecord,
    ClaimRecordEntity,
    Entity,
    EntityMention,
    Outlet,
    SourceItem,
)
from app.services.source_display import display_source_name


# ── Configuration (kept at module top for discoverability) ──────────────

LABEL_ALLOWLIST: tuple[str, ...] = (
    "attack",
    "endorsement",
    "vote",
    "commitment",
    "policy_position",
    "defense",
)
# 'statement' (catch-all) and 'announcement' deliberately excluded — too
# low-signal for memo grounding. The extraction prompt's label vocabulary
# is wider; we narrow at the retrieval layer so the policy is one place.

LABEL_PRIORITY: dict[str, float] = {
    "attack": 1.0,
    "endorsement": 1.0,
    "vote": 0.9,
    "commitment": 0.7,
    "policy_position": 0.6,
    "defense": 0.5,
}

# Filters byline junk like "She writes from Malvern, Pa." (28 chars) that
# the v15.0 extractor occasionally captures as a "claim".
MIN_QUOTE_LENGTH = 40

# Curated alias map. Auto-discovered canonical_ids that point to the
# same real-world entity as a seeded one. ONLY add after manually
# verifying — relatives sharing a last name are NOT aliases (Karen
# Bresnahan is a different person from Rob Bresnahan).
ENTITY_ALIASES: dict[str, str] = {
    "person:auto:rob-bresnahan-jr": "person:bresnahan",
    "person:auto:congressman-cartwright": "person:cartwright",
}

# Always-show entities for the briefing's "top entities" card.
ALWAYS_SHOW_ENTITIES: tuple[str, ...] = (
    "person:cognetti",
    "person:bresnahan",
)
# Plus top 4 movers from this curated context allowlist. Scoped narrowly
# to race-relevant figures + the bills the race is litigating, so the
# card doesn't drift into "trending names" wire-service noise.
CONTEXT_ENTITY_ALLOWLIST: tuple[str, ...] = (
    "person:trump",
    "person:shapiro",
    "person:cartwright",
    "org:dccc",
    "org:nrcc",
    "bill:stock-act",
    "bill:medicaid-cuts",
    "bill:tax-cuts",
    "bill:aca-subsidies",
)


# ── Helpers ────────────────────────────────────────────────────────────

def _aliases_of(canonical_id: str) -> list[str]:
    """Return the alias canonical_ids that point to `canonical_id`."""
    return [alias for alias, target in ENTITY_ALIASES.items() if target == canonical_id]


def _entity_ids_for_canonical(db: Session, canonical_id: str) -> list[int]:
    """Entity row IDs for a canonical_id + all known aliases pointing to it.

    Use when querying mention counts so seeded + auto-discovered aliases
    are folded into one total.
    """
    all_cids = [canonical_id] + _aliases_of(canonical_id)
    rows = db.query(Entity.id).filter(Entity.canonical_id.in_(all_cids)).all()
    return [row[0] for row in rows]


def _resolve_alias(canonical_id: str) -> str:
    """If `canonical_id` is an alias, return its target. Otherwise return as-is."""
    return ENTITY_ALIASES.get(canonical_id, canonical_id)


# ── Public retrieval API ───────────────────────────────────────────────

def top_claims_for_briefing(
    db: Session,
    days: int = 7,
    limit: int = 20,
) -> list[dict]:
    """Return labeled, race-relevant ClaimRecords for memo grounding.

    Filters:
        - Article `race_relevance_score >= 50`
        - Article not archived
        - Label in LABEL_ALLOWLIST
        - Quote length >= MIN_QUOTE_LENGTH
        - Article published within `days` days

    Ranking (composite, all 0-1):
        score = label_priority × reliability_weight × recency_weight
        where:
          reliability_weight = log(reliability_score or 50 + 1) / log(101)
          recency_weight     = max(0.1, 1 - days_old / days)

    Returns the top `limit` claims as plain dicts. Output shape:
        {
          claim_id, quote, label, entities[{id, name, affiliation}],
          outlet, reliability_score, published_at, article_id, article_url
        }
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    rows = (
        db.query(ClaimRecord, SourceItem, Outlet)
        .join(SourceItem, SourceItem.id == ClaimRecord.article_id)
        .outerjoin(Outlet, Outlet.id == SourceItem.outlet_id)
        .filter(ClaimRecord.label.in_(LABEL_ALLOWLIST))
        .filter(func.length(ClaimRecord.evidence_span) >= MIN_QUOTE_LENGTH)
        .filter(SourceItem.race_relevance_score >= 50)
        .filter(SourceItem.published_at >= cutoff)
        .filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
        .all()
    )
    if not rows:
        return []

    now = datetime.utcnow()
    scored: list[tuple[float, ClaimRecord, SourceItem, Outlet | None]] = []
    for cr, si, outlet in rows:
        label_p = LABEL_PRIORITY.get(cr.label, 0.5)
        reliability = (
            outlet.reliability_score
            if outlet and outlet.reliability_score is not None
            else 50  # NULL outlets treated as median
        )
        rel_w = math.log(max(reliability, 1) + 1) / math.log(101)
        days_old = max(0, (now - (si.published_at or now)).total_seconds() / 86400)
        recency_w = max(0.1, 1 - days_old / max(days, 1))
        score = label_p * rel_w * recency_w
        scored.append((score, cr, si, outlet))

    scored.sort(key=lambda x: -x[0])
    top = scored[:limit]

    # Batch-load entities for the top claims (avoid N+1)
    claim_ids = [cr.id for _, cr, _, _ in top]
    junction = (
        db.query(ClaimRecordEntity, Entity)
        .join(Entity, Entity.id == ClaimRecordEntity.entity_id)
        .filter(ClaimRecordEntity.claim_record_id.in_(claim_ids))
        .all()
    )
    entities_per_claim: dict[int, list[dict]] = {}
    for j, e in junction:
        entities_per_claim.setdefault(j.claim_record_id, []).append({
            "id": _resolve_alias(e.canonical_id),
            "name": e.name,
            "affiliation": e.affiliation,
        })

    out: list[dict] = []
    for _score, cr, si, outlet in top:
        out.append({
            "claim_id": cr.id,
            "quote": cr.evidence_span,
            "label": cr.label,
            "entities": entities_per_claim.get(cr.id, []),
            "outlet": display_source_name(si, outlet),
            "reliability_score": outlet.reliability_score if outlet else None,
            "published_at": si.published_at.isoformat() if si.published_at else None,
            "article_id": si.id,
            "article_url": si.source_url,
        })
    return out


def top_entities_for_briefing(
    db: Session,
    days: int = 7,
) -> list[dict]:
    """Return ~6 race-allowlist entities with mention deltas + sample titles.

    Always-show: cognetti, bresnahan (the race itself — never hidden).
    Plus the top 4 by mentions_this_period from CONTEXT_ENTITY_ALLOWLIST.

    Mentions are aliased — `person:bresnahan` + `person:auto:rob-bresnahan-jr`
    count as the same entity. Article-level distinct count (one entity
    mentioned 5 times in one article counts once).

    Output shape per row:
        {id, name, type, affiliation, mentions_this_week,
         mentions_last_week, delta, sample_recent_titles}
    """
    now = datetime.utcnow()
    this_start = now - timedelta(days=days)
    prev_start = now - timedelta(days=2 * days)

    def _data_for(canonical_id: str) -> dict | None:
        ent_ids = _entity_ids_for_canonical(db, canonical_id)
        if not ent_ids:
            return None
        seed = db.query(Entity).filter(Entity.canonical_id == canonical_id).one_or_none()
        if not seed:
            return None
        this_n = (
            db.query(func.count(func.distinct(EntityMention.article_id)))
            .join(SourceItem, SourceItem.id == EntityMention.article_id)
            .filter(EntityMention.entity_id.in_(ent_ids))
            .filter(SourceItem.published_at >= this_start)
            .scalar()
        ) or 0
        prev_n = (
            db.query(func.count(func.distinct(EntityMention.article_id)))
            .join(SourceItem, SourceItem.id == EntityMention.article_id)
            .filter(EntityMention.entity_id.in_(ent_ids))
            .filter(SourceItem.published_at >= prev_start)
            .filter(SourceItem.published_at < this_start)
            .scalar()
        ) or 0
        title_rows = (
            db.query(SourceItem.title)
            .join(EntityMention, EntityMention.article_id == SourceItem.id)
            .filter(EntityMention.entity_id.in_(ent_ids))
            .filter(SourceItem.published_at >= this_start)
            .filter(SourceItem.title.isnot(None))
            .order_by(SourceItem.published_at.desc())
            .limit(3)
            .all()
        )
        return {
            "id": seed.canonical_id,
            "name": seed.name,
            "type": seed.type,
            "affiliation": seed.affiliation,
            "mentions_this_week": this_n,
            "mentions_last_week": prev_n,
            "delta": this_n - prev_n,
            "sample_recent_titles": [r[0] for r in title_rows],
        }

    out: list[dict] = []
    for cid in ALWAYS_SHOW_ENTITIES:
        d = _data_for(cid)
        if d:
            out.append(d)

    context_rows = [_data_for(cid) for cid in CONTEXT_ENTITY_ALLOWLIST]
    context_rows = [r for r in context_rows if r]
    context_rows.sort(key=lambda r: -r["mentions_this_week"])
    out.extend(context_rows[:4])

    out.sort(key=lambda r: -r["mentions_this_week"])
    return out


def overnight_changes(
    db: Session,
    hours: int = 48,
    limit: int = 5,
) -> list[dict]:
    """Quick-glance 'what changed in the race' items for the briefing.

    Returns up to `limit` labeled claims from the last `hours` involving
    the actual race candidates (Cognetti or Bresnahan). High signal,
    narrow scope — designed to fit in a 3-5 line bullet list at the top
    of the briefing.

    Window default is 48h because labeled race-specific claims are
    genuinely sparse overnight (~1-2/day typical for PA-08). Frontend
    should hide the section gracefully when this returns [].

    Tradeoff note: this gate requires Cognetti or Bresnahan in the quote.
    Trump-only or Shapiro-only quotes don't qualify, which means national
    events with race implications won't surface here — but they'd also
    flood the section with off-race noise if allowed (e.g. Jen Kiggans
    in VA, Al Green in TX). The memo above this section can still draw
    on top_claims_for_briefing's wider 7d window for narrative context.

    Filtering:
        - Quote length >= MIN_QUOTE_LENGTH (no byline junk)
        - Label in LABEL_ALLOWLIST (no statement/announcement)
        - Article race_relevance_score >= 50, not archived
        - Article published within `hours` hours
        - Quote mentions at least one entity from the seeded race allowlist
          (always-show + context — same set as top_entities_for_briefing)

    Note we deliberately do NOT filter on content_category here — race
    entity mentions ARE the relevance signal for this section, and the
    extra filter would silently drop most material in a sparse window.

    Ranking: label_priority × log(reliability_score or 50) × recency.
    Same composite as top_claims but with a tighter time window.

    Output shape per row (subset of top_claims):
        {claim_id, quote, label, entities[], outlet, published_at,
         article_id, article_url}
    """
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    # OVERNIGHT GATE: require Cognetti OR Bresnahan in the quote — that's the
    # signal that this quote is about OUR race specifically. Trump/Shapiro
    # alone would let national-race noise (Jen Kiggans in VA, Al Green in TX,
    # etc.) through, because they're tagged as race-relevant entities but
    # often appear in quotes that have nothing to do with PA-08.
    race_entity_ids: list[int] = []
    for cid in ALWAYS_SHOW_ENTITIES:  # cognetti + bresnahan
        race_entity_ids.extend(_entity_ids_for_canonical(db, cid))
    race_entity_ids = list(set(race_entity_ids))
    if not race_entity_ids:
        return []

    rows = (
        db.query(ClaimRecord, SourceItem, Outlet)
        .join(SourceItem, SourceItem.id == ClaimRecord.article_id)
        .outerjoin(Outlet, Outlet.id == SourceItem.outlet_id)
        .join(ClaimRecordEntity, ClaimRecordEntity.claim_record_id == ClaimRecord.id)
        .filter(ClaimRecord.label.in_(LABEL_ALLOWLIST))
        .filter(func.length(ClaimRecord.evidence_span) >= MIN_QUOTE_LENGTH)
        .filter(SourceItem.race_relevance_score >= 50)
        .filter(SourceItem.published_at >= cutoff)
        .filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
        .filter(ClaimRecordEntity.entity_id.in_(race_entity_ids))
        .all()
    )
    # Python-side dedup of ClaimRecord (the JOIN to ClaimRecordEntity
    # can yield the same claim multiple times when it has multiple linked
    # entities). DISTINCT ON isn't portable to SQLite, so we dedup below.
    if not rows:
        return []

    # Composite rank — same shape as top_claims for consistency.
    now = datetime.utcnow()
    scored: list[tuple[float, ClaimRecord, SourceItem, Outlet | None]] = []
    seen_ids: set[int] = set()
    for cr, si, outlet in rows:
        if cr.id in seen_ids:
            continue
        seen_ids.add(cr.id)
        label_p = LABEL_PRIORITY.get(cr.label, 0.5)
        reliability = (
            outlet.reliability_score
            if outlet and outlet.reliability_score is not None
            else 50
        )
        rel_w = math.log(max(reliability, 1) + 1) / math.log(101)
        hours_old = max(0, (now - (si.published_at or now)).total_seconds() / 3600)
        recency_w = max(0.1, 1 - hours_old / max(hours, 1))
        score = label_p * rel_w * recency_w
        scored.append((score, cr, si, outlet))

    scored.sort(key=lambda x: -x[0])
    top = scored[:limit]

    # Batch-load entities for the selected claims (avoid N+1)
    claim_ids = [cr.id for _, cr, _, _ in top]
    junction = (
        db.query(ClaimRecordEntity, Entity)
        .join(Entity, Entity.id == ClaimRecordEntity.entity_id)
        .filter(ClaimRecordEntity.claim_record_id.in_(claim_ids))
        .all()
    )
    entities_per_claim: dict[int, list[dict]] = {}
    for j, e in junction:
        entities_per_claim.setdefault(j.claim_record_id, []).append({
            "id": _resolve_alias(e.canonical_id),
            "name": e.name,
            "affiliation": e.affiliation,
        })

    out: list[dict] = []
    for _score, cr, si, outlet in top:
        out.append({
            "claim_id": cr.id,
            "quote": cr.evidence_span,
            "label": cr.label,
            "entities": entities_per_claim.get(cr.id, []),
            "outlet": display_source_name(si, outlet),
            "published_at": si.published_at.isoformat() if si.published_at else None,
            "article_id": si.id,
            "article_url": si.source_url,
        })
    return out
