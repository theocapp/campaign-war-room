"""Registry of historical extractor versions for ontology-drift handling.

Every piece of evidence in entity_relations.evidence_json carries the
`extractor_version` string of the run that produced it. This registry maps
each version string to a capability snapshot — which predicates were valid,
whether the endorses definition was tight, whether commonsense rules
were active, etc.

When the live version (entity_extraction.EXTRACTOR_VERSION) changes, any
evidence produced under an OLDER version is "stale" — its extraction may
not satisfy the constraints the current code would impose. The drift API
surfaces this so the user can decide whether to re-extract.

Bumped whenever the prompt or constraint layer changes meaningfully.
Append-only — never edit existing entries (otherwise the historical
record loses meaning).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass(frozen=True)
class ExtractorVersion:
    version: str
    released_at: str  # ISO date
    summary: str
    breaking_changes: tuple[str, ...] = field(default_factory=tuple)

    # Capability snapshot — exactly what the extractor would do at this version.
    entity_types: tuple[str, ...] = field(default_factory=tuple)
    predicates: tuple[str, ...] = field(default_factory=tuple)
    endorses_strict: bool = False
    domain_range_enforced: bool = False
    commonsense_enforced: bool = False
    excerpt_chars: int = 1500
    # v14.5+ — events are first-class with strict (name + date OR location) dedup.
    events_first_class: bool = False
    # v14.6+ — relations carry stance="supporting"|"contesting"; persist_extraction
    # dual-writes to the claims/claim_supports tables with auto-contested logic.
    stance_aware: bool = False
    claim_layer_writes: bool = False
    # v15.0+ — extraction output is quote-anchored claim records, NOT triples.
    # The LLM emits (entities[], evidence_span, label?) — never (subject,
    # predicate, object). Predicates / domain-range / commonsense / stance
    # are inert here; persist writes go to claim_records, not entity_relations
    # or claims. v15.0 explicitly retires the triple-shaped action predicates
    # (endorses, criticizes, attacks, attended, voted_for, voted_against,
    # co_sponsored) from LLM extraction; structural relations from seed
    # files continue to land in entity_relations as before.
    claim_record_shape: bool = False
    # v15.0+ — predicate-emitting paths in the prompt are gone. Set False on
    # all legacy versions; True only on v15.0+. Useful for drift API to flag
    # legacy evidence as "old shape" without needing version-string equality.
    triple_shape_retired: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# ── History ────────────────────────────────────────────────────────────
#
# Append-only. When you bump EXTRACTOR_VERSION in entity_extraction.py,
# add a new entry below describing what changed.

_DEFAULT_TYPES = ("person", "organization", "bill", "location")
_DEFAULT_PREDS = (
    "endorses", "criticizes", "attacks",
    "voted_for", "voted_against", "co_sponsored",
    "represents", "member_of", "predecessor_of",
)
# v14.5 — added `event` as a fifth type, `attended` as a tenth predicate.
_V145_TYPES = _DEFAULT_TYPES + ("event",)
_V145_PREDS = _DEFAULT_PREDS + ("attended",)


VERSIONS: list[ExtractorVersion] = [
    ExtractorVersion(
        version="v14.1",
        released_at="2026-05-26T03:50:00",
        summary="Initial 4-type / 9-predicate schema. Soft endorses synonyms (supports/praises/backs). No domain-range, no commonsense.",
        entity_types=_DEFAULT_TYPES,
        predicates=_DEFAULT_PREDS,
        excerpt_chars=1500,
    ),
    ExtractorVersion(
        version="v14.1-backfilled",
        released_at="2026-05-26T11:30:00",
        summary="Migration tag for evidence_json rows backfilled from pre-evidence-array data. Same capabilities as v14.1, but only the FIRST article in each source_articles list has a sample_quote (others are null) — lossy migration.",
        entity_types=_DEFAULT_TYPES,
        predicates=_DEFAULT_PREDS,
        excerpt_chars=1500,
    ),
    ExtractorVersion(
        version="v14.2",
        released_at="2026-05-26T15:30:00",
        summary="Evidence-array schema added. Lenient parser drops bad entities/relations instead of failing the whole article. Bug fixes for D|null affiliation, NoneType datetime, predicate validation.",
        entity_types=_DEFAULT_TYPES,
        predicates=_DEFAULT_PREDS,
        excerpt_chars=1500,
    ),
    ExtractorVersion(
        version="v14.3",
        released_at="2026-05-26T17:00:00",
        summary="Tightened `endorses` to require explicit endorsement language. Soft synonyms (supports/praises/backs/allies_with) removed and now fall through to strict Literal validation. Domain/range constraint table enforced at write time.",
        breaking_changes=(
            "endorses_strict",       # endorses no longer triggered by soft language
            "domain_range_enforced", # rejects relations with mismatched subject/object types
        ),
        entity_types=_DEFAULT_TYPES,
        predicates=_DEFAULT_PREDS,
        endorses_strict=True,
        domain_range_enforced=True,
        excerpt_chars=1500,
    ),
    ExtractorVersion(
        version="v14.4",
        released_at="2026-05-26T19:00:00",
        summary="Added 10 commonsense rules (role-aware constraints — POTUS can't represent districts, senators don't represent counties, House reps don't represent other states' districts). Bumped excerpt window from 1.5K to 8K chars.",
        breaking_changes=(
            "commonsense_enforced",   # rejects role-violating relations
            "excerpt_window_bumped",  # captures more entities from long articles
        ),
        entity_types=_DEFAULT_TYPES,
        predicates=_DEFAULT_PREDS,
        endorses_strict=True,
        domain_range_enforced=True,
        commonsense_enforced=True,
        excerpt_chars=8000,
    ),
    ExtractorVersion(
        version="v14.5",
        released_at="2026-05-26T20:30:00",
        summary="Events as first-class entities (5th type). Strict dedup: events must carry an event_date OR event_location. Re-added `attended` (10th predicate) for person→event / org→event participation. Vague event mentions ('the launch', 'the event') are rejected by canonicalize_entity.",
        breaking_changes=(
            "events_first_class",      # `event` type added; event-typed extracts without date/location are dropped
            "predicate_attended_added", # extractions can now produce `attended`
        ),
        entity_types=_V145_TYPES,
        predicates=_V145_PREDS,
        endorses_strict=True,
        domain_range_enforced=True,
        commonsense_enforced=True,
        excerpt_chars=8000,
        events_first_class=True,
    ),
    ExtractorVersion(
        version="v14.6",
        released_at="2026-05-26T22:00:00",
        summary="Relations carry stance='supporting' (default) or 'contesting' (article disputes/denies/fact-checks the claim). persist_extraction dual-writes to claims + claim_supports; claims auto-flip status='contested' when both stances are present. Cross-document event date observations recorded on entity.metadata_json so the UI can surface 'dates contested'.",
        breaking_changes=(
            "stance_aware",          # relation rows carry stance; ClaimSupport.stance preserves it
            "claim_layer_writes",    # every extracted relation writes a Claim + ClaimSupport
            "event_date_reconciliation",  # multi-article event-date observations are accumulated
        ),
        entity_types=_V145_TYPES,
        predicates=_V145_PREDS,
        endorses_strict=True,
        domain_range_enforced=True,
        commonsense_enforced=True,
        excerpt_chars=8000,
        events_first_class=True,
        stance_aware=True,
        claim_layer_writes=True,
    ),
    ExtractorVersion(
        version="v14.7",
        released_at="2026-05-27T00:30:00",
        summary="Tightened event + attended definitions after the v14.6 stage-1 audit found ~40% noise on attended relations. Events must be HAPPENINGS (one place, bounded time window) — elections, primary seasons, and 'the campaign' are now explicitly rejected. attended now requires the sample_quote to contain an attendance verb tying the subject to the event; inference patterns ('did not respond', 'tweeted about', 'won the election') are explicitly forbidden.",
        breaking_changes=(
            "attended_requires_attendance_verb",  # sample_quote must literally describe presence
            "election_processes_not_events",      # multi-month processes excluded from event type
        ),
        entity_types=_V145_TYPES,
        predicates=_V145_PREDS,
        endorses_strict=True,
        domain_range_enforced=True,
        commonsense_enforced=True,
        excerpt_chars=8000,
        events_first_class=True,
        stance_aware=True,
        claim_layer_writes=True,
    ),
    ExtractorVersion(
        version="v15.0",
        released_at="2026-05-27T02:00:00",
        summary=(
            "Pivot from triple-shaped extraction to quote-anchored claim records. "
            "After v14.5–v14.7 hit a structural ceiling (LLM systematically "
            "projected (subject, predicate, object) edges onto prose that didn't "
            "contain them — election processes as events, 'did not respond' as "
            "attended, subject-swapping to satisfy schema geometry), the action "
            "predicates (endorses, criticizes, attacks, attended, voted_for, "
            "voted_against, co_sponsored) are RETIRED from LLM extraction. "
            "New shape: for each article, the LLM returns entities[] + 1–3 "
            "claim records, each a verbatim quote span + the entities that "
            "appear in it + an optional shallow label (statement, attack, "
            "defense, endorsement, policy_position, vote, announcement, "
            "commitment, or NULL). Validators reject non-verbatim spans and "
            "entities that don't appear in the quote. Structural relations "
            "(represents, member_of, predecessor_of) continue to come from "
            "seed files, not LLM. The event entity type is retired."
        ),
        breaking_changes=(
            "claim_record_shape",        # extraction emits claim_records, not relations/claims
            "action_predicates_retired", # endorses/attacks/attended/voted_*/co_sponsored no longer LLM-emitted
            "event_type_retired",        # 'event' type removed from extraction
            "label_set_replaced_predicates",  # shallow labels replace the predicate vocabulary
        ),
        # Capability snapshot: only person/org/bill/location remain as
        # extractable entity types. Predicates are EMPTY — the LLM no longer
        # emits them at all.
        entity_types=_DEFAULT_TYPES,
        predicates=(),  # no LLM-emitted predicates
        endorses_strict=False,           # N/A — endorses is retired from extraction
        domain_range_enforced=False,     # N/A — no triples to constrain
        commonsense_enforced=False,      # N/A — no triples to validate
        excerpt_chars=8000,
        events_first_class=False,
        stance_aware=False,
        claim_layer_writes=False,        # writes go to claim_records, not claims
        claim_record_shape=True,
        triple_shape_retired=True,
    ),
]


def by_version(v: str) -> ExtractorVersion | None:
    for ev in VERSIONS:
        if ev.version == v:
            return ev
    return None


def current() -> ExtractorVersion:
    return VERSIONS[-1]


def diff_summary(old: str, new: str) -> list[str]:
    """Human-readable list of capability changes between two versions.
    Used by the drift summary endpoint to explain WHY stale evidence
    might be wrong."""
    a = by_version(old)
    b = by_version(new)
    if not a or not b:
        return [f"Unknown version comparison: {old!r} → {new!r}"]
    out: list[str] = []
    if not a.endorses_strict and b.endorses_strict:
        out.append(f"{b.version} tightened `endorses` — older evidence may include soft-language false positives that the new prompt would reject.")
    if not a.domain_range_enforced and b.domain_range_enforced:
        out.append(f"{b.version} enforces domain/range — older evidence may contain type-mismatched relations (location representing a person, bill attacking, etc.).")
    if not a.commonsense_enforced and b.commonsense_enforced:
        out.append(f"{b.version} enforces commonsense rules — older evidence may contain role-violating relations (POTUS represents a district, senator represents a county).")
    if a.excerpt_chars < b.excerpt_chars:
        out.append(f"{b.version} reads {b.excerpt_chars} chars per article (was {a.excerpt_chars}) — may catch entities older runs missed.")
    if not a.events_first_class and b.events_first_class:
        out.append(f"{b.version} extracts events as first-class entities — older runs collapsed event references into surrounding orgs/people, so the older corpus has no event entities at all.")
    if not a.stance_aware and b.stance_aware:
        out.append(f"{b.version} distinguishes supporting vs contesting evidence — older relations are all assumed 'supporting', so fact-checks and denials in old articles strengthen the claim they were meant to dispute.")
    if not a.claim_layer_writes and b.claim_layer_writes:
        out.append(f"{b.version} writes the claim layer at extraction time — older articles were back-filled into claims by claim_layer_backfill.py but carry no per-article ClaimSupport.stance signal.")
    if not a.triple_shape_retired and b.triple_shape_retired:
        out.append(f"{b.version} RETIRED the triple shape from LLM extraction. Older evidence emits (subject, predicate, object) edges and writes to entity_relations/claims; {b.version} emits quote-anchored claim_records instead. The action predicates (endorses, criticizes, attacks, attended, voted_for, voted_against, co_sponsored) and the event entity type are no longer LLM-extractable. Older data is FROZEN, not migrated — query the legacy tables separately.")
    if not a.claim_record_shape and b.claim_record_shape:
        out.append(f"{b.version} writes claim_records (verbatim quote spans + entity lists + optional shallow labels). The validator enforces: evidence_span is a substring of source article text, every entity appears in the quote, label is in the closed set or NULL.")
    if not out:
        out.append("Same capabilities; the version bump was probably an internal refactor.")
    return out
