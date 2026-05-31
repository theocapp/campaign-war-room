"""
Generates a short LLM-written race-situation memo for the Briefing page.

Two variants:
  - `get_or_generate` (v1 / default): legacy prose memo from article summaries +
    narrative pulse. Returns a string. Time-bound cache (30 min TTL).
  - `get_or_generate_grounded` (v2): NEW grounded memo that takes labeled
    ClaimRecord quotes as input, has the LLM emit `[C1]..[Cn]` citation
    markers when referencing what someone said, and returns a structured
    {text, citations[]} object. Surfaced behind `?v=2` on /briefing/morning.

v2 caching strategy is INPUT-HASHED, not time-bound. Each request rebuilds
the prompt (cheap — one GROUP BY plus a few field substitutions) and hashes
it. If the hash matches what's cached, we return the cached payload and
skip the LLM call entirely — same input would produce semantically the
same memo (with non-deterministic surface variation we explicitly don't
want). New articles whose labeled claims don't fall into top_claims, or
whose summaries don't make the top-5 article list, don't change the hash
either, so trivial ingestion churn doesn't force regeneration.

v2 LLM call deviates from the shared `get_judge_provider()` defaults: it
uses `gpt-4o` at temperature 0.3 by default (vs the judge default of
`gpt-4o-mini` at server temperature ~1.0). Briefing-memo quality is more
sensitive to lead-sentence concreteness and hedge-verb avoidance than the
other judge use cases (variant naming, frame reclassification), and a
stronger model + lower temperature lifts the floor of "first sentence that
comes to mind." Cost impact is bounded by hash-caching — call count is
input-driven, not poll-driven. Override via OPENAI_BRIEFING_MODEL and
OPENAI_BRIEFING_TEMPERATURE env vars.
"""
import hashlib
import logging
import os
import re
import time
from datetime import datetime
from sqlalchemy.orm import Session

from app.services import llm_provider

log = logging.getLogger(__name__)

_TTL_SECONDS = 1800  # 30 minutes — v1 only. v2 uses input-hash matching.
_cache: dict[int, dict] = {}  # keyed by campaign_id — v1 only
_cache_v2: dict[int, dict] = {}  # keyed by campaign_id — v2 grounded memo

_briefing_llm_singleton: "llm_provider.BaseLLMProvider | None" = None


def _get_briefing_llm() -> tuple["llm_provider.BaseLLMProvider", float | None]:
    """Return (llm, temperature) for the v2 grounded memo.

    Prefers a fresh OpenAI provider configured with `gpt-4o` at temperature
    0.3 — both override the shared `get_judge_provider()` defaults.
    Falls back to the shared judge provider (and its built-in Groq/Gemini
    fallback chain) if OPENAI_API_KEY isn't configured.

    Singletons the OpenAI primary so we don't re-instantiate per request.
    The fallback judge provider is its own singleton inside llm_provider.
    """
    global _briefing_llm_singleton
    if _briefing_llm_singleton is not None:
        return _briefing_llm_singleton, _briefing_temperature()

    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if openai_key:
        model = os.environ.get("OPENAI_BRIEFING_MODEL", "gpt-4o").strip()
        try:
            _briefing_llm_singleton = llm_provider.OpenAIProvider(api_key=openai_key, model=model)
            log.info("briefing v2 LLM: OpenAI %s at temperature %s", model, _briefing_temperature())
            return _briefing_llm_singleton, _briefing_temperature()
        except Exception as e:
            log.warning("briefing v2 LLM: OpenAI init failed (%s) — falling back to judge provider", e)

    # No OpenAI configured → fall back to the shared judge chain. We can't
    # set temperature on a composite provider reliably, so pass None and
    # accept the judge's server default.
    log.info("briefing v2 LLM: using shared judge provider (no OPENAI_API_KEY)")
    fallback = llm_provider.get_judge_provider()
    _briefing_llm_singleton = fallback
    return fallback, None


def _briefing_temperature() -> float:
    try:
        return float(os.environ.get("OPENAI_BRIEFING_TEMPERATURE", "0.3"))
    except ValueError:
        return 0.3


def _complete_with_temperature(
    llm: "llm_provider.BaseLLMProvider", prompt: str, temperature: float | None
) -> str:
    """Call the LLM, passing temperature through if (a) one was requested
    and (b) the provider is an OpenAIProvider that supports it via _chat.
    Other providers (or no-temperature calls) get the abstract complete().
    """
    if temperature is not None and isinstance(llm, llm_provider.OpenAIProvider):
        return llm._chat(prompt, temperature=temperature)
    return llm.complete(prompt)


def _campaign_key(campaign) -> int:
    return getattr(campaign, "id", None) or 0


def invalidate(campaign=None):
    """Clear the v1 memo cache. v2 is NOT cleared here — it uses input-hash
    matching (see get_or_generate_grounded), so an ingestion that didn't
    change the labeled-claim pool or top-5 articles wouldn't change its
    prompt either and shouldn't force a wasted LLM regeneration. v2 stays
    "valid until input changes" without any explicit invalidation; the
    next request rebuilds the prompt, hashes it, and re-uses the cached
    payload when the hash still matches.

    If campaign is provided, only that campaign's v1 entry is cleared.
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


def _strip_recency_fillers(text: str) -> str:
    """Remove vague timing fillers the LLM sometimes inserts despite explicit
    prompt bans. Operates on the body text after generation, BEFORE the
    citation-marker numbering pass, so we don't disturb [Cn] markers (the
    patterns below never match inside a `[C\\d+]` token).

    Why post-process instead of relying on the prompt: after many rounds of
    prompt tuning, "recent" / "recently" still slip ~50% of the time. The
    rule "use a real date or omit the time reference" is invariant — these
    words shouldn't appear regardless — so a deterministic strip is the
    right tool. We patch adjacent context (articles, capitalization) so
    grammar stays intact.

    Patterns handled (case-insensitive):
      "the recent X"              → "the X"
      "X's recent Y"              → "X's Y"
      "a recent X"                → "the X"        (article swap)
      "Recently, X..."            → "X..."        (sentence-initial, recapitalize)
      "...Recently, X..."         → "...X..."     (mid-sentence post-period)
      "Y recently <verb>"         → "Y <verb>"
      "in recent days"            → ""
      "this past week"            → "this week"
      Any remaining "recent X"    → "X"
    """
    s = text
    # "the recent X" → "the X"
    s = re.sub(r"\bthe\s+recent\s+", "the ", s, flags=re.IGNORECASE)
    # "X's recent Y" → "X's Y"
    s = re.sub(r"(\w)'s\s+recent\s+", r"\1's ", s, flags=re.IGNORECASE)
    # "a recent X" → "the X"
    s = re.sub(r"\b[Aa]\s+recent\s+", "the ", s)
    # Sentence-initial "Recently, X" → "X" with caps fix
    s = re.sub(
        r"^Recently,?\s+(\w)",
        lambda m: m.group(1).upper(),
        s,
    )
    s = re.sub(
        r"(\.\s+)Recently,?\s+(\w)",
        lambda m: m.group(1) + m.group(2).upper(),
        s,
    )
    # Comma-wrapped ", recently, " (mid-sentence interjection) → " "
    s = re.sub(r",\s*recently\s*,\s*", " ", s, flags=re.IGNORECASE)
    # "recently <verb>" → "<verb>"
    s = re.sub(r"\brecently\s+(?=\w)", "", s, flags=re.IGNORECASE)
    # "in recent days" → ""
    s = re.sub(r",?\s*\bin\s+recent\s+days\b,?\s*", " ", s, flags=re.IGNORECASE)
    # "this past week" → "this week"
    s = re.sub(r"\bthis\s+past\s+week\b", "this week", s, flags=re.IGNORECASE)
    # Catch any remaining "recent X" → "X"
    s = re.sub(r"\brecent\s+(?=\w)", "", s, flags=re.IGNORECASE)
    # Normalize whitespace
    s = re.sub(r"\s+", " ", s).strip()
    # Tighten spacing before punctuation
    s = re.sub(r"\s+([,.;:])", r"\1", s)
    return s


def get_or_generate_grounded(
    db: Session,
    articles: list[dict],
    campaign,
    opponents: list,
    top_claims: list[dict],
) -> dict | None:
    """Cached entry for the grounded v2 memo.

    Returns {headline, text, citations, sources_used, input_hash,
    overridden_headline, overridden_text, overridden_by, overridden_at}
    or None on failure / no data. `headline` is a punchy 1-line LLM-generated
    summary (may be None if the model didn't follow the HEADLINE: line format).
    `text` is prose with [C1]..[Cn] markers substituted in. `citations` maps
    each marker back to a claim_id + article_id. `sources_used` is the input
    `top_claims` list (echoed back so the frontend can render the audit
    "Sources Used" expandable). `input_hash` is the prompt hash this memo
    was generated against; the frontend sends it back when an admin saves
    an override so the override is pinned to the inputs it was edited from.
    `overridden_*` flag which fields the admin manually edited.

    Caching is INPUT-HASHED: we build the prompt first, hash it, and
    compare against the prior hash for this campaign. Same hash → return
    the cached payload (no LLM call, no cost). Different hash → run the
    LLM. This eliminates the cosmetic "memo changed but the news didn't"
    regenerations that a pure time-bound TTL produces.

    Admin manual overrides: if a TextOverride row exists for
    `briefing.memo.headline` or `briefing.memo.text` AND its input_hash
    matches the current prompt_hash, the override value is substituted
    into the payload. If the hash differs (news has moved on), the stale
    row is deleted — the override is auto-cleared on material change, as
    designed.
    """
    if not top_claims and not articles:
        log.info("briefing_grounded: no claims or articles — skipping")
        return None

    key = _campaign_key(campaign)
    prompt = _build_grounded_prompt(db, articles, campaign, opponents, top_claims)
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    entry = _cache_v2.get(key)
    if entry and entry.get("prompt_hash") == prompt_hash:
        log.info("briefing_grounded: prompt hash match — reusing cached memo (no LLM call)")
        return _apply_briefing_overrides(db, entry["payload"], prompt_hash)

    llm, temperature = _get_briefing_llm()
    payload = _run_grounded_llm(llm, prompt, top_claims, temperature=temperature)
    if payload:
        _cache_v2[key] = {
            "payload": payload,
            "generated_at": time.time(),
            "prompt_hash": prompt_hash,
        }
        return _apply_briefing_overrides(db, payload, prompt_hash)
    return payload


# Override keys used by the briefing memo. Single source of truth so the
# admin route can validate against the same allow-list.
BRIEFING_OVERRIDE_KEYS = {"briefing.memo.headline", "briefing.memo.text"}


def _apply_briefing_overrides(db: Session, payload: dict, prompt_hash: str) -> dict:
    """Layer admin manual overrides on top of the LLM-generated payload.

    Mutates a copy of `payload` (not in-place — the caller may have cached
    the original and we don't want to corrupt that cache with override
    state, which is per-request). Always stamps `input_hash` so the
    frontend can echo it back on save.

    For each override key whose `input_hash` matches the current
    `prompt_hash`, the stored value replaces the corresponding field in
    the payload and the `overridden_*` flag is set. For each override
    whose `input_hash` does NOT match, the row is deleted — the news has
    moved on, the override is by design auto-cleared on material change.
    Multiple overrides may share an `overridden_by` / `overridden_at`
    timestamp; we surface the most recent edit so the UI can show one
    "Edited by X · 5m ago" indicator.
    """
    from app.models import TextOverride

    result = dict(payload)
    result["input_hash"] = prompt_hash
    result.setdefault("overridden_headline", False)
    result.setdefault("overridden_text", False)
    result.setdefault("overridden_by", None)
    result.setdefault("overridden_at", None)

    rows = (
        db.query(TextOverride)
        .filter(TextOverride.key.in_(list(BRIEFING_OVERRIDE_KEYS)))
        .all()
    )
    if not rows:
        return result

    most_recent: TextOverride | None = None
    for row in rows:
        if row.input_hash != prompt_hash:
            # Stale — the underlying inputs have changed since the admin
            # edited. Auto-clear, per the persistence contract.
            log.info(
                "briefing_grounded: auto-clearing stale override key=%s "
                "(stored hash %s != current %s)",
                row.key, (row.input_hash or "")[:8], prompt_hash[:8],
            )
            db.delete(row)
            continue
        if row.key == "briefing.memo.headline":
            result["headline"] = row.value
            result["overridden_headline"] = True
        elif row.key == "briefing.memo.text":
            result["text"] = row.value
            result["overridden_text"] = True
        if most_recent is None or (row.updated_at or row.created_at or datetime.min) > (
            most_recent.updated_at or most_recent.created_at or datetime.min
        ):
            most_recent = row

    if most_recent is not None:
        result["overridden_by"] = most_recent.created_by_name
        ts = most_recent.updated_at or most_recent.created_at
        result["overridden_at"] = ts.isoformat() if ts else None

    # Commit the auto-clear deletes (if any). If nothing was deleted this
    # is a no-op commit.
    db.commit()
    return result


def _build_grounded_prompt(
    db: Session,
    articles: list[dict],
    campaign,
    opponents: list,
    top_claims: list[dict],
) -> str:
    """Construct the exact prompt string sent to the LLM. Pure function of
    its inputs — same inputs always produce byte-identical output. This is
    the determinism the input-hash cache in `get_or_generate_grounded`
    depends on: hash this string and compare against the prior hash.

    Includes a CACHE-key prompt-version suffix that you can bump when the
    prompt template itself changes, so deployed servers don't keep serving
    memos against the previous template after a code update.
    """
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

    return f"""You are a senior political campaign analyst writing the opening memo for a daily briefing.

RACE: {candidate} vs {opponent_names} — {office}, {district}
CANDIDATE MESSAGE: {message or "(not set)"}

RECENT RELEVANT ARTICLES (last 48 hours):
{articles_block}

NARRATIVE MOMENTUM (week-over-week mention counts):
{pulse_block}

RECENT QUOTES (use these to ground assertions about what people said or did):
{claims_block}

OUTPUT FORMAT (strict):
Line 1 must be `HEADLINE: <punchy 1-line summary>` — see HEADLINE RULES below.
Line 2 must be blank.
Line 3+ is the memo body (2-3 sentences, with [CN] citation markers).

HEADLINE RULES:
- Write the headline as a sharper newsworthy TITLE — descriptive, not
  directive. You are informing the campaign manager, not telling her
  what to do. Action recommendations live in the body memo, never
  the headline.
- 8-18 words. One line, no period at the end.
- The headline MUST surface the biggest tactical development this
  week. Identify it by scanning NARRATIVE MOMENTUM for the frame with
  the LARGEST week-over-week delta (positive OR negative). That is
  the lead.

PREFERRED HEADLINE STRUCTURE — "trend + cause + magnitude":

   "[Subject]'s [frame-level subject] [trend verb] with [specific cause
   from RECENT QUOTES], [magnitude or significance clause]"

   GOLD EXAMPLE: "Bresnahan's local engagement surges with BUILD Act
                 promotion, +13 mentions this week"

   Why this works: the FRAME (local engagement) tells the staffer WHAT
   trend is happening at the tracked-narrative level. The CAUSE (BUILD
   Act) grounds it in a specific event from QUOTES, so it's not
   abstract. The MAGNITUDE (+13 mentions) makes the trend's size
   concrete and scannable.

GROUNDING REQUIREMENTS — this is what makes the preferred structure
work. Every headline MUST have:

   (a) A frame-level subject — usually drawn from NARRATIVE MOMENTUM
       (e.g. "local engagement," "anti-corruption message," "stock
       trades," "healthcare record"). Pure frame names are OK ONLY
       when conditions (b) and (c) are also satisfied.
   (b) A specific anchoring cause — from RECENT QUOTES — a bill name
       (BUILD Act), a vote, an event (Scranton rally), a person
       (Shapiro), an organization (Pipefitters Local 524, NRCC),
       or a quoted line. This is the "this is what actually happened
       this week" part.
   (c) A magnitude or significance clause — drawn from MOMENTUM
       numbers or QUOTES context: "+13 mentions this week," "biggest
       of cycle," "first union endorsement," "dropped from 13 to 3,"
       "third week in a row," "ahead of Friday's FEC deadline."

   Trend verbs like "surges," "fades," "drops," "jumps," "wanes"
   are PERMITTED in this structure because they're paired with a
   specific cause and magnitude. They're only banned when used
   standalone with no grounding.

   If you have a frame-level subject + trend verb but NO specific
   cause and NO magnitude, you don't have a headline — you have an
   abstract observation. Add the grounding, or rewrite around a
   specific event.

WHEN TO USE A SINGLE-EVENT HEADLINE INSTEAD — if QUOTES has a single
dominant event AND that event IS itself the news (not just a cause
behind a trend), it's fine to lead with the event:

   "Pipefitters Local 524 sealed Cognetti's first union endorsement of cycle"
   "Bresnahan voted for Medicaid cut after $300K stock sale"
   "Shapiro headlines Cognetti's Wyoming rally, her biggest validator yet"

   Use this shape only when the event is bigger than any frame-level
   trend that week. Otherwise prefer the trend + cause + magnitude
   structure, since it tells the staffer both WHAT is happening at
   the tracked level AND why.

BANNED — no matter which structure:
- Directive phrasing: "lead with X," "pivot to Y," "drop Z frame,"
  "hit on X," "match the attack," "hold for later." These belong in
  the body, not the headline.
- Pure vague observation with no grounding: "gains traction,"
  "loses traction," "gains momentum," "dominates the news cycle,"
  "appears to," "seems to," "sought," "looks to," "takes shape."
  These are banned because they're abstract about an unspecified
  thing — they have no (b) or (c). If you have a specific cause
  and magnitude, you don't need them anyway.
- Citation markers — `[CN]` lives only in the body.

ONE development per headline. Semicolons join two clauses ONLY if
both halves are tactically linked (cause→effect, attack→counter,
A neutralizes B) AND adding the second half is sharper than running
just the bigger half alone. Do NOT use a semicolon to glue two
unrelated news items together.

GOOD EXAMPLES (trend + cause + magnitude, primary structure):
  "Bresnahan's local engagement surges with BUILD Act promotion, +13 mentions this week"
  "Bresnahan's farmer-support frame jumps with Local Farmers Act push, 0 → 12 mentions"
  "Cognetti's anti-corruption frame dropped from 13 mentions to 3 as NRCC attack lands"
  "Bresnahan's healthcare record cools to 1 mention as Pipefitters endorsement lands"

GOOD EXAMPLES (single dominant event, secondary structure):
  "Pipefitters Local 524 sealed Cognetti's first union endorsement of cycle"
  "Shapiro headlines Cognetti's Wyoming rally, her biggest validator yet"
  "Bresnahan voted for Medicaid cut after $300K stock sale"

BAD EXAMPLES (and why):
  "Lead with Pipefitters endorsement" — directive
  "A look at this week's PA-08 race" — says nothing
  "Bresnahan gains momentum on infrastructure" — banned phrase + no
    specific cause + no magnitude
  "Bresnahan's local engagement surges" — frame + trend verb but NO
    cause and NO magnitude. The grounding pieces are missing — this
    is the failure mode the preferred structure exists to prevent.
  "Cognetti's anti-corruption message loses traction" — same:
    frame name + trend verb with no cause, no magnitude.
  "Bresnahan's infrastructure push gains traction; Cognetti faces
    workday campaign scrutiny" — banned phrase, "workday campaign
    scrutiny" is meaningless, two unrelated items semicolon-glued.

Now write a TIGHT 2-3 sentence body for {candidate}'s campaign manager.

LEAD WITH: the development that most changes {candidate}'s tactical position this week. The bar is "would the campaign manager change a decision today because of this?" If yes, lead with it. If not, it is not the lead.

FOLLOW WITH: the specific opening, threat, or action that development points to. Format the implication as "{opponent_names} did X, which strengthens/exposes Y, so {candidate}'s position is Z" — concrete and directional, not observational.

If the input genuinely contains no high-leverage development today (quiet news, flat coverage, no labeled quotes worth grounding), the body says so in one short sentence and stops. The HEADLINE in that case should be honest: e.g. "No high-leverage developments in PA-08 this cycle."

LENGTH: maximum 100 words. Aim for 60-80. Each sentence should be 8-18 words. If a sentence runs over 22 words you have packed too much — split it or cut.

WRITE SHORT AND CONCRETE:
- Short sentences. One claim per sentence. Punch over prose.
- Concrete nouns over abstract ones: "the BUILD America 250 Act vote" not "infrastructure engagement"; "Cognetti's mayoral payroll record" not "her record on local issues"; "the maternity-leave letter" not "the personal record narrative."
- Verbs that name an action: voted, signed, endorsed, defended, attacked, pulled out, hosted, refused. NOT: positions, highlights, shifts, poses, could overshadow, generates momentum, strengthens his image.
- No hedging — "may," "could," "appears to," "risks" — either the input supports the claim or it doesn't. If it doesn't, drop the claim.
- No qualifier-laden constructions: "X, who recently focused on Y, has been generating Z" → break into separate sentences or cut.
- Active voice. Subject does verb to object.
- NO RESTATEMENT BRIDGES. Sentence N+1 must NOT begin by summarizing
  sentence N — the reader just read it. Each sentence advances; none
  recaps. Move from development → implication → action directly. Banned
  sentence-openers when they merely re-package the prior sentence:
    "This surge highlights..."
    "This shift shows / reveals / points to..."
    "This development indicates / underscores..."
    "Such X demonstrates / signals..."
    "The trend suggests..."
    "These mentions reflect..."
  GOOD shape (no bridge):
    "Bresnahan's BUILD Act push drove +13 mentions this week [Cx]. This
     could overshadow Cognetti's anti-corruption frame, which dropped
     from 13 mentions last week to 3 this week [Cy]."
  BAD shape (recap bridge):
    "Bresnahan's BUILD Act push drove +13 mentions this week [Cx]. This
     surge highlights his focus on infrastructure, which could overshadow
     Cognetti's anti-corruption frame, which dropped from 13 to 3 [Cy]."
  Note "This could overshadow..." is fine — "This" refers to the prior
  development; the sentence carries new information (the implication).
  What's banned is the SUMMARY clause attached to the pronoun.

CITATION RULES (strict):
- Aim for 2-4 [CN] citations in the memo. Citations are the credibility — a memo with quoted evidence beats smooth prose without it.
- For every factual claim about what someone said or did, scan RECENT QUOTES for a matching one and cite it. If no quote backs it, the claim probably isn't strong enough for this memo — drop it.
- Use the exact verbatim quote (or its sharpest phrase) and append [CN] immediately after.
- Prefer the most specific available quote: a named bill, a named action, or a specific commitment beats a general framing quote.
- Do NOT paraphrase a quote — copy it exactly.
- Do NOT invent [CN] markers that aren't in the list.

STYLE RULES:
- Be specific — name events, people, bills, votes, and implications.
- Connect every point back to the race and {candidate}'s position.
- Do not list articles, use bullet points, or enumerate multiple developments — pick ONE and commit.
- Do not start with "Good morning" or any greeting.
- Do not say "currently," "right now," or "today" — implicit; wastes words.
- Do not mention scores, labels, or [CN] markers as concepts — just embed [CN] inline after a quote.

TIMING & MAGNITUDE PRECISION (strict — campaign managers operate on
days, not vague references):
- Every magnitude number MUST include its time window. "15 mentions"
  alone is ambiguous — could be this week, this month, or all time.
  Write "15 mentions this week," "15 mentions in 7 days," or "15
  mentions since primary." Bare numbers are banned.
- For week-over-week comparisons drawn from NARRATIVE MOMENTUM, use
  this explicit shape: "from X last week to Y this week" (or
  "dropped from X mentions last week to Y this week"). The MOMENTUM
  block IS this-week-vs-last-week data, so you have those windows.
  Never write "from X to Y" without naming the windows.
- The date next to each RECENT QUOTE (e.g. "2026-05-26") is the
  ARTICLE'S publish date, NOT necessarily the event date. Articles
  often re-report things that happened earlier (a May 26 article
  about an endorsement that was announced May 20).
- Use a specific date in the memo ONLY when the QUOTE TEXT itself
  contains a temporal marker (e.g. "on Tuesday," "last week,"
  "May 22," "Friday's vote"). Lift that timing from the quote.
  Do NOT use the article publish_at as a stand-in for "when this
  happened."
- For momentum-driven claims (the MOMENTUM block confirms it's
  this-week activity), default to "this week" framing — that's a
  reliable signal regardless of the underlying article date.
- BANNED filler phrases when no real date is available: "recent,"
  "recently," "in recent days," "this past week," "just" (as in
  "just endorsed"). If you can't anchor the timing precisely, omit
  the time reference entirely rather than fudge it.
    BAD:  "the recent Pipefitters Local 524 endorsement"
    GOOD: "the Pipefitters Local 524 endorsement"
    (No date in the quote text, so no time reference is better than
    a vague one. The reader knows it's from RECENT QUOTES; that's
    enough context.)
    BAD:  "Cognetti's recent commitment to the PRO Act"
    GOOD: "Cognetti's PRO Act commitment"
    BAD:  "Bresnahan recently voted for Medicaid cuts"
    GOOD: "Bresnahan's Medicaid cut vote"
    (Or, if the quote text says "voted last summer," then:
     "Bresnahan's summer Medicaid cut vote" — anchor lifted from quote.)

EXAMPLES — study these before writing. [Cx], [Cy], [Cz] are PLACEHOLDER markers used only in the examples below. In YOUR memo use real [CN] markers from the RECENT QUOTES list above.

EXAMPLE A — STRONG (write memos like this):
Illustrative input quotes (NOT your real input):
  [Cx] Bresnahan: "The BUILD America 250 Act is a major win for Northeastern Pennsylvania."
  [Cy] "Pipefitters and Plumbers Local 524 endorses Cognetti."
  [Cz] Cognetti: "I will fight to pass the PRO Act and protect workers' rights."

Output:
HEADLINE: Bresnahan runs BUILD Act vote as NEPA earned media; Pipefitters endorse Cognetti

Bresnahan is running his BUILD Act vote [Cx] as deliverable-for-NEPA earned media this week. Pipefitters Local 524 endorsed Cognetti [Cy], giving her a labor-anchored counter she has not yet used. Lead Tuesday's messaging with the endorsement plus the PRO Act commitment [Cz] — anti-corruption is the wrong contrast this week.

Why it works: HEADLINE names the two specific developments. Body has three citations, one per sentence. Action verbs only ("is running," "endorsed," "Lead"). Specific recommended action with a timeframe.

EXAMPLE B — STRONG:
Illustrative input quotes (NOT your real input):
  [Cx] Cognetti: "I won't stand by while a corrupt Washington prioritizes special interests over NEPA families."
  [Cy] NRCC: "Political opportunist Paige Cognetti is the poster child of political corruption."
  [Cz] "Governor Shapiro endorses Scranton Mayor Paige Cognetti at Wyoming campaign event."

Output:
HEADLINE: Cognetti and NRCC corruption attacks cancel; Shapiro endorsement is cleaner ground

Cognetti's "corrupt Washington" framing [Cx] and the NRCC's "political corruption" attack on Cognetti [Cy] both landed this week and cancel. Match the personal attack with a Bresnahan-specific record claim, or pivot. The Shapiro endorsement [Cz] is the cleaner ground for the next 48 hours.

Why it works: HEADLINE surfaces the tactical tension (attacks cancel) AND the pivot. Body has three citations. Tactical fork ("match X or pivot to Y") forces a campaign decision.

EXAMPLE C — WEAK (do NOT write like this):
Output:
HEADLINE: Bresnahan's transportation focus shifts the race narrative

Bresnahan's significant focus on local transportation investments, highlighted by recent article mentions, positions him as a proactive leader in NEPA, which risks overshadowing Cognetti's anti-corruption narrative. She should now pivot her messaging to emphasize how her commitment translates into tangible benefits for families.

Why it fails: HEADLINE is vague — "transportation focus" names no bill, vote, or action; "shifts the race narrative" is hedged observation, not a tactical development. Body has zero citations even though quotes exist that would back the assertions. "Significant focus" / "positions him" / "risks overshadowing" are vague observational verbs. "Tangible benefits for families" is fluff with no named issue. First sentence is 30+ words.

Now write the briefing for the REAL input above. Use real [CN] markers from RECENT QUOTES — NOT [Cx]/[Cy]/[Cz], those were placeholders. Start with `HEADLINE: ` on line 1, blank line on 2, body from line 3. No preamble, no "Memo:" prefix."""


def _run_grounded_llm(
    llm, prompt: str, top_claims: list[dict],
    temperature: float | None = None,
) -> dict | None:
    """Call the LLM with the pre-built prompt and post-process its [CN]
    citation markers into a structured payload. Pure of any DB / cache
    state — the only reason it lives in this module rather than inline in
    `get_or_generate_grounded` is to keep that function focused on the
    cache-hit / cache-miss decision.

    `temperature` is passed through only when the provider supports it
    (currently: OpenAIProvider). On other providers it's silently ignored
    and we fall back to the abstract complete() call.
    """
    try:
        raw = _complete_with_temperature(llm, prompt, temperature).strip()
    except Exception as e:
        log.warning("briefing_grounded generation failed: %s", e, exc_info=True)
        return None

    if not raw:
        log.warning("briefing_grounded: LLM returned empty (provider=%s)", type(llm).__name__)
        return None

    # Split out the headline from the body. Expected format is
    #   HEADLINE: <one line>
    #   <blank>
    #   <body...>
    # If the model doesn't emit a HEADLINE: line we fall back to
    # headline=None and treat the whole output as the body — the frontend
    # renders body-only gracefully in that case.
    headline: str | None = None
    text = raw
    if raw.upper().startswith("HEADLINE:"):
        first_nl = raw.find("\n")
        if first_nl == -1:
            # Just a headline with no body — unusual but handle it.
            headline = raw[len("HEADLINE:"):].strip() or None
            text = ""
        else:
            headline = raw[len("HEADLINE:"):first_nl].strip() or None
            text = raw[first_nl + 1:].lstrip("\n").strip()
        # Defensive: trim if the model produced a too-long headline.
        if headline and len(headline) > 140:
            log.info("briefing_grounded: headline length %d > 140, truncating", len(headline))
            headline = headline[:140].rsplit(" ", 1)[0] + "…"
        # Strip any accidental [CN] markers from the headline — those
        # belong to the body and confuse the citation post-process.
        if headline:
            headline = _CITATION_PATTERN.sub("", headline).strip()
    else:
        log.warning(
            "briefing_grounded: model did not emit HEADLINE: line — falling back to body-only "
            "(provider=%s, first 60 chars=%r)",
            type(llm).__name__, raw[:60],
        )

    # Strip vague recency fillers ("recent" / "recently" / etc.) from the
    # body before citation parsing. Headline goes through the same scrub
    # so we keep the two consistent. See _strip_recency_fillers for the
    # rationale (prompt-only enforcement was unreliable; this is the
    # invariant-rule layer).
    if text:
        text = _strip_recency_fillers(text)
    if headline:
        headline = _strip_recency_fillers(headline)

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
        "headline": headline,
        "text": text,
        "citations": citations,
        "sources_used": top_claims,
    }
