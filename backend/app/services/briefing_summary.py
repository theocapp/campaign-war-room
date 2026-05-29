"""
Generates a short LLM-written race-situation memo for the Briefing page.
Cached in-memory; regenerated after each ingest cycle or when stale (>30 min).

Two variants:
  - `get_or_generate` (v1 / default): legacy prose memo from article summaries +
    narrative pulse. Returns a string. What the briefing page renders today.
  - `get_or_generate_grounded` (v2): NEW grounded memo that takes labeled
    ClaimRecord quotes as input, has the LLM emit `[C1]..[Cn]` citation
    markers when referencing what someone said, and returns a structured
    {text, citations[]} object. Surfaced behind `?v=2` on /briefing/morning.
"""
import logging
import re
import time
from datetime import datetime
from sqlalchemy.orm import Session

from app.services import llm_provider

log = logging.getLogger(__name__)

_TTL_SECONDS = 1800  # 30 minutes
_cache: dict[int, dict] = {}  # keyed by campaign_id — v1 only
_cache_v2: dict[int, dict] = {}  # keyed by campaign_id — v2 grounded memo


def _campaign_key(campaign) -> int:
    return getattr(campaign, "id", None) or 0


def invalidate(campaign=None):
    """Call after an ingestion run to force regeneration on next request.

    If campaign is provided, only the cache entry for that campaign is cleared.
    If campaign is None, all entries are cleared. Invalidates both v1 and v2
    caches — claim data feeding v2 also changes with new articles.
    """
    if campaign is not None:
        _cache.pop(_campaign_key(campaign), None)
        _cache_v2.pop(_campaign_key(campaign), None)
    else:
        _cache.clear()
        _cache_v2.clear()


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
    """Top movers — frames whose mention count this week diverges most from last.

    Uses the canonical Option-C "this week" definition from frame_counts
    (distinct clusters with any article in window). One GROUP BY query for
    all frames; previously this ran 2N correlated subqueries against the
    stale NarrativeFrameMention table and produced "No narrative activity"
    most of the time even when coverage was lively.
    """
    from app.models import NarrativeFrame
    from app.services.frame_counts import frame_pulse_counts

    frames = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).all()  # noqa: E712
    counts = frame_pulse_counts(db, [f.id for f in frames])
    rows: list[tuple[str, str, int, int]] = []
    for f in frames:
        this_week, last_week, _ = counts.get(f.id, (0, 0, 0))
        if this_week + last_week == 0:
            continue
        rows.append((f.name, f.owner_type or "media", this_week, last_week))

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


# ── v2: grounded memo with [C1]..[Cn] citations ───────────────────────────

_CITATION_PATTERN = re.compile(r"\[C(\d+)\]")


def get_or_generate_grounded(
    db: Session,
    articles: list[dict],
    campaign,
    opponents: list,
    top_claims: list[dict],
) -> dict | None:
    """Cached entry for the grounded v2 memo.

    Returns {text, citations, sources_used} or None on failure / no data.
    `text` is prose with [C1]..[Cn] markers substituted in. `citations`
    maps each marker back to a claim_id + article_id. `sources_used` is
    the input `top_claims` list (echoed back so the frontend can render
    the audit "Sources Used" expandable).
    """
    key = _campaign_key(campaign)
    entry = _cache_v2.get(key)
    now = time.time()
    if entry and (now - entry["generated_at"]) < _TTL_SECONDS:
        return entry["payload"]

    payload = _generate_grounded(db, articles, campaign, opponents, top_claims)
    if payload:
        _cache_v2[key] = {"payload": payload, "generated_at": now}
    return payload


def _generate_grounded(
    db: Session,
    articles: list[dict],
    campaign,
    opponents: list,
    top_claims: list[dict],
) -> dict | None:
    """Build the grounded memo prompt, call the LLM, post-process citations.

    Approach:
      1. Pass the top claims as a numbered list [C1]..[Cn] in the prompt.
      2. Instruct the model to use [CN] markers when referencing what
         someone said — and to QUOTE the exact text, not paraphrase.
      3. After the model responds, find every [CN] reference and build
         the citations array. Markers in the output that DON'T resolve to
         a real claim_id are stripped (defensive — the model shouldn't
         invent IDs but we don't trust it not to).
    """
    if not top_claims and not articles:
        log.info("briefing_grounded: no claims or articles — skipping")
        return None

    llm = llm_provider.get_judge_provider()

    candidate = getattr(campaign, "candidate_name", None) or "the candidate"
    office = getattr(campaign, "office", None) or "office"
    district = getattr(campaign, "district", None) or "the district"
    message = getattr(campaign, "campaign_message", None) or ""
    opponent_names = ", ".join(o.name for o in opponents[:3]) if opponents else "the incumbent"

    pulse_block = _narrative_pulse_block(db)

    # Numbered claim block — the source material for the memo.
    claim_lines = []
    for i, c in enumerate(top_claims, start=1):
        ents = ", ".join(e["name"] for e in c.get("entities", [])) or "—"
        outlet = c.get("outlet", "?")
        date = (c.get("published_at") or "")[:10] or "?"
        label = c.get("label", "?")
        quote = c.get("quote") or ""
        claim_lines.append(
            f"[C{i}] [{label}] entities: {ents} | {outlet}, {date}\n"
            f"     \"{quote}\""
        )
    claims_block = "\n".join(claim_lines) if claim_lines else "(no labeled quotes in window)"

    # Recent article context block (smaller than v1 since claims carry most weight now)
    article_lines = []
    for a in articles[:5]:
        title = a.get("title") or ""
        summary = (a.get("summary") or "")[:140]
        source = a.get("source_name") or ""
        article_lines.append(f"- {title} ({source}): {summary}")
    articles_block = "\n".join(article_lines) if article_lines else "No new high-priority articles in the last 48 hours."

    prompt = f"""You are a senior political campaign analyst writing the opening memo for a daily briefing.

RACE: {candidate} vs {opponent_names} — {office}, {district}
CANDIDATE MESSAGE: {message or "(not set)"}

RECENT RELEVANT ARTICLES (last 48 hours):
{articles_block}

NARRATIVE MOMENTUM (week-over-week mention counts):
{pulse_block}

RECENT QUOTES (use these to ground assertions about what people said or did):
{claims_block}

Write a TIGHT 2-3 sentence opening that briefs a campaign manager on:
1. The single most important thing happening in the race right now
2. What it means for {candidate}

LENGTH: maximum 100 words. Aim for 60-80. Cut every sentence that doesn't directly affect {candidate}'s next decision.

CITATION RULES (strict):
- When you reference what someone SAID or DID that's covered in RECENT QUOTES, use the exact verbatim quote and append the citation marker [CN] for the matching quote.
- Do NOT paraphrase a quote — copy it exactly.
- Do NOT invent [CN] markers that aren't in the list.
- If you can't find a quote to back an assertion, just don't include the assertion.
- It is fine to write a sentence without citations (e.g. for momentum observations from article counts).

STYLE RULES:
- Be specific — name events, people, and implications.
- Connect every point back to the race and {candidate}'s position.
- Do not list articles, use bullet points, or list multiple developments — pick the ONE most important and lead with it.
- Do not start with "Good morning" or any greeting.
- Do not say "currently," "right now," or "today" — implicit; wastes words.
- Do not mention scores, labels, or [CN] markers as concepts — just embed [CN] inline after a quote."""

    try:
        text = llm.complete(prompt).strip()
    except Exception as e:
        log.warning("briefing_grounded generation failed: %s", e, exc_info=True)
        return None

    if not text:
        log.warning("briefing_grounded: LLM returned empty (provider=%s)", type(llm).__name__)
        return None

    # Post-process: extract [CN] markers, validate against the claim list,
    # build the citations array.
    citations: list[dict] = []
    valid_claims_by_marker: dict[str, dict] = {
        f"C{i}": c for i, c in enumerate(top_claims, start=1)
    }
    seen_markers: set[str] = set()
    invalid_markers: list[str] = []

    for match in _CITATION_PATTERN.finditer(text):
        marker = "C" + match.group(1)
        if marker in seen_markers:
            continue
        seen_markers.add(marker)
        claim = valid_claims_by_marker.get(marker)
        if not claim:
            invalid_markers.append(marker)
            continue
        citations.append({
            "marker": marker,
            "claim_id": claim["claim_id"],
            "article_id": claim["article_id"],
        })

    if invalid_markers:
        log.warning(
            "briefing_grounded: model invented %d invalid citation markers: %s — stripping",
            len(invalid_markers), invalid_markers,
        )
        # Strip invalid [CN] from the output text
        for marker in invalid_markers:
            text = text.replace(f"[{marker}]", "")

    log.info(
        "briefing_grounded: generated %d chars with %d citations (provider=%s)",
        len(text), len(citations), type(llm).__name__,
    )

    return {
        "text": text,
        "citations": citations,
        "sources_used": top_claims,
    }
