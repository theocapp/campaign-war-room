"""
Single LLM call per article: relevance + summary + framing + sentiment + frame matching.

Replaces the separate summarize_source, classify_urgency, keyword-based
race_relevance, and match_article_to_frames calls. The LLM reads the article
with full campaign context and makes a single holistic judgment — including
which narrative frames apply and the article's sentiment toward the candidate.

Falls back to the keyword scorer if the LLM call fails or is unavailable.
"""
import json
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models import CampaignConfig, NarrativeFrame, Opponent, SourceItem

logger = logging.getLogger(__name__)


def _build_context(db: Session) -> dict:
    config = db.query(CampaignConfig).first()
    opponents = db.query(Opponent).all()

    if not config:
        return {
            "candidate": "Unknown",
            "race": "Unknown",
            "location": "Unknown",
            "opponents": ["Unknown"],
            "issues": [],
        }

    priorities = []
    if config.key_priorities:
        if isinstance(config.key_priorities, str):
            try:
                priorities = json.loads(config.key_priorities)
            except Exception:
                priorities = [p.strip() for p in config.key_priorities.split(",")]
        else:
            priorities = list(config.key_priorities)

    race = (
        config.race
        or f"{config.office or 'office'}"
        + (f", {config.district}" if config.district else "")
    )

    return {
        "candidate": config.candidate_name or "Unknown",
        "race": race,
        "location": config.location or config.district or "Unknown",
        "opponents": [o.name for o in opponents] if opponents else ["Unknown"],
        "issues": [str(p) for p in priorities if p],
    }


def _article_text(item: SourceItem, max_words: int = 600) -> str:
    text = item.raw_text or item.summary or item.title or ""
    words = text.split()
    return " ".join(words[:max_words])


def _build_frames_section(frames: list) -> str:
    """Return a numbered NARRATIVE FRAMES block for the prompt, or empty string."""
    if not frames:
        return ""
    lines = "\n".join(
        f"{i + 1}. [{f.owner_type}] {f.name}: {f.description or ''}"
        for i, f in enumerate(frames)
    )
    return f"\nNARRATIVE FRAMES (match by number — return numbers in frame_matches):\n{lines}\n"


def _build_prompt(item: SourceItem, ctx: dict, frames: list | None = None) -> str:
    opponent_str = " and ".join(ctx["opponents"])
    issues_str = ", ".join(ctx["issues"]) if ctx["issues"] else "general campaign issues"
    article_text = _article_text(item)
    frames_section = _build_frames_section(frames or [])
    frames_json_note = (
        '"frame_matches": [1, 3]' if frames else '"frame_matches": []'
    )

    return f"""You are analyzing a news article for a political campaign intelligence tool.

CAMPAIGN CONTEXT:
- Candidate: {ctx["candidate"]}
- Race: {ctx["race"]}
- Location: {ctx["location"]}
- Opponent: {opponent_str}
- Key campaign issues: {issues_str}
{frames_section}
ARTICLE:
Title: {item.title or "No title"}
Source: {item.source_name or "Unknown"}
Text: {article_text}

Your job: decide if this article matters for the {ctx["race"]} campaign, and
extract any opponent attacks, claims, or promises in the same pass.

MARK IRRELEVANT (relevant=false, score 0-15):
- Sports results, restaurant reviews, weather reports
- Local events with no political figures involved
- National news with no connection to {ctx["location"]} or this race
- University/school events unless {ctx["candidate"]} or {opponent_str} appears
- General crime, flooding, traffic, or community calendar items

MARK RELEVANT (relevant=true, score 40-100):
- Anything directly involving {ctx["candidate"]} or {opponent_str}
- Local policy debates affecting the district (housing, jobs, healthcare in {ctx["location"]})
- Campaign finance, endorsements, polling, fundraising
- Attacks, scandals, or controversies involving either candidate
- Congressional votes or positions on {issues_str}
- Statements or press releases from either campaign

OPPONENT ATTACKS: when the article describes the opponent ({opponent_str})
as the ACTOR — making a claim, launching an attack, or making a promise —
copy the exact sentence into `opponent_attacks`. Only include sentences
where the opponent themselves is the subject (NOT sentences where
{ctx["candidate"]} is criticizing the opponent). Return [] if none.

SENTIMENT: how the article's overall tone affects {ctx["candidate"]}:
- "positive" = favorable coverage for the candidate
- "negative" = unfavorable coverage for the candidate
- "neutral" = balanced/factual, no clear tilt
- "mixed" = contains both positive and negative elements

Return ONLY a JSON object, no other text:
{{
  "relevant": true or false,
  "relevance_score": integer 0-100,
  "one_sentence": "What happened politically in one sentence. null if irrelevant.",
  "framing": "helps_candidate" or "hurts_candidate" or "opponent_news" or "background" or "irrelevant",
  "needs_attention": true or false,
  "reason": "One sentence explaining the relevance judgment.",
  "sentiment": "positive" or "negative" or "neutral" or "mixed",
  "opponent_attacks": [
    {{
      "opponent_name": "name of the opponent making this statement",
      "type": "attack" or "claim" or "promise",
      "text": "the exact sentence from the article"
    }}
  ],
  {frames_json_note}
}}"""


def _fallback_result() -> dict:
    return {
        "relevant": False,
        "relevance_score": 0,
        "one_sentence": None,
        "framing": "irrelevant",
        "needs_attention": False,
        "reason": "LLM unavailable; article not scored.",
        "sentiment": "neutral",
        "opponent_attacks": [],
        "frame_matches": [],
        "_used_fallback": True,
    }


def _validate_opponent_attacks(raw_attacks, known_opponents: list[str]) -> list[dict]:
    """Coerce LLM-returned attacks into a clean list of dicts.

    Drops entries with missing text, unknown opponent names, or invalid type.
    """
    if not isinstance(raw_attacks, list):
        return []
    valid_types = {"attack", "claim", "promise"}
    known_lower = {o.lower() for o in known_opponents if o}
    cleaned: list[dict] = []
    for entry in raw_attacks:
        if not isinstance(entry, dict):
            continue
        text = (entry.get("text") or "").strip()
        attack_type = (entry.get("type") or "").strip().lower()
        opponent_name = (entry.get("opponent_name") or "").strip()
        if not text or attack_type not in valid_types or not opponent_name:
            continue
        # Only keep attacks attributed to an opponent we actually track.
        if opponent_name.lower() not in known_lower:
            continue
        cleaned.append({"text": text, "type": attack_type, "opponent_name": opponent_name})
    return cleaned


_VALID_SENTIMENTS = {"positive", "negative", "neutral", "mixed"}


def analyze(db: Session, item: SourceItem) -> dict:
    """Thin wrapper — calls analyze_with_frames with no frame list."""
    return analyze_with_frames(db, item, frames=None)


def analyze_cluster(
    db: Session,
    cluster,  # StoryCluster (avoid circular import at module load)
    frames: list[NarrativeFrame] | None = None,
) -> dict:
    """Cluster-level LLM analysis.

    Phase A defines this so Phase D's retrigger path has the entry point ready;
    ingestion does NOT call this yet (per-article analyze_with_frames is still
    the per-article LLM call to preserve dual-write parity).

    Resolves the cluster's analysis anchor (or representative on the first
    run), then delegates to analyze_with_frames on that one article. Future
    work can enrich the prompt with sibling article snippets; today we keep
    the contract identical to the per-article path so callers can swap in.
    """
    anchor_id = cluster.analysis_anchor_source_item_id or cluster.representative_source_item_id
    if not anchor_id:
        return _fallback_result()
    anchor = db.query(SourceItem).filter_by(id=anchor_id).first()
    if not anchor:
        return _fallback_result()
    return analyze_with_frames(db, anchor, frames=frames)


def analyze_with_frames(
    db: Session,
    item: SourceItem,
    frames: list[NarrativeFrame] | None = None,
) -> dict:
    """
    Single LLM call per article: relevance + summary + framing + sentiment +
    frame matching.

    When `frames` is provided the prompt includes a numbered frame list and
    the response includes `frame_matches` (1-indexed ints).  Ingestion code is
    responsible for translating those indices to NarrativeFrameMention rows.

    Returns a dict with:
        relevant, relevance_score, one_sentence, framing, needs_attention,
        reason, sentiment, opponent_attacks, frame_matches

    On any failure, returns a fallback dict with _used_fallback=True.
    """
    raw = None
    try:
        from app.services.llm_provider import get_ingestion_provider, MockLLMProvider
        provider = get_ingestion_provider()

        # Mock provider doesn't understand structured prompts — use keyword fallback
        if isinstance(provider, MockLLMProvider):
            logger.warning(
                "campaign_analysis: MockLLMProvider active — AI scoring disabled, "
                "using fallback for item %d",
                item.id,
            )
            return _fallback_result()

        ctx = _build_context(db)
        prompt = _build_prompt(item, ctx, frames=frames)
        raw = provider.complete(prompt)

        if not raw or not raw.strip():
            logger.warning("campaign_analysis: empty response for item %d", item.id)
            return _fallback_result()

        # Strip markdown code fences if the model wraps output in ```json ... ```
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            inner = lines[1:] if lines[-1].strip() == "```" else lines[1:]
            text = "\n".join(inner).strip()

        result = json.loads(text)

        # Validate and clamp all fields
        score = max(0, min(100, int(result.get("relevance_score", 0))))
        result["relevance_score"] = score
        result["relevant"] = bool(result.get("relevant", False))
        result["needs_attention"] = bool(result.get("needs_attention", False))
        result.setdefault("framing", "irrelevant")
        result.setdefault("reason", "")
        result.setdefault("one_sentence", None)

        # Sentiment — coerce to a known value
        raw_sentiment = (result.get("sentiment") or "neutral").strip().lower()
        result["sentiment"] = raw_sentiment if raw_sentiment in _VALID_SENTIMENTS else "neutral"

        result["opponent_attacks"] = _validate_opponent_attacks(
            result.get("opponent_attacks"), ctx["opponents"]
        )

        # frame_matches: validate each is a 1-based int within the frame list
        n_frames = len(frames) if frames else 0
        raw_matches = result.get("frame_matches") or []
        if isinstance(raw_matches, list):
            result["frame_matches"] = [
                idx for idx in raw_matches
                if isinstance(idx, int) and 1 <= idx <= n_frames
            ]
        else:
            result["frame_matches"] = []

        result["_used_fallback"] = False

        logger.info(
            "campaign_analysis: item=%d  relevant=%s  score=%d  framing=%s  "
            "sentiment=%s  frames=%s  title=%r",
            item.id,
            result["relevant"],
            result["relevance_score"],
            result["framing"],
            result["sentiment"],
            result["frame_matches"],
            (item.title or "")[:60],
        )
        return result

    except json.JSONDecodeError:
        preview = (raw or "")[:300]
        logger.warning(
            "campaign_analysis: JSON parse error for item %d. Raw: %r", item.id, preview
        )
        return _fallback_result()
    except Exception as exc:
        logger.warning("campaign_analysis: failed for item %d: %s", item.id, exc)
        return _fallback_result()


def framing_to_action(framing: str) -> str:
    """Map LLM framing label to actionability label used across the app."""
    return {
        "hurts_candidate": "respond",
        "opponent_news": "respond",
        "helps_candidate": "review",
        "background": "monitor",
        "irrelevant": "ignore",
    }.get(framing, "monitor")
