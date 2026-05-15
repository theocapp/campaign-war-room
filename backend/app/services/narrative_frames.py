"""
Narrative frame management: auto-suggest frames from article summaries,
match articles to frames.
"""
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models import NarrativeFrame, NarrativeFrameMention, SourceItem, CampaignConfig, Opponent

logger = logging.getLogger(__name__)


def _name_tokens(full_name: str) -> list[str]:
    """Meaningful (>2 char) lowercase tokens from a person's name.

    "BRESNAHAN, ROB" → ["bresnahan", "rob"]. Used so the validator matches
    either order or either casing in frame text.
    """
    if not full_name:
        return []
    raw = re.sub(r"[^a-zA-Z\s]", " ", full_name).lower()
    return [tok for tok in raw.split() if len(tok) > 2]


def _mentions_person(text: str, full_name: str) -> bool:
    if not text or not full_name:
        return False
    lower = text.lower()
    return any(tok in lower for tok in _name_tokens(full_name))


def _validate_owner_type(
    owner_type: str,
    frame_text: str,
    candidate: str,
    opponents: list[str],
) -> str:
    """Downgrade owner_type to 'media' when the frame text doesn't mention
    the relevant party. Bug 6: the LLM kept labelling generic news as
    'opponent' even when no opponent was named.
    """
    if owner_type == "candidate" and not _mentions_person(frame_text, candidate):
        return "media"
    if owner_type == "opponent" and not any(_mentions_person(frame_text, o) for o in opponents):
        return "media"
    return owner_type


def _campaign_context(db: Session) -> dict:
    config = db.query(CampaignConfig).first()
    opponents = db.query(Opponent).all()
    return {
        "candidate": config.candidate_name if config else "Unknown",
        "race": (config.race or config.office or "Unknown") if config else "Unknown",
        "location": (config.location or config.district or "Unknown") if config else "Unknown",
        "opponents": [o.name for o in opponents] if opponents else [],
    }


def suggest_frames(db: Session, days_back: int = 14, max_summaries: int = 25) -> list[dict]:
    """
    Read recent relevant article summaries and ask Groq to identify 3-5 narrative
    frames the campaign should track.

    Returns a list of dicts: [{name, description, owner_type}]
    Each is also written to the narrative_frames table with source='llm'.
    """
    cutoff = datetime.utcnow() - timedelta(days=days_back)
    items = (
        db.query(SourceItem)
        .filter(
            SourceItem.archived_as_irrelevant == False,
            SourceItem.created_at >= cutoff,
            SourceItem.summary.isnot(None),
        )
        .order_by(SourceItem.created_at.desc())
        .limit(max_summaries)
        .all()
    )

    if not items:
        return []

    ctx = _campaign_context(db)
    summaries_text = "\n".join(
        f"- {(item.summary or '')[:200]}" for item in items if item.summary
    )
    opponent_str = " and ".join(ctx["opponents"]) if ctx["opponents"] else "the opponent"

    prompt = f"""You are helping a political campaign identify the key narrative frames developing in the news.

CAMPAIGN:
- Candidate: {ctx["candidate"]}
- Race: {ctx["race"]}
- Location: {ctx["location"]}
- Opponent: {opponent_str}

RECENT RELEVANT ARTICLE SUMMARIES:
{summaries_text}

Based on these summaries, identify 3 to 5 distinct narrative frames this campaign should track.

CRITICAL: A frame is a recurring CATEGORY of story, not one specific event.
- Good description: "Public-safety coverage tying the opponent to rising crime statistics."
- Bad description: "A March 12 press conference where Smith blamed Jones for a shooting."
Describe the category. Do not name specific dates or articles in the description.

owner_type rules — be strict:
- "candidate": ONLY if the frame is one {ctx["candidate"]} is actively pushing, or that clearly helps them. The name "{ctx["candidate"]}" should appear in the frame.
- "opponent": ONLY if the frame is one the opponent is pushing, or that clearly helps them. An opponent's name from this list ({opponent_str}) should appear in the frame.
- "media": Neutral or contested coverage where no campaign clearly owns it. Use this when in doubt — generic local-news coverage of an issue is "media", not "opponent".

Each frame should be:
- Specific to this race (not generic national politics)
- Something that appears in multiple articles or is strategically important

Return ONLY a JSON array, no other text:
[
  {{
    "name": "Short frame name (5 words max)",
    "description": "One sentence: what category this frame covers and why it matters.",
    "owner_type": "candidate" or "opponent" or "media"
  }}
]"""

    try:
        from app.services.llm_provider import get_provider, MockLLMProvider
        provider = get_provider()
        if isinstance(provider, MockLLMProvider):
            return []

        raw = provider.complete(prompt)
        if not raw or not raw.strip():
            return []

        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            inner = lines[1:] if lines[-1].strip() == "```" else lines[1:]
            text = "\n".join(inner).strip()

        frames_data = json.loads(text)
        if not isinstance(frames_data, list):
            return []

        created = []
        for f in frames_data:
            name = (f.get("name") or "").strip()
            description = (f.get("description") or "").strip()
            owner_type = f.get("owner_type", "media")
            if owner_type not in ("candidate", "opponent", "media"):
                owner_type = "media"
            if not name:
                continue

            # Validate owner_type — the LLM tends to over-assign "opponent" to
            # generic news. Downgrade to "media" unless the frame text actually
            # mentions the relevant party. Names are compared case-insensitively
            # against the frame's name + description.
            owner_type = _validate_owner_type(
                owner_type=owner_type,
                frame_text=f"{name} {description}",
                candidate=ctx["candidate"],
                opponents=ctx["opponents"],
            )

            # Don't duplicate if a frame with this name already exists
            existing = db.query(NarrativeFrame).filter(NarrativeFrame.name == name).first()
            if existing:
                created.append({"id": existing.id, "name": name, "description": description, "owner_type": owner_type})
                continue

            frame = NarrativeFrame(
                name=name,
                description=description,
                owner_type=owner_type,
                source="llm",
                active=True,
            )
            db.add(frame)
            db.flush()
            created.append({"id": frame.id, "name": name, "description": description, "owner_type": owner_type})

        db.commit()
        logger.info("narrative_frames: suggested %d frames", len(created))

        # Immediately match recent articles so counts are populated right away
        if created:
            matched = rematch_all(db, days_back=30)
            logger.info("narrative_frames: auto-matched %d mentions after suggestion", matched)

        return created

    except json.JSONDecodeError as e:
        logger.warning("narrative_frames.suggest_frames: JSON parse error: %s", e)
        return []
    except Exception as e:
        logger.warning("narrative_frames.suggest_frames: failed: %s", e)
        return []


def match_article_to_frames(db: Session, item: SourceItem) -> list[int]:
    """
    Ask Groq which active narrative frames this article belongs to.
    Writes NarrativeFrameMention rows. Returns list of matched frame IDs.
    """
    frames = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).all()
    if not frames:
        return []

    if not item.summary and not item.title:
        return []

    frames_list = "\n".join(
        f"{i+1}. [{f.owner_type}] {f.name}: {f.description or ''}"
        for i, f in enumerate(frames)
    )
    article_text = item.summary or item.title or ""

    prompt = f"""You are matching a news article to narrative frames for a political campaign.

FRAMES:
{frames_list}

ARTICLE:
Title: {item.title or "No title"}
Summary: {article_text}

Which frames (by number) does this article clearly relate to? Return ONLY a JSON array of frame numbers (e.g. [1, 3]). Return [] if none apply."""

    try:
        from app.services.llm_provider import get_provider, MockLLMProvider
        provider = get_provider()
        if isinstance(provider, MockLLMProvider):
            return []

        raw = provider.complete(prompt)
        if not raw or not raw.strip():
            return []

        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            inner = lines[1:] if lines[-1].strip() == "```" else lines[1:]
            text = "\n".join(inner).strip()

        matched_indices = json.loads(text)
        if not isinstance(matched_indices, list):
            return []

        matched_frame_ids = []
        for idx in matched_indices:
            if not isinstance(idx, int) or idx < 1 or idx > len(frames):
                continue
            frame = frames[idx - 1]
            # Upsert: skip if mention already exists
            existing = (
                db.query(NarrativeFrameMention)
                .filter_by(frame_id=frame.id, source_item_id=item.id)
                .first()
            )
            if not existing:
                db.add(NarrativeFrameMention(
                    frame_id=frame.id,
                    source_item_id=item.id,
                    confidence=75,
                    matched_by="llm",
                ))
            matched_frame_ids.append(frame.id)

        db.commit()
        return matched_frame_ids

    except Exception as e:
        logger.warning("narrative_frames.match_article: item=%d failed: %s", item.id, e)
        return []


def rematch_all(db: Session, days_back: int = 30) -> int:
    """Rematch all recent relevant articles to current active frames. Returns count matched."""
    cutoff = datetime.utcnow() - timedelta(days=days_back)
    items = (
        db.query(SourceItem)
        .filter(
            SourceItem.archived_as_irrelevant == False,
            SourceItem.created_at >= cutoff,
        )
        .all()
    )
    total = 0
    for item in items:
        matched = match_article_to_frames(db, item)
        total += len(matched)
    return total


def get_frames_with_counts(db: Session) -> list[dict]:
    """Return all active frames with mention counts for this week and last week."""
    from sqlalchemy import func
    now = datetime.utcnow()
    week_start = now - timedelta(days=7)
    prev_week_start = now - timedelta(days=14)

    frames = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).all()
    result = []
    for frame in frames:
        this_week = (
            db.query(func.count(NarrativeFrameMention.id))
            .filter(
                NarrativeFrameMention.frame_id == frame.id,
                NarrativeFrameMention.created_at >= week_start,
            )
            .scalar()
        )
        last_week = (
            db.query(func.count(NarrativeFrameMention.id))
            .filter(
                NarrativeFrameMention.frame_id == frame.id,
                NarrativeFrameMention.created_at >= prev_week_start,
                NarrativeFrameMention.created_at < week_start,
            )
            .scalar()
        )
        total = (
            db.query(func.count(NarrativeFrameMention.id))
            .filter(NarrativeFrameMention.frame_id == frame.id)
            .scalar()
        )

        recent_articles = (
            db.query(SourceItem)
            .join(NarrativeFrameMention, NarrativeFrameMention.source_item_id == SourceItem.id)
            .filter(NarrativeFrameMention.frame_id == frame.id)
            .order_by(SourceItem.published_at.desc())
            .limit(3)
            .all()
        )

        trend = "up" if this_week > last_week else ("down" if this_week < last_week else "flat")

        result.append({
            "id": frame.id,
            "name": frame.name,
            "description": frame.description,
            "owner_type": frame.owner_type,
            "source": frame.source,
            "created_at": frame.created_at.isoformat() if frame.created_at else None,
            "mentions_this_week": this_week,
            "mentions_last_week": last_week,
            "mentions_total": total,
            "trend": trend,
            "recent_articles": [
                {
                    "id": a.id,
                    "title": a.title,
                    "summary": a.summary,
                    "source_name": a.source_name,
                    "source_url": a.source_url,
                    "published_at": a.published_at.isoformat() if a.published_at else None,
                }
                for a in recent_articles
            ],
        })

    result.sort(key=lambda x: x["mentions_this_week"], reverse=True)
    return result
