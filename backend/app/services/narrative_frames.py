"""
Narrative frame management: auto-suggest frames from article summaries,
match articles to frames.
"""
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models import NarrativeFrame, NarrativeFrameMention, SourceItem, CampaignConfig, Opponent

logger = logging.getLogger(__name__)

# In-memory progress tracker for the current rematch run.
# Safe for single-server use — reset each time rematch_all starts.
_rematch_progress: dict = {"running": False, "done": 0, "total": 0}
_rematch_lock = False  # guard against concurrent runs


def get_rematch_progress() -> dict:
    return dict(_rematch_progress)


def _normalize_frame_name(name: str) -> str:
    """Reduce a frame name to a canonical form for fuzzy dedup.

    Strips noise words so near-duplicates like "Bresnahan's Stock Trades" and
    "Bresnahan's Stock Trading Scandal" collapse to the same key.
    """
    noise = {"the", "a", "an", "and", "or", "of", "in", "on", "at", "to",
             "issue", "issues", "scandal", "problem", "crisis", "controversy",
             "attack", "attacks", "narrative", "message", "story", "stories"}
    tokens = re.sub(r"[^a-z0-9\s]", "", name.lower()).split()
    # Also collapse "trading" → "trade" style variants by stripping common suffixes
    stemmed = []
    for t in tokens:
        if t not in noise:
            for suffix in ("ing", "ion", "ions", "ed", "s"):
                if t.endswith(suffix) and len(t) - len(suffix) >= 4:
                    t = t[: -len(suffix)]
                    break
            stemmed.append(t)
    return " ".join(sorted(stemmed))


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


def _revalidate_all_frames(db: Session, ctx: dict) -> int:
    """Scan every active frame and correct any owner_type that fails the name-mention check.

    Returns the number of frames corrected.
    """
    frames = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).all()
    fixed = 0
    for frame in frames:
        frame_text = f"{frame.name} {frame.description or ''}"
        corrected = _validate_owner_type(
            owner_type=frame.owner_type,
            frame_text=frame_text,
            candidate=ctx["candidate"],
            opponents=ctx["opponents"],
        )
        if corrected != frame.owner_type:
            logger.info(
                "narrative_frames: revalidate corrected frame %d '%s' %s → %s",
                frame.id, frame.name, frame.owner_type, corrected,
            )
            frame.owner_type = corrected
            fixed += 1
    if fixed:
        db.commit()
    return fixed


def suggest_frames(db: Session, days_back: int = 60, max_summaries: int = 60) -> list[dict]:
    """
    Read recent relevant article titles/snippets and ask the LLM to identify 8-12 specific
    narratives (recurring claims and attacks) the campaign should track.

    Returns a list of dicts: [{name, description, owner_type}]
    Each is also written to the narrative_frames table with source='llm'.
    """
    cutoff = datetime.utcnow() - timedelta(days=days_back)
    items = (
        db.query(SourceItem)
        .filter(
            SourceItem.archived_as_irrelevant == False,
            SourceItem.created_at >= cutoff,
            SourceItem.race_relevance_score >= 40,
        )
        .order_by(SourceItem.race_relevance_score.desc(), SourceItem.created_at.desc())
        .limit(max_summaries)
        .all()
    )

    if not items:
        return []

    ctx = _campaign_context(db)
    # Use title + first 80 chars of summary to keep token usage low (~1500 tokens total)
    summaries_text = "\n".join(
        f"- {item.title or ''}: {(item.summary or '')[:80]}"
        for item in items
    )
    opponent_str = " and ".join(ctx["opponents"]) if ctx["opponents"] else "the opponent"

    # Pass existing frames so the LLM doesn't create near-duplicates
    existing_frames = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).all()
    existing_names_str = ""
    if existing_frames:
        existing_names_str = "\nEXISTING FRAMES (do NOT create duplicates or near-duplicates of these):\n" + \
            "\n".join(f"- {f.name}" for f in existing_frames) + "\n"

    prompt = f"""You are a political intelligence analyst helping a campaign identify the specific narratives playing out in the news.

CAMPAIGN:
- Our candidate: {ctx["candidate"]}
- Race: {ctx["race"]}
- Location: {ctx["location"]}
- Opponent: {opponent_str}
{existing_names_str}
RECENT ARTICLE SUMMARIES:
{summaries_text}

Identify 8 to 12 NEW specific NARRATIVES developing in this race that are NOT already covered by the existing frames above.

A NARRATIVE is a specific recurring claim, attack, or story — something someone is actively saying or that keeps getting repeated.
A NARRATIVE is NOT a broad topic bucket.

EXAMPLES of GOOD narratives (specific, recurring claims):
- "{opponent_str}'s Stock Trading Scandal" — repeated coverage of the opponent's stock trades while in office
- "{ctx["candidate"]}'s Anti-Corruption Message" — the candidate running on cleaning up Congress and attacking insider trading
- "{opponent_str} Not From Here" — the opponent attacking {ctx["candidate"]} as an outsider who doesn't represent NEPA
- "{ctx["candidate"]}'s Hidden Second Home" — attacks on the candidate for owning property outside the district

EXAMPLES of BAD narratives (too generic — do NOT generate these):
- "Community Events" ← this is a topic, not a narrative
- "Economic Development" ← too broad
- "Education Support" ← topic bucket, not a recurring claim
- "Cognetti's Momentum" ← vague, not a specific claim anyone is making

owner_type — who is PUSHING or BENEFITING from this narrative:
- "candidate": {ctx["candidate"]} is actively promoting this, or it clearly helps her.
  → Only use if {ctx["candidate"]}'s name appears naturally in the narrative.
  → A POSITIVE story about her OR an attack she is making against the opponent.
  → NEVER use for attacks AGAINST {ctx["candidate"]} — those help the opponent.
- "opponent": {opponent_str} is actively promoting this, or it clearly helps them.
  → Only use if an opponent name appears naturally in the narrative.
  → A POSITIVE story about the opponent OR an attack the opponent is making against {ctx["candidate"]}.
  → Attacks ON {opponent_str} that {ctx["candidate"]} is running on = "candidate", not "opponent".
- "media": Neither side clearly owns it. Neutral coverage. Use when in doubt.

KEY RULE: An attack story about {ctx["candidate"]} (e.g. "secret home", "hypocrisy") is owner_type="opponent" because it helps the opponent — NOT "candidate".
An attack story about {opponent_str} (e.g. "stock trades", "lies") is owner_type="candidate" because {ctx["candidate"]} is pushing it — NOT "opponent".

Return ONLY a JSON array, no other text:
[
  {{
    "name": "Specific narrative name (5 words max)",
    "description": "One sentence: the specific claim or attack being made, and who is making it.",
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
        # Strip markdown fences
        if text.startswith("```"):
            lines = text.splitlines()
            inner = lines[1:] if lines[-1].strip() == "```" else lines[1:]
            text = "\n".join(inner).strip()

        # Extract the JSON array even if the model added surrounding text.
        # Find the first '[' and the matching ']' to be robust against
        # thinking models (Gemini 2.5) that add prose before or after the array.
        bracket_start = text.find("[")
        bracket_end = text.rfind("]")
        if bracket_start != -1 and bracket_end != -1:
            text = text[bracket_start:bracket_end + 1]

        try:
            frames_data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning("narrative_frames.suggest_frames: JSON parse error: %s", exc)
            return []
        if not isinstance(frames_data, list):
            return []

        # Build a map of normalized name → existing frame for fuzzy dedup.
        # This catches near-duplicates like "Bresnahan's Stock Trades" vs
        # "Bresnahan's Stock Trading Scandal" that share the same key after
        # noise/suffix stripping.
        all_existing = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).all()
        existing_by_norm: dict[str, NarrativeFrame] = {
            _normalize_frame_name(f.name): f for f in all_existing
        }
        # Also track normalized keys of frames suggested in this batch so we
        # don't create two new frames that are near-duplicates of each other.
        seen_this_batch: dict[str, str] = {}  # norm_key → canonical name

        created = []
        for f in frames_data:
            name = (f.get("name") or "").strip()
            description = (f.get("description") or "").strip()
            owner_type = f.get("owner_type", "media")
            if owner_type not in ("candidate", "opponent", "media"):
                owner_type = "media"
            if not name:
                continue

            norm_key = _normalize_frame_name(name)

            # Skip if this batch already produced a near-duplicate
            if norm_key in seen_this_batch:
                logger.info(
                    "narrative_frames: dropping near-duplicate '%s' (matches '%s' in this batch)",
                    name, seen_this_batch[norm_key],
                )
                continue
            seen_this_batch[norm_key] = name

            owner_type = _validate_owner_type(
                owner_type=owner_type,
                frame_text=f"{name} {description}",
                candidate=ctx["candidate"],
                opponents=ctx["opponents"],
            )

            # Check DB for near-duplicate (exact name OR same normalized key)
            existing = (
                db.query(NarrativeFrame).filter(NarrativeFrame.name == name).first()
                or existing_by_norm.get(norm_key)
            )
            if existing:
                if existing.owner_type != owner_type:
                    logger.info(
                        "narrative_frames: correcting frame %d owner_type %s → %s",
                        existing.id, existing.owner_type, owner_type,
                    )
                    existing.owner_type = owner_type
                created.append({"id": existing.id, "name": existing.name, "description": existing.description, "owner_type": owner_type})
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

        # Revalidate ALL existing frames — catches any that were mislabeled in
        # earlier runs and never re-surfaced by the LLM suggestion prompt.
        _revalidate_all_frames(db, ctx)

        # Match all articles in the archive so counts are fully populated
        if created:
            matched = rematch_all(db, days_back=365)
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
    Ask Groq which active narrative frames this article belongs to, and extract
    the specific claim/quote from the article for each matched frame.
    Writes NarrativeFrameMention rows. Returns list of matched frame IDs.
    """
    frames = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).all()
    if not frames:
        return []

    if not item.summary and not item.title:
        return []

    # Skip articles that are not relevant to this specific race — this prevents
    # debates or news from unrelated races from polluting frame mentions.
    relevance = getattr(item, "race_relevance_score", None) or 0
    if relevance < 35:
        logger.debug("narrative_frames.match_article: skipping item=%d (relevance=%d)", item.id, relevance)
        return []

    frames_list = "\n".join(
        f"{i+1}. {f.name}: {f.description or ''}"
        for i, f in enumerate(frames)
    )
    article_text = item.summary or item.title or ""

    prompt = f"""You are a political research assistant tagging news articles with the campaign narratives they cover.

NARRATIVES:
{frames_list}

ARTICLE:
Title: {item.title or "No title"}
Summary: {article_text}

TASK:
For each narrative above, decide: does this article discuss or mention this topic, regardless of how it is framed or which side it favors?

Rules:
- Match based on TOPIC, not on tone or who benefits. An investigative piece about Bresnahan's stock trades counts the same as a Cognetti press release about it.
- One article can match MULTIPLE narratives if it contains distinct information about each.
- Do NOT match vague thematic overlap — only match when the article has specific information about that narrative topic.
- For each match, extract the 1-2 most relevant sentences verbatim from the article. Do not paraphrase.

Return ONLY a JSON array. Each element: {{"frame": <number>, "snippet": "<exact quote from article>"}}
Return [] if no narratives apply.

Example: [{{"frame": 2, "snippet": "Bresnahan bought and sold stocks in healthcare companies while sitting on committees overseeing those industries."}}, {{"frame": 4, "snippet": "Cognetti pointed to her record cutting city contracts as evidence of her anti-corruption stance."}}]"""

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

        bracket_start = text.find("[")
        bracket_end = text.rfind("]")
        if bracket_start != -1 and bracket_end != -1:
            text = text[bracket_start:bracket_end + 1]

        try:
            matched_items = json.loads(text)
        except json.JSONDecodeError:
            return []
        if not isinstance(matched_items, list):
            return []

        matched_frame_ids = []
        for entry in matched_items:
            # Support both old format (plain int) and new format (dict with frame + snippet)
            if isinstance(entry, int):
                idx = entry
                snippet = None
            elif isinstance(entry, dict):
                idx = entry.get("frame")
                snippet = (entry.get("snippet") or "").strip() or None
            else:
                continue

            if not isinstance(idx, int) or idx < 1 or idx > len(frames):
                continue
            frame = frames[idx - 1]

            existing = (
                db.query(NarrativeFrameMention)
                .filter_by(frame_id=frame.id, source_item_id=item.id)
                .first()
            )
            if existing:
                # Update snippet if we now have one and didn't before
                if snippet and not existing.extracted_text:
                    existing.extracted_text = snippet
            else:
                db.add(NarrativeFrameMention(
                    frame_id=frame.id,
                    source_item_id=item.id,
                    confidence=75,
                    matched_by="llm",
                    extracted_text=snippet,
                ))
            matched_frame_ids.append(frame.id)

        db.commit()
        return matched_frame_ids

    except Exception as e:
        logger.warning("narrative_frames.match_article: item=%d failed: %s", item.id, e)
        return []


def rematch_all(db: Session, days_back: int = 365) -> int:
    """Rematch all relevant articles to current active frames.

    Inserts a small delay between each article to stay within Groq's per-minute
    token limits. With 4 keys round-robining and ~800 tokens per call, 2.5 s
    between calls keeps each key well under its 6,000 TPM ceiling.

    After matching, auto-prunes any frame that still has zero total mentions —
    it means the LLM couldn't find a single example in the entire archive, so
    the frame isn't grounded in real coverage.

    Returns total mention count created.
    """
    global _rematch_lock
    if _rematch_lock:
        logger.warning("rematch_all: already running — ignoring concurrent request")
        return 0
    _rematch_lock = True

    import time
    from sqlalchemy import func

    # Configurable via env — increase if still hitting limits, decrease if fast enough
    delay = float(os.environ.get("REMATCH_DELAY_SECONDS", "2.5"))

    try:
        cutoff = datetime.utcnow() - timedelta(days=days_back)

        # Only process articles that have never been matched — articles already
        # carrying at least one mention were processed in a previous run and
        # don't need to be re-sent to the LLM (saves tokens).
        already_matched_ids = {
            row[0]
            for row in db.query(NarrativeFrameMention.source_item_id).distinct().all()
        }
        all_items = (
            db.query(SourceItem)
            .filter(
                SourceItem.archived_as_irrelevant == False,
                SourceItem.created_at >= cutoff,
            )
            .all()
        )
        items = [i for i in all_items if i.id not in already_matched_ids]
        logger.info(
            "rematch_all: %d total articles, %d already matched, processing %d new",
            len(all_items), len(already_matched_ids), len(items),
        )

        _rematch_progress["running"] = True
        _rematch_progress["done"] = 0
        _rematch_progress["total"] = len(items)

        total = 0
        for i, item in enumerate(items):
            matched = match_article_to_frames(db, item)
            total += len(matched)
            _rematch_progress["done"] = i + 1
            if i < len(items) - 1:
                time.sleep(delay)

        _rematch_progress["running"] = False

        # Prune frames with zero mentions across all time (not just the window)
        frames = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).all()
        pruned = 0
        for frame in frames:
            mention_count = (
                db.query(func.count(NarrativeFrameMention.id))
                .filter(NarrativeFrameMention.frame_id == frame.id)
                .scalar()
            )
            if mention_count == 0:
                logger.info(
                    "narrative_frames: pruning empty frame %d '%s' (0 mentions in archive)",
                    frame.id, frame.name,
                )
                db.delete(frame)
                pruned += 1
        if pruned:
            db.commit()
            logger.info("narrative_frames: pruned %d empty frames", pruned)

        return total
    finally:
        _rematch_lock = False
        _rematch_progress["running"] = False


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
            .join(SourceItem, SourceItem.id == NarrativeFrameMention.source_item_id)
            .filter(
                NarrativeFrameMention.frame_id == frame.id,
                SourceItem.published_at >= week_start,
                SourceItem.published_at.isnot(None),
            )
            .scalar()
        )
        last_week = (
            db.query(func.count(NarrativeFrameMention.id))
            .join(SourceItem, SourceItem.id == NarrativeFrameMention.source_item_id)
            .filter(
                NarrativeFrameMention.frame_id == frame.id,
                SourceItem.published_at >= prev_week_start,
                SourceItem.published_at < week_start,
                SourceItem.published_at.isnot(None),
            )
            .scalar()
        )
        total = (
            db.query(func.count(NarrativeFrameMention.id))
            .filter(NarrativeFrameMention.frame_id == frame.id)
            .scalar()
        )

        # Fetch the mention alongside the article so we can include extracted_text
        recent_pairs = (
            db.query(NarrativeFrameMention, SourceItem)
            .join(SourceItem, SourceItem.id == NarrativeFrameMention.source_item_id)
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
                    "extracted_text": mention.extracted_text,
                }
                for mention, a in recent_pairs
            ],
        })

    result.sort(key=lambda x: x["mentions_this_week"], reverse=True)
    return result
