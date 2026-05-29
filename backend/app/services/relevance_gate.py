"""Keyword relevance gate for the review queue.

The per-article LLM scorer over-classifies items as `content_category='campaign'`
for anything geographically in the district — community calendar items,
sandwich-shop reopenings, library news, etc. — because the scorer treats
"happened in our district" as a synonym for "campaign-relevant."

This second-pass filter requires the item to mention something genuinely
campaign-relevant before it surfaces in the human review queue. It does
NOT delete or archive anything — items the gate kicks out are still in
the database, still on the Articles page, still searchable. The Review
Queue's "Recently filtered" section exposes them as a spot-check.

The gate is generic: it builds its keyword set from `CampaignConfig` +
`Opponent` rows at query time. No hard-coded race specifics. Three
inputs feed the keyword set:

  1. Candidate + opponent names (from CampaignConfig + Opponents).
  2. District identifiers (district code, district number expansions).
  3. Priority-issue keyword expansions (e.g. "Healthcare" → medicare,
     medicaid, hospital, insurance, …) — see PRIORITY_EXPANSIONS.
  4. UNIVERSAL_TERMS — federal/election vocabulary that's relevant for
     any congressional race.

Safety bypasses (skip the keyword check, item passes regardless):
  - actionability_label in {'review', 'respond'} — the LLM independently
    flagged this for human attention.
  - candidate_mentioned / opponent_mentioned / priority_issue_mentioned
    boolean flags set to True — the upstream scorer noticed something.
    (These flags are all False on every current queue item, but they're
    the right signal in principle and worth honoring when populated.)
"""

import json
import re
from typing import Optional

from sqlalchemy.orm import Session

from app.models import CampaignConfig, Opponent, SourceItem


# Federal/election/political vocabulary — same for every race. These
# terms almost always indicate campaign-adjacent coverage regardless of
# the specific candidates involved. Kept lowercase; matched case-insensitively.
UNIVERSAL_TERMS: set[str] = {
    # Federal & legislative
    "congress", "congressional", "senate", "senator", "senators",
    "representative", "congressman", "congresswoman",
    "lawmaker", "lawmakers", "legislator", "legislation",
    "federal", "fema", "hud", "doj", "fbi", "pentagon",
    "white house", "executive order",
    # "House" alone is too broad ("Share House", "household") — require
    # explicit congressional context.
    "u.s. house", "us house", "house republican", "house republicans",
    "house democrat", "house democrats", "house passed", "house bill",
    "house panel", "house committee", "house majority", "house minority",
    "house floor", "house vote",
    # Election & campaign
    "election", "elections", "primary election", "general election",
    "campaign trail", "candidate", "candidates",
    "voter", "voters", "ballot", "midterm", "midterms",
    "endorsement", "endorses", "endorsed",
    # Presidential / major figures (any race covers these)
    "trump", "biden", "harris", "vance", "obama", "president",
    # Party + governance
    "republican", "republicans", "democrat", "democrats", "democratic party",
    "gop", "partisan", "bipartisan",
    "incumbent", "constituent", "constituents",
    "shutdown", "appropriations", "appropriation", "filibuster",
    "voted for", "voted against", "voted to",
}


# Issue keyword expansion. Each canonical priority maps to common
# coverage terms used in news headlines/summaries.
# Issue expansions — tightened to terms that are *only* signal in a
# political-coverage context. Generic words ("hospital", "school",
# "police", "investigation") were removed because they trigger on
# unrelated local-news stories (a hospital fire, a school recital,
# a fire investigation) and dragged false positives into the queue.
PRIORITY_EXPANSIONS: dict[str, set[str]] = {
    "housing": {"housing", "rent", "rental", "affordable housing",
                "affordability", "mortgage", "homeless", "homelessness",
                "section 8", "eviction"},
    "affordability": {"affordable", "affordability", "cost of living",
                      "inflation"},
    "healthcare": {"healthcare", "health care", "medicare", "medicaid",
                   "prescription", "aca", "obamacare", "premium",
                   "deductible", "drug pricing", "uninsured",
                   "public option", "single payer"},
    "economy": {"economy", "economic", "unemployment", "wages",
                "manufacturing jobs", "layoff", "layoffs", "inflation",
                "recession", "small business", "tariff", "tariffs"},
    "jobs": {"unemployment", "wages", "manufacturing jobs", "layoff",
             "layoffs", "workforce", "employment numbers"},
    "corruption": {"corruption", "bribery", "scandal", "indictment",
                   "indicted", "subpoena", "ethics violation",
                   "campaign finance", "FEC"},
    "ethics": {"corruption", "ethics violation", "bribery", "scandal",
               "conflict of interest"},
    "taxes": {"tax cut", "tax cuts", "tax hike", "tax hikes", "taxation",
              "tax bill", "tax reform"},
    "budget": {"federal budget", "deficit", "appropriations", "fiscal year",
               "continuing resolution"},
    "education": {"public education", "title i", "title 1", "school board",
                  "school funding", "tuition", "student loan"},
    "energy": {"energy policy", "pipeline", "renewable", "fracking",
               "climate change", "carbon emissions"},
    "environment": {"environmental policy", "epa regulation", "climate change",
                    "pollution", "carbon emissions"},
    "immigration": {"immigration", "immigrant", "immigrants", "border",
                    "deportation", "asylum", "daca", "sanctuary city"},
    "abortion": {"abortion", "roe", "reproductive rights", "pro-choice",
                 "pro-life"},
    "guns": {"gun control", "gun violence", "firearm", "second amendment",
             "ar-15", "assault weapon"},
    "infrastructure": {"infrastructure", "broadband", "transit funding",
                       "bridge collapse"},
    "veterans": {"veteran", "veterans", "VA benefits", "military service"},
}


def _expand_issues(priorities: list[str]) -> set[str]:
    """Expand priority labels into matchable keyword sets.

    Splits multi-word labels on common separators ("Housing & Affordability"
    → housing, affordability), then looks each token up in
    PRIORITY_EXPANSIONS. Unknown tokens become single-keyword matches so
    the user can add custom priorities and have them work without code
    changes (less generous coverage, but it still works).
    """
    out: set[str] = set()
    for label in priorities:
        if not label:
            continue
        tokens = [t.strip().lower() for t in re.split(r"[,&/]+|\s+", label) if t.strip()]
        for tok in tokens:
            if tok in PRIORITY_EXPANSIONS:
                out.update(PRIORITY_EXPANSIONS[tok])
            elif len(tok) >= 4:
                out.add(tok)
    return out


def build_keyword_pattern(db: Session) -> Optional[re.Pattern]:
    """Build the active campaign's relevance regex.

    Returns None if no campaign config exists (in which case the gate
    should be skipped — typically test environments or fresh installs).
    """
    config = db.query(CampaignConfig).first()
    if not config:
        return None

    keywords: set[str] = set(UNIVERSAL_TERMS)

    # Candidate name — accept first name, last name, full name. Skip
    # short tokens (<= 2 chars) to avoid e.g. matching "Al" everywhere.
    if config.candidate_name:
        for part in config.candidate_name.split():
            if len(part) > 2:
                keywords.add(part.lower())
        keywords.add(config.candidate_name.lower())

    # District identifiers
    if config.district:
        keywords.add(config.district.lower())
    if config.district_number:
        n = config.district_number
        keywords.add(f"{n}th district".lower())
        keywords.add(f"district {n}".lower())
        keywords.add(f"{n}th congressional".lower())

    # Priority issue expansions
    try:
        priorities = json.loads(config.key_priorities) if config.key_priorities else []
        keywords.update(_expand_issues(priorities))
    except Exception:
        pass

    # Opponent names
    for opp in db.query(Opponent).all():
        if not opp.name:
            continue
        for part in opp.name.split():
            if len(part) > 2:
                keywords.add(part.lower())
        keywords.add(opp.name.lower())

    # Compile with word-boundary anchors so substring false positives
    # (e.g. "EPA" matching inside "NEPA") don't sneak through. Longest
    # keywords first so multi-word phrases win the alternation race.
    #
    # `(?<![A-Za-z0-9])` / `(?![A-Za-z0-9])` is a manual word boundary
    # that treats letters+digits as "inside a word" so e.g. "PA-08" lines
    # up at " PA-08 " but not " XPA-08X ". Plain `\b` is unreliable for
    # tokens with embedded punctuation.
    escaped = [re.escape(k) for k in sorted(keywords, key=len, reverse=True)]
    if not escaped:
        return None
    body = "|".join(escaped)
    return re.compile(rf"(?<![A-Za-z0-9])(?:{body})(?![A-Za-z0-9])", re.IGNORECASE)


def passes_gate(item: SourceItem, pattern: Optional[re.Pattern]) -> bool:
    """Return True if this item should appear in the review queue.

    Three ways an item can pass:

    1. Safety bypass — the upstream scorer / pipeline flagged this for
       human attention. We honor those signals regardless of keyword
       presence so we don't lose anything the LLM was confident about.
    2. The boolean mention flags are set (candidate_mentioned,
       opponent_mentioned, priority_issue_mentioned).
    3. The title or summary matches the campaign relevance pattern.

    If no pattern is supplied (no campaign config) we pass everything
    so the gate doesn't accidentally hide all items in a fresh install.
    """
    # Bypass 1: scorer flagged for action.
    if item.actionability_label in ("review", "respond"):
        return True

    # Bypass 2: mention flags.
    if item.candidate_mentioned or item.opponent_mentioned or item.priority_issue_mentioned:
        return True

    if pattern is None:
        return True

    # Keyword match on title + summary.
    haystack = " ".join(filter(None, [item.title or "", item.summary or ""]))
    return bool(pattern.search(haystack))
