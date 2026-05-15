from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.knowledge_graph.orm import KGClaim, KGNarrative, KGNarrativeClaim, KGSource
from app.models import SourceItem
from app.schemas import (
    NarrativeBriefingCardsOut,
    NarrativeComparisonItem,
    NarrativeComparisonOut,
    DashboardNarrativeCard,
    NarrativeDetailOut,
    NarrativeMentionOut,
)
from app.services.narrative_briefing import (
    _derive_card_base,
    _derive_brief_fields,
    _derive_narrative_fields,
    _kg_status_to_legacy,
    _kg_velocity_to_traction,
    _evidence_strength,
    _load_claims_and_sources,
    _why_it_matters,
    build_brief_cards,
)
from app.services.snapshots import source_out

router = APIRouter()


def _top_kg_narratives(db: Session, limit: int = 10) -> list[KGNarrative]:
    return (
        db.query(KGNarrative)
        .options(
            joinedload(KGNarrative.claim_links)
            .joinedload(KGNarrativeClaim.claim)
            .joinedload(KGClaim.source)
        )
        .filter(KGNarrative.status == "active")
        .order_by(KGNarrative.velocity_score.desc())
        .limit(limit)
        .all()
    )


@router.get("/narratives/briefing", response_model=NarrativeBriefingCardsOut)
def get_narrative_briefing(db: Session = Depends(get_db)):
    narratives = _top_kg_narratives(db, limit=5)
    cards = build_brief_cards(db=db, narratives=narratives, limit=5)
    if not cards:
        summary = "No high-signal campaign narratives have enough evidence yet. Add opponent statements, manual captures, or local coverage to begin tracking message traction."
    else:
        rising = [c for c in cards if c.status == "rising"]
        weak = [c for c in cards if c.evidence_strength == "weak"]
        summary = (
            f"{len(cards)} narrative{'s' if len(cards) != 1 else ''} are being tracked. "
            f"{len(rising)} appear to be rising; {len(weak)} still have weak evidence."
        )
    return NarrativeBriefingCardsOut(
        narratives=cards,
        summary=summary,
        generated_at=datetime.utcnow(),
    )


def _outside_owned_channels_kg(kg_sources: list[KGSource], owner_type: str) -> bool:
    """Return True if this narrative has evidence from outside campaign-owned sources."""
    for src in kg_sources:
        if not src.source_type:
            continue
        if owner_type == "candidate":
            if src.source_type not in {"campaign_note"}:
                return True
        elif src.source_type not in {"opponent_statement"}:
            return True
    return False


def _comparison_item_from_kg(kg_narr: KGNarrative) -> NarrativeComparisonItem:
    claims, kg_sources = _load_claims_and_sources(kg_narr)
    base = _derive_card_base(kg_narr, claims, kg_sources)
    outside = _outside_owned_channels_kg(kg_sources, base["owner_type"])

    owner_type = base["owner_type"]
    status = base["status"]
    if owner_type == "candidate" and not outside:
        practical = "Candidate frame is still mostly confined to campaign-owned material."
    elif owner_type == "candidate" and outside:
        practical = "Candidate frame has some evidence outside campaign-owned channels."
    elif owner_type == "opponent" and status == "rising":
        practical = "Opponent frame appears to be gaining traction across the evidence base."
    elif owner_type == "opponent":
        practical = "Opponent frame is present; monitor for repetition before escalating."
    else:
        practical = "Narrative attribution is limited; treat this as an early signal."

    return NarrativeComparisonItem(
        narrative_id=kg_narr.id,
        short_label=base["short_label"],
        owner_type=owner_type,
        narrative_type=base["narrative_type"],
        status=status,
        traction_score=base["traction_score"],
        evidence_strength=base["evidence_strength"],
        source_cluster_count=base["source_cluster_count"],
        messenger_diversity_count=base["messenger_diversity_count"],
        geography_count=base["geography_count"],
        outside_owned_channels=outside,
        practical_read=practical,
    )


@router.get("/narratives/compare", response_model=NarrativeComparisonOut)
def compare_narratives(db: Session = Depends(get_db)):
    kg_narratives = (
        db.query(KGNarrative)
        .options(
            joinedload(KGNarrative.claim_links)
            .joinedload(KGNarrativeClaim.claim)
            .joinedload(KGClaim.source)
        )
        .filter(KGNarrative.status.in_(["active", "inactive"]))
        .order_by(KGNarrative.velocity_score.desc())
        .limit(50)
        .all()
    )
    items = [_comparison_item_from_kg(n) for n in kg_narratives]
    opponents = [i for i in items if i.owner_type == "opponent"]
    candidates = [i for i in items if i.owner_type == "candidate"]
    candidate_owned_only = [i for i in candidates if not i.outside_owned_channels]
    candidate_broader = [i for i in candidates if i.outside_owned_channels]
    opponent_rising = [i for i in opponents if i.status == "rising"]
    needs_response = [i for i in opponents if i.status in {"rising", "emerging"} and i.traction_score >= 45]
    ready_to_amplify = [
        i for i in candidates
        if i.outside_owned_channels and i.evidence_strength in {"moderate", "strong"}
    ]
    if not candidates:
        summary = "No candidate narratives from the message library have enough matched evidence yet."
    else:
        summary = (
            f"{len(candidates)} candidate narrative{'s' if len(candidates) != 1 else ''} tracked; "
            f"{len(candidate_broader)} show evidence outside campaign-owned channels. "
            "This is traction evidence, not proof of persuasion."
        )
    return NarrativeComparisonOut(
        top_opponent_narratives=opponents[:3],
        top_candidate_narratives=candidates[:3],
        candidate_owned_only=candidate_owned_only[:3],
        candidate_broader_spread=candidate_broader[:3],
        opponent_rising_faster=opponent_rising[:3],
        ready_to_amplify=ready_to_amplify[:3],
        needs_response=needs_response[:3],
        summary=summary,
        generated_at=datetime.utcnow(),
    )


@router.get("/narratives/briefs", response_model=list[DashboardNarrativeCard])
def get_narrative_briefs(limit: int = 5, db: Session = Depends(get_db)):
    narratives = _top_kg_narratives(db, limit=limit)
    return build_brief_cards(db=db, narratives=narratives, limit=limit)


@router.get("/narratives/debug")
def narrative_debug_stats(db: Session = Depends(get_db)):
    from sqlalchemy import func
    kg_active = db.query(KGNarrative).filter(KGNarrative.status == "active").count()
    kg_inactive = db.query(KGNarrative).filter(KGNarrative.status == "inactive").count()
    kg_merged = db.query(KGNarrative).filter(KGNarrative.status == "merged").count()

    rows = (
        db.query(KGSource.source_item_id, KGNarrativeClaim.narrative_id)
        .join(KGClaim, KGClaim.source_id == KGSource.id)
        .join(KGNarrativeClaim, KGNarrativeClaim.claim_id == KGClaim.id)
        .filter(KGSource.source_item_id.isnot(None))
        .distinct()
        .all()
    )
    si_to_narratives: dict[int, set[int]] = {}
    for si_id, narr_id in rows:
        si_to_narratives.setdefault(si_id, set()).add(narr_id)
    divergent_count = sum(1 for nids in si_to_narratives.values() if len(nids) > 1)

    return {
        "kg_narrative_count": kg_active,
        "kg_inactive_count": kg_inactive,
        "kg_merged_count": kg_merged,
        "source_items_in_multiple_kg_narratives": divergent_count,
        "divergence_detected": divergent_count > 0,
        "note": "kg_narrative_count is the authoritative active count.",
    }


# ── Narrative overview ─────────────────────────────────────────────────────────

class NarrativeCoverageWeek(BaseModel):
    week_start: str
    count: int


class NarrativeRecentSource(BaseModel):
    title: str
    source_name: str
    published_at: Optional[str]
    source_url: Optional[str]


class NarrativeOverviewCard(BaseModel):
    id: int
    label: str
    owner_type: str
    direction: str
    status: str
    traction_score: int
    source_count: int
    first_seen_at: Optional[str]
    last_seen_at: Optional[str]
    outlets: list[str]
    coverage_by_week: list[NarrativeCoverageWeek]
    recent_sources: list[NarrativeRecentSource]


class NarrativeOverviewOut(BaseModel):
    top: list[NarrativeOverviewCard]
    rising: list[NarrativeOverviewCard]


def _build_overview_card(kg_narr: KGNarrative) -> NarrativeOverviewCard:
    claims, kg_sources = _load_claims_and_sources(kg_narr)
    base = _derive_card_base(kg_narr, claims, kg_sources)

    # Outlets: distinct source_name values from KGSource
    outlets = list(dict.fromkeys(
        s.source_name for s in kg_sources if s.source_name
    ))[:4]

    # Weekly coverage buckets from KGSource published_at
    week_counts: dict[str, int] = defaultdict(int)
    for src in kg_sources:
        dt = src.published_at or src.ingested_at
        if not dt:
            continue
        monday = dt - timedelta(days=dt.weekday())
        week_counts[monday.strftime("%Y-%m-%d")] += 1

    coverage_by_week = [
        NarrativeCoverageWeek(week_start=k, count=v)
        for k, v in sorted(week_counts.items())
    ]

    # Recent sources: top 3 KGSource by published_at desc
    sorted_sources = sorted(kg_sources, key=lambda s: s.published_at or s.ingested_at or datetime.min, reverse=True)
    recent_sources = [
        NarrativeRecentSource(
            title=s.title or "",
            source_name=s.source_name or "",
            published_at=s.published_at.isoformat() if s.published_at else None,
            source_url=s.url,
        )
        for s in sorted_sources[:3]
    ]

    return NarrativeOverviewCard(
        id=kg_narr.id,
        label=base["short_label"],
        owner_type=base["owner_type"],
        direction=base["direction"],
        status=base["status"],
        traction_score=base["traction_score"],
        source_count=base["source_count"],
        first_seen_at=kg_narr.first_seen_at.isoformat() if kg_narr.first_seen_at else None,
        last_seen_at=kg_narr.last_seen_at.isoformat() if kg_narr.last_seen_at else None,
        outlets=outlets,
        coverage_by_week=coverage_by_week,
        recent_sources=recent_sources,
    )


@router.get("/narratives/overview", response_model=NarrativeOverviewOut)
def get_narrative_overview(db: Session = Depends(get_db)):
    # Require at least 2 KGSources — single-source extractions are too narrow.
    all_kg = (
        db.query(KGNarrative)
        .options(
            joinedload(KGNarrative.claim_links)
            .joinedload(KGNarrativeClaim.claim)
            .joinedload(KGClaim.source)
        )
        .filter(KGNarrative.status == "active")
        .order_by(KGNarrative.velocity_score.desc())
        .all()
    )

    # Filter to those with at least 2 distinct sources
    multi_source = [
        n for n in all_kg
        if len({c.claim.source_id for c in n.claim_links if c.claim and c.claim.source_id}) >= 2
    ]

    top = [_build_overview_card(n) for n in multi_source[:8]]
    rising = [_build_overview_card(n) for n in multi_source if _kg_status_to_legacy(n) in ("rising", "emerging")][:5]

    return NarrativeOverviewOut(top=top, rising=rising)


@router.get("/narratives/{narrative_id}", response_model=NarrativeDetailOut)
def get_narrative_detail(narrative_id: int, db: Session = Depends(get_db)):
    from fastapi import HTTPException

    kg_narr = (
        db.query(KGNarrative)
        .options(
            joinedload(KGNarrative.claim_links)
            .joinedload(KGNarrativeClaim.claim)
            .joinedload(KGClaim.source)
        )
        .filter(KGNarrative.id == narrative_id)
        .first()
    )
    if not kg_narr:
        raise HTTPException(status_code=404, detail="Narrative not found")

    claims, kg_sources = _load_claims_and_sources(kg_narr)
    base = _derive_card_base(kg_narr, claims, kg_sources)
    brief = _derive_brief_fields(kg_narr, claims, kg_sources, base)
    why = _why_it_matters(base)

    owner_confidence = base["owner_confidence"]
    attribution_type = base["attribution_type"]
    target_confidence = base["target_confidence"]

    # Build mentions from KGNarrativeClaim → KGClaim → KGSource → SourceItem
    mentions_out: list[NarrativeMentionOut] = []
    for link in kg_narr.claim_links:
        claim = link.claim
        if not claim:
            continue
        src = claim.source
        si_id = src.source_item_id if src else None
        si = db.get(SourceItem, si_id) if si_id else None
        mentions_out.append(NarrativeMentionOut(
            id=claim.id,
            narrative_id=kg_narr.id,
            source_item_id=si_id,
            opponent_activity_id=None,
            source_cluster_id=None,
            matched_text=(claim.normalized_text or claim.text or "")[:500],
            mention_role="evidence",
            confidence_score=int((claim.confidence or 0.5) * 100),
            owner_confidence=owner_confidence,
            attribution_type=attribution_type,
            target_confidence=target_confidence,
            candidate_narrative_id=None,
            source_platform=src.source_type if src else None,
            source_author_name=None,
            target_person=None,
            stance=claim.stance or "neutral",
            published_at=src.published_at if src else None,
            ingested_at=src.ingested_at if src else None,
            created_at=link.added_at or datetime.utcnow(),
            source_item=source_out(si) if si else None,
        ))

    return NarrativeDetailOut(
        id=kg_narr.id,
        canonical_text=base["canonical_text"],
        short_label=base["short_label"],
        narrative_type=base["narrative_type"],
        owner_type=base["owner_type"],
        direction=base["direction"],
        status=base["status"],
        first_seen_at=kg_narr.first_seen_at,
        last_seen_at=kg_narr.last_seen_at,
        source_cluster_count=base["source_cluster_count"],
        source_count=base["source_count"],
        messenger_diversity_count=base["messenger_diversity_count"],
        geography_count=base["geography_count"],
        traction_score=base["traction_score"],
        evidence_strength=base["evidence_strength"],
        response_status=base["response_status"],
        owner_confidence=owner_confidence,
        attribution_type=attribution_type,
        target_confidence=target_confidence,
        notes=None,
        what_changed=brief.get("what_changed"),
        why_it_matters=why,
        spread_summary=brief.get("spread_summary"),
        risk_or_opportunity=brief.get("risk_or_opportunity"),
        action=brief.get("action"),
        momentum_shift=brief.get("momentum_shift"),
        recent_window_summary=brief.get("recent_window_summary"),
        mentions=mentions_out,
    )
