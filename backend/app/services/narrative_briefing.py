from datetime import datetime, timedelta
from typing import List

from sqlalchemy.orm import Session

from app.schemas import DashboardNarrativeCard
from app.services.snapshots import source_out
from app.services.story_clustering import unique_by_cluster


def _derive_brief_fields(narrative, db: Session):
    # collect mention sources
    mentions_sources = [m.source_item for m in narrative.mentions if m.source_item]
    unique_sources = unique_by_cluster(mentions_sources)
    now = datetime.utcnow()
    recent_cutoff = now - timedelta(days=7)
    recent_mentions = [s for s in mentions_sources if (s.published_at or s.created_at) and (s.published_at or s.created_at) >= recent_cutoff]
    recent_count = len(recent_mentions)
    prior_cutoff = now - timedelta(days=14)
    prior_window = [s for s in mentions_sources if (s.published_at or s.created_at) and prior_cutoff <= (s.published_at or s.created_at) < recent_cutoff]
    prior_count = len(prior_window)
    escaped_owned = any(s.source_type not in {"campaign_note", "opponent_statement"} for s in mentions_sources)

    # timeline/change-detection: new clusters, messenger types, geographies
    recent_clusters = {s.story_cluster_id or f"source-{s.id}" for s in recent_mentions}
    prior_clusters = {s.story_cluster_id or f"source-{s.id}" for s in prior_window}
    new_clusters = recent_clusters - prior_clusters
    recent_messengers = {s.source_type for s in recent_mentions if s.source_type}
    prior_messengers = {s.source_type for s in prior_window if s.source_type}
    new_messengers = recent_messengers - prior_messengers
    recent_geos = {s.geo_relevance for s in recent_mentions if s.geo_relevance and s.geo_relevance != 'none'}
    prior_geos = {s.geo_relevance for s in prior_window if s.geo_relevance and s.geo_relevance != 'none'}
    new_geos = list(recent_geos - prior_geos)
    escaped_owned_recently = any(s.source_type not in {"campaign_note", "opponent_statement"} for s in recent_mentions)

    # momentum heuristic
    momentum_shift = "unchanged"
    if prior_count == 0 and recent_count >= 2:
        momentum_shift = "stronger"
    elif prior_count > 0:
        if recent_count >= prior_count * 1.5 and (recent_count - prior_count) >= 2:
            momentum_shift = "stronger"
        elif recent_count <= prior_count * 0.7 and (prior_count - recent_count) >= 2:
            momentum_shift = "weaker"
        else:
            momentum_shift = "unchanged"


    # spread summary
    if escaped_owned and narrative.source_cluster_count > 1:
        spread_summary = "Has begun to appear outside owned channels and across multiple clusters."
    elif narrative.source_cluster_count >= 3:
        spread_summary = "Multiple distinct source clusters and messenger types detected."
    elif narrative.source_cluster_count == 1:
        spread_summary = "Currently confined to a single source cluster."
    else:
        spread_summary = None

    # what changed: prefer concrete computed signals over generic status language
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

    if new_geos:
        label = "New geography" if len(new_geos) == 1 else "New geographies"
        change_lines.append(f"{label}: {', '.join(sorted(new_geos)[:3])}.")

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
    elif narrative.status == "rising":
        what_changed = "This narrative is rising, but no new cluster or messenger signal is isolated yet."
    elif narrative.status == "fading":
        what_changed = "Recent activity is weaker than the prior baseline."
    else:
        what_changed = "No major recent change detected."

    # risk/opportunity
    if narrative.owner_type == "opponent" and narrative.status in {"rising", "emerging"}:
        risk_or_opportunity = "risk"
    elif narrative.owner_type == "candidate" and narrative.status == "rising":
        risk_or_opportunity = "opportunity"
    else:
        risk_or_opportunity = "monitor"

    # action
    if narrative.response_status == "response_ready" and narrative.owner_type == "opponent":
        action = "respond"
    elif narrative.owner_type == "candidate" and escaped_owned and narrative.evidence_strength in {"moderate", "strong"}:
        action = "amplify"
    elif narrative.evidence_strength == "weak" and narrative.status == "emerging":
        action = "monitor"
    else:
        action = "ignore" if narrative.evidence_strength == "weak" else "monitor"

    evidence_summary = f"{narrative.source_cluster_count} clusters · {narrative.messenger_diversity_count} messengers · {narrative.geography_count} geographies"
    top_supporting = [source_out(s) for s in unique_sources[:3]]
    verify_links = [s.source_url for s in unique_sources[:3] if getattr(s, 'source_url', None)]

    recent_window_summary = None
    if recent_count or prior_count:
        recent_window_summary = (
            f"{recent_count} mentions in last 7 days vs {prior_count} in prior week."
        )

    return {
        "what_changed": what_changed,
        "spread_summary": spread_summary,
        "risk_or_opportunity": risk_or_opportunity,
        "action": action,
        "confidence": narrative.owner_confidence,
        "evidence_summary": evidence_summary,
        "top_supporting_sources": top_supporting,
        "verify_links": verify_links,
        "change_summary": None,
        "new_messenger_types": list(new_messengers) if recent_count else [],
        "new_source_clusters_count": len(new_clusters) if recent_count else 0,
        "new_geographies": new_geos,
        "escaped_owned_recently": escaped_owned_recently,
        "momentum_shift": momentum_shift,
        "recent_window_summary": recent_window_summary,
    }


def build_brief_cards(db: Session, narratives: List, limit: int = 5) -> List[DashboardNarrativeCard]:
    cards: List[DashboardNarrativeCard] = []
    for narrative in narratives[:limit]:
        source = None
        for mention in narrative.mentions:
            if mention.source_item:
                source = mention.source_item
                break
        derived = _derive_brief_fields(narrative, db)

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

        card = DashboardNarrativeCard(
            narrative_id=narrative.id,
            short_label=narrative.short_label,
            canonical_text=narrative.canonical_text,
            narrative_type=narrative.narrative_type,
            owner_type=narrative.owner_type,
            direction=narrative.direction,
            status=narrative.status,
            traction_score=narrative.traction_score,
            evidence_strength=narrative.evidence_strength,
            response_status=narrative.response_status,
            owner_confidence=narrative.owner_confidence,
            attribution_type=narrative.attribution_type,
            target_confidence=narrative.target_confidence,
            source_count=narrative.source_count,
            source_cluster_count=narrative.source_cluster_count,
            messenger_diversity_count=narrative.messenger_diversity_count,
            geography_count=narrative.geography_count,
            why_it_matters=why,
            source_item_id=source.id if source else None,
            source_title=source.title if source else None,
            source_url=source.source_url if source else None,
            what_changed=derived.get("what_changed"),
            why_it_matters_now=why,
            spread_summary=derived.get("spread_summary"),
            risk_or_opportunity=derived.get("risk_or_opportunity"),
            action=derived.get("action"),
            confidence=derived.get("confidence"),
            evidence_summary=derived.get("evidence_summary"),
            top_supporting_sources=derived.get("top_supporting_sources") or [],
            verify_links=derived.get("verify_links") or [],
            change_summary=derived.get("change_summary"),
            new_messenger_types=derived.get("new_messenger_types") or [],
            new_source_clusters_count=derived.get("new_source_clusters_count") or 0,
            new_geographies=derived.get("new_geographies") or [],
            escaped_owned_recently=derived.get("escaped_owned_recently") or False,
            momentum_shift=derived.get("momentum_shift"),
            recent_window_summary=derived.get("recent_window_summary"),
        )
        cards.append(card)
    return cards
