"""
Integration tests for the KG pipeline hook in the ingestion service.

Tests verify:
  - Feature flag gates execution (ENABLE_KG_PIPELINE)
  - archived_as_irrelevant items are skipped
  - get_or_create_kg_source idempotency
  - _run_kg_pipeline writes to kg_* tables when enabled
  - failures in KG pipeline do not propagate as exceptions
"""
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.knowledge_graph import orm as _kg_orm  # noqa: F401 — registers kg_* tables
from app.knowledge_graph.ingestion import get_or_create_kg_source
from app.knowledge_graph.orm import KGClaim, KGEntity, KGIssue, KGSource
from app.models import SourceItem  # noqa: F401 — registers core tables


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


def _fake_item(db, *, archived=False, raw_text="The mayor raised taxes on housing."):
    """Create and flush a minimal SourceItem."""
    item = SourceItem(
        title="Test headline",
        raw_text=raw_text,
        source_name="TestSource",
        source_type="news",
        source_url="https://example.com/test",
        archived_as_irrelevant=archived,
    )
    db.add(item)
    db.flush()
    return item


# ── get_or_create_kg_source ───────────────────────────────────────────────────

class TestGetOrCreateKGSource:
    def test_creates_source_row(self, db):
        src = get_or_create_kg_source(db, url="https://example.com", text="text")
        assert src.id is not None
        assert db.query(KGSource).count() == 1

    def test_idempotent_on_same_url_and_text(self, db):
        s1 = get_or_create_kg_source(db, url="https://example.com", text="text")
        s2 = get_or_create_kg_source(db, url="https://example.com", text="text")
        assert s1.id == s2.id
        assert db.query(KGSource).count() == 1

    def test_different_text_creates_new_source(self, db):
        s1 = get_or_create_kg_source(db, url="https://example.com", text="text A")
        s2 = get_or_create_kg_source(db, url="https://example.com", text="text B")
        assert s1.id != s2.id

    def test_stores_source_item_id(self, db):
        src = get_or_create_kg_source(db, url="https://x.com", text="t", source_item_id=42)
        assert src.source_item_id == 42

    def test_none_url_does_not_crash(self, db):
        src = get_or_create_kg_source(db, url=None, text="some text")
        assert src.id is not None


# ── _run_kg_pipeline feature flag ─────────────────────────────────────────────

class TestKGPipelineFeatureFlag:
    def test_disabled_by_default(self, db, monkeypatch):
        """With no env var, nothing is written to kg_* tables."""
        monkeypatch.delenv("ENABLE_KG_PIPELINE", raising=False)
        from app.services.ingestion import _run_kg_pipeline
        item = _fake_item(db)
        _run_kg_pipeline(db, item)
        db.commit()
        assert db.query(KGSource).count() == 0

    def test_enabled_writes_kg_source(self, db, monkeypatch):
        monkeypatch.setenv("ENABLE_KG_PIPELINE", "true")
        from app.services.ingestion import _run_kg_pipeline
        item = _fake_item(db)
        _run_kg_pipeline(db, item)
        assert db.query(KGSource).count() == 1

    def test_enabled_flag_variants(self, db, monkeypatch):
        for val in ("1", "true", "yes", "TRUE", "YES"):
            monkeypatch.setenv("ENABLE_KG_PIPELINE", val)
            from app.services.ingestion import _kg_enabled
            assert _kg_enabled() is True

    def test_disabled_flag_variants(self, db, monkeypatch):
        for val in ("0", "false", "no", "off", ""):
            monkeypatch.setenv("ENABLE_KG_PIPELINE", val)
            from app.services.ingestion import _kg_enabled
            assert _kg_enabled() is False


# ── _run_kg_pipeline skip logic ───────────────────────────────────────────────

class TestKGPipelineSkipLogic:
    def test_skips_archived_items(self, db, monkeypatch):
        monkeypatch.setenv("ENABLE_KG_PIPELINE", "true")
        from app.services.ingestion import _run_kg_pipeline
        item = _fake_item(db, archived=True)
        _run_kg_pipeline(db, item)
        assert db.query(KGSource).count() == 0

    def test_skips_empty_text(self, db, monkeypatch):
        monkeypatch.setenv("ENABLE_KG_PIPELINE", "true")
        from app.services.ingestion import _run_kg_pipeline
        item = _fake_item(db, raw_text="")
        item.title = ""
        _run_kg_pipeline(db, item)
        assert db.query(KGSource).count() == 0


# ── _run_kg_pipeline writes KG data ──────────────────────────────────────────

class TestKGPipelineWrites:
    def test_mock_provider_populates_tables(self, db, monkeypatch):
        """
        MockLLMProvider runs keyword extraction — housing text should yield at
        least one claim in kg_claims and one source in kg_sources.
        """
        monkeypatch.setenv("ENABLE_KG_PIPELINE", "true")
        monkeypatch.setenv("LLM_PROVIDER", "mock")

        from app.services.ingestion import _run_kg_pipeline
        item = _fake_item(
            db,
            raw_text=(
                "Mayor Chen raised rents in the district by 30 percent. "
                "Housing affordability has worsened under the current council. "
                "Tenants are facing eviction across the city."
            ),
        )
        _run_kg_pipeline(db, item)

        assert db.query(KGSource).count() == 1
        kg_src = db.query(KGSource).first()
        assert kg_src.source_item_id == item.id
        # Mock provider does keyword extraction; housing text should yield entities
        # and/or claims.  We assert non-zero total rather than exact counts since
        # mock output is heuristic.
        total = db.query(KGClaim).count() + db.query(KGEntity).count()
        assert total > 0

    def test_idempotent_on_second_run(self, db, monkeypatch):
        monkeypatch.setenv("ENABLE_KG_PIPELINE", "true")
        monkeypatch.setenv("LLM_PROVIDER", "mock")
        from app.services.ingestion import _run_kg_pipeline

        item = _fake_item(db, raw_text="Housing costs soared as tenants faced eviction.")
        _run_kg_pipeline(db, item)
        _run_kg_pipeline(db, item)

        assert db.query(KGSource).count() == 1

    def test_kg_source_linked_to_source_item(self, db, monkeypatch):
        monkeypatch.setenv("ENABLE_KG_PIPELINE", "true")
        monkeypatch.setenv("LLM_PROVIDER", "mock")
        from app.services.ingestion import _run_kg_pipeline

        item = _fake_item(db, raw_text="Road potholes worsened infrastructure problems.")
        _run_kg_pipeline(db, item)

        kg_src = db.query(KGSource).first()
        assert kg_src.source_item_id == item.id
        assert kg_src.url == item.source_url


# ── FK correctness: kg_claims.source_id → kg_sources.id (not source_items.id) ─

class TestKGClaimSourceIdFK:
    """
    These tests force item.id != kg_source.id so we can prove the right value
    is stored in kg_claims.source_id.

    Strategy: pre-create N SourceItems before the pipeline runs so the target
    item's id > 1, while the KGSource table is empty and its first row gets id=1.
    That makes item.id != kg_source.id, exposing any bug where the code passes
    item.id instead of kg_source.id.
    """

    def _setup(self, db, monkeypatch, n_pre_items=2):
        monkeypatch.setenv("ENABLE_KG_PIPELINE", "true")
        monkeypatch.setenv("LLM_PROVIDER", "mock")

        # Burn n_pre_items IDs in source_items so item.id starts at n_pre_items+1
        for i in range(n_pre_items):
            dummy = SourceItem(
                title=f"dummy {i}",
                raw_text="filler",
                source_name="s",
                source_type="news",
                source_url=f"https://dummy{i}.example.com",
            )
            db.add(dummy)
        db.flush()

        # The real item — its id will be > 1
        item = SourceItem(
            title="Housing crisis deepens",
            raw_text=(
                "Mayor Chen raised rents across the district. "
                "Tenants face eviction as housing affordability worsens."
            ),
            source_name="TestSource",
            source_type="news",
            source_url="https://real.example.com/article",
        )
        db.add(item)
        db.flush()
        return item

    def test_item_id_and_kg_source_id_differ(self, db, monkeypatch):
        """Sanity check: IDs must differ for the FK tests below to be meaningful."""
        item = self._setup(db, monkeypatch)
        from app.services.ingestion import _run_kg_pipeline
        _run_kg_pipeline(db, item)

        kg_src = db.query(KGSource).first()
        assert kg_src is not None
        assert item.id != kg_src.id, (
            f"Test precondition failed: item.id={item.id} == kg_source.id={kg_src.id}; "
            "IDs must differ for this test to be meaningful"
        )

    def test_kg_claims_source_id_points_to_kg_sources_id(self, db, monkeypatch):
        """
        kg_claims.source_id must equal kg_sources.id, NOT source_items.id.
        This is the core FK invariant: claims belong to a KG source row, not
        directly to the feed ingestion row.
        """
        item = self._setup(db, monkeypatch)
        from app.services.ingestion import _run_kg_pipeline
        _run_kg_pipeline(db, item)

        kg_src = db.query(KGSource).first()
        assert kg_src is not None

        claims = db.query(KGClaim).all()
        assert claims, "mock provider should produce at least one claim for housing text"

        for claim in claims:
            assert claim.source_id == kg_src.id, (
                f"claim.source_id={claim.source_id} should equal "
                f"kg_source.id={kg_src.id}, not item.id={item.id}"
            )
            assert claim.source_id != item.id, (
                f"claim.source_id={claim.source_id} must NOT equal "
                f"item.id={item.id} — that would be the wrong FK target"
            )

    def test_kg_source_source_item_id_points_to_source_item(self, db, monkeypatch):
        """
        kg_sources.source_item_id must equal source_items.id — the soft
        back-reference for traceability from the KG back to the feed row.
        """
        item = self._setup(db, monkeypatch)
        from app.services.ingestion import _run_kg_pipeline
        _run_kg_pipeline(db, item)

        kg_src = db.query(KGSource).first()
        assert kg_src is not None
        assert kg_src.source_item_id == item.id, (
            f"kg_source.source_item_id={kg_src.source_item_id} should equal "
            f"item.id={item.id}"
        )

    def test_both_fks_simultaneously(self, db, monkeypatch):
        """
        Combined assertion: kg_claims.source_id → kg_sources.id ← source_item_id
        → source_items.id.  All three values must be distinct and correct.
        """
        item = self._setup(db, monkeypatch, n_pre_items=3)
        from app.services.ingestion import _run_kg_pipeline
        _run_kg_pipeline(db, item)

        kg_src = db.query(KGSource).first()
        claims = db.query(KGClaim).all()
        assert claims

        # The three IDs in play
        source_item_id = item.id       # e.g. 4
        kg_source_id   = kg_src.id     # e.g. 1
        claim_source_id = claims[0].source_id

        assert source_item_id != kg_source_id, "precondition: IDs must differ"
        assert claim_source_id == kg_source_id
        assert kg_src.source_item_id == source_item_id


# ── Exception isolation ───────────────────────────────────────────────────────

class TestKGPipelineExceptionIsolation:
    def test_extractor_failure_does_not_raise(self, db, monkeypatch):
        """
        If the KG extractor raises an unexpected exception, _run_kg_pipeline
        must catch it and return normally — never propagating to the caller.
        """
        monkeypatch.setenv("ENABLE_KG_PIPELINE", "true")

        # Patch KGExtractor.extract to blow up
        import app.knowledge_graph.extractor as ext_mod
        original_extract = ext_mod.KGExtractor.extract

        def _boom(self, text):
            raise RuntimeError("simulated extractor crash")

        monkeypatch.setattr(ext_mod.KGExtractor, "extract", _boom)

        from app.services.ingestion import _run_kg_pipeline
        item = _fake_item(db)
        # Must NOT raise
        _run_kg_pipeline(db, item)

        # Restore
        monkeypatch.setattr(ext_mod.KGExtractor, "extract", original_extract)

    def test_existing_data_unaffected_after_kg_failure(self, db, monkeypatch):
        """
        A KG pipeline failure must not roll back already-committed SourceItem data.
        """
        monkeypatch.setenv("ENABLE_KG_PIPELINE", "true")

        import app.knowledge_graph.extractor as ext_mod

        def _boom(self, text):
            raise RuntimeError("simulated crash")

        monkeypatch.setattr(ext_mod.KGExtractor, "extract", _boom)

        item = _fake_item(db)
        db.commit()

        from app.services.ingestion import _run_kg_pipeline
        _run_kg_pipeline(db, item)

        # SourceItem row must still be present
        assert db.query(SourceItem).filter_by(id=item.id).first() is not None
