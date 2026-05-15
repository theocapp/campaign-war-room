"""
Single LLM call per article: relevance + summary + framing.

Replaces the separate summarize_source, classify_urgency, and keyword-based
race_relevance calls. The LLM reads the article with full campaign context
and makes a single holistic judgment about whether it matters for the race.

Falls back to the keyword scorer if the LLM call fails or is unavailable.
"""
import json
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models import CampaignConfig, Opponent, SourceItem

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


def _build_prompt(item: SourceItem, ctx: dict) -> str:
    opponent_str = " and ".join(ctx["opponents"])
    issues_str = ", ".join(ctx["issues"]) if ctx["issues"] else "general campaign issues"
    article_text = _article_text(item)

    return f"""You are analyzing a news article for a political campaign intelligence tool.

CAMPAIGN CONTEXT:
- Candidate: {ctx["candidate"]}
- Race: {ctx["race"]}
- Location: {ctx["location"]}
- Opponent: {opponent_str}
- Key campaign issues: {issues_str}

ARTICLE:
Title: {item.title or "No title"}
Source: {item.source_name or "Unknown"}
Text: {article_text}

Your job: decide if this article matters for the {ctx["race"]} campaign.

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

Return ONLY a JSON object, no other text:
{{
  "relevant": true or false,
  "relevance_score": integer 0-100,
  "one_sentence": "What happened politically in one sentence. null if irrelevant.",
  "framing": "helps_candidate" or "hurts_candidate" or "opponent_news" or "background" or "irrelevant",
  "needs_attention": true or false,
  "reason": "One sentence explaining the relevance judgment."
}}"""


def _fallback_result() -> dict:
    return {
        "relevant": False,
        "relevance_score": 0,
        "one_sentence": None,
        "framing": "irrelevant",
        "needs_attention": False,
        "reason": "LLM unavailable; article not scored.",
        "_used_fallback": True,
    }


def analyze(db: Session, item: SourceItem) -> dict:
    """
    Run one LLM call to assess whether an article matters for the campaign.

    Returns a dict with:
        relevant (bool), relevance_score (int 0-100), one_sentence (str|None),
        framing (str), needs_attention (bool), reason (str)

    On any failure, returns a fallback dict with _used_fallback=True so the
    caller knows to run keyword scoring instead.
    """
    raw = None
    try:
        from app.services.llm_provider import get_provider, MockLLMProvider
        provider = get_provider()

        # Mock provider doesn't understand structured prompts — use keyword fallback
        if isinstance(provider, MockLLMProvider):
            logger.warning("campaign_analysis: MockLLMProvider active — AI scoring disabled, using fallback for item %d", item.id)
            return _fallback_result()

        ctx = _build_context(db)
        prompt = _build_prompt(item, ctx)
        raw = provider.complete(prompt)

        if not raw or not raw.strip():
            logger.warning("campaign_analysis: empty response for item %d", item.id)
            return _fallback_result()

        # Strip markdown code fences if the model wraps output in ```json ... ```
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            # Drop first line (```json or ```) and last line (```)
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
        result["_used_fallback"] = False

        logger.info(
            "campaign_analysis: item=%d  relevant=%s  score=%d  framing=%s  title=%r",
            item.id,
            result["relevant"],
            result["relevance_score"],
            result["framing"],
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
