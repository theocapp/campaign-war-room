"""Surface disagreements between the calibrated gate and historical NFMs.

Two interesting populations:
  • OLD_YES + NEW_NO: existing NFM exists, but the gate would now drop it.
    Likely: noisy historical match, or gate too tight.
  • OLD_NO + NEW_YES: no NFM, but the gate shortlist would include this frame
    AND the article has high similarity to the frame. Likely: missed match,
    or the LLM rejected for some non-similarity reason (snippet failed
    verbatim check, etc.).

Outputs a structured markdown report I can grade. Read-only.

Usage:
    cd backend && .venv/bin/python -m app.scripts.inspect_gate_disagreements
"""
from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path

import numpy as np

from app.db import SessionLocal
from app.models import NarrativeFrame, NarrativeFrameMention, SourceItem
from app.services.embeddings import embed_texts
from app.services.narrative_frames import _GLOBAL_FLOOR_DEFAULT


MIN_RELEVANCE = 50
ARTICLE_BODY_CHARS = 1500
SAMPLE_PER_BUCKET = 50


def article_text(item: SourceItem) -> str:
    title = (item.title or "").strip()
    body = (item.raw_text or item.summary or "").strip()
    return f"{title}\n\n{body[:ARTICLE_BODY_CHARS]}".strip()


def frame_text(frame: NarrativeFrame) -> str:
    return f"{frame.name}\n\n{frame.description or ''}".strip()


def main(seed: int = 17) -> None:
    random.seed(seed)
    db = SessionLocal()
    try:
        frames = (db.query(NarrativeFrame)
                  .filter(NarrativeFrame.active == True)  # noqa: E712
                  .order_by(NarrativeFrame.id)
                  .all())
        articles = (db.query(SourceItem)
                    .filter(SourceItem.race_relevance_score >= MIN_RELEVANCE,
                            SourceItem.title.isnot(None))
                    .order_by(SourceItem.id)
                    .all())
        articles = [a for a in articles if article_text(a)]
        nfm_pairs = {(s, f) for s, f in
                     db.query(NarrativeFrameMention.source_item_id,
                              NarrativeFrameMention.frame_id).all()}
        print(f"loaded {len(articles)} articles, {len(frames)} frames, "
              f"{len(nfm_pairs)} positive pairs")

        # Embed everything
        print("embedding…")
        a_embs = embed_texts([article_text(a) for a in articles],
                             task_type="SEMANTIC_SIMILARITY")
        f_embs = embed_texts([frame_text(f) for f in frames],
                             task_type="SEMANTIC_SIMILARITY")
        keep_a = [i for i, e in enumerate(a_embs) if e is not None]
        keep_f = [i for i, e in enumerate(f_embs) if e is not None]
        articles_kept = [articles[i] for i in keep_a]
        frames_kept = [frames[i] for i in keep_f]
        A = np.array([a_embs[i] for i in keep_a], dtype=np.float32)
        F = np.array([f_embs[i] for i in keep_f], dtype=np.float32)
        A = A / np.linalg.norm(A, axis=1, keepdims=True).clip(min=1e-12)
        F = F / np.linalg.norm(F, axis=1, keepdims=True).clip(min=1e-12)
        sim = A @ F.T

        # Production thresholds (per-frame p0, floor 0.30)
        is_positive = np.zeros_like(sim, dtype=bool)
        a_id_to_row = {a.id: r for r, a in enumerate(articles_kept)}
        f_id_to_col = {f.id: c for c, f in enumerate(frames_kept)}
        for (aid, fid) in nfm_pairs:
            r = a_id_to_row.get(aid)
            c = f_id_to_col.get(fid)
            if r is not None and c is not None:
                is_positive[r, c] = True

        per_frame_thresh = np.full(F.shape[0], _GLOBAL_FLOOR_DEFAULT, dtype=np.float32)
        for c in range(F.shape[0]):
            col_pos = sim[is_positive[:, c], c]
            if len(col_pos) >= 5:
                per_frame_thresh[c] = max(float(np.percentile(col_pos, 0)),
                                          _GLOBAL_FLOOR_DEFAULT)
        gated = sim >= per_frame_thresh[np.newaxis, :]

        # Two disagreement populations
        old_yes_new_no = []   # NFM exists, gate would drop
        old_no_new_yes = []   # no NFM, gate would shortlist

        for r in range(sim.shape[0]):
            for c in range(sim.shape[1]):
                pos = bool(is_positive[r, c])
                kept = bool(gated[r, c])
                if pos and not kept:
                    old_yes_new_no.append((r, c, float(sim[r, c]), float(per_frame_thresh[c])))
                # Only flag old_no_new_yes when similarity is HIGH (near top of
                # negative distribution) — otherwise we'd flood with marginal
                # passes that aren't really disagreements worth inspecting.
                elif (not pos) and kept and sim[r, c] >= 0.55:
                    old_no_new_yes.append((r, c, float(sim[r, c]), float(per_frame_thresh[c])))

        print(f"\n{len(old_yes_new_no)} old_yes_new_no pairs")
        print(f"{len(old_no_new_yes)} old_no_new_yes pairs (sim ≥ 0.55)")

        # Sample
        sample_oyn = random.sample(old_yes_new_no, min(SAMPLE_PER_BUCKET, len(old_yes_new_no)))
        sample_ony = random.sample(old_no_new_yes, min(SAMPLE_PER_BUCKET, len(old_no_new_yes)))

        # Build report
        md = ["# Gate vs NFM disagreements\n",
              "Two populations to inspect:",
              f"- `OLD_YES_NEW_NO` ({len(old_yes_new_no)} total, showing {len(sample_oyn)}): NFM exists, gate drops",
              f"- `OLD_NO_NEW_YES` ({len(old_no_new_yes)} total at sim≥0.55, showing {len(sample_ony)}): no NFM, gate keeps",
              "",
              "## OLD_YES_NEW_NO — historical match the gate would now reject",
              "If most are noise (LLM mis-tags), the gate is silently improving accuracy.",
              "If many look correct, the gate is too tight.",
              ""]
        for r, c, s, t in sample_oyn:
            art = articles_kept[r]; frame = frames_kept[c]
            body_snip = (art.raw_text or art.summary or "")[:300].replace("\n", " ")
            md.append(f"### sim={s:.3f} (thresh {t:.2f}) — frame {frame.id} '{frame.name}'")
            md.append(f"- **article {art.id}**: {art.title!r}")
            md.append(f"- frame desc: {(frame.description or '')[:150]}")
            md.append(f"- body[:300]: {body_snip}")
            md.append("")

        md.append("## OLD_NO_NEW_YES — gate keeps, but no NFM exists")
        md.append("If many look correct, the original LLM missed real matches (good — gate finds them).")
        md.append("If many look wrong, the gate is too loose and would create false positives.")
        md.append("")
        for r, c, s, t in sample_ony:
            art = articles_kept[r]; frame = frames_kept[c]
            body_snip = (art.raw_text or art.summary or "")[:300].replace("\n", " ")
            md.append(f"### sim={s:.3f} (thresh {t:.2f}) — frame {frame.id} '{frame.name}'")
            md.append(f"- **article {art.id}**: {art.title!r}")
            md.append(f"- frame desc: {(frame.description or '')[:150]}")
            md.append(f"- body[:300]: {body_snip}")
            md.append("")

        out_path = Path("/tmp/gate_disagreements.md")
        out_path.write_text("\n".join(md))
        print(f"\nWrote {out_path}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
