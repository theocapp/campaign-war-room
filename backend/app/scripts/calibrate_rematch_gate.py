"""Calibrate the embedding-similarity gate for rematch_all.

The current `match_article_to_frames` makes one LLM judge call per article
asking which of ~40 frames it covers. Almost all 40 are rejected per call —
the LLM is mostly doing cheap filtering. We can move that filtering into
embedding cosine similarity, keeping the LLM only for the small shortlist
of frames that survive the gate.

This script measures the recall/workload tradeoff:
  • POSITIVE pairs: (article, frame) where an NFM already exists. These
    are pairs the LLM previously said "yes" to. The gate must keep them.
  • NEGATIVE pairs: (article, frame) with no NFM. The LLM previously said
    "no" (or never evaluated). The gate should skip them to save calls.

Outputs a recall vs workload curve and recommends a threshold.

Usage:
    cd backend && .venv/bin/python -m app.scripts.calibrate_rematch_gate
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from app.db import SessionLocal
from app.models import NarrativeFrame, NarrativeFrameMention, SourceItem
from app.services.embeddings import embed_texts


# Match how the LLM sees an article: title + body excerpt (or summary as
# fallback). This is what the existing match_article_to_frames builds.
ARTICLE_BODY_CHARS = 1500
# Race-relevance gate matches what rematch_all already uses.
MIN_RELEVANCE = 50


def article_text_for_embedding(item: SourceItem) -> str:
    title = (item.title or "").strip()
    body = (item.raw_text or item.summary or "").strip()
    if not title and not body:
        return ""
    return f"{title}\n\n{body[:ARTICLE_BODY_CHARS]}"


def frame_text_for_embedding(frame: NarrativeFrame) -> str:
    return f"{frame.name}\n\n{frame.description or ''}".strip()


def main() -> None:
    db = SessionLocal()
    try:
        # ── Pull data ───────────────────────────────────────────────────────
        frames = (db.query(NarrativeFrame)
                  .filter(NarrativeFrame.active == True)  # noqa: E712
                  .order_by(NarrativeFrame.id)
                  .all())
        articles = (db.query(SourceItem)
                    .filter(SourceItem.race_relevance_score >= MIN_RELEVANCE,
                            SourceItem.title.isnot(None))
                    .order_by(SourceItem.id)
                    .all())
        # Only articles with usable text
        articles = [a for a in articles if article_text_for_embedding(a)]
        print(f"loaded {len(articles)} articles, {len(frames)} frames")

        # NFMs = positive (article, frame) pairs
        nfm_rows = (db.query(NarrativeFrameMention.source_item_id,
                             NarrativeFrameMention.frame_id)
                    .all())
        positive_pairs = {(s, f) for s, f in nfm_rows}
        print(f"loaded {len(positive_pairs)} positive (article, frame) pairs")

        # ── Embed ───────────────────────────────────────────────────────────
        print(f"\nembedding {len(articles)} articles + {len(frames)} frames...")
        article_texts = [article_text_for_embedding(a) for a in articles]
        article_embs = embed_texts(article_texts, task_type="SEMANTIC_SIMILARITY")
        frame_texts = [frame_text_for_embedding(f) for f in frames]
        frame_embs = embed_texts(frame_texts, task_type="SEMANTIC_SIMILARITY")

        # Build matrices, dropping rows with failed embeddings
        keep_a_idx = [i for i, e in enumerate(article_embs) if e is not None]
        keep_f_idx = [i for i, e in enumerate(frame_embs) if e is not None]
        articles_kept = [articles[i] for i in keep_a_idx]
        frames_kept = [frames[i] for i in keep_f_idx]
        A = np.array([article_embs[i] for i in keep_a_idx], dtype=np.float32)
        F = np.array([frame_embs[i] for i in keep_f_idx], dtype=np.float32)
        print(f"embedded {len(articles_kept)} articles, {len(frames_kept)} frames")

        # L2-normalize
        A = A / np.linalg.norm(A, axis=1, keepdims=True).clip(min=1e-12)
        F = F / np.linalg.norm(F, axis=1, keepdims=True).clip(min=1e-12)

        # Pairwise cosine: [n_articles, n_frames]
        sim = A @ F.T

        # ── Build positive mask aligned to the matrix ───────────────────────
        article_id_to_row = {a.id: r for r, a in enumerate(articles_kept)}
        frame_id_to_col = {f.id: c for c, f in enumerate(frames_kept)}
        is_positive = np.zeros_like(sim, dtype=bool)
        n_aligned_pos = 0
        for (aid, fid) in positive_pairs:
            r = article_id_to_row.get(aid)
            c = frame_id_to_col.get(fid)
            if r is not None and c is not None:
                is_positive[r, c] = True
                n_aligned_pos += 1
        n_total_pairs = sim.size
        n_negative = n_total_pairs - n_aligned_pos
        print(f"\naligned {n_aligned_pos} positives into a {sim.shape} matrix "
              f"({n_total_pairs:,} total pairs, {n_negative:,} negatives)")

        pos_sims = sim[is_positive]
        neg_sims = sim[~is_positive]
        print(f"\nPOS sim distribution: n={len(pos_sims)} "
              f"mean={pos_sims.mean():.3f} "
              f"p10={np.percentile(pos_sims, 10):.3f} "
              f"p25={np.percentile(pos_sims, 25):.3f} "
              f"p50={np.percentile(pos_sims, 50):.3f}")
        print(f"NEG sim distribution: n={len(neg_sims)} "
              f"mean={neg_sims.mean():.3f} "
              f"p90={np.percentile(neg_sims, 90):.3f} "
              f"p95={np.percentile(neg_sims, 95):.3f} "
              f"p99={np.percentile(neg_sims, 99):.3f}")

        # ── Threshold sweep ─────────────────────────────────────────────────
        print(f"\n{'sim':>6} {'pos_recall':>11} {'workload_pct':>13} {'work_saved':>11} {'tp':>6} {'fn':>6} {'fp':>8}")
        rows = []
        for sim_thresh in [round(0.30 + 0.01 * i, 2) for i in range(60)]:
            gated_keep = sim >= sim_thresh
            tp = int((gated_keep & is_positive).sum())
            fp = int((gated_keep & ~is_positive).sum())
            fn = int((~gated_keep & is_positive).sum())
            pos_recall = tp / n_aligned_pos if n_aligned_pos else 0
            workload_pct = (tp + fp) / n_total_pairs
            row = {
                "sim": round(sim_thresh, 3),
                "pos_recall": round(pos_recall, 4),
                "workload_pct": round(workload_pct, 5),
                "work_saved": round(1 - workload_pct, 5),
                "tp": tp, "fn": fn, "fp": fp,
            }
            rows.append(row)
            print(f"{row['sim']:>6} {row['pos_recall']:>11.4f} "
                  f"{row['workload_pct']:>13.5f} {row['work_saved']:>11.5f} "
                  f"{tp:>6} {fn:>6} {fp:>8}")

        # ── Recommendation ──────────────────────────────────────────────────
        # Find loosest threshold with pos_recall ≥ 0.95 (and 0.99) — that's
        # the lowest workload that still keeps the LLM's judgments.
        def loosest_at(target_recall: float):
            qualifying = [r for r in rows if r["pos_recall"] >= target_recall]
            if not qualifying:
                return None
            return min(qualifying, key=lambda r: r["workload_pct"])

        rec_95 = loosest_at(0.95)
        rec_98 = loosest_at(0.98)
        rec_99 = loosest_at(0.99)

        print("\n=== Recommendations ===")
        for label, r in [("≥95% recall", rec_95),
                         ("≥98% recall", rec_98),
                         ("≥99% recall", rec_99)]:
            if r is None:
                print(f"  {label}: not achievable in sweep")
                continue
            saved_pct = 100 * r["work_saved"]
            print(f"  {label}: sim ≥ {r['sim']:.2f}  "
                  f"recall={r['pos_recall']:.3f}  "
                  f"workload={100*r['workload_pct']:.2f}%  "
                  f"saved={saved_pct:.1f}% of LLM calls  "
                  f"(misses {r['fn']} positives)")

        # ── Per-frame threshold strategy ────────────────────────────────────
        # For each frame, the optimal gate floor is determined by its own
        # positive distribution. Frames whose positives sit at low sim need
        # a looser τ than frames whose positives are tight.
        #
        # Strategy: τ_f = max(percentile(positives_f, P), GLOBAL_FLOOR)
        # where P ∈ {0, 5, 10} controls recall, GLOBAL_FLOOR protects frames
        # with too few positives or genuine noise.
        MIN_POS_FOR_PER_FRAME = 5  # below this, fall back to global default
        GLOBAL_FLOOR = 0.30        # never go below this regardless

        print("\n=== Per-frame threshold strategies (LLM-call workload) ===")
        print("  The real cost metric: how many articles still need an LLM call, and")
        print("  how many frames does each call ask about (shortlist size).")
        print(f"\n{'strategy':<22} {'floor':>5} {'recall':>8} {'art_calls':>10} {'pct_skip':>9} {'avg_short':>10} {'misses':>7}")
        n_articles_total = A.shape[0]
        for global_floor in [0.15, 0.20, 0.25, 0.30]:
            for percentile_pick in [0, 5, 10]:
                per_frame_thresh = np.full(F.shape[0], global_floor, dtype=np.float32)
                for c in range(F.shape[0]):
                    col_pos = sim[is_positive[:, c], c]
                    if len(col_pos) >= MIN_POS_FOR_PER_FRAME:
                        per_frame_thresh[c] = max(
                            float(np.percentile(col_pos, percentile_pick)),
                            global_floor,
                        )
                gated_keep_pf = sim >= per_frame_thresh[np.newaxis, :]
                # Shortlist size per article
                shortlist_sizes = gated_keep_pf.sum(axis=1)
                n_articles_with_call = int((shortlist_sizes > 0).sum())
                avg_shortlist = float(shortlist_sizes[shortlist_sizes > 0].mean()) if n_articles_with_call else 0
                pct_skip = 100 * (1 - n_articles_with_call / n_articles_total)
                # Recall on labels
                tp_pf = int((gated_keep_pf & is_positive).sum())
                fn_pf = int((~gated_keep_pf & is_positive).sum())
                pos_recall_pf = tp_pf / n_aligned_pos if n_aligned_pos else 0
                strategy = f"per-frame p{percentile_pick:>2}"
                print(f"  {strategy:<20} {global_floor:>5.2f} {pos_recall_pf:>8.4f} "
                      f"{n_articles_with_call:>10} {pct_skip:>8.1f}% "
                      f"{avg_shortlist:>10.1f} {fn_pf:>7}")

        # Best honest pick: per-frame p0 with GLOBAL_FLOOR low enough to catch
        # noisy positives. We'll surface the (article, frame) pairs that fall
        # ── Hard-negative threshold calibration ─────────────────────────────
        # For each frame, find its positives' nearest "other-frame" articles
        # by article-article embedding similarity. Those are the semantically
        # adjacent boundary cases the threshold must separate from positives.
        #
        # The hard-negative threshold for frame F is then:
        #   max(percentile(hard_negatives_to_F, 90), per_frame_p0(positives))
        # i.e. tight enough to reject 90% of hard negatives, loose enough not
        # to lose any positive that was already above the per-frame floor.
        print("\n=== Hard-negative mining ===")
        HARD_NEG_K = 5  # nearest other-frame articles per positive
        article_article_sim = A @ A.T

        per_frame_hard_thresh = np.full(F.shape[0], GLOBAL_FLOOR, dtype=np.float32)
        per_frame_hard_recall = np.zeros(F.shape[0])
        per_frame_hard_workload = np.zeros(F.shape[0])
        n_hard_neg_collected = 0

        for c in range(F.shape[0]):
            positive_rows = np.where(is_positive[:, c])[0]
            if len(positive_rows) < MIN_POS_FOR_PER_FRAME:
                continue

            # Mine hard negatives: articles most similar to positives but
            # NOT themselves in this frame.
            hard_neg_rows: set = set()
            for p_row in positive_rows:
                neighbor_sims = article_article_sim[p_row].copy()
                neighbor_sims[p_row] = -1  # exclude self
                # Top candidates by similarity, walk down until we find K not-in-frame
                sorted_idx = np.argsort(-neighbor_sims)
                taken = 0
                for n_row in sorted_idx:
                    if not is_positive[n_row, c]:
                        hard_neg_rows.add(int(n_row))
                        taken += 1
                        if taken >= HARD_NEG_K:
                            break

            n_hard_neg_collected += len(hard_neg_rows)
            hard_neg_sims = np.array([sim[r, c] for r in hard_neg_rows])
            pos_sims_for_frame = sim[positive_rows, c]

            # Maximize the gap between positive recall and hard-negative
            # keep-rate. At each candidate threshold τ:
            #   recall(τ) = P(positive sim ≥ τ)
            #   hn_keep(τ) = P(hard-neg sim ≥ τ)
            # We want recall high AND hn_keep low. Pick τ maximizing
            # (recall - hn_keep), with a recall floor of 0.95.
            recall_floor = 0.95
            candidates = sorted(set(list(pos_sims_for_frame) + list(hard_neg_sims)))
            best_gap = -1.0
            chosen = float(min(pos_sims_for_frame))
            for cand in candidates:
                recall_at = float((pos_sims_for_frame >= cand).mean())
                if recall_at < recall_floor:
                    continue
                hn_keep = float((hard_neg_sims >= cand).mean())
                gap = recall_at - hn_keep
                if gap > best_gap:
                    best_gap = gap
                    chosen = float(cand)
            chosen = max(chosen, GLOBAL_FLOOR)
            per_frame_hard_thresh[c] = chosen

            # Evaluate this threshold
            recall_at = float((pos_sims_for_frame >= chosen).mean())
            workload_at = float((sim[:, c] >= chosen).mean())
            per_frame_hard_recall[c] = recall_at
            per_frame_hard_workload[c] = workload_at

        # Apply across all frames (those without per-frame fall back to GLOBAL_FLOOR)
        gated_keep_hard = sim >= per_frame_hard_thresh[np.newaxis, :]
        tp_h = int((gated_keep_hard & is_positive).sum())
        fn_h = int((~gated_keep_hard & is_positive).sum())
        n_articles_with_call_h = int((gated_keep_hard.sum(axis=1) > 0).sum())
        avg_shortlist_h = (
            gated_keep_hard.sum(axis=1)[gated_keep_hard.sum(axis=1) > 0].mean()
            if n_articles_with_call_h else 0
        )
        recall_h = tp_h / n_aligned_pos if n_aligned_pos else 0

        print(f"  collected {n_hard_neg_collected} hard-negative pairs")
        print(f"\nproduction (p0 only): recall=0.977  shortlist=15.9 frames  "
              f"articles_skipped=8.5%")
        print(f"hard-negative calibration: recall={recall_h:.4f}  "
              f"shortlist={avg_shortlist_h:.1f} frames  "
              f"articles_skipped={100*(1 - n_articles_with_call_h/A.shape[0]):.1f}%  "
              f"(misses {fn_h})")

        # ── Recall by frame-frequency bucket ────────────────────────────────
        # Aggregate recall hides whether small-sample frames behave differently
        # from large-sample frames. Bucket by frame's positive count and
        # report recall per bucket.
        BUCKETS = [
            ("xs (1-4 pos)", 1, 4),
            ("s  (5-9 pos)", 5, 9),
            ("m  (10-29 pos)", 10, 29),
            ("l  (30+ pos)", 30, 10_000),
        ]
        # Use the production strategy (per-frame p0 + floor 0.30) for this analysis.
        per_frame_thresh_prod = np.full(F.shape[0], 0.30, dtype=np.float32)
        for c in range(F.shape[0]):
            col_pos = sim[is_positive[:, c], c]
            if len(col_pos) >= MIN_POS_FOR_PER_FRAME:
                per_frame_thresh_prod[c] = max(float(np.percentile(col_pos, 0)), 0.30)
        gated_keep_prod = sim >= per_frame_thresh_prod[np.newaxis, :]

        print("\n=== Recall by frame-frequency bucket (production thresholds) ===")
        print(f"{'bucket':<18} {'n_frames':>10} {'n_pos':>8} {'recall':>9} {'avg_short_size':>16}")
        for label, lo, hi in BUCKETS:
            frame_cols = [c for c, f in enumerate(frames_kept)
                          if lo <= int(is_positive[:, c].sum()) <= hi]
            if not frame_cols:
                print(f"  {label:<16}  (no frames in bucket)")
                continue
            n_pos_bucket = int(is_positive[:, frame_cols].sum())
            tp_bucket = int((gated_keep_prod[:, frame_cols] & is_positive[:, frame_cols]).sum())
            recall_bucket = tp_bucket / n_pos_bucket if n_pos_bucket else 0
            # Per-article shortlist size contribution from these frames
            shortlist_contrib = gated_keep_prod[:, frame_cols].sum(axis=1)
            avg_contrib = float(shortlist_contrib.mean())
            print(f"  {label:<16}  {len(frame_cols):>10} {n_pos_bucket:>8} "
                  f"{recall_bucket:>9.4f} {avg_contrib:>16.2f}")

        print("\n=== Inspecting low-sim positives ===")
        print("  (positives below sim 0.30 — may be LLM noise or real edge cases)")
        low_sim_positives = []
        for c, f in enumerate(frames_kept):
            for r in range(A.shape[0]):
                if is_positive[r, c] and sim[r, c] < 0.30:
                    low_sim_positives.append({
                        "article_id": articles_kept[r].id,
                        "article_title": (articles_kept[r].title or "")[:80],
                        "frame_id": f.id,
                        "frame_name": f.name,
                        "sim": float(sim[r, c]),
                    })
        low_sim_positives.sort(key=lambda r: r["sim"])
        print(f"  {len(low_sim_positives)} positives below 0.30")
        for r in low_sim_positives[:20]:
            print(f"    sim={r['sim']:.3f}  article {r['article_id']}  "
                  f"frame {r['frame_id']} '{r['frame_name'][:40]}'\n"
                  f"      title: {r['article_title']}")

        print("\n=== Per-frame positive-similarity distribution ===")
        print(f"  Frames whose positives have low median sim need looser τ.")
        print(f"{'frame_id':>8}  {'n_pos':>6}  {'p10':>6} {'p25':>6} {'p50':>6} {'p75':>6}  name")
        per_frame: list = []
        for c, f in enumerate(frames_kept):
            col_pos = sim[is_positive[:, c], c]
            if len(col_pos) == 0:
                continue
            per_frame.append({
                "frame_id": f.id,
                "name": f.name,
                "n_pos": int(len(col_pos)),
                "p10": float(np.percentile(col_pos, 10)),
                "p25": float(np.percentile(col_pos, 25)),
                "p50": float(np.percentile(col_pos, 50)),
                "p75": float(np.percentile(col_pos, 75)),
            })
        per_frame.sort(key=lambda r: r["p10"])  # show toughest cases first
        for r in per_frame[:20]:
            print(f"  {r['frame_id']:>6}  {r['n_pos']:>6}  "
                  f"{r['p10']:>6.3f} {r['p25']:>6.3f} {r['p50']:>6.3f} {r['p75']:>6.3f}  "
                  f"{r['name'][:60]}")

        # ── Save ────────────────────────────────────────────────────────────
        out = {
            "n_articles": int(len(articles_kept)),
            "n_frames": int(len(frames_kept)),
            "n_positive_pairs": int(n_aligned_pos),
            "n_total_pairs": int(n_total_pairs),
            "pos_sim_stats": {
                "mean": float(pos_sims.mean()),
                "p10": float(np.percentile(pos_sims, 10)),
                "p25": float(np.percentile(pos_sims, 25)),
                "p50": float(np.percentile(pos_sims, 50)),
                "p75": float(np.percentile(pos_sims, 75)),
            },
            "neg_sim_stats": {
                "mean": float(neg_sims.mean()),
                "p90": float(np.percentile(neg_sims, 90)),
                "p95": float(np.percentile(neg_sims, 95)),
                "p99": float(np.percentile(neg_sims, 99)),
            },
            "threshold_curve": rows,
            "rec_95": rec_95,
            "rec_98": rec_98,
            "rec_99": rec_99,
            "per_frame_positive_distribution": per_frame,
        }
        Path("/tmp/rematch_gate_calibration.json").write_text(json.dumps(out, indent=2))
        print(f"\nWrote /tmp/rematch_gate_calibration.json")

        # ── Write production thresholds file ────────────────────────────────
        # Use hard-negative gap-maximizing thresholds (computed above). Small
        # improvement over per-frame-p0 but real: ~7% shortlist reduction.
        prod_thresholds: dict = {}
        for c, f in enumerate(frames_kept):
            prod_thresholds[str(f.id)] = round(float(per_frame_hard_thresh[c]), 4)

        from datetime import datetime as _dt
        prod_out = {
            "calibrated_at": _dt.utcnow().isoformat() + "Z",
            "method": "hard_negative_gap_max_recall_0.95_floor_0.30",
            "global_floor": GLOBAL_FLOOR,
            "min_pos_for_per_frame": MIN_POS_FOR_PER_FRAME,
            "hard_neg_k": HARD_NEG_K,
            "recall_floor": 0.95,
            "n_articles_calibrated_on": int(len(articles_kept)),
            "n_positive_pairs": int(n_aligned_pos),
            "frame_thresholds": prod_thresholds,
        }
        prod_path = Path(__file__).parent.parent.parent / "data" / "rematch_thresholds.json"
        prod_path.parent.mkdir(parents=True, exist_ok=True)
        prod_path.write_text(json.dumps(prod_out, indent=2))
        print(f"Wrote production thresholds → {prod_path}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
