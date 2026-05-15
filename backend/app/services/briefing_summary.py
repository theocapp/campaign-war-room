"""
Generates a short LLM-written race-situation memo for the Briefing page.
Cached in-memory; regenerated after each ingest cycle or when stale (>30 min).
"""
import logging
import time
from datetime import datetime
from sqlalchemy.orm import Session

from app.services import llm_provider

log = logging.getLogger(__name__)

_cache: dict = {
    "text": None,
    "generated_at": None,
    "ttl_seconds": 1800,  # 30 minutes
}


def invalidate():
    """Call this after an ingestion run to force regeneration on next request."""
    _cache["text"] = None
    _cache["generated_at"] = None


def get_or_generate(db: Session, articles: list[dict], campaign, opponents: list) -> str | None:
    now = time.time()
    if (
        _cache["text"]
        and _cache["generated_at"]
        and (now - _cache["generated_at"]) < _cache["ttl_seconds"]
    ):
        return _cache["text"]

    result = _generate(db, articles, campaign, opponents)
    if result:
        _cache["text"] = result
        _cache["generated_at"] = now
    return result


def _generate(db: Session, articles: list[dict], campaign, opponents: list) -> str | None:
    if not articles and not opponents:
        return None

    llm = llm_provider.get_provider()

    candidate = getattr(campaign, "candidate_name", None) or "the candidate"
    office = getattr(campaign, "office", None) or "office"
    district = getattr(campaign, "district", None) or "the district"
    message = getattr(campaign, "campaign_message", None) or ""
    opponent_names = ", ".join(o.name for o in opponents[:3]) if opponents else "the incumbent"

    article_lines = []
    for a in articles[:6]:
        title = a.get("title") or ""
        summary = a.get("summary") or ""
        label = a.get("actionability_label") or "monitor"
        score = a.get("race_relevance_score") or 0
        article_lines.append(f"- [{label.upper()}, score {score}] {title}: {summary[:120]}")

    articles_block = "\n".join(article_lines) if article_lines else "No new articles in the last 48 hours."

    prompt = f"""You are a senior political campaign analyst writing the opening memo for a daily briefing.

RACE: {candidate} vs {opponent_names} — {office}, {district}
CANDIDATE MESSAGE: {message or "(not set)"}

RECENT RELEVANT ARTICLES (last 48 hours):
{articles_block}

Write 3–4 sentences that directly brief a campaign manager on:
1. What is happening in the race RIGHT NOW based on the articles above
2. The most important development and what it means for this specific race
3. Any threat or opportunity that needs attention

Rules:
- Be specific — name events, people, and implications
- Connect every point back to the race and the candidate's position
- If an article is about the opponent, say what it means for the campaign
- Do not list articles or use bullet points — write flowing prose
- Do not start with "Good morning" or any greeting
- Do not mention scores or labels
- If there is nothing significant, say so plainly in one sentence"""

    try:
        text = llm.complete(prompt).strip()
        return text if text else None
    except Exception as e:
        log.warning("briefing_summary generation failed: %s", e)
        return None
