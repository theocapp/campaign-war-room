"""Data-driven cosine-distance threshold calibration for variant clustering.

Uses SimHash story_cluster_id as weak supervision: pairs of NFMs whose source
articles share a story_cluster_id are near-duplicate wire-syndicated quotes
and MUST cluster together. That gives us a labeled positive class. Pairs with
different story_cluster_ids are "unknown" — they might be genuine paraphrases
that should still merge, or unrelated quotes that shouldn't.

The calibration uses both:
  • Recall on positives (same story_cluster_id) — must stay high (>0.95)
  • FPR on negatives (different story_cluster_id) — must stay low
  • Plus a distribution-shape sanity check

Output: a recommended distance threshold, the precision/recall curve, and
ASCII histograms of the positive vs negative similarity distributions.

Usage:
    cd backend && .venv/bin/python -m app.scripts.calibrate_variant_threshold
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


MIN_QUOTES_PER_FRAME = 10
# Sweep distance thresholds at 0.01 resolution from very-strict to lenient.
THRESHOLD_GRID = [round(0.01 * i, 2) for i in range(2, 41)]


def pull_data(db) -> dict:
    """Return frame_id → list of (nfm_id, embedding, story_cluster_id, quote)."""
    rows = (
        db.query(NarrativeFrameMention.id,
                 NarrativeFrameMention.frame_id,
                 NarrativeFrameMention.extracted_text,
                 SourceItem.story_cluster_id)
        .join(SourceItem, SourceItem.id == NarrativeFrameMention.source_item_id)
        .filter(NarrativeFrameMention.extracted_text.isnot(None))
        .all()
    )
    by_frame: dict = defaultdict(list)
    for nfm_id, frame_id, text, story_cluster in rows:
        text = (text or "").strip()
        if not text:
            continue
        by_frame[frame_id].append({
            "nfm_id": nfm_id, "quote": text,
            "story_cluster_id": story_cluster,
        })
    # Drop frames with too few quotes to bother with.
    by_frame = {fid: items for fid, items in by_frame.items()
                if len(items) >= MIN_QUOTES_PER_FRAME}
    return by_frame


def embed_all(by_frame: dict) -> None:
    """Mutates: adds 'embedding' to each item dict. Embeds in one batch."""
    all_quotes = []
    refs = []
    for fid, items in by_frame.items():
        for it in items:
            refs.append(it)
            all_quotes.append(it["quote"])
    print(f"embedding {len(all_quotes)} quotes (single batch)...")
    embs = embed_texts(all_quotes, task_type="SEMANTIC_SIMILARITY")
    for it, e in zip(refs, embs):
        it["embedding"] = e


def build_pair_dataset(by_frame: dict, cross_frame_neg_sample: int = 5000
                       ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (similarities, labels, kinds).

    Labels:
      1 = positive  — same story_cluster_id within a frame (definitely same variant)
      0 = within-frame-different-story (ambiguous — could be paraphrase OR distinct claim)
     -1 = cross-frame (definitely different claim by construction)

    The cross-frame class is cleaner negative signal: any pair of NFMs from
    DIFFERENT narrative frames is, by definition, about different topics.
    These provide an unambiguous "should NOT merge" baseline.

    cross_frame_neg_sample caps the number of cross-frame pairs we sample
    so we don't drown the within-frame signal in millions of cross-frame
    pairs (which would all have very low similarity).
    """
    import random as _r
    rng = _r.Random(20260527)
    sims: list[float] = []
    labels: list[int] = []
    kinds: list[str] = []

    # Within-frame pairs: positive (same story_cluster_id) or ambiguous (different).
    for fid, items in by_frame.items():
        usable = [it for it in items
                  if it.get("embedding") is not None
                  and it.get("story_cluster_id") is not None]
        n = len(usable)
        for i in range(n):
            ei = usable[i]["embedding"]
            si = usable[i]["story_cluster_id"]
            for j in range(i + 1, n):
                ej = usable[j]["embedding"]
                sj = usable[j]["story_cluster_id"]
                s = _cosine(ei, ej)
                sims.append(s)
                if si == sj:
                    labels.append(1)
                    kinds.append("pos_same_story")
                else:
                    labels.append(0)
                    kinds.append("ambig_diff_story_same_frame")

    # Cross-frame pairs: sampled because the full set is enormous (and
    # uninformative — most are trivially low similarity).
    frame_ids = list(by_frame.keys())
    flat = []
    for fid in frame_ids:
        for it in by_frame[fid]:
            if it.get("embedding") is not None:
                flat.append((fid, it))
    pairs_added = 0
    attempts = 0
    while pairs_added < cross_frame_neg_sample and attempts < cross_frame_neg_sample * 20:
        attempts += 1
        a = rng.choice(flat)
        b = rng.choice(flat)
        if a[0] == b[0]:  # same frame — skip
            continue
        sims.append(_cosine(a[1]["embedding"], b[1]["embedding"]))
        labels.append(-1)
        kinds.append("neg_cross_frame")
        pairs_added += 1

    return np.array(sims), np.array(labels), np.array(kinds)


def _percentiles(values: np.ndarray) -> dict:
    if len(values) == 0:
        return {}
    return {
        "n": int(len(values)),
        "mean": round(float(values.mean()), 4),
        "p10": round(float(np.percentile(values, 10)), 4),
        "p25": round(float(np.percentile(values, 25)), 4),
        "p50": round(float(np.percentile(values, 50)), 4),
        "p75": round(float(np.percentile(values, 75)), 4),
        "p90": round(float(np.percentile(values, 90)), 4),
        "p95": round(float(np.percentile(values, 95)), 4),
        "p99": round(float(np.percentile(values, 99)), 4),
    }


def ascii_histogram(values: np.ndarray, bins: int = 30,
                    range_: tuple = (0.0, 1.0), width: int = 50,
                    label: str = "") -> str:
    counts, edges = np.histogram(values, bins=bins, range=range_)
    max_c = max(counts) if counts.size else 1
    out = [f"{label} (n={len(values)})"]
    for c, e in zip(counts, edges):
        bar = "█" * int(width * c / max_c) if max_c > 0 else ""
        out.append(f"  sim≥{e:.2f}  {bar} {c}")
    return "\n".join(out)


def metrics_at_threshold(sims: np.ndarray, labels: np.ndarray,
                         min_sim: float) -> dict:
    """At similarity ≥ min_sim, report:
      • pos_recall — fraction of positive (same story_cluster) pairs merged.
        Must stay HIGH — these are wire-sync pairs we MUST cluster together.
      • cross_frame_fpr — fraction of cross-frame pairs merged.
        Must stay LOW — these are definitely-different-claim pairs.
      • ambig_merge_rate — fraction of within-frame-different-story pairs merged.
        Informational only (these may be real paraphrases or distinct claims).
    """
    pred = sims >= min_sim
    is_pos = labels == 1
    is_ambig = labels == 0
    is_cross = labels == -1

    n_pos = int(is_pos.sum())
    n_ambig = int(is_ambig.sum())
    n_cross = int(is_cross.sum())

    pos_recall = float((pred & is_pos).sum()) / n_pos if n_pos else 0
    ambig_rate = float((pred & is_ambig).sum()) / n_ambig if n_ambig else 0
    cross_fpr = float((pred & is_cross).sum()) / n_cross if n_cross else 0
    return {
        "sim": round(min_sim, 3),
        "dist": round(1 - min_sim, 3),
        "pos_recall": round(pos_recall, 4),
        "ambig_merge_rate": round(ambig_rate, 5),
        "cross_frame_fpr": round(cross_fpr, 6),
    }


def main() -> None:
    db = SessionLocal()
    try:
        by_frame = pull_data(db)
        n_frames = len(by_frame)
        n_quotes = sum(len(v) for v in by_frame.values())
        print(f"loaded {n_quotes} quotes across {n_frames} frames")

        embed_all(by_frame)

        sims, labels, kinds = build_pair_dataset(by_frame)
        n_pos = int((labels == 1).sum())
        n_ambig = int((labels == 0).sum())
        n_cross = int((labels == -1).sum())
        print(f"\nbuilt {len(sims):,} labeled pairs:")
        print(f"  {n_pos:>6,}  pos_same_story          (DEFINITELY same variant)")
        print(f"  {n_ambig:>6,}  ambig_diff_story_same_frame (ambiguous — could be paraphrase or distinct)")
        print(f"  {n_cross:>6,}  neg_cross_frame         (DEFINITELY different claim)")

        pos_sims = sims[labels == 1]
        ambig_sims = sims[labels == 0]
        cross_sims = sims[labels == -1]
        print()
        print(ascii_histogram(pos_sims, range_=(0.3, 1.0),
                              label="POS pairs (same story_cluster) — should merge"))
        print()
        print(ascii_histogram(ambig_sims, range_=(0.3, 1.0),
                              label="AMBIG (within-frame, diff story) — unknown"))
        print()
        print(ascii_histogram(cross_sims, range_=(0.0, 1.0),
                              label="CROSS-FRAME (definitely different)"))

        # Sweep thresholds. Sweep more broadly so we see where each curve
        # crosses its target.
        print("\n\n=== Threshold sweep ===")
        print(f"{'sim':>6} {'dist':>6} {'pos_recall':>11} {'ambig_merge':>12} {'cross_fpr':>10}")
        rows = []
        for sim in [round(0.30 + 0.01 * i, 2) for i in range(70)]:
            row = metrics_at_threshold(sims, labels, sim)
            rows.append(row)
            print(f"{row['sim']:>6} {row['dist']:>6} "
                  f"{row['pos_recall']:>11.3f} {row['ambig_merge_rate']:>12.5f} "
                  f"{row['cross_frame_fpr']:>10.6f}")

        # Find thresholds at clean recall targets, with cross-frame FPR ceiling.
        # The recommendation logic: pick the LOWEST sim (loosest threshold) that
        # keeps cross-frame FPR below 1% — that's "we never merge stuff that's
        # definitely different". Among those, pick the one with highest pos_recall.
        candidates = [r for r in rows if r["cross_frame_fpr"] <= 0.01]
        rec = max(candidates, key=lambda r: r["pos_recall"]) if candidates else None
        # Also pick a tighter option (cross-frame FPR ≤ 0.1%) for high-precision use.
        candidates_tight = [r for r in rows if r["cross_frame_fpr"] <= 0.001]
        rec_tight = max(candidates_tight, key=lambda r: r["pos_recall"]) if candidates_tight else None

        print("\n=== Recommendations ===")
        if rec:
            print(f"Balanced (cross-frame FPR ≤ 1%):  sim≥{rec['sim']:.2f}  dist≤{rec['dist']:.2f}  "
                  f"pos_recall={rec['pos_recall']:.3f}  ambig_merge={rec['ambig_merge_rate']:.4f}  "
                  f"cross_fpr={rec['cross_frame_fpr']:.5f}")
        if rec_tight:
            print(f"Tight   (cross-frame FPR ≤ 0.1%): sim≥{rec_tight['sim']:.2f}  dist≤{rec_tight['dist']:.2f}  "
                  f"pos_recall={rec_tight['pos_recall']:.3f}  ambig_merge={rec_tight['ambig_merge_rate']:.4f}  "
                  f"cross_fpr={rec_tight['cross_frame_fpr']:.5f}")

        # Save artifacts
        out = {
            "n_frames": n_frames, "n_quotes": n_quotes,
            "n_pairs": len(sims),
            "n_pos_same_story": n_pos,
            "n_ambig_diff_story_same_frame": n_ambig,
            "n_neg_cross_frame": n_cross,
            "pos_sim_stats": _percentiles(pos_sims),
            "ambig_sim_stats": _percentiles(ambig_sims),
            "cross_sim_stats": _percentiles(cross_sims),
            "threshold_curve": rows,
            "rec_balanced": rec,
            "rec_tight": rec_tight,
        }
        Path("/tmp/cluster_calibration.json").write_text(json.dumps(out, indent=2))
        print(f"\nWrote /tmp/cluster_calibration.json")
    finally:
        db.close()


if __name__ == "__main__":
    main()
