"""Inspect the actual cluster contents produced by the winning config
(complete linkage, distance_threshold=0.42). Same 5 frames I graded blindly.

Goal: confirm clusters are semantically clean — quotes in one cluster make
the same specific claim, distinct claims live in distinct clusters.

Usage:
    cd backend && .venv/bin/python -m app.scripts.inspect_winning_clusters
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np

from app.db import SessionLocal
from app.models import NarrativeFrame, NarrativeFrameMention, SourceItem
from app.services.embeddings import embed_texts
from app.scripts.verify_calibrated_threshold import agglom

TARGET_FRAMES = [1, 4, 3, 35, 60]
LINKAGE = "complete"
DIST_THRESHOLD = 0.42


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

        out = ["# Clusters produced by complete linkage @ distance ≤ 0.42",
               "",
               "Calibrated via grid search on this campaign's data.",
               "Wire-sync purity = 0.640, max cluster size ≤ 28% of frame.",
               ""]

        for fid in TARGET_FRAMES:
            frame = db.get(NarrativeFrame, fid)
            clusters = agglom(by_frame[fid], DIST_THRESHOLD, linkage=LINKAGE)
            # Sort by size descending, multi-member first
            multi = [c for c in clusters if len(c) > 1]
            singletons = [c for c in clusters if len(c) == 1]
            multi.sort(key=lambda c: -len(c))
            out.append(f"## Frame {fid} — {frame.name!r}  "
                       f"({len(by_frame[fid])} quotes → {len(clusters)} clusters, "
                       f"{len(singletons)} singletons)")
            out.append("")
            for ci, c in enumerate(multi[:10], start=1):
                out.append(f"### Cluster {ci} (size {len(c)})")
                for m in c[:7]:
                    q = m["quote"][:200]
                    out.append(f"- _(nfm {m['nfm_id']})_ \"{q}\"")
                if len(c) > 7:
                    out.append(f"_(+ {len(c) - 7} more)_")
                out.append("")
            if len(multi) > 10:
                out.append(f"_(+ {len(multi) - 10} more multi-clusters)_")
            out.append("")
            out.append(f"_(+ {len(singletons)} singletons not shown)_")
            out.append("")

        Path("/tmp/winning_clusters.md").write_text("\n".join(out))
        print("Wrote /tmp/winning_clusters.md")
    finally:
        db.close()


if __name__ == "__main__":
    main()
