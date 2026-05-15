"""Generates the 'Today's Briefing' summary shown at the top of the dashboard.

Calls the configured LLM once per session (cached for 4 hours) to produce
three actionable bullets for the campaign staffer opening the app.
"""
from datetime import datetime, timedelta
import json
import logging

from sqlalchemy.orm import Session

from app.models import CampaignConfig, OpponentActivity, Opponent, Issue
from app.knowledge_graph.orm import KGNarrative

logger = logging.getLogger(__name__)

# Simple in-memory cache: {date_str -> (generated_at, bullets)}
_cache: dict[str, tuple[datetime, list[dict]]] = {}
_CACHE_TTL = timedelta(hours=4)


def _cache_key() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def _is_cached() -> bool:
    key = _cache_key()
    if key not in _cache:
        return False
    generated_at, _ = _cache[key]
    return datetime.utcnow() - generated_at < _CACHE_TTL


def get_cached() -> dict | None:
    key = _cache_key()
    if _is_cached():
        generated_at, bullets = _cache[key]
        return {"generated_at": generated_at.isoformat(), "bullets": bullets}
    return None


def generate_daily_briefing(db: Session) -> dict:
    cached = get_cached()
    if cached:
        return cached

    bullets = _build_bullets(db)
    now = datetime.utcnow()
    _cache[_cache_key()] = (now, bullets)
    return {"generated_at": now.isoformat(), "bullets": bullets}


def _build_bullets(db: Session) -> list[dict]:
    config = db.query(CampaignConfig).first()
    candidate_name = config.candidate_name if config else "the candidate"

    # Pull context data
    cutoff = datetime.utcnow() - timedelta(hours=48)

    recent_attacks = (
        db.query(OpponentActivity)
        .filter(OpponentActivity.created_at >= cutoff)
        .filter(OpponentActivity.attack.isnot(None))
        .order_by(OpponentActivity.created_at.desc())
        .limit(5)
        .all()
    )

    from app.services.narrative_briefing import _kg_status_to_legacy, _load_claims_and_sources, _derive_card_base

    active_narratives = (
        db.query(KGNarrative)
        .filter(KGNarrative.status == "active")
        .order_by(KGNarrative.velocity_score.desc())
        .limit(10)
        .all()
    )

    top_opponent_narratives = []
    rising_narratives = []
    for n in active_narratives:
        from app.knowledge_graph.orm import KGNarrativeClaim, KGClaim
        from sqlalchemy.orm import joinedload
        status = _kg_status_to_legacy(n)
        label = n.label[:72] if len(n.label) <= 72 else n.label[:69] + "..."
        velocity = n.velocity_score or 0.0
        traction = min(100, max(0, int(velocity * 20)))
        # Approximate owner_type from description/label heuristics for daily briefing
        # (avoid loading all claims for performance; just use label/velocity)
        top_opponent_narratives.append({"label": label, "traction": traction, "status": status})
        if status == "rising":
            rising_narratives.append({"label": label})

    top_issues = (
        db.query(Issue)
        .order_by(Issue.mention_count.desc())
        .limit(5)
        .all()
    )

    # Build prompt context
    context_parts = []

    if recent_attacks:
        attacks_text = " | ".join(
            a.attack[:120] for a in recent_attacks if a.attack
        )
        context_parts.append(f"Recent opponent attacks (last 48h): {attacks_text}")

    if top_opponent_narratives:
        opp_text = " | ".join(
            f"{n['label']} (traction: {n['traction']})"
            for n in top_opponent_narratives[:3]
        )
        context_parts.append(f"Top narratives: {opp_text}")

    if rising_narratives:
        rising_text = " | ".join(n["label"] for n in rising_narratives)
        context_parts.append(f"Rising narratives: {rising_text}")

    if top_issues:
        issues_text = ", ".join(i.name for i in top_issues if i.name)
        context_parts.append(f"Top issues being discussed: {issues_text}")

    if not context_parts:
        return _fallback_bullets(candidate_name)

    context = "\n".join(context_parts)

    prompt = f"""You are a senior political campaign analyst writing the morning briefing for {candidate_name}'s campaign team.

Based on the following intelligence data, write exactly 3 briefing bullets. Each bullet should:
- Be immediately actionable for a campaign staffer
- Name the specific threat or opportunity
- Suggest one concrete next step

Intelligence data:
{context}

Return a JSON array with exactly 3 objects. Each object must have:
- "type": one of "threat", "opportunity", "watchlist"
- "headline": 8 words or fewer, punchy
- "detail": 1-2 sentences max, specific and concrete
- "action_label": 3-4 words, action verb (e.g. "Draft rebuttal now", "Amplify this message")
- "action_url": one of "/opponents", "/narratives", "/talking", "/issues", "/review"

Return only the JSON array, no other text."""

    try:
        from app.services.llm_provider import get_provider
        provider = get_provider()
        raw = provider.complete(prompt)
        # Strip markdown fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()
        bullets = json.loads(raw)
        if isinstance(bullets, list) and len(bullets) >= 1:
            # Validate and clean each bullet
            cleaned = []
            for b in bullets[:3]:
                if isinstance(b, dict) and "headline" in b:
                    cleaned.append({
                        "type": b.get("type", "watchlist"),
                        "headline": b.get("headline", ""),
                        "detail": b.get("detail", ""),
                        "action_label": b.get("action_label", "View details"),
                        "action_url": b.get("action_url", "/dashboard"),
                    })
            if cleaned:
                return cleaned
    except Exception as e:
        logger.warning("Daily briefing LLM call failed: %s", e)

    return _fallback_bullets(candidate_name)


def _fallback_bullets(candidate_name: str) -> list[dict]:
    return [
        {
            "type": "watchlist",
            "headline": "Review latest opponent activity",
            "detail": "Check for new attacks or messaging from your opponent in the last 24 hours.",
            "action_label": "Open opponent tracker",
            "action_url": "/opponents",
        },
        {
            "type": "opportunity",
            "headline": "Review rising narratives",
            "detail": "Narratives gaining traction may be worth amplifying or countering.",
            "action_label": "View narratives",
            "action_url": "/narratives",
        },
        {
            "type": "watchlist",
            "headline": "Clear the review queue",
            "detail": "New articles are waiting for your review.",
            "action_label": "Open review queue",
            "action_url": "/review",
        },
    ]


def invalidate_cache() -> None:
    """Call this after ingestion to force a fresh briefing next request."""
    _cache.clear()
