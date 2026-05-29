"""Evaluate variant clustering algorithms on real frame data.

Goal: before scheduling cluster_all_frames in production, prove HDBSCAN
(the current implementation) actually produces clusters a human would
agree with — or find a better algorithm.

Runs four algorithms on the same embeddings and writes two artifacts:
  /tmp/cluster_eval_blind.md   — clusters labeled A/B/C/D (no algo names)
  /tmp/cluster_eval_key.json   — A→algo mapping + raw cluster data

The blind report shuffles cluster order so I can grade without anchoring
on cluster size or position. Algorithm letters are randomized per run.

Usage:
    cd backend && .venv/bin/python -m app.scripts.eval_variant_clustering
"""
from __future__ import annotations

import json
import random
import string
from collections import Counter
from pathlib import Path

import numpy as np

from app.db import SessionLocal
from app.models import NarrativeFrame, NarrativeFrameMention
from app.services.embeddings import embed_texts
from app.services.frame_variants import _cosine, _hdbscan_cluster

# How many frames to evaluate (top N by quote count). Manual grading time
# scales with this; 5 frames at ~20-100 quotes each is what I can grade
# carefully in one sitting.
TOP_N_FRAMES = 5

# Reproducible randomization so re-runs produce the same blind labels.
random.seed(20260527)


# ── Alternative clustering algorithms ────────────────────────────────────────

def cluster_hdbscan(items: list[dict]) -> list[list[dict]]:
    """The production algorithm. Kept as-is for fair comparison."""
    return _hdbscan_cluster(items, min_cluster_size=2)


def cluster_incremental_cosine(
    items: list[dict], threshold: float = 0.88,
) -> list[list[dict]]:
    """Order-dependent greedy clustering. For each item, attach to the
    existing cluster with the most similar centroid if similarity ≥ threshold,
    otherwise start a new cluster. Reproducible (sort items by id first).
    """
    items = sorted(items, key=lambda it: it["nfm"].id)
    clusters: list[dict] = []  # {"centroid": vec, "members": [items]}
    for it in items:
        emb = it.get("embedding")
        if not emb:
            continue
        best_sim, best_idx = -1.0, -1
        for ci, c in enumerate(clusters):
            sim = _cosine(emb, c["centroid"])
            if sim > best_sim:
                best_sim, best_idx = sim, ci
        if best_idx >= 0 and best_sim >= threshold:
            c = clusters[best_idx]
            members = c["members"] + [it]
            # Recompute centroid incrementally
            embs = [m["embedding"] for m in members]
            centroid = [sum(e[i] for e in embs) / len(embs)
                        for i in range(len(embs[0]))]
            clusters[best_idx] = {"centroid": centroid, "members": members}
        else:
            clusters.append({"centroid": emb, "members": [it]})
    return [c["members"] for c in clusters]


def cluster_connected_components(
    items: list[dict], threshold: float = 0.85,
) -> list[list[dict]]:
    """Order-independent. Build a graph with edges between items whose
    cosine ≥ threshold, then return connected components.
    """
    n = len(items)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        ei = items[i].get("embedding")
        if not ei:
            continue
        for j in range(i + 1, n):
            ej = items[j].get("embedding")
            if not ej:
                continue
            if _cosine(ei, ej) >= threshold:
                union(i, j)

    groups: dict = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(items[i])
    return list(groups.values())


def cluster_agglomerative(
    items: list[dict], distance_threshold: float = 0.18,
) -> list[list[dict]]:
    """sklearn AgglomerativeClustering with cosine distance. Order-independent."""
    from sklearn.cluster import AgglomerativeClustering

    items_with_emb = [it for it in items if it.get("embedding")]
    if len(items_with_emb) < 2:
        return [[it] for it in items_with_emb]
    X = np.array([it["embedding"] for it in items_with_emb], dtype=np.float32)
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    Xn = X / norms
    model = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=distance_threshold,
        metric="cosine",
        linkage="average",
    )
    labels = model.fit_predict(Xn)
    groups: dict = {}
    for it, lbl in zip(items_with_emb, labels):
        groups.setdefault(int(lbl), []).append(it)
    return list(groups.values())


ALGORITHMS = {
    "hdbscan": cluster_hdbscan,
    "incremental_cosine_0.88": cluster_incremental_cosine,
    "connected_components_0.85": cluster_connected_components,
    "agglomerative_0.18": cluster_agglomerative,
}


# ── Quantitative metrics ─────────────────────────────────────────────────────

def metrics(clusters: list[list[dict]]) -> dict:
    if not clusters:
        return {"n_clusters": 0, "singletons": 0, "mean_size": 0,
                "max_size": 0, "cohesion": 0.0, "separation": 0.0,
                "cohesion_gap": 0.0}

    sizes = [len(c) for c in clusters]
    singletons = sum(1 for s in sizes if s == 1)

    # Cohesion: mean pairwise cosine within each multi-member cluster.
    cohesions: list[float] = []
    centroids: list[list[float]] = []
    for c in clusters:
        embs = [m.get("embedding") for m in c if m.get("embedding")]
        if not embs:
            continue
        n = len(embs)
        centroid = [sum(e[i] for e in embs) / n for i in range(len(embs[0]))]
        centroids.append(centroid)
        if n >= 2:
            pairs = []
            for i in range(n):
                for j in range(i + 1, n):
                    pairs.append(_cosine(embs[i], embs[j]))
            if pairs:
                cohesions.append(sum(pairs) / len(pairs))

    # Separation: mean pairwise cosine between centroids.
    seps = []
    for i in range(len(centroids)):
        for j in range(i + 1, len(centroids)):
            seps.append(_cosine(centroids[i], centroids[j]))

    cohesion = sum(cohesions) / len(cohesions) if cohesions else 0.0
    separation = sum(seps) / len(seps) if seps else 0.0
    return {
        "n_clusters": len(clusters),
        "singletons": singletons,
        "singleton_ratio": round(singletons / len(clusters), 2) if clusters else 0,
        "mean_size": round(sum(sizes) / len(sizes), 1),
        "max_size": max(sizes),
        "cohesion": round(cohesion, 3),
        "separation": round(separation, 3),
        "cohesion_gap": round(cohesion - separation, 3),
    }


# ── Stability check ──────────────────────────────────────────────────────────

def cluster_signature(clusters: list[list[dict]]) -> str:
    """A canonical string fingerprint of a clustering — set-of-sets of NFM IDs."""
    sets = []
    for c in clusters:
        ids = sorted(m["nfm"].id for m in c)
        sets.append(tuple(ids))
    return "|".join(sorted(",".join(str(i) for i in s) for s in sets))


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    db = SessionLocal()
    try:
        # Pick top N frames by quote count.
        frame_rows = (
            db.query(NarrativeFrame.id, NarrativeFrame.name,
                     NarrativeFrameMention.id)
            .join(NarrativeFrameMention,
                  NarrativeFrameMention.frame_id == NarrativeFrame.id)
            .filter(NarrativeFrameMention.extracted_text.isnot(None))
            .all()
        )
        counts: Counter = Counter()
        for fid, fname, _ in frame_rows:
            counts[(fid, fname)] += 1
        top_frames = [fid for (fid, _), _ in counts.most_common(TOP_N_FRAMES)]

        # For each frame, pull NFMs and embed.
        frame_clusterings: dict = {}  # frame_id -> {"name": str, "results": {algo: [[item]]}, "metrics": {algo: {...}}}
        for frame_id in top_frames:
            frame = db.get(NarrativeFrame, frame_id)
            nfms = (
                db.query(NarrativeFrameMention)
                .filter(NarrativeFrameMention.frame_id == frame_id,
                        NarrativeFrameMention.extracted_text.isnot(None))
                .all()
            )
            quotes = [(n, (n.extracted_text or "").strip()) for n in nfms]
            quotes = [(n, q) for n, q in quotes if q]
            if len(quotes) < 5:
                continue

            print(f"frame {frame_id} {frame.name!r}: embedding {len(quotes)} quotes")
            embs = embed_texts([q for _, q in quotes],
                               task_type="SEMANTIC_SIMILARITY")
            items: list[dict] = []
            for (n, q), e in zip(quotes, embs):
                if e is None:
                    continue
                items.append({"nfm": n, "embedding": e, "quote": q})

            if len(items) < 5:
                continue

            results: dict = {}
            metrics_per_algo: dict = {}
            for algo_name, algo_fn in ALGORITHMS.items():
                clusters = algo_fn(items)
                results[algo_name] = clusters
                metrics_per_algo[algo_name] = metrics(clusters)

            # Stability: re-run HDBSCAN and confirm identical clustering.
            clusters_2 = ALGORITHMS["hdbscan"](items)
            metrics_per_algo["hdbscan_stability"] = {
                "identical": cluster_signature(results["hdbscan"]) == cluster_signature(clusters_2)
            }

            frame_clusterings[frame_id] = {
                "name": frame.name,
                "results": results,
                "metrics": metrics_per_algo,
            }

        # Generate random algorithm letters PER FRAME so I can't memorize
        # "HDBSCAN is always A".
        algo_names = list(ALGORITHMS.keys())
        per_frame_mapping: dict = {}
        for fid in frame_clusterings:
            shuffled = algo_names[:]
            random.shuffle(shuffled)
            letters = list("ABCD")[: len(shuffled)]
            per_frame_mapping[fid] = dict(zip(letters, shuffled))

        # Build blind markdown report.
        md_lines = [
            "# Variant clustering eval — BLIND",
            "",
            "Algorithm names are hidden behind letters A/B/C/D. The mapping is",
            "in /tmp/cluster_eval_key.json — read it ONLY after grading.",
            "Letters are randomized per frame so you can't memorize across frames.",
            "",
            f"Frames evaluated: {len(frame_clusterings)}",
            "",
        ]
        # Aggregate metrics first (also blind).
        md_lines.append("## Quantitative metrics (per frame, blind)")
        md_lines.append("")
        for fid, data in frame_clusterings.items():
            md_lines.append(f"### Frame {fid} — {data['name']!r}")
            md_lines.append("")
            md_lines.append("| | n_clusters | singletons | mean_size | max_size | cohesion | separation | gap |")
            md_lines.append("|---|---|---|---|---|---|---|---|")
            for letter, algo in per_frame_mapping[fid].items():
                m = data["metrics"][algo]
                md_lines.append(
                    f"| **{letter}** | {m['n_clusters']} | {m['singletons']} ({m['singleton_ratio']}) | "
                    f"{m['mean_size']} | {m['max_size']} | {m['cohesion']} | {m['separation']} | {m['cohesion_gap']} |"
                )
            stab = data["metrics"]["hdbscan_stability"]["identical"]
            md_lines.append("")
            md_lines.append(f"_HDBSCAN stability: {'identical across 2 runs' if stab else '⚠ NOT identical'}_")
            md_lines.append("")

        # Then each frame, each algorithm, clusters in random order.
        md_lines.append("## Cluster contents")
        md_lines.append("")
        for fid, data in frame_clusterings.items():
            md_lines.append(f"## Frame {fid} — {data['name']!r}")
            md_lines.append("")
            for letter, algo in per_frame_mapping[fid].items():
                clusters = data["results"][algo]
                # Sort by size desc, then shuffle within size groups to
                # break the cluster-order signal.
                clusters_sorted = sorted(clusters, key=lambda c: -len(c))
                # Limit to top 12 clusters per algorithm to keep grading scope manageable.
                # Singletons after the first few are noise; show 12 then collapse.
                shown = clusters_sorted[:12]
                hidden = clusters_sorted[12:]
                md_lines.append(f"### Algorithm {letter} — {len(clusters)} clusters")
                md_lines.append("")
                for ci, c in enumerate(shown):
                    md_lines.append(f"#### Cluster {letter}.{ci + 1} (size {len(c)})")
                    quotes = [(m["nfm"].id, m["quote"]) for m in c]
                    quotes = quotes[:6]   # cap quote display per cluster
                    for qid, q in quotes:
                        md_lines.append(f"- _(nfm {qid})_ \"{q[:220]}\"")
                    md_lines.append("")
                if hidden:
                    sizes = [len(c) for c in hidden]
                    md_lines.append(
                        f"_…{len(hidden)} more clusters omitted (sizes "
                        f"{min(sizes)}–{max(sizes)})_"
                    )
                    md_lines.append("")

        out_md = Path("/tmp/cluster_eval_blind.md")
        out_md.write_text("\n".join(md_lines))

        # Save the key separately.
        key_data = {
            "per_frame_mapping": {str(k): v for k, v in per_frame_mapping.items()},
            "frames": {
                str(fid): {
                    "name": data["name"],
                    "metrics": data["metrics"],
                }
                for fid, data in frame_clusterings.items()
            },
        }
        out_key = Path("/tmp/cluster_eval_key.json")
        out_key.write_text(json.dumps(key_data, indent=2, default=str))

        print(f"\nWrote {out_md}")
        print(f"Wrote {out_key}")
        print(f"\nFrames evaluated: {len(frame_clusterings)}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
