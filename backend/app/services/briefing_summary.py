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

_TTL_SECONDS = 1800  # 30 minutes
_cache: dict[int, dict] = {}  # keyed by campaign_id


def _campaign_key(campaign) -> int:
    return getattr(campaign, "id", None) or 0


def invalidate(campaign=None):
    """Call after an ingestion run to force regeneration on next request.

    If campaign is provided, only the cache entry for that campaign is cleared.
    If campaign is None, all entries are cleared.
    """
    if campaign is not None:
        _cache.pop(_campaign_key(campaign), None)
    else:
        _cache.clear()


def get_or_generate(db: Session, articles: list[dict], campaign, opponents: list) -> str | None:
    key = _campaign_key(campaign)
    entry = _cache.get(key)
    now = time.time()
    if entry and entry.get("text") and (now - entry["generated_at"]) < _TTL_SECONDS:
        return entry["text"]

    result = _generate(db, articles, campaign, opponents)
    if result:
        _cache[key] = {"text": result, "generated_at": now}
    return result


def _generate(db: Session, articles: list[dict], campaign, opponents: list) -> str | None:
    if not articles and not opponents:
        log.info("briefing_summary: no articles or opponents — skipping")
        return None

    # Use the judge provider (OpenAI gpt-4o-mini → Groq fallback), the same
    # model the rest of the app uses for written/analytical output. The older
    # get_provider() chain defaults to Groq + Mock, which silently returned
    # empty strings when no keys were available.
    llm = llm_provider.get_judge_provider()

    candidate = getattr(campaign, "candidate_name", None) or "the candidate"
    office = getattr(campaign, "office", None) or "office"
    district = getattr(campaign, "district", None) or "the district"
    message = getattr(campaign, "campaign_message", None) or ""
    opponent_names = ", ".join(o.name for o in opponents[:3]) if opponents else "the incumbent"

    # Pull richer narrative-pulse context so the memo can reference momentum,
    # not just the top 6 articles. Frames with the biggest week-over-week
    # swings are the most newsworthy to mention.
    pulse_block = _narrative_pulse_block(db)

    article_lines = []
    for a in articles[:8]:
        title = a.get("title") or ""
        summary = a.get("summary") or ""
        source = a.get("source_name") or ""
        article_lines.append(f"- {title} ({source}): {summary[:160]}")

    articles_block = "\n".join(article_lines) if article_lines else "No new high-priority articles in the last 48 hours."

    prompt = f"""You are a senior political campaign analyst writing the opening memo for a daily briefing.

RACE: {candidate} vs {opponent_names} — {office}, {district}
CANDIDATE MESSAGE: {message or "(not set)"}

RECENT RELEVANT ARTICLES (last 48 hours):
{articles_block}

NARRATIVE MOMENTUM (week-over-week mention counts):
{pulse_block}

Write 3–4 sentences that directly brief a campaign manager on:
1. What is happening in the race RIGHT NOW based on the articles and narrative momentum
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
        if not text:
            log.warning("briefing_summary: LLM returned empty string (provider=%s)", type(llm).__name__)
            return None
        log.info("briefing_summary: generated %d chars via %s", len(text), type(llm).__name__)
        return text
    except Exception as e:
        log.warning("briefing_summary generation failed: %s", e, exc_info=True)
        return None


def _narrative_pulse_block(db: Session) -> str:
    """Top movers — frames whose mention count this week diverges most from last."""
    from datetime import datetime, timedelta
    from sqlalchemy import func
    from app.models import NarrativeFrame, NarrativeFrameMention, SourceItem

    now = datetime.utcnow()
    cutoff_7d = now - timedelta(days=7)
    cutoff_14d = now - timedelta(days=14)

    frames = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).all()  # noqa: E712
    rows: list[tuple[str, str, int, int]] = []
    for f in frames:
        this_week = (
            db.query(func.count(NarrativeFrameMention.id))
            .join(SourceItem, SourceItem.id == NarrativeFrameMention.source_item_id)
            .filter(NarrativeFrameMention.frame_id == f.id,
                    SourceItem.published_at >= cutoff_7d)
            .scalar() or 0
        )
        last_week = (
            db.query(func.count(NarrativeFrameMention.id))
            .join(SourceItem, SourceItem.id == NarrativeFrameMention.source_item_id)
            .filter(NarrativeFrameMention.frame_id == f.id,
                    SourceItem.published_at >= cutoff_14d,
                    SourceItem.published_at < cutoff_7d)
            .scalar() or 0
        )
        if this_week + last_week == 0:
            continue
        rows.append((f.name, f.owner_type or "media", int(this_week), int(last_week)))

    if not rows:
        return "No narrative activity in the last two weeks."

    # Sort by absolute change, take top 6
    rows.sort(key=lambda r: abs(r[2] - r[3]), reverse=True)
    lines = []
    for name, owner, tw, lw in rows[:6]:
        delta = tw - lw
        arrow = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
        lines.append(f"- [{owner.upper()}] {name}: {tw} this week vs {lw} last ({arrow}{abs(delta)})")
    return "\n".join(lines)
