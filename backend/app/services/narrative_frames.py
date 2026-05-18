"""
Narrative frame management: auto-suggest frames from article summaries,
match articles to frames.
"""
import html
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Optional


def _repair_json(text: str) -> str:
    """Best-effort cleanup of common LLM JSON formatting errors.

    Handles the two most common 8B model failures:
    1. Trailing commas before } or ]
    2. Missing commas between a closing quote and the next key ('"value" "key"')
    """
    # Fix trailing commas: ,  } or ,  ]
    text = re.sub(r",\s*([}\]])", r"\1", text)
    # Fix missing commas between end of value and start of next key:
    # "something" "next_key" → "something", "next_key"
    text = re.sub(r'("\s*)(")(?=[^:,\]\}])', r'\1,\2', text)
    return text

def _clean_description(description: str) -> str:
    """Strip prompt-scaffolding that leaks into LLM-generated descriptions.

    The generation prompt includes owner_type as a field name, and smaller
    models sometimes echo it into the description string, e.g.:
      "She attacks insider trading, owner_type: candidate"
    This strips those suffixes before the frame is saved.
    """
    if not description:
        return description
    # Strip trailing ", owner_type: value" or "owner_type: value" patterns
    cleaned = re.sub(
        r',?\s*"?owner_type"?\s*:\s*"?(candidate|opponent|media)"?\s*$',
        "",
        description,
        flags=re.IGNORECASE,
    )
    return cleaned.strip().rstrip(",").strip()


_MIN_BODY_FOR_QUOTE = 200  # articles shorter than this are thin/paywalled — skip quote extraction


def _frame_topic_keywords(frame_name: str, frame_desc: str, ctx: dict) -> list[str]:
    """Extract substantive topic words from a frame's name and description.

    Strips candidate/opponent names (they appear in almost every article)
    and common stop words, leaving the terms that are specific to this topic.
    Used by the keyword gate to reject LLM matches on off-topic articles.
    """
    stop = {
        "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for",
        "is", "are", "was", "were", "be", "been", "being", "that", "this",
        "with", "by", "from", "as", "it", "its", "their", "they", "our",
        "message", "narrative", "frame", "push", "pushes", "pushing", "who",
        "attack", "attacks", "claim", "claims", "says", "said", "this", "has",
        "have", "while", "being", "about", "over", "into", "amid", "amid",
    }
    # Exclude candidate and opponent name tokens — every article mentions them
    name_tokens: set[str] = set()
    for person in [ctx.get("candidate", "")] + ctx.get("opponents", []):
        name_tokens.update(_name_tokens(person))

    # Use only the frame NAME (not description) — descriptions add broad terms
    # like "campaign", "push", "making" that match almost every election article.
    text = frame_name
    raw_tokens = re.sub(r"[^a-z0-9\s]", " ", text.lower()).split()

    keywords: set[str] = set()
    for tok in raw_tokens:
        if len(tok) < 4 or tok in stop or tok in name_tokens:
            continue
        # Light stemming so "trades"→"trade", "corruption"→"corrupt"
        stem = tok
        for suffix in ("tion", "ions", "ing", "ion", "ed", "s"):
            if tok.endswith(suffix) and len(tok) - len(suffix) >= 4:
                stem = tok[: -len(suffix)]
                break
        keywords.add(stem)
    return list(keywords)


def _article_passes_keyword_gate(item: "SourceItem", frame_name: str, frame_desc: str, ctx: dict) -> bool:
    """Return True if the article contains at least one topic keyword from the frame.

    Rejects LLM matches where a general roundup article was tagged to a specific
    frame just because it broadly covers the race. Requires at least one keyword
    from the frame's name/description to appear in the article text.
    """
    keywords = _frame_topic_keywords(frame_name, frame_desc, ctx)
    if not keywords:
        return True  # No keywords derivable — don't block the match

    search_text = " ".join([
        html.unescape(item.raw_text or "").lower(),
        html.unescape(item.title or "").lower(),
        html.unescape(item.summary or "").lower(),
    ])
    return any(kw in search_text for kw in keywords)


def _validate_snippet(snippet: str, item: "SourceItem") -> Optional[str]:
    """Return snippet only if it is verifiably in the article text.

    When the LLM has only a short summary to work from it fabricates quotes.
    We validate by checking for the snippet (HTML-decoded) as a substring of
    the article body + title + summary.  If it's not found, we keep the frame
    match but discard the extracted quote.
    """
    if not snippet or not snippet.strip():
        return None

    body = item.raw_text or ""
    if len(body) < _MIN_BODY_FOR_QUOTE:
        # Thin / paywalled article — LLM has nothing real to quote from
        return None

    norm_snippet = html.unescape(snippet).lower().strip()
    search_space = " ".join([
        html.unescape(body).lower(),
        html.unescape(item.title or "").lower(),
        html.unescape(item.summary or "").lower(),
    ])

    if norm_snippet in search_space:
        return snippet

    # Allow minor whitespace/punctuation differences: require 90%+ word overlap
    words = norm_snippet.split()
    if len(words) >= 5:
        matched = sum(1 for w in words if w in search_space)
        if matched / len(words) >= 0.90:
            return snippet

    return None


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


def _parse_json_array(raw: str, label: str) -> list:
    """Parse a JSON array from LLM output, handling fences and stray prose."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:] if lines[-1].strip() == "```" else lines[1:]).strip()
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    text = _repair_json(text)
    try:
        result = json.loads(text)
        return result if isinstance(result, list) else []
    except json.JSONDecodeError as exc:
        logger.warning("narrative_frames.%s: JSON parse error: %s", label, exc)
        return []


def _dedup_candidates(
    candidates: list[dict],
    existing_frames: list,
    provider,
    ctx: dict,
) -> list[dict]:
    """Pass 2: ask the LLM to remove candidates that duplicate existing frames.

    The LLM's only job here is comparison — no generation, no creativity.
    Returns the subset of candidates that are genuinely new narratives.
    """
    if not candidates or not existing_frames:
        return candidates

    existing_lines = "\n".join(
        f"- {f.name}: {f.description or '(no description)'}"
        for f in existing_frames
    )
    candidate_lines = "\n".join(
        f"{i+1}. {c['name']}: {c['description']}"
        for i, c in enumerate(candidates)
    )

    prompt = f"""You are checking a list of candidate narrative frames for duplicates against frames that are already tracked.

ALREADY TRACKED:
{existing_lines}

CANDIDATES TO CHECK:
{candidate_lines}

A candidate is a DUPLICATE of an existing frame if they would tag the same news articles — even if the wording is different or one is more specific than the other.

Examples of SAME narrative (duplicates — reject the candidate):
- Candidate "Cognetti Anti-Corruption Attack" vs existing "Cognetti's Anti-Corruption" → same actor, same claim → duplicate
- Candidate "Cognetti Outraises Bresnahan" vs existing "Cognetti's Congressional Campaign Momentum" (which mentions fundraising) → outraising IS momentum → duplicate
- Candidate "Cognetti Running Two Races" vs existing "Cognetti's Dual Campaigns" → same story → duplicate

Examples of DIFFERENT narratives (keep the candidate):
- Candidate "Bresnahan's Veterans Policy Criticized" vs existing "Bresnahan's Veteran Support" → different angle (critics vs his campaign) → keep
- Candidate "Bresnahan Secures Federal Earmarks" vs existing "Bresnahan's Local Engagement" → different specific claim (money won vs general presence) → keep

Return ONLY the numbers of candidates that are genuinely new (not duplicates).
Return as a JSON array of integers, e.g. [1, 3, 5] or [] if all are duplicates."""

    raw = provider.complete(prompt)
    if not raw or not raw.strip():
        logger.warning("narrative_frames.dedup: provider returned empty — skipping frame creation to avoid duplicates")
        return []

    # MockLLMProvider.complete() returns "[]" — detect it and bail rather than
    # treating mock output as a real dedup decision.
    stripped = raw.strip()
    if stripped == "[]":
        # Could be a genuine "all duplicates" answer from a real model, OR the
        # mock fallback. We can't tell — treat conservatively: return nothing.
        # If real providers have quota and genuinely found all duplicates, that's
        # the right answer. If it's mock, we avoid saving unverified candidates.
        logger.info("narrative_frames.dedup: all candidates identified as duplicates (or providers unavailable)")
        return []

    kept_indices = _parse_json_array(raw, "dedup_candidates")
    if not isinstance(kept_indices, list):
        logger.warning("narrative_frames.dedup: unexpected response format — skipping frame creation")
        return []

    valid_indices = {i for i in kept_indices if isinstance(i, int) and 1 <= i <= len(candidates)}
    kept = [c for i, c in enumerate(candidates, 1) if i in valid_indices]
    logger.info(
        "narrative_frames.dedup: %d candidates → %d after dedup (%d removed)",
        len(candidates), len(kept), len(candidates) - len(kept),
    )
    return kept


def suggest_frames(db: Session, days_back: int = 60, max_summaries: int = 60) -> list[dict]:
    """
    Two-pass approach to identify new narrative frames from recent articles:

    Pass 1 — Generation: ask the LLM what recurring narratives appear in the
    articles, with no mention of existing frames. The model focuses purely on
    what's in the news.

    Pass 2 — Dedup: give the LLM the candidates + existing frames and ask it
    to remove anything already covered. The model focuses purely on comparison.

    Separating these tasks makes each one reliable. Asking one model call to
    generate AND deduplicate simultaneously caused duplicates to slip through.

    Returns a list of dicts: [{name, description, owner_type}]
    Each new frame is written to the narrative_frames table with source='llm'.
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
    summaries_text = "\n".join(f"- {item.title or ''}" for item in items)
    opponent_str = " and ".join(ctx["opponents"]) if ctx["opponents"] else "the opponent"
    existing_frames = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).all()

    # ── Pass 1: Generate candidates from articles (no existing frames) ────────
    generation_prompt = f"""You are a political intelligence analyst. Your job is to read recent article titles from a congressional race and identify the specific recurring narratives playing out.

CAMPAIGN:
- Our candidate: {ctx["candidate"]}
- Race: {ctx["race"]}
- Location: {ctx["location"]}
- Opponent: {opponent_str}

RECENT ARTICLE TITLES:
{summaries_text}

A NARRATIVE is a recurring theme — a specific claim, attack, or story that appears across multiple articles.
A NARRATIVE is NOT a single event (one visit, one press conference, one announcement).

GOOD examples:
- "Bresnahan's Stock Trading Scandal" — repeated coverage of him trading stocks while in office
- "Cognetti's Anti-Corruption Message" — she keeps attacking insider trading as her core message
- "Bresnahan Touts Veteran Support" — his campaign keeps promoting his vet credentials across many articles

BAD examples (too specific — single events, not recurring):
- "Bresnahan Visits Tobyhanna VFW" ← one event
- "Cognetti Files FEC Report" ← one event

OWNER TYPE:
- "candidate": {ctx["candidate"]} is pushing this, or it helps her
- "opponent": {opponent_str} is pushing this, or it helps them
- "media": neutral, neither side owns it

Return ONLY a JSON array, no other text:
[
  {{
    "name": "Narrative name (6 words max)",
    "description": "One sentence: the specific recurring claim and who is making it.",
    "owner_type": "candidate" or "opponent" or "media"
  }}
]"""

    try:
        from app.services.llm_provider import get_provider, get_ingestion_provider, MockLLMProvider
        provider = get_provider()
        if isinstance(provider, MockLLMProvider):
            provider = get_ingestion_provider()
        if isinstance(provider, MockLLMProvider):
            return []

        raw = provider.complete(generation_prompt)
        if not raw or not raw.strip():
            return []

        candidates = _parse_json_array(raw, "suggest_frames.generation")
        if not candidates:
            return []

        logger.info("narrative_frames.suggest_frames: %d candidates from generation pass", len(candidates))

        # ── Pass 2: Dedup candidates against existing frames ─────────────────
        if existing_frames:
            candidates = _dedup_candidates(candidates, existing_frames, provider, ctx)

        if not candidates:
            logger.info("narrative_frames.suggest_frames: all candidates were duplicates — nothing new to add")
            return []

        # ── Persist new frames ────────────────────────────────────────────────
        created = []
        seen_names: set[str] = set()

        for f in candidates:
            name = (f.get("name") or "").strip()
            description = _clean_description((f.get("description") or "").strip())
            owner_type = f.get("owner_type", "media")
            if owner_type not in ("candidate", "opponent", "media"):
                owner_type = "media"
            if not name or name in seen_names:
                continue
            seen_names.add(name)

            owner_type = _validate_owner_type(
                owner_type=owner_type,
                frame_text=f"{name} {description}",
                candidate=ctx["candidate"],
                opponents=ctx["opponents"],
            )

            existing = db.query(NarrativeFrame).filter(NarrativeFrame.name == name).first()
            if existing:
                if existing.owner_type != owner_type:
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
        logger.info("narrative_frames: suggested %d new frames", len(created))

        _revalidate_all_frames(db, ctx)

        # After adding new frames, audit all frames for duplicates. This catches
        # any that slipped through the two-pass generation/dedup process.
        audit_result = audit_duplicates(db)
        if audit_result.get("merged", 0):
            logger.info(
                "narrative_frames: post-suggest audit merged %d duplicate frames",
                audit_result["merged"],
            )

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
    Ask Groq which active narrative frames this article belongs to. Writes
    a FrameClusterMatch row (UPSERT) for the article's cluster on every
    matched frame. Returns the list of matched frame IDs.
    """
    frames = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).all()
    if not frames:
        return []

    if not item.summary and not item.title:
        return []

    ctx = _campaign_context(db)

    # Skip articles that are not clearly relevant to this race. Score 55 is the
    # lower bound of "solidly on-topic" — below that, articles merely mention
    # the race in passing and produce too many false-positive frame matches.
    relevance = getattr(item, "race_relevance_score", None) or 0
    if relevance < 55:
        logger.debug("narrative_frames.match_article: skipping item=%d (relevance=%d)", item.id, relevance)
        return []

    frames_list = "\n".join(
        f"{i+1}. {f.name}: {f.description or ''}"
        for i, f in enumerate(frames)
    )

    # Prefer the cached structured extraction (summary + framing + opponent attacks)
    # over the raw article body — much shorter prompt, no need to re-read article.
    cached_summary = item.summary or item.title or ""
    cached_framing = ""
    cached_attacks = ""
    used_cache = False
    if item.structured_extraction:
        try:
            extracted = json.loads(item.structured_extraction)
            cached_summary = extracted.get("one_sentence") or cached_summary
            cached_framing = extracted.get("framing") or ""
            attacks = extracted.get("opponent_attacks") or []
            if attacks:
                cached_attacks = "\n".join(
                    f"- {a.get('text','')}" for a in attacks if a.get("text")
                )
            used_cache = True
        except Exception:
            used_cache = False

    article_section = f"""Title: {item.title or "No title"}
Summary: {cached_summary}"""
    if cached_framing:
        article_section += f"\nFraming: {cached_framing}"
    if cached_attacks:
        article_section += f"\nOpponent statements:\n{cached_attacks}"

    if used_cache:
        logger.debug(
            "narrative_frames.match_article: item=%d using cached extraction (no article re-read)",
            item.id,
        )

    prompt = f"""You are a political research assistant tagging news articles with the campaign narratives they cover.

NARRATIVES:
{frames_list}

ARTICLE:
{article_section}

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
        from app.services.llm_provider import get_ingestion_provider, MockLLMProvider
        provider = get_ingestion_provider()
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

        text = _repair_json(text)

        try:
            matched_items = json.loads(text)
        except json.JSONDecodeError:
            return []
        if not isinstance(matched_items, list):
            return []

        matched_frame_ids = []
        seen_frame_ids: set[int] = set()  # guard against LLM returning same frame twice
        for entry in matched_items:
            # Accept either a plain int (old LLM output shape) or a dict with
            # a "frame" key (newer shape). The dict shape also carries a
            # "snippet" field that the legacy NarrativeFrameMention used to
            # store; cluster-native FrameClusterMatch has no per-article
            # snippet so we just take the frame index.
            if isinstance(entry, int):
                idx = entry
            elif isinstance(entry, dict):
                idx = entry.get("frame")
            else:
                continue

            if not isinstance(idx, int) or idx < 1 or idx > len(frames):
                continue
            frame = frames[idx - 1]

            if frame.id in seen_frame_ids:
                continue
            seen_frame_ids.add(frame.id)

            # Reject the match if the article doesn't contain any topic-specific
            # keywords from this frame. Catches general roundup articles that the
            # LLM tags to specific frames because they broadly cover the race.
            if not _article_passes_keyword_gate(item, frame.name, frame.description or "", ctx):
                logger.debug(
                    "narrative_frames.match_article: item=%d frame=%d '%s' rejected by keyword gate",
                    item.id, frame.id, frame.name,
                )
                continue

            # Cluster-native only (Phase D). UPSERT a FrameClusterMatch keyed
            # on the item's cluster. The per-article snippet (extracted_text)
            # that the legacy NarrativeFrameMention stored has no analogue at
            # the cluster level — clusters have many articles, and the snippet
            # was always article-scoped — so it disappears with the legacy
            # write. The snippet validation logic remains available for
            # diagnostic logging if future work re-introduces per-article
            # provenance on a side table.
            if item.story_cluster_id:
                from app.services import cluster_writes
                cluster_writes.upsert_frame_match(
                    db,
                    frame_id=frame.id,
                    cluster_id=item.story_cluster_id,
                    confidence=75,
                    source_type="cluster_runtime",
                    matched_by="llm",
                )

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

        # Cluster-native: skip clusters that already have any FrameClusterMatch
        # rows (they were processed in a previous run). Iterate one
        # representative article per remaining cluster instead of every article
        # — that's the saved-tokens story.
        from app.models import FrameClusterMatch, StoryCluster
        already_matched_clusters = {
            row[0]
            for row in db.query(FrameClusterMatch.story_cluster_id).distinct().all()
        }

        candidate_clusters = (
            db.query(StoryCluster, SourceItem)
            .join(SourceItem, SourceItem.id == StoryCluster.representative_source_item_id)
            .filter(
                StoryCluster.last_seen_at >= cutoff,
                SourceItem.archived_as_irrelevant == False,
            )
            .all()
        )
        items = [
            rep for (cluster, rep) in candidate_clusters
            if cluster.id not in already_matched_clusters
        ]
        logger.info(
            "rematch_all: %d candidate clusters, %d already matched, processing %d new",
            len(candidate_clusters), len(already_matched_clusters), len(items),
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

        # Prune frames the LLM never matched. Check the cluster-native table —
        # the legacy NarrativeFrameMention is still written for parity but
        # analytics no longer reads it (Phase C). A frame with zero
        # FrameClusterMatch rows is ungrounded in real coverage.
        from app.models import FrameClusterMatch
        frames = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).all()
        pruned = 0
        for frame in frames:
            cluster_match_count = (
                db.query(func.count(FrameClusterMatch.id))
                .filter(FrameClusterMatch.frame_id == frame.id)
                .scalar()
            )
            if cluster_match_count == 0:
                logger.info(
                    "narrative_frames: pruning empty frame %d '%s' (0 cluster matches in archive)",
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


def audit_duplicates(db: Session) -> dict:
    """Ask the LLM to audit all existing frames and identify semantic duplicates.

    For each duplicate group the lower-mention frame is deleted. Its mentions
    are also deleted so rematch can re-assign them to the surviving frame.

    Returns {"merged": N, "groups": [...]} describing what was cleaned up.
    """
    from sqlalchemy import func
    from app.services.llm_provider import get_provider, get_ingestion_provider, MockLLMProvider

    frames = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).all()
    if len(frames) < 2:
        return {"merged": 0, "groups": []}

    # Build frame list with cluster-match counts so the LLM can see what each covers
    from app.models import FrameClusterMatch
    frame_lines = []
    counts: dict[int, int] = {}
    for f in frames:
        count = (
            db.query(func.count(FrameClusterMatch.id))
            .filter(FrameClusterMatch.frame_id == f.id)
            .scalar()
        )
        counts[f.id] = count
        frame_lines.append(
            f"ID {f.id} [{f.owner_type}] \"{f.name}\" ({count} mentions)\n"
            f"  Description: {f.description or '(none)'}"
        )

    frames_text = "\n".join(frame_lines)

    prompt = f"""You are auditing a list of political campaign narrative frames to find semantic duplicates.

FRAMES:
{frames_text}

Two frames are DUPLICATES if they track the same specific claim from the same actor/angle — even if the wording is different.

Two frames on the SAME TOPIC are NOT duplicates if they track different claims or different actors:
- "Bresnahan Touts Veteran Support" (his campaign promoting his record) vs "Bresnahan's Veterans Policy Criticized" (critics attacking it) → DIFFERENT, keep both
- "Cognetti's Anti-Corruption" (her platform) vs "Bresnahan's Stock Trades" (specific attack) → DIFFERENT, keep both

Two frames ARE duplicates if an article that belongs to one would always also belong to the other:
- "Cognetti's Anti-Corruption" vs "Cognetti Anti-Corruption Message Unfolds" → SAME, merge
- "Cognetti's Dual Campaigns" vs "Cognetti's Dual Roles Conflict" → SAME if both are about her running for two offices simultaneously, merge
- "Cognetti's Congressional Campaign Momentum" vs "Cognetti Outraises Bresnahan" → SAME if outraising IS the momentum story, merge

Return ONLY a JSON array of duplicate groups. Each group lists the IDs that are duplicates of each other.
Return [] if there are no duplicates.

Example: [[2, 12], [4, 24], [18, 20]]

Each number is a frame ID from the list above. Do not include frames that have no duplicate."""

    provider = get_provider()
    if isinstance(provider, MockLLMProvider):
        provider = get_ingestion_provider()
    if isinstance(provider, MockLLMProvider):
        return {"merged": 0, "groups": [], "error": "LLM unavailable"}

    raw = provider.complete(prompt)
    if not raw or not raw.strip():
        return {"merged": 0, "groups": [], "error": "LLM returned empty response"}

    groups = _parse_json_array(raw, "audit_duplicates")
    if not groups:
        logger.info("narrative_frames.audit_duplicates: no duplicates found")
        return {"merged": 0, "groups": []}

    merged = 0
    result_groups = []

    for group in groups:
        if not isinstance(group, list) or len(group) < 2:
            continue
        # Validate all IDs exist
        group_frames = [f for f in frames if f.id in group]
        if len(group_frames) < 2:
            continue

        # Keep the frame with the most mentions; delete the rest
        group_frames.sort(key=lambda f: counts.get(f.id, 0), reverse=True)
        survivor = group_frames[0]
        to_delete = group_frames[1:]

        group_info = {
            "kept": {"id": survivor.id, "name": survivor.name, "mentions": counts.get(survivor.id, 0)},
            "deleted": [],
        }

        for dup in to_delete:
            mention_count = counts.get(dup.id, 0)
            # Cluster-native FrameClusterMatch rows pointing at the merged
            # frame are the analytics-relevant ones; sweep them up so the
            # surviving frame is the only carrier post-merge.
            db.query(FrameClusterMatch).filter(FrameClusterMatch.frame_id == dup.id).delete()
            db.delete(dup)
            group_info["deleted"].append({"id": dup.id, "name": dup.name, "mentions": mention_count})
            merged += 1
            logger.info(
                "narrative_frames.audit_duplicates: merged '%s' (id=%d, %d mentions) into '%s' (id=%d)",
                dup.name, dup.id, mention_count, survivor.name, survivor.id,
            )

        result_groups.append(group_info)

    if merged:
        db.commit()

    logger.info("narrative_frames.audit_duplicates: merged %d duplicate frames into %d survivors", merged, len(result_groups))
    return {"merged": merged, "groups": result_groups}


def repair_frame_data(db: Session) -> dict:
    """One-time (idempotent) cleanup of two known data quality problems.

    1. Frame descriptions that contain leaked prompt scaffolding such as
       ", owner_type: candidate" are stripped clean.

    2. NarrativeFrameMention.extracted_text values that cannot be verified
       against the corresponding article's body/title/summary are nulled out.
       These are hallucinated quotes produced when the LLM had no real text to
       quote from (thin / paywalled articles, or early cached-extraction path).

    Safe to call on every startup — cheap when there is nothing to fix.
    """
    desc_fixed = 0
    frames = db.query(NarrativeFrame).all()
    for frame in frames:
        if not frame.description:
            continue
        cleaned = _clean_description(frame.description)
        if cleaned != frame.description:
            logger.info(
                "repair_frame_data: cleaned description for frame %d '%s'",
                frame.id, frame.name,
            )
            frame.description = cleaned
            desc_fixed += 1
    if desc_fixed:
        db.commit()

    # Null out unverifiable extracted_text in batches
    mentions_fixed = 0
    mentions = (
        db.query(NarrativeFrameMention)
        .filter(NarrativeFrameMention.extracted_text.isnot(None))
        .all()
    )
    item_cache: dict[int, SourceItem] = {}
    for mention in mentions:
        item = item_cache.get(mention.source_item_id)
        if item is None:
            item = db.query(SourceItem).filter(SourceItem.id == mention.source_item_id).first()
            if item:
                item_cache[mention.source_item_id] = item
        if not item:
            continue
        verified = _validate_snippet(mention.extracted_text, item)
        if verified is None:
            mention.extracted_text = None
            mentions_fixed += 1

    if mentions_fixed:
        db.commit()

    # Remove existing false-positive matches: articles that score below the
    # relevance threshold or fail the keyword gate are almost certainly noise.
    ctx = _campaign_context(db)
    all_frames = {f.id: f for f in db.query(NarrativeFrame).all()}
    false_positives = 0
    all_mentions = db.query(NarrativeFrameMention).all()
    for mention in all_mentions:
        item = item_cache.get(mention.source_item_id)
        if item is None:
            item = db.query(SourceItem).filter(SourceItem.id == mention.source_item_id).first()
            if item:
                item_cache[mention.source_item_id] = item
        if not item:
            continue
        frame = all_frames.get(mention.frame_id)
        if not frame:
            continue
        # Drop if article is below the relevance floor
        if (item.race_relevance_score or 0) < 55:
            db.delete(mention)
            false_positives += 1
            continue
        # Drop if article fails the keyword gate for this frame
        if not _article_passes_keyword_gate(item, frame.name, frame.description or "", ctx):
            db.delete(mention)
            false_positives += 1

    if false_positives:
        db.commit()

    logger.info(
        "repair_frame_data: fixed %d frame descriptions, nulled %d unverifiable quotes, removed %d false-positive matches",
        desc_fixed, mentions_fixed, false_positives,
    )
    return {"descriptions_fixed": desc_fixed, "quotes_nulled": mentions_fixed, "false_positives_removed": false_positives}


def _narrative_stage(total: int, this_week: int, last_week: int, days_since_last: float | None) -> str:
    if total == 0 or days_since_last is None or days_since_last > 14:
        return "dormant"
    if total <= 5:
        return "emerging"
    if this_week >= max(2, last_week * 1.5):
        return "spreading"
    if last_week >= 3 and this_week < last_week * 0.5:
        return "fading"
    if total > 15:
        return "mainstream"
    return "active"


def get_frames_with_counts(db: Session) -> list[dict]:
    """Return all active frames with cluster counts and weighted reach.

    Cluster-native (Phase C). Field names on the response stay the same
    (`mentions_this_week`, etc.) so the frontend keeps working — but the
    numbers now count distinct story clusters, not raw article mentions, so
    wire syndication doesn't inflate them.
    """
    from sqlalchemy import case, func
    from app.models import FrameClusterMatch, Outlet, StoryCluster
    now = datetime.utcnow()
    week_start = now - timedelta(days=7)
    prev_week_start = now - timedelta(days=14)

    reach_weight = case(
        (Outlet.monthly_visitors.isnot(None), Outlet.monthly_visitors * 0.003),
        else_=func.coalesce(Outlet.authority_score, 5) / 10.0,
    )

    def _cluster_count(frame_id: int, start=None, end=None) -> int:
        q = db.query(func.count(FrameClusterMatch.id)).filter(
            FrameClusterMatch.frame_id == frame_id
        )
        if start is not None:
            q = q.filter(FrameClusterMatch.first_seen_at >= start)
        if end is not None:
            q = q.filter(FrameClusterMatch.first_seen_at < end)
        return q.scalar() or 0

    def _reach_for(frame_id: int, start=None, end=None) -> float:
        """Sum reach_weight over every member article of every cluster matched
        to this frame, optionally filtering by article publish window. Reach
        is intentionally NOT cluster-deduped."""
        q = (
            db.query(func.round(func.sum(reach_weight), 1))
            .select_from(FrameClusterMatch)
            .join(StoryCluster, StoryCluster.id == FrameClusterMatch.story_cluster_id)
            .join(SourceItem, SourceItem.story_cluster_id == StoryCluster.id)
            .outerjoin(Outlet, Outlet.id == SourceItem.outlet_id)
            .filter(FrameClusterMatch.frame_id == frame_id)
        )
        if start is not None:
            q = q.filter(SourceItem.published_at >= start)
        if end is not None:
            q = q.filter(SourceItem.published_at < end)
        return float(q.scalar() or 0)

    frames = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).all()
    result = []
    for frame in frames:
        this_week = _cluster_count(frame.id, start=week_start)
        last_week = _cluster_count(frame.id, start=prev_week_start, end=week_start)
        total = _cluster_count(frame.id)

        reach_this_week = _reach_for(frame.id, start=week_start)
        reach_last_week = _reach_for(frame.id, start=prev_week_start, end=week_start)
        reach_total = _reach_for(frame.id)

        trend = "up" if this_week > last_week else ("down" if this_week < last_week else "flat")

        # First / last cluster attachment timestamps
        first_seen = (
            db.query(func.min(FrameClusterMatch.first_seen_at))
            .filter(FrameClusterMatch.frame_id == frame.id)
            .scalar()
        )
        last_seen = (
            db.query(func.max(FrameClusterMatch.last_seen_at))
            .filter(FrameClusterMatch.frame_id == frame.id)
            .scalar()
        )
        days_since = (now - last_seen).days if last_seen else None
        stage = _narrative_stage(total, this_week, last_week, days_since)

        # Key articles: first cluster, peak-day cluster, latest cluster — each
        # represented by its dynamically resolved representative article.
        first_cluster_id = (
            db.query(FrameClusterMatch.story_cluster_id)
            .filter(FrameClusterMatch.frame_id == frame.id)
            .order_by(FrameClusterMatch.first_seen_at.asc())
            .limit(1).scalar()
        )
        latest_cluster_id = (
            db.query(FrameClusterMatch.story_cluster_id)
            .filter(FrameClusterMatch.frame_id == frame.id)
            .order_by(FrameClusterMatch.last_seen_at.desc())
            .limit(1).scalar()
        )
        peak_day_row = (
            db.query(
                func.date(FrameClusterMatch.first_seen_at).label("d"),
                func.count(FrameClusterMatch.id).label("n"),
            )
            .filter(FrameClusterMatch.frame_id == frame.id)
            .group_by(func.date(FrameClusterMatch.first_seen_at))
            .order_by(func.count(FrameClusterMatch.id).desc())
            .first()
        )
        peak_cluster_id = None
        if peak_day_row:
            # Pick the highest-authority representative on that peak day
            peak_cluster_id = (
                db.query(FrameClusterMatch.story_cluster_id)
                .select_from(FrameClusterMatch)
                .join(StoryCluster, StoryCluster.id == FrameClusterMatch.story_cluster_id)
                .join(SourceItem, SourceItem.id == StoryCluster.representative_source_item_id)
                .outerjoin(Outlet, Outlet.id == SourceItem.outlet_id)
                .filter(
                    FrameClusterMatch.frame_id == frame.id,
                    func.date(FrameClusterMatch.first_seen_at) == peak_day_row.d,
                )
                .order_by(Outlet.authority_score.desc().nulls_last())
                .limit(1).scalar()
            )

        key_articles: list[dict] = []
        seen_cluster_ids: set = set()
        for role, cid in [("First mention", first_cluster_id), ("Peak day", peak_cluster_id), ("Latest", latest_cluster_id)]:
            if not cid or cid in seen_cluster_ids:
                continue
            seen_cluster_ids.add(cid)
            cluster = db.query(StoryCluster).filter_by(id=cid).first()
            if not cluster:
                continue
            rep = db.query(SourceItem).filter_by(id=cluster.representative_source_item_id).first()
            if not rep:
                continue
            key_articles.append({
                "role": role,
                "id": rep.id,
                "title": rep.title,
                "summary": rep.summary or cluster.summary_representative,
                "source_name": rep.source_name,
                "source_url": rep.source_url,
                "published_at": rep.published_at.isoformat() if rep.published_at else None,
                # Legacy NFM stored an extracted_text snippet on the mention row;
                # cluster-native doesn't (snippet is a per-article concept and
                # the cluster has many articles). Leave as None for now.
                "extracted_text": None,
            })

        # Unique outlets this week / last week — across all member articles of
        # any cluster matched to this frame, in the window.
        def _unique_outlets(start, end=None) -> int:
            q = (
                db.query(func.count(func.distinct(SourceItem.outlet_id)))
                .select_from(FrameClusterMatch)
                .join(StoryCluster, StoryCluster.id == FrameClusterMatch.story_cluster_id)
                .join(SourceItem, SourceItem.story_cluster_id == StoryCluster.id)
                .filter(
                    FrameClusterMatch.frame_id == frame.id,
                    SourceItem.outlet_id.isnot(None),
                    SourceItem.published_at >= start,
                    SourceItem.published_at.isnot(None),
                )
            )
            if end is not None:
                q = q.filter(SourceItem.published_at < end)
            return q.scalar() or 0

        unique_outlets_this_week = _unique_outlets(week_start)
        unique_outlets_last_week = _unique_outlets(prev_week_start, week_start)

        # Days active in last 7 — distinct publish dates across member articles
        days_active_last_7 = (
            db.query(func.count(func.distinct(func.date(SourceItem.published_at))))
            .select_from(FrameClusterMatch)
            .join(StoryCluster, StoryCluster.id == FrameClusterMatch.story_cluster_id)
            .join(SourceItem, SourceItem.story_cluster_id == StoryCluster.id)
            .filter(
                FrameClusterMatch.frame_id == frame.id,
                SourceItem.published_at >= week_start,
                SourceItem.published_at.isnot(None),
            )
            .scalar() or 0
        )

        # Outlet tier breakdown — distinct outlets across all member articles
        # of all clusters matching this frame, grouped by outlet_type.
        tier_rows = (
            db.query(Outlet.outlet_type, func.count(func.distinct(Outlet.id)))
            .select_from(FrameClusterMatch)
            .join(StoryCluster, StoryCluster.id == FrameClusterMatch.story_cluster_id)
            .join(SourceItem, SourceItem.story_cluster_id == StoryCluster.id)
            .join(Outlet, Outlet.id == SourceItem.outlet_id)
            .filter(FrameClusterMatch.frame_id == frame.id)
            .group_by(Outlet.outlet_type)
            .all()
        )
        tiers: dict[str, int] = {"national": 0, "regional": 0, "local": 0, "blog": 0, "social": 0}
        for outlet_type, count in tier_rows:
            if outlet_type in ("national", "broadcast"):
                tiers["national"] += count
            elif outlet_type == "regional_news":
                tiers["regional"] += count
            elif outlet_type == "local_news":
                tiers["local"] += count
            elif outlet_type == "blog":
                tiers["blog"] += count
            elif outlet_type == "social":
                tiers["social"] += count

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
            "unique_outlets_this_week": unique_outlets_this_week,
            "unique_outlets_last_week": unique_outlets_last_week,
            "days_active_last_7": days_active_last_7,
            "reach_this_week": reach_this_week,
            "reach_last_week": reach_last_week,
            "reach_total": reach_total,
            "trend": trend,
            "stage": stage,
            "outlet_tiers": tiers,
            "first_seen_at": first_seen.isoformat() if first_seen else None,
            "last_seen_at": last_seen.isoformat() if last_seen else None,
            "key_articles": key_articles,
        })

    result.sort(key=lambda x: x["mentions_this_week"], reverse=True)
    return result
