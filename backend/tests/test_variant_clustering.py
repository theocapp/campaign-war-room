"""Tests for the agglomerative variant clusterer.

The clusterer was changed from HDBSCAN to agglomerative complete-linkage
on 2026-05-27 after a SimHash-calibrated threshold sweep showed HDBSCAN
chained distinct claims through shared-vocabulary density regions.
See app/scripts/calibrate_variant_threshold.py for the calibration.

These tests use synthetic 4-d embeddings to pin the behavior without
needing live LLM/embedding calls.
"""
from app.services.frame_variants import (
    _agglomerative_cluster,
    CLUSTER_DISTANCE_THRESHOLD,
    CLUSTER_LINKAGE,
)


def _item(nfm_id: int, embedding: list[float]) -> dict:
    return {"nfm_id": nfm_id, "embedding": embedding}


def test_near_duplicates_cluster_together():
    # Three near-duplicate quotes (≈ identical embeddings) should land in
    # one cluster.
    items = [
        _item(1, [1.0, 0.0, 0.0, 0.0]),
        _item(2, [0.99, 0.05, 0.0, 0.0]),
        _item(3, [0.98, 0.0, 0.05, 0.0]),
    ]
    clusters = _agglomerative_cluster(items)
    assert len(clusters) == 1
    assert len(clusters[0]) == 3


def test_orthogonal_quotes_stay_separate():
    # Two unrelated quotes (perpendicular vectors) must be in different clusters.
    items = [
        _item(1, [1.0, 0.0, 0.0, 0.0]),
        _item(2, [0.0, 1.0, 0.0, 0.0]),
    ]
    clusters = _agglomerative_cluster(items)
    assert len(clusters) == 2


def test_three_distinct_claims_three_clusters():
    # Three groups of near-duplicates that are orthogonal to each other.
    items = [
        _item(1, [1.0, 0.0, 0.0, 0.0]),
        _item(2, [0.99, 0.05, 0.0, 0.0]),
        _item(3, [0.0, 1.0, 0.0, 0.0]),
        _item(4, [0.05, 0.99, 0.0, 0.0]),
        _item(5, [0.0, 0.0, 1.0, 0.0]),
        _item(6, [0.0, 0.0, 0.99, 0.05]),
    ]
    clusters = _agglomerative_cluster(items)
    assert len(clusters) == 3
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [2, 2, 2]


def test_complete_linkage_prevents_chaining():
    # The chaining failure mode: a series of pairs where consecutive items
    # are close but the endpoints are far apart. Single-linkage would merge
    # them all; complete-linkage must NOT.
    items = [
        _item(1, [1.0, 0.0, 0.0, 0.0]),
        _item(2, [0.85, 0.4, 0.0, 0.0]),
        _item(3, [0.5, 0.85, 0.0, 0.0]),
        _item(4, [0.0, 1.0, 0.0, 0.0]),
    ]
    clusters = _agglomerative_cluster(items)
    # Endpoints (1 and 4) are orthogonal — cosine distance 1.0. Complete
    # linkage refuses to merge clusters where any pair exceeds the threshold.
    # So 1 and 4 must end up in different clusters.
    nfm_to_cluster = {}
    for ci, c in enumerate(clusters):
        for m in c:
            nfm_to_cluster[m["nfm_id"]] = ci
    assert nfm_to_cluster[1] != nfm_to_cluster[4], \
        "complete linkage should prevent chaining between orthogonal endpoints"


def test_empty_input():
    assert _agglomerative_cluster([]) == []


def test_single_item():
    items = [_item(1, [1.0, 0.0, 0.0, 0.0])]
    clusters = _agglomerative_cluster(items)
    assert len(clusters) == 1
    assert len(clusters[0]) == 1


def test_items_missing_embedding_are_dropped():
    items = [
        _item(1, [1.0, 0.0, 0.0, 0.0]),
        _item(2, [0.99, 0.05, 0.0, 0.0]),
        {"nfm_id": 3, "embedding": None},
    ]
    clusters = _agglomerative_cluster(items)
    # Only the 2 items with embeddings get clustered.
    all_ids = {m["nfm_id"] for c in clusters for m in c}
    assert all_ids == {1, 2}


def test_calibrated_constants_are_in_expected_range():
    # If someone tunes these, they should know they're touching calibrated values.
    assert 0.10 <= CLUSTER_DISTANCE_THRESHOLD <= 0.60
    assert CLUSTER_LINKAGE in {"complete", "average", "single", "ward"}
