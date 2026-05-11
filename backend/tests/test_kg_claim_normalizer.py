"""
Tests for app/knowledge_graph/claim_normalizer.py and its integration with
the KG ingestion layer.

Three categories of tests:
  1. Unit: normalize_claim() produces equal semantic_ids for equivalent claims
     and different semantic_ids for distinct claims.
  2. Integration: get_or_create_claim() deduplicates on semantic_id within the
     same source, while preserving separate rows across sources.
  3. Provenance: claims about DIFFERENT subjects (entities) do NOT collapse even
     when the action phrasing is identical.

All DB tests use in-memory SQLite with a fresh schema for isolation.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.knowledge_graph import orm as _kg_orm  # noqa: F401 — register kg_* tables
from app.knowledge_graph.claim_normalizer import normalize_claim
from app.knowledge_graph.ingestion import (
    IngestionReport,
    get_or_create_claim,
)
from app.knowledge_graph.orm import KGClaim


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


SOURCE_A = 1
SOURCE_B = 2


# ── 1. Unit tests: normalize_claim() ─────────────────────────────────────────


class TestNormalizeClaimEquivalence:
    """
    Each test verifies that two semantically equivalent claim strings produce
    the same semantic_id (collapse), or that distinct claims do NOT collapse.
    """

    # ── Collapse case 1 ───────────────────────────────────────────────────────
    def test_vote_against_bill_vs_opposed_funding_collapse(self):
        """
        'voted against infrastructure bill' ≈ 'opposed infrastructure funding'

        Both describe the same political action:
          subject OPPOSES infrastructure (legislative vehicle is incidental).
        After phrase substitution:
          "voted against" → "opposed"
          "infrastructure bill"    → "infrastructure legislation"  (phrase map)
          "infrastructure funding" → "infrastructure legislation"  (phrase map)
        After wrapper-word removal both reduce to the same token set.
        """
        _, sid1 = normalize_claim(
            "voted against infrastructure bill",
            stance="oppose",
            entity_names=["Jane Smith"],
            issue_slugs=["infrastructure"],
        )
        _, sid2 = normalize_claim(
            "opposed infrastructure funding",
            stance="oppose",
            entity_names=["Jane Smith"],
            issue_slugs=["infrastructure"],
        )
        assert sid1 == sid2, (
            f"Expected same semantic_id for equivalent infrastructure claims, "
            f"got {sid1!r} vs {sid2!r}"
        )

    # ── Collapse case 2 ───────────────────────────────────────────────────────
    def test_endorsed_vs_supported_universal_healthcare_collapse(self):
        """
        'endorsed universal healthcare coverage' ≈ 'supported universal healthcare'

        'endorsed' → 'supported' (token synonym table)
        'coverage' removed as wrapper word
        Remaining tokens are identical.
        """
        _, sid1 = normalize_claim(
            "endorsed universal healthcare coverage",
            stance="support",
            entity_names=["Rob Bresnahan"],
            issue_slugs=["health"],
        )
        _, sid2 = normalize_claim(
            "supported universal healthcare",
            stance="support",
            entity_names=["Rob Bresnahan"],
            issue_slugs=["health"],
        )
        assert sid1 == sid2, (
            f"Expected same semantic_id for healthcare support claim variants, "
            f"got {sid1!r} vs {sid2!r}"
        )

    # ── Collapse case 3 ───────────────────────────────────────────────────────
    def test_received_donations_from_pacs_vs_accepted_contributions_collapse(self):
        """
        'received donations from PACs'
        ≈ 'accepted contributions from political action committees'

        Phrase map:
          'received donations from'       → 'received contributions from'
          'accepted contributions from'   → 'received contributions from'
          'political action committees'   → 'pac'
        After stop-word removal and synonym normalization the token sets are
        identical.
        """
        _, sid1 = normalize_claim(
            "received donations from PACs",
            stance="neutral",
            entity_names=["Mary Cognetti"],
            issue_slugs=["campaign_finance"],
        )
        _, sid2 = normalize_claim(
            "accepted contributions from political action committees",
            stance="neutral",
            entity_names=["Mary Cognetti"],
            issue_slugs=["campaign_finance"],
        )
        assert sid1 == sid2, (
            f"Expected same semantic_id for PAC-donation claim variants, "
            f"got {sid1!r} vs {sid2!r}"
        )

    # ── Collapse case 4 (bonus) ───────────────────────────────────────────────
    def test_voted_yes_vs_backed_legislation_collapse(self):
        """
        'voted yes on the climate bill' ≈ 'backed climate legislation'

        'voted yes on the' → 'supported'
        'backed'           → 'supported'  (token synonym)
        'climate bill' / 'climate legislation' → 'climate legislation' (phrase map)
        'legislation' removed as wrapper word.
        """
        _, sid1 = normalize_claim(
            "voted yes on the climate bill",
            stance="support",
            entity_names=["Senator Jones"],
            issue_slugs=["climate"],
        )
        _, sid2 = normalize_claim(
            "backed climate legislation",
            stance="support",
            entity_names=["Senator Jones"],
            issue_slugs=["climate"],
        )
        assert sid1 == sid2, (
            f"Expected same semantic_id for climate-support claim variants, "
            f"got {sid1!r} vs {sid2!r}"
        )

    # ── Non-collapse: different stance ────────────────────────────────────────
    def test_different_stance_does_not_collapse(self):
        """
        'supported the infrastructure bill' vs 'opposed the infrastructure bill'
        must NOT collapse — opposite political positions.
        """
        _, sid_support = normalize_claim(
            "supported the infrastructure bill",
            stance="support",
            entity_names=["Jane Smith"],
        )
        _, sid_oppose = normalize_claim(
            "opposed the infrastructure bill",
            stance="oppose",
            entity_names=["Jane Smith"],
        )
        assert sid_support != sid_oppose, (
            "Claims with opposite stances must not share a semantic_id"
        )

    # ── Non-collapse: different subjects ─────────────────────────────────────
    def test_different_entities_do_not_collapse(self):
        """
        'Cognetti opposed infrastructure funding' vs
        'Bresnahan opposed infrastructure funding'

        Same action, different subjects — entity names are folded into the
        semantic_id so these must NOT collapse.
        """
        _, sid_cognetti = normalize_claim(
            "opposed infrastructure funding",
            stance="oppose",
            entity_names=["Mary Cognetti"],
            issue_slugs=["infrastructure"],
        )
        _, sid_bresnahan = normalize_claim(
            "opposed infrastructure funding",
            stance="oppose",
            entity_names=["Rob Bresnahan"],
            issue_slugs=["infrastructure"],
        )
        assert sid_cognetti != sid_bresnahan, (
            "Claims about different subjects must not share a semantic_id"
        )

    # ── Normalized text is human-readable ─────────────────────────────────────
    def test_normalized_text_is_readable(self):
        """normalized_text is the phrase-substituted form, not a hash."""
        norm, _ = normalize_claim(
            "voted against infrastructure bill",
            stance="oppose",
        )
        # Must be a human-readable string, not a hex digest
        assert len(norm) > 5
        assert not all(c in "0123456789abcdef" for c in norm)
        # Phrase substitution should have fired
        assert "opposed" in norm or "infrastructure" in norm

    # ── semantic_id is a 16-char hex string ───────────────────────────────────
    def test_semantic_id_format(self):
        _, sid = normalize_claim("Smith raised taxes.", stance="oppose")
        assert len(sid) == 16
        assert all(c in "0123456789abcdef" for c in sid)

    # ── Deterministic ─────────────────────────────────────────────────────────
    def test_deterministic(self):
        """Same inputs always produce the same output."""
        args = ("Jane Smith voted against the education bill", "oppose",
                ["Jane Smith"], ["education"])
        result_a = normalize_claim(*args)
        result_b = normalize_claim(*args)
        assert result_a == result_b

    # ── Hedging removal ───────────────────────────────────────────────────────
    def test_hedging_phrases_stripped(self):
        """
        'reportedly voted against X' and 'voted against X' should produce the
        same semantic_id once the hedging word is removed.
        """
        _, sid_hedged = normalize_claim(
            "reportedly voted against infrastructure bill",
            stance="oppose",
            entity_names=["Jane Smith"],
        )
        _, sid_plain = normalize_claim(
            "voted against infrastructure bill",
            stance="oppose",
            entity_names=["Jane Smith"],
        )
        assert sid_hedged == sid_plain, (
            "Hedging prefix should not change the semantic_id"
        )


# ── 2. Integration tests: get_or_create_claim() ───────────────────────────────


class TestGetOrCreateClaimSemanticDedup:
    """
    Verify that the ingestion layer uses semantic_id to deduplicate claims
    within a source, without breaking the original exact-text behaviour.
    """

    def test_exact_duplicate_skipped(self, db):
        """Original behaviour: same (source, text) → one row."""
        report = IngestionReport()
        c1 = get_or_create_claim(db, "Smith raised taxes.", "oppose", 0.8,
                                  SOURCE_A, report)
        c2 = get_or_create_claim(db, "Smith raised taxes.", "oppose", 0.8,
                                  SOURCE_A, report)
        assert c1.id == c2.id
        assert report.claims_created == 1
        assert report.claims_skipped == 1

    def test_semantic_duplicate_same_source_skipped(self, db):
        """
        Semantically equivalent paraphrases from the SAME SOURCE → one row.
        This is the core new behaviour.
        """
        report = IngestionReport()
        c1 = get_or_create_claim(
            db,
            "voted against infrastructure bill",
            stance="oppose",
            confidence=0.8,
            source_id=SOURCE_A,
            entity_names=["Jane Smith"],
            issue_slugs=["infrastructure"],
            report=report,
        )
        c2 = get_or_create_claim(
            db,
            "opposed infrastructure funding",
            stance="oppose",
            confidence=0.75,
            source_id=SOURCE_A,
            entity_names=["Jane Smith"],
            issue_slugs=["infrastructure"],
            report=report,
        )
        assert c1.id == c2.id, (
            "Semantically equivalent claims from the same source should be deduplicated"
        )
        assert report.claims_created == 1
        assert report.claims_skipped == 1
        assert db.query(KGClaim).count() == 1

    def test_semantic_duplicate_different_source_preserved(self, db):
        """
        Same claim text from DIFFERENT SOURCES → separate rows (provenance preserved).
        """
        report = IngestionReport()
        c1 = get_or_create_claim(db, "Smith cut education funding.", "oppose",
                                  0.9, SOURCE_A, report)
        c2 = get_or_create_claim(db, "Smith cut education funding.", "oppose",
                                  0.9, SOURCE_B, report)
        assert c1.id != c2.id, (
            "Same claim from different sources must create separate rows"
        )
        assert report.claims_created == 2
        assert db.query(KGClaim).count() == 2

    def test_semantic_paraphrase_different_source_both_preserved(self, db):
        """
        Semantically equivalent phrasing from DIFFERENT SOURCES → two rows.
        Provenance is never merged.
        """
        report = IngestionReport()
        c1 = get_or_create_claim(
            db,
            "voted against infrastructure bill",
            stance="oppose",
            confidence=0.8,
            source_id=SOURCE_A,
            entity_names=["Jane Smith"],
            issue_slugs=["infrastructure"],
            report=report,
        )
        c2 = get_or_create_claim(
            db,
            "opposed infrastructure funding",
            stance="oppose",
            confidence=0.75,
            source_id=SOURCE_B,
            entity_names=["Jane Smith"],
            issue_slugs=["infrastructure"],
            report=report,
        )
        assert c1.id != c2.id, (
            "Semantically equivalent claims from different sources must each be stored"
        )
        assert report.claims_created == 2
        assert db.query(KGClaim).count() == 2
        # But they share the same semantic_id — the link is there for the narrative engine
        assert c1.semantic_id == c2.semantic_id, (
            "Cross-source semantic equivalents should share a semantic_id "
            "so the narrative engine can recognise them as the same assertion"
        )

    def test_normalized_text_and_semantic_id_persisted(self, db):
        """
        After insertion, both normalized_text and semantic_id must be non-null.
        """
        report = IngestionReport()
        claim = get_or_create_claim(
            db,
            "Jane Smith voted against infrastructure bill",
            stance="oppose",
            confidence=0.85,
            source_id=SOURCE_A,
            entity_names=["Jane Smith"],
            issue_slugs=["infrastructure"],
            report=report,
        )
        db.flush()
        assert claim.normalized_text is not None
        assert claim.semantic_id is not None
        assert len(claim.semantic_id) == 16

    def test_different_subject_not_deduplicated(self, db):
        """
        'Cognetti opposed X' and 'Bresnahan opposed X' from the SAME SOURCE
        must NOT be deduplicated — they are claims about different people.
        """
        report = IngestionReport()
        c1 = get_or_create_claim(
            db,
            "opposed infrastructure funding",
            stance="oppose",
            confidence=0.8,
            source_id=SOURCE_A,
            entity_names=["Mary Cognetti"],
            issue_slugs=["infrastructure"],
            report=report,
        )
        c2 = get_or_create_claim(
            db,
            "opposed infrastructure funding",
            stance="oppose",
            confidence=0.8,
            source_id=SOURCE_A,
            entity_names=["Rob Bresnahan"],
            issue_slugs=["infrastructure"],
            report=report,
        )
        assert c1.id != c2.id, (
            "Claims about different entities must not be deduplicated "
            "even when the action phrasing is identical"
        )
        assert report.claims_created == 2

    def test_idempotent_on_repeated_ingest(self, db):
        """
        Calling get_or_create_claim three times with the same args → one row.
        """
        report = IngestionReport()
        kwargs = dict(
            text="endorsed universal healthcare coverage",
            stance="support",
            confidence=0.7,
            source_id=SOURCE_A,
            entity_names=["Rob Bresnahan"],
            issue_slugs=["health"],
            report=report,
        )
        c1 = get_or_create_claim(db, **kwargs)
        c2 = get_or_create_claim(db, **kwargs)
        c3 = get_or_create_claim(db, **kwargs)
        assert c1.id == c2.id == c3.id
        assert report.claims_created == 1
        assert report.claims_skipped == 2
        assert db.query(KGClaim).count() == 1


# ── 3. Numeric-fact correctness ───────────────────────────────────────────────


class TestNumericFactPreservation:
    """
    Dollar amounts, percentages, and vote ratios must be preserved in
    normalized_text and must differentiate otherwise-identical claims.
    """

    def test_same_amount_paraphrase_collapses(self, db):
        """
        'received $500,000 from PACs'
        ≈ 'accepted $500,000 from political action committees'
        Same actor, same amount, semantically equivalent wording → ONE row.
        """
        report = IngestionReport()
        c1 = get_or_create_claim(
            db,
            "received $500,000 from PACs",
            stance="neutral",
            confidence=0.8,
            source_id=SOURCE_A,
            entity_names=["Jane Smith"],
            report=report,
        )
        c2 = get_or_create_claim(
            db,
            "accepted $500,000 from political action committees",
            stance="neutral",
            confidence=0.8,
            source_id=SOURCE_A,
            entity_names=["Jane Smith"],
            report=report,
        )
        assert c1.id == c2.id, (
            "Same amount + semantically equivalent wording must deduplicate"
        )
        assert report.claims_created == 1
        assert report.claims_skipped == 1

    def test_dollar_amount_preserved_in_normalized_text(self, db):
        """Dollar amount must survive into normalized_text."""
        report = IngestionReport()
        claim = get_or_create_claim(
            db,
            "received $250,000 from donors",
            stance="neutral",
            confidence=0.8,
            source_id=SOURCE_A,
            entity_names=["Jane Smith"],
            report=report,
        )
        db.flush()
        assert claim.normalized_text is not None
        assert "250" in claim.normalized_text, (
            f"Expected '250' in normalized_text, got: {claim.normalized_text!r}"
        )

    def test_percentage_preserved_in_normalized_text(self):
        """Percentage must survive into normalized_text (unit test)."""
        from app.knowledge_graph.claim_normalizer import normalize_claim
        norm, _ = normalize_claim("won with 62% of the vote", stance="neutral")
        assert "62" in norm, (
            f"Expected '62' in normalized_text, got: {norm!r}"
        )


# ── 4. Phrase-boundary safety ─────────────────────────────────────────────────


class TestPhraseBoundaryCorrectness:
    """
    Word-boundary-anchored substitution must not corrupt tokens whose
    prefix accidentally matches a phrase entry.
    """

    def test_voted_for_their_not_corrupted(self):
        """
        'voted for their constituents' — 'their' starts with 'the' but is NOT
        a standalone 'the'.  The substitution must not fire mid-word.
        """
        from app.knowledge_graph.claim_normalizer import normalize_claim
        norm, _ = normalize_claim(
            "voted for their constituents",
            stance="support",
            entity_names=["Jane Smith"],
        )
        assert "supportedir" not in norm, (
            f"Partial-word corruption detected in: {norm!r}"
        )
        assert "constituents" in norm

    def test_voted_against_them_not_corrupted(self):
        """
        'voted against them' — 'them' starts with 'the' but is not 'the'.
        """
        from app.knowledge_graph.claim_normalizer import normalize_claim
        norm, _ = normalize_claim(
            "voted against them",
            stance="oppose",
            entity_names=["Rob Bresnahan"],
        )
        assert "opposedm" not in norm, (
            f"Partial-word corruption detected in: {norm!r}"
        )

    def test_voted_for_the_bill_still_substituted(self):
        """
        The fix must not break the legitimate 'voted for the' case.
        """
        from app.knowledge_graph.claim_normalizer import normalize_claim
        norm, _ = normalize_claim(
            "voted for the climate bill",
            stance="support",
            entity_names=["Jane Smith"],
        )
        assert "supported" in norm or "support" in norm, (
            f"Expected 'supported' in normalized_text, got: {norm!r}"
        )

    def test_voted_against_the_bill_still_substituted(self):
        from app.knowledge_graph.claim_normalizer import normalize_claim
        norm, _ = normalize_claim(
            "voted against the infrastructure bill",
            stance="oppose",
            entity_names=["Jane Smith"],
        )
        assert "opposed" in norm, (
            f"Expected 'opposed' in normalized_text, got: {norm!r}"
        )

    def test_different_domain_bills_do_not_collapse(self):
        """
        'voted for the education bill' vs 'voted for the climate bill' —
        domain tokens survive wrapper stripping and must produce different semantic_ids.
        """
        from app.knowledge_graph.claim_normalizer import normalize_claim
        _, sid_edu = normalize_claim(
            "voted for the education bill",
            stance="support",
            entity_names=["Jane Smith"],
        )
        _, sid_cli = normalize_claim(
            "voted for the climate bill",
            stance="support",
            entity_names=["Jane Smith"],
        )
        assert sid_edu != sid_cli, (
            "Claims about different legislative domains must not collapse"
        )


# ── 5. Numeric magnitude equivalence ─────────────────────────────────────────


class TestNumericMagnitudeEquivalence:
    """
    $1.2M, $1,200,000, and $1.2 million must hash to the same numeric key so
    that the same fundraising fact expressed in different formats deduplicates.
    """

    def test_dollar_suffix_k_equals_comma_form(self):
        """$500K == $500,000 — same claim after numeric normalization."""
        _, sid1 = normalize_claim(
            "raised $500K from donors", stance="neutral", entity_names=["Jane Smith"]
        )
        _, sid2 = normalize_claim(
            "raised $500,000 from donors", stance="neutral", entity_names=["Jane Smith"]
        )
        assert sid1 == sid2, "$500K and $500,000 must produce the same semantic_id"

    def test_dollar_suffix_m_equals_comma_form(self):
        """$1.2M == $1,200,000 == $1.2 million."""
        _, sid_m = normalize_claim(
            "raised $1.2M from donors", stance="neutral", entity_names=["Jane Smith"]
        )
        _, sid_comma = normalize_claim(
            "raised $1,200,000 from donors", stance="neutral", entity_names=["Jane Smith"]
        )
        _, sid_word = normalize_claim(
            "raised $1.2 million from donors", stance="neutral", entity_names=["Jane Smith"]
        )
        assert sid_m == sid_comma == sid_word, (
            "$1.2M, $1,200,000, and $1.2 million must produce the same semantic_id"
        )

    def test_dollar_billion_forms_collapse(self):
        """$2B == $2 billion == $2,000,000,000."""
        _, sid1 = normalize_claim(
            "received $2B from donors", stance="neutral", entity_names=["Jane Smith"]
        )
        _, sid2 = normalize_claim(
            "received $2 billion from donors", stance="neutral", entity_names=["Jane Smith"]
        )
        assert sid1 == sid2

    def test_percent_trailing_zero_collapse(self):
        """42% and 42.0% must collapse."""
        _, sid1 = normalize_claim(
            "won 42% of the vote", stance="neutral", entity_names=["Jane Smith"]
        )
        _, sid2 = normalize_claim(
            "won 42.0% of the vote", stance="neutral", entity_names=["Jane Smith"]
        )
        assert sid1 == sid2, "42% and 42.0% must produce the same semantic_id"

    def test_different_magnitudes_collapse(self):
        """$500K and $2M differ only in numeric value — same semantic_id (numerics excluded from hash)."""
        _, sid1 = normalize_claim(
            "raised $500K from donors", stance="neutral", entity_names=["Jane Smith"]
        )
        _, sid2 = normalize_claim(
            "raised $2M from donors", stance="neutral", entity_names=["Jane Smith"]
        )
        assert sid1 == sid2, "$500K and $2M must collapse — numerics are not in the hash"

    def test_magnitude_equivalence_in_integration(self, db):
        """Same amount in K vs comma form deduplicates within same source."""
        report = IngestionReport()
        c1 = get_or_create_claim(
            db,
            "raised $500K from donors",
            stance="neutral",
            confidence=0.8,
            source_id=SOURCE_A,
            entity_names=["Jane Smith"],
            report=report,
        )
        c2 = get_or_create_claim(
            db,
            "raised $500,000 from donors",
            stance="neutral",
            confidence=0.8,
            source_id=SOURCE_A,
            entity_names=["Jane Smith"],
            report=report,
        )
        assert c1.id == c2.id, "$500K and $500,000 must deduplicate to the same row"
        assert report.claims_created == 1
        assert report.claims_skipped == 1


# ── 6. Entity drift stability ─────────────────────────────────────────────────


class TestEntityDriftStability:
    """
    Title-prefix stripping: "Rep. Jane Smith" and "Jane Smith" must resolve to
    the same entity key.  DB entity_ids take precedence over name strings and
    are immune to any name variation.
    """

    def test_title_prefix_stripped_for_identity(self):
        """'Rep. Jane Smith' and 'Jane Smith' produce the same semantic_id."""
        _, sid_titled = normalize_claim(
            "opposed infrastructure bill",
            stance="oppose",
            entity_names=["Rep. Jane Smith"],
        )
        _, sid_plain = normalize_claim(
            "opposed infrastructure bill",
            stance="oppose",
            entity_names=["Jane Smith"],
        )
        assert sid_titled == sid_plain, (
            "'Rep. Jane Smith' and 'Jane Smith' must hash to the same entity key"
        )

    def test_senator_prefix_stripped(self):
        _, sid_titled = normalize_claim(
            "supported the climate bill",
            stance="support",
            entity_names=["Senator Rob Bresnahan"],
        )
        _, sid_plain = normalize_claim(
            "supported the climate bill",
            stance="support",
            entity_names=["Rob Bresnahan"],
        )
        assert sid_titled == sid_plain

    def test_honorable_prefix_stripped(self):
        _, sid_hon = normalize_claim(
            "voted against the bill",
            stance="oppose",
            entity_names=["The Honorable Mary Cognetti"],
        )
        _, sid_plain = normalize_claim(
            "voted against the bill",
            stance="oppose",
            entity_names=["Mary Cognetti"],
        )
        assert sid_hon == sid_plain


# ── 7. Issue slug canonicalization ────────────────────────────────────────────


class TestIssueSlugCanonicalization:
    """
    Issue slugs are accepted but excluded from the semantic_id hash.
    Any slug variation on otherwise-identical claims must collapse.
    """

    def test_health_and_healthcare_collapse(self):
        """Different issue slugs on the same claim produce the same semantic_id."""
        _, sid1 = normalize_claim(
            "supported the healthcare bill",
            stance="support",
            entity_names=["Jane Smith"],
            issue_slugs=["health"],
        )
        _, sid2 = normalize_claim(
            "supported the healthcare bill",
            stance="support",
            entity_names=["Jane Smith"],
            issue_slugs=["healthcare"],
        )
        assert sid1 == sid2

    def test_health_care_underscore_variant_collapses(self):
        _, sid1 = normalize_claim(
            "supported legislation",
            stance="support",
            entity_names=["Jane Smith"],
            issue_slugs=["health_care"],
        )
        _, sid2 = normalize_claim(
            "supported legislation",
            stance="support",
            entity_names=["Jane Smith"],
            issue_slugs=["healthcare"],
        )
        assert sid1 == sid2

    def test_climate_and_climate_change_collapse(self):
        _, sid1 = normalize_claim(
            "opposed climate legislation",
            stance="oppose",
            entity_names=["Jane Smith"],
            issue_slugs=["climate"],
        )
        _, sid2 = normalize_claim(
            "opposed climate legislation",
            stance="oppose",
            entity_names=["Jane Smith"],
            issue_slugs=["climate_change"],
        )
        assert sid1 == sid2

    def test_slug_canonicalization_in_integration(self, db):
        """
        Any issue slug variation on otherwise-identical claims within the same
        source must deduplicate to one row.
        """
        report = IngestionReport()
        c1 = get_or_create_claim(
            db,
            "supported the healthcare bill",
            stance="support",
            confidence=0.8,
            source_id=SOURCE_A,
            entity_names=["Jane Smith"],
            issue_slugs=["health"],
            report=report,
        )
        c2 = get_or_create_claim(
            db,
            "supported the healthcare bill",
            stance="support",
            confidence=0.8,
            source_id=SOURCE_A,
            entity_names=["Jane Smith"],
            issue_slugs=["healthcare"],
            report=report,
        )
        assert c1.id == c2.id
        assert report.claims_created == 1
        assert report.claims_skipped == 1

    def test_unknown_slug_passes_through_unchanged(self):
        """An unrecognized slug is not corrupted — still deterministic."""
        _, sid1 = normalize_claim(
            "supported legislation",
            stance="support",
            entity_names=["Jane Smith"],
            issue_slugs=["zoning_policy"],
        )
        _, sid2 = normalize_claim(
            "supported legislation",
            stance="support",
            entity_names=["Jane Smith"],
            issue_slugs=["zoning_policy"],
        )
        assert sid1 == sid2

    def test_no_slug_same_as_any_slug(self):
        """Omitting issue_slugs produces the same semantic_id as any slug."""
        _, sid_no_slug = normalize_claim(
            "supported legislation",
            stance="support",
            entity_names=["Jane Smith"],
        )
        _, sid_with_slug = normalize_claim(
            "supported legislation",
            stance="support",
            entity_names=["Jane Smith"],
            issue_slugs=["zoning_policy"],
        )
        assert sid_no_slug == sid_with_slug


# ── 8. Minimal semantic_id contract ───────────────────────────────────────────


class TestMinimalSemanticIdContract:
    """
    Proves the v3 semantic_id contract:
      hash(entity_canonical_name + stance + coarse_action_tokens)

    Numeric facts and issue slugs must NOT influence dedup identity.
    Entity name drift (titles) must NOT split.
    Verb synonym gaps that are NOT in the synonym table MUST still split.
    """

    def test_numeric_difference_does_not_split_semantic_id(self):
        """$500K and $2M — same entity/stance/action → same semantic_id."""
        _, sid1 = normalize_claim(
            "raised $500K from donors",
            stance="neutral",
            entity_names=["Jane Smith"],
        )
        _, sid2 = normalize_claim(
            "raised $2,000,000 from donors",
            stance="neutral",
            entity_names=["Jane Smith"],
        )
        assert sid1 == sid2, (
            "Numeric difference must not split semantic_id — "
            "numerics are excluded from the hash"
        )

    def test_issue_slug_variation_does_not_split_semantic_id(self):
        """Different issue slugs on the same claim → same semantic_id."""
        _, sid1 = normalize_claim(
            "opposed the bill",
            stance="oppose",
            entity_names=["Jane Smith"],
            issue_slugs=["healthcare"],
        )
        _, sid2 = normalize_claim(
            "opposed the bill",
            stance="oppose",
            entity_names=["Jane Smith"],
            issue_slugs=["climate"],
        )
        assert sid1 == sid2, (
            "Issue slug variation must not split semantic_id — "
            "issue slugs are excluded from the hash"
        )

    def test_no_issue_slug_same_as_any_slug(self):
        """Omitting issue_slugs is identical to providing any slug."""
        _, sid_no_slug = normalize_claim(
            "opposed the bill",
            stance="oppose",
            entity_names=["Jane Smith"],
        )
        _, sid_with_slug = normalize_claim(
            "opposed the bill",
            stance="oppose",
            entity_names=["Jane Smith"],
            issue_slugs=["healthcare"],
        )
        assert sid_no_slug == sid_with_slug

    def test_verb_synonym_gap_still_splits(self):
        """Verbs not in the synonym table produce different action tokens → different semantic_id."""
        _, sid_proposed = normalize_claim(
            "proposed a tax cut",
            stance="neutral",
            entity_names=["Jane Smith"],
        )
        _, sid_supported = normalize_claim(
            "supported a tax cut",
            stance="neutral",
            entity_names=["Jane Smith"],
        )
        assert sid_proposed != sid_supported, (
            "Verbs not in the synonym table must still differentiate claims"
        )

    def test_opposite_stances_always_split(self):
        """Support and oppose stances must always produce different semantic_ids."""
        _, sid_sup = normalize_claim(
            "supported the bill",
            stance="support",
            entity_names=["Jane Smith"],
        )
        _, sid_opp = normalize_claim(
            "opposed the bill",
            stance="oppose",
            entity_names=["Jane Smith"],
        )
        assert sid_sup != sid_opp
