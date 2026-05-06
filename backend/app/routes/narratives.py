from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.models import ManualCapture, Narrative, NarrativeMention
from app.schemas import NarrativeBriefingOut, NarrativeBriefingCardsOut, NarrativeComparisonItem, NarrativeComparisonOut, NarrativeOut, DashboardNarrativeCard, NarrativeDetailOut
from app.services.narratives import refresh_narratives, top_narratives
from app.services.narrative_briefing import build_brief_cards

router = APIRouter()


@router.get("/narratives/briefing", response_model=NarrativeBriefingCardsOut)
def get_narrative_briefing(db: Session = Depends(get_db)):
    # Return a focused briefing payload using the centralized briefing builder
    narratives = top_narratives(db, limit=5)
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


def _owned_candidate_source_ids(db: Session) -> set[int]:
    return {
        row[0]
        for row in db.query(ManualCapture.source_item_id).filter(ManualCapture.candidate_related == True).all()  # noqa: E712
    }


def _outside_owned_channels(narrative: Narrative, candidate_owned_ids: set[int]) -> bool:
    for mention in narrative.mentions:
        source = mention.source_item
        if not source:
            continue
        if narrative.owner_type == "candidate":
            if source.id not in candidate_owned_ids and source.source_type not in {"campaign_note"}:
                return True
        elif source.source_type not in {"opponent_statement"}:
            return True
    return False


def _comparison_item(narrative: Narrative, candidate_owned_ids: set[int]) -> NarrativeComparisonItem:
    outside = _outside_owned_channels(narrative, candidate_owned_ids)
    if narrative.owner_type == "candidate" and not outside:
        practical = "Candidate frame is still mostly confined to campaign-owned material."
    elif narrative.owner_type == "candidate" and outside:
        practical = "Candidate frame has some evidence outside campaign-owned channels."
    elif narrative.owner_type == "opponent" and narrative.status == "rising":
        practical = "Opponent frame appears to be gaining traction across the evidence base."
    elif narrative.owner_type == "opponent":
        practical = "Opponent frame is present; monitor for repetition before escalating."
    else:
        practical = "Narrative attribution is limited; treat this as an early signal."
    return NarrativeComparisonItem(
        narrative_id=narrative.id,
        short_label=narrative.short_label,
        owner_type=narrative.owner_type,
        narrative_type=narrative.narrative_type,
        status=narrative.status,
        traction_score=narrative.traction_score,
        evidence_strength=narrative.evidence_strength,
        source_cluster_count=narrative.source_cluster_count,
        messenger_diversity_count=narrative.messenger_diversity_count,
        geography_count=narrative.geography_count,
        outside_owned_channels=outside,
        practical_read=practical,
    )


@router.get("/narratives/compare", response_model=NarrativeComparisonOut)
def compare_narratives(db: Session = Depends(get_db)):
    refresh_narratives(db)
    narratives = (
        db.query(Narrative)
        .options(joinedload(Narrative.mentions).joinedload(NarrativeMention.source_item))
        .order_by(Narrative.traction_score.desc(), Narrative.last_seen_at.desc())
        .limit(50)
        .all()
    )
    candidate_owned_ids = _owned_candidate_source_ids(db)
    items = [_comparison_item(narrative, candidate_owned_ids) for narrative in narratives]
    opponents = [item for item in items if item.owner_type == "opponent"]
    candidates = [item for item in items if item.owner_type == "candidate"]
    candidate_owned_only = [item for item in candidates if not item.outside_owned_channels]
    candidate_broader = [item for item in candidates if item.outside_owned_channels]
    opponent_rising = [item for item in opponents if item.status == "rising"]
    needs_response = [item for item in opponents if item.status in {"rising", "emerging"} and item.traction_score >= 45]
    ready_to_amplify = [
        item for item in candidates
        if item.outside_owned_channels and item.evidence_strength in {"moderate", "strong"}
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
    """Return normalized narrative briefing cards for consumers (dashboard or API)."""
    narratives = top_narratives(db, limit=limit)
    cards = build_brief_cards(db=db, narratives=narratives, limit=limit)
    return cards


@router.get("/narratives/{narrative_id}", response_model=NarrativeDetailOut)
def get_narrative_detail(narrative_id: int, db: Session = Depends(get_db)):
    """Return full narrative detail with all supporting evidence mentions."""
    from app.services.narrative_briefing import _derive_brief_fields
    
    try:
        narrative = (
            db.query(Narrative)
            .options(joinedload(Narrative.mentions).joinedload(NarrativeMention.source_item))
            .filter(Narrative.id == narrative_id)
            .first()
        )
    except OperationalError as exc:
        if "no such table" not in str(exc).lower():
            raise
        narrative = None
    if not narrative:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Narrative not found")
    
    # Get briefing fields to populate detail view
    derived = _derive_brief_fields(narrative, db)
    
    # Build why_it_matters text (same logic as briefing cards)
    if narrative.owner_confidence == "low" or narrative.attribution_type in {"unclear", "media_frame"}:
        why = "Attribution is not strong enough to call this an opponent attack."
    elif narrative.evidence_strength == "weak":
        why = "Treat this as an early signal; evidence is still narrow."
    elif narrative.status == "rising":
        why = (
            f"Appears in {narrative.source_cluster_count} distinct source clusters "
            f"from {narrative.messenger_diversity_count} messenger/source types."
        )
    elif narrative.response_status in {"response_ready", "no_response"} and narrative.owner_type == "opponent":
        why = "Opponent-owned frame is confirmed in the evidence base."
    else:
        why = "Monitor whether this frame spreads beyond its current evidence base."
    
    # Convert mentions to proper schema with source details
    mentions_out = []
    for mention in narrative.mentions:
        mention_dict = {
            "id": mention.id,
            "narrative_id": mention.narrative_id,
            "source_item_id": mention.source_item_id,
            "opponent_activity_id": mention.opponent_activity_id,
            "source_cluster_id": mention.source_cluster_id,
            "matched_text": mention.matched_text,
            "mention_role": mention.mention_role,
            "confidence_score": mention.confidence_score,
            "owner_confidence": mention.owner_confidence,
            "attribution_type": mention.attribution_type,
            "target_confidence": mention.target_confidence,
            "candidate_narrative_id": mention.candidate_narrative_id,
            "created_at": mention.created_at,
            "source_item": None,
        }
        if mention.source_item:
            from app.services.snapshots import source_out
            mention_dict["source_item"] = source_out(mention.source_item)
        mentions_out.append(mention_dict)
    
    return NarrativeDetailOut(
        id=narrative.id,
        canonical_text=narrative.canonical_text,
        short_label=narrative.short_label,
        narrative_type=narrative.narrative_type,
        owner_type=narrative.owner_type,
        direction=narrative.direction,
        status=narrative.status,
        first_seen_at=narrative.first_seen_at,
        last_seen_at=narrative.last_seen_at,
        source_cluster_count=narrative.source_cluster_count,
        source_count=narrative.source_count,
        messenger_diversity_count=narrative.messenger_diversity_count,
        geography_count=narrative.geography_count,
        traction_score=narrative.traction_score,
        evidence_strength=narrative.evidence_strength,
        response_status=narrative.response_status,
        owner_confidence=narrative.owner_confidence,
        attribution_type=narrative.attribution_type,
        target_confidence=narrative.target_confidence,
        notes=narrative.notes,
        what_changed=derived.get("what_changed"),
        why_it_matters=why,
        spread_summary=derived.get("spread_summary"),
        risk_or_opportunity=derived.get("risk_or_opportunity"),
        action=derived.get("action"),
        momentum_shift=derived.get("momentum_shift"),
        recent_window_summary=derived.get("recent_window_summary"),
        mentions=[m for m in mentions_out if m]  # Filter out None entries
    )
