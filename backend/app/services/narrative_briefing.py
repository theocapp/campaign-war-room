"""
Narrative briefing builder — reads directly from KGNarrative.

All projection logic that was in kg_narrative_projection.py now lives here.
This is the single source of truth for converting KGNarrative rows into
DashboardNarrativeCard objects.
"""
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy.orm import Session

from app.knowledge_graph.orm import KGClaim, KGNarrative, KGNarrativeClaim, KGSource
from app.models import SourceItem
from app.schemas import DashboardNarrativeCard
from app.services.snapshots import source_out


# ── KGNarrative projection helpers ────────────────────────────────────────────

def _kg_status_to_legacy(kg_narr: KGNarrative) -> str:
    if kg_narr.status == "inactive":
        return "fading"
    velocity = kg_narr.velocity_score or 0.0
    if velocity >= 2.0:
        return "rising"
    if velocity >= 0.5:
        return "stable"
    return "emerging"


def _kg_velocity_to_traction(velocity: float) -> int:
    return min(100, max(0, int(velocity * 20)))


def _evidence_strength(source_count: int, claim_count: int) -> str:
    if source_count >= 3 and claim_count >= 5:
        return "strong"
    if source_count >= 2 or claim_count >= 3:
        return "moderate"
    return "weak"


def _derive_narrative_fields(
    claims: list[KGClaim],
    kg_sources: list[KGSource],
) -> tuple[str, str, str, str, str]:
    """Return (narrative_type, owner_type, direction, stance, response_status)."""
    source_types = [s.source_type for s in kg_sources if s.source_type]
    stances = [c.stance for c in claims]

    total_sources = len(source_types) or 1
    opponent_count = sum(1 for st in source_types if st == "opponent_statement")
    candidate_count = sum(1 for st in source_types if st == "campaign_note")

    oppose_count = sum(1 for s in stances if s == "oppose")
    support_count = sum(1 for s in stances if s == "support")

    if opponent_count / total_sources >= 0.4:
        owner_type = "opponent"
        if oppose_count > support_count:
            return "opponent_attack", owner_type, "against_candidate", "attack", "no_response"
        return "policy_frame", owner_type, "neutral", "neutral", "no_response"

    if candidate_count / total_sources >= 0.4:
        return "candidate_self_definition", "candidate", "for_candidate", "support", "no_response"

    return "media_frame", "media", "neutral", "neutral", "no_response"


def _load_claims_and_sources(kg_narr: KGNarrative) -> tuple[list[KGClaim], list[KGSource]]:
    claims = [link.claim for link in kg_narr.claim_links if link.claim]
    seen: set[int] = set()
    kg_sources: list[KGSource] = []
    for claim in claims:
        if claim.source and claim.source_id not in seen:
            seen.add(claim.source_id)
            kg_sources.append(claim.source)
    return claims, kg_sources


def _derive_card_base(
    kg_narr: KGNarrative,
    claims: list[KGClaim],
    kg_sources: list[KGSource],
) -> dict:
    source_count = len({s.id for s in kg_sources})
    claim_count = len(claims)

    narrative_type, owner_type, direction, stance, response_status = _derive_narrative_fields(claims, kg_sources)
    status = _kg_status_to_legacy(kg_narr)
    traction_score = _kg_velocity_to_traction(kg_narr.velocity_score or 0.0)
    ev_strength = _evidence_strength(source_count, claim_count)

    messenger_types = {s.source_type for s in kg_sources if s.source_type}
    messenger_diversity_count = len(messenger_types)
    cluster_count = len({s.source_name for s in kg_sources if s.source_name})
    cluster_count = max(cluster_count, min(1, source_count))

    owner_confidence = "high" if owner_type in {"opponent", "candidate"} else "low"
    attribution_type = (
        "opponent_owned_source" if owner_type == "opponent"
        else "candidate_owned_source" if owner_type == "candidate"
        else "media_frame"
    )
    short_label = kg_narr.label if len(kg_narr.label) <= 72 else kg_narr.label[:69] + "..."
    canonical_text = kg_narr.description or kg_narr.label

    return {
        "short_label": short_label,
        "canonical_text": canonical_text,
        "narrative_type": narrative_type,
        "owner_type": owner_type,
        "direction": direction,
        "stance": stance,
        "status": status,
        "traction_score": traction_score,
        "evidence_strength": ev_strength,
        "response_status": response_status,
        "owner_confidence": owner_confidence,
        "attribution_type": attribution_type,
        "target_confidence": "medium" if owner_type == "opponent" else "low",
        "source_count": source_count,
        "source_cluster_count": cluster_count,
        "messenger_diversity_count": messenger_diversity_count,
        "geography_count": 0,
    }


def _derive_brief_fields(
    kg_narr: KGNarrative,
    claims: list[KGClaim],
    kg_sources: list[KGSource],
    base: dict,
) -> dict:
    owned_types = {"campaign_note", "opponent_statement"}
    now = datetime.utcnow()
    recent_cutoff = now - timedelta(days=7)
    prior_cutoff = now - timedelta(days=14)

    def src_date(s: KGSource) -> Optional[datetime]:
        return s.published_at or s.ingested_at

    dated = [s for s in kg_sources if src_date(s)]
    recent = [s for s in dated if src_date(s) >= recent_cutoff]
    prior = [s for s in dated if prior_cutoff <= src_date(s) < recent_cutoff]

    recent_count = len(recent)
    prior_count = len(prior)

    escaped_owned = any(s.source_type not in owned_types for s in kg_sources)
    escaped_owned_recently = any(s.source_type not in owned_types for s in recent)

    recent_clusters = {f"kgsrc-{s.id}" for s in recent}
    prior_clusters = {f"kgsrc-{s.id}" for s in prior}
    new_clusters = recent_clusters - prior_clusters

    recent_messengers = {s.source_type for s in recent if s.source_type}
    prior_messengers = {s.source_type for s in prior if s.source_type}
    new_messengers = recent_messengers - prior_messengers

    momentum_shift = "unchanged"
    if prior_count == 0 and recent_count >= 2:
        momentum_shift = "stronger"
    elif prior_count > 0:
        if recent_count >= prior_count * 1.5 and (recent_count - prior_count) >= 2:
            momentum_shift = "stronger"
        elif recent_count <= prior_count * 0.7 and (prior_count - recent_count) >= 2:
            momentum_shift = "weaker"

    cluster_count = base["source_cluster_count"]
    status = base["status"]
    owner_type = base["owner_type"]
    evidence_strength = base["evidence_strength"]
    response_status = base["response_status"]

    if escaped_owned and cluster_count > 1:
        spread_summary = "Has begun to appear outside owned channels and across multiple clusters."
    elif cluster_count >= 3:
        spread_summary = "Multiple distinct source clusters and messenger types detected."
    elif cluster_count == 1:
        spread_summary = "Currently confined to a single source cluster."
    else:
        spread_summary = None

    change_lines: list[str] = []
    if len(new_clusters) > 0 and escaped_owned_recently:
        change_lines.append(
            f"Now appearing in {len(new_clusters)} new source cluster"
            f"{'' if len(new_clusters) == 1 else 's'} and outside owned channels."
        )
    elif len(new_clusters) > 0:
        change_lines.append(
            f"Now appearing in {len(new_clusters)} new source cluster"
            f"{'' if len(new_clusters) == 1 else 's'}."
        )
    elif escaped_owned_recently:
        change_lines.append("Now appearing outside owned channels.")

    if new_messengers:
        messengers = sorted(new_messengers)
        label = "New messenger" if len(messengers) == 1 else "New messengers"
        change_lines.append(f"{label}: {', '.join(messengers[:3])}.")

    if recent_count or prior_count:
        if recent_count > prior_count:
            change_lines.append(
                f"Activity increased from {prior_count} mention"
                f"{'' if prior_count == 1 else 's'} last week to {recent_count} this week."
            )
        elif recent_count < prior_count:
            change_lines.append(
                f"Activity decreased from {prior_count} mention"
                f"{'' if prior_count == 1 else 's'} last week to {recent_count} this week."
            )

    if change_lines:
        what_changed = " ".join(change_lines[:2])
    elif status == "rising":
        what_changed = "This narrative is rising, but no new cluster or messenger signal is isolated yet."
    elif status == "fading":
        what_changed = "Recent activity is weaker than the prior baseline."
    else:
        what_changed = "No major recent change detected."

    if owner_type == "opponent" and status in {"rising", "emerging"}:
        risk_or_opportunity = "risk"
    elif owner_type == "candidate" and status == "rising":
        risk_or_opportunity = "opportunity"
    else:
        risk_or_opportunity = "monitor"

    if response_status == "response_ready" and owner_type == "opponent":
        action = "respond"
    elif owner_type == "candidate" and escaped_owned and evidence_strength in {"moderate", "strong"}:
        action = "amplify"
    elif evidence_strength == "weak" and status == "emerging":
        action = "monitor"
    else:
        action = "ignore" if evidence_strength == "weak" else "monitor"

    evidence_summary = (
        f"{cluster_count} clusters · {base['messenger_diversity_count']} messengers "
        f"· {base['geography_count']} geographies"
    )
    recent_window_summary = (
        f"{recent_count} mentions in last 7 days vs {prior_count} in prior week."
        if (recent_count or prior_count) else None
    )

    return {
        "what_changed": what_changed,
        "spread_summary": spread_summary,
        "risk_or_opportunity": risk_or_opportunity,
        "action": action,
        "confidence": base["owner_confidence"],
        "evidence_summary": evidence_summary,
        "new_messenger_types": list(new_messengers) if recent_count else [],
        "new_source_clusters_count": len(new_clusters) if recent_count else 0,
        "new_geographies": [],
        "escaped_owned_recently": escaped_owned_recently,
        "momentum_shift": momentum_shift,
        "recent_window_summary": recent_window_summary,
    }


def _why_it_matters(base: dict) -> str:
    owner_confidence = base["owner_confidence"]
    attribution_type = base.get("attribution_type", "")
    evidence_strength = base["evidence_strength"]
    status = base["status"]
    source_cluster_count = base["source_cluster_count"]
    messenger_diversity_count = base["messenger_diversity_count"]
    response_status = base["response_status"]
    owner_type = base["owner_type"]

    if owner_confidence == "low" or attribution_type in {"unclear", "media_frame"}:
        return "Attribution is not strong enough to call this an opponent attack."
    if evidence_strength == "weak":
        return "Treat this as an early signal; evidence is still narrow."
    if status == "rising":
        return (
            f"Appears in {source_cluster_count} distinct source clusters "
            f"from {messenger_diversity_count} messenger/source types."
        )
    if response_status in {"response_ready", "no_response"} and owner_type == "opponent":
        return "Opponent-owned frame is confirmed in the evidence base."
    return "Monitor whether this frame spreads beyond its current evidence base."


def build_brief_cards(
    db: Session,
    narratives: List[KGNarrative],
    limit: int = 5,
) -> List[DashboardNarrativeCard]:
    """
    Build DashboardNarrativeCard objects from KGNarrative rows.

    Each KGNarrative must have claim_links eagerly loaded with claim and source.
    Use joinedload(KGNarrative.claim_links).joinedload(KGNarrativeClaim.claim)
    .joinedload(KGClaim.source) when querying.
    """
    cards: List[DashboardNarrativeCard] = []

    for kg_narr in narratives[:limit]:
        claims, kg_sources = _load_claims_and_sources(kg_narr)
        base = _derive_card_base(kg_narr, claims, kg_sources)
        brief = _derive_brief_fields(kg_narr, claims, kg_sources, base)

        seed_src = next((s for s in kg_sources if s.source_item_id), None)
        source_item_id = seed_src.source_item_id if seed_src else None
        source_title = seed_src.title if seed_src else None
        source_url = seed_src.url if seed_src else None

        top_supporting = []
        si_ids = [s.source_item_id for s in kg_sources if s.source_item_id][:3]
        if si_ids:
            si_objs = db.query(SourceItem).filter(SourceItem.id.in_(si_ids)).all()
            top_supporting = [source_out(s) for s in si_objs]

        verify_links = [s.url for s in kg_sources[:3] if s.url]
        why = _why_it_matters(base)

        cards.append(DashboardNarrativeCard(
            narrative_id=kg_narr.id,
            short_label=base["short_label"],
            canonical_text=base["canonical_text"],
            narrative_type=base["narrative_type"],
            owner_type=base["owner_type"],
            direction=base["direction"],
            status=base["status"],
            traction_score=base["traction_score"],
            evidence_strength=base["evidence_strength"],
            response_status=base["response_status"],
            owner_confidence=base["owner_confidence"],
            attribution_type=base["attribution_type"],
            target_confidence=base["target_confidence"],
            source_count=base["source_count"],
            source_cluster_count=base["source_cluster_count"],
            messenger_diversity_count=base["messenger_diversity_count"],
            geography_count=base["geography_count"],
            why_it_matters=why,
            source_item_id=source_item_id,
            source_title=source_title,
            source_url=source_url,
            what_changed=brief.get("what_changed"),
            why_it_matters_now=why,
            spread_summary=brief.get("spread_summary"),
            risk_or_opportunity=brief.get("risk_or_opportunity"),
            action=brief.get("action"),
            confidence=brief.get("confidence"),
            evidence_summary=brief.get("evidence_summary"),
            top_supporting_sources=top_supporting,
            verify_links=verify_links,
            change_summary=None,
            new_messenger_types=brief.get("new_messenger_types") or [],
            new_source_clusters_count=brief.get("new_source_clusters_count") or 0,
            new_geographies=brief.get("new_geographies") or [],
            escaped_owned_recently=brief.get("escaped_owned_recently") or False,
            momentum_shift=brief.get("momentum_shift"),
            recent_window_summary=brief.get("recent_window_summary"),
            last_seen_at=kg_narr.last_seen_at,
        ))

    return cards
