"""Per-frame adaptive threshold for variant clustering.

Idea: rather than a global threshold, compute each frame's distance
distribution and pick a threshold from the distribution itself — for
example, the n-th percentile of within-frame distances.

For each frame, sweep candidate strategies:
  • global complete_0.42 (current winner)
  • per_frame_p10 (use the 10th percentile of intra-frame distances)
  • per_frame_p15 / p20 / p25 / p30
  • per_frame_min_intra (use the minimum positive-pair distance for that frame)

Score by wire-sync purity + cluster shape sanity.

Usage:
    cd backend && .venv/bin/python -m app.scripts.per_frame_threshold
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np

from app.db import SessionLocal
from app.models import NarrativeFrame, NarrativeFrameMention, SourceItem
from app.services.embeddings import embed_texts
from app.scripts.verify_calibrated_threshold import agglom, evaluate

TARGET_FRAMES = [1, 4, 3, 35, 60]


def pairwise_distances(items: list[dict]) -> np.ndarray:
    """Compute all pairwise cosine distances within a frame."""
    usable = [it for it in items if it.get("embedding")]
    n = len(usable)
    if n < 2:
        return np.array([])
    X = np.array([it["embedding"] for it in usable], dtype=np.float32)
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    Xn = X / norms
    sim = Xn @ Xn.T
    dists = 1 - sim
    triu = np.triu_indices(n, k=1)
    return dists[triu]


def main() -> None:
    db = SessionLocal()
    try:
        by_frame: dict = defaultdict(list)
        rows = (
            db.query(NarrativeFrameMention.id, NarrativeFrameMention.frame_id,
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

        all_quotes, refs = [], []
        for items in by_frame.values():
            for it in items:
                refs.append(it)
                all_quotes.append(it["quote"])
        embs = embed_texts(all_quotes, task_type="SEMANTIC_SIMILARITY")
        for it, e in zip(refs, embs):
            it["embedding"] = e

        # Diagnostic: per-frame distance distribution
        print("=== Per-frame intra-quote distance distributions ===\n")
        print(f"{'fid':>4}  {'name':<45} {'p10':>6} {'p15':>6} {'p25':>6} {'p50':>6}  {'wire_sync_p_min':>18}")
        per_frame_stats: dict = {}
        for fid in TARGET_FRAMES:
            dists = pairwise_distances(by_frame[fid])
            frame = db.get(NarrativeFrame, fid)
            # Compute the MAX distance among wire-sync (same story_cluster_id) pairs.
            # If we set threshold ABOVE that, all wire-syncs get merged.
            wire_pair_dists = []
            usable = [it for it in by_frame[fid] if it.get("embedding")
                      and it.get("story_cluster_id")]
            for i in range(len(usable)):
                for j in range(i + 1, len(usable)):
                    if usable[i]["story_cluster_id"] == usable[j]["story_cluster_id"]:
                        a, b = usable[i]["embedding"], usable[j]["embedding"]
                        na = sum(x * x for x in a) ** 0.5
                        nb = sum(x * x for x in b) ** 0.5
                        sim = sum(x * y for x, y in zip(a, b)) / (na * nb) if (na and nb) else 0
                        wire_pair_dists.append(1 - sim)
            wire_max = max(wire_pair_dists) if wire_pair_dists else None
            wire_p75 = np.percentile(wire_pair_dists, 75) if wire_pair_dists else None
            per_frame_stats[fid] = {
                "p10": np.percentile(dists, 10),
                "p15": np.percentile(dists, 15),
                "p25": np.percentile(dists, 25),
                "p50": np.percentile(dists, 50),
                "wire_max": wire_max,
                "wire_p75": wire_p75,
            }
            ws = f"max={wire_max:.2f}" if wire_max is not None else "n/a"
            print(f"{fid:>4}  {frame.name[:45]:<45} {per_frame_stats[fid]['p10']:>6.2f} "
                  f"{per_frame_stats[fid]['p15']:>6.2f} {per_frame_stats[fid]['p25']:>6.2f} "
                  f"{per_frame_stats[fid]['p50']:>6.2f}  {ws:>18}")

        # Strategies
        strategies = [
            ("global_complete_0.42", lambda fid: 0.42),
            ("global_complete_0.36", lambda fid: 0.36),
            ("per_frame_p10",        lambda fid: per_frame_stats[fid]["p10"]),
            ("per_frame_p15",        lambda fid: per_frame_stats[fid]["p15"]),
            ("per_frame_p25",        lambda fid: per_frame_stats[fid]["p25"]),
            ("per_frame_wire_p75",   lambda fid: per_frame_stats[fid].get("wire_p75") or 0.30),
            ("per_frame_wire_max",   lambda fid: per_frame_stats[fid].get("wire_max") or 0.30),
        ]

        print("\n=== Strategy comparison ===\n")
        for strat_name, strat_fn in strategies:
            total_pairs, total_recovered = 0, 0
            mega_count = 0
            for fid in TARGET_FRAMES:
                thresh = strat_fn(fid)
                clusters = agglom(by_frame[fid], thresh, linkage="complete")
                m = evaluate(clusters)
                total_pairs += m["wire_sync_pairs"]
                total_recovered += m["wire_sync_recovered"]
                share = m["max_cluster_size"] / len(by_frame[fid]) if by_frame[fid] else 0
                if share > 0.30:
                    mega_count += 1
            purity = total_recovered / total_pairs if total_pairs else 0
            print(f"  {strat_name:<24}  purity={purity:.3f}  ({total_recovered}/{total_pairs})  "
                  f"mega-clusters={mega_count}/{len(TARGET_FRAMES)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
