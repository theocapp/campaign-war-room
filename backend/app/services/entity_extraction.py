"""Knowledge-graph entity extraction (Feature A, Phase 2 skeleton).

This module defines the data contract between:
  - The LLM extractor (gpt-4o-mini / Claude Haiku-4.5) that reads articles
  - The DB persistence layer (Entity / EntityMention / EntityRelation tables)
  - The frontend (Entity Network, Timeline, Geographic Overlay) that consumes
    real entities once the backfill runs.

Phase 2 (this file) = data contract + canonicalization helpers + LLM prompt.
Phase 3 (next, separate) = the actual extraction runner that calls the LLM
                            over the 2,367-article corpus and persists rows.

See backend/docs/entity_schema.md for the human-readable design spec.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.models import Claim, ClaimSupport, Entity, EntityMention, EntityRelation, SourceItem

log = logging.getLogger(__name__)


# ── Type definitions ────────────────────────────────────────────────────────
# V14.1 — per inter-session review, dropped `event` and `issue` types and
# the `donated_to`/`attended` predicates:
#   - issue: redundant with narrative_frames; LLM hallucinated them constantly
#     in the prior KG attempt (see project-kg-pivot memory).
#   - event: hardest type to canonicalize ("the launch" vs "April 9 kickoff" vs
#     "Cognetti's announcement" → same thing, three surface forms). The
#     previous KG's 3rd rewrite died on this. Revisit when Sonnet resolution
#     layer is online.
#   - donated_to: donation data lives in FEC filings, not prose. Articles
#     don't say "X donated to Y" — including the verb just adds hallucination
#     surface. Add back when FEC import lands as a separate data source.
#   - attended: depends on `event` to exist as a target — pulled along with it.

# Bumped when we change the prompt or the schema in a way that affects what
# `endorses` / `voted_for` / etc. mean. Stored on each piece of evidence so
# we can identify rows that need re-extraction after future prompt changes.
#
# v14.3 — tightened `endorses` definition to require explicit endorsement
# language (the prompt rejects "discussed favorably" and "signed a discharge
# petition" as endorsements; those become `voted_for` or `co_sponsored`).
# v14.4 — wider context window for the extractor (single-call semantic chunking
# via context expansion). EXCERPT_CHARS bumped from 1,500 to 8,000.
# v14.5 — re-added `event` entity type and `attended` predicate, but with
# STRICT dedup constraints: events require event_date OR event_location_id.
# Vague event mentions ("the launch", "the campaign event") are now
# rejected rather than auto-discovered as ambiguous entities.
# v14.6 — relations now carry a `stance` field ("supporting" default, or
# "contesting" when the article disputes/denies/fact-checks the claim).
# Contesting evidence is written to claim_supports with stance="contesting"
# and auto-flips the underlying claim's status to "contested" when both
# supporting and contesting evidence exist.
# v14.7 — tightened `attended` and `event` definitions. Did not fix the
# underlying problem (LLM kept emitting election processes as events and
# inferring attendance from context); audit showed quality REGRESSED.
# That run is what motivated the v15.0 pivot below.
# v15.0 — TRIPLE SHAPE RETIRED. Replaced predicate-based extraction with
# quote-anchored claim records. The LLM no longer emits
# (subject, predicate, object) — it emits entities[] + claim_records[]
# where each record is a verbatim quote span + the entities in it + an
# optional shallow label (statement|attack|defense|endorsement|
# policy_position|vote|announcement|commitment | NULL). Predicates,
# domain/range, commonsense, dimensional stance, contested-status —
# all retired for action data. Structural relations (represents,
# member_of, predecessor_of) remain seed-sourced. Event entity type
# removed. See app/services/extractor_versions.py:v15.0 for the full
# rationale; the v14.7 failure-mode audit is in NOCTUA_KG_BRAINSTORM.md.
EXTRACTOR_VERSION = "v15.0"

# Context window for the LLM extractor. Used by backfill / retry / targeted
# re-extract scripts to build the user prompt. gpt-4o-mini handles 128K
# tokens; 8K chars ≈ 2K tokens for the excerpt — comfortable headroom.
# Bumped from 1,500 in v14.4 to capture entities mentioned later in long
# articles. See INTER_SESSION.md Session D for the trade-off discussion.
EXCERPT_CHARS = 8000
TITLE_CHARS = 240
SUMMARY_CHARS = 1200

EntityType = Literal["person", "organization", "bill", "location", "event"]

# 10 verbs (v14.5 re-added `attended` for event participation).
# See entity_schema.md §"Relationship types".
Predicate = Literal[
    "endorses",
    "criticizes",
    "attacks",
    "voted_for",
    "voted_against",
    "co_sponsored",
    "represents",
    "member_of",
    "predecessor_of",
    "attended",
]

Confidence = Literal["high", "medium", "low"]


# Domain/range constraints — GKG principle #4 (ontology constraints).
# For each predicate, the allowed (subject.type, object.type) pairs. Relations
# that don't satisfy these are rejected at write time and during one-shot
# cleanup (scripts/entity_domain_range_cleanup.py).
#
# These catch a whole class of LLM noise: locations "endorsing" things, bills
# "attacking" people, a person "representing" a bill, etc. See
# scripts/entity_domain_range_cleanup.py for the cleanup pass against
# pre-V14.3 data.
PREDICATE_DOMAIN_RANGE: dict[str, dict[str, set[str]]] = {
    # Rhetorical predicates also allow `event` as object — politicians
    # frequently criticize/attack/endorse specific rallies, debates, or
    # discharge petitions. Added in v14.5 alongside the event entity type.
    "endorses":       {"subject": {"person", "organization"},
                       "object":  {"person", "organization", "bill", "event"}},
    "criticizes":     {"subject": {"person", "organization"},
                       "object":  {"person", "organization", "bill", "event"}},
    "attacks":        {"subject": {"person", "organization"},
                       "object":  {"person", "organization", "bill", "event"}},
    "voted_for":      {"subject": {"person"},
                       "object":  {"bill"}},
    "voted_against":  {"subject": {"person"},
                       "object":  {"bill"}},
    "co_sponsored":   {"subject": {"person"},
                       "object":  {"bill"}},
    "represents":     {"subject": {"person"},
                       "object":  {"location", "organization"}},
    "member_of":      {"subject": {"person", "organization"},
                       "object":  {"organization"}},
    "predecessor_of": {"subject": {"person"},
                       "object":  {"person"}},
    # v14.5 — event participation. Persons and orgs can attend events.
    "attended":       {"subject": {"person", "organization"},
                       "object":  {"event"}},
}


def relation_type_allowed(subject_type: str, predicate: str, object_type: str) -> bool:
    """Return True if (subject_type, predicate, object_type) satisfies the
    domain/range constraints for this predicate. Unknown predicates are
    rejected (we have exactly 9 — anything else is a bug)."""
    rules = PREDICATE_DOMAIN_RANGE.get(predicate)
    if not rules:
        return False
    return subject_type in rules["subject"] and object_type in rules["object"]


# ── Pydantic models — what the LLM extractor returns ───────────────────────

class ExtractedEntity(BaseModel):
    """One entity mention found in an article. The LLM emits a canonical_id
    GUESS — the canonicalization layer either accepts it (matches a seeded
    entity), reconciles via embedding similarity, or creates a new canonical
    entity."""
    name: str = Field(..., description="The entity's name as it appeared in the article (or a clean canonical form).")
    type: EntityType
    surface_text: Optional[str] = Field(None, description="The exact text from the article — useful for context.")
    description: Optional[str] = Field(None, description="One-sentence summary if the LLM can write one.")
    affiliation: Optional[Literal["D", "R", "I"]] = None
    # Optional canonical_id hint — the LLM can guess one for a seeded entity.
    # If absent the canonicalizer infers it from name + type.
    canonical_id_hint: Optional[str] = None
    # v14.5 — event-specific metadata. Only meaningful when type == "event".
    # Required: at least one of event_date OR event_location must be set
    # (enforced by canonicalize_entity for dedup safety).
    event_date: Optional[str] = Field(None, description="ISO date (YYYY-MM-DD or YYYY-MM) when known.")
    event_location: Optional[str] = Field(None, description="Name of an associated location entity (must also appear in result.entities).")
    event_type: Optional[Literal["rally", "debate", "fundraiser", "vote", "town_hall",
                                  "press_conference", "endorsement", "other"]] = None

    @field_validator("affiliation", mode="before")
    @classmethod
    def _coerce_null_string(cls, v):
        """LLMs sometimes return the string "null" or "none" instead of JSON
        null. Coerce these — and empty strings — to actual None before the
        Literal validator runs.

        Also handles the LLM-emits-union-literally bug: when the schema doc
        says affiliation is "D|R|I|null", gpt-4o-mini occasionally outputs
        the *string* "D|null" or "R|I" instead of choosing one. Split on the
        pipe and take the first non-null, non-empty token."""
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("", "null", "none", "n/a", "na"):
                return None
            # Union-literal bug: "D|null" → "D", "R|I" → "R", etc.
            if "|" in s:
                for token in s.split("|"):
                    t = token.strip()
                    if t and t not in ("null", "none", "n/a", "na"):
                        s = t
                        break
                else:
                    return None
            # Some LLM responses give full party names; normalize.
            if s in ("democrat", "democratic", "dem", "d"):
                return "D"
            if s in ("republican", "rep", "gop", "r"):
                return "R"
            if s in ("independent", "ind", "i"):
                return "I"
        return v

    @field_validator("type", mode="before")
    @classmethod
    def _coerce_type_synonyms(cls, v):
        """LLMs occasionally return slightly off type names (e.g. 'people',
        'org', 'policy_issue'). Map common variants to the 4 canonical types.
        Unrecognized types fall through to strict Literal validation, which
        rejects them — those entities get dropped, which is what we want
        for unsupported types (event/issue/etc.)."""
        if not isinstance(v, str):
            return v
        s = v.strip().lower()
        synonyms = {
            "people": "person", "individual": "person",
            "org": "organization", "company": "organization", "group": "organization",
            "agency": "organization", "department": "organization",
            "legislation": "bill", "act": "bill", "law": "bill", "resolution": "bill",
            "place": "location", "geo": "location", "area": "location", "region": "location",
            "city": "location", "county": "location", "district": "location",
            # v14.5 — event is now a valid type
            "rally": "event", "debate": "event", "fundraiser": "event",
            "town_hall": "event", "press_conference": "event",
            "campaign_event": "event", "gathering": "event",
            # Intentionally NOT mapping: issue/topic/policy → those are
            # rejected (return original; Literal validator drops the entity).
        }
        return synonyms.get(s, s)


class ExtractedRelation(BaseModel):
    """A subject-predicate-object triple extracted from the article.
    Subject and object refer to entities by NAME — the persistence layer
    canonicalizes and looks up the corresponding entity row. Earlier
    versions used integer indices into result.entities[], which was
    fragile (out-of-range silently dropped relations)."""
    subject_name: str = Field(..., description="Name of the subject entity (must match an entity in result.entities[].name).")
    predicate: Predicate
    object_name: str = Field(..., description="Name of the object entity (must match an entity in result.entities[].name).")
    sample_quote: Optional[str] = Field(None, description="Short article excerpt that supports the claim.")
    confidence: Confidence = "medium"
    # v14.6 — stance: does this article ASSERT the relation, or CONTEST it?
    # "supporting" = article asserts the relation as true (default).
    # "contesting" = article disputes the relation (fact-check, denial,
    # refutation). Same (subject, predicate, object) triple — the claim
    # is the same logical fact; the article is just disagreeing with it.
    stance: Literal["supporting", "contesting"] = "supporting"

    @field_validator("predicate", mode="before")
    @classmethod
    def _coerce_predicate_synonyms(cls, v):
        """LLMs sometimes return verbs slightly off our canonical list.
        Map the common variants to our 9 verbs. Anything still unrecognized
        will fall through and trigger the strict Literal validator (which
        will raise — these get logged as failures and skipped, not silently
        misclassified)."""
        if not isinstance(v, str):
            return v
        s = v.strip().lower().replace(" ", "_").replace("-", "_")
        synonyms = {
            # endorses family — tightened in v14.3. Only explicit-endorsement
            # verbs map to `endorses`. Soft language ("supports", "praises",
            # "backs", "approves_of", "allies_with", "supported_by") is
            # intentionally NOT mapped — those fall through to strict
            # Literal validation and the lenient parser drops just that
            # relation, preserving the rest of the article's extraction.
            # Reason: those soft verbs produced the Bresnahan-endorses-ACA
            # class of false positives in v14.1.
            "endorsed": "endorses",
            "officially_backs": "endorses",
            "officially_supports": "endorses",
            "officially_endorses": "endorses",
            "endorsement_of": "endorses",
            "throws_support_behind": "endorses",
            # criticizes family (lighter than attacks)
            "criticized": "criticizes", "rebukes": "criticizes",
            "denounces": "criticizes", "opposes": "criticizes",
            "opposed": "criticizes",
            # attacks family (hostile)
            "attacked": "attacks", "accuses": "attacks", "slams": "attacks",
            "blasts": "attacks",
            # voting
            "voted_yes": "voted_for", "voted_in_favor": "voted_for",
            "voted_no": "voted_against",
            "introduced": "co_sponsored",  # close enough
            # geographic
            "representative_of": "represents", "represented": "represents",
            # group / role
            "led": "member_of", "leads": "member_of", "chairs": "member_of",
            "chair_of": "member_of", "works_for": "member_of",
            "part_of": "member_of",
            # succession
            "succeeded": "predecessor_of", "preceded": "predecessor_of",
            "replaced": "predecessor_of", "unseated": "predecessor_of",
            # event participation
            "spoke_at": "attended", "appeared_at": "attended",
            "participated_in": "attended", "took_part_in": "attended",
            "joined": "attended",  # context-dependent, but usually OK for events
            "headlined": "attended", "addressed": "attended",
            # DROPPED in V14.1: attended (event-target), donated_to (FEC, not prose)
            # If LLM emits these the strict Literal will reject and the
            # relation gets logged + skipped.
        }
        return synonyms.get(s, s)


# ── v15.0 — quote-anchored claim record ──────────────────────────────────
# Replaces ExtractedRelation. The LLM emits a verbatim quote span + the
# canonical entities visible in it + an optional shallow label. We do NOT
# ask for predicates or directionality — those were the v14.x failure
# surface (see extractor_versions.py:v15.0 for the rationale).

ClaimLabel = Literal[
    "statement",        # generic safe catch-all
    "attack",           # adversarial language toward a target
    "defense",          # response / denial / pushback
    "endorsement",      # explicit endorsement language
    "policy_position",  # candidate's stated position on an issue
    "vote",             # a recorded legislative vote
    "announcement",     # campaign launch, filing, rollout
    "commitment",       # a promise / commitment (more observable than "promise")
]


class ExtractedClaim(BaseModel):
    """One quote-anchored claim record extracted from an article.

    DESIGN INVARIANTS (enforced at persist time, not by the LLM):
      - evidence_span MUST be a verbatim substring of the article's text.
      - Every name in `entities` MUST appear in evidence_span (by canonical
        name, alias, or a recognized surface form).
      - label is OPTIONAL — if uncertain, leave it None. Forcing a label
        was the failure mode that retired the triple shape; resist that
        temptation here too.

    DO NOT add fields like target, directionality, predicate, or stance
    here. Those re-introduce the v14.x failure mode (LLM completing
    schema slots by force-fitting prose). The invariant is:
      "If a human can't verify it directly from the highlighted span,
       it doesn't belong in extraction."
    """
    entities: list[str] = Field(
        min_length=1,
        description="Names of entities visible in the quote. Must each appear "
                    "in evidence_span. Use canonical names (e.g. 'Paige "
                    "Cognetti'), not pronouns or roles."
    )
    evidence_span: str = Field(
        min_length=10, max_length=600,
        description="A verbatim substring of the article text — quote-shaped, "
                    "self-contained, ideally under 300 chars. Do NOT "
                    "paraphrase, summarize, or rewrite."
    )
    label: Optional[ClaimLabel] = Field(
        default=None,
        description="Optional shallow tag. Leave None if uncertain — null is a "
                    "valid extraction. Do NOT force classification."
    )
    confidence: Literal["high", "medium", "low"] = "medium"


class ExtractionResult(BaseModel):
    """Output of a single LLM extraction call on one article.

    v15.0 — `claims` is the active output; `relations` is preserved on the
    schema for backwards-compatibility with legacy scripts but is not
    populated by v15.0 prompts. Bookkeeping helper `is_v15` checks which
    shape was actually returned.
    """
    entities: list[ExtractedEntity] = Field(default_factory=list)
    claims: list[ExtractedClaim] = Field(default_factory=list)
    # Legacy field — preserved for forward-compat with older extraction logs.
    # v15.0 prompts do not produce this; if it's non-empty, you're parsing
    # a v14.x extraction result, not a v15.0 one.
    relations: list[ExtractedRelation] = Field(default_factory=list)

    @property
    def is_v15(self) -> bool:
        """True if this looks like a v15.0 result (claims, no legacy relations)."""
        return bool(self.claims) and not self.relations


# ── LLM system prompt ──────────────────────────────────────────────────────

LLM_SYSTEM_PROMPT = """You are a precise political-news evidence extractor.

Given an article, extract:
  1. ENTITIES present — people, organizations, bills, locations
  2. CLAIMS — verbatim quote spans from the article that involve named entities

You DO NOT emit predicates, directionality, or stance. You DO NOT decide
"who did what to whom." You select spans of text that ground meaningful
political assertions. Downstream systems handle interpretation.

ENTITY TYPES (exactly 4 — never invent new types):
  person        — humans (candidates, officials, journalists, donors, activists)
  organization  — groups acting as a unit (parties, PACs, unions, news outlets, advocacy, government agencies)
  bill          — specific named legislation (must have a clear identifying name like "ACA Subsidy Extension" or "H.R. 1234"). NOT broad policy topics.
  location      — named geographic places (cities, counties, districts, regions, states)

DO NOT extract:
  - Generic topics or issues (healthcare, immigration, etc.) — these are handled separately by narrative frames.
  - Generic phrases ("Four House Republicans", "voters", "the public", "the GOP base") — not entities.
  - Events (rallies, elections, debates) — events were retired in v15.0 because the LLM could not reliably distinguish a "happening" from a "process".
  - Quotes themselves as entities — those go in claim records, not the entity list.

CLAIM RECORDS — this is the new core output:
  For each significant political assertion in the article, emit ONE claim record:
    - entities: list of canonical entity names that appear in the quote
    - evidence_span: a verbatim quote from the article that contains those entities
    - label: an OPTIONAL shallow tag (see below). LEAVE NULL IF UNCERTAIN.
    - confidence: high | medium | low

  VERBATIM REQUIREMENT: evidence_span MUST be copy-pasted from the article
  text. Do not paraphrase, summarize, condense, or rewrite. Down to the
  punctuation and capitalization. Validators will reject non-verbatim
  spans and drop the record.

  ENTITY REQUIREMENT: every name in `entities` MUST appear in
  evidence_span (by canonical name, surface form, or recognized alias).
  Do not list entities the quote does not literally mention.

  SPAN GUIDANCE: aim for sentence-shaped spans of 50–300 characters.
  Avoid 1000-char paragraph dumps; avoid 20-char fragments that lose
  context. Self-contained is the goal.

  HOW MANY CLAIMS PER ARTICLE: typically 1–3 high-signal quotes. Long
  feature pieces may justify up to ~5. Routine news may produce 0 if
  no quote ties two-or-more entities together. Quality over quantity.

LABELS (closed set of 8 — OPTIONAL):
  statement       — generic political utterance; safe catch-all when label is unclear
  attack          — adversarial language toward a target (accusations, scandal claims, mockery)
  defense         — response, denial, pushback, fact-correction
  endorsement     — explicit endorsement language ("X endorsed Y", "Y received X's endorsement")
  policy_position — candidate's stated position on an issue ("Cognetti supports expanding ACA subsidies")
  vote            — description of a recorded legislative vote
  announcement    — campaign launch, candidacy filing, rollout, public schedule announcement
  commitment      — a promise or commitment to future action ("I will fight for ...")

  LEAVE label NULL if uncertain. NULL is a valid extraction result.
  Forcing a label was the failure mode that retired the previous schema.
  Better to extract a quote with no label than to invent a category.

CRITICAL: WHAT NOT TO DO
  - Do NOT generate triples like "X endorses Y" or "X attended Z". Those
    were the v14.x failure surface. If you find yourself thinking
    "subject, predicate, object", STOP — you're doing the wrong job.
  - Do NOT infer attendance, endorsement, or stance from context. If the
    article doesn't literally say it, don't extract it.
  - Do NOT include quotes from sources other than the article — only
    text that appears verbatim in the article body counts.
  - Do NOT compose claims from multiple paragraphs. One claim = one quote.
  - Do NOT extract quotes that contain no named entities. The output is
    entity-anchored evidence; an entity-less quote belongs nowhere.

Output strict JSON matching the ExtractionResult schema:
  {
    "entities": [
      {"name": "Paige Cognetti", "type": "person", "description": "...", "affiliation": "D"},
      {"name": "Rob Bresnahan", "type": "person", "description": "...", "affiliation": "R"}
    ],
    "claims": [
      {
        "entities": ["Paige Cognetti", "Rob Bresnahan"],
        "evidence_span": "Mayor of Scranton and Pa. Congressional candidate Paige Cognetti accused Bresnahan of public corruption involving stock trades",
        "label": "attack",
        "confidence": "high"
      },
      {
        "entities": ["Rob Bresnahan"],
        "evidence_span": "Bresnahan's office did not respond to a request for comment.",
        "label": null,
        "confidence": "high"
      }
    ]
  }

Note in the second example: a quote with one entity and no clear label
is still a VALID extraction. It documents that Bresnahan was the subject
of a non-response — useful context, no inference required.
"""

# The v14.x triple-schema prompt (5 entity types × 10 predicates × domain/range
# × stance rules × event-specific gates) lived here from v14.1 through v14.7.
# RETIRED in v15.0 — see app/services/extractor_versions.py for the rationale.
# If you ever need to reconstruct it for an audit or comparison, check the
# git history: `git log -p --follow app/services/entity_extraction.py`.
_LEGACY_V14X_PROMPT_REMOVED = """Removed in v15.0 — see EXTRACTOR_VERSION docstring above for context."""

# The remaining text below was the legacy v14.x prompt body, now unused.
# Kept inside this string literal so it doesn't interfere with the live
# system prompt above. Safe to delete entirely after a few sessions.
_UNUSED_LEGACY_PROMPT_BODY = """LEGACY v14.x — ignore unless explicitly told to use the triple schema:

ENTITY TYPES (exactly 5 — never invent new types):
  person        — humans (candidates, officials, journalists, donors, activists)
  organization  — groups acting as a unit (parties, PACs, unions, news outlets, advocacy, government agencies)
  bill          — specific named legislation (must have a clear identifying name like "ACA Subsidy Extension" or "H.R. 1234"). NOT broad policy topics.
  location      — named geographic places (cities, counties, districts, regions, states)
  event         — a specific dated/located political HAPPENING — something that takes place at one location over a bounded time window (hours to one day). Examples: a rally, a debate, a fundraiser, a Congressional vote, a town hall, a press conference, a specific endorsement ceremony.
                  STRICT REQUIREMENTS — ALL must hold:
                    (a) The event has a SPECIFIC name (e.g. "Wilkes-Barre rally", "Farmers for Free Trade roundtable"), not a generic process.
                    (b) You can extract event_date (YYYY-MM-DD ideally, YYYY-MM minimum) OR event_location.
                    (c) The event happens at ONE place over a SHORT time window — not a multi-day, multi-state, or multi-month process.
                  REJECT these as events (they are NOT happenings):
                    - "the 2024 election" / "the midterms" / "the 2026 Congressional Election" — these are months-long processes across many locations, NOT events.
                    - "the campaign" / "the launch" / "the primary season" — process language, not happenings.
                    - "the launch" / "the campaign event" — too vague, can't dedupe.
                  Election DAYS may be events ONLY when the article describes a specific gathering ON that day at one place (a watch party, a polling-location vote, an election-night speech). The election itself is NOT an event.

EVENT METADATA (only for type=event):
  event_date    — ISO date when known (YYYY-MM-DD or YYYY-MM)
  event_location — name of an associated location entity (must also appear in your entities list)
  event_type    — one of: rally | debate | fundraiser | vote | town_hall | press_conference | endorsement | other

DO NOT extract:
  - Generic topics or issues (healthcare, immigration, etc.) — these are handled separately.
  - Vague event mentions without a name + (date or location) — they're impossible to dedupe.
  - Generic phrases ("Four House Republicans", "voters", "the public", "the GOP base") — not entities.
  - Quotes or statements — those are evidence text on relations, not entities themselves.

PREDICATES (exactly 10 — never invent new verbs):
  endorses          — EXPLICIT public endorsement of a candidate or named bill.
                      ONLY use when the article literally says "X endorsed Y",
                      "X officially backed Y", "Y received X's endorsement",
                      or equivalent formal language. Do NOT use endorses for:
                        - discussing a bill favorably
                        - voting on a bill (use voted_for)
                        - signing a discharge petition (use co_sponsored)
                        - mere mention of agreement
                        - praising someone without an explicit endorsement
                      When in doubt, do NOT emit endorses.
  criticizes        — publicly criticizes (lighter than attack — political disagreement)
  attacks           — hostile attack, accusation, scandal claim
  voted_for         — voted yes on a specific bill in a recorded vote
  voted_against     — voted no on a specific bill in a recorded vote
  co_sponsored      — formally co-sponsored a bill, OR signed a discharge
                      petition to force a vote (procedural support)
  represents        — currently holds elected office for this district/location
  member_of         — is part of / leads / works for an organization
  predecessor_of    — held the same office before this person
  attended          — was PHYSICALLY PRESENT at a specific event. Object MUST
                      be an entity of type=event. STRICT REQUIREMENTS:
                        (a) The article must LITERALLY describe the subject's
                            presence at the event. The sample_quote MUST
                            contain an attendance verb tying the subject to
                            the event — e.g. "spoke at", "attended", "appeared
                            at", "was at", "addressed", "headlined",
                            "participated in", "joined", "rallied at",
                            "delivered remarks at", "took the stage at".
                        (b) DO NOT infer attendance from context. Examples
                            that ARE NOT evidence of attended:
                              - "X's office did not respond" — opposite signal
                              - "X blamed Y for high prices" — only attributes
                                a position, doesn't place X at any event
                              - "X won the election" — describes an outcome,
                                not attendance
                              - "X will appear at the rally tomorrow" — future
                                tense, not yet observed
                              - "X tweeted about the rally" — comment, not
                                attendance
                        (c) "running in" or "campaigning for" an election is
                            NEVER attended (and elections aren't events anyway
                            — see ENTITY TYPES).
                      When in doubt, do NOT emit attended.

DOMAIN/RANGE — every relation must satisfy these (mismatched ones get dropped):
  endorses / criticizes / attacks  — subject ∈ {person, organization}; object ∈ {person, organization, bill, event}
  voted_for / voted_against / co_sponsored — subject ∈ {person}; object ∈ {bill}
  represents — subject ∈ {person}; object ∈ {location, organization}
  member_of — subject ∈ {person, organization}; object ∈ {organization}
  predecessor_of — subject ∈ {person}; object ∈ {person}
  attended — subject ∈ {person, organization}; object ∈ {event}

  Examples of FORBIDDEN relations the extractor will silently drop:
    - location → represents → anything  (locations don't represent — only persons do)
    - person → represents → bill        (representing a district/org is geographic/structural, not legislative)
    - bill → attacks → person           (bills don't act)
    - location → endorses → person      (places don't endorse)
    - person → attended → person        (you attend events, not other persons)

RELATION STANCE — does this article ASSERT the relation or CONTEST it?
  Default stance: "supporting" (article asserts the relation as true).
  Use stance: "contesting" when the article explicitly:
    - DENIES the relation: "Bresnahan denied co-sponsoring the bill" → predicate=co_sponsored, stance=contesting
    - REFUTES it: "Fact check: this claim is false"
    - DISPUTES it: "Sources contradict the claim that..."
    - CORRECTS prior reporting: "Earlier reports were wrong about..."
  The predicate stays the same (the logical fact being discussed); the stance
  tells the system whether this article supports or disputes that fact.
  When in doubt, default to supporting.

RULES:
  - Only extract entities that are NAMED in the article. Do NOT invent or generalize.
  - For each entity, pick ONE canonical name (not the article's exact phrasing — use the proper name, e.g. "Paige Cognetti" not "the mayor").
  - For relations, the subject_name and object_name MUST exactly match one of the entity names you extracted in the same article.
  - sample_quote should be a short (under 30 words) verbatim excerpt that supports the claim.
  - If you're unsure about a relation, use confidence="low" — don't fabricate.

Output strict JSON matching the ExtractionResult schema:
  {
    "entities": [
      {"name": "Paige Cognetti", "type": "person", "description": "...", "affiliation": "D"},
      {"name": "Wilkes-Barre rally", "type": "event", "event_date": "2026-04-15",
       "event_location": "Wilkes-Barre", "event_type": "rally"},
      ...
    ],
    "relations": [
      {"subject_name": "JD Vance", "predicate": "attended", "object_name": "Wilkes-Barre rally",
       "sample_quote": "VP Vance headlined the rally", "confidence": "high"},
      ...
    ]
  }
"""


# ── Canonicalization ──────────────────────────────────────────────────────

@dataclass
class CanonicalizationResult:
    """Result of mapping an ExtractedEntity to a canonical Entity row.
    new_canonical_id is set when we created a fresh entity (no existing match)."""
    entity_id: int
    canonical_id: str
    matched_via: Literal["seed_name", "seed_alias", "embedding", "fresh"]
    confidence: Confidence


def _normalize_for_match(s: str) -> str:
    """Lowercase + strip punctuation + collapse whitespace. Used to match
    article surface forms against canonical names / aliases."""
    import re
    s = s.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


# Type-bucket sanity sets. We reject entities whose name is clearly the
# wrong type. Bounded — we don't try to comprehensively validate every
# entity; we only block the specific patterns observed in production.
# Add new patterns when audits surface them.

_TEMPORAL_NOT_LOCATION: frozenset[str] = frozenset({
    # Months — the LLM occasionally emits "May" or "November" as a location
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec",
    # Seasons
    "spring", "summer", "fall", "autumn", "winter",
    # Day-relative references (rare but seen)
    "today", "tomorrow", "yesterday", "tonight",
    # Year references
    "this year", "next year", "last year",
})

_ROLE_TITLES_NOT_ORG: frozenset[str] = frozenset({
    # Standalone role titles the LLM occasionally puts in the org bucket.
    # We require the org name to be the GROUP, not the role
    # ("U.S. House" → org; "U.S. Representative" → role, reject).
    "representative", "u.s. representative", "us representative",
    "congressman", "congresswoman",
    "senator", "u.s. senator", "us senator",
    "governor", "lieutenant governor",
    "mayor", "deputy mayor",
    "councilman", "councilwoman", "councilmember", "councilor",
    "president", "vice president", "vp",
    "secretary", "deputy secretary",
    "attorney general", "deputy attorney general",
    "judge", "justice", "chief justice",
    "ambassador",
})

_TOPIC_NOT_BILL: frozenset[str] = frozenset({
    # Single-word/short policy topics the prompt forbids — but the LLM
    # sometimes still emits them under type=bill. The schema says bills
    # must be "specific named legislation", not policy areas.
    "healthcare", "health care", "healthcare subsidies",
    "tax cuts", "tax policy", "taxes",
    "immigration", "border security",
    "infrastructure", "education", "abortion",
    "the budget", "the federal budget", "the deficit",
    "spending", "discretionary spending",
})


def _is_valid_type_member(entity_type: str, name: str) -> bool:
    """Reject obvious type-bucket errors before any matching.

    The LLM occasionally emits entities whose TYPE doesn't match the
    schema's definition of that type — e.g. "May" as location (it's a
    month), "U.S. Representative" as organization (it's a role title),
    "healthcare subsidies" as bill (it's a policy topic, not legislation).

    Returns True if the (type, name) pair passes the sanity check,
    False if it should be rejected at canonicalization time.
    """
    if not name:
        return False
    name_lc = name.strip().lower()
    if not name_lc:
        return False

    if entity_type == "location":
        if name_lc in _TEMPORAL_NOT_LOCATION:
            return False
        return True

    if entity_type == "organization":
        if name_lc in _ROLE_TITLES_NOT_ORG:
            return False
        return True

    if entity_type == "bill":
        if name_lc in _TOPIC_NOT_BILL:
            return False
        # "{YYYY} Budget" forms — city/state operating budgets, not
        # federal bills. Reject anything that's just a year + budget.
        import re as _re
        if _re.match(r"^(19|20)\d{2}\s+(budget|spending plan)$", name_lc):
            return False
        return True

    return True  # person / event / other types — default allow


def canonicalize_entity(
    db: Session,
    extracted: ExtractedEntity,
) -> CanonicalizationResult:
    """Map one extracted entity to a canonical Entity row.

    Resolution order (cheap → expensive):
      0. (Events only) STRICT dedup via (name, date) or (name, location) —
         events MUST have one of date/location per the v14.5 contract.
      1. Direct name match against an existing canonical entity of the same type
      2. Alias match against an existing entity (e.g. "Mayor Cognetti" → cognetti)
      3. Embedding similarity (Phase 3 — not yet wired)
      4. Create new entity with auto-generated canonical_id

    Returns the resulting Entity.id + the canonical_id + how we matched.
    """
    norm_name = _normalize_for_match(extracted.name)
    if not norm_name:
        # Should never happen, but be defensive — create a placeholder.
        return _create_fresh_entity(db, extracted)

    # Type-bucket sanity gate (v15.0+). Reject entities whose TYPE doesn't
    # match what the schema means by that type — months as locations,
    # role titles as orgs, policy topics as bills. Bounded list of known
    # failure patterns; caller treats entity_id=-1 as rejected (claim
    # validator drops the entity, may still keep the claim if other
    # entities survive).
    if not _is_valid_type_member(extracted.type, extracted.name):
        return CanonicalizationResult(
            entity_id=-1, canonical_id="", matched_via="fresh", confidence="low",
        )

    # v14.5 event-specific gate: events without date or location are
    # rejected. The LLM is told this explicitly; this is a backstop.
    if extracted.type == "event":
        if not extracted.event_date and not extracted.event_location:
            # Reject — caller treats entity_id=-1 as unresolved and skips.
            return CanonicalizationResult(
                entity_id=-1, canonical_id="", matched_via="fresh", confidence="low",
            )

    # 1. Direct match: same type + name matches canonical name
    existing = (
        db.query(Entity)
        .filter(Entity.type == extracted.type)
        .all()
    )
    for ent in existing:
        if _normalize_for_match(ent.name) == norm_name:
            # For events, also require matching date OR location, otherwise
            # different events with the same name are treated as distinct.
            if extracted.type == "event":
                try:
                    ent_meta = json.loads(ent.metadata_json or "{}")
                except Exception:
                    ent_meta = {}
                same_date = (extracted.event_date and
                             ent_meta.get("event_date") == extracted.event_date)
                same_loc = (extracted.event_location and
                            ent_meta.get("event_location") == extracted.event_location)
                if not (same_date or same_loc):
                    continue
                # v14.6 — cross-document timeline reconciliation. Even when
                # dates agree exactly, log this as an observation; when they
                # disagree on the date but agree on location, record both
                # date observations so the UI can surface the discrepancy.
                _record_event_date_observation(ent, ent_meta, extracted)
            return CanonicalizationResult(
                entity_id=ent.id,
                canonical_id=ent.canonical_id,
                matched_via="seed_name",
                confidence="high",
            )

    # 2. Alias match: same type + name matches one of the entity's aliases
    for ent in existing:
        if not ent.aliases:
            continue
        try:
            aliases = json.loads(ent.aliases)
        except Exception:
            continue
        for alias in aliases:
            if _normalize_for_match(alias) == norm_name:
                return CanonicalizationResult(
                    entity_id=ent.id,
                    canonical_id=ent.canonical_id,
                    matched_via="seed_alias",
                    confidence="high",
                )

    # 2.3. District surface-form match (location entities only).
    # The LLM emits many variants for the campaign's congressional
    # district — "8th Congressional District", "Pennsylvania's 8th",
    # "Eighth Congressional District", "PA-08", etc. Each used to land
    # as a separate auto-discovered entity, fragmenting the location
    # graph. Now: if the extracted name matches ANY plausible surface
    # form of the campaign's configured district, route it to the
    # seeded canonical (loc:{state}-{NN}). Generic across any race.
    if extracted.type == "location":
        from app.services.canonicalize_district import is_district_surface_form
        from app.models import CampaignConfig
        config = db.query(CampaignConfig).first()
        district_code = (config.district or "").strip() if config else ""
        if district_code and is_district_surface_form(extracted.name, district_code):
            # Find the seeded canonical (lookup by canonical_id pattern)
            seeded_id = f"loc:{district_code.lower()}"
            seeded = (
                db.query(Entity)
                .filter(Entity.canonical_id == seeded_id)
                .first()
            )
            if seeded:
                # Add the incoming form as an alias for cheaper match next time
                try:
                    cur_aliases = json.loads(seeded.aliases) if seeded.aliases else []
                except Exception:
                    cur_aliases = []
                if extracted.name and extracted.name not in cur_aliases and extracted.name != seeded.name:
                    cur_aliases.append(extracted.name)
                    seeded.aliases = json.dumps(cur_aliases)
                return CanonicalizationResult(
                    entity_id=seeded.id,
                    canonical_id=seeded.canonical_id,
                    matched_via="seed_alias",
                    confidence="high",
                )

    # 2.4. Honorific-stripped match (person entities only).
    # If the extracted name has a leading honorific ("Dr. Mehmet Oz",
    # "Rep. Bresnahan", "Mayor Cognetti") or trailing suffix ("Jr.",
    # "MD"), strip them and retry the exact-name match against the
    # existing inventory. When matched, register the original form
    # as an alias.
    if extracted.type == "person":
        from app.services.honorifics import strip_honorifics
        stripped_h, removed = strip_honorifics(extracted.name)
        if removed and stripped_h:
            stripped_norm = _normalize_for_match(stripped_h)
            for ent in existing:
                if _normalize_for_match(ent.name) == stripped_norm:
                    try:
                        cur_aliases = json.loads(ent.aliases) if ent.aliases else []
                    except Exception:
                        cur_aliases = []
                    if extracted.name not in cur_aliases and extracted.name != ent.name:
                        cur_aliases.append(extracted.name)
                        ent.aliases = json.dumps(cur_aliases)
                    return CanonicalizationResult(
                        entity_id=ent.id,
                        canonical_id=ent.canonical_id,
                        matched_via="seed_alias",
                        confidence="high",
                    )

    # 2.5. Nickname-aware match (person entities only).
    # Catches variants embedding similarity misses because first names
    # diverge: "Patricia Beynon" ↔ "Trish Beynon" → same person.
    # See app/services/nicknames.py for the dictionary + match logic.
    # When multiple existing entities match (i.e. the inventory is already
    # fragmented), prefer the one with the most mentions — that's the
    # most-attested canonical form. Future cleanup passes can merge the
    # rest. Auto-promotes the incoming form to an alias on the chosen
    # canonical so subsequent identical extractions hit the cheap alias path.
    if extracted.type == "person":
        from app.services.nicknames import person_names_match, strip_quoted_nickname
        stripped, quoted_nick = strip_quoted_nickname(extracted.name)
        candidate_names = {extracted.name, stripped} if stripped != extracted.name else {extracted.name}
        # Collect ALL matches, then pick the one with the most mentions.
        nickname_matches: list[Entity] = []
        for ent in existing:
            for candidate in candidate_names:
                if person_names_match(ent.name, candidate):
                    nickname_matches.append(ent)
                    break
        if nickname_matches:
            best = max(nickname_matches, key=lambda e: e.mention_count or 0)
            # Add the incoming form(s) as aliases on the chosen canonical
            try:
                cur_aliases = json.loads(best.aliases) if best.aliases else []
            except Exception:
                cur_aliases = []
            added = False
            for new_alias in candidate_names:
                if new_alias and new_alias not in cur_aliases and new_alias != best.name:
                    cur_aliases.append(new_alias)
                    added = True
            if quoted_nick and quoted_nick not in cur_aliases:
                cur_aliases.append(quoted_nick)
                added = True
            if added:
                best.aliases = json.dumps(cur_aliases)
            return CanonicalizationResult(
                entity_id=best.id,
                canonical_id=best.canonical_id,
                matched_via="seed_alias",
                confidence="high",
            )

    # 3. Embedding similarity → Phase 3. For now, fall through to fresh.
    # TODO(phase3): use existing app.services.embeddings to compute the
    # extracted entity's name embedding, compare to cached embeddings of
    # canonical entities, accept matches over ~0.85 cosine similarity.

    # 4. Create new canonical entity (auto-discovered)
    return _create_fresh_entity(db, extracted)


def _record_event_date_observation(ent: Entity, ent_meta: dict, extracted: ExtractedEntity) -> None:
    """Append an event-date observation to the entity's metadata_json.

    Cross-document reconciliation: when multiple articles disagree on an
    event's date, we don't pick one — we record all observations along
    with the (running) primary date (the most-attested value). UI consumers
    can surface "5 articles say April 9, 1 says April 10" rather than
    silently picking one.
    """
    from collections import Counter
    if not extracted.event_date:
        return  # Article had no date claim; nothing to observe
    obs: list[dict] = ent_meta.get("date_observations") or []
    obs.append({"date": extracted.event_date})
    # Cap at 50 most recent
    obs = obs[-50:]
    # Recompute primary as most-frequent observed date.
    counts = Counter(o["date"] for o in obs if o.get("date"))
    primary = counts.most_common(1)[0][0] if counts else extracted.event_date
    ent_meta["date_observations"] = obs
    ent_meta["event_date"] = primary
    # Track distinct date count so consumers can detect conflict quickly
    ent_meta["date_disagreement"] = len(set(o["date"] for o in obs if o.get("date"))) > 1
    ent.metadata_json = json.dumps(ent_meta)


def _create_fresh_entity(db: Session, extracted: ExtractedEntity) -> CanonicalizationResult:
    """Create a new canonical Entity row with auto-generated canonical_id.

    canonical_id format: "<type>:auto:<slug>" where slug is name.lower().replace.
    If a collision somehow happens, we append :2, :3, etc.
    """
    import re
    base_slug = re.sub(r"[^\w-]+", "-", extracted.name.lower().strip()).strip("-")
    if not base_slug:
        base_slug = "unknown"
    candidate_id = f"{extracted.type}:auto:{base_slug}"
    n = 2
    while db.query(Entity).filter(Entity.canonical_id == candidate_id).first():
        candidate_id = f"{extracted.type}:auto:{base_slug}-{n}"
        n += 1

    # For event entities, capture date/location/type metadata so the
    # canonicalizer can dedupe future surface-form variants.
    metadata_json = None
    if extracted.type == "event":
        md: dict = {}
        if extracted.event_date:
            md["event_date"] = extracted.event_date
            # Seed the observation list so later canonicalizations can
            # add to it; cross-document timeline reconciliation.
            md["date_observations"] = [{"date": extracted.event_date}]
            md["date_disagreement"] = False
        if extracted.event_location:
            md["event_location"] = extracted.event_location
        if extracted.event_type:
            md["event_type"] = extracted.event_type
        if md:
            metadata_json = json.dumps(md)

    ent = Entity(
        canonical_id=candidate_id,
        type=extracted.type,
        name=extracted.name,
        aliases=json.dumps([]),
        description=extracted.description,
        affiliation=extracted.affiliation,
        metadata_json=metadata_json,
        seeded=False,
    )
    db.add(ent)
    # Race-safe insert: in parallel-worker mode, two workers can both
    # decide to create the same fresh entity simultaneously. The canonical_id
    # UNIQUE constraint means one wins, the other gets an IntegrityError.
    # Catch it, re-query the winning row, return its id.
    from sqlalchemy.exc import IntegrityError as _IntegrityError
    try:
        db.flush()  # populate id without committing the whole transaction
    except _IntegrityError:
        db.rollback()
        existing = (
            db.query(Entity)
            .filter(Entity.canonical_id == candidate_id)
            .one()
        )
        return CanonicalizationResult(
            entity_id=existing.id,
            canonical_id=existing.canonical_id,
            matched_via="seed_name",  # logged as if it matched an existing one
            confidence="high",
        )
    return CanonicalizationResult(
        entity_id=ent.id,
        canonical_id=ent.canonical_id,
        matched_via="fresh",
        confidence="medium",
    )


# ── Persistence ────────────────────────────────────────────────────────────

def _rewrite_remove_article_contribution(db: Session, article_id: int) -> dict:
    """Remove this article's previous contribution to all relations so we can
    re-extract it fresh under a new prompt/extractor version.

    For each EntityRelation R whose evidence_json contains an entry with
    article_id=this:
      - drop that entry from evidence_json
      - drop article_id from source_articles list
      - decrement weight by 1 (assuming one mention per article per relation)
      - if weight reaches 0, delete R

    Also drops the EntityMention rows for this article so they get re-created.

    Returns counts for logging.
    """
    stats = {"relations_decremented": 0, "relations_deleted": 0, "mentions_dropped": 0}

    # Drop all EntityMention rows from this article first
    mentions = db.query(EntityMention).filter(EntityMention.article_id == article_id).all()
    for m in mentions:
        db.delete(m)
        stats["mentions_dropped"] += 1

    # Find all relations whose evidence_json mentions this article. We can't
    # use SQL JSON ops portably, so iterate. At ~2400 relations this is fast.
    rels = db.query(EntityRelation).all()
    aid = article_id
    for r in rels:
        try:
            evidence = json.loads(r.evidence_json) if r.evidence_json else []
        except Exception:
            evidence = []
        try:
            src_list = json.loads(r.source_articles) if r.source_articles else []
        except Exception:
            src_list = []
        had_article = any(ev.get("article_id") == aid for ev in evidence) or (aid in src_list)
        if not had_article:
            continue
        # Drop the article from both fields
        evidence = [ev for ev in evidence if ev.get("article_id") != aid]
        src_list = [x for x in src_list if x != aid]
        r.evidence_json = json.dumps(evidence)
        r.source_articles = json.dumps(src_list)
        r.weight = max(0, (r.weight or 1) - 1)
        if r.weight <= 0:
            db.delete(r)
            stats["relations_deleted"] += 1
        else:
            stats["relations_decremented"] += 1
    return stats


# ── v15.0 — quote-anchored claim record persistence ─────────────────────

def _normalize_for_hash(text: str) -> str:
    """Normalize a quote span for deterministic hashing.

    Goals:
      - Identical AP-syndicated phrasing across outlets collapses to one hash.
      - Trivial whitespace / smart-quote / em-dash differences don't fork rows.
      - Case differences don't fork rows.

    Not goals:
      - Semantic similarity. Two paraphrases of the same idea must still
        hash differently — clustering is a separate concern (post-v15.0).
    """
    import re as _re
    if not text:
        return ""
    s = text.strip().lower()
    # Unify quote characters
    s = s.replace("‘", "'").replace("’", "'")  # curly single
    s = s.replace("“", '"').replace("”", '"')  # curly double
    s = s.replace("–", "-").replace("—", "-")  # en/em dash → hyphen
    s = s.replace("\xa0", " ")  # non-breaking space → space
    # Collapse all whitespace runs to single space
    s = _re.sub(r"\s+", " ", s)
    return s


def _compute_evidence_hash(text: str) -> str:
    """SHA1 of the normalized text. Used as claim_records.evidence_hash."""
    import hashlib
    norm = _normalize_for_hash(text)
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def _find_verbatim_offsets(span: str, article_text: str) -> Optional[tuple[int, int]]:
    """Locate `span` inside `article_text` and return (start_char, end_char).

    Tries progressively more lenient matches:
      1. Exact substring (preserves case, punctuation, whitespace).
      2. Case-insensitive substring.
      3. Normalized-form match (smart quotes / dashes / whitespace flattened).

    Returns None if no match found at any tier — that's a verbatim-validation
    failure and the claim should be rejected.

    The returned offsets are into the ORIGINAL article_text, not the
    normalized form, so the UI can highlight the actual on-page bytes.
    """
    if not span or not article_text:
        return None

    # Tier 1: exact
    idx = article_text.find(span)
    if idx >= 0:
        return (idx, idx + len(span))

    # Tier 2: case-insensitive — find in lowercase, return original-text offsets
    span_lc = span.lower()
    text_lc = article_text.lower()
    idx = text_lc.find(span_lc)
    if idx >= 0:
        return (idx, idx + len(span))

    # Tier 3: normalized — find in normalized text, then walk back to find a
    # plausible original-text range. This is approximate; if normalization
    # collapses several chars (e.g. "  " → " ") the end offset may overshoot
    # by a small amount. Acceptable for UI highlighting.
    norm_span = _normalize_for_hash(span)
    norm_text = _normalize_for_hash(article_text)
    nidx = norm_text.find(norm_span)
    if nidx < 0:
        return None
    # Approximate original-text mapping: scan article_text and count
    # normalized chars until we hit nidx, then continue until norm_span length.
    start_orig = _approx_unnormalized_offset(article_text, nidx)
    end_orig = _approx_unnormalized_offset(article_text, nidx + len(norm_span))
    if start_orig is None or end_orig is None:
        return None
    return (start_orig, end_orig)


def _approx_unnormalized_offset(article_text: str, normalized_target: int) -> Optional[int]:
    """Walk article_text and find the original-text offset whose normalized
    prefix length equals `normalized_target`. Returns None if the target
    exceeds the normalized text length."""
    if normalized_target <= 0:
        return 0
    norm_count = 0
    last_was_space = False
    for i, ch in enumerate(article_text):
        # Replicate the normalization choices in _normalize_for_hash, char-by-char.
        if ch in "‘’“”–—\xa0":
            mapped = ch  # gets replaced with single-char, length-preserving
            norm_count += 1
            last_was_space = False
        elif ch.isspace():
            if not last_was_space:
                norm_count += 1
                last_was_space = True
        else:
            norm_count += 1
            last_was_space = False
        if norm_count >= normalized_target:
            return i + 1
    return None


def _entity_appears_in_span(entity: Entity, span: str) -> bool:
    """True if the entity is referenced by name or alias in the span.

    Case-insensitive. Aliases come from entity.aliases (JSON-encoded list).
    Pronouns and role-words ("the mayor", "she") are NOT considered — they
    must be explicit aliases for this to match. This is intentional:
    pronoun-based matching is exactly how v14.x hallucination crept in.
    """
    span_lc = span.lower()
    if entity.name and entity.name.lower() in span_lc:
        return True
    if entity.aliases:
        try:
            aliases = json.loads(entity.aliases)
        except Exception:
            aliases = []
        for a in aliases:
            if a and a.lower() in span_lc:
                return True
    return False


def persist_claims(
    db: Session,
    article_id: int,
    result: ExtractionResult,
) -> dict:
    """Write v15.0 claim records to the database.

    Process per article:
      1. Canonicalize each ExtractedEntity (writes Entity + EntityMention).
         Skip event-typed entities — v15.0 retired the type.
      2. For each ExtractedClaim:
         a. Look up the article's raw_text.
         b. Validate evidence_span is verbatim — find offsets.
         c. Compute evidence_hash.
         d. Look up each entity by name + check it appears in the span.
         e. If hash already exists: skip (syndication dedup).
         f. Else: insert ClaimRecord + ClaimRecordEntity rows.

    Returns a stats dict with counts of each step + rejections.
    """
    from app.models import ClaimRecord, ClaimRecordEntity

    stats = {
        "mentions_created": 0,
        "claims_created": 0,
        "claims_skipped_duplicate_hash": 0,
        "claims_rejected_non_verbatim": 0,
        "claims_rejected_no_entities_in_span": 0,
        "claims_rejected_unresolved_entity": 0,
        "entities_rejected_event_type_retired": 0,
        # Legacy fields the backfill script reads — keep them present + zero
        # so the script doesn't blow up when migrating.
        "relations_created": 0,
        "relations_strengthened": 0,
    }

    # ── Step 1: entities (reuse existing canonicalization path, but skip events) ──
    canon: dict[int, CanonicalizationResult] = {}
    for i, e in enumerate(result.entities):
        if e.type == "event":
            stats["entities_rejected_event_type_retired"] += 1
            continue
        canon[i] = canonicalize_entity(db, e)

    article = db.query(SourceItem).filter(SourceItem.id == article_id).first()
    if not article:
        return stats
    article_text = article.raw_text or ""
    article_ts = article.published_at if article else datetime.utcnow()

    # Mention bookkeeping (idempotent via UNIQUE constraint + intra-call dedup).
    # The DB query catches mentions ALREADY in the DB; the in-Python
    # set catches mentions PENDING in the current session that the DB
    # query can't see because autoflush=False. Without the local set,
    # two extracted entities that canonicalize to the same canonical
    # (e.g. "Rob Bresnahan" + "Bresnahan" → same person) both think
    # they're the first mention, both add an EntityMention row, and
    # the UNIQUE constraint blows up at commit time. Bug observed in
    # Day-2 stage-1 v15.0 backfill on article 4151.
    pending_mentions_this_call: set[int] = set()
    for i, ext in enumerate(result.entities):
        cr = canon.get(i)
        if not cr or cr.entity_id < 0:
            continue
        if cr.entity_id in pending_mentions_this_call:
            continue  # already added a mention for this entity in this call
        ent = db.query(Entity).filter(Entity.id == cr.entity_id).one()
        existing_mention = (
            db.query(EntityMention)
            .filter(EntityMention.article_id == article_id,
                    EntityMention.entity_id == cr.entity_id)
            .first()
        )
        if not existing_mention:
            ent.mention_count = (ent.mention_count or 0) + 1
            if not ent.first_seen or (article_ts and article_ts < ent.first_seen):
                ent.first_seen = article_ts
            if not ent.last_seen or (article_ts and article_ts > ent.last_seen):
                ent.last_seen = article_ts
            db.add(EntityMention(
                article_id=article_id,
                entity_id=cr.entity_id,
                surface_text=ext.surface_text or ext.name,
                confidence=cr.confidence,
                extraction_method=cr.matched_via,
            ))
            pending_mentions_this_call.add(cr.entity_id)
            stats["mentions_created"] += 1
            ent.source_count = (ent.source_count or 0) + 1
        else:
            # Already in DB. Still record locally so a later iteration
            # in this call skips it without re-querying.
            pending_mentions_this_call.add(cr.entity_id)

    # ── Step 2: claim records ──
    # Build name → entity_id index for fast lookup
    name_to_eid: dict[str, int] = {}
    for i, ext in enumerate(result.entities):
        cr = canon.get(i)
        if not cr or cr.entity_id < 0:
            continue
        name_to_eid[ext.name.strip().lower()] = cr.entity_id
        ent = db.query(Entity).filter(Entity.id == cr.entity_id).one()
        name_to_eid[ent.name.strip().lower()] = cr.entity_id

    for claim_ext in result.claims:
        # Verbatim check + offset resolution
        offsets = _find_verbatim_offsets(claim_ext.evidence_span, article_text)
        if offsets is None:
            stats["claims_rejected_non_verbatim"] += 1
            continue
        start_char, end_char = offsets

        # Resolve entity names → ids, dropping individual entities that
        # don't ground in the span. The claim survives as long as ≥1
        # entity is grounded. Softened from the initial v15.0 release
        # after the Day 1 audit found the all-or-nothing rule was killing
        # good claims because the LLM listed extra entities that appear
        # in the surrounding article but not in the specific quote span.
        # New behavior: drop the bad entity, keep the claim.
        resolved_entity_ids: list[int] = []
        for ename in claim_ext.entities:
            eid = name_to_eid.get(ename.strip().lower())
            if not eid:
                # LLM listed an entity in the claim that isn't in the
                # top-level entities[] array. Drop the entity; keep
                # processing the rest. Tracked for visibility but not
                # a rejection signal.
                stats["entities_dropped_unresolved"] = stats.get(
                    "entities_dropped_unresolved", 0
                ) + 1
                continue
            ent = db.query(Entity).filter(Entity.id == eid).one()
            if not _entity_appears_in_span(ent, claim_ext.evidence_span):
                # Entity canonicalizes fine but its name/aliases don't
                # appear in this specific span. The LLM was inferring
                # from the surrounding article; drop it.
                stats["entities_dropped_not_in_span"] = stats.get(
                    "entities_dropped_not_in_span", 0
                ) + 1
                continue
            resolved_entity_ids.append(eid)

        if not resolved_entity_ids:
            # ZERO grounded entities → the claim has no anchor in the
            # entity inventory. Reject the whole claim.
            stats["claims_rejected_no_entities_in_span"] += 1
            continue

        # Compute hash + check for dedup
        ev_hash = _compute_evidence_hash(claim_ext.evidence_span)
        existing = (
            db.query(ClaimRecord)
            .filter(ClaimRecord.evidence_hash == ev_hash)
            .one_or_none()
        )
        if existing:
            # Syndicated dup. Don't write a new claim row, but DO ensure the
            # entity-mention bookkeeping covers this article (already done
            # above). Skip silently.
            stats["claims_skipped_duplicate_hash"] += 1
            continue

        # Apply deterministic label correction before insert. Zero-cost
        # (pure regex) — overrides the LLM's label when quote text contains
        # clear trigger words (e.g. "voted in favor of" → vote), downgrades
        # to null when the LLM picked a label but no triggers are present.
        # See app/services/label_correction.py for the rule details +
        # rationale (manual audit found LLM labels were ~70% accurate,
        # rules push that up substantially without any new LLM calls).
        from app.services.label_correction import correct_label
        corrected_label, _rule = correct_label(claim_ext.evidence_span, claim_ext.label)

        # Insert. Race-safe: in parallel-worker mode, two workers can
        # independently extract the same syndicated quote at the same time
        # and both pass the existing-hash check above. The UNIQUE constraint
        # on evidence_hash means one of them gets an IntegrityError on
        # flush — catch it, re-query, treat as a syndication dup.
        from sqlalchemy.exc import IntegrityError as _IntegrityError
        new_cr = ClaimRecord(
            article_id=article_id,
            evidence_span=claim_ext.evidence_span,
            evidence_start_char=start_char,
            evidence_end_char=end_char,
            evidence_hash=ev_hash,
            label=corrected_label,
            confidence=claim_ext.confidence,
            extractor_version=EXTRACTOR_VERSION,
        )
        db.add(new_cr)
        try:
            db.flush()  # populate id
        except _IntegrityError:
            db.rollback()
            # Another worker raced us to insert this evidence_hash.
            # Treat as a syndicated duplicate.
            stats["claims_skipped_duplicate_hash"] += 1
            stats["claims_race_lost"] = stats.get("claims_race_lost", 0) + 1
            continue
        for eid in resolved_entity_ids:
            # Find surface_text by re-extracting the matched substring
            ent = db.query(Entity).filter(Entity.id == eid).one()
            surface = ent.name  # could improve to actual matched alias
            db.add(ClaimRecordEntity(
                claim_record_id=new_cr.id,
                entity_id=eid,
                surface_text=surface,
            ))
        stats["claims_created"] += 1

    db.commit()
    return stats


def persist_extraction(
    db: Session,
    article_id: int,
    result: ExtractionResult,
    rewrite: bool = False,
) -> dict:
    """⚠️ LEGACY (pre-v15.0). Writes triple-shaped data to claims/claim_supports/
    entity_relations. Kept for backward-compat with the legacy backfill
    scripts. NEW work should call persist_claims() instead.

    Set rewrite=True when re-extracting an article under a new prompt — it
    first removes the article's existing contribution from all relations
    (decrementing weights, deleting if weight hits 0), then writes fresh
    relations from the new extraction. This is destructive on the prior
    extraction but bounded to the article being re-processed.

    Returns a stats dict for logging / debugging:
      {"mentions_created": int, "relations_created": int, "relations_strengthened": int}
    """
    stats = {"mentions_created": 0, "relations_created": 0,
             "relations_strengthened": 0, "relations_skipped_unresolved": 0,
             "rewrite_relations_decremented": 0, "rewrite_relations_deleted": 0,
             "rewrite_mentions_dropped": 0}

    if rewrite:
        rstats = _rewrite_remove_article_contribution(db, article_id)
        stats["rewrite_relations_decremented"] = rstats["relations_decremented"]
        stats["rewrite_relations_deleted"] = rstats["relations_deleted"]
        stats["rewrite_mentions_dropped"] = rstats["mentions_dropped"]
        # Decrement entity mention counts to match the dropped mentions.
        # Cheap recompute: SQL UPDATE that recounts from entity_mentions.
        # Done at the end of the function so the new mentions we're about to
        # write are included in the count.

    # Canonicalize each entity, build the index → canonical entity_id map
    canon: dict[int, CanonicalizationResult] = {}
    for i, e in enumerate(result.entities):
        canon[i] = canonicalize_entity(db, e)

    # Update mention counts / first_seen / last_seen
    article = db.query(SourceItem).filter(SourceItem.id == article_id).first()
    article_ts = article.published_at if article else datetime.utcnow()

    for i, ext in enumerate(result.entities):
        cr = canon[i]
        # Skip entities that canonicalize_entity rejected (e.g. events
        # missing required date/location).
        if cr.entity_id < 0:
            stats["entities_rejected_dedup_unsafe"] = stats.get(
                "entities_rejected_dedup_unsafe", 0
            ) + 1
            continue
        ent = db.query(Entity).filter(Entity.id == cr.entity_id).one()

        # Idempotency: only bump counters / write a mention when this article
        # hasn't already contributed to the entity. Pre-fix this incremented
        # mention_count unconditionally, so a re-run inflated counts even when
        # the UNIQUE mention row was correctly skipped.
        existing = (
            db.query(EntityMention)
            .filter(EntityMention.article_id == article_id,
                    EntityMention.entity_id == cr.entity_id)
            .first()
        )
        if not existing:
            ent.mention_count = (ent.mention_count or 0) + 1
            if not ent.first_seen or (article_ts and article_ts < ent.first_seen):
                ent.first_seen = article_ts
            if not ent.last_seen or (article_ts and article_ts > ent.last_seen):
                ent.last_seen = article_ts
            db.add(EntityMention(
                article_id=article_id,
                entity_id=cr.entity_id,
                surface_text=ext.surface_text,
                confidence=cr.confidence,
                extraction_method=cr.matched_via,
            ))
            stats["mentions_created"] += 1
            ent.source_count = (ent.source_count or 0) + 1

    # Build name → entity_id index from the entities we just canonicalized,
    # so relations can lookup by entity name (not fragile index).
    name_to_eid: dict[str, int] = {}
    for i, ext in enumerate(result.entities):
        cr = canon[i]
        if cr.entity_id < 0:
            continue  # entity was rejected at canonicalization (e.g. dedup-unsafe event)
        # The LLM might refer to the entity by the name it provided OR by
        # the canonical name after our resolution. Index both.
        name_to_eid[ext.name.strip().lower()] = cr.entity_id
        ent = db.query(Entity).filter(Entity.id == cr.entity_id).one()
        name_to_eid[ent.name.strip().lower()] = cr.entity_id

    # Persist relations (incrementing weight on duplicates).
    # Relations are looked up by name (V14.1 refactor — was index-based).
    relations_skipped = 0
    relations_rejected_by_constraints = 0
    for rel in result.relations:
        s_id = name_to_eid.get(rel.subject_name.strip().lower())
        o_id = name_to_eid.get(rel.object_name.strip().lower())
        if not s_id or not o_id:
            relations_skipped += 1
            continue  # subject or object not in extracted entities — drop
        if s_id == o_id:
            continue  # self-relations don't make sense

        # Domain/range constraint check (V14.3). Reject nonsense like
        # location→represents→person or bill→attacks→organization.
        subj_ent = db.query(Entity).filter(Entity.id == s_id).first()
        obj_ent = db.query(Entity).filter(Entity.id == o_id).first()
        if subj_ent and obj_ent and not relation_type_allowed(
            subj_ent.type, rel.predicate, obj_ent.type
        ):
            relations_rejected_by_constraints += 1
            continue

        # Commonsense rule check — catches role-aware violations like
        # POTUS→represents→district, Senator→represents→city, etc.
        from app.services.commonsense_rules import evaluate as commonsense_evaluate
        action, rule_name = commonsense_evaluate(subj_ent, rel.predicate, obj_ent)
        if action == "reject":
            stats["relations_rejected_by_commonsense"] = stats.get(
                "relations_rejected_by_commonsense", 0
            ) + 1
            continue
        elif action == "downgrade_confidence":
            # Mutate the local rel object's confidence — persisted below
            rel.confidence = "low"
        # flag_for_review is non-blocking; the review queue picks it up

        existing = (
            db.query(EntityRelation)
            .filter(
                EntityRelation.subject_id == s_id,
                EntityRelation.predicate == rel.predicate,
                EntityRelation.object_id == o_id,
            )
            .first()
        )
        # Build the per-article evidence dict for this extraction. Even when
        # we strengthen an existing relation we append a new evidence entry
        # so we can later see WHICH articles produced the relation and which
        # ones had explicit supporting quotes vs. just a name co-occurrence.
        new_evidence = {
            "article_id": article_id,
            "sample_quote": rel.sample_quote,
            "confidence": rel.confidence,
            "extracted_at": datetime.utcnow().isoformat(),
            "extractor_version": EXTRACTOR_VERSION,
        }

        if existing:
            existing.weight = (existing.weight or 1) + 1
            # Update last_seen carefully — either side may be None.
            # (Articles with no published_at would crash max() against the existing datetime.)
            if existing.last_seen is None:
                existing.last_seen = article_ts
            elif article_ts is not None and article_ts > existing.last_seen:
                existing.last_seen = article_ts
            # Append article to source_articles (legacy field, kept for back-compat)
            try:
                src_list = json.loads(existing.source_articles) if existing.source_articles else []
            except Exception:
                src_list = []
            if article_id not in src_list:
                src_list.append(article_id)
                src_list = src_list[-50:]
                existing.source_articles = json.dumps(src_list)
            # Append to evidence_json — only if this article isn't already
            # represented (idempotency for re-extraction).
            try:
                evidence_list = json.loads(existing.evidence_json) if existing.evidence_json else []
            except Exception:
                evidence_list = []
            if not any(ev.get("article_id") == article_id for ev in evidence_list):
                evidence_list.append(new_evidence)
                evidence_list = evidence_list[-50:]
                existing.evidence_json = json.dumps(evidence_list)
            stats["relations_strengthened"] += 1
        else:
            db.add(EntityRelation(
                subject_id=s_id,
                predicate=rel.predicate,
                object_id=o_id,
                weight=1,
                first_seen=article_ts,
                last_seen=article_ts,
                sample_quote=rel.sample_quote,
                source_articles=json.dumps([article_id]),
                evidence_json=json.dumps([new_evidence]),
                confidence=rel.confidence,
            ))
            stats["relations_created"] += 1

        # ── Claim-layer dual-write ─────────────────────────────────────
        # Claims are the new source-of-truth; entity_relations stays as a
        # denormalization for backward compat. Same triple → same claim.
        # Each article-extraction adds one ClaimSupport row.
        from app.services.stance import stance_for
        sv = stance_for(rel.predicate)
        claim = (
            db.query(Claim)
            .filter(Claim.subject_id == s_id,
                    Claim.predicate == rel.predicate,
                    Claim.object_id == o_id)
            .one_or_none()
        )
        if not claim:
            claim = Claim(
                subject_id=s_id,
                predicate=rel.predicate,
                object_id=o_id,
                procedural=sv.procedural if sv else None,
                rhetorical=sv.rhetorical if sv else None,
                ideological=sv.ideological if sv else None,
                status="active",
                first_seen=article_ts,
                last_seen=article_ts,
                sample_quote=rel.sample_quote,
                confidence=rel.confidence,
                extractor_version=EXTRACTOR_VERSION,
            )
            db.add(claim)
            db.flush()
            stats["claims_created"] = stats.get("claims_created", 0) + 1
        else:
            # Refresh last_seen and sample_quote (best-quote heuristic = latest)
            if article_ts and (not claim.last_seen or article_ts > claim.last_seen):
                claim.last_seen = article_ts
            if rel.sample_quote:
                claim.sample_quote = rel.sample_quote
            stats["claims_strengthened"] = stats.get("claims_strengthened", 0) + 1
        # Always add a ClaimSupport row (idempotent via UNIQUE).
        # The stance carries through from the LLM extraction — "supporting"
        # for normal assertions, "contesting" when the article disputes.
        cs_existing = (
            db.query(ClaimSupport)
            .filter(ClaimSupport.claim_id == claim.id,
                    ClaimSupport.article_id == article_id)
            .one_or_none()
        )
        if not cs_existing:
            db.add(ClaimSupport(
                claim_id=claim.id,
                article_id=article_id,
                stance=rel.stance,  # v14.6 — was hardcoded "supporting"
                sample_quote=rel.sample_quote,
                confidence=rel.confidence,
                extractor_version=EXTRACTOR_VERSION,
                extracted_at=datetime.utcnow(),
            ))
            stats["claim_supports_created"] = stats.get("claim_supports_created", 0) + 1
            if rel.stance == "contesting":
                stats["claim_supports_contesting"] = stats.get("claim_supports_contesting", 0) + 1
        elif cs_existing.stance != rel.stance:
            # Same article re-asserting the claim with a different stance —
            # take the newer one. Rare; only happens on rewrite re-extraction.
            cs_existing.stance = rel.stance

        # Auto-flip claim.status to 'contested' when this claim now has both
        # supporting and contesting evidence. Don't override a 'retracted'
        # claim (those are human decisions).
        if claim.status != "retracted":
            db.flush()  # make sure the just-added ClaimSupport is visible
            counts = (
                db.query(ClaimSupport.stance)
                .filter(ClaimSupport.claim_id == claim.id)
                .all()
            )
            stances = {s for (s,) in counts}
            if "supporting" in stances and "contesting" in stances:
                if claim.status != "contested":
                    claim.status = "contested"
                    stats["claims_marked_contested"] = stats.get("claims_marked_contested", 0) + 1
            elif claim.status == "contested":
                # No longer has both — flip back to active
                claim.status = "active"

    stats["relations_skipped_unresolved"] = relations_skipped
    stats["relations_rejected_by_constraints"] = relations_rejected_by_constraints
    db.commit()
    return stats
