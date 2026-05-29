"""Verify the calibrated threshold by clustering at it and measuring quality.

For each candidate threshold (balanced 0.36, tight 0.24, plus a few others),
run agglomerative clustering on the same 5 frames I graded blindly. For each
cluster:
  • count fraction of wire-sync pairs preserved (intra-cluster same story_cluster_id)
  • count cross-frame contamination (should always be zero — clustering is per-frame)
  • compute a "purity" proxy: of all same-story pairs within a frame, what
    fraction land in the same cluster?

This lets us pick the threshold whose downstream clusters are cleanest.

Usage:
    cd backend && .venv/bin/python -m app.scripts.verify_calibrated_threshold
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Optional

import numpy as np

from app.db import SessionLocal
from app.models import NarrativeFrame, NarrativeFrameMention, SourceItem
from app.services.embeddings import embed_texts
from app.services.frame_variants import _cosine


# Frames we already evaluated blindly — same ones for continuity.
TARGET_FRAMES = [1, 4, 3, 35, 60]

# Candidate distance thresholds to verify. dist = 1 - sim.
# Broader grid: linkage × threshold combinations to score against both
# purity AND cluster-shape sanity.
CANDIDATES = [
    (f"avg_{t:.2f}",      "average",  t) for t in [0.20, 0.24, 0.28, 0.32, 0.36, 0.40]
] + [
    (f"single_{t:.2f}",   "single",   t) for t in [0.16, 0.20, 0.24, 0.28, 0.30, 0.32]
] + [
    (f"complete_{t:.2f}", "complete", t) for t in [0.24, 0.30, 0.36, 0.42, 0.48]
]


def agglom(items: list[dict], dist_thresh: float,
           linkage: str = "average") -> list[list[dict]]:
    from sklearn.cluster import AgglomerativeClustering
    usable = [it for it in items if it.get("embedding")]
    if len(usable) < 2:
        return [[it] for it in usable]
    X = np.array([it["embedding"] for it in usable], dtype=np.float32)
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    Xn = X / norms
    model = AgglomerativeClustering(
        n_clusters=None, distance_threshold=dist_thresh,
        metric="cosine", linkage=linkage,
    )
    labels = model.fit_predict(Xn)
    groups: dict = {}
    for it, lbl in zip(usable, labels):
        groups.setdefault(int(lbl), []).append(it)
    return list(groups.values())


def evaluate(clusters: list[list[dict]]) -> dict:
    """Per-clustering metrics. The key one is wire_sync_purity: of all pairs
    of NFMs that share a story_cluster_id, what fraction land in the same
    cluster? That's our objective measure of recall on the verifiable positive
    class.

    Also report:
      • cluster_count
      • singleton_count
      • mean_cluster_size (excluding singletons)
      • max_cluster_size
      • intra_cluster_story_purity: avg fraction of pairs within a cluster
        that share a story_cluster_id (informational — high purity = many wire-syncs)
    """
    # Map NFM → cluster_idx
    nfm_to_cluster: dict = {}
    for ci, c in enumerate(clusters):
        for m in c:
            nfm_to_cluster[m["nfm_id"]] = ci

    # Group NFMs by story_cluster_id (within this frame).
    by_story: dict = defaultdict(list)
    for c in clusters:
        for m in c:
            if m.get("story_cluster_id"):
                by_story[m["story_cluster_id"]].append(m["nfm_id"])

    # wire_sync_purity: of all same-story pairs, how many land in same cluster?
    same_story_pairs = 0
    same_cluster_pairs = 0
    for story_id, members in by_story.items():
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                same_story_pairs += 1
                if nfm_to_cluster[members[i]] == nfm_to_cluster[members[j]]:
                    same_cluster_pairs += 1
    wire_sync_purity = same_cluster_pairs / same_story_pairs if same_story_pairs else None

    sizes = [len(c) for c in clusters]
    singletons = sum(1 for s in sizes if s == 1)
    multi = [s for s in sizes if s > 1]
    return {
        "cluster_count": len(clusters),
        "singleton_count": singletons,
        "mean_multi_cluster_size": round(sum(multi) / len(multi), 2) if multi else 0,
        "max_cluster_size": max(sizes) if sizes else 0,
        "wire_sync_pairs": same_story_pairs,
        "wire_sync_recovered": same_cluster_pairs,
        "wire_sync_purity": round(wire_sync_purity, 4) if wire_sync_purity is not None else None,
    }


def main() -> None:
    db = SessionLocal()
    try:
        # Load NFMs for target frames
        by_frame: dict = defaultdict(list)
        rows = (
            db.query(NarrativeFrameMention.id,
                     NarrativeFrameMention.frame_id,
                     NarrativeFrameMention.extracted_text,
                     SourceItem.story_cluster_id)
            .join(SourceItem, SourceItem.id == NarrativeFrameMention.source_item_id)
            .filter(NarrativeFrameMention.frame_id.in_(TARGET_FRAMES))
            .filter(NarrativeFrameMention.extracted_text.isnot(None))
            .all()
        )
        for nfm_id, fid, text, story in rows:
            t = (text or "").strip()
            if not t:
                continue
            by_frame[fid].append({"nfm_id": nfm_id, "quote": t, "story_cluster_id": story})

        # Embed all
        all_quotes, refs = [], []
        for items in by_frame.values():
            for it in items:
                refs.append(it)
                all_quotes.append(it["quote"])
        print(f"embedding {len(all_quotes)} quotes...")
        embs = embed_texts(all_quotes, task_type="SEMANTIC_SIMILARITY")
        for it, e in zip(refs, embs):
            it["embedding"] = e

        # Cluster at each candidate threshold; report per-frame metrics.
        report: dict = {}
        print("\n=== Per-frame cluster metrics ===\n")
        for fid in TARGET_FRAMES:
            frame = db.get(NarrativeFrame, fid)
            print(f"## Frame {fid} — {frame.name!r}  ({len(by_frame[fid])} quotes)")
            report[fid] = {"name": frame.name, "by_threshold": {}}
            print(f"{'config':>16}  {'#clusters':>10}  {'#single':>8}  {'mean_size':>10}  {'max_size':>10}  {'wire_sync_purity':>18}")
            for label, linkage, dist in CANDIDATES:
                clusters = agglom(by_frame[fid], dist, linkage=linkage)
                m = evaluate(clusters)
                report[fid]["by_threshold"][label] = m
                purity_str = f"{m['wire_sync_purity']:.3f}" if m['wire_sync_purity'] is not None else "n/a"
                print(f"{label:>16}  {m['cluster_count']:>10}  {m['singleton_count']:>8}  "
                      f"{m['mean_multi_cluster_size']:>10}  {m['max_cluster_size']:>10}  "
                      f"{purity_str:>18}")
            print()

        # Aggregate scoring: purity × cluster-shape sanity.
        #
        # A good config has:
        #   • High wire-sync purity (most same-story pairs co-cluster)
        #   • Largest cluster ≤ 30% of frame size (no mega-clusters / chaining)
        #   • Mean multi-cluster size in [2, 12] — useful chart granularity
        print("\n=== Aggregate scoring (across all 5 frames) ===")
        print(f"{'config':>16}  {'purity':>8}  {'max_share':>10}  {'mean_size':>10}  {'penalty':>8}  {'score':>8}")
        scoreboard = []
        for label, _, _ in CANDIDATES:
            total_pairs = sum(report[fid]["by_threshold"][label]["wire_sync_pairs"]
                              for fid in TARGET_FRAMES)
            total_recovered = sum(report[fid]["by_threshold"][label]["wire_sync_recovered"]
                                  for fid in TARGET_FRAMES)
            purity = total_recovered / total_pairs if total_pairs else 0

            # Mega-cluster penalty: any frame where the largest cluster covers
            # >30% of all quotes is "chaining failure". Hard penalty.
            max_share = 0.0
            penalty = 0.0
            sizes_overall: list[int] = []
            for fid in TARGET_FRAMES:
                m = report[fid]["by_threshold"][label]
                n_quotes = len(by_frame[fid])
                share = m["max_cluster_size"] / n_quotes if n_quotes else 0
                max_share = max(max_share, share)
                if share > 0.30:
                    penalty += (share - 0.30) * 2  # ramp up sharply
                # Also penalize singleton-dominated (>85% singletons)
                if m["singleton_count"] / m["cluster_count"] > 0.85:
                    penalty += 0.1

            # Composite score: purity minus penalty. Range roughly [0, 1].
            score = purity - penalty
            scoreboard.append({
                "label": label, "purity": purity, "max_share": max_share,
                "mean_multi_size": sum(report[fid]["by_threshold"][label]["mean_multi_cluster_size"]
                                       for fid in TARGET_FRAMES) / len(TARGET_FRAMES),
                "penalty": penalty, "score": score,
            })
            print(f"{label:>16}  {purity:>8.3f}  {max_share:>10.3f}  "
                  f"{scoreboard[-1]['mean_multi_size']:>10.2f}  "
                  f"{penalty:>8.3f}  {score:>8.3f}")

        # Rank by composite score
        scoreboard.sort(key=lambda r: -r["score"])
        print("\n=== Top 5 by composite score ===")
        for s in scoreboard[:5]:
            print(f"  {s['label']:>16}  score={s['score']:.3f}  "
                  f"purity={s['purity']:.3f}  max_share={s['max_share']:.3f}  "
                  f"penalty={s['penalty']:.3f}")

        Path("/tmp/cluster_verify.json").write_text(json.dumps(report, indent=2, default=str))
        print(f"\nWrote /tmp/cluster_verify.json")
    finally:
        db.close()


if __name__ == "__main__":
    main()
