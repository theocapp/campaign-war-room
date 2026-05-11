"""
Knowledge graph ingestion layer — repository functions + ingestion service.

Public surface:
  get_or_create_kg_source(...)   — mirror a SourceItem into kg_sources
  KGIngestionService.ingest(...) — persist an ExtractionResult into kg_* tables


Persists a validated ExtractionResult into the kg_* tables.

Idempotency contract
────────────────────
Every public function in this module is idempotent: calling it twice with the
same arguments leaves the database in exactly the same state as calling it once.

  • Entities  — deduplicated by canonical_name match (canonical_name_candidate
                from the LLM, or name if none was provided).  When a new surface
                form of an already-known entity is seen, the new form is recorded
                as a kg_entity_alias rather than creating a duplicate row.

  • Issues    — deduplicated by slug (the `name` column in kg_issues, which has
                a UNIQUE constraint).  display_name is updated if the new
                extraction provides a non-empty value.

  • Events    — deduplicated by (name, event_type).  No FK unique constraint
                exists in the schema, so we query before inserting.

  • Claims    — deduplicated by (source_id, text).  The schema indexes
                (source_id, md5(text)), so we query first to avoid duplicate rows
                for the same source+claim combination.

  • Edges     — the schema has UNIQUE (from_type, from_id, to_type, to_id,
                relationship_type).  We query before inserting.

No LLM calls are made anywhere in this module.  All decisions are deterministic
lookups against already-validated data.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.knowledge_graph.claim_normalizer import normalize_claim
from app.knowledge_graph.extraction_types import ExtractionResult, RawExtractedEntity
from app.knowledge_graph.orm import (
    KGClaim,
    KGClaimEntity,
    KGClaimIssue,
    KGEdge,
    KGEntity,
    KGEntityAlias,
    KGEvent,
    KGIssue,
    KGSource,
)

log = logging.getLogger(__name__)


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class IngestionReport:
    """Counts of rows created vs. skipped per entity type in one ingest call."""
    entities_created: int = 0
    entities_skipped: int = 0
    aliases_added:    int = 0
    issues_created:   int = 0
    issues_skipped:   int = 0
    events_created:   int = 0
    events_skipped:   int = 0
    claims_created:   int = 0
    claims_skipped:   int = 0
    edges_created:    int = 0
    edges_skipped:    int = 0
    errors:           list[str] = field(default_factory=list)

    @property
    def total_created(self) -> int:
        return (
            self.entities_created + self.issues_created +
            self.events_created   + self.claims_created +
            self.edges_created
        )


# ── KGSource repository ───────────────────────────────────────────────────────

def _content_hash(url: str, text: str) -> str:
    """SHA-256 of (url + text) — stable dedup key for kg_sources."""
    return hashlib.sha256((url + text).encode("utf-8", errors="replace")).hexdigest()


def _parse_domain(url: str) -> Optional[str]:
    """Extract registered domain from a URL, e.g. 'https://news.bbc.com/…' → 'bbc.com'."""
    if not url:
        return None
    try:
        host = urlparse(url).hostname or ""
        # Strip leading 'www.'
        if host.startswith("www."):
            host = host[4:]
        return host or None
    except Exception:
        return None


# Credibility rules applied in priority order (first match wins).
# Each rule is (predicate, score, verified_official).
# Predicate receives (source_type, source_owner_type, domain).
_CREDIBILITY_RULES: list[tuple] = [
    # .gov domains are authoritative
    (lambda st, sot, dom: dom is not None and dom.endswith(".gov"),
     0.9, True),
    # Verified candidate/opponent/official statements
    (lambda st, sot, dom: sot in ("candidate", "opponent", "official"),
     0.8, True),
    # Established news outlets
    (lambda st, sot, dom: st == "news",
     0.7, False),
    # Public records
    (lambda st, sot, dom: st == "public_record",
     0.75, False),
    # Opponent statements (attributed but adversarial)
    (lambda st, sot, dom: st == "opponent_statement",
     0.6, False),
    # Social media with known owner
    (lambda st, sot, dom: st == "social" and sot not in ("unclear", "", None),
     0.5, False),
    # Social media with unknown owner
    (lambda st, sot, dom: st == "social",
     0.4, False),
    # Campaign notes written internally
    (lambda st, sot, dom: st == "campaign_note",
     0.6, False),
    # Anonymous / unclear provenance
    (lambda st, sot, dom: sot in ("unclear", "", None),
     0.3, False),
]
_CREDIBILITY_DEFAULT = (0.5, False)


def assign_credibility(
    source_type: Optional[str],
    source_owner_type: Optional[str],
    domain: Optional[str],
) -> tuple[float, bool]:
    """
    Return (credibility_score, verified_official) for the given provenance
    attributes.  Rules are evaluated in priority order; first match wins.
    Falls back to (0.5, False).
    """
    st  = (source_type  or "").strip().lower()
    sot = (source_owner_type or "").strip().lower()
    dom = (domain or "").strip().lower() or None
    for predicate, score, verified in _CREDIBILITY_RULES:
        try:
            if predicate(st, sot, dom):
                return score, verified
        except Exception:
            continue
    return _CREDIBILITY_DEFAULT


def get_or_create_kg_source(
    db: Session,
    *,
    url: str,
    title: Optional[str] = None,
    text: Optional[str] = None,
    published_at: Optional[datetime] = None,
    source_item_id: Optional[int] = None,
    source_type: Optional[str] = None,
    source_name: Optional[str] = None,
    source_owner_type: Optional[str] = None,
) -> KGSource:
    """
    Return an existing KGSource row or create one.

    Idempotency key: SHA-256 of (url + text).  Calling this twice with the
    same arguments returns the same row — no duplicate sources are created.

    Provenance fields (source_type, source_name, source_owner_type) drive the
    credibility_score and verified_official heuristics via assign_credibility().
    """
    ch = _content_hash(url or "", text or "")
    source = db.query(KGSource).filter(KGSource.content_hash == ch).first()
    if source:
        return source

    domain = _parse_domain(url or "")
    credibility, verified = assign_credibility(source_type, source_owner_type, domain)

    source = KGSource(
        url=url or "",
        title=title,
        text=text,
        published_at=published_at,
        content_hash=ch,
        source_item_id=source_item_id,
        source_type=source_type,
        source_name=source_name,
        domain=domain,
        credibility_score=credibility,
        verified_official=int(verified),
    )
    db.add(source)
    db.flush()
    return source


# ── Entity repository ─────────────────────────────────────────────────────────

def _effective_canonical(raw: RawExtractedEntity) -> str:
    """Return the canonical name to use for deduplication lookups."""
    cand = (raw.canonical_name_candidate or "").strip()
    return cand if cand else raw.name


def _find_entity(db: Session, canonical: str, surface_name: str) -> Optional[KGEntity]:
    """
    Look up an entity by four descending-priority strategies:
      1. canonical matches stored canonical_name
      2. canonical matches stored name
      3. surface_name matches stored name
      4. surface_name matches any alias
    Returns the first match, or None.
    """
    # 1 & 2: canonical key matches either stored column
    entity = (
        db.query(KGEntity)
        .filter(
            (KGEntity.canonical_name == canonical) |
            (KGEntity.name == canonical)
        )
        .first()
    )
    if entity:
        return entity

    # 3: surface form matches stored name (catches same entity, different canonical candidate)
    if surface_name != canonical:
        entity = db.query(KGEntity).filter(KGEntity.name == surface_name).first()
        if entity:
            return entity

    # 4: surface form is a known alias
    alias_row = (
        db.query(KGEntityAlias)
        .filter(KGEntityAlias.alias == surface_name)
        .first()
    )
    if alias_row:
        return db.query(KGEntity).get(alias_row.entity_id)

    return None


def upsert_entity(
    db: Session,
    raw: RawExtractedEntity,
    report: IngestionReport,
) -> KGEntity:
    """
    Insert or retrieve an entity row.

    If the entity already exists, and the current extraction used a different
    surface form (raw.name), that surface form is registered as an alias so
    future lookups via any spelling resolve to the same row.
    """
    canonical = _effective_canonical(raw)

    entity = _find_entity(db, canonical, raw.name)

    if entity is None:
        entity = KGEntity(
            entity_type=raw.type,
            name=raw.name,
            canonical_name=canonical if canonical != raw.name else None,
        )
        db.add(entity)
        db.flush()
        report.entities_created += 1
        log.debug("KG ingest: created entity %r (id=%d)", raw.name, entity.id)
    else:
        report.entities_skipped += 1
        # Register this surface form as an alias if it is new
        _add_alias_if_new(db, entity, raw.name, report)
        # Also register canonical as alias if it differs from both stored values
        if canonical != raw.name:
            _add_alias_if_new(db, entity, canonical, report)

    return entity


def _add_alias_if_new(
    db: Session,
    entity: KGEntity,
    alias: str,
    report: IngestionReport,
) -> None:
    if not alias or alias == entity.name or alias == entity.canonical_name:
        return
    existing = (
        db.query(KGEntityAlias)
        .filter(
            KGEntityAlias.entity_id == entity.id,
            KGEntityAlias.alias == alias,
        )
        .first()
    )
    if not existing:
        db.add(KGEntityAlias(entity_id=entity.id, alias=alias))
        report.aliases_added += 1


# ── Issue repository ──────────────────────────────────────────────────────────

def upsert_issue(db: Session, slug: str, display_name: str, report: IngestionReport) -> KGIssue:
    """
    Insert or retrieve an issue row by slug.  Updates display_name if a
    non-empty value arrives and the stored value differs.
    """
    issue = db.query(KGIssue).filter(KGIssue.name == slug).first()

    if issue is None:
        issue = KGIssue(name=slug, display_name=display_name)
        db.add(issue)
        db.flush()
        report.issues_created += 1
    else:
        report.issues_skipped += 1
        if display_name and issue.display_name != display_name:
            issue.display_name = display_name

    return issue


# ── Event repository ──────────────────────────────────────────────────────────

def get_or_create_event(
    db: Session,
    name: str,
    event_type: str,
    event_timestamp: Optional[str],
    description: Optional[str],
    source_id: int,
    report: IngestionReport,
) -> KGEvent:
    """
    Deduplicate by (name, event_type).  The schema has no unique constraint
    here, so we query before inserting.
    """
    event = (
        db.query(KGEvent)
        .filter(KGEvent.name == name, KGEvent.event_type == event_type)
        .first()
    )

    if event is None:
        ts = _parse_timestamp(event_timestamp)
        event = KGEvent(
            name=name,
            event_type=event_type,
            event_timestamp=ts,
            description=description,
            related_source_id=source_id,
        )
        db.add(event)
        db.flush()
        report.events_created += 1
    else:
        report.events_skipped += 1

    return event


def _parse_timestamp(value: Optional[str]):
    """Parse an ISO 8601 string into a datetime, silently returning None on failure."""
    if not value:
        return None
    from datetime import datetime
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(value[:19], fmt[:len(value[:19].replace("Z", ""))])
        except ValueError:
            continue
    log.debug("KG ingest: could not parse timestamp %r, storing NULL", value)
    return None


# ── Claim repository ──────────────────────────────────────────────────────────

def get_or_create_claim(
    db: Session,
    text: str,
    stance: str,
    confidence: float,
    source_id: int,
    report: IngestionReport,
    entity_names: Optional[list[str]] = None,
    issue_slugs: Optional[list[str]] = None,
) -> KGClaim:
    """
    Deduplicate within a source by semantic_id.

    Semantic identity is computed by claim_normalizer.normalize_claim().
    The hash is minimal: action tokens + stance + entity canonical names.
    Numeric facts and issue slugs are excluded from the hash by design.

    Provenance is never merged: only rows with the SAME source_id are compared.
    Two different sources asserting the same fact each retain their own row.
    """
    # ── Semantic normalisation ────────────────────────────────────────────────
    normalized_text, semantic_id = normalize_claim(
        text,
        stance=stance,
        entity_names=entity_names,
        issue_slugs=issue_slugs,
    )

    # ── Deduplication: semantic_id within the same source ─────────────────────
    # We use semantic_id (not raw text equality) as the dedup key.  This is
    # strictly better than exact-text matching because:
    #   • same text, same entities  → same semantic_id → correctly deduped
    #   • same text, diff entities  → diff semantic_id → correctly NOT deduped
    #   • diff text, same meaning   → same semantic_id → deduped (new feature)
    #
    # Cross-source: only claims with the same source_id are compared here.
    # A different source asserting the same fact creates its own row.
    sem_match = (
        db.query(KGClaim)
        .filter(
            KGClaim.source_id   == source_id,
            KGClaim.semantic_id == semantic_id,
        )
        .first()
    )
    if sem_match is not None:
        log.debug(
            "KG ingest: semantic dedup — skipping %r (matches existing claim id=%d, "
            "semantic_id=%s)",
            text[:80], sem_match.id, semantic_id,
        )
        report.claims_skipped += 1
        return sem_match

    # ── Create new claim ───────────────────────────────────────────────────────
    claim = KGClaim(
        text=text,
        normalized_text=normalized_text,
        stance=stance,
        confidence=confidence,
        source_id=source_id,
        semantic_id=semantic_id,
    )
    db.add(claim)
    db.flush()
    report.claims_created += 1
    return claim


# ── Claim↔Entity join ─────────────────────────────────────────────────────────

def link_claim_entity(db: Session, claim_id: int, entity_id: int) -> bool:
    """Insert a claim↔entity join row if it does not already exist."""
    existing = (
        db.query(KGClaimEntity)
        .filter(
            KGClaimEntity.claim_id == claim_id,
            KGClaimEntity.entity_id == entity_id,
        )
        .first()
    )
    if existing:
        return False
    db.add(KGClaimEntity(claim_id=claim_id, entity_id=entity_id))
    db.flush()  # make row visible to subsequent checks in the same transaction
    return True


# ── Claim↔Issue join ──────────────────────────────────────────────────────────

def link_claim_issue(db: Session, claim_id: int, issue_id: int) -> bool:
    """Insert a claim↔issue join row if it does not already exist."""
    existing = (
        db.query(KGClaimIssue)
        .filter(
            KGClaimIssue.claim_id == claim_id,
            KGClaimIssue.issue_id == issue_id,
        )
        .first()
    )
    if existing:
        return False
    db.add(KGClaimIssue(claim_id=claim_id, issue_id=issue_id))
    db.flush()  # make row visible to subsequent checks in the same transaction
    return True


# ── Edge repository ───────────────────────────────────────────────────────────

def insert_edge_if_missing(
    db: Session,
    from_type: str,
    from_id: int,
    to_type: str,
    to_id: int,
    relationship_type: str,
    confidence_score: Optional[float],
    report: IngestionReport,
) -> None:
    """
    Insert a kg_edges row only if the typed relationship does not already exist.
    The schema has UNIQUE (from_type, from_id, to_type, to_id, relationship_type).
    """
    existing = (
        db.query(KGEdge)
        .filter(
            KGEdge.from_type == from_type,
            KGEdge.from_id   == from_id,
            KGEdge.to_type   == to_type,
            KGEdge.to_id     == to_id,
            KGEdge.relationship_type == relationship_type,
        )
        .first()
    )
    if existing:
        report.edges_skipped += 1
        return

    db.add(KGEdge(
        from_type=from_type,
        from_id=from_id,
        to_type=to_type,
        to_id=to_id,
        relationship_type=relationship_type,
        confidence_score=confidence_score,
    ))
    db.flush()  # make row visible to subsequent checks in the same transaction
    report.edges_created += 1


# ── Top-level ingestion service ───────────────────────────────────────────────

class KGIngestionService:
    """
    Persists a validated ExtractionResult into the kg_* tables.

    All writes happen within the caller-supplied SQLAlchemy session.  The
    caller is responsible for committing or rolling back.

    Usage:
        svc = KGIngestionService()
        report = svc.ingest(result, source_id=42, db=session)
        session.commit()
    """

    def ingest(
        self,
        result: ExtractionResult,
        source_id: int,
        db: Session,
    ) -> IngestionReport:
        """
        Persist result into the kg_* tables.

        Writes are ordered to satisfy FK dependencies:
          entities → issues → events → claims → join tables → edges
        """
        report = IngestionReport()

        # ── 1. Entities ───────────────────────────────────────────────────────
        # name → KGEntity row; used to resolve claim.entity_names below.
        entity_map: dict[str, KGEntity] = {}
        for raw_ent in result.entities:
            try:
                entity = upsert_entity(db, raw_ent, report)
                # Store under all names the claim layer might reference
                entity_map[raw_ent.name] = entity
                canonical = _effective_canonical(raw_ent)
                if canonical != raw_ent.name:
                    entity_map[canonical] = entity
            except Exception as exc:
                msg = f"entity upsert failed for {raw_ent.name!r}: {exc}"
                log.error("KG ingest: %s", msg)
                report.errors.append(msg)

        # ── 2. Issues ─────────────────────────────────────────────────────────
        # slug → KGIssue row
        issue_map: dict[str, KGIssue] = {}
        for raw_issue in result.issues:
            try:
                issue = upsert_issue(db, raw_issue.slug, raw_issue.display_name, report)
                issue_map[raw_issue.slug] = issue
            except Exception as exc:
                msg = f"issue upsert failed for {raw_issue.slug!r}: {exc}"
                log.error("KG ingest: %s", msg)
                report.errors.append(msg)

        # ── 3. Events ─────────────────────────────────────────────────────────
        # name → KGEvent row
        event_map: dict[str, KGEvent] = {}
        for raw_ev in result.events:
            try:
                event = get_or_create_event(
                    db,
                    name=raw_ev.name,
                    event_type=raw_ev.type,
                    event_timestamp=raw_ev.event_timestamp,
                    description=raw_ev.description,
                    source_id=source_id,
                    report=report,
                )
                event_map[raw_ev.name] = event
            except Exception as exc:
                msg = f"event insert failed for {raw_ev.name!r}: {exc}"
                log.error("KG ingest: %s", msg)
                report.errors.append(msg)

        # ── 4. Claims + join tables + edges ───────────────────────────────────
        for validated_claim in result.claims:
            try:
                # Resolve entity surface names → canonical names so the
                # semantic_id uses stable forms regardless of LLM phrasing.
                resolved_entity_names = [
                    (entity_map[n].canonical_name or entity_map[n].name)
                    for n in validated_claim.entity_names
                    if n in entity_map
                ]
                claim = get_or_create_claim(
                    db,
                    text=validated_claim.text,
                    stance=validated_claim.stance,
                    confidence=validated_claim.confidence,
                    source_id=source_id,
                    entity_names=resolved_entity_names,
                    issue_slugs=validated_claim.issue_slugs,
                    report=report,
                )
            except Exception as exc:
                msg = f"claim insert failed: {exc}"
                log.error("KG ingest: %s", msg)
                report.errors.append(msg)
                continue

            # ── 4a. Claim ↔ Entity links + MENTIONS edges ──────────────────
            for ename in validated_claim.entity_names:
                entity = entity_map.get(ename)
                if entity is None:
                    log.warning("KG ingest: claim references unknown entity %r — skipping", ename)
                    continue
                link_claim_entity(db, claim.id, entity.id)
                insert_edge_if_missing(
                    db,
                    from_type="claim", from_id=claim.id,
                    to_type="entity",  to_id=entity.id,
                    relationship_type="MENTIONS",
                    confidence_score=validated_claim.confidence,
                    report=report,
                )

            # ── 4b. Claim ↔ Issue links + RELATES_TO edges ────────────────
            for islug in validated_claim.issue_slugs:
                issue = issue_map.get(islug)
                if issue is None:
                    log.warning("KG ingest: claim references unknown issue %r — skipping", islug)
                    continue
                link_claim_issue(db, claim.id, issue.id)
                insert_edge_if_missing(
                    db,
                    from_type="claim", from_id=claim.id,
                    to_type="issue",   to_id=issue.id,
                    relationship_type="RELATES_TO",
                    confidence_score=validated_claim.confidence,
                    report=report,
                )

            # ── 4c. Claim → Event edges (OCCURRED_IN) ─────────────────────
            for ename in validated_claim.event_names:
                event = event_map.get(ename)
                if event is None:
                    log.warning("KG ingest: claim references unknown event %r — skipping", ename)
                    continue
                insert_edge_if_missing(
                    db,
                    from_type="claim", from_id=claim.id,
                    to_type="event",   to_id=event.id,
                    relationship_type="OCCURRED_IN",
                    confidence_score=validated_claim.confidence,
                    report=report,
                )

        log.info(
            "KG ingest complete — source_id=%d  created=%d  skipped=%d  errors=%d",
            source_id, report.total_created,
            report.entities_skipped + report.issues_skipped +
            report.events_skipped   + report.claims_skipped +
            report.edges_skipped,
            len(report.errors),
        )
        return report
