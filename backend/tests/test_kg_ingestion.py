"""
Tests for the KG ingestion layer.

All tests use an in-memory SQLite database so they are fully isolated.
No LLM calls are made; inputs are hand-built ExtractionResult objects.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.knowledge_graph import orm as _kg_orm  # noqa: F401 — registers kg_* tables
from app.knowledge_graph.extraction_types import (
    ExtractionResult,
    RawExtractedEntity,
    RawExtractedEvent,
    RawExtractedIssue,
    ValidatedClaim,
)
from app.knowledge_graph.ingestion import (
    KGIngestionService,
    IngestionReport,
    upsert_entity,
    upsert_issue,
    get_or_create_event,
    get_or_create_claim,
    insert_edge_if_missing,
)
from app.knowledge_graph.orm import (
    KGClaim, KGEdge, KGEntity, KGEntityAlias, KGEvent, KGIssue,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


SOURCE_ID = 1  # fictional source_id; no FK enforcement in SQLite by default


def _result(
    entities=None, issues=None, events=None, claims=None
) -> ExtractionResult:
    return ExtractionResult(
        entities=entities or [],
        issues=issues   or [],
        events=events   or [],
        claims=claims   or [],
    )


# ── upsert_entity ─────────────────────────────────────────────────────────────

class TestUpsertEntity:
    def _raw(self, name, canonical=None, etype="PERSON"):
        return RawExtractedEntity(type=etype, name=name,
                                  canonical_name_candidate=canonical)

    def test_creates_new_entity(self, db):
        report = IngestionReport()
        e = upsert_entity(db, self._raw("Jane Smith", "Jane Smith"), report)
        assert e.id is not None
        assert e.name == "Jane Smith"
        assert report.entities_created == 1
        assert report.entities_skipped == 0

    def test_deduplicates_by_canonical_name(self, db):
        report = IngestionReport()
        e1 = upsert_entity(db, self._raw("J. Smith", "Jane Smith"), report)
        e2 = upsert_entity(db, self._raw("Jane Smith", "Jane Smith"), report)
        assert e1.id == e2.id
        assert report.entities_created == 1
        assert report.entities_skipped == 1

    def test_deduplicates_by_surface_name(self, db):
        report = IngestionReport()
        e1 = upsert_entity(db, self._raw("Jane Smith"), report)
        e2 = upsert_entity(db, self._raw("Jane Smith"), report)
        assert e1.id == e2.id
        assert report.entities_created == 1
        assert report.entities_skipped == 1

    def test_adds_alias_for_new_surface_form(self, db):
        report = IngestionReport()
        e1 = upsert_entity(db, self._raw("Jane Smith", "Jane E. Smith"), report)
        # Second extraction uses a different surface form but same canonical
        e2 = upsert_entity(db, self._raw("Mayor Smith", "Jane E. Smith"), report)
        db.flush()
        assert e1.id == e2.id
        aliases = db.query(KGEntityAlias).filter(KGEntityAlias.entity_id == e1.id).all()
        alias_values = {a.alias for a in aliases}
        assert "Mayor Smith" in alias_values
        assert report.aliases_added >= 1

    def test_deduplicates_via_alias_lookup(self, db):
        report = IngestionReport()
        # First extraction creates the entity + alias
        e1 = upsert_entity(db, self._raw("Mayor Smith", "Jane E. Smith"), report)
        db.flush()
        # Second extraction uses the alias as the surface name
        e2 = upsert_entity(db, self._raw("Mayor Smith", "Jane E. Smith"), report)
        assert e1.id == e2.id

    def test_no_self_alias(self, db):
        """The entity's own name should never appear as an alias row."""
        report = IngestionReport()
        e = upsert_entity(db, self._raw("Jane Smith", "Jane Smith"), report)
        db.flush()
        aliases = db.query(KGEntityAlias).filter(KGEntityAlias.entity_id == e.id).all()
        assert not any(a.alias == "Jane Smith" for a in aliases)


# ── upsert_issue ──────────────────────────────────────────────────────────────

class TestUpsertIssue:
    def test_creates_issue(self, db):
        report = IngestionReport()
        iss = upsert_issue(db, "housing_affordability", "Housing Affordability", report)
        assert iss.id is not None
        assert iss.name == "housing_affordability"
        assert report.issues_created == 1

    def test_deduplicates_by_slug(self, db):
        report = IngestionReport()
        i1 = upsert_issue(db, "housing_affordability", "Housing Affordability", report)
        i2 = upsert_issue(db, "housing_affordability", "Housing Affordability", report)
        assert i1.id == i2.id
        assert report.issues_created == 1
        assert report.issues_skipped == 1

    def test_updates_display_name(self, db):
        report = IngestionReport()
        i1 = upsert_issue(db, "pub_safety", "Public Safety", report)
        i2 = upsert_issue(db, "pub_safety", "Public Safety & Crime", report)
        assert i1.id == i2.id
        assert i2.display_name == "Public Safety & Crime"


# ── get_or_create_event ───────────────────────────────────────────────────────

class TestGetOrCreateEvent:
    def test_creates_event(self, db):
        report = IngestionReport()
        ev = get_or_create_event(db, "City Debate 2024", "DEBATE", None, None, SOURCE_ID, report)
        assert ev.id is not None
        assert report.events_created == 1

    def test_deduplicates_by_name_and_type(self, db):
        report = IngestionReport()
        ev1 = get_or_create_event(db, "City Debate 2024", "DEBATE", None, None, SOURCE_ID, report)
        ev2 = get_or_create_event(db, "City Debate 2024", "DEBATE", "2024-09-10", "desc", SOURCE_ID, report)
        assert ev1.id == ev2.id
        assert report.events_created == 1
        assert report.events_skipped == 1

    def test_same_name_different_type_is_distinct(self, db):
        report = IngestionReport()
        ev1 = get_or_create_event(db, "Budget Vote", "VOTE",   None, None, SOURCE_ID, report)
        ev2 = get_or_create_event(db, "Budget Vote", "POLICY", None, None, SOURCE_ID, report)
        assert ev1.id != ev2.id
        assert report.events_created == 2


# ── get_or_create_claim ───────────────────────────────────────────────────────

class TestGetOrCreateClaim:
    def test_creates_claim(self, db):
        report = IngestionReport()
        c = get_or_create_claim(db, "Smith raised taxes.", "oppose", 0.8, SOURCE_ID, report)
        assert c.id is not None
        assert report.claims_created == 1

    def test_deduplicates_same_source_and_text(self, db):
        report = IngestionReport()
        c1 = get_or_create_claim(db, "Smith raised taxes.", "oppose", 0.8, SOURCE_ID, report)
        c2 = get_or_create_claim(db, "Smith raised taxes.", "oppose", 0.8, SOURCE_ID, report)
        assert c1.id == c2.id
        assert report.claims_skipped == 1

    def test_same_text_different_source_is_distinct(self, db):
        report = IngestionReport()
        c1 = get_or_create_claim(db, "Smith raised taxes.", "oppose", 0.8, SOURCE_ID,     report)
        c2 = get_or_create_claim(db, "Smith raised taxes.", "oppose", 0.8, SOURCE_ID + 1, report)
        assert c1.id != c2.id
        assert report.claims_created == 2


# ── insert_edge_if_missing ────────────────────────────────────────────────────

class TestInsertEdge:
    def test_inserts_edge(self, db):
        report = IngestionReport()
        insert_edge_if_missing(db, "claim", 1, "entity", 2, "MENTIONS", 0.9, report)
        db.flush()
        assert db.query(KGEdge).count() == 1
        assert report.edges_created == 1

    def test_idempotent_on_duplicate(self, db):
        report = IngestionReport()
        insert_edge_if_missing(db, "claim", 1, "entity", 2, "MENTIONS", 0.9, report)
        insert_edge_if_missing(db, "claim", 1, "entity", 2, "MENTIONS", 0.9, report)
        db.flush()
        assert db.query(KGEdge).count() == 1
        assert report.edges_skipped == 1

    def test_different_rel_type_creates_separate_edge(self, db):
        report = IngestionReport()
        insert_edge_if_missing(db, "claim", 1, "entity", 2, "MENTIONS",   0.9, report)
        insert_edge_if_missing(db, "claim", 1, "entity", 2, "RELATES_TO", 0.9, report)
        db.flush()
        assert db.query(KGEdge).count() == 2


# ── KGIngestionService.ingest (end-to-end) ────────────────────────────────────

class TestKGIngestionService:
    def _make_result(self) -> ExtractionResult:
        entities = [
            RawExtractedEntity(type="PERSON", name="Jane Smith",
                               canonical_name_candidate="Jane Smith"),
            RawExtractedEntity(type="ORG",    name="City Council",
                               canonical_name_candidate="City Council"),
        ]
        issues = [
            RawExtractedIssue(slug="housing_affordability",
                              display_name="Housing Affordability"),
        ]
        events = [
            RawExtractedEvent(name="Budget Hearing 2024", type="POLICY",
                              event_timestamp=None, description="Annual budget hearing"),
        ]
        claims = [
            ValidatedClaim(
                text="Jane Smith voted against affordable housing funding.",
                stance="oppose",
                confidence=0.85,
                entity_names=["Jane Smith", "City Council"],
                issue_slugs=["housing_affordability"],
                event_names=["Budget Hearing 2024"],
            ),
        ]
        return ExtractionResult(entities=entities, issues=issues,
                                events=events, claims=claims)

    def test_full_ingest_creates_expected_rows(self, db):
        svc = KGIngestionService()
        report = svc.ingest(self._make_result(), SOURCE_ID, db)
        db.commit()

        assert db.query(KGEntity).count() == 2
        assert db.query(KGIssue).count()  == 1
        assert db.query(KGEvent).count()  == 1
        assert db.query(KGClaim).count()  == 1
        assert report.entities_created == 2
        assert report.issues_created   == 1
        assert report.events_created   == 1
        assert report.claims_created   == 1
        assert len(report.errors)      == 0

    def test_edges_created_correctly(self, db):
        svc = KGIngestionService()
        svc.ingest(self._make_result(), SOURCE_ID, db)
        db.commit()

        edges = db.query(KGEdge).all()
        rel_types = {e.relationship_type for e in edges}
        # claim → entity (×2), claim → issue (×1), claim → event (×1)
        assert "MENTIONS"   in rel_types
        assert "RELATES_TO" in rel_types
        assert "OCCURRED_IN" in rel_types
        assert db.query(KGEdge).filter(KGEdge.relationship_type == "MENTIONS").count() == 2

    def test_full_idempotency(self, db):
        """Ingesting the same ExtractionResult twice must not duplicate any rows."""
        svc = KGIngestionService()
        result = self._make_result()

        svc.ingest(result, SOURCE_ID, db)
        db.commit()

        counts_after_first = {
            "entities": db.query(KGEntity).count(),
            "issues":   db.query(KGIssue).count(),
            "events":   db.query(KGEvent).count(),
            "claims":   db.query(KGClaim).count(),
            "edges":    db.query(KGEdge).count(),
        }

        report2 = svc.ingest(result, SOURCE_ID, db)
        db.commit()

        assert db.query(KGEntity).count() == counts_after_first["entities"]
        assert db.query(KGIssue).count()  == counts_after_first["issues"]
        assert db.query(KGEvent).count()  == counts_after_first["events"]
        assert db.query(KGClaim).count()  == counts_after_first["claims"]
        assert db.query(KGEdge).count()   == counts_after_first["edges"]

        assert report2.entities_created == 0
        assert report2.issues_created   == 0
        assert report2.events_created   == 0
        assert report2.claims_created   == 0

    def test_empty_extraction_result_is_safe(self, db):
        svc = KGIngestionService()
        report = svc.ingest(ExtractionResult(), SOURCE_ID, db)
        db.commit()
        assert report.total_created == 0
        assert len(report.errors) == 0

    def test_claim_with_no_entity_links(self, db):
        """Claims with empty entity_names/issue_slugs should still be persisted."""
        result = _result(
            claims=[ValidatedClaim(
                text="Something happened.",
                stance="neutral",
                confidence=0.6,
                entity_names=[],
                issue_slugs=[],
                event_names=[],
            )]
        )
        svc = KGIngestionService()
        report = svc.ingest(result, SOURCE_ID, db)
        db.commit()
        assert db.query(KGClaim).count() == 1
        assert report.claims_created == 1
        assert report.edges_created  == 0

    def test_multiple_sources_same_claim_text(self, db):
        """The same claim text under two different source_ids creates two rows."""
        claim_kwargs = dict(
            text="Smith cut education funding.",
            stance="oppose", confidence=0.9,
            entity_names=[], issue_slugs=[], event_names=[],
        )
        r1 = _result(claims=[ValidatedClaim(**claim_kwargs)])
        r2 = _result(claims=[ValidatedClaim(**claim_kwargs)])

        svc = KGIngestionService()
        svc.ingest(r1, SOURCE_ID,     db)
        svc.ingest(r2, SOURCE_ID + 1, db)
        db.commit()

        assert db.query(KGClaim).count() == 2
