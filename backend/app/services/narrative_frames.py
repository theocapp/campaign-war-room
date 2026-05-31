"""
Narrative frame management: auto-suggest frames from article summaries,
match articles to frames.
"""
import hashlib
import html
import json
import logging
import math
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


# ── Rematch gate (embedding pre-filter) ──────────────────────────────────────
# match_article_to_frames used to ask the LLM about every active frame for
# every article. Calibration showed the LLM rejects ~60% of those pairs based
# on similarity alone — work an embedding cosine can do for free.
#
# We cache frame embeddings in-memory (frames don't change often). The
# per-frame thresholds come from app/scripts/calibrate_rematch_gate.py and
# live in backend/data/rematch_thresholds.json. Calibration is manual; this
# code consumes whatever the calibration produced.
_FRAME_EMBEDDING_CACHE: dict = {}  # frame_id -> (content_hash, embedding)
_FRAME_THRESHOLDS_CACHE: Optional[dict] = None  # str(frame_id) -> threshold
_GLOBAL_FLOOR_DEFAULT = 0.30  # used when no calibration file or new frame
_REMATCH_THRESHOLDS_PATH = (
    Path(__file__).parent.parent.parent / "data" / "rematch_thresholds.json"
)


def _frame_content_hash(frame) -> str:
    """SHA1 of frame's matching-relevant content, used to invalidate the
    embedding cache when a frame is edited.
    """
    src = f"{frame.name}|||{frame.description or ''}"
    return hashlib.sha1(src.encode("utf-8")).hexdigest()


def _load_rematch_thresholds() -> dict:
    """Read the calibrated per-frame thresholds. Cached after first load."""
    global _FRAME_THRESHOLDS_CACHE
    if _FRAME_THRESHOLDS_CACHE is not None:
        return _FRAME_THRESHOLDS_CACHE
    if not _REMATCH_THRESHOLDS_PATH.exists():
        logger.warning(
            "narrative_frames: no calibration file at %s — using global floor %.2f for all frames",
            _REMATCH_THRESHOLDS_PATH, _GLOBAL_FLOOR_DEFAULT,
        )
        _FRAME_THRESHOLDS_CACHE = {}
        return _FRAME_THRESHOLDS_CACHE
    try:
        data = json.loads(_REMATCH_THRESHOLDS_PATH.read_text())
        _FRAME_THRESHOLDS_CACHE = data.get("frame_thresholds", {})
        return _FRAME_THRESHOLDS_CACHE
    except Exception as e:
        logger.warning("narrative_frames: failed to load rematch thresholds: %s", e)
        _FRAME_THRESHOLDS_CACHE = {}
        return _FRAME_THRESHOLDS_CACHE


def invalidate_rematch_thresholds_cache() -> None:
    """Force the next call to re-read the calibration file. Call after
    re-running the calibration script."""
    global _FRAME_THRESHOLDS_CACHE
    _FRAME_THRESHOLDS_CACHE = None


def _cosine(a: list, b: list) -> float:
    if not a or not b:
        return 0.0
    s = 0.0; na = 0.0; nb = 0.0
    for x, y in zip(a, b):
        s += x * y; na += x * x; nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return s / (math.sqrt(na) * math.sqrt(nb))


def _ensure_frame_embeddings(frames: list) -> None:
    """Embed any frames whose content has changed (or whose embedding isn't
    cached yet). Mutates _FRAME_EMBEDDING_CACHE.
    """
    from app.services.embeddings import embed_texts
    needs: list = []
    for f in frames:
        h = _frame_content_hash(f)
        cached = _FRAME_EMBEDDING_CACHE.get(f.id)
        if cached is None or cached[0] != h:
            needs.append((f, h))
    if not needs:
        return
    texts = [f"{f.name}\n\n{f.description or ''}".strip() for f, _ in needs]
    embs = embed_texts(texts, task_type="SEMANTIC_SIMILARITY")
    for (f, h), e in zip(needs, embs):
        if e is not None:
            _FRAME_EMBEDDING_CACHE[f.id] = (h, e)


def _get_or_compute_article_embedding(item) -> Optional[list]:
    """Read the article's cached frame_match_embedding if it was computed
    with the currently-configured embedding model. Otherwise compute it
    fresh and write it back to the cache.

    Returns None on embedding failure (caller falls back to full frame list).
    """
    from app.services.embeddings import current_primary_model_name, embed_texts

    current_model = current_primary_model_name()

    cached_blob = getattr(item, "frame_match_embedding", None)
    cached_model = getattr(item, "frame_match_embedding_model", None)
    if cached_blob and cached_model == current_model:
        try:
            return json.loads(cached_blob)
        except Exception:
            # Corrupt cache — fall through to re-embed
            pass

    article_text = (item.title or "") + "\n\n" + ((item.raw_text or item.summary or "")[:1500])
    article_text = article_text.strip()
    if not article_text:
        return None

    try:
        emb = embed_texts([article_text], task_type="SEMANTIC_SIMILARITY")[0]
    except Exception as e:
        logger.debug("rematch gate: article embed failed for item=%d: %s", item.id, e)
        return None
    if emb is None:
        return None

    # Write-through cache. Only set the attributes when the SQLAlchemy session
    # tracks this object; standalone (e.g. test) objects just get the values
    # set without persistence.
    try:
        item.frame_match_embedding = json.dumps(emb)
        item.frame_match_embedding_model = current_model
        # Caller's session will commit (or not). We avoid committing here so
        # this stays composable inside the broader rematch transaction.
    except Exception:
        # Non-ORM dict-like test object — ignore.
        pass
    return emb


def _shortlist_frames_for_article(item, frames: list) -> list:
    """Embedding-gated shortlist. Returns the subset of `frames` whose
    per-frame similarity threshold the article's embedding clears.

    On any embedding failure, falls back to the full frame list (safe — the
    LLM step will still filter). Returns [] only when embedding succeeds AND
    no frame clears its threshold.
    """
    article_emb = _get_or_compute_article_embedding(item)
    if article_emb is None:
        return frames

    _ensure_frame_embeddings(frames)
    thresholds = _load_rematch_thresholds()

    shortlist: list = []
    for f in frames:
        entry = _FRAME_EMBEDDING_CACHE.get(f.id)
        if entry is None:
            # Couldn't embed this frame — include it (LLM will judge).
            shortlist.append(f)
            continue
        _, frame_emb = entry
        sim = _cosine(article_emb, frame_emb)
        threshold = thresholds.get(str(f.id), _GLOBAL_FLOOR_DEFAULT)
        if sim >= threshold:
            shortlist.append(f)
    return shortlist


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

# Articles published before this date are almost certainly date-parsing
# artifacts (related-links, sidebar evergreen, malformed feed pubDates).
# 398 articles in the live DB have published_at < 2024 — including one
# stamped 0001-01-01. Using a hard floor here keeps frame "first_seen"
# displays from being poisoned by these outliers. NOT a substitute for
# fixing the ingest path that creates them.
_PLAUSIBLE_DATE_FLOOR = datetime(2024, 1, 1)


def _parse_momentum_data(raw: Optional[str]) -> Optional[dict]:
    """Decode the momentum_data column safely.

    Stored as a JSON string by services/frame_momentum.py. Returns None if
    the column is empty OR if the JSON is malformed — we'd rather show no
    tooltip than crash the list endpoint over a single bad row.
    """
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning("narrative_frames: malformed momentum_data — skipping")
        return None


def _compute_strategic_lens(frame) -> Optional[dict]:
    """Wrap services.strategic_lens.strategic_lens() with safe import.

    Inline import so a broken `strategic_lens` module never 500s the list
    endpoint (the rest of the response is still useful without this field).
    """
    try:
        from app.services.strategic_lens import strategic_lens
    except Exception as exc:
        logger.warning("strategic_lens import failed: %s", exc)
        return None
    return strategic_lens(frame.momentum_signal, frame.owner_type)


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

# In-memory progress tracker for the current rematch run.
# Safe for single-server use — reset each time rematch_all starts.
_rematch_progress: dict = {"running": False, "done": 0, "total": 0, "started_at": None}
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
    """Downgrade owner_type to 'media' only when the frame text names no
    party at all. A frame that *attacks* the opponent legitimately favors
    the candidate (and vice-versa), so checking only that the frame mentions
    the same-side person was too strict — it was burying anti-opponent
    frames under 'media' even though the LLM correctly tagged them as
    candidate-favoring.

    The rule now: if the LLM says this frame has an owner, the frame must
    mention someone on either side. Otherwise it's truly neutral coverage.
    """
    if owner_type not in ("candidate", "opponent"):
        return owner_type
    mentions_candidate = _mentions_person(frame_text, candidate)
    mentions_any_opponent = any(_mentions_person(frame_text, o) for o in opponents)
    if not mentions_candidate and not mentions_any_opponent:
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


def reclassify_media_frames(db: Session) -> dict:
    """One-shot pass over every frame currently tagged 'media' — ask the LLM
    whether the frame actually favors a side. Use only when the validation
    rules change and existing frames may be misclassified (e.g. anti-opponent
    frames that were previously buried under 'media').

    Updates DB in place. Returns a summary dict.
    """
    from app.services.llm_provider import get_judge_provider

    ctx = _campaign_context(db)
    candidate = ctx["candidate"]
    opponents = ", ".join(ctx["opponents"]) or "the opponent"

    media_frames = (
        db.query(NarrativeFrame)
        .filter(NarrativeFrame.active == True, NarrativeFrame.owner_type == "media")  # noqa: E712
        .all()
    )
    if not media_frames:
        return {"checked": 0, "reclassified": 0}

    llm = get_judge_provider()
    reclassified = 0
    for frame in media_frames:
        prompt = f"""You are a political analyst classifying a narrative frame in a campaign.

RACE: {candidate} vs {opponents}

FRAME:
- Name: {frame.name}
- Description: {frame.description or ''}

Decide who this narrative favors. Reply with exactly one word:
- "candidate" if it promotes {candidate} or attacks/criticizes {opponents}
- "opponent" if it promotes {opponents} or attacks/criticizes {candidate}
- "media" if it's neutral news coverage that helps neither side

ANSWER:"""
        try:
            ans = llm.complete(prompt).strip().lower()
        except Exception as e:
            logger.warning("reclassify_media_frames: LLM error for frame %d: %s", frame.id, e)
            continue
        # Take the first matching token in case the LLM adds explanation
        new_type = next((t for t in ("candidate", "opponent", "media") if t in ans), None)
        if not new_type or new_type == "media":
            continue
        # Sanity check via the same validator — make sure the chosen side is
        # consistent with whose name is in the frame.
        new_type = _validate_owner_type(
            new_type, f"{frame.name} {frame.description or ''}",
            candidate=candidate, opponents=ctx["opponents"],
        )
        if new_type == frame.owner_type:
            continue
        logger.info("reclassify_media_frames: frame %d '%s' media → %s",
                    frame.id, frame.name, new_type)
        frame.owner_type = new_type
        reclassified += 1
    if reclassified:
        db.commit()
    return {"checked": len(media_frames), "reclassified": reclassified}


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

    # Embedding-gated shortlist: drop frames whose calibrated per-frame
    # similarity threshold the article doesn't clear. Lets us skip the LLM
    # entirely when no frame is plausibly relevant, and shortens the prompt
    # when only a few frames are. See _shortlist_frames_for_article above.
    n_total_frames = len(frames)
    frames = _shortlist_frames_for_article(item, frames)
    if not frames:
        logger.debug(
            "narrative_frames.match_article: item=%d — embedding gate found no candidate frames (was %d)",
            item.id, n_total_frames,
        )
        return []
    if len(frames) < n_total_frames:
        logger.debug(
            "narrative_frames.match_article: item=%d — shortlist %d/%d frames",
            item.id, len(frames), n_total_frames,
        )

    OWNER_ROLE = {
        "candidate": "OUR MESSAGE",
        "opponent":  "OPPONENT ATTACK",
        "media":     "MEDIA THEME",
    }
    frames_list = "\n".join(
        f"{i+1}. [{OWNER_ROLE.get(f.owner_type, f.owner_type.upper())}] {f.name}: {f.description or ''}"
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

    # Include a truncated chunk of the article body so the LLM can ground its
    # match in the actual reporting, not just the one-sentence LLM summary.
    # The summary alone often mentions "PA-08" / "the district" / "campaign"
    # and triggers false-positive frame matches on tangential national stories.
    _MAX_BODY_CHARS = 2000
    body_excerpt = ""
    if item.raw_text:
        body_clean = item.raw_text.strip()
        if len(body_clean) > _MAX_BODY_CHARS:
            body_excerpt = body_clean[:_MAX_BODY_CHARS].rstrip() + " …[truncated]"
        else:
            body_excerpt = body_clean

    article_section = f"""Title: {item.title or "No title"}
Summary: {cached_summary}"""
    if cached_framing:
        article_section += f"\nFraming: {cached_framing}"
    if cached_attacks:
        article_section += f"\nOpponent statements:\n{cached_attacks}"
    if body_excerpt:
        article_section += f"\n\nArticle body:\n{body_excerpt}"

    if used_cache:
        logger.debug(
            "narrative_frames.match_article: item=%d using cached extraction (no article re-read)",
            item.id,
        )

    prompt = f"""You are a political research assistant tagging news articles with the campaign narratives they cover.

NARRATIVES (each tagged with its perspective):
{frames_list}

ARTICLE:
{article_section}

TASK:
Decide which narratives this article meaningfully covers. The tag on each narrative matters:

[OUR MESSAGE] — Match ONLY if the article reports on or amplifies {ctx["candidate"]}'s own messaging/record on this topic. Do NOT match if the article attacks, mocks, or disputes this message — an attack piece from the opponent that merely mentions the topic does not count.

[OPPONENT ATTACK] — Match if the article covers, repeats, or reports on this attack line, whether as criticism of the opponent or as the attack itself being made. Both "Bresnahan buys stocks" and "Cognetti attacks Bresnahan on stocks" count.

[MEDIA THEME] — Match ONLY if the article discusses this theme specifically in the context of the {ctx["race"]} race — that is, it names {ctx["candidate"]}, an opponent, the district, or the race itself while covering this theme. Generic national coverage of the same topic does NOT count as a match.

Additional rules:
- MOST articles match 0 or 1 narratives. Match more than one narrative ONLY when the article body contains substantively distinct information about each.
- Do NOT match on vague thematic overlap, shared keywords, or topical adjacency — match only when the article has specific, substantive information about that narrative.
- HARD REQUIREMENT: For each match you propose, you must quote a verbatim sentence from the "Article body" section above that directly supports the match. If no such sentence exists in the body, do NOT include the match. Snippets cannot be paraphrased or summarized — they must be copied character-for-character from the body.
- Rate your confidence (0-100) per match:
    90-100 — central topic of the article, named explicitly with detail
    75-89  — covered as a clear secondary topic with substantive detail
    60-74  — mentioned with some specificity but not the article's focus
    40-59  — loose thematic overlap or passing reference only — DO NOT INCLUDE
    0-39   — DO NOT INCLUDE
- If you are uncertain whether the article truly covers a narrative, omit it.

Return ONLY a JSON array. Each element: {{"frame": <number>, "confidence": <0-100>, "snippet": "<verbatim sentence from the article body>"}}
Return [] if no narratives apply."""

    try:
        # Use the centralized judge provider (OpenAI gpt-4o-mini primary, Groq
        # fallback). Frame matching benefits from gpt-4o-mini's better
        # instruction-following on structured-JSON tasks. Cost is negligible —
        # ~$0.0001 per article × low-volume callers.
        from app.services.llm_provider import get_judge_provider, MockLLMProvider
        provider = get_judge_provider()
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
            confidence: Optional[int] = None
            snippet: Optional[str] = None
            if isinstance(entry, int):
                idx = entry
            elif isinstance(entry, dict):
                idx = entry.get("frame")
                raw_conf = entry.get("confidence")
                if isinstance(raw_conf, (int, float)):
                    confidence = max(0, min(100, int(raw_conf)))
                raw_snip = entry.get("snippet")
                if isinstance(raw_snip, str):
                    snippet = raw_snip
            else:
                continue

            if not isinstance(idx, int) or idx < 1 or idx > len(frames):
                continue
            frame = frames[idx - 1]

            if frame.id in seen_frame_ids:
                continue
            seen_frame_ids.add(frame.id)

            # Snippet validation: an LLM match without a verifiable verbatim
            # quote from the article body is a thematic hallucination. Drop
            # the match entirely. This replaces the brittle keyword gate —
            # snippet validation catches the same false positives without
            # killing legitimate matches whose frame name uses abstract verbs
            # ("Delivers Funding") that don't appear verbatim in news copy.
            verified = _validate_snippet(snippet or "", item)
            if verified is None:
                logger.info(
                    "narrative_frames.match_article: item=%d frame=%d '%s' "
                    "rejected — snippet failed verbatim check (conf=%s)",
                    item.id, frame.id, frame.name,
                    confidence if confidence is not None else "?",
                )
                continue

            # Second-pass verifier: after the snippet's verbatim check passes,
            # ask a focused LLM whether the extract is actually about this
            # frame's topic. Catches the "topically adjacent but wrong"
            # failure mode (e.g. a Cognetti traffic-plan quote getting
            # matched to Bresnahan's Healthcare Record because both mention
            # PA-08). Same V5 prompt that won the bake-off and purged 552
            # bad assignments during the V12 cleanup pass.
            from app.services.extract_verifier import verify_match
            verifier_result = verify_match(
                extract=verified,
                frame_id=frame.id,
                frame_name=frame.name,
                frame_description=frame.description,
                source_item_id=item.id,
            )
            if not verifier_result.keep:
                # The verifier already logged the rejection at INFO.
                continue

            # Confidence is a hard requirement of the prompt. In the v3
            # precision dry-run every LLM response included one — if a
            # response omits it, that's a signal the model glitched, NOT a
            # signal we should manufacture a moderate-confidence number.
            # Previously we fell back to 75, which placed unconfident
            # matches squarely in the "covered as a clear secondary topic
            # with substantive detail" band of our own rubric. That was an
            # unsupported claim. Now we drop the match instead.
            if confidence is None:
                logger.warning(
                    "narrative_frames.match_article: item=%d frame=%d '%s' "
                    "rejected — LLM omitted confidence field",
                    item.id, frame.id, frame.name,
                )
                continue
            effective_conf = confidence
            logger.info(
                "narrative_frames.match_article: item=%d frame=%d '%s' confidence=%d",
                item.id, frame.id, frame.name, confidence,
            )

            # Cluster-native: UPSERT a FrameClusterMatch keyed on the item's
            # cluster. This is what powers all the read-side counts
            # (dashboard, briefing, detail page — see services/frame_counts).
            if item.story_cluster_id:
                from app.services import cluster_writes
                cluster_writes.upsert_frame_match(
                    db,
                    frame_id=frame.id,
                    cluster_id=item.story_cluster_id,
                    confidence=effective_conf,
                    source_type="cluster_runtime",
                    matched_by="llm",
                    article_date=item.published_at,
                    frame_content_hash=_frame_content_hash(frame),
                )

            # Phase-D-gap fix (A2): also write a NarrativeFrameMention so
            # the per-article snippet and confidence are available to:
            #   - frame_variants clustering (reads NFM.extracted_text)
            #   - detail page NOTABLE QUOTES (reads NFM.extracted_text)
            #   - audit / repair maintenance jobs
            # Earlier Phase D removed this write because FCM has no per-article
            # snippet column; we kept the snippet in NFM as the right place.
            # `verified` is the validated verbatim quote from the body.
            existing_nfm = (
                db.query(NarrativeFrameMention)
                .filter(
                    NarrativeFrameMention.frame_id == frame.id,
                    NarrativeFrameMention.source_item_id == item.id,
                )
                .first()
            )
            if existing_nfm:
                existing_nfm.confidence = effective_conf
                existing_nfm.extracted_text = verified
                existing_nfm.matched_by = "llm"
            else:
                db.add(NarrativeFrameMention(
                    frame_id=frame.id,
                    source_item_id=item.id,
                    confidence=effective_conf,
                    matched_by="llm",
                    extracted_text=verified,
                    # claim_meta is rescore-only (per-claim metadata) — the
                    # runtime matcher prompt doesn't extract those fields.
                    claim_meta=None,
                ))

            matched_frame_ids.append(frame.id)

        db.commit()
        return matched_frame_ids

    except Exception as e:
        # B3: log the full traceback so matcher failures don't disappear
        # silently. Caller still gets [] so a single broken article doesn't
        # break the ingest loop.
        logger.exception(
            "narrative_frames.match_article: item=%d failed: %s", item.id, e,
        )
        return []


def rematch_recent(db: Session, days_back: int = 7) -> int:
    """Lightweight rematch: re-score recent articles against current frames.

    Used by the hands-off auto-promote workflow. After auto-execute creates
    new tracked frames, those frames start at "0 this week" because no
    recent articles have been scored against them yet (the article scorer
    runs at ingest time, against whatever frames existed then). This
    helper sweeps the last N days of articles and re-runs the scorer.

    Differences from `rematch_all`:
      - Does NOT prune empty frames (the new frames already have some
        mentions from promote_cluster's backfill).
      - Does NOT use the global _rematch_lock (this is a small targeted
        pass that runs after auto-promote, not a hours-long full rescore).
      - Default 7-day window — fast (~$0.50 in LLM cost on typical volume).

    Returns the number of new mentions/cluster-matches created.
    """
    import time
    from sqlalchemy import func
    from app.models import FrameClusterMatch, StoryCluster

    delay = float(os.environ.get("REMATCH_DELAY_SECONDS", "0.1"))
    cutoff = datetime.utcnow() - timedelta(days=days_back)

    # Cluster-native iteration like rematch_all — score one representative
    # article per story cluster instead of every article. Much cheaper.
    candidate_clusters = (
        db.query(StoryCluster, SourceItem)
        .join(SourceItem, SourceItem.id == StoryCluster.representative_source_item_id)
        .filter(
            StoryCluster.last_seen_at >= cutoff,
            SourceItem.archived_as_irrelevant == False,
        )
        .all()
    )

    total = 0
    for _, item in candidate_clusters:
        try:
            matched = match_article_to_frames(db, item)
            total += len(matched)
        except Exception as exc:
            logger.warning(
                "rematch_recent: item=%d failed: %s", item.id, exc,
            )
        if delay > 0:
            time.sleep(delay)
    logger.info(
        "rematch_recent: scored %d recent clusters, %d frame matches created",
        len(candidate_clusters), total,
    )
    return total


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

    # Configurable via env — was 2.5s tuned for free Groq tier (4-key
    # round-robin). With OpenAI tier 2 we have 5K RPM headroom; a 0.1s
    # courtesy delay between calls is plenty.
    delay = float(os.environ.get("REMATCH_DELAY_SECONDS", "0.1"))

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
        _rematch_progress["started_at"] = datetime.utcnow().isoformat()

        # Parallelize: LLM/embedding calls are I/O-bound, so threads are fine.
        # Each worker gets its own DB session so SQLAlchemy isn't shared.
        # Worker count tunable via env; default 8 is comfortably below OpenAI
        # tier 2's 5K RPM and gpt-4o-mini's per-key limits.
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from app.db import SessionLocal

        workers = int(os.environ.get("REMATCH_PARALLEL_WORKERS", "8"))
        logger.info("rematch_all: using %d worker threads", workers)

        item_ids = [it.id for it in items]
        total = 0
        completed = 0

        def _worker(item_id: int) -> int:
            with SessionLocal() as worker_db:
                worker_item = worker_db.get(SourceItem, item_id)
                if worker_item is None:
                    return 0
                try:
                    matched = match_article_to_frames(worker_db, worker_item)
                    return len(matched)
                except Exception as exc:
                    logger.warning("rematch_all: item=%d failed: %s", item_id, exc)
                    return 0

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_worker, iid): iid for iid in item_ids}
            for fut in as_completed(futures):
                total += fut.result()
                completed += 1
                _rematch_progress["done"] = completed
                # Light pacing — only matters if the worker pool is much
                # larger than the API's TPM headroom. With 8 workers and
                # OpenAI tier 2 we don't need it, but keep the knob.
                if delay > 0 and workers == 1:
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
                # Cascade-aware delete — prevents the orphan-variant /
                # orphan-candidate_frame patterns we found in tonight's audit.
                from app.services.safe_deletes import safe_delete_frame
                safe_delete_frame(db, frame.id)
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

        from app.services.safe_deletes import safe_delete_frame
        for dup in to_delete:
            mention_count = counts.get(dup.id, 0)
            # Cascade-aware delete: FCM + NFM + FrameVariant + FrameStageHistory
            # all get cleaned, and CandidateFrame.resolved_to_frame_id pointers
            # get SET NULL. The previous code only deleted FCM + the frame
            # itself, leaving FrameVariant / FrameStageHistory / CandidateFrame
            # as orphans (the 13 + 4 orphans found in tonight's audit
            # originated here on the daily run).
            safe_delete_frame(db, dup.id)
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


_REFUSAL_MARKERS = (
    "no information provided",
    "not enough information",
    "no context provided",
    "no content provided",
    "no summary available",
    "cannot create a summary",
    "unable to create a summary",
    "no article text",
    "no text was provided",
    "i don't have enough",
    "i do not have enough",
    # Added after seeing these surface as "Notable Quotes" on the detail page.
    # NOTE: "but the article does not contain direct mention of X" is a
    # LEGITIMATE informative summary, not a refusal — so we don't match on
    # the generic substring "does not contain".
    "no one-sentence summary",
    "does not contain a sentence",
    "no relevant information",
)


_SHORT_REFUSAL_PHRASES = {
    "no summary available.",
    "no summary available",
    "no summary.",
    "no summary",
    "n/a",
    "no content.",
    "no content",
    "no information.",
    "no information",
    # Short LLM refusal verbatims (under the 40-char substring-match floor):
    "i do not have enough.",
    "i do not have enough info.",
    "i do not have enough information.",
    "i don't have enough.",
    "i don't have enough info.",
    "not enough info.",
    "unable to summarize.",
    "cannot summarize.",
}


def is_refusal_summary(s: str | None) -> bool:
    """True when a stored summary looks like an LLM refusal for empty input
    (paywalled / unextractable article). These should be hidden in quotes
    and ideally nulled in the DB so they get re-summarized later."""
    s_norm = (s or "").strip().lower()
    if not s_norm:
        return False
    # Exact-match short refusals — these are below the substring-match floor
    # but are unambiguous as a whole-string.
    if s_norm.rstrip(".") in {p.rstrip(".") for p in _SHORT_REFUSAL_PHRASES}:
        return True
    # For longer summaries, look for known refusal substrings. The 40-char
    # floor prevents false positives on short legitimate summaries.
    if len(s_norm) < 40:
        return False
    return any(m in s_norm for m in _REFUSAL_MARKERS)


def clear_refusal_summaries(db: Session) -> dict:
    """Null out source_items.summary rows that are LLM refusals. After this
    runs, the next rescore/re-summary pass will regenerate them from the
    (now possibly extracted) article text — or skip them if still empty,
    which is cleaner than carrying a fake summary forever.

    Returns counts: how many were checked, how many cleared.
    """
    from app.models import SourceItem

    items = (
        db.query(SourceItem)
        .filter(SourceItem.summary.isnot(None), SourceItem.summary != "")
        .all()
    )
    cleared = 0
    for it in items:
        if is_refusal_summary(it.summary):
            it.summary = None
            cleared += 1
    if cleared:
        db.commit()
    return {"checked": len(items), "cleared": cleared}


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
    # Single-transaction repair: previously 3 sequential commits would
    # leave partial state if any midway step failed. Now everything
    # happens in one BEGIN/COMMIT — either all changes land or none do.
    desc_fixed = 0
    mentions_fixed = 0
    false_positives = 0
    try:
        # Pass 1: clean leaked-prompt-scaffolding from descriptions
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

        # Pass 2: null unverifiable extracted_text
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

        # Pass 3: remove false-positive matches
        ctx = _campaign_context(db)
        all_frames = {f.id: f for f in db.query(NarrativeFrame).all()}
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
            if (item.race_relevance_score or 0) < 55:
                db.delete(mention)
                false_positives += 1
                continue
            if not _article_passes_keyword_gate(item, frame.name, frame.description or "", ctx):
                db.delete(mention)
                false_positives += 1

        # Single commit at the end — atomic for the whole repair pass.
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("repair_frame_data: failed mid-pass, rolled back all changes")
        raise

    logger.info(
        "repair_frame_data: fixed %d frame descriptions, nulled %d unverifiable quotes, removed %d false-positive matches",
        desc_fixed, mentions_fixed, false_positives,
    )
    return {"descriptions_fixed": desc_fixed, "quotes_nulled": mentions_fixed, "false_positives_removed": false_positives}


# ── Reach weighting ──────────────────────────────────────────────────────────
# Articles are weighted by their outlet's reach so a wire story carried by
# major papers outweighs a burst of low-authority blogs. We use audience size
# when known, else fall back to a curated authority score (1-10 scale).
REACH_PER_MONTHLY_VISITOR = 0.003     # weight per monthly visitor
REACH_DEFAULT_OUTLET_AUTHORITY = 5    # midpoint of the 1-10 scale
REACH_AUTHORITY_SCALE = 10.0          # divisor → normalizes to ~0-1


# ── Stage classification ─────────────────────────────────────────────────────
# These thresholds split into two groups:
#   1. Universal constants — campaign-size-invariant. Same for a presidential
#      race or a city council seat: ratios and time windows.
#   2. Volume thresholds — DERIVED from each campaign's own data (see
#      _compute_stage_scale below). This is what lets the tool work at any
#      scale: a frame that would be "high volume" in a House race might be
#      noise in a presidential, so we compare each frame against the campaign's
#      own median, not a hardcoded number.

# Universal — same for any campaign.
STAGE_DORMANT_DAYS = 14        # no activity in this many days → dormant
STAGE_SPREADING_RATIO = 2.0    # this_week must be ≥ baseline × this to spread
STAGE_FADING_RATIO = 0.5       # this_week must be < baseline × this to fade

# Safe defaults used when the campaign has no data yet. These are floors;
# derived per-campaign values only ever exceed them. The baseline_floor is
# the statistical sample-size minimum — 3 is the conventional "enough events
# to be more than noise" threshold, and it stays constant at any scale.
_DEFAULT_STAGE_SCALE = {
    "emerging_total_max": 5,
    "resurfacing_total_min": 5,
    "mainstream_total_min": 20,
    "this_week_floor": 3,
    "baseline_floor": 3,
}


def _compute_stage_scale(frame_data: list[tuple[int, int]]) -> dict:
    """Return per-campaign volume thresholds derived from current data.

    `frame_data` is a list of (article_total, articles_this_week) per active
    frame. Thresholds anchor to percentiles of the campaign's own distribution
    so the tool generalizes across race sizes:
      • emerging_total_max    = bottom-quartile total (still building)
      • resurfacing_total_min = same — a resurfacing frame just has to be
                                past the "emerging" stage
      • mainstream_total_min  = median total (top half is mainstream-eligible)
      • this_week_floor       = half the median weekly (noise filter for spikes)
      • baseline_floor        = constant 3 (statistical sample-size minimum —
                                independent of campaign scale)
    """
    if not frame_data:
        return dict(_DEFAULT_STAGE_SCALE)

    totals = sorted(t for t, _ in frame_data)
    weeks = sorted(w for _, w in frame_data)
    n = len(totals)

    def pct(arr: list[int], p: float) -> int:
        return arr[min(n - 1, max(0, int(p * (n - 1))))]

    median_total = pct(totals, 0.5)
    q1_total = pct(totals, 0.25)
    median_weekly = pct(weeks, 0.5)

    return {
        "emerging_total_max": max(_DEFAULT_STAGE_SCALE["emerging_total_max"], q1_total),
        "resurfacing_total_min": max(_DEFAULT_STAGE_SCALE["resurfacing_total_min"], q1_total),
        "mainstream_total_min": max(_DEFAULT_STAGE_SCALE["mainstream_total_min"], median_total),
        "this_week_floor": max(_DEFAULT_STAGE_SCALE["this_week_floor"], median_weekly // 2),
        "baseline_floor": _DEFAULT_STAGE_SCALE["baseline_floor"],
    }


def _narrative_stage(
    total: int,
    this_week: int,
    baseline_weekly: int,
    days_since_last: float | None,
    scale: dict,
) -> str:
    """Classify a frame's lifecycle stage from article-level counts.

    All volume comparisons use `scale` (derived per-campaign), so the same
    logic produces sensible classifications for any race size. The exception
    is dormant: 14 days of silence means silence at any scale.
    """
    if total == 0 or days_since_last is None or days_since_last > STAGE_DORMANT_DAYS:
        return "dormant"
    if total <= scale["emerging_total_max"]:
        return "emerging"
    # Resurfacing: established frame, currently active, but flatlined for the
    # past 3+ weeks — distinct campaign-intel signal vs a normal surge. Uses
    # a softer this-week threshold than spreading: a quiet frame just waking
    # up to modest activity is the signal, not a huge surge.
    if (total >= scale["resurfacing_total_min"]
        and this_week >= max(2, scale["this_week_floor"] // 2)
        and baseline_weekly == 0):
        return "resurfacing"
    # Spreading: real this-week volume AND a clear surge over a non-trivial baseline.
    if (this_week >= scale["this_week_floor"]
        and baseline_weekly >= scale["baseline_floor"]
        and this_week >= baseline_weekly * STAGE_SPREADING_RATIO):
        return "spreading"
    # Fading: a frame with established weekly coverage has dropped off.
    if (baseline_weekly >= scale["baseline_floor"] * 2
        and this_week < baseline_weekly * STAGE_FADING_RATIO):
        return "fading"
    if total >= scale["mainstream_total_min"]:
        return "mainstream"
    return "active"


def get_frames_with_counts(db: Session) -> list[dict]:
    """Return all active frames with cluster counts and weighted reach.

    Cluster-native (Phase C). Field names on the response stay the same
    (`mentions_this_week`, etc.) so the frontend keeps working — but the
    numbers now count distinct story clusters, not raw article mentions, so
    wire syndication doesn't inflate them.

    V13.21 — also includes `subject_type` (who the frame is ABOUT —
    candidate/opponent/media) so frontend can render the 4-quadrant
    color scheme consistent with the landscape. Computed from frame
    name via the shared subject_classifier; no schema change.
    """
    from collections import defaultdict

    from sqlalchemy import and_, case, cast, func, Numeric
    from app.models import FrameClusterMatch, Outlet, StoryCluster
    from app.services.frame_counts import frame_pulse_counts, week_window
    from app.services.subject_classifier import get_subject_classifier
    classify_subject = get_subject_classifier(db)

    week_start, prev_week_start, now = week_window()

    # Reach weighting — see REACH_* constants near the top of this file.
    reach_weight = case(
        (Outlet.monthly_visitors.isnot(None), Outlet.monthly_visitors * REACH_PER_MONTHLY_VISITOR),
        else_=func.coalesce(Outlet.authority_score, REACH_DEFAULT_OUTLET_AUTHORITY) / REACH_AUTHORITY_SCALE,
    )

    frames = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).all()
    if not frames:
        return []

    # Canonical "this week" / "last week" counts (Option C — distinct
    # clusters with any article in the window). One GROUP BY query for all
    # frames; see services/frame_counts.py for the definition.
    pulse = frame_pulse_counts(db, [f.id for f in frames])

    # This was an N+1: ~17 queries per frame. It is now a fixed set of batch
    # queries grouped by frame_id, regardless of how many frames exist.

    # ---- Query 1: every FrameClusterMatch row, plus the authority score of
    # its cluster's representative article's outlet. One row per match — drives
    # counts, first/last-seen, and the first/peak/latest "key" clusters.
    fcm_rows = (
        db.query(
            FrameClusterMatch.frame_id,
            FrameClusterMatch.story_cluster_id,
            FrameClusterMatch.first_seen_at,
            FrameClusterMatch.last_seen_at,
            Outlet.authority_score.label("rep_authority"),
        )
        .select_from(FrameClusterMatch)
        .outerjoin(StoryCluster, StoryCluster.id == FrameClusterMatch.story_cluster_id)
        .outerjoin(SourceItem, SourceItem.id == StoryCluster.representative_source_item_id)
        .outerjoin(Outlet, Outlet.id == SourceItem.outlet_id)
        .all()
    )

    # ---- Query 2: weighted reach per frame for 3 windows, summed over every
    # member article of every matched cluster (intentionally not deduped).
    reach_rows = (
        db.query(
            FrameClusterMatch.frame_id,
            # round(numeric, int) — cast to Numeric so this works on both
            # SQLite (which doesn't care about the type) AND Postgres
            # (where round(double, int) doesn't exist).
            func.round(cast(func.sum(
                case((SourceItem.published_at >= week_start, reach_weight))),
                Numeric), 1),
            func.round(cast(func.sum(
                case((and_(SourceItem.published_at >= prev_week_start,
                           SourceItem.published_at < week_start), reach_weight))),
                Numeric), 1),
            func.round(cast(func.sum(reach_weight), Numeric), 1),
        )
        .select_from(FrameClusterMatch)
        .join(StoryCluster, StoryCluster.id == FrameClusterMatch.story_cluster_id)
        .join(SourceItem, SourceItem.story_cluster_id == StoryCluster.id)
        .outerjoin(Outlet, Outlet.id == SourceItem.outlet_id)
        .group_by(FrameClusterMatch.frame_id)
        .all()
    )
    reach_by_frame = {
        fid: (float(tw or 0), float(lw or 0), float(tot or 0))
        for fid, tw, lw, tot in reach_rows
    }

    # ---- Query 3: distinct outlets per frame, this week and last week.
    outlet_rows = (
        db.query(
            FrameClusterMatch.frame_id,
            func.count(func.distinct(
                case((SourceItem.published_at >= week_start, SourceItem.outlet_id)))),
            func.count(func.distinct(
                case((and_(SourceItem.published_at >= prev_week_start,
                           SourceItem.published_at < week_start), SourceItem.outlet_id)))),
        )
        .select_from(FrameClusterMatch)
        .join(StoryCluster, StoryCluster.id == FrameClusterMatch.story_cluster_id)
        .join(SourceItem, SourceItem.story_cluster_id == StoryCluster.id)
        .filter(SourceItem.outlet_id.isnot(None))
        .group_by(FrameClusterMatch.frame_id)
        .all()
    )
    outlets_by_frame = {fid: (tw or 0, lw or 0) for fid, tw, lw in outlet_rows}

    # ---- Query 4: distinct active publish-days in the last 7, per frame.
    days_rows = (
        db.query(
            FrameClusterMatch.frame_id,
            func.count(func.distinct(func.date(SourceItem.published_at))),
        )
        .select_from(FrameClusterMatch)
        .join(StoryCluster, StoryCluster.id == FrameClusterMatch.story_cluster_id)
        .join(SourceItem, SourceItem.story_cluster_id == StoryCluster.id)
        .filter(SourceItem.published_at >= week_start,
                SourceItem.published_at.isnot(None))
        .group_by(FrameClusterMatch.frame_id)
        .all()
    )
    days_active_by_frame = {fid: (n or 0) for fid, n in days_rows}

    # ---- Query 4b: article-level publish-date counts per frame, used only for
    # stage classification. We count this_week and last_30_days (not last_week)
    # — comparing to a 30-day rolling baseline smooths out the noise of a
    # single sparse previous week and avoids the cluster-density artifact
    # where every recent week looks like a flood. The cluster-based mentions_*
    # in the response are unchanged.
    baseline_start = now - timedelta(days=30)
    article_rows = (
        db.query(
            FrameClusterMatch.frame_id,
            func.count(func.distinct(
                case((SourceItem.published_at >= week_start, SourceItem.id)))),
            func.count(func.distinct(
                case((SourceItem.published_at >= baseline_start, SourceItem.id)))),
            func.count(func.distinct(SourceItem.id)),
            func.max(SourceItem.published_at),
        )
        .select_from(FrameClusterMatch)
        .join(StoryCluster, StoryCluster.id == FrameClusterMatch.story_cluster_id)
        .join(SourceItem, SourceItem.story_cluster_id == StoryCluster.id)
        .filter(SourceItem.published_at.isnot(None))
        .group_by(FrameClusterMatch.frame_id)
        .all()
    )
    articles_by_frame = {
        fid: (tw or 0, last_30d or 0, tot or 0, last_pub)
        for fid, tw, last_30d, tot, last_pub in article_rows
    }
    # Stage classification adapts to each campaign's article volume — see
    # _compute_stage_scale. Computed once from the current (total, this_week)
    # distribution and reused for every frame.
    stage_scale = _compute_stage_scale(
        [(tot, tw) for tw, _, tot, _ in articles_by_frame.values()]
    )

    # ---- Query 4c: 30-day per-day article counts per frame, broken down by
    # outlet tier. Powers the always-visible sparkline AND the cross-tier
    # transition detectors in lib/featuredFrame.ts (Phase 2.3) — e.g.
    # "Crossed into national coverage" requires knowing that national
    # counts spiked in the last 3 days while being zero before. The
    # backwards-compat `count` field is kept equal to `total` so existing
    # consumers don't break.
    sparkline_cutoff = now - timedelta(days=30)
    sparkline_rows = (
        db.query(
            FrameClusterMatch.frame_id,
            func.date(SourceItem.published_at).label("d"),
            Outlet.outlet_type,
            func.count(func.distinct(SourceItem.id)),
        )
        .select_from(FrameClusterMatch)
        .join(StoryCluster, StoryCluster.id == FrameClusterMatch.story_cluster_id)
        .join(SourceItem, SourceItem.story_cluster_id == StoryCluster.id)
        # Outerjoin so articles whose outlet_id is NULL still contribute to
        # the `total` and `unknown` buckets — losing them would skew the
        # sparkline downward for frames that ingest from non-outlet sources.
        .outerjoin(Outlet, Outlet.id == SourceItem.outlet_id)
        .filter(
            SourceItem.published_at >= sparkline_cutoff,
            SourceItem.published_at.isnot(None),
        )
        .group_by(FrameClusterMatch.frame_id, "d", Outlet.outlet_type)
        .all()
    )

    def _empty_bucket() -> dict:
        return {
            "total": 0,
            "national": 0,
            "regional": 0,
            "local": 0,
            "blog": 0,
            "social": 0,
            "unknown": 0,
        }

    _by_frame_date: dict = defaultdict(lambda: defaultdict(_empty_bucket))
    for fid, d, outlet_type, c in sparkline_rows:
        b = _by_frame_date[fid][str(d)]
        c_int = int(c or 0)
        b["total"] += c_int
        if outlet_type in ("national", "broadcast"):
            b["national"] += c_int
        elif outlet_type == "regional_news":
            b["regional"] += c_int
        elif outlet_type == "local_news":
            b["local"] += c_int
        elif outlet_type == "blog":
            b["blog"] += c_int
        elif outlet_type == "social":
            b["social"] += c_int
        else:
            b["unknown"] += c_int

    # Densify into a 30-day window with zero-filled gaps. The frontend's
    # cross-tier detectors (lib/featuredFrame.ts) rely on
    # `activity.slice(-3)` meaning "last 3 calendar days" — a sparse array
    # would give them "last 3 days that happened to have articles," which
    # could span weeks. Densifying also gives the sparkline an honest
    # shape: a frame with a one-day spike then silence reads as a spike,
    # not as a flat line of 1 evenly-spaced point.
    sparkline_window_start = (now - timedelta(days=29)).date()
    sparkline_window: list = [
        (sparkline_window_start + timedelta(days=i)) for i in range(30)
    ]
    sparkline_by_frame: dict = {}
    for fid, by_date in _by_frame_date.items():
        rows = []
        for d in sparkline_window:
            bucket = by_date.get(str(d)) or _empty_bucket()
            rows.append({"date": str(d), "count": bucket["total"], **bucket})
        sparkline_by_frame[fid] = rows

    # ---- Query 5: outlet-tier breakdown per frame.
    tier_rows = (
        db.query(
            FrameClusterMatch.frame_id,
            Outlet.outlet_type,
            func.count(func.distinct(Outlet.id)),
        )
        .select_from(FrameClusterMatch)
        .join(StoryCluster, StoryCluster.id == FrameClusterMatch.story_cluster_id)
        .join(SourceItem, SourceItem.story_cluster_id == StoryCluster.id)
        .join(Outlet, Outlet.id == SourceItem.outlet_id)
        .group_by(FrameClusterMatch.frame_id, Outlet.outlet_type)
        .all()
    )
    tiers_by_frame: dict = {}
    for fid, outlet_type, count in tier_rows:
        tiers = tiers_by_frame.setdefault(
            fid, {"national": 0, "regional": 0, "local": 0, "blog": 0, "social": 0})
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

    # ---- Query 5b: dashboard featured-card appearances in last 7 days.
    # Feeds the saturation-penalty term in the frontend's multi-objective
    # score (lib/featuredFrame.ts). Frames that have been featured 3+ days
    # running get demoted unless their urgency stays high — prevents the
    # panel from becoming wallpaper.
    from app.models import FeaturedAppearance
    from datetime import date as _date, timedelta as _timedelta
    _seven_days_ago = _date.today() - _timedelta(days=6)
    featured_rows = (
        db.query(
            FeaturedAppearance.frame_id,
            func.count(FeaturedAppearance.id),
        )
        .filter(FeaturedAppearance.appeared_on >= _seven_days_ago)
        .group_by(FeaturedAppearance.frame_id)
        .all()
    )
    featured_by_frame: dict = {fid: int(c) for fid, c in featured_rows}

    # ---- Group the FCM rows per frame and resolve the first/peak/latest
    # "key" clusters in Python (one pass, no per-frame queries).
    fcm_by_frame: dict = defaultdict(list)
    for row in fcm_rows:
        fcm_by_frame[row.frame_id].append(row)

    frame_key_clusters: dict = {}
    needed_cluster_ids: set = set()
    for fid, rows in fcm_by_frame.items():
        first_row = min(rows, key=lambda r: r.first_seen_at or datetime.max)
        latest_row = max(rows, key=lambda r: r.last_seen_at or datetime.min)
        # Peak day = the calendar day with the most matches; on that day pick
        # the cluster whose representative article has the highest-authority
        # outlet (None authority sorts last).
        by_day: dict = defaultdict(list)
        for r in rows:
            if r.first_seen_at:
                by_day[r.first_seen_at.date()].append(r)
        peak_cluster_id = None
        if by_day:
            _peak_day, peak_rows = max(by_day.items(), key=lambda kv: len(kv[1]))
            best = max(peak_rows,
                       key=lambda r: r.rep_authority if r.rep_authority is not None else -1)
            peak_cluster_id = best.story_cluster_id
        ordered = [
            ("First mention", first_row.story_cluster_id),
            ("Peak day", peak_cluster_id),
            ("Latest", latest_row.story_cluster_id),
        ]
        frame_key_clusters[fid] = ordered
        for _role, cid in ordered:
            if cid:
                needed_cluster_ids.add(cid)

    # ---- Query 6 + 7: bulk-hydrate the key clusters and their representative
    # articles (two queries total, not two per cluster).
    clusters_by_id: dict = {}
    reps_by_id: dict = {}
    if needed_cluster_ids:
        for c in (db.query(StoryCluster)
                  .filter(StoryCluster.id.in_(needed_cluster_ids)).all()):
            clusters_by_id[c.id] = c
        rep_ids = {c.representative_source_item_id for c in clusters_by_id.values()
                   if c.representative_source_item_id}
        if rep_ids:
            for r in db.query(SourceItem).filter(SourceItem.id.in_(rep_ids)).all():
                reps_by_id[r.id] = r

    # ---- Assemble the response (pure Python, no further queries).
    result = []
    for frame in frames:
        rows = fcm_by_frame.get(frame.id, [])
        # Counts come from frame_pulse_counts (canonical Option-C definition:
        # distinct clusters with any article published in the window). The
        # previous logic counted FCM rows by their first_seen_at, which under-
        # reported recent activity on clusters whose match arrived earlier.
        this_week, last_week, total = pulse.get(frame.id, (0, 0, 0))

        first_seen = min(
            (r.first_seen_at for r in rows
             if r.first_seen_at and r.first_seen_at >= _PLAUSIBLE_DATE_FLOOR),
            default=None,
        )
        last_seen = max(
            (r.last_seen_at for r in rows
             if r.last_seen_at and r.last_seen_at >= _PLAUSIBLE_DATE_FLOOR),
            default=None,
        )
        days_since = (now - last_seen).days if last_seen else None

        # Stage classification uses article-level counts with a 30-day baseline
        # to avoid the clustering-density and one-sparse-prior-week artifacts
        # that made every frame look like it was spreading. The displayed
        # mentions_* and trend stay cluster-based so the card metrics remain
        # wire-syndication-deduped.
        art_tw, art_last_30d, art_total, last_pub = articles_by_frame.get(
            frame.id, (0, 0, 0, None))
        # Baseline = the 23 days BEFORE this week, projected to a weekly rate.
        # When art_last_30d == art_tw (all activity is recent, no prior baseline),
        # use >= not > so we don't force baseline_weekly=0 — which would falsely
        # signal "explosive growth from nothing." For brand-new frames whose
        # entire history fits in this week, baseline_weekly stays 0 (correct:
        # no baseline exists). For established frames where the 30d window just
        # happens to equal this-week (rare), the formula above gives 0 anyway,
        # which is still correct.
        baseline_weekly = int(round((art_last_30d - art_tw) / 23 * 7)) if art_last_30d >= art_tw else 0
        days_since_article = (now - last_pub).days if last_pub else None
        stage = _narrative_stage(art_total, art_tw, baseline_weekly, days_since_article, stage_scale)

        # Stage transition detection — only writes when the stage actually
        # changes vs the last recorded value. First observation (NULL → stage)
        # also gets logged so we have a full history.
        if frame.last_known_stage != stage:
            try:
                import json as _json
                from app.models import FrameStageHistory
                db.add(FrameStageHistory(
                    frame_id=frame.id,
                    from_stage=frame.last_known_stage,
                    to_stage=stage,
                    transitioned_at=now,
                    metrics_snapshot=_json.dumps({
                        "art_total": art_total,
                        "art_this_week": art_tw,
                        "art_last_30d": art_last_30d,
                        "baseline_weekly": baseline_weekly,
                        "days_since_article": days_since_article,
                    }),
                ))
                frame.last_known_stage = stage
                frame.last_stage_check_at = now
                # Commit immediately so transitions are recorded even if a
                # later frame in this loop raises. Cheap — single tiny insert.
                db.commit()
            except Exception as exc:
                logger.warning("frame_stage_history: log failed for frame %d: %s", frame.id, exc)
                db.rollback()

        reach_this_week, reach_last_week, reach_total = \
            reach_by_frame.get(frame.id, (0.0, 0.0, 0.0))
        unique_outlets_this_week, unique_outlets_last_week = \
            outlets_by_frame.get(frame.id, (0, 0))
        days_active_last_7 = days_active_by_frame.get(frame.id, 0)
        tiers = tiers_by_frame.get(
            frame.id, {"national": 0, "regional": 0, "local": 0, "blog": 0, "social": 0})

        # Key articles — first / peak / latest, deduped by cluster.
        key_articles: list = []
        seen_cluster_ids: set = set()
        for role, cid in frame_key_clusters.get(frame.id, []):
            if not cid or cid in seen_cluster_ids:
                continue
            seen_cluster_ids.add(cid)
            cluster = clusters_by_id.get(cid)
            if not cluster:
                continue
            rep = reps_by_id.get(cluster.representative_source_item_id)
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
                "extracted_text": None,
            })

        result.append({
            "id": frame.id,
            "name": frame.name,
            "description": frame.description,
            "owner_type": frame.owner_type,
            # Stored value (user-picked quadrant) wins over heuristic. NULL
            # in the column = no explicit choice → fall back to name-based
            # inference. See subject_classifier.py for the heuristic logic.
            "subject_type": frame.subject_type or classify_subject(frame.name),
            "source": frame.source,
            "created_at": frame.created_at.isoformat() if frame.created_at else None,
            "mentions_this_week": this_week,
            "mentions_last_week": last_week,
            "mentions_total": total,
            "articles_last_30d": art_last_30d,
            "activity_30d": sparkline_by_frame.get(frame.id, []),
            "unique_outlets_this_week": unique_outlets_this_week,
            "unique_outlets_last_week": unique_outlets_last_week,
            "days_active_last_7": days_active_last_7,
            "reach_this_week": reach_this_week,
            "reach_last_week": reach_last_week,
            "reach_total": reach_total,
            "stage": stage,
            "outlet_tiers": tiers,
            "first_seen_at": first_seen.isoformat() if first_seen else None,
            "last_seen_at": last_seen.isoformat() if last_seen else None,
            "key_articles": key_articles,
            # Saturation signal — how many of the last 7 days this frame
            # has been featured on the dashboard. Drives the homepage-
            # decay penalty in lib/featuredFrame.ts.
            "days_featured_last_7": featured_by_frame.get(frame.id, 0),
            # Momentum signal from services/frame_momentum.py. One of
            # viral / missing_coverage / elite_only / stable / no_trend_signal,
            # or None for frames below MIN_ACTIVE_ARTICLES. momentum_data is
            # the raw classifier inputs (parsed from the stored JSON) so the
            # UI can build tooltips like "13 articles this week vs baseline 1/week".
            "momentum_signal": frame.momentum_signal,
            "momentum_data": _parse_momentum_data(frame.momentum_data),
            # Strategic interpretation layer — converts the (signal, owner_type)
            # pair into a posture/action/urgency triple via the matrix in
            # services/strategic_lens.py. None when either input is missing
            # or the combination isn't recognized — UI hides the chip cleanly.
            "strategic_lens": _compute_strategic_lens(frame),
        })

    result.sort(key=lambda x: x["mentions_this_week"], reverse=True)
    return result


def get_frame_timeline(
    db: "Session", frame_id: int, days_back: int = 90,
) -> Optional[dict]:
    """Variant-level mention timeline for a frame.

    For each FrameVariant of the given frame, returns daily mention counts
    over the past `days_back` days based on `source_items.published_at`.

    Also returns per-day frame totals (sum across variants + un-clustered)
    so the UI can overlay the frame's overall trajectory.

    Returns None if the frame doesn't exist.
    """
    from app.models import NarrativeFrame, NarrativeFrameMention, FrameVariant, SourceItem
    from sqlalchemy import func, and_

    frame = db.query(NarrativeFrame).filter_by(id=frame_id).first()
    if not frame:
        return None

    cutoff = datetime.utcnow() - timedelta(days=days_back)

    # ── Per-variant daily counts ───────────────────────────────────────────────
    # Join: NarrativeFrameMention → SourceItem (for the date)
    variant_rows = (
        db.query(
            NarrativeFrameMention.variant_id,
            func.date(SourceItem.published_at).label("day"),
            func.count(NarrativeFrameMention.id).label("count"),
        )
        .join(SourceItem, SourceItem.id == NarrativeFrameMention.source_item_id)
        .filter(
            NarrativeFrameMention.frame_id == frame_id,
            NarrativeFrameMention.variant_id.isnot(None),
            SourceItem.published_at >= cutoff,
            SourceItem.published_at.isnot(None),
        )
        .group_by(NarrativeFrameMention.variant_id, func.date(SourceItem.published_at))
        .all()
    )
    # Organize by variant_id → list of {date, count}
    by_variant: dict[int, list[dict]] = {}
    for variant_id, day, count in variant_rows:
        by_variant.setdefault(variant_id, []).append({"date": str(day), "count": int(count)})
    for v_id in by_variant:
        by_variant[v_id].sort(key=lambda x: x["date"])

    # ── Variant metadata ──────────────────────────────────────────────────────
    variant_meta = {
        v.id: v for v in
        db.query(FrameVariant).filter(FrameVariant.frame_id == frame_id).all()
    }

    variants_out = []
    for v_id, meta in variant_meta.items():
        variants_out.append({
            "id": v_id,
            "name": meta.name,
            "first_seen_at": meta.first_seen_at.isoformat() if meta.first_seen_at else None,
            "last_seen_at": meta.last_seen_at.isoformat() if meta.last_seen_at else None,
            "mention_count": meta.mention_count,
            "daily_counts": by_variant.get(v_id, []),
        })
    # Sort by total mentions desc (biggest variants first)
    variants_out.sort(key=lambda x: -x["mention_count"])

    # ── Frame-wide daily totals (including un-clustered mentions) ─────────────
    # This is mentions for the whole frame regardless of variant assignment.
    total_rows = (
        db.query(
            func.date(SourceItem.published_at).label("day"),
            func.count(NarrativeFrameMention.id).label("count"),
        )
        .join(SourceItem, SourceItem.id == NarrativeFrameMention.source_item_id)
        .filter(
            NarrativeFrameMention.frame_id == frame_id,
            SourceItem.published_at >= cutoff,
            SourceItem.published_at.isnot(None),
        )
        .group_by(func.date(SourceItem.published_at))
        .order_by(func.date(SourceItem.published_at))
        .all()
    )
    totals_by_day = [
        {"date": str(day), "count": int(count)}
        for day, count in total_rows
    ]

    # ── Un-clustered mentions (no variant_id yet) ─────────────────────────────
    unclustered_count = (
        db.query(func.count(NarrativeFrameMention.id))
        .join(SourceItem, SourceItem.id == NarrativeFrameMention.source_item_id)
        .filter(
            NarrativeFrameMention.frame_id == frame_id,
            NarrativeFrameMention.variant_id.is_(None),
            SourceItem.published_at >= cutoff,
        )
        .scalar() or 0
    )

    from app.services.subject_classifier import get_subject_classifier
    _classify_subject = get_subject_classifier(db)
    return {
        "frame": {
            "id": frame.id,
            "name": frame.name,
            "owner_type": frame.owner_type,
            "subject_type": frame.subject_type or _classify_subject(frame.name),
            "description": frame.description,
            "stage": frame.last_known_stage,
            "momentum_signal": frame.momentum_signal,
        },
        "window_days": days_back,
        "variants": variants_out,
        "totals_by_day": totals_by_day,
        "unclustered_mention_count": int(unclustered_count),
    }


def get_variant_articles(
    db: "Session",
    frame_id: int,
    variant_id: int,
    date: Optional[str] = None,
) -> list[dict]:
    """Articles supporting a given (frame_id, variant_id), optionally one day only.

    Powers the Variant-Evolution chart's click-to-drill-down behavior in the
    UI — the user clicks a spike on a given day and we surface the actual
    articles that built that height. Returns the same DetailArticle shape as
    get_frame_detail() so the frontend can render them with the same component.

    `date` (YYYY-MM-DD) filters to articles whose published_at falls on that
    calendar day. When omitted, returns every article tagged with this variant.
    """
    from app.models import NarrativeFrameMention, SourceItem, Outlet
    from sqlalchemy import func

    q = (
        db.query(
            SourceItem.id,
            SourceItem.title,
            SourceItem.summary,
            SourceItem.source_name,
            SourceItem.source_url,
            SourceItem.published_at,
            SourceItem.race_relevance_score,
            SourceItem.sentiment,
            Outlet.name.label("outlet_name"),
            Outlet.outlet_type,
            Outlet.authority_score,
        )
        .select_from(NarrativeFrameMention)
        .join(SourceItem, SourceItem.id == NarrativeFrameMention.source_item_id)
        .outerjoin(Outlet, Outlet.id == SourceItem.outlet_id)
        .filter(
            NarrativeFrameMention.frame_id == frame_id,
            NarrativeFrameMention.variant_id == variant_id,
        )
    )
    if date:
        q = q.filter(func.date(SourceItem.published_at) == date)
    rows = q.order_by(SourceItem.published_at.desc().nulls_last()).all()

    seen: set = set()
    out: list[dict] = []
    for r in rows:
        if r.id in seen:
            continue
        seen.add(r.id)
        out.append({
            "id": r.id,
            "title": r.title,
            "summary": r.summary,
            "source_name": r.source_name,
            "source_url": r.source_url,
            "outlet_name": r.outlet_name,
            "outlet_type": r.outlet_type,
            "outlet_authority": r.authority_score,
            "published_at": r.published_at.isoformat() if r.published_at else None,
            "race_relevance_score": r.race_relevance_score,
            "sentiment": r.sentiment,
        })
    return out


def get_frame_detail(db: "Session", frame_id: int) -> Optional[dict]:
    """Full per-frame detail for the dedicated frame detail page.

    Returns the frame's metadata plus everything a deep-dive view wants:
      - All articles (joined via cluster → outlet), newest first
      - Daily article counts for the last 90 days (activity chart)
      - Outlet-tier breakdown
      - Top quotes (representative-article summaries from highest-authority outlets)
    Returns None if the frame doesn't exist.
    """
    from sqlalchemy import func
    from app.models import FrameClusterMatch, Outlet, StoryCluster

    frame = db.query(NarrativeFrame).filter_by(id=frame_id).first()
    if not frame:
        return None

    now = datetime.utcnow()
    week_start = now - timedelta(days=7)
    # Activity goes back a full year so the detail page's "All time" toggle
    # has meaningful data without re-fetching. 365 days × stacked-tier shape
    # is still cheap to query and ship (~one row per day per active tier).
    activity_cutoff = now - timedelta(days=365)

    # Every article linked to this frame, with outlet metadata, newest-first.
    rows = (
        db.query(
            SourceItem.id,
            SourceItem.title,
            SourceItem.summary,
            SourceItem.source_name,
            SourceItem.source_url,
            SourceItem.published_at,
            SourceItem.race_relevance_score,
            SourceItem.sentiment,
            Outlet.name.label("outlet_name"),
            Outlet.outlet_type,
            Outlet.authority_score,
        )
        .select_from(FrameClusterMatch)
        .join(StoryCluster, StoryCluster.id == FrameClusterMatch.story_cluster_id)
        .join(SourceItem, SourceItem.story_cluster_id == StoryCluster.id)
        .outerjoin(Outlet, Outlet.id == SourceItem.outlet_id)
        .filter(FrameClusterMatch.frame_id == frame_id)
        .order_by(SourceItem.published_at.desc().nulls_last())
        .all()
    )

    # Defend against duplicates if the schema ever changes; in current design
    # one article belongs to one cluster, which has one FCM per frame.
    seen: set = set()
    articles: list[dict] = []
    for r in rows:
        if r.id in seen:
            continue
        seen.add(r.id)
        articles.append({
            "id": r.id,
            "title": r.title,
            "summary": r.summary,
            "source_name": r.source_name,
            "source_url": r.source_url,
            "outlet_name": r.outlet_name,
            "outlet_type": r.outlet_type,
            "outlet_authority": r.authority_score,
            "published_at": r.published_at.isoformat() if r.published_at else None,
            "race_relevance_score": r.race_relevance_score,
            "sentiment": r.sentiment,
        })

    # Counts — use the canonical Option-C definition so the detail page
    # agrees with the list endpoint. `articles_this_week` is now the count
    # of distinct CLUSTERS with any article in the last 7 days, not the raw
    # article count (which over-counted by ~wire-syndication factor).
    from app.services.frame_counts import frame_pulse_counts
    _pulse = frame_pulse_counts(db, [frame_id], now=now)
    this_week_c, _last_week_c, total_c = _pulse.get(frame_id, (0, 0, 0))
    articles_total = total_c
    articles_this_week = this_week_c

    # Activity by day, broken out by outlet tier so the detail page can render
    # stacked bars showing WHO is covering the story each day, not just volume.
    # One row per (date, outlet_type); we pivot to a flat per-date shape below.
    from collections import defaultdict as _defaultdict
    daily_tier_rows = (
        db.query(
            func.date(SourceItem.published_at).label("d"),
            Outlet.outlet_type,
            func.count(func.distinct(SourceItem.id)),
        )
        .select_from(FrameClusterMatch)
        .join(StoryCluster, StoryCluster.id == FrameClusterMatch.story_cluster_id)
        .join(SourceItem, SourceItem.story_cluster_id == StoryCluster.id)
        .outerjoin(Outlet, Outlet.id == SourceItem.outlet_id)
        .filter(
            FrameClusterMatch.frame_id == frame_id,
            SourceItem.published_at >= activity_cutoff,
            SourceItem.published_at.isnot(None),
        )
        .group_by("d", Outlet.outlet_type)
        .order_by("d")
        .all()
    )

    _empty_tiers = lambda: {
        "national": 0, "regional": 0, "local": 0,
        "blog": 0, "social": 0, "unknown": 0,
    }
    by_date: dict = _defaultdict(_empty_tiers)
    for d, outlet_type, c in daily_tier_rows:
        bucket = "unknown"
        if outlet_type in ("national", "broadcast"): bucket = "national"
        elif outlet_type == "regional_news": bucket = "regional"
        elif outlet_type == "local_news": bucket = "local"
        elif outlet_type == "blog": bucket = "blog"
        elif outlet_type == "social": bucket = "social"
        by_date[str(d)][bucket] += c

    activity = []
    for date_str in sorted(by_date.keys()):
        bucket = by_date[date_str]
        total = sum(bucket.values())
        activity.append({
            "date": date_str,
            "count": total,    # back-compat with the simple sparkline consumer
            "total": total,
            **bucket,
        })

    # Outlet-tier breakdown.
    tier_rows = (
        db.query(Outlet.outlet_type, func.count(func.distinct(Outlet.id)))
        .select_from(FrameClusterMatch)
        .join(StoryCluster, StoryCluster.id == FrameClusterMatch.story_cluster_id)
        .join(SourceItem, SourceItem.story_cluster_id == StoryCluster.id)
        .join(Outlet, Outlet.id == SourceItem.outlet_id)
        .filter(FrameClusterMatch.frame_id == frame_id)
        .group_by(Outlet.outlet_type)
        .all()
    )
    tiers = {"national": 0, "regional": 0, "local": 0, "blog": 0, "social": 0}
    for ot, c in tier_rows:
        if ot in ("national", "broadcast"): tiers["national"] += c
        elif ot == "regional_news": tiers["regional"] += c
        elif ot == "local_news": tiers["local"] += c
        elif ot == "blog": tiers["blog"] += c
        elif ot == "social": tiers["social"] += c

    # First/last seen from FCM rows. Skip dates earlier than the plausibility
    # floor — those come from articles whose feed pubDate was misparsed
    # (some as far back as year 0001), which would otherwise show as
    # "First Seen Sep 2, 2016" for a 2026-era frame.
    fcm_dates = (
        db.query(FrameClusterMatch.first_seen_at, FrameClusterMatch.last_seen_at)
        .filter(FrameClusterMatch.frame_id == frame_id)
        .all()
    )
    first_seen = min(
        (r.first_seen_at for r in fcm_dates
         if r.first_seen_at and r.first_seen_at >= _PLAUSIBLE_DATE_FLOOR),
        default=None,
    )
    last_seen = max(
        (r.last_seen_at for r in fcm_dates
         if r.last_seen_at and r.last_seen_at >= _PLAUSIBLE_DATE_FLOOR),
        default=None,
    )

    # Notable quotes — top 3 article summaries, ranked by outlet authority.
    # Skip LLM-refusal placeholders (paywall / empty-body articles).
    quotes_pool = [
        a for a in articles
        if (a.get("summary") or "").strip() and not is_refusal_summary(a.get("summary"))
    ]
    quotes_pool.sort(
        key=lambda a: ((a.get("outlet_authority") or 0), a.get("published_at") or ""),
        reverse=True,
    )
    quotes = [
        {
            "text": a["summary"],
            "source_name": a["source_name"],
            "source_url": a["source_url"],
            "published_at": a["published_at"],
            "outlet_name": a.get("outlet_name"),
        }
        for a in quotes_pool[:3]
    ]

    from app.services.subject_classifier import get_subject_classifier
    return {
        "id": frame.id,
        "name": frame.name,
        "description": frame.description,
        "owner_type": frame.owner_type,
        "subject_type": frame.subject_type or get_subject_classifier(db)(frame.name),
        "source": frame.source,
        "created_at": frame.created_at.isoformat() if frame.created_at else None,
        "first_seen_at": first_seen.isoformat() if first_seen else None,
        "last_seen_at": last_seen.isoformat() if last_seen else None,
        "articles_total": articles_total,
        "articles_this_week": articles_this_week,
        "outlet_tiers": tiers,
        "activity": activity,
        "quotes": quotes,
        "articles": articles,
    }
