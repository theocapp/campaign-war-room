"""
Knowledge graph extraction service.

Turns raw political text into a validated ExtractionResult that maps cleanly
into the kg_* schema — no hallucinated entities, no cross-reference gaps.

Architecture:
  KGExtractor.extract(text)
      → provider.extract_knowledge_graph(text)   # LLM call
      → _parse_raw_response(raw_str)             # JSON parse + Pydantic coerce
      → _validate(payload, source_text)          # groundedness + cross-ref
      → ExtractionResult                         # ready for DB write
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from pydantic import ValidationError

from app.services.llm_provider import BaseLLMProvider
from app.knowledge_graph.extraction_types import (
    ExtractionResult,
    RawExtractionPayload,
    RawExtractedClaim,
    RawExtractedEntity,
    RawExtractedEvent,
    RawExtractedIssue,
    ValidatedClaim,
)

log = logging.getLogger(__name__)

# ── Prompt ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a political knowledge graph extraction engine.

Read political source text and extract structured information about claims,
entities, issues, and events. Output ONLY valid JSON — no commentary, no
markdown fences, no explanation before or after the JSON object.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EXTRACTION RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. CLAIMS — atomic political assertions
   • Each claim is a single subject–predicate–object statement.
   • Compound: "She opposed the bill and later reversed course" → split into two.
   • Extract only assertions directly present in the text — never infer or extend.
   • stance:
       support  → subject/author affirms, endorses, advocates a position
       oppose   → subject/author rejects, attacks, contradicts, or denies
       neutral  → factual report with no directional framing
       unknown  → direction genuinely ambiguous
   • confidence [0.0–1.0]:
       0.85–1.0  verbatim or near-verbatim quote from text
       0.65–0.84 clear paraphrase of explicit statement
       0.40–0.64 reasonable inference from context
       0.00–0.39 speculative; prefer dropping instead
   • entity_names: names from YOUR "entities" array involved in this claim
   • issue_names:  display_names from YOUR "issues" array this claim concerns
   • event_names:  names from YOUR "events" array this claim references

2. ENTITIES — named things that appear verbatim in the text
   • PERSON  named individuals (politicians, officials, journalists, activists)
   • ORG     organizations, parties, agencies, committees, PACs, media outlets
   • PLACE   geographic entities (cities, districts, states, precincts, addresses)
   • ISSUE   use ONLY when the text treats a topic AS a named entity (rare)
   • name: as written in source text (use the fullest form that appears)
   • canonical_name_candidate: best-known fully-qualified name; repeat name if unknown
   • Do NOT add entities that do not appear in the source text.

3. ISSUES — substantive policy/political topics
   • Include only topics with real substance in the text, not passing mentions.
   • slug:         snake_case, all lowercase (e.g. "housing_affordability")
   • display_name: title case (e.g. "Housing Affordability")

4. EVENTS — explicitly named events
   • Only if the text explicitly describes a named event (debate, vote, speech,
     scandal, policy action). Do NOT infer events from implications.
   • event_timestamp: ISO 8601 string if inferable; null otherwise
   • type: DEBATE | SCANDAL | POLICY | SPEECH | VOTE

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HARD CONSTRAINTS — violations make output unusable
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Every name in claims.entity_names  MUST appear in entities[].name
• Every name in claims.issue_names   MUST match issues[].display_name exactly
• Every name in claims.event_names   MUST match events[].name exactly
• confidence must be a float in [0.0, 1.0]
• stance must be exactly: support | oppose | neutral | unknown
• entity type must be exactly: PERSON | ORG | ISSUE | PLACE
• event type must be exactly: DEBATE | SCANDAL | POLICY | SPEECH | VOTE

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT — return ONLY this JSON, nothing else
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{
  "claims": [
    {
      "text": "...",
      "stance": "support|oppose|neutral|unknown",
      "confidence": 0.0,
      "entity_names": ["..."],
      "issue_names": ["..."],
      "event_names": ["..."]
    }
  ],
  "entities": [
    {
      "type": "PERSON|ORG|ISSUE|PLACE",
      "name": "...",
      "canonical_name_candidate": "..."
    }
  ],
  "issues": [
    {
      "slug": "snake_case_slug",
      "display_name": "Human Readable Label"
    }
  ],
  "events": [
    {
      "name": "...",
      "type": "DEBATE|SCANDAL|POLICY|SPEECH|VOTE",
      "event_timestamp": "ISO 8601 or null",
      "description": "..."
    }
  ]
}

Return empty arrays [] for any category with nothing to extract.
Return {} only if the text is entirely non-political and non-substantive.\
"""


def build_user_prompt(text: str, max_chars: int = 5000) -> str:
    truncated = text[:max_chars]
    suffix = "\n[... text truncated for length ...]" if len(text) > max_chars else ""
    return f"SOURCE TEXT:\n\n{truncated}{suffix}\n\nExtract all claims, entities, issues, and events."


# ── Groundedness ──────────────────────────────────────────────────────────────

def _is_grounded(name: str, source_lower: str) -> bool:
    """
    Return True if the entity name is sufficiently anchored in the source text.
    Checks full name first, then any word longer than 4 characters.
    """
    if name.lower() in source_lower:
        return True
    significant_words = [w for w in re.findall(r"\b\w+\b", name) if len(w) > 4]
    return any(w.lower() in source_lower for w in significant_words)


# ── JSON parsing ──────────────────────────────────────────────────────────────

def _parse_raw_response(raw: str) -> Optional[RawExtractionPayload]:
    """
    Parse an LLM response string into a RawExtractionPayload.
    Handles markdown fences, leading/trailing whitespace, and partial JSON.
    """
    # Strip markdown fences
    text = re.sub(r"```(?:json)?\s*", "", raw).strip().strip("`").strip()

    # Find outermost JSON object
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        log.warning("KG extractor: no JSON object in LLM response")
        return None

    try:
        data = json.loads(match.group())
    except json.JSONDecodeError as exc:
        log.warning("KG extractor: JSON decode error — %s", exc)
        return None

    if not data:  # LLM returned {} for non-political text
        return RawExtractionPayload()

    try:
        return RawExtractionPayload.model_validate(data)
    except ValidationError as exc:
        log.warning("KG extractor: schema validation error — %s", exc)
        # Partial recovery: validate each top-level array independently
        cleaned: dict = {}
        for key in ("claims", "entities", "issues", "events"):
            val = data.get(key, [])
            cleaned[key] = val if isinstance(val, list) else []
        try:
            return RawExtractionPayload.model_validate(cleaned)
        except ValidationError:
            return None


# ── Validation ────────────────────────────────────────────────────────────────

def _validate(payload: RawExtractionPayload, source_text: str) -> ExtractionResult:
    """
    Apply two checks after LLM parsing:

    1. Groundedness — entity names must appear in the source text.
       Ungrounded entities are dropped; claims referencing only dropped entities
       have those references pruned (the claim itself is kept if text is valid).

    2. Cross-reference integrity — every name in a claim's entity_names /
       issue_names / event_names must correspond to an entry in the respective
       arrays. Dangling references are pruned silently (not a hard error, since
       the LLM sometimes gets the display_name capitalisation slightly wrong).
    """
    source_lower = source_text.lower()
    dropped_entities = 0

    # ── 1. Groundedness filter ────────────────────────────────────────────────
    grounded: list[RawExtractedEntity] = []
    for ent in payload.entities:
        if _is_grounded(ent.name, source_lower):
            grounded.append(ent)
        else:
            log.warning("KG extractor: dropping ungrounded entity %r", ent.name)
            dropped_entities += 1

    valid_entity_names   = {e.name for e in grounded}
    valid_issue_by_name  = {iss.display_name: iss.slug for iss in payload.issues}
    # Also allow case-insensitive match to tolerate minor capitalisation drift
    valid_issue_lower    = {iss.display_name.lower(): iss.slug for iss in payload.issues}
    valid_event_names    = {ev.name for ev in payload.events}

    # ── 2. Claim cross-reference pruning ──────────────────────────────────────
    validated_claims: list[ValidatedClaim] = []
    dropped_claims = 0

    for claim in payload.claims:
        if not claim.text:
            dropped_claims += 1
            continue

        clean_entities = [n for n in claim.entity_names if n in valid_entity_names]

        # Resolve issue display names → slugs (tolerates minor capitalisation)
        clean_issue_slugs: list[str] = []
        for n in claim.issue_names:
            slug = valid_issue_by_name.get(n) or valid_issue_lower.get(n.lower())
            if slug:
                clean_issue_slugs.append(slug)

        clean_events = [n for n in claim.event_names if n in valid_event_names]

        validated_claims.append(ValidatedClaim(
            text=claim.text,
            stance=claim.stance,
            confidence=claim.confidence,
            entity_names=clean_entities,
            issue_slugs=clean_issue_slugs,
            event_names=clean_events,
        ))

    if dropped_claims:
        log.warning("KG extractor: dropped %d empty/invalid claims", dropped_claims)
    if dropped_entities:
        log.warning("KG extractor: dropped %d ungrounded entities", dropped_entities)

    log.info(
        "KG extractor _validate: raw_entities=%d grounded=%d  "
        "raw_claims=%d accepted=%d dropped=%d  issues=%d",
        len(payload.entities), len(grounded),
        len(payload.claims), len(validated_claims), dropped_claims,
        len(payload.issues),
    )
    # Debug: log each dropped claim text so operators know what was lost
    for claim in payload.claims:
        if not claim.text:
            log.debug("KG extractor: dropped claim (empty text)")

    return ExtractionResult(
        claims=validated_claims,
        entities=grounded,
        issues=payload.issues,
        events=payload.events,
        dropped_claims=dropped_claims,
        dropped_entities=dropped_entities,
    )


# ── Mock extraction (used by MockLLMProvider) ─────────────────────────────────

# Narrow keyword map kept for backward-compat — used only by legacy mock_extract().
_ISSUE_KEYWORDS: dict[str, list[str]] = {
    "Housing & Affordability": ["rent", "housing", "afford", "tenant", "landlord",
                                "evict", "homebuyer", "mortgage", "zoning"],
    "Public Safety":           ["crime", "police", "safety", "break-in", "theft",
                                "patrol", "enforcement", "defund", "officer"],
    "Education & Schools":     ["school", "education", "classroom", "student",
                                "teacher", "overcrowd", "art", "music", "parent"],
    "Infrastructure":          ["pothole", "road", "sidewalk", "infrastructure",
                                "repair", "transit", "bus", "street", "flood"],
    "Downtown Development":    ["development", "developer", "downtown", "project",
                                "zoning", "construction", "gentrification"],
}

# Broad keyword map used by the high-recall extractor.
_HIGH_RECALL_ISSUE_KEYWORDS: dict[str, list[str]] = {
    "Housing & Affordability": [
        "rent", "housing", "afford", "tenant", "landlord", "evict",
        "homebuyer", "mortgage", "zoning", "apartment", "shelter", "homeless",
    ],
    "Public Safety": [
        "crime", "police", "safety", "break-in", "theft", "patrol",
        "enforcement", "defund", "officer", "arrest", "violence", "shooting",
        "911", "firefighter", "emergency",
    ],
    "Education & Schools": [
        "school", "education", "classroom", "student", "teacher", "overcrowd",
        "art", "music", "parent", "curriculum", "college", "university",
        "district", "principal", "literacy",
    ],
    "Infrastructure": [
        "pothole", "road", "sidewalk", "infrastructure", "repair", "transit",
        "bus", "street", "flood", "bridge", "water", "sewer", "utility",
        "construction", "highway", "traffic",
    ],
    "Downtown Development": [
        "development", "developer", "downtown", "project", "zoning",
        "construction", "gentrification", "permit", "building", "redevelopment",
        "mixed-use", "commercial",
    ],
    "Campaign Finance": [
        "contribution", "donation", "donor", "fundrais", "pac",
        "campaign finance", "disbursement", "expenditure", "committee",
        "fec", "federal election commission", "treasurer", "filing",
        "independent expenditure", "super pac",
    ],
    "Elections & Voting": [
        "election", "vote", "ballot", "primary", "general election",
        "candidate", "polling", "absentee", "early voting", "voter",
        "turnout", "registration", "precinct", "caucus", "runoff",
    ],
    "Government & Policy": [
        "council", "assembly", "senate", "governor", "legislature",
        "ordinance", "resolution", "policy", "bill", "law", "regulation",
        "government", "official", "mayor", "commissioner", "supervisor",
        "administration", "department", "agency", "hearing", "testimony",
    ],
    "Economy & Taxes": [
        "tax", "budget", "spending", "deficit", "fund", "grant", "contract",
        "economic", "jobs", "unemployment", "business", "revenue", "fiscal",
        "appropriation", "stimulus", "payroll",
    ],
    "Environment": [
        "environment", "climate", "pollution", "clean", "water quality",
        "air quality", "emissions", "green", "energy", "solar", "fossil",
        "conservation", "wildfire", "drought",
    ],
    "Health": [
        "health", "hospital", "medical", "insurance", "medicaid", "medicare",
        "opioid", "drug", "mental health", "healthcare", "pandemic", "vaccine",
        "overdose", "clinic",
    ],
    "Immigration": [
        "immigration", "immigrant", "border", "visa", "citizenship",
        "deportation", "asylum", "refugee", "undocumented", "enforcement",
    ],
}

# Org-type keywords — used to type extracted capitalized spans as ORG vs PERSON.
_ORG_NAME_SIGNALS = frozenset([
    "committee", "council", "board", "party", "pac", "association",
    "authority", "department", "agency", "foundation", "coalition",
    "institute", "commission", "corporation", "company", "inc", "llc",
    "organization", "union", "group", "bureau", "office", "division",
    "times", "tribune", "gazette", "post", "herald", "news", "journal",
])

_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+')

# Stance detection word lists for heuristic labelling.
_OPPOSE_SIGNALS = frozenset([
    "oppose", "against", "reject", "denounce", "criticize", "attack",
    "false", "lie", "mislead", "wrong", "deny", "refuse", "block",
    "accuse", "contradict", "dispute", "challenge",
])
_SUPPORT_SIGNALS = frozenset([
    "support", "endorse", "favor", "advocate", "promote", "champion",
    "back", "approve", "praise", "applaud", "defend", "urge",
])


def _classify_entity_type(name: str) -> str:
    name_lower = name.lower()
    if any(sig in name_lower for sig in _ORG_NAME_SIGNALS):
        return "ORG"
    return "PERSON"


def high_recall_extract(text: str) -> RawExtractionPayload:
    """
    High-recall deterministic extractor — used when no real LLM is available.

    Design goals vs. the old mock_extract():
    • Generates a claim for EVERY substantial sentence (≥ 5 words), not just
      keyword-matched ones.  This guarantees ≥ 3 claims for any real article.
    • Recognises 12 issue categories including campaign finance, elections,
      and general government — so FEC filings and local political news are
      never left with 0 issues.
    • Falls back to a "General Politics" issue if no specific category matches.
    • Extracts up to 20 entities: multi-word proper nouns AND common political
      acronyms (FEC, PAC, DNC, RNC, …).
    • Types entities as ORG when name contains org-signal words.
    • Assigns confidence heuristically (0.4–0.9); never drops weak claims.
    • Assigns stance heuristically from oppose/support signal words.
    """
    text_lower = text.lower()
    # Primary split: sentence boundaries (.!?)
    sentence_candidates = [s.strip() for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip()]
    # Secondary split: newlines — handles structured/tabular data (FEC filings,
    # press-release bullet points) that lack terminal punctuation between entries.
    # We merge any long sentence-candidate further split by its own newlines.
    sentences: list[str] = []
    for cand in sentence_candidates:
        if "\n" in cand:
            # Split the candidate on newlines and keep each non-trivial line
            lines = [ln.strip() for ln in cand.splitlines() if ln.strip()]
            sentences.extend(lines)
        else:
            sentences.append(cand)

    # ── Issues ────────────────────────────────────────────────────────────────
    issues: list[RawExtractedIssue] = []
    for display_name, keywords in _HIGH_RECALL_ISSUE_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            slug = re.sub(r"[^a-z0-9]+", "_", display_name.lower()).strip("_")
            issues.append(RawExtractedIssue(slug=slug, display_name=display_name))

    if not issues:
        issues.append(RawExtractedIssue(slug="general_politics", display_name="General Politics"))

    # ── Entities ──────────────────────────────────────────────────────────────
    seen: set[str] = set()
    entities: list[RawExtractedEntity] = []

    # Multi-word capitalized spans (persons, org names)
    for name in re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b', text):
        if name not in seen and len(entities) < 20:
            seen.add(name)
            entities.append(RawExtractedEntity(
                type=_classify_entity_type(name),
                name=name,
                canonical_name_candidate=name,
            ))

    # All-caps acronyms that commonly appear in political text
    _SKIP_CAPS = frozenset(["I", "A", "AN", "THE", "OF", "IN", "AT", "TO",
                             "OR", "AND", "BUT", "FOR", "NOR", "SO", "YET"])
    for m in re.finditer(r'\b([A-Z]{2,6})\b', text):
        name = m.group(1)
        if name not in seen and name not in _SKIP_CAPS and len(entities) < 20:
            seen.add(name)
            entities.append(RawExtractedEntity(
                type="ORG",
                name=name,
                canonical_name_candidate=name,
            ))

    # Build lookup for efficient per-sentence entity matching
    entity_names_list = [e.name for e in entities]

    # ── Claims ────────────────────────────────────────────────────────────────
    # Build a per-issue keyword lookup for sentence→issue linking
    issue_kw_map = {
        iss.display_name: _HIGH_RECALL_ISSUE_KEYWORDS.get(iss.display_name, [])
        for iss in issues
    }
    issue_display_names = [iss.display_name for iss in issues]

    claims: list[RawExtractedClaim] = []
    for sentence in sentences:
        words = sentence.split()
        if len(words) < 5:
            continue

        sent_lower = sentence.lower()

        # Stance
        if any(w in sent_lower for w in _OPPOSE_SIGNALS):
            stance = "oppose"
        elif any(w in sent_lower for w in _SUPPORT_SIGNALS):
            stance = "support"
        else:
            stance = "neutral"

        # Confidence: quotes or long sentences → higher confidence
        if '"' in sentence or "'" in sentence:
            confidence = 0.75
        elif len(words) >= 20:
            confidence = 0.65
        elif len(words) >= 10:
            confidence = 0.55
        else:
            confidence = 0.45

        # Issue links: match any issue whose keyword appears in this sentence
        matched_issues = [
            name for name, kws in issue_kw_map.items()
            if any(kw in sent_lower for kw in kws)
        ]
        # If none matched, fall back to the first (most relevant) issue
        if not matched_issues:
            matched_issues = [issue_display_names[0]]

        # Entity links: entities whose name (or any significant word) appears
        mentioned = [
            ename for ename in entity_names_list
            if ename.lower() in sent_lower
            or any(w.lower() in sent_lower for w in ename.split() if len(w) > 4)
        ]

        claims.append(RawExtractedClaim(
            text=sentence[:400],
            stance=stance,
            confidence=confidence,
            entity_names=mentioned,
            issue_names=matched_issues,
            event_names=[],
        ))

    log.debug(
        "high_recall_extract: %d sentences → %d claims, %d entities, %d issues",
        len(sentences), len(claims), len(entities), len(issues),
    )
    return RawExtractionPayload(claims=claims, entities=entities, issues=issues, events=[])


def mock_extract(text: str) -> RawExtractionPayload:
    """
    Legacy narrow extractor — kept for backward compatibility with tests.
    New code should use high_recall_extract() instead.
    """
    text_lower = text.lower()

    issues: list[RawExtractedIssue] = []
    for display_name, keywords in _ISSUE_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            slug = re.sub(r"[^a-z0-9]+", "_", display_name.lower()).strip("_")
            issues.append(RawExtractedIssue(slug=slug, display_name=display_name))

    seen: set[str] = set()
    entities: list[RawExtractedEntity] = []
    for name in re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b', text):
        if name not in seen and len(entities) < 8:
            seen.add(name)
            entities.append(RawExtractedEntity(
                type="PERSON",
                name=name,
                canonical_name_candidate=name,
            ))

    claims: list[RawExtractedClaim] = []
    entity_names = [e.name for e in entities[:2]]

    for iss in issues[:3]:
        for kw in _ISSUE_KEYWORDS.get(iss.display_name, []):
            for sentence in _SENTENCE_SPLIT.split(text):
                sentence = sentence.strip()
                if kw in sentence.lower() and len(sentence.split()) >= 6:
                    claims.append(RawExtractedClaim(
                        text=sentence[:300],
                        stance="neutral",
                        confidence=0.6,
                        entity_names=entity_names,
                        issue_names=[iss.display_name],
                        event_names=[],
                    ))
                    break  # one claim per issue

    return RawExtractionPayload(claims=claims, entities=entities, issues=issues, events=[])


# ── KGExtractor ───────────────────────────────────────────────────────────────

class KGExtractor:
    """
    Orchestrates LLM extraction, parsing, and validation for a single source.

    Usage:
        extractor = KGExtractor(provider)
        result    = extractor.extract(source_text)
        # result.claims, result.entities, result.issues, result.events are
        # validated and ready for DB insertion via the kg_* ORM models.
    """

    def __init__(self, provider: BaseLLMProvider) -> None:
        self._provider = provider

    def extract(self, text: str) -> ExtractionResult:
        if not text or not text.strip():
            log.warning("KG extractor: skipping empty text")
            return ExtractionResult()

        provider_name = type(self._provider).__name__
        log.info(
            "KG extractor: starting extraction  provider=%s  text_len=%d",
            provider_name, len(text),
        )

        try:
            raw_str = self._provider.extract_knowledge_graph(text)
        except Exception as exc:
            log.error("KG extractor: LLM call failed — %s", exc)
            return ExtractionResult()

        if not raw_str:
            log.warning("KG extractor: provider returned empty response")
            return ExtractionResult()

        log.debug("KG extractor: raw response length=%d", len(raw_str))

        payload = _parse_raw_response(raw_str)
        if payload is None:
            log.error("KG extractor: failed to parse provider response into RawExtractionPayload")
            return ExtractionResult()

        log.info(
            "KG extractor: parsed payload  claims=%d  entities=%d  issues=%d  events=%d",
            len(payload.claims), len(payload.entities),
            len(payload.issues), len(payload.events),
        )

        result = _validate(payload, text)
        log.info(
            "KG extractor: final result  accepted_claims=%d  accepted_entities=%d  "
            "dropped_claims=%d  dropped_entities=%d",
            len(result.claims), len(result.entities),
            result.dropped_claims, result.dropped_entities,
        )
        return result
