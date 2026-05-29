"""Unit tests for the cluster-merge backfill's domain-agnostic guards.

These pin the behavior of the number-mismatch guard and the normalized-title
similarity for cases that have historically caused false-positive merges
(template pages, weekly columns, district/date mismatches).

Tests are intentionally domain-agnostic — no campaign-specific names or
districts — so they hold for any campaign's data.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.scripts.merge_fragmented_clusters import (
    _numbers_in_title,
    _numbers_mismatch,
)
from app.services.story_clustering import (
    assign_story_cluster_v2,
    normalize_title,
    simhash64,
    title_similarity,
)


# ─────────────────────────────────────────────────────────────────────────
# Number-mismatch guard
# ─────────────────────────────────────────────────────────────────────────

class TestNumbersMismatch:
    """The guard blocks merges only when BOTH titles have unique digit
    tokens. Asymmetric digits (one side has none or all match) don't fire.
    Outlet-name suffixes are stripped so radio frequencies / channel IDs
    don't cause spurious blocks."""

    @pytest.mark.parametrize("t1,t2", [
        ("District 8 results", "District 3 results"),
        ("District 8 race 2026", "District 12 race 2026"),  # year shared, district differs
        ("May 18 weekly update", "May 4 weekly update"),
        ("Article 1", "Article 2"),
        ("Article 1 of 5", "Article 1 of 6"),
        ("$10M settlement", "$50M settlement"),
        ("5pm event", "8pm event"),
        ("Phone 555-1234", "Phone 555-5678"),
        ("House District 19 results", "House District 01 results"),
    ])
    def test_blocks_when_both_have_unique_digits(self, t1, t2):
        assert _numbers_mismatch(t1, t2) is True, (
            f"Expected block: {t1!r} vs {t2!r}"
        )

    @pytest.mark.parametrize("t1,t2", [
        ("Bill summary", "Bill summary (HR 3001)"),  # asymmetric add
        ("Story from 2026", "Story from 2026 update"),
        ("Candidate speaks", "Candidate speaks at rally"),  # no digits
        ("Speech | 95.7 FM", "Speech | 92.3 FM"),  # outlet suffix stripped
        ("Speech | 95.7 FM", "Speech | The Atlantic"),
        ("Story", "Story (2026 update)"),
        ("$50B health plan", "$50B health plan (updated)"),
    ])
    def test_allows_asymmetric_or_matching_digits(self, t1, t2):
        assert _numbers_mismatch(t1, t2) is False, (
            f"Expected NOT block: {t1!r} vs {t2!r}"
        )

    def test_extracts_digits_glued_to_letters(self):
        """`$10M` and `5pm` should yield {10} and {5} — boundary-free regex."""
        assert _numbers_in_title("$10M settlement") == {"10"}
        assert _numbers_in_title("5pm meeting") == {"5"}
        assert _numbers_in_title("Article2026") == {"2026"}

    def test_strips_outlet_suffix_before_extraction(self):
        """Outlet names following ' | ', ' - ', ' : ' delimiters are dropped."""
        assert _numbers_in_title("Trump speech | 95.7 FM") == set()
        assert _numbers_in_title("Story headline - Times Tribune") == set()
        assert _numbers_in_title("News - Local 9 Channel") == set()


# ─────────────────────────────────────────────────────────────────────────
# Title-similarity behavior under the new normalize_title
# ─────────────────────────────────────────────────────────────────────────

class TestNormalizeTitle:
    """normalize_title now keeps digit tokens (district numbers, dates,
    counts) instead of dropping them as part of the ≤2-char filter.
    Political stopwords like 'primary' and 'election' are also no longer
    filtered — they're distinguishing in news titles."""

    def test_keeps_short_digit_tokens(self):
        """Digit tokens are always retained, even single digits."""
        norm = normalize_title("Article 1 of 5 ways to vote")
        tokens = norm.split()
        assert "1" in tokens
        assert "5" in tokens

    def test_keeps_year_tokens(self):
        norm = normalize_title("Election update for 2026")
        assert "2026" in norm.split()

    def test_keeps_political_distinguishers(self):
        """'primary' and 'election' previously dropped as stopwords —
        in political content they are distinguishing."""
        norm = normalize_title("Primary Election Results")
        tokens = norm.split()
        assert "primary" in tokens
        assert "election" in tokens
        assert "results" in tokens

    def test_drops_short_non_digit_tokens(self):
        """Short fillers ('to', 'in', 'on', 'be') still dropped."""
        norm = normalize_title("How to vote in the primary")
        tokens = norm.split()
        assert "to" not in tokens
        assert "in" not in tokens
        assert "the" not in tokens

    def test_strips_outlet_suffix(self):
        """Trailing ' | Outlet', ' - Outlet', ' : Outlet' are removed."""
        norm = normalize_title("Bresnahan votes against bill - Times Tribune")
        assert "tribune" not in norm.split()
        assert "times" not in norm.split()


class TestTitleSimilarity:
    """Cross-check: the new normalization actually changes Jaccard scores
    for the historically-problematic cases. These pin the desired behavior."""

    def test_district_number_change_lowers_jaccard(self):
        """Two titles differing only by district number must NOT score 1.0."""
        sim = title_similarity(
            "Pennsylvania House District 8 Primary Election Results",
            "Pennsylvania House District 3 Primary Election Results",
        )
        # Before fix: 1.0 (silent collapse). After fix: ~0.75.
        assert sim < 0.85, f"District mismatch should not pass 0.85 threshold; got {sim}"

    def test_state_change_in_template_lowers_jaccard(self):
        """Template pages with same body and only state/district differing
        must not score above rule-3 threshold."""
        sim = title_similarity(
            "2026 Election US House Florida District 19 | FEC",
            "2026 Election US House Texas District 01 | FEC",
        )
        # Before fix: 0.71 (above 0.65). After fix: <0.65.
        assert sim < 0.65, f"State/district template mismatch; got {sim}"

    def test_identical_titles_still_score_1(self):
        sim = title_similarity(
            "Representative Advances Historic Bridge",
            "Representative Advances Historic Bridge",
        )
        assert sim == 1.0

    def test_legitimate_wire_pickup_still_matches(self):
        """Wire pickup with minor headline rewording should still pass
        rule 3's 0.85 threshold."""
        sim = title_similarity(
            "President proposes $50B for rural health care",
            "President proposes $50B for rural health care — update",
        )
        assert sim >= 0.85, f"Legit wire pickup must not be blocked; got {sim}"

    def test_different_dates_lower_jaccard(self):
        """Weekly column with different week labels must not collapse."""
        sim = title_similarity(
            "Weekly Roundup — Week of May 18, 2026",
            "Weekly Roundup — Week of May 4, 2026",
        )
        # Both have '18' vs '4' now preserved as digits, so Jaccard drops.
        assert sim < 0.85, f"Different week labels must not pass; got {sim}"


# ─────────────────────────────────────────────────────────────────────────
# assign_story_cluster_v2 idempotency — calling on an already-clustered
# item must NOT mutate the cluster's article_count or other aggregates.
# Without this short-circuit, reanalysis flows would double-count members.
# ─────────────────────────────────────────────────────────────────────────

class TestV2Idempotency:
    """Reanalysis calls v2 on items that already have story_cluster_id set.
    The function must short-circuit instead of re-running attach logic and
    incrementing article_count."""

    def test_already_clustered_item_returns_existing_cluster_without_mutation(self, tmp_path):
        """Item with valid story_cluster_id: return its cluster unchanged,
        with is_new=False and retrigger=None."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.models import Base, SourceItem, StoryCluster

        engine = create_engine(f"sqlite:///{tmp_path}/test.db")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()

        now = datetime.utcnow()
        cluster = StoryCluster(
            id="cluster-1",
            seed_source_item_id=1,
            representative_source_item_id=1,
            title_representative="A real headline",
            simhash_64="0123456789abcdef",
            first_seen_at=now, last_seen_at=now,
            article_count=5, outlet_count=3,
            source_diversity_score=0.0,
        )
        item = SourceItem(
            id=1,
            title="A real headline",
            source_type="news",
            story_cluster_id="cluster-1",
            published_at=now,
        )
        db.add_all([cluster, item])
        db.commit()

        before_count = cluster.article_count
        before_outlets = cluster.outlet_count

        result_cluster, is_new, retrigger = assign_story_cluster_v2(db, item)

        assert result_cluster.id == "cluster-1"
        assert is_new is False
        assert retrigger is None
        # The critical invariant: no mutation of aggregates.
        assert cluster.article_count == before_count
        assert cluster.outlet_count == before_outlets

    def test_simhash_sentinel_value_pins_algorithm(self):
        """Golden test for the simhash algorithm.

        Every story_clusters.simhash_64 in the live DB was produced by the
        current `simhash64` implementation against a body normalized by
        `_tokens_for_hash` and shingled at k=4. The cluster-merge backfill
        compares freshly-computed item hashes against those stored hashes —
        if anyone changes ANY of these (the function, the tokenizer, the
        hash-side stopword set, the shingle size, the blake2b digest, the
        bit-folding), every stored hash silently becomes incompatible and
        dedup quality collapses.

        This sentinel pins the output for a known input. If it fails:
          1. You probably changed `_HASH_STOPWORDS`, `_tokens_for_hash`,
             `_shingles`, or `simhash64`.
          2. EITHER revert the change, OR plan a recompute of every
             story_clusters.simhash_64 row + a coordinated re-run of any
             merge backfill that uses stored hashes.
          3. After confirming the change is intentional, update the
             expected value here to the new output.
        """
        SENTINEL_INPUT = (
            "The candidate announced a major policy proposal at a campaign event "
            "in the district on Tuesday. The election race has drawn national attention "
            "with both candidates making their case to voters ahead of the primary ballot. "
            "Local community members expressed support for the initiative during the town "
            "hall meeting. According to campaign officials, the plan would be implemented "
            "within the first six months of taking office if elected in the general election."
        )
        EXPECTED = 0x2de5b5e1c0dbc873
        actual = simhash64(SENTINEL_INPUT)
        assert actual == EXPECTED, (
            f"simhash64 output changed: expected {EXPECTED:016x}, got {actual:016x}. "
            f"This breaks stored story_clusters.simhash_64 backward-compat. "
            f"Read the docstring before changing the expected value."
        )

    def test_number_mismatch_blocks_live_clustering(self, tmp_path):
        """The number-mismatch guard now applies to live ingestion too.
        Two items with identical-by-tokens titles but distinguishing digits
        ("District 8" vs "District 12") must NOT cluster together via the
        live `assign_story_cluster_v2`, even though their token-Jaccard
        would otherwise pass rule 2 / rule 3. URL match still wins above
        the guard (same URL = same article regardless of title digits)."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.models import Base, SourceItem, StoryCluster

        engine = create_engine(f"sqlite:///{tmp_path}/test.db")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()

        now = datetime.utcnow()
        # First item: District 8 results
        item1 = SourceItem(
            id=1,
            title="Pennsylvania House District 8 Primary Election Results",
            raw_text="Results from the District 8 race " * 10,
            source_type="news",
            source_url="https://example.com/d8",
            published_at=now,
        )
        db.add(item1)
        db.commit()
        cluster1, is_new1, _ = assign_story_cluster_v2(db, item1)
        assert is_new1 is True
        db.commit()

        # Second item: District 12 results — same title structure, different digits
        item2 = SourceItem(
            id=2,
            title="Pennsylvania House District 12 Primary Election Results",
            raw_text="Results from the District 12 race " * 10,
            source_type="news",
            source_url="https://example.com/d12",  # different URL
            published_at=now,
        )
        db.add(item2)
        db.commit()
        cluster2, is_new2, _ = assign_story_cluster_v2(db, item2)
        # Must be a separate cluster — number-mismatch guard blocks merge
        assert is_new2 is True, "District 8 and District 12 were merged into one cluster"
        assert cluster1.id != cluster2.id

    def test_orphan_cluster_id_falls_through_to_assignment(self, tmp_path):
        """If item.story_cluster_id points to a non-existent cluster (e.g.
        the cluster was deleted), v2 should treat it as if unassigned and
        find or create a new cluster — not return None or crash."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.models import Base, SourceItem, StoryCluster

        engine = create_engine(f"sqlite:///{tmp_path}/test.db")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()

        now = datetime.utcnow()
        item = SourceItem(
            id=42,
            title="A real headline goes here",
            raw_text="Body text content " * 20,
            source_type="news",
            story_cluster_id="cluster-that-was-deleted",
            published_at=now,
        )
        db.add(item)
        db.commit()

        cluster, is_new, _ = assign_story_cluster_v2(db, item)
        assert cluster is not None
        assert is_new is True  # created fresh since no other cluster exists
        assert item.story_cluster_id == cluster.id  # reassigned
