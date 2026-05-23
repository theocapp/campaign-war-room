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


def _article_text(item: SourceItem, max_words: int = 8000) -> tuple[str, bool]:
    """Return (text, truncated) — truncated=True when we cut content.

    8K words ≈ ~12K tokens — well under gpt-4o-mini's 128K context window.
    Catches 95%+ of articles in full; only extremely long-form investigations
    get truncated. Bumped from 1500 after tier-2 OpenAI access removed the
    practical cost-of-tokens constraint that motivated the tighter cap.
    """
    text = item.raw_text or item.summary or item.title or ""
    words = text.split()
    if len(words) <= max_words:
        return text, False
    return " ".join(words[:max_words]), True


def _build_frames_section(frames: list) -> str:
    """Format the frame list for the prompt. LLM matches by NAME, not index."""
    if not frames:
        return "(none yet — propose candidate_new_frame for any recurring narratives you find)"
    return "\n".join(
        f'  - "{f.name}" [{f.owner_type}]: {(f.description or "no description").strip()[:200]}'
        for f in frames
    )


SYSTEM_PROMPT = (
    "You are a political campaign intelligence analyst. You read news articles "
    "and extract structured intelligence for a real campaign team. You are "
    "rigorous, evidence-based, and conservative — you flag claims only when "
    "they are clearly present in the text. You never invent quotes. You never "
    "invent narrative frames unless the article shows a genuinely recurring "
    "pattern. Default to fewer findings of higher confidence over more "
    "findings of lower confidence. Return only valid JSON matching the "
    "requested schema."
)


def _build_prompt(item: SourceItem, ctx: dict, frames: list | None = None) -> str:
    """Build the v2 user prompt. See SYSTEM_PROMPT for role framing."""
    from datetime import datetime, timezone

    opponent_str = " and ".join(ctx["opponents"])
    issues_str = ", ".join(ctx["issues"]) if ctx["issues"] else "general campaign issues"
    article_text, truncated = _article_text(item)
    frames_section = _build_frames_section(frames or [])
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pub = (item.published_at.strftime("%Y-%m-%d") if item.published_at else "unknown")

    truncation_note = (
        "\n[NOTE: Article was truncated to the first 1500 words for context. "
        "Extract claims only from the visible text above.]" if truncated else ""
    )

    return f"""Today's date: {today}

CAMPAIGN CONTEXT
Candidate (our side): {ctx["candidate"]}
Race: {ctx["race"]}
Location: {ctx["location"]}
Opponent(s): {opponent_str}
Key campaign issues: {issues_str}

EXISTING NARRATIVE FRAMES
Match extracted claims to these frames by exact name. If a claim fits no
frame but expresses a recurring narrative worth tracking, propose a
candidate_new_frame.
{frames_section}

ARTICLE
Title: {item.title or "No title"}
Source: {item.source_name or "Unknown"}
Published: {pub}
Text:
{article_text}{truncation_note}

═══════════════════════════════════════════════════════════════════════
YOUR JOB
═══════════════════════════════════════════════════════════════════════

(1) Decide if this article matters for the {ctx["race"]} campaign (verdict).
(2) Extract the specific claims it makes — with VERBATIM quotes as evidence.
(3) Match each claim to existing frames OR propose a new one if it's a
    recurring narrative.
(4) Recommend a campaign_action.

VERDICT RUBRIC
- "irrelevant": no political content about this race. Sports, weather,
  unrelated national news, generic local events.
- "loosely_related": touches political/policy themes in {ctx["location"]} but
  doesn't directly involve {ctx["candidate"]}, {opponent_str}, or this race.
- "relevant": directly involves {ctx["candidate"]}, {opponent_str}, district
  policy, endorsements, polling, or campaign activity. Most political
  coverage lands here.
- "critical": breaking news the campaign should act on TODAY — scandal,
  major polling shift, new attack ad, debate gaffe, opposition research,
  major endorsement. Use sparingly.

CLAIM EXTRACTION RULES
- Quotes MUST be verbatim from the article. Never paraphrase. Never invent.
- Extract 0–20 claims per article. Most articles have 1–4.
- Skip narrative throat-clearing ("It's been a busy week for..."). Only
  extract claims that make a substantive point.
- "actor_name" = free-form name of the person/entity whose position the
  claim represents (NOT the journalist reporting it). Use full names.
- "actor_role" = the role enum (see schema).
- "matched_frames" must be EXACT names from the frame list above. If
  unsure between a match and a candidate_new_frame, prefer the match if
  it's a reasonable fit.

CANDIDATE NEW FRAME — STRICT (DEFAULT IS NULL)
Default to null. Only propose when ALL FIVE conditions hold:

  1. SPECIFIC actor: the claim names {ctx["candidate"]}, {opponent_str}, or a
     clearly identified third party (a specific senator, donor, ruling body) —
     NOT generic groups like "voters", "Pennsylvanians", "Democrats",
     "mom-and-pop shops".

  2. SPECIFIC act: the claim describes a concrete vote, statement, action,
     donation, event, or measurable outcome — NOT a theme, mood, value, or
     thesis statement.
     ✗ "Cognetti's pragmatic approach"  (mood/value)
     ✗ "Cognetti's leadership on poverty"  (theme)
     ✓ "Cognetti's vote against the maternity leave amendment"  (act)

  3. GEOGRAPHY hook: the claim ties to {ctx["location"]} (its cities/counties)
     OR to a named candidate directly. Generic state-wide or national stories
     without a local tie do NOT qualify.
     ✗ "Pennsylvania housing crisis" / "Trump housing cuts nationally"
     ✓ "Bresnahan voted against PA housing bill"

  4. PLAUSIBLY RECURRING: you can imagine this exact narrative appearing in
     5+ separate future articles from different outlets.
     ✗ Single-voter quotes, one-off statements, biographical color
     ✓ Policy positions, scandals, endorsements, contested votes

  5. NO existing frame fits. If unsure between match and create, PREFER MATCH.

If any condition is unclear, set to null. False rejections are cheap (real
narratives reappear). False creations create permanent junk frames that
must be manually deleted. When in doubt, set to null.

OWNER TYPE FOR NEW FRAMES
owner_type identifies which SIDE BENEFITS from / is PUSHING this narrative —
NOT which person the frame mentions. An attack on Bresnahan is owned by
{ctx["candidate"]}'s side (it helps us). An attack on {ctx["candidate"]} is
owned by the opponent's side.

  - "candidate" = frame promotes {ctx["candidate"]} OR attacks the opponent.
    Used by our side.
  - "opponent" = frame promotes the opponent OR attacks {ctx["candidate"]}.
    Used by their side.
  - "media" = neutral observation, horse-race coverage, or pattern neither
    side is actively pushing. DO NOT use "media" just because a story is
    "covered in the media" — most political stories are. Use "media" only
    when neither side gains from the framing.

CLAIM TYPE DEFINITIONS
  - policy_position: stance on legislation, policy, or issue
  - personal_attack: critical statement about character/competence/judgment
    (no specific wrongdoing alleged)
  - endorsement: support from an organization or notable individual
  - polling: numeric poll results
  - scandal: alleged or confirmed wrongdoing (financial/ethical/legal —
    distinct from personal_attack because an act is implied)
  - promise: commitment to take a future action if elected
  - rebuttal: response defending against an attack or correcting a claim

SOURCE CREDIBILITY ANCHORS
  - "high": NYT, WaPo, AP, Reuters, major TV networks, established local
    papers (Times-Tribune, Citizens' Voice, WBRE), university polling
  - "medium": niche outlets, partisan-leaning but established (Politico,
    The Hill), most local PA outlets, established political blogs
  - "low": Substack, personal blogs, social media, unsigned posts,
    aggregator sites, AI-generated content

CONSISTENCY RULES (enforced)
  - If verdict="irrelevant" → campaign_action MUST be "ignore"
  - If verdict="critical" → campaign_action CANNOT be "ignore"
  - If verdict="irrelevant" → summary MUST be null and extracted_claims MUST be []
  - needs_attention=true REQUIRES needs_attention_reason to be a non-empty string

CRITICAL — ANTI-HALLUCINATION
Every quote you extract MUST appear verbatim in the article text above.
Server-side validation discards claims with quotes not found in the source.
If you cannot find an exact verbatim sentence to support a claim, do not
include the claim at all.

═══════════════════════════════════════════════════════════════════════
OUTPUT SCHEMA — return only valid JSON, no markdown fences
═══════════════════════════════════════════════════════════════════════

{{
  "verdict": "irrelevant" | "loosely_related" | "relevant" | "critical",
  "summary": "One-sentence factual recap. null if irrelevant.",
  "campaign_action": "ignore" | "monitor" | "review" | "respond" | "amplify",
  "needs_attention": true | false,
  "needs_attention_reason": "Brief justification, or null when false.",
  "sentiment": "favors_candidate" | "favors_opponent" | "neutral",
  "source_credibility": "high" | "medium" | "low",
  "extracted_claims": [
    {{
      "quote": "verbatim sentence from the article",
      "claim_type": "policy_position" | "personal_attack" | "endorsement" | "polling" | "scandal" | "promise" | "rebuttal",
      "actor_name": "free-form name of person/entity whose position this is",
      "actor_role": "candidate" | "opponent" | "media" | "third_party",
      "matched_frames": ["exact frame name from list above", ...],
      "candidate_new_frame": null OR {{
        "suggested_name": "short, specific frame name (5-10 words)",
        "owner_type": "candidate" | "opponent" | "media",
        "reasoning": "why this is a recurring pattern, not a one-off"
      }},
      "confidence": "high" | "medium" | "low",
      "claim_intensity": "factual" | "critical" | "inflammatory",
      "temporal_frame": "past" | "present" | "future",
      "attribution": "direct_quote" | "paraphrased" | "reported" | "unattributed",
      "has_rebuttal": true | false,
      "rebuttal_quote": "verbatim rebuttal sentence" OR null
    }}
  ]
}}

═══════════════════════════════════════════════════════════════════════
EXAMPLES
═══════════════════════════════════════════════════════════════════════

EXAMPLE 1 (relevant article with strong attack + rebuttal):

Article: "Bresnahan defends fracking vote as Cognetti calls it 'reckless'
By Times-Tribune Staff, May 14, 2026
Rep. Rob Bresnahan defended his recent vote against new fracking
regulations on Tuesday, saying the rules would 'kill Pennsylvania jobs.'
Democratic challenger Paige Cognetti called the vote 'reckless and out
of touch with PA-08 families who breathe this air.' A Bresnahan
spokesperson dismissed Cognetti's response as 'campaign theatrics.'
Polling from Susquehanna University this week shows Bresnahan leading
47-43 among likely voters."

Output:
{{
  "verdict": "relevant",
  "summary": "Bresnahan defended his vote against fracking regulations as job-protective; Cognetti called it reckless; Susquehanna poll has Bresnahan +4.",
  "campaign_action": "respond",
  "needs_attention": true,
  "needs_attention_reason": "Opponent is publicly defending an attackable environmental vote AND a new poll shows him with a real lead — both signals our team should address today.",
  "sentiment": "favors_opponent",
  "source_credibility": "high",
  "extracted_claims": [
    {{
      "quote": "Rep. Rob Bresnahan defended his recent vote against new fracking regulations on Tuesday, saying the rules would 'kill Pennsylvania jobs.'",
      "claim_type": "policy_position",
      "actor_name": "Rob Bresnahan",
      "actor_role": "opponent",
      "matched_frames": [],
      "candidate_new_frame": {{
        "suggested_name": "Bresnahan opposed fracking regulations",
        "owner_type": "candidate",
        "reasoning": "Environmental votes by Bresnahan are likely to be a recurring attack vector for Cognetti's campaign through the election."
      }},
      "confidence": "high",
      "claim_intensity": "critical",
      "temporal_frame": "past",
      "attribution": "direct_quote",
      "has_rebuttal": true,
      "rebuttal_quote": "Democratic challenger Paige Cognetti called the vote 'reckless and out of touch with PA-08 families who breathe this air.'"
    }},
    {{
      "quote": "Polling from Susquehanna University this week shows Bresnahan leading 47-43 among likely voters.",
      "claim_type": "polling",
      "actor_name": "Susquehanna University",
      "actor_role": "third_party",
      "matched_frames": [],
      "candidate_new_frame": null,
      "confidence": "high",
      "claim_intensity": "factual",
      "temporal_frame": "present",
      "attribution": "reported",
      "has_rebuttal": false,
      "rebuttal_quote": null
    }}
  ]
}}

EXAMPLE 2 (irrelevant article — keep extraction empty):

Article: "Mets drop series to Diamondbacks — Hazleton Standard-Speaker"
[sports recap with no political content]

Output:
{{
  "verdict": "irrelevant",
  "summary": null,
  "campaign_action": "ignore",
  "needs_attention": false,
  "needs_attention_reason": null,
  "sentiment": "neutral",
  "source_credibility": "medium",
  "extracted_claims": []
}}

Now analyze the article above and return the JSON."""


def _fallback_result() -> dict:
    """Returned when the LLM call fails. Shape matches a successful analysis
    so downstream consumers (rescore, ingestion) don't need fallback branches."""
    return {
        # v2 fields
        "verdict": "irrelevant",
        "summary": None,
        "campaign_action": "ignore",
        "needs_attention": False,
        "needs_attention_reason": None,
        "sentiment_new": "neutral",
        "source_credibility": "medium",
        "extracted_claims": [],
        # back-compat fields (computed from v2 in successful calls)
        "relevant": False,
        "relevance_score": 0,
        "one_sentence": None,
        "framing": "irrelevant",
        "reason": "LLM unavailable; article not scored.",
        "sentiment": "neutral",
        "opponent_attacks": [],
        "frame_matches": [],
        # candidate-new-frame staging payload (consumed by _rescore_one)
        "candidate_new_frames": [],
        "_used_fallback": True,
    }


# ─── v2 schema validation helpers ─────────────────────────────────────────────

_VALID_VERDICTS = {"irrelevant", "loosely_related", "relevant", "critical"}
_VALID_CAMPAIGN_ACTIONS = {"ignore", "monitor", "review", "respond", "amplify"}
_VALID_SENTIMENTS_V2 = {"favors_candidate", "favors_opponent", "neutral"}
_VALID_CREDIBILITY = {"high", "medium", "low"}
_VALID_CLAIM_TYPES = {
    "policy_position", "personal_attack", "endorsement", "polling",
    "scandal", "promise", "rebuttal",
}
_VALID_ACTOR_ROLES = {"candidate", "opponent", "media", "third_party"}
_VALID_INTENSITY = {"factual", "critical", "inflammatory"}
_VALID_TEMPORAL = {"past", "present", "future"}
_VALID_ATTRIBUTION = {"direct_quote", "paraphrased", "reported", "unattributed"}
_VALID_NEW_OWNER = {"candidate", "opponent", "media"}

# Maps verdict tier → race_relevance_score. Preserves the 0-100 column without
# pretending to 100 levels of precision — there are only 4 real tiers.
_VERDICT_TO_SCORE = {
    "irrelevant": 0,
    "loosely_related": 25,
    "relevant": 65,
    "critical": 90,
}


def _normalize_text(s: str) -> str:
    """Loose normalization for quote substring checks: collapse whitespace,
    normalize smart quotes to ASCII, lowercase. Used ONLY for membership
    tests — never mutates stored data."""
    import re
    return re.sub(r"\s+", " ", (
        (s or "")
        .replace("“", '"').replace("”", '"')
        .replace("‘", "'").replace("’", "'")
        .replace("—", "-").replace("–", "-")
        .replace("\xa0", " ")
        .strip()
        .lower()
    ))


def _verify_quote(quote: str, article_text: str) -> bool:
    """True iff the quote appears verbatim in the article (after normalization)."""
    if not quote or not article_text:
        return False
    return _normalize_text(quote) in _normalize_text(article_text)


def _fuzzy_match_frame(name: str, frames: list) -> "NarrativeFrame | None":
    """Match an LLM-returned frame name to the existing frame list.

    Strategy: exact match (case-insensitive) → substring match → None.
    More elaborate fuzzy matching can come later; this catches the common
    casing/punctuation variants without false positives.
    """
    if not name or not frames:
        return None
    target = name.strip().lower()
    # Exact match first
    for f in frames:
        if (f.name or "").strip().lower() == target:
            return f
    # Substring match (target inside frame name OR frame name inside target)
    for f in frames:
        fn = (f.name or "").strip().lower()
        if not fn:
            continue
        if target in fn or fn in target:
            return f
    return None


def _coerce_enum(value, valid: set, default: str) -> str:
    """Coerce a string to one of `valid`, falling back to `default`."""
    v = (value or "").strip().lower() if isinstance(value, str) else ""
    return v if v in valid else default


def _validate_v2_claim(
    raw: dict, article_text: str, frames: list, candidate: str, opponents: list[str],
) -> dict | None:
    """Validate and clean a single extracted_claim from the LLM.

    Returns the cleaned dict, or None to drop the claim entirely.
    Drops claims with hallucinated quotes (quote not found in article).
    """
    if not isinstance(raw, dict):
        return None

    quote = (raw.get("quote") or "").strip()
    if not quote or not _verify_quote(quote, article_text):
        return None  # hallucinated or empty — drop silently

    claim_type = _coerce_enum(raw.get("claim_type"), _VALID_CLAIM_TYPES, "policy_position")
    actor_role = _coerce_enum(raw.get("actor_role"), _VALID_ACTOR_ROLES, "third_party")
    actor_name = (raw.get("actor_name") or "").strip()[:120] or "unknown"
    confidence = _coerce_enum(raw.get("confidence"), {"high", "medium", "low"}, "medium")
    intensity = _coerce_enum(raw.get("claim_intensity"), _VALID_INTENSITY, "factual")
    temporal = _coerce_enum(raw.get("temporal_frame"), _VALID_TEMPORAL, "present")
    attribution = _coerce_enum(raw.get("attribution"), _VALID_ATTRIBUTION, "reported")

    # matched_frames: keep only names that fuzzy-match a real frame
    matched_names: list[str] = []
    matched_frame_ids: list[int] = []
    raw_matches = raw.get("matched_frames") or []
    if isinstance(raw_matches, list):
        for n in raw_matches:
            if not isinstance(n, str):
                continue
            f = _fuzzy_match_frame(n, frames)
            if f and f.id not in matched_frame_ids:
                matched_frame_ids.append(f.id)
                matched_names.append(f.name)

    # candidate_new_frame: validate shape, drop if matches existing or empty
    cnf_raw = raw.get("candidate_new_frame")
    candidate_new_frame = None
    if isinstance(cnf_raw, dict):
        suggested = (cnf_raw.get("suggested_name") or "").strip()
        owner = _coerce_enum(cnf_raw.get("owner_type"), _VALID_NEW_OWNER, "media")
        reasoning = (cnf_raw.get("reasoning") or "").strip()[:500]
        if suggested and len(suggested.split()) >= 2:
            # Drop if it actually matches an existing frame after all
            if not _fuzzy_match_frame(suggested, frames):
                candidate_new_frame = {
                    "suggested_name": suggested[:120],
                    "owner_type": owner,
                    "reasoning": reasoning or "no reasoning provided",
                }

    # has_rebuttal + rebuttal_quote (rebuttal quote also verified)
    has_rebuttal = bool(raw.get("has_rebuttal"))
    rebuttal_quote = (raw.get("rebuttal_quote") or "").strip() or None
    if rebuttal_quote and not _verify_quote(rebuttal_quote, article_text):
        rebuttal_quote = None
        has_rebuttal = False

    return {
        "quote": quote,
        "claim_type": claim_type,
        "actor_name": actor_name,
        "actor_role": actor_role,
        "matched_frame_ids": matched_frame_ids,  # resolved by server
        "matched_frame_names": matched_names,    # for logging/audit
        "candidate_new_frame": candidate_new_frame,
        "confidence": confidence,
        "claim_intensity": intensity,
        "temporal_frame": temporal,
        "attribution": attribution,
        "has_rebuttal": has_rebuttal,
        "rebuttal_quote": rebuttal_quote,
    }


def _enforce_consistency(result: dict) -> dict:
    """Apply the CONSISTENCY RULES from the prompt server-side as well.

    The prompt asks the LLM to enforce these, but we double-check so a bad
    LLM run can't push nonsense like verdict=irrelevant + action=respond.
    """
    verdict = result["verdict"]
    if verdict == "irrelevant":
        result["campaign_action"] = "ignore"
        result["summary"] = None
        result["extracted_claims"] = []
        result["needs_attention"] = False
        result["needs_attention_reason"] = None
    elif verdict == "critical" and result["campaign_action"] == "ignore":
        result["campaign_action"] = "respond"
    if result["needs_attention"] and not (result.get("needs_attention_reason") or "").strip():
        result["needs_attention"] = False
        result["needs_attention_reason"] = None
    return result


def _v2_to_legacy_sentiment(s: str) -> str:
    """Translate v2 sentiment to the old DB enum for backward compat."""
    return {
        "favors_candidate": "positive",
        "favors_opponent": "negative",
        "neutral": "neutral",
    }.get(s, "neutral")


def _v2_action_to_legacy_framing(action: str, sentiment_v2: str) -> str:
    """Derive the legacy `framing` value (used in some places) from the v2 fields."""
    if action == "ignore":
        return "irrelevant"
    if action == "respond":
        return "opponent_news" if sentiment_v2 == "favors_opponent" else "hurts_candidate"
    if action == "amplify":
        return "helps_candidate"
    if action == "review":
        return "helps_candidate" if sentiment_v2 == "favors_candidate" else "hurts_candidate"
    return "background"


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
        from app.services.llm_provider import (
            get_ingestion_provider, MockLLMProvider, OpenAIProvider,
        )
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
        # Pull frames here if caller didn't pass them — the v2 prompt needs
        # the full list to do candidate_new_frame correctly.
        if frames is None:
            frames = db.query(NarrativeFrame).all()

        prompt = _build_prompt(item, ctx, frames=frames)

        # Use OpenAI's JSON mode when we're on an OpenAI-compatible provider.
        # The "json" keyword in the prompt is the trigger word OpenAI requires.
        if isinstance(provider, OpenAIProvider) and hasattr(provider, "_chat"):
            raw = provider._chat(
                prompt, system_prompt=SYSTEM_PROMPT, json_mode=True,
            )
        else:
            raw = provider.complete(prompt)

        if not raw or not raw.strip():
            logger.warning("campaign_analysis: empty response for item %d", item.id)
            return _fallback_result()

        # Strip markdown fences if the model wraps output despite instructions.
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            text = "\n".join(inner).strip()

        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            logger.warning(
                "campaign_analysis: non-dict response for item %d (type=%s)",
                item.id, type(parsed).__name__,
            )
            return _fallback_result()

        # ---- v2 field validation + coercion ----
        verdict = _coerce_enum(parsed.get("verdict"), _VALID_VERDICTS, "irrelevant")
        campaign_action = _coerce_enum(
            parsed.get("campaign_action"), _VALID_CAMPAIGN_ACTIONS, "ignore"
        )
        sentiment_v2 = _coerce_enum(
            parsed.get("sentiment"), _VALID_SENTIMENTS_V2, "neutral"
        )
        source_credibility = _coerce_enum(
            parsed.get("source_credibility"), _VALID_CREDIBILITY, "medium"
        )
        summary = parsed.get("summary")
        if isinstance(summary, str):
            summary = summary.strip() or None
        else:
            summary = None
        needs_attention = bool(parsed.get("needs_attention"))
        needs_attention_reason = (parsed.get("needs_attention_reason") or "")
        if isinstance(needs_attention_reason, str):
            needs_attention_reason = needs_attention_reason.strip() or None
        else:
            needs_attention_reason = None

        # ---- per-claim validation (drops hallucinated quotes) ----
        full_article = item.raw_text or item.summary or item.title or ""
        raw_claims = parsed.get("extracted_claims") or []
        cleaned_claims: list[dict] = []
        if isinstance(raw_claims, list):
            for rc in raw_claims[:20]:  # hard cap matching prompt
                cc = _validate_v2_claim(
                    rc, full_article, frames, ctx["candidate"], ctx["opponents"],
                )
                if cc is not None:
                    cleaned_claims.append(cc)

        dropped = (
            len(raw_claims) - len(cleaned_claims)
            if isinstance(raw_claims, list) else 0
        )

        # ---- assemble result ----
        result = {
            # v2 native fields
            "verdict": verdict,
            "summary": summary,
            "campaign_action": campaign_action,
            "needs_attention": needs_attention,
            "needs_attention_reason": needs_attention_reason,
            "sentiment_new": sentiment_v2,
            "source_credibility": source_credibility,
            "extracted_claims": cleaned_claims,
            # back-compat fields (computed) — keep _rescore_one & ingestion working
            "relevance_score": _VERDICT_TO_SCORE[verdict],
            "relevant": verdict in ("relevant", "critical"),
            "one_sentence": summary,
            "framing": _v2_action_to_legacy_framing(campaign_action, sentiment_v2),
            "reason": needs_attention_reason or "",
            "sentiment": _v2_to_legacy_sentiment(sentiment_v2),
            # Derive opponent_attacks from extracted_claims for back-compat
            "opponent_attacks": [
                {
                    "opponent_name": c["actor_name"],
                    "type": (
                        "attack" if c["claim_type"] in ("personal_attack", "scandal")
                        else "promise" if c["claim_type"] == "promise"
                        else "claim"
                    ),
                    "text": c["quote"],
                }
                for c in cleaned_claims
                if c["actor_role"] == "opponent"
                and c["claim_type"] in ("personal_attack", "scandal", "promise", "policy_position")
            ],
            # Resolved frame IDs across all claims (deduped)
            "frame_matches": list({
                fid for c in cleaned_claims for fid in c["matched_frame_ids"]
            }),
            # Candidate-frame staging payload (consumed by _rescore_one)
            "candidate_new_frames": [
                {
                    "suggested_name": c["candidate_new_frame"]["suggested_name"],
                    "owner_type": c["candidate_new_frame"]["owner_type"],
                    "reasoning": c["candidate_new_frame"]["reasoning"],
                    "evidence_quote": c["quote"],
                }
                for c in cleaned_claims
                if c["candidate_new_frame"] is not None
            ],
            "_used_fallback": False,
        }

        result = _enforce_consistency(result)

        logger.info(
            "campaign_analysis: item=%d verdict=%s action=%s claims=%d "
            "dropped=%d frames=%s new_frames=%d title=%r",
            item.id, verdict, campaign_action, len(cleaned_claims), dropped,
            result["frame_matches"], len(result["candidate_new_frames"]),
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
        # Rate-limit errors must propagate so callers (parallel rescore worker)
        # can wait + retry on the same key.
        from app.services.llm_provider import ProviderRateLimitError
        if isinstance(exc, ProviderRateLimitError):
            raise
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
