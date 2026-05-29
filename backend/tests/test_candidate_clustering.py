"""
Tests for the HDBSCAN-based candidate_frame clusterer in
candidate_frame_promoter._build_clusters.

These tests REPLACE the earlier test_calibrate_similarity_threshold.py
which pinned a magic-number threshold. The clusterer no longer uses a
similarity threshold — it uses HDBSCAN with `min_cluster_size` as the
only knob, which is a domain choice (not a per-race calibration).

Why these tests use synthetic vectors
-------------------------------------
Real LLM embeddings vary across model versions, which would make tests
flake. Synthetic vectors with controlled geometry let us pin the
clusterer's BEHAVIOR — dense groups cluster, sparse points fall to
noise, edge cases handled.
"""
import math

import pytest

from app.services.candidate_frame_promoter import (
    MIN_CLUSTER_ROWS,
    MIN_DISTINCT_ARTICLES,
    MIN_DISTINCT_OUTLETS,
    _build_clusters,
)


# ─────────────────────────────────────────────────────────────────────────
# Constants pin: promotion-gate thresholds. These are product decisions
# (not similarity numbers) and must stay stable across deployments.
# ─────────────────────────────────────────────────────────────────────────

def test_promotion_gates_are_pinned():
    """If these change, downstream behavior changes for every race. Treat
    a change as a deliberate product decision."""
    assert MIN_CLUSTER_ROWS == 3
    assert MIN_DISTINCT_ARTICLES == 3
    assert MIN_DISTINCT_OUTLETS == 2


def test_no_more_similarity_threshold_constant():
    """We deliberately REMOVED the SIMILARITY_THRESHOLD constant when
    switching to HDBSCAN. A density-based clusterer doesn't need one —
    and adding one back would re-introduce the race-dependency problem
    that motivated the switch (Gemini calibrated to 0.85, OpenAI to
    0.65, next race would need its own number).

    If you find yourself wanting to re-introduce a similarity threshold,
    read the docstring on `_build_clusters` first."""
    from app.services import candidate_frame_promoter
    assert not hasattr(candidate_frame_promoter, "SIMILARITY_THRESHOLD"), (
        "Don't re-introduce SIMILARITY_THRESHOLD. See _build_clusters "
        "docstring for why density-based clustering replaces it."
    )


# ─────────────────────────────────────────────────────────────────────────
# Synthetic clustering behavior tests
# ─────────────────────────────────────────────────────────────────────────

def _group(center_angle: float, n: int, spread: float = 0.05) -> list[list[float]]:
    """Build n 2D vectors clustered around `center_angle` with controlled
    angular spread. Deterministic — same inputs always produce the same
    output (no randomness, so tests don't flake).

    `spread` is the half-width of the arc the n points span. 0.05 ≈ 3°.
    HDBSCAN needs SOME spread to identify density — perfectly-identical
    points degenerate to noise."""
    out = []
    for k in range(n):
        # Spread points evenly across the arc, deterministically.
        a = center_angle + spread * (2 * k / max(n - 1, 1) - 1)
        out.append([math.cos(a), math.sin(a)])
    return out


def test_distinct_groups_each_form_a_cluster():
    """Three distinct dense groups (sufficient inter-group separation)
    should each cluster together. This is the core "does HDBSCAN find
    the obvious clusters" test."""
    # 4 near angle 0, 4 near angle π/2, 4 near angle π — all far apart
    embeddings = (
        _group(0.0, 4)
        + _group(math.pi / 2, 4)
        + _group(math.pi, 4)
    )
    candidates = [object()] * 12
    clusters = _build_clusters(candidates, embeddings, min_cluster_size=3)

    # 3 non-singleton clusters expected
    multi = sorted([sorted(c) for c in clusters if len(c) >= 2])
    assert len(multi) == 3, (
        f"Expected 3 dense clusters, got {len(multi)}: {multi}"
    )
    # First 4 indices in one cluster, next 4 in another, last 4 in third
    assert multi[0] == [0, 1, 2, 3]
    assert multi[1] == [4, 5, 6, 7]
    assert multi[2] == [8, 9, 10, 11]


def test_isolated_point_does_not_merge_with_distant_cluster():
    """An item that's clearly far from any dense region must NOT get
    force-joined into a distant cluster. It should either become a noise
    singleton OR (if HDBSCAN can't classify on tiny data) at minimum NOT
    appear in the dense cluster.

    Less brittle than asserting exact cluster sizes — HDBSCAN's behavior
    on tiny synthetic datasets varies. What we really care about: in
    real-sized data, a clearly-isolated narrative doesn't get falsely
    promoted into a clearly-different cluster's group."""
    # Tight 6-point dense group near angle 0, plus 1 isolated point at π/2.
    # Bigger dense group than the previous test so HDBSCAN sees density.
    embeddings = _group(0.0, 6) + [[0.0, 1.0]]
    candidates = [object()] * 7
    clusters = _build_clusters(candidates, embeddings, min_cluster_size=3)

    # Find which cluster (if any) contains the isolated point at index 6
    isolated_cluster = next((c for c in clusters if 6 in c), None)
    assert isolated_cluster is not None, "Isolated point must be in SOME cluster"
    # The isolated point should NOT be lumped with the 6-item dense group.
    # I.e. it should be alone OR with at most a handful, not all 7 together.
    assert len(isolated_cluster) <= 2, (
        f"Isolated point got merged into dense cluster of size "
        f"{len(isolated_cluster)} — false-positive merge"
    )


def test_min_cluster_size_invariant_holds():
    """The min_cluster_size INVARIANT: every cluster (non-singleton) that
    HDBSCAN returns must contain at least min_cluster_size items.
    HDBSCAN's noise classification handles undersized groupings — they
    either get absorbed into bigger nearby clusters or become singletons.

    This is the property the production pipeline relies on (the gate
    logic in find_promotable_clusters also enforces MIN_CLUSTER_ROWS,
    so doubly safe). The test verifies the contract directly."""
    # Mix of structure: 8 dense + 4 background + 2 pair
    background = [
        [math.cos(a), math.sin(a)]
        for a in (0.7, 1.4, 2.1, 4.0)
    ]
    embeddings = _group(0.0, 8) + _group(math.pi, 2) + background
    candidates = [object()] * 14
    clusters = _build_clusters(candidates, embeddings, min_cluster_size=3)

    # Every multi-item cluster must respect min_cluster_size
    for c in clusters:
        if len(c) >= 2:  # non-singleton
            assert len(c) >= 3, (
                f"Cluster of size {len(c)} violates min_cluster_size=3: {c}. "
                f"HDBSCAN should have classified this as noise singletons."
            )


def test_none_embeddings_filtered_before_clustering():
    """Items with no embedding (failed provider, etc.) shouldn't appear
    in any cluster's indices — they're filtered out entirely."""
    embeddings = _group(0.0, 4) + [None, None]
    candidates = [object()] * 6
    clusters = _build_clusters(candidates, embeddings, min_cluster_size=3)

    all_indices = {i for c in clusters for i in c}
    assert all_indices == {0, 1, 2, 3}, (
        f"Indices 4 and 5 (None embeddings) shouldn't appear in any cluster. "
        f"Got: {sorted(all_indices)}"
    )


def test_below_min_cluster_size_returns_singletons():
    """If fewer items than min_cluster_size are provided, HDBSCAN refuses
    to cluster. We must still return ONE list per item so downstream
    code doesn't crash."""
    embeddings = _group(0.0, 2)  # only 2 items
    candidates = [object()] * 2
    clusters = _build_clusters(candidates, embeddings, min_cluster_size=3)

    # 2 singletons (gate will reject them later, but we shouldn't crash)
    assert len(clusters) == 2
    assert all(len(c) == 1 for c in clusters)


def test_empty_input_returns_empty():
    """No items in, no clusters out. No crash."""
    assert _build_clusters([], [], min_cluster_size=3) == []


def test_all_none_embeddings_returns_empty():
    """If every embedding is None (all failed to embed), we return empty.
    Don't synthesize fake clusters."""
    candidates = [object()] * 4
    embeddings = [None, None, None, None]
    assert _build_clusters(candidates, embeddings, min_cluster_size=3) == []


# ─────────────────────────────────────────────────────────────────────────
# Race-agnostic invariant — the meat of why we did this work.
# ─────────────────────────────────────────────────────────────────────────

def test_no_provider_dependent_parameters_in_signature():
    """`_build_clusters` must NOT take a similarity threshold parameter.
    Adding one would silently re-introduce the race-dependency problem."""
    import inspect
    sig = inspect.signature(_build_clusters)
    forbidden = {"similarity_threshold", "threshold", "cosine_threshold"}
    params = set(sig.parameters.keys())
    leaked = params & forbidden
    assert not leaked, (
        f"_build_clusters signature exposes provider-dependent parameter(s): {leaked}. "
        f"HDBSCAN doesn't need a similarity threshold — re-read the docstring."
    )


def test_min_cluster_size_is_the_only_clustering_knob():
    """The only behavioral parameter besides embeddings should be
    min_cluster_size (a product/recall decision). Everything else is
    determined by HDBSCAN's density model."""
    import inspect
    sig = inspect.signature(_build_clusters)
    expected_params = {"candidates", "embeddings", "min_cluster_size"}
    assert set(sig.parameters.keys()) == expected_params
