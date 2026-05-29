"""
Per-article perspective classification (V13.21).

Returns one of three perspectives per article:
  - 'pro_candidate'  — article frames the story favorably for the candidate
  - 'pro_opponent'   — frames it favorably for the opponent
  - 'neutral'        — straight reporting, no clear lean

WHY this exists
---------------
Currently a dot inherits its parent narrative's `owner_type` — meaning
every article extract in "Bresnahan's Stock Trades" (a Cognetti-campaign
attack frame) gets colored the same, even if some of those articles are
Bresnahan defending himself, Cognetti's own press release, or a neutral
AP wire. Per-article perspective lets dots reflect WHO IS FRAMING the
story, not just WHO OWNS THE NARRATIVE.

Cascading pipeline (cheapest → most expensive)
-----------------------------------------------
Phase 0 — existing high-confidence labels (FREE)
  SourceItem.source_owner_type is already populated for ~1000 articles
  during ingestion. We map those directly:
    candidate_statement     → pro_candidate (high confidence)
    opponent_statement      → pro_opponent  (high confidence)
    party_committee_statement → look up the party from source_name
    outside_group_statement → look up the group from source_name
    media                   → neutral
    community/manual        → fall through (uninformative label)
    unclear                 → fall through (default)

Phase 1 — outlet bias (FREE, hardcoded)
  A small curated list of well-known partisan outlets. Right-leaning →
  perspective favors the Republican. Left-leaning → favors the Democrat.
  Maps to pro_candidate / pro_opponent based on the campaign's party.

Phase 2 — attribution heuristic (FREE, regex)
  Detect direct quote attribution in extracted_text:
    "Cognetti said", "Cognetti's spokesperson", "Cognetti's campaign"
      → pro_candidate (the candidate is speaking)
    "Bresnahan said", "Bresnahan's office"
      → pro_opponent (the opponent is speaking)
  When both appear, the FIRST speaker wins.

Phase 3 — LLM fallback (small cost, gpt-4o-mini)
  Implemented as a stub for now — returns 'neutral' + low confidence.
  Caller can plug in an LLM-based classifier when needed.

Confidence levels
-----------------
  high   — Phase 0 with explicit statement labels, or Phase 1 campaign domains
  medium — Phase 1 partisan outlets, Phase 2 attribution matches
  low    — Phase 3 LLM result (or fallback when LLM unavailable)

Output contract
---------------
classify_perspective(item, db) returns a PerspectiveResult dict with:
  perspective: 'pro_candidate' | 'pro_opponent' | 'neutral'
  method:      'existing' | 'outlet_bias' | 'attribution' | 'llm' | 'fallback'
  confidence:  'high' | 'medium' | 'low'
  reason:      one-line explanation (for debugging / manual inspection)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.models import CampaignConfig, Opponent, SourceItem

log = logging.getLogger(__name__)


# ── Constants ──────────────────────────────────────────────────────────────

# Well-known partisan outlets. Lower-case domain matching against the
# article's source_url. Curated conservatively — we'd rather miss a
# classification than mis-classify.
RIGHT_LEANING_DOMAINS: set[str] = {
    # National conservative
    "foxnews.com", "foxbusiness.com",
    "breitbart.com",
    "nationalreview.com",
    "washingtonexaminer.com",
    "washingtontimes.com",
    "thefederalist.com",
    "dailywire.com",
    "redstate.com",
    "townhall.com",
    "newsmax.com",
    "theblaze.com",
    "westernjournal.com",
    "pjmedia.com",
    "freebeacon.com",
    # Republican party / committee
    "nrcc.org", "rnc.org", "gop.com",
    # Conservative tabloids
    "nypost.com",
}

LEFT_LEANING_DOMAINS: set[str] = {
    # National progressive
    "jacobin.com",
    "motherjones.com",
    "thenation.com",
    "alternet.org",
    "dailykos.com",
    "huffpost.com",
    "commondreams.org",
    "truthout.org",
    "democracynow.org",
    "rawstory.com",
    "salon.com",
    "vox.com",   # arguably mixed; lean-left enough for prior
    "slate.com",
    "msnbc.com",
    # Democratic party / committee
    "dccc.org", "democrats.org", "dnc.org",
    # Progressive advocacy
    "emilyslist.org",
    "moveon.org",
}

# Domains that DEFINITIVELY belong to a campaign / candidate. These
# override all other signals — a press release from cognettiforcongress.com
# is a Cognetti statement, period.
# Populated per-campaign at classifier construction time (see _build_campaign_domain_map).

# Outside-group → perspective mapping. Hardcoded for the most common
# campaign-adjacent groups. Used when source_owner_type='outside_group_statement'.
OUTSIDE_GROUPS_LEFT: set[str] = {
    "emily", "emilyslist", "emily's list",
    "moveon", "indivisible",
    "everytown", "sierra club",
    "planned parenthood",
    "league of conservation voters",
    "human rights campaign",
    "afl-cio",
    "service employees international union", "seiu",
}
OUTSIDE_GROUPS_RIGHT: set[str] = {
    "club for growth",
    "americans for prosperity",
    "national rifle association", "nra",
    "americans for tax reform",
    "heritage", "heritage foundation",
    "freedomworks",
    "national right to life",
    "susan b. anthony",
    "concerned women for america",
}


# ── Attribution patterns ───────────────────────────────────────────────────

# Build attribution regex from name tokens. Speaker-verb patterns that
# strongly indicate the named person is the source of the quote.
# Active-voice only — passive voice ("X has been criticized by Y") would
# require parsing direction, so we skip those patterns to avoid mis-classifying.
SPEAKER_VERBS = [
    "said", "says", "stated", "told", "wrote", "tweeted", "posted",
    "announced", "declared", "argued", "claimed", "responded",
    "explained", "added", "asserted", "noted", "remarked",
]
# V13.22 audit fix #4 — Attribution polarity:
# Possessives were too brittle. "Bresnahan's office" matched in articles
# like "Protesters delivered petition to Bresnahan's office" — which is
# *anti*-Bresnahan but attribution would tag it pro_opponent.
# Dropped ambiguous ones: "office", "team", "aide", "campaign" (the
# campaign can be the *subject* of criticism, not just the speaker).
# Kept only possessives that unambiguously mark someone as a SOURCE of speech.
SPEAKER_POSSESSIVES = [
    "spokesperson",
    "communications director",
    "press secretary",
    "campaign manager",
]


def _attribution_pattern(tokens: list[str]) -> re.Pattern:
    """Build a regex that matches "<token> <speaker_verb>" or
    "<token>'s <possessive>" for any of the given name tokens.

    The verb / possessive must follow within a short word window so we
    don't accidentally match "John, who said that Mary" → attributing
    the quote to John when it could actually be about Mary.
    """
    if not tokens:
        return re.compile(r"(?!x)x")  # never matches
    tok_alt = "|".join(re.escape(t) for t in tokens)
    verb_alt = "|".join(re.escape(v) for v in SPEAKER_VERBS)
    poss_alt = "|".join(re.escape(p) for p in SPEAKER_POSSESSIVES)
    # Patterns: "Cognetti said" OR "Cognetti's spokesperson"
    pattern = (
        rf"\b(?:{tok_alt})"
        rf"(?:\s+(?:{verb_alt})\b"  # bare speaker verb
        rf"|['’]s\s+(?:{poss_alt})\b)"  # possessive + role
    )
    return re.compile(pattern, re.IGNORECASE)


# ── Types ──────────────────────────────────────────────────────────────────

@dataclass
class PerspectiveResult:
    perspective: str            # 'pro_candidate' | 'pro_opponent' | 'neutral'
    method: str                 # 'existing' | 'outlet_bias' | 'attribution' | 'llm' | 'fallback'
    confidence: str             # 'high' | 'medium' | 'low'
    reason: str                 # human-readable for manual inspection


# ── Helpers ────────────────────────────────────────────────────────────────

def _name_tokens(full_name: str) -> list[str]:
    """Mirror of subject_classifier._name_tokens."""
    parts = (full_name or "").strip().split()
    if not parts:
        return []
    out = [full_name.strip()]
    if len(parts) > 1:
        out.append(parts[-1])
        out.append(parts[0])
    return out


def _extract_domain(url: Optional[str]) -> Optional[str]:
    """Lowercase bare domain from a URL, stripping 'www.' prefix."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host or None
    except Exception:
        return None


def _party_to_perspective(party: Optional[str], candidate_party: str) -> str:
    """Convert a party label (Democrat/Republican/Independent/etc.) into
    pro_candidate / pro_opponent / neutral based on the candidate's party.

    party: the party of the OUTLET / GROUP being classified.
    candidate_party: the candidate's own party (normalized).
    """
    if not party:
        return "neutral"
    p = party.strip().lower()
    cp = (candidate_party or "").strip().lower()
    # Normalize common variants.
    aliases = {
        "democrat": "d", "democratic": "d", "dem": "d", "d": "d",
        "republican": "r", "rep": "r", "gop": "r", "r": "r",
        "independent": "i", "i": "i",
        "libertarian": "l", "green": "g",
    }
    p_key = aliases.get(p, p)
    cp_key = aliases.get(cp, cp)
    if not p_key or not cp_key:
        return "neutral"
    if p_key == cp_key:
        return "pro_candidate"
    # Different party → favors opponent (in a 2-party race; third-party
    # cases stay neutral).
    if p_key in ("d", "r") and cp_key in ("d", "r"):
        return "pro_opponent"
    return "neutral"


# ── Classifier ─────────────────────────────────────────────────────────────

def get_classifier(db: Session) -> Callable[[SourceItem], PerspectiveResult]:
    """Build a classifier bound to the current campaign.

    Caches campaign-specific lookups (candidate party, campaign domains,
    attribution regexes) so the returned callable is cheap per-call.
    """
    cfg = db.query(CampaignConfig).first()
    candidate_name = cfg.candidate_name if cfg else ""
    candidate_party = cfg.party if cfg else ""

    opponents = db.query(Opponent).all()
    opponent_names = [o.name for o in opponents if o.name]
    # Use the first opponent's party as the "opposing" reference if
    # candidate_party isn't set explicitly. (For a 2-party race they're
    # complementary.)
    opponent_party_fallback = (
        opponents[0].party
        if opponents and opponents[0].party
        else ""
    )

    candidate_tokens = _name_tokens(candidate_name)
    opponent_tokens: list[str] = []
    for opp in opponents:
        opponent_tokens.extend(_name_tokens(opp.name or ""))
    # Dedupe + sort longest-first so multi-word names match before subsets.
    candidate_tokens = sorted(set(candidate_tokens), key=len, reverse=True)
    opponent_tokens = sorted(set(opponent_tokens), key=len, reverse=True)

    cand_attr = _attribution_pattern(candidate_tokens)
    opp_attr = _attribution_pattern(opponent_tokens)

    # V13.22 audit fix #1+#3 — Race-mention gate
    # Audit revealed 78% of outlet_bias classifications and ~530 LLM
    # classifications were wrong because the article was about a national
    # figure / different race with no PA-08 angle. Gate cheap heuristics
    # behind "did this article actually mention the race".
    # Tokens accepted: candidate name, opponent name, OR district/location
    # keywords from campaign config (skipping overly-generic single tokens).
    race_mention_tokens: set[str] = set()
    for t in candidate_tokens + opponent_tokens:
        if t and len(t) >= 3:
            race_mention_tokens.add(t.lower())
    if cfg:
        # Direct district code (e.g. "PA-08") and the full district + location strings.
        for attr in ("district", "location"):
            v = getattr(cfg, attr, None)
            if v and len(str(v).strip()) >= 3:
                race_mention_tokens.add(str(v).strip().lower())
        # Parsed geography keywords (JSON list of city/region names).
        gk_raw = getattr(cfg, "geography_keywords", None) or ""
        try:
            import json
            gk = json.loads(gk_raw) if isinstance(gk_raw, str) else gk_raw
            if isinstance(gk, list):
                for term in gk:
                    s = str(term).strip().lower()
                    # Skip overly-generic single tokens that match unrelated content.
                    # "PA" matches any Pennsylvania mention; "8" matches everywhere.
                    if len(s) < 4 or s in {"pa", "n/a", "none"}:
                        continue
                    # Truncate long descriptive strings to first phrase.
                    if len(s) > 50:
                        s = s.split(".")[0].split(",")[0].strip()
                    if len(s) >= 4:
                        race_mention_tokens.add(s)
        except Exception:
            pass

    def _mentions_race(item: SourceItem) -> bool:
        """True if article text contains any candidate name or district marker.
        Used as a gate for cheap heuristics that would otherwise mis-fire on
        national-politics articles."""
        if not race_mention_tokens:
            return True  # no tokens to check — don't block (degrade safely)
        blob = " ".join([
            (item.title or ""), (item.summary or ""),
            (item.raw_text or "")[:1500],
        ]).lower()
        return any(t in blob for t in race_mention_tokens if t)

    # Pre-normalize candidate / opponent parties for perspective resolution.
    cand_party_norm = (candidate_party or "").strip()
    opp_party_norm = (opponent_party_fallback or "").strip()

    def _outsidegroup_perspective(source_name: Optional[str]) -> Optional[str]:
        """If an outside-group source_name matches a known left/right
        advocacy group, return the appropriate perspective."""
        if not source_name:
            return None
        s = source_name.lower()
        for needle in OUTSIDE_GROUPS_LEFT:
            if needle in s:
                # Left-aligned group: pro-Democrat
                return _party_to_perspective("Democrat", cand_party_norm)
        for needle in OUTSIDE_GROUPS_RIGHT:
            if needle in s:
                return _party_to_perspective("Republican", cand_party_norm)
        return None

    # Well-known news-outlet domains that should NEVER be classified
    # as a candidate/opponent statement even if upstream tagged them.
    # (Upstream's "opponent_statement" heuristic sometimes mislabels a
    # news article ABOUT the opponent as a statement BY them.)
    NEWS_OUTLET_DOMAINS = {
        "nbcnews.com", "cnn.com", "abcnews.go.com", "cbsnews.com",
        "reuters.com", "apnews.com", "nytimes.com", "washingtonpost.com",
        "bloomberg.com", "politico.com", "axios.com", "thehill.com",
        "wvia.org", "wnep.com", "wbre.com",
        "thetimes-tribune.com", "citizensvoice.com", "timesleader.com",
        "standardspeaker.com", "pennlive.com", "wesa.fm",
        "pennsylvaniaindependent.com",  # appears to be a local news outlet
    }

    def classify(item: SourceItem) -> PerspectiveResult:
        domain = _extract_domain(item.source_url)
        # Aggregator URLs (Google News) carry the real publisher in
        # publisher_domain — use that when domain is an aggregator.
        if domain in ("news.google.com", "google.com", "yahoo.com"):
            if item.publisher_domain:
                domain = (item.publisher_domain or "").lower().lstrip("www.")

        # ── Phase 0: existing source_owner_type labels ───────────────────
        sot = (item.source_owner_type or "").strip().lower()
        # Phase 0 sanity check: don't trust 'opponent_statement' on a
        # known news-outlet domain. Those are upstream mislabels — fall
        # through to outlet_bias / attribution for the real signal.
        is_news_outlet = domain in NEWS_OUTLET_DOMAINS
        if sot == "candidate_statement" and not is_news_outlet:
            return PerspectiveResult(
                perspective="pro_candidate", method="existing",
                confidence="high",
                reason="source_owner_type=candidate_statement",
            )
        if sot == "opponent_statement" and not is_news_outlet:
            return PerspectiveResult(
                perspective="pro_opponent", method="existing",
                confidence="high",
                reason="source_owner_type=opponent_statement",
            )
        if sot == "party_committee_statement":
            # NRCC = Republican; DCCC = Democratic. Identify by source_name.
            sn = (item.source_name or "").lower()
            if any(k in sn for k in ("nrcc", "rnc", "gop", "republican")):
                return PerspectiveResult(
                    perspective=_party_to_perspective("Republican", cand_party_norm),
                    method="existing", confidence="high",
                    reason=f"party_committee_statement (Republican: {item.source_name!r})",
                )
            if any(k in sn for k in ("dccc", "dnc", "democratic", "democrats")):
                return PerspectiveResult(
                    perspective=_party_to_perspective("Democrat", cand_party_norm),
                    method="existing", confidence="high",
                    reason=f"party_committee_statement (Democrat: {item.source_name!r})",
                )
            # party_committee but ambiguous source_name — fall through to later phases
        if sot == "outside_group_statement":
            persp = _outsidegroup_perspective(item.source_name)
            if persp:
                return PerspectiveResult(
                    perspective=persp, method="existing", confidence="high",
                    reason=f"outside_group_statement: {item.source_name!r}",
                )
            # Unknown outside group — fall through
        if sot == "media":
            return PerspectiveResult(
                perspective="neutral", method="existing",
                confidence="high",
                reason="source_owner_type=media",
            )
        # community/manual or unclear → fall through

        # ── Phase 1: outlet bias by domain ───────────────────────────────
        # `domain` already resolved above (aggregator-aware).
        #
        # V13.22 audit fix #1 — outlet_bias gated on candidate mention.
        # Pre-fix, outlet_bias fired on Fox News articles about Cannes film
        # festivals or Iowa elections, all tagged pro_opponent. 78% error
        # rate. Gate it: outlet_bias only applies when article actually
        # mentions a candidate (or district). Otherwise fall through to LLM,
        # which will (after fix #2) usually call it neutral.
        if domain and _mentions_race(item):
            if domain in RIGHT_LEANING_DOMAINS:
                return PerspectiveResult(
                    perspective=_party_to_perspective("Republican", cand_party_norm),
                    method="outlet_bias", confidence="medium",
                    reason=f"right-leaning outlet: {domain}",
                )
            if domain in LEFT_LEANING_DOMAINS:
                return PerspectiveResult(
                    perspective=_party_to_perspective("Democrat", cand_party_norm),
                    method="outlet_bias", confidence="medium",
                    reason=f"left-leaning outlet: {domain}",
                )

        # ── Phase 2: attribution in title + summary + raw_text ──────────
        # title FIRST because article titles often summarize WHO acted:
        # "Cognetti blasts Bresnahan…" — the title alone tells us the
        # framing. Summary + first 1500 chars of raw_text catches the
        # rest. raw_text is too long for regex past that.
        text_blob = (
            (item.title or "") + " . " +
            (item.summary or "") + " . " +
            (item.raw_text or "")[:1500]
        )
        cand_match = cand_attr.search(text_blob)
        opp_match = opp_attr.search(text_blob)
        if cand_match and opp_match:
            # Both speakers attributed — whichever appears FIRST owns the lead.
            if cand_match.start() < opp_match.start():
                return PerspectiveResult(
                    perspective="pro_candidate", method="attribution",
                    confidence="medium",
                    reason=f"candidate speaker first: {cand_match.group()!r}",
                )
            return PerspectiveResult(
                perspective="pro_opponent", method="attribution",
                confidence="medium",
                reason=f"opponent speaker first: {opp_match.group()!r}",
            )
        if cand_match:
            return PerspectiveResult(
                perspective="pro_candidate", method="attribution",
                confidence="medium",
                reason=f"candidate attribution: {cand_match.group()!r}",
            )
        if opp_match:
            return PerspectiveResult(
                perspective="pro_opponent", method="attribution",
                confidence="medium",
                reason=f"opponent attribution: {opp_match.group()!r}",
            )

        # ── Phase 3: LLM fallback (only fires if explicitly invoked) ────
        # The classifier here returns "fallback" — callers decide whether
        # to invoke the LLM via classify_with_llm() below. Phase 3 is a
        # one-time backfill cost ($0.0001/article on gpt-4o-mini) and
        # should be cached, so we don't make it automatic from the
        # per-item classify path.
        return PerspectiveResult(
            perspective="neutral", method="fallback", confidence="low",
            reason="no signal from existing labels, outlet bias, or attribution",
        )

    return classify


# ── Phase 3: LLM classifier (separate fn so it can be invoked selectively) ──

_LLM_SYSTEM_PROMPT = """Classify which campaign benefits from this article being in the press.

You're given CANDIDATE_A (Party_A) vs CANDIDATE_B (Party_B). Output the favored candidate's name.

REASONING PROCESS — think step-by-step in your reasoning, then output the verdict:

STEP 0 (race-relevance gate): Is this article actually about THIS RACE?
  - Mentions CANDIDATE_A or CANDIDATE_B by name? → continue to STEP 1.
  - Mentions the district / state / race directly (e.g. "PA-08", the candidates'
    home city, etc.)? → continue to STEP 1.
  - About a DIFFERENT race (other state's candidates, other district)? → "neutral".
  - National-politics article (Trump, Congress, GOP/Dem in the abstract) with no
    mention of either named candidate or this district? → "neutral".
  - A national figure's speech / scandal that doesn't intersect this race? → "neutral".

  The rule: a partisan-leaning article that doesn't touch this race doesn't move
  votes in this race. Default to "neutral" when in doubt.

STEP 1: Who is the article's subject?
  - One candidate by name? Both candidates? A partisan figure (Trump/Pelosi/etc.)?
    A topic only? Off-topic entirely?

STEP 2: What's the framing?
  - Negative / critical / accusatory of a named candidate → favors the OPPOSITE candidate.
  - Positive / endorsing / promotional of a named candidate → favors THAT candidate
    (UNLESS the topic is a known attack vector — see step 3).
  - Symmetric / mixed coverage of both → use first-named in title.
  - Pure partisan framing without either candidate as subject → see step 4.
  - No political content → neutral.

STEP 3: Is the topic a known attack vector? (OVERRIDES STEP 2's positive framing)
  Attack vectors are topics that originated as opposition research — when these come up,
  the OPPOSING side benefits regardless of how the candidate is framed (even reforming /
  defending). The mere visibility of the attack vector is the campaign asset for the side
  that raised it.

  Generic patterns to recognize:
    - Stock trading / insider trading allegations against a candidate
    - Ethics allegations / corruption allegations against a candidate
    - "Carpetbagger" / running for multiple offices / abandoning constituents
    - Hypocrisy / promise-breaking on a signature issue

  → If the candidate is in an established attack-vector topic, favors the OPPONENT.

STEP 4: Partisan-figure framing (only when neither candidate is the subject):
  ONLY apply this step if STEP 0 confirmed the article touches THIS race. National
  partisan-figure coverage with no district/candidate intersection → "neutral".

  When STEP 0 passes:
    Democratic figure / framing wins → favors the DEMOCRATIC candidate.
    Republican figure / framing wins → favors the REPUBLICAN candidate.
    Critique of Dems → favors the Republican candidate.
    Critique of GOP → favors the Democratic candidate.
    GOP defectors backing Dem position → favors the Democratic candidate (Dem framing wins).
    Dem defectors backing GOP position → favors the Republican candidate.

STEP 5: Verify the output:
  - If your reasoning says "criticized" or "attack vector against X" → output X's OPPONENT.
  - If your reasoning says "positive coverage of X" and NOT an attack vector → output X.

WORKED EXAMPLES (with reasoning trace):

  "Letter: [candidate] voted to enable ICE"
    STEP 1: candidate is subject. STEP 2: critical → favors opposite candidate.
    Output: the opposite.

  "[Candidate] welcomes Dr. Oz to Scranton"
    STEP 1: candidate is subject. STEP 2: positive (their event). STEP 3: not an attack vector.
    → favors that candidate.

  "[Candidate] signs discharge petition to ban congressional stock trading"
    STEP 1: candidate is subject. STEP 2: positive framing (reform). STEP 3: stock trading IS
    an attack vector against that candidate → favors OPPONENT regardless of positive framing.

  "[Partisan figure] says Democrats' coalition is huge"
    STEP 1: partisan figure; neither candidate is the subject.
    STEP 4: Dem self-promo → favors the Democratic candidate.

  "Four Republicans join Democrats to force healthcare vote"
    STEP 1: GOP defectors; neither named candidate is the subject.
    STEP 4: Dem framing wins (defectors validating Dem position) → favors Dem candidate.

  "[Candidate A], [Candidate B] trade barbs as race heats up"
    STEP 1: Both candidates, symmetric. STEP 2: mixed coverage → first-name tie-break.

  "Bridge replacement in central Pa."
    STEP 1: off-topic. → neutral.

Return strict JSON:
  {"favored_candidate": "<NAME_A>" | "<NAME_B>" | "neutral",
   "reason": "<one sentence summarizing which step decided it>"}
"""


def classify_with_llm(
    item: SourceItem,
    candidate_name: str,
    candidate_party: str,
    opponent_name: str,
    opponent_party: str,
    *,
    provider=None,
) -> PerspectiveResult:
    """Classify a single article via gpt-4o-mini.

    Caller controls when this fires — call it for items where
    `classify(item).method == 'fallback'` and you want a real answer.
    The function does NOT cache; persistence is the caller's job.
    """
    if provider is None:
        import os
        try:
            from app.services.llm_provider import OpenAIProvider
            key = os.environ.get("OPENAI_API_KEY", "").strip()
            if not key:
                return PerspectiveResult(
                    perspective="neutral", method="fallback", confidence="low",
                    reason="OPENAI_API_KEY not set",
                )
            # gpt-4o-mini is the right tool here: cheap, fast, and the task
            # is a 3-way classification, not deep reasoning.
            provider = OpenAIProvider(api_key=key, model="gpt-4o-mini")
        except Exception as exc:
            log.warning("perspective LLM: provider unavailable (%s)", exc)
            return PerspectiveResult(
                perspective="neutral", method="fallback", confidence="low",
                reason=f"llm unavailable: {exc}",
            )

    title = (item.title or "")[:200]
    summary = (item.summary or "")[:600]
    excerpt = (item.raw_text or "")[:600]
    user_prompt = (
        f"CANDIDATE_A: {candidate_name} ({candidate_party})\n"
        f"CANDIDATE_B: {opponent_name} ({opponent_party})\n\n"
        f"Article title: {title}\n"
        f"Summary: {summary}\n"
        f"Excerpt: {excerpt}\n\n"
        f"Which campaign would WANT this article spread? Return JSON with "
        f"favored_candidate = exactly {candidate_name!r}, {opponent_name!r}, or \"neutral\"."
    )
    try:
        from app.services.llm_provider import _parse_json_response
        raw = provider._chat(
            user_prompt=user_prompt,
            system_prompt=_LLM_SYSTEM_PROMPT,
            json_mode=True,
            temperature=0,
            seed=42,
        )
        parsed = _parse_json_response(raw)
        if not parsed:
            return PerspectiveResult(
                perspective="neutral", method="llm", confidence="low",
                reason="llm response unparseable",
            )
        favored = (parsed.get("favored_candidate") or "").strip()
        reason = (parsed.get("reason") or "")[:120]
        # Map favored_candidate name → perspective. Match case-insensitive,
        # allow surname-only matches (LLM sometimes returns just "Cognetti").
        cand_lc = candidate_name.lower()
        opp_lc = opponent_name.lower()
        f_lc = favored.lower()
        # Direct equality or substring match (handles last-name responses).
        if f_lc == "neutral":
            return PerspectiveResult(
                perspective="neutral", method="llm", confidence="low",
                reason=f"llm: {reason}",
            )
        # Match candidate. Surname is in last-token of cand_lc.
        cand_surname = cand_lc.split()[-1] if cand_lc else ""
        opp_surname = opp_lc.split()[-1] if opp_lc else ""
        if cand_surname and (f_lc == cand_lc or cand_surname in f_lc):
            return PerspectiveResult(
                perspective="pro_candidate", method="llm", confidence="low",
                reason=f"llm: {reason}",
            )
        if opp_surname and (f_lc == opp_lc or opp_surname in f_lc):
            return PerspectiveResult(
                perspective="pro_opponent", method="llm", confidence="low",
                reason=f"llm: {reason}",
            )
        return PerspectiveResult(
            perspective="neutral", method="llm", confidence="low",
            reason=f"llm returned unrecognized name: {favored!r}",
        )
    except Exception as exc:
        log.warning("perspective LLM: call failed (%s)", exc)
        return PerspectiveResult(
            perspective="neutral", method="fallback", confidence="low",
            reason=f"llm call failed: {exc}",
        )
